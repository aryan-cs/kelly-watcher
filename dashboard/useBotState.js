import { useEffect, useState } from 'react';
import { fetchApiJson } from './api.js';
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
                    setState(response.state || {});
                }
            }
            catch {
                if (!cancelled) {
                    setState({});
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
