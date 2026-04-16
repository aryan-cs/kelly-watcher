import { useEffect, useState } from 'react';
import { ApiError, apiBaseUrl, fetchApiJson } from './api.js';
import { useRefreshToken } from './refresh.js';
let botStateCache = {
    api_base_url: apiBaseUrl,
    api_error: '',
    trade_log_archive_state_known: false,
    trade_log_archive_enabled: false,
    trade_log_archive_status: 'checking',
    trade_log_archive_pending: false,
    trade_log_archive_requested_at: 0,
    trade_log_archive_request_message: '',
    trade_log_archive_db_path: '',
    trade_log_archive_archive_path: '',
    trade_log_archive_archive_exists: false,
    trade_log_archive_active_db_size_bytes: 0,
    trade_log_archive_active_db_allocated_bytes: 0,
    trade_log_archive_archive_db_size_bytes: 0,
    trade_log_archive_archive_db_allocated_bytes: 0,
    trade_log_archive_active_row_count: 0,
    trade_log_archive_archive_row_count: 0,
    trade_log_archive_eligible_row_count: 0,
    trade_log_archive_cutoff_ts: 0,
    trade_log_archive_preserve_since_ts: 0,
    trade_log_archive_last_run_at: 0,
    trade_log_archive_last_candidate_count: 0,
    trade_log_archive_last_archived_count: 0,
    trade_log_archive_last_deleted_count: 0,
    trade_log_archive_last_vacuumed: false,
    trade_log_archive_last_message: '',
    trade_log_archive_block_reason: '',
    storage_state_known: false,
    storage_save_dir_size_bytes: 0,
    storage_data_dir_size_bytes: 0,
    storage_log_dir_size_bytes: 0,
    storage_trading_db_size_bytes: 0,
    storage_trading_db_allocated_bytes: 0,
    storage_trade_log_archive_db_size_bytes: 0,
    storage_trade_log_archive_db_allocated_bytes: 0,
    storage_identity_cache_size_bytes: 0,
    storage_events_file_size_bytes: 0,
    storage_background_log_size_bytes: 0,
    storage_model_artifact_size_bytes: 0,
    storage_artifact_quarantine_file_count: 0,
    storage_artifact_quarantine_size_bytes: 0,
    storage_db_recovery_quarantine_file_count: 0,
    storage_db_recovery_quarantine_size_bytes: 0,
    storage_message: ''
};
let shadowRestartPending = false;
let shadowRestartKind = '';
let shadowRestartRequestedAtMs = 0;
let shadowRestartPreviousSessionId = null;
let shadowRestartPreviousStartedAt = null;
const SHADOW_RESTART_PENDING_TIMEOUT_MS = 45000;
function normalizeShadowRestartKind(kind) {
    const normalized = String(kind || '').trim().toLowerCase();
    return normalized === 'shadow_reset' || normalized === 'db_recovery' ? normalized : '';
}
function shadowRestartPendingMessage(kind = shadowRestartKind) {
    if (!shadowRestartPending) {
        return '';
    }
    if (shadowRestartRequestedAtMs > 0 && Date.now() - shadowRestartRequestedAtMs > SHADOW_RESTART_PENDING_TIMEOUT_MS) {
        return kind === 'db_recovery'
            ? 'Shadow DB recovery is taking longer than expected. Waiting for a new backend session.'
            : 'Shadow restart is taking longer than expected. Waiting for a new backend session.';
    }
    return kind === 'db_recovery'
        ? 'Shadow DB recovery in progress. Waiting for backend to come back.'
        : 'Shadow restart in progress. Waiting for backend to come back.';
}
function clearShadowRestartPending() {
    shadowRestartPending = false;
    shadowRestartKind = '';
    shadowRestartRequestedAtMs = 0;
    shadowRestartPreviousSessionId = null;
    shadowRestartPreviousStartedAt = null;
}
function resolveShadowRestartState(nextState) {
    if (!shadowRestartPending) {
        return nextState;
    }
    if (Boolean(nextState.shadow_restart_pending)) {
        const nextKind = normalizeShadowRestartKind(nextState.shadow_restart_kind) || shadowRestartKind;
        shadowRestartKind = nextKind;
        return nextKind && nextState.shadow_restart_kind !== nextKind
            ? { ...nextState, shadow_restart_kind: nextKind }
            : nextState;
    }
    const nextSessionId = String(nextState.session_id || '').trim();
    if (nextSessionId) {
        if (shadowRestartPreviousSessionId == null || nextSessionId !== shadowRestartPreviousSessionId) {
            clearShadowRestartPending();
            return nextState;
        }
    }
    const nextStartedAt = Number(nextState.started_at || 0);
    if (nextStartedAt <= 0) {
        return shadowRestartWaitingState(nextState);
    }
    if (shadowRestartPreviousSessionId == null && shadowRestartPreviousStartedAt == null) {
        clearShadowRestartPending();
        return nextState;
    }
    if (nextStartedAt !== shadowRestartPreviousStartedAt) {
        clearShadowRestartPending();
        return nextState;
    }
    // Successful backend reads are authoritative. If the backend no longer reports
    // a restart as pending, stop forcing the local placeholder state.
    clearShadowRestartPending();
    return nextState;
}
function shadowRestartPlaceholderState(state, kind, message = '') {
    return {
        ...state,
        api_base_url: apiBaseUrl,
        api_error: '',
        shadow_restart_pending: true,
        shadow_restart_kind: kind,
        shadow_restart_message: String(message || '').trim() || shadowRestartPendingMessage(kind),
        // Preserve durable replay/retrain/promotion/gate truth while clearing only
        // session-scoped runtime fields during the restart handoff window.
        started_at: 0,
        startup_detail: kind === 'db_recovery' ? 'Recovering shadow database' : 'Restarting shadow bot',
        startup_failed: false,
        startup_failure_message: '',
        startup_validation_failed: false,
        startup_validation_message: '',
        startup_blocked: false,
        startup_recovery_only: false,
        startup_block_reason: '',
        mode: 'shadow',
        n_wallets: 0,
        loop_in_progress: false,
        last_poll_at: 0,
        last_activity_at: 0,
        bankroll_usd: undefined,
        last_event_count: 0,
        retrain_in_progress: false,
        loaded_scorer: 'heuristic',
        loaded_model_backend: 'heuristic',
        model_artifact_exists: false,
        model_artifact_path: '',
        model_artifact_backend: '',
        model_artifact_contract: null,
        runtime_contract: null,
        model_artifact_label_mode: '',
        runtime_label_mode: '',
        model_runtime_compatible: false,
        model_fallback_reason: '',
        model_load_error: '',
        model_prediction_mode: '',
        model_loaded_at: 0,
        model_training_scope: 'unknown',
        model_training_since_ts: 0,
        model_training_routed_only: false,
        model_training_provenance_trusted: false,
        model_training_block_reason: ''
    };
}
function shadowRestartWaitingState(nextState) {
    const nextKind = normalizeShadowRestartKind(nextState.shadow_restart_kind) || shadowRestartKind;
    shadowRestartKind = nextKind;
    return {
        ...nextState,
        api_base_url: apiBaseUrl,
        api_error: '',
        shadow_restart_pending: true,
        shadow_restart_kind: nextKind,
        shadow_restart_message: String(nextState.shadow_restart_message || '').trim() || shadowRestartPendingMessage(nextKind),
        startup_detail: String(nextState.startup_detail || '').trim() || (nextKind === 'db_recovery' ? 'Recovering shadow database' : 'Restarting shadow bot'),
        startup_blocked: false,
        startup_recovery_only: false,
        startup_block_reason: ''
    };
}
export function beginShadowRestartBotState(kind, message = '') {
    shadowRestartPending = true;
    shadowRestartKind = kind;
    shadowRestartRequestedAtMs = Date.now();
    shadowRestartPreviousSessionId = String(botStateCache.session_id || '').trim() || null;
    shadowRestartPreviousStartedAt = Number(botStateCache.started_at || 0) || null;
    botStateCache = shadowRestartPlaceholderState(botStateCache, kind, message);
}
export function isShadowRestartPending() {
    return shadowRestartPending || Boolean(botStateCache.shadow_restart_pending);
}
export function isShadowQueryBlocked() {
    const startupDetail = String(botStateCache.startup_detail || '').trim();
    const startupBlocked = Boolean(botStateCache.startup_blocked) || /startup blocked/i.test(startupDetail);
    const startupRecoveryOnly = Boolean(botStateCache.startup_recovery_only) || startupBlocked;
    const dbIntegrityKnown = Boolean(botStateCache.db_integrity_known);
    const dbIntegrityOk = Boolean(botStateCache.db_integrity_ok);
    return isShadowRestartPending() || startupRecoveryOnly || (dbIntegrityKnown && !dbIntegrityOk);
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
                const responseState = response.state || {};
                const nextState = {
                    ...responseState,
                    api_base_url: apiBaseUrl,
                    api_error: '',
                    shadow_restart_pending: Boolean(responseState.shadow_restart_pending),
                    shadow_restart_kind: normalizeShadowRestartKind(responseState.shadow_restart_kind),
                    shadow_restart_message: String(responseState.shadow_restart_message || '')
                };
                const resolvedState = resolveShadowRestartState(nextState);
                botStateCache = resolvedState;
                if (!cancelled) {
                    setState(resolvedState);
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
                const cachedShadowRestartPending = Boolean(botStateCache.shadow_restart_pending);
                const effectiveShadowRestartPending = cachedShadowRestartPending || shadowRestartPending;
                const effectiveShadowRestartKind = cachedShadowRestartPending
                    ? normalizeShadowRestartKind(botStateCache.shadow_restart_kind)
                    : shadowRestartKind;
                const effectiveShadowRestartMessage = cachedShadowRestartPending
                    ? String(botStateCache.shadow_restart_message || '')
                    : shadowRestartPending
                        ? shadowRestartPendingMessage(effectiveShadowRestartKind)
                        : '';
                const nextState = {
                    ...botStateCache,
                    api_base_url: apiBaseUrl,
                    api_error: message,
                    shadow_restart_pending: effectiveShadowRestartPending,
                    shadow_restart_kind: effectiveShadowRestartKind,
                    shadow_restart_message: effectiveShadowRestartMessage
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
