import { useEffect, useState } from 'react';
import { ApiError, apiBaseUrl, fetchApiJson } from './api.js';
import { useRefreshToken } from './refresh.js';
export function useBotState(intervalMs = 2000) {
    const [state, setState] = useState({});
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let cancelled = false;
        const read = async () => {
            try {
                const response = await fetchApiJson('/api/bot-state');
                if (!cancelled) {
                    setState({
                        ...(response.state || {}),
                        api_base_url: apiBaseUrl,
                        api_error: ''
                    });
                }
            }
            catch (error) {
                const message = error instanceof ApiError && error.status === 401
                    ? `Backend API rejected the dashboard at ${apiBaseUrl}. Check KELLY_API_TOKEN.`
                    : error instanceof Error && String(error.message || '').trim()
                        ? String(error.message || '').trim()
                        : `Could not reach backend API at ${apiBaseUrl}.`;
                if (!cancelled) {
                    setState({
                        api_base_url: apiBaseUrl,
                        api_error: message
                    });
                }
            }
        };
        void read();
        const timer = setInterval(() => {
            void read();
        }, Math.max(intervalMs, 250));
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, [intervalMs, refreshToken]);
    return state;
}
