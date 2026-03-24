import { useEffect, useState } from 'react';
import { fetchApiJson } from './api.js';
import { useRefreshToken } from './refresh.js';
import { isShadowRestartPending } from './useBotState.js';
let identityCache = new Map();
export function clearIdentityCache() {
    identityCache = new Map();
}
export function isPlaceholderUsername(username, wallet) {
    const display = (username || '').trim();
    if (!display) {
        return true;
    }
    const normalizedWallet = (wallet || '').trim().toLowerCase();
    const normalizedUsername = display.toLowerCase();
    if (normalizedWallet && normalizedUsername === normalizedWallet) {
        return true;
    }
    if (normalizedWallet && normalizedUsername.startsWith(`${normalizedWallet}-`)) {
        const suffix = normalizedUsername.slice(normalizedWallet.length + 1);
        if (/^\d+$/.test(suffix)) {
            return true;
        }
    }
    return false;
}
function normalizeIdentityMap(payload) {
    const lookup = new Map();
    for (const [wallet, usernameValue] of Object.entries(payload.wallets || {})) {
        const normalizedWallet = wallet.trim().toLowerCase();
        const username = String(usernameValue || '').trim();
        if (!normalizedWallet || isPlaceholderUsername(username, normalizedWallet)) {
            continue;
        }
        lookup.set(normalizedWallet, username);
    }
    return lookup;
}
export function readIdentityMap() {
    return new Map(identityCache);
}
export function useIdentityMap(intervalMs = 1000) {
    const [lookup, setLookup] = useState(() => readIdentityMap());
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let cancelled = false;
        let timer = null;
        setLookup(new Map(identityCache));
        const schedule = () => {
            if (cancelled) {
                return;
            }
            timer = setTimeout(() => {
                void read();
            }, Math.max(intervalMs, 250));
        };
        const read = async () => {
            if (isShadowRestartPending()) {
                if (!cancelled) {
                    setLookup(new Map(identityCache));
                }
                schedule();
                return;
            }
            try {
                const payload = await fetchApiJson('/api/identities');
                const nextLookup = normalizeIdentityMap(payload);
                identityCache = nextLookup;
                if (!cancelled) {
                    setLookup(new Map(nextLookup));
                }
            }
            catch {
                if (!cancelled) {
                    setLookup(new Map(identityCache));
                }
            }
            finally {
                schedule();
            }
        };
        void read();
        return () => {
            cancelled = true;
            if (timer) {
                clearTimeout(timer);
            }
        };
    }, [intervalMs, refreshToken]);
    return lookup;
}
