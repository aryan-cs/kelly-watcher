import { useEffect, useState } from 'react';
import { ApiError, apiBaseUrl, fetchApiJson } from './api.js';
import { useRefreshToken } from './refresh.js';
let botStateCache = { api_base_url: apiBaseUrl, api_error: '' };
export function beginShadowRestartBotState() {
    botStateCache = {
        ...botStateCache,
        api_base_url: apiBaseUrl,
        api_error: 'Shadow restart in progress. Waiting for backend to come back.',
        mode: 'shadow',
        loop_in_progress: false,
        last_poll_at: 0,
        last_activity_at: 0
    };
}
export function useBotState(intervalMs = 2000) {
    const [state, setState] = useState(() => ({ ...botStateCache }));
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let cancelled = false;
        let timer = null;
        let activeController = null;
        setState({ ...botStateCache });
        const schedule = () => {
            if (cancelled) {
                return;
            }
            timer = setTimeout(() => {
                void read();
            }, Math.max(intervalMs, 250));
        };
        const read = async () => {
            const controller = new AbortController();
            activeController = controller;
            try {
                const response = await fetchApiJson('/api/bot-state', { signal: controller.signal });
                const nextState = {
                    ...(response.state || {}),
                    api_base_url: apiBaseUrl,
                    api_error: ''
                };
                botStateCache = nextState;
                if (!cancelled) {
                    setState(nextState);
                }
            }
            catch (error) {
                if (cancelled || controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
                    return;
                }
                const message = error instanceof ApiError && error.status === 401
                    ? `Backend API rejected the dashboard at ${apiBaseUrl}. Check KELLY_API_TOKEN.`
                    : error instanceof Error && String(error.message || '').trim()
                        ? String(error.message || '').trim()
                        : `Could not reach backend API at ${apiBaseUrl}.`;
                const nextState = {
                    ...botStateCache,
                    api_base_url: apiBaseUrl,
                    api_error: message
                };
                botStateCache = nextState;
                if (!cancelled) {
                    setState(nextState);
                }
            }
            finally {
                if (activeController === controller) {
                    activeController = null;
                }
                schedule();
            }
        };
        void read();
        return () => {
            cancelled = true;
            if (timer) {
                clearTimeout(timer);
            }
            activeController?.abort();
        };
    }, [intervalMs, refreshToken]);
    return state;
}
