import { useEffect, useState } from 'react';
import { fetchApiJson } from './api.js';
import { createPollingHealthStore, pollingErrorMessage } from './pollingHealth.js';
import { useRefreshToken } from './refresh.js';
import { isShadowRestartPending } from './useBotState.js';
const eventCache = new Map();
const EVENT_POLL_INTERVAL_MS = 2000;
const eventStreamHealthStore = createPollingHealthStore();
export function clearEventStreamCache() {
    eventCache.clear();
    eventStreamHealthStore.clear();
}
export function useEventStreamHealth() {
    return eventStreamHealthStore.useHealth();
}
export function useEventStream(maxEvents = 50) {
    const sourceKey = String(maxEvents);
    const [events, setEvents] = useState(() => eventCache.get(maxEvents) || []);
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let cancelled = false;
        let timer = null;
        let activeController = null;
        setEvents(eventCache.get(maxEvents) || []);
        eventStreamHealthStore.register(sourceKey);
        const schedule = () => {
            if (cancelled) {
                return;
            }
            timer = setTimeout(() => {
                void read();
            }, EVENT_POLL_INTERVAL_MS);
        };
        const read = async () => {
            if (isShadowRestartPending()) {
                if (!cancelled) {
                    setEvents(eventCache.get(maxEvents) || []);
                }
                schedule();
                return;
            }
            const controller = new AbortController();
            activeController = controller;
            eventStreamHealthStore.recordAttempt(sourceKey);
            try {
                const response = await fetchApiJson(`/api/events?max=${Math.max(1, Math.min(maxEvents, 1000))}`, { signal: controller.signal });
                const nextEvents = Array.isArray(response.events) ? response.events : [];
                eventCache.set(maxEvents, nextEvents);
                eventStreamHealthStore.recordSuccess(sourceKey);
                if (!cancelled) {
                    setEvents(nextEvents);
                }
            }
            catch (error) {
                if (cancelled || controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
                    return;
                }
                const cachedEvents = eventCache.get(maxEvents);
                if (!cancelled && cachedEvents) {
                    setEvents(cachedEvents);
                }
                eventStreamHealthStore.recordFailure(sourceKey, pollingErrorMessage(error, 'Event stream request failed.'));
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
            eventStreamHealthStore.unregister(sourceKey);
        };
    }, [maxEvents, refreshToken, sourceKey]);
    return events;
}
