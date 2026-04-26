import { useEffect, useState } from 'react';
const emptyHealth = {
    lastAttemptAt: 0,
    lastSuccessAt: 0,
    lastErrorAt: 0,
    lastError: '',
    staleSourceCount: 0
};
export function pollingErrorMessage(error, fallback) {
    if (error instanceof Error) {
        const message = String(error.message || '').replace(/\s+/g, ' ').trim();
        if (message) {
            return message;
        }
    }
    return fallback;
}
export function createPollingHealthStore() {
    let health = { ...emptyHealth };
    const activeCounts = new Map();
    const failures = new Map();
    const listeners = new Set();
    const latestFailure = () => {
        let latest = null;
        for (const failure of failures.values()) {
            if (!latest || failure.at >= latest.at) {
                latest = failure;
            }
        }
        return latest;
    };
    const publish = (update = {}) => {
        const latest = latestFailure();
        health = {
            ...health,
            ...update,
            lastErrorAt: latest?.at || 0,
            lastError: latest?.message || '',
            staleSourceCount: failures.size
        };
        const snapshot = { ...health };
        for (const listener of listeners) {
            listener(snapshot);
        }
    };
    const hasActiveSource = (sourceKey) => (activeCounts.get(sourceKey) || 0) > 0;
    return {
        clear() {
            failures.clear();
            health = { ...emptyHealth };
            const snapshot = { ...health };
            for (const listener of listeners) {
                listener(snapshot);
            }
        },
        register(sourceKey) {
            activeCounts.set(sourceKey, (activeCounts.get(sourceKey) || 0) + 1);
        },
        unregister(sourceKey) {
            const nextCount = (activeCounts.get(sourceKey) || 0) - 1;
            if (nextCount > 0) {
                activeCounts.set(sourceKey, nextCount);
                return;
            }
            activeCounts.delete(sourceKey);
            if (failures.delete(sourceKey)) {
                publish();
            }
        },
        recordAttempt(sourceKey, at = Date.now()) {
            if (!hasActiveSource(sourceKey)) {
                return;
            }
            publish({ lastAttemptAt: at });
        },
        recordSuccess(sourceKey, at = Date.now()) {
            if (!hasActiveSource(sourceKey)) {
                return;
            }
            failures.delete(sourceKey);
            publish({ lastSuccessAt: at });
        },
        recordFailure(sourceKey, message, at = Date.now()) {
            if (!hasActiveSource(sourceKey)) {
                return;
            }
            failures.set(sourceKey, { at, message });
            publish({ lastAttemptAt: Math.max(health.lastAttemptAt, at) });
        },
        useHealth() {
            const [current, setCurrent] = useState(() => ({ ...health }));
            useEffect(() => {
                const listener = (nextHealth) => {
                    setCurrent({ ...nextHealth });
                };
                listeners.add(listener);
                setCurrent({ ...health });
                return () => {
                    listeners.delete(listener);
                };
            }, []);
            return current;
        }
    };
}
