import { useEffect, useState } from 'react';
import { postApiJson } from './api.js';
import { createPollingHealthStore, pollingErrorMessage } from './pollingHealth.js';
import { useRefreshToken } from './refresh.js';
import { isShadowRestartPending } from './useBotState.js';
const queryCache = new Map();
const queryHealthStore = createPollingHealthStore();
export function clearQueryCache() {
    queryCache.clear();
    queryHealthStore.clear();
}
export function useQueryHealth() {
    return queryHealthStore.useHealth();
}
export function useQuery(sql, params = [], intervalMs = 1000) {
    const paramsKey = JSON.stringify(params);
    const cacheKey = `${sql}\u0000${paramsKey}`;
    const [rows, setRows] = useState(() => queryCache.get(cacheKey) || []);
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let cancelled = false;
        let timer = null;
        let activeController = null;
        setRows(queryCache.get(cacheKey) || []);
        queryHealthStore.register(cacheKey);
        const schedule = () => {
            if (cancelled) {
                return;
            }
            timer = setTimeout(() => {
                void run();
            }, Math.max(intervalMs, 250));
        };
        const run = async () => {
            if (isShadowRestartPending()) {
                if (!cancelled) {
                    setRows(queryCache.get(cacheKey) || []);
                }
                schedule();
                return;
            }
            const controller = new AbortController();
            activeController = controller;
            queryHealthStore.recordAttempt(cacheKey);
            try {
                const response = await postApiJson('/api/query', { sql, params }, { signal: controller.signal });
                const nextRows = Array.isArray(response.rows) ? response.rows : [];
                queryCache.set(cacheKey, nextRows);
                queryHealthStore.recordSuccess(cacheKey);
                if (!cancelled) {
                    setRows(nextRows);
                }
            }
            catch (error) {
                if (cancelled || controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
                    return;
                }
                const cachedRows = queryCache.get(cacheKey);
                if (!cancelled && cachedRows) {
                    setRows(cachedRows);
                }
                queryHealthStore.recordFailure(cacheKey, pollingErrorMessage(error, 'Query API request failed.'));
            }
            finally {
                if (activeController === controller) {
                    activeController = null;
                }
                schedule();
            }
        };
        void run();
        return () => {
            cancelled = true;
            if (timer) {
                clearTimeout(timer);
            }
            activeController?.abort();
            queryHealthStore.unregister(cacheKey);
        };
    }, [cacheKey, intervalMs, paramsKey, refreshToken, sql]);
    return rows;
}
