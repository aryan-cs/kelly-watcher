import { useEffect, useState } from 'react';
import { fetchApiJson } from './api.js';
import { useRefreshToken } from './refresh.js';
export function useEventStream(maxEvents = 50) {
    const [events, setEvents] = useState([]);
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let cancelled = false;
        const read = async () => {
            try {
                const response = await fetchApiJson(`/api/events?max=${Math.max(1, Math.min(maxEvents, 1000))}`);
                if (!cancelled) {
                    setEvents(Array.isArray(response.events) ? response.events : []);
                }
            }
            catch {
                if (!cancelled) {
                    setEvents([]);
                }
            }
        };
        void read();
        const timer = setInterval(() => {
            void read();
        }, 500);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, [maxEvents, refreshToken]);
    return events;
}
