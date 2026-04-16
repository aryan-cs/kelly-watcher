import { useEffect, useState } from 'react';
import { ApiError, postApiJson } from './api.js';
import { useRefreshToken } from './refresh.js';
import { isShadowQueryBlocked } from './useBotState.js';
const queryCache = new Map();
export function clearQueryCache() {
    queryCache.clear();
}
export function useQuery(sql, params = [], intervalMs = 1000) {
    const paramsKey = JSON.stringify(params);
    const cacheKey = `${sql}\u0000${paramsKey}`;
    const [rows, setRows] = useState(() => isShadowQueryBlocked() ? [] : queryCache.get(cacheKey) || []);
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let cancelled = false;
        let timer = null;
        let activeController = null;
        setRows(isShadowQueryBlocked() ? [] : queryCache.get(cacheKey) || []);
        const schedule = () => {
            if (cancelled) {
                return;
            }
            timer = setTimeout(() => {
                void run();
            }, Math.max(intervalMs, 250));
        };
        const run = async () => {
            if (isShadowQueryBlocked()) {
                queryCache.delete(cacheKey);
                if (!cancelled) {
                    setRows([]);
                }
                schedule();
                return;
            }
            const controller = new AbortController();
            activeController = controller;
            try {
                const response = await postApiJson('/api/query', { sql, params }, { signal: controller.signal });
                const nextRows = Array.isArray(response.rows) ? response.rows : [];
                queryCache.set(cacheKey, nextRows);
                if (!cancelled) {
                    setRows(nextRows);
                }
            }
            catch (error) {
                if (cancelled || controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
                    return;
                }
                if (error instanceof ApiError && error.status === 409) {
                    queryCache.delete(cacheKey);
                    if (!cancelled) {
                        setRows([]);
                    }
                    return;
                }
                const cachedRows = queryCache.get(cacheKey);
                if (!cancelled && cachedRows) {
                    setRows(cachedRows);
                }
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
        };
    }, [cacheKey, sql, paramsKey, intervalMs, refreshToken]);
    return rows;
}
