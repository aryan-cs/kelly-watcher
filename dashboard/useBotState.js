import { useEffect, useState } from 'react';
import { ApiError, apiBaseUrl, fetchApiJson } from './api.js';
import { useRefreshToken } from './refresh.js';
let botStateCache = { api_base_url: apiBaseUrl, api_error: '' };
let shadowRestartPending = false;
let shadowRestartRequestedAtMs = 0;
let shadowRestartPreviousSessionId = null;
let shadowRestartPreviousStartedAt = null;
const SHADOW_RESTART_PENDING_TIMEOUT_MS = 45000;
function shadowRestartPendingMessage() {
    if (!shadowRestartPending) {
        return '';
    }
    if (shadowRestartRequestedAtMs > 0 && Date.now() - shadowRestartRequestedAtMs > SHADOW_RESTART_PENDING_TIMEOUT_MS) {
        return 'Shadow restart is taking longer than expected. Waiting for a new backend session.';
    }
    return 'Shadow restart in progress. Waiting for backend to come back.';
}
function hasShadowRestartCompleted(nextState) {
    if (!shadowRestartPending) {
        return true;
    }
    const nextSessionId = String(nextState.session_id || '').trim();
    if (nextSessionId) {
        if (shadowRestartPreviousSessionId == null || nextSessionId !== shadowRestartPreviousSessionId) {
            shadowRestartPending = false;
            shadowRestartRequestedAtMs = 0;
            shadowRestartPreviousSessionId = null;
            shadowRestartPreviousStartedAt = null;
            return true;
        }
    }
    const nextStartedAt = Number(nextState.started_at || 0);
    if (nextStartedAt <= 0) {
        return false;
    }
    if (shadowRestartPreviousSessionId == null && shadowRestartPreviousStartedAt == null) {
        shadowRestartPending = false;
        shadowRestartRequestedAtMs = 0;
        shadowRestartPreviousSessionId = null;
        shadowRestartPreviousStartedAt = null;
        return true;
    }
    if (nextStartedAt !== shadowRestartPreviousStartedAt) {
        shadowRestartPending = false;
        shadowRestartRequestedAtMs = 0;
        shadowRestartPreviousSessionId = null;
        shadowRestartPreviousStartedAt = null;
        return true;
    }
    return false;
}
export function beginShadowRestartBotState() {
    shadowRestartPending = true;
    shadowRestartRequestedAtMs = Date.now();
    shadowRestartPreviousSessionId = String(botStateCache.session_id || '').trim() || null;
    shadowRestartPreviousStartedAt = Number(botStateCache.started_at || 0) || null;
    botStateCache = {
        ...botStateCache,
        api_base_url: apiBaseUrl,
        api_error: shadowRestartPendingMessage(),
        started_at: 0,
        startup_detail: 'Restarting shadow bot',
        mode: 'shadow',
        n_wallets: 0,
        loop_in_progress: false,
        last_poll_at: 0,
        last_activity_at: 0,
        bankroll_usd: undefined,
        last_event_count: 0,
        retrain_in_progress: false,
        last_retrain_started_at: 0,
        last_retrain_finished_at: 0,
        last_retrain_status: '',
        last_retrain_message: '',
        last_retrain_sample_count: 0,
        last_retrain_min_samples: 0,
        last_retrain_trigger: '',
        last_retrain_deployed: false
    };
}
export function isShadowRestartPending() {
    return shadowRestartPending;
}
export function useBotState(intervalMs = 1000) {
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
            if (!hasShadowRestartCompleted(nextState)) {
                const waitingState = {
                    ...botStateCache,
                    api_base_url: apiBaseUrl,
                    api_error: shadowRestartPendingMessage()
                };
                botStateCache = waitingState;
                if (!cancelled) {
                    setState(waitingState);
                }
                return;
            }
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
                api_error: shadowRestartPending ? shadowRestartPendingMessage() : message
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
