import {useEffect, useState} from 'react'
import {ApiError, apiBaseUrl, fetchApiJson} from './api.js'
import {useRefreshToken} from './refresh.js'

export interface BotState {
  session_id?: string
  started_at?: number
  last_loop_started_at?: number
  last_activity_at?: number
  loop_in_progress?: boolean
  startup_detail?: string
  startup_failed?: boolean
  startup_failure_message?: string
  startup_validation_failed?: boolean
  startup_validation_message?: string
  startup_blocked?: boolean
  startup_recovery_only?: boolean
  startup_block_reason?: string
  mode?: 'shadow' | 'live'
  n_wallets?: number
  poll_interval?: number
  last_poll_at?: number
  last_poll_duration_s?: number
  bankroll_usd?: number
  last_event_count?: number
  retrain_in_progress?: boolean
  retrain_started_at?: number
  last_retrain_started_at?: number
  last_retrain_finished_at?: number
  last_retrain_status?: string
  last_retrain_message?: string
  last_retrain_sample_count?: number
  last_retrain_min_samples?: number
  last_retrain_trigger?: string
  last_retrain_deployed?: boolean
  manual_retrain_pending?: boolean
  manual_retrain_requested_at?: number
  manual_retrain_message?: string
  last_replay_search_started_at?: number
  last_replay_search_finished_at?: number
  last_replay_search_status?: string
  last_replay_search_message?: string
  last_replay_search_trigger?: string
  last_replay_search_run_id?: number
  last_replay_search_candidate_count?: number
  last_replay_search_feasible_count?: number
  last_replay_search_best_score?: number | null
  last_replay_search_best_pnl_usd?: number | null
  last_replay_search_scope?: string
  last_replay_promotion_id?: number
  last_replay_promotion_at?: number
  last_replay_promotion_status?: string
  last_replay_promotion_message?: string
  last_replay_promotion_scope?: string
  last_replay_promotion_run_id?: number
  last_replay_promotion_candidate_id?: number
  last_replay_promotion_score_delta?: number | null
  last_replay_promotion_pnl_delta_usd?: number | null
  last_applied_replay_promotion_id?: number
  last_applied_replay_promotion_at?: number
  last_applied_replay_promotion_status?: string
  last_applied_replay_promotion_message?: string
  last_applied_replay_promotion_scope?: string
  last_applied_replay_promotion_run_id?: number
  last_applied_replay_promotion_candidate_id?: number
  last_applied_replay_promotion_score_delta?: number | null
  last_applied_replay_promotion_pnl_delta_usd?: number | null
  manual_trade_pending?: boolean
  manual_trade_requested_at?: number
  manual_trade_message?: string
  shadow_history_state_known?: boolean
  resolved_shadow_trade_count?: number
  live_require_shadow_history_enabled?: boolean
  live_min_shadow_resolved?: number
  live_shadow_history_total_ready?: boolean
  resolved_shadow_since_last_promotion?: number
  live_min_shadow_resolved_since_last_promotion?: number
  live_shadow_history_ready?: boolean
  shadow_history_epoch_known?: boolean
  shadow_history_epoch_started_at?: number
  shadow_history_epoch_source_label?: string
  shadow_history_epoch_active_scope_label?: string
  shadow_history_epoch_status?: string
  shadow_history_epoch_total_resolved?: number
  shadow_history_epoch_ready_count?: number
  shadow_history_epoch_blocked_count?: number
  shadow_history_epoch_min_resolved?: number
  shadow_history_epoch_routed_resolved?: number
  shadow_history_epoch_legacy_resolved?: number
  shadow_history_epoch_coverage_pct?: number | null
  shadow_history_epoch_ready?: boolean
  shadow_history_epoch_block_reason?: string
  shadow_snapshot_state_known?: boolean
  shadow_snapshot_scope?: string
  shadow_snapshot_started_at?: number
  shadow_snapshot_status?: string
  shadow_snapshot_resolved?: number
  shadow_snapshot_routed_resolved?: number
  shadow_snapshot_legacy_resolved?: number
  shadow_snapshot_coverage_pct?: number | null
  shadow_snapshot_ready?: boolean
  shadow_snapshot_total_pnl_usd?: number | null
  shadow_snapshot_return_pct?: number | null
  shadow_snapshot_profit_factor?: number | null
  shadow_snapshot_expectancy_usd?: number | null
  shadow_snapshot_block_reason?: string
  shadow_snapshot_block_state?: string
  shadow_snapshot_optimization_block_reason?: string
  routed_shadow_state_known?: boolean
  routed_shadow_status?: string
  routed_shadow_routed_resolved?: number
  routed_shadow_legacy_resolved?: number
  routed_shadow_min_resolved?: number
  routed_shadow_total_resolved?: number
  routed_shadow_coverage_pct?: number | null
  routed_shadow_ready?: boolean
  routed_shadow_block_reason?: string
  routed_shadow_total_pnl_usd?: number | null
  routed_shadow_return_pct?: number | null
  routed_shadow_profit_factor?: number | null
  routed_shadow_expectancy_usd?: number | null
  routed_shadow_data_warning?: string
  routed_shadow_epoch_known?: boolean
  routed_shadow_epoch_started_at?: number
  routed_shadow_epoch_source_label?: string
  routed_shadow_epoch_active_scope_label?: string
  routed_shadow_epoch_status?: string
  shadow_segment_state_known?: boolean
  shadow_segment_status?: string
  shadow_segment_scope?: string
  shadow_segment_scope_started_at?: number
  shadow_segment_min_resolved?: number
  shadow_segment_total?: number
  shadow_segment_ready_count?: number
  shadow_segment_positive_count?: number
  shadow_segment_negative_count?: number
  shadow_segment_blocked_count?: number
  shadow_segment_history_status?: string
  shadow_segment_routed_signals?: number
  shadow_segment_routed_acted?: number
  shadow_segment_routed_resolved?: number
  shadow_segment_legacy_resolved?: number
  shadow_segment_routing_coverage_pct?: number | null
  shadow_segment_summary_json?: string
  shadow_segment_block_reason?: string
  db_integrity_known?: boolean
  db_integrity_ok?: boolean
  db_integrity_message?: string
  db_recovery_state_known?: boolean
  db_recovery_candidate_ready?: boolean
  db_recovery_candidate_path?: string
  db_recovery_candidate_source_path?: string
  db_recovery_candidate_message?: string
  db_recovery_candidate_mode?: string
  db_recovery_candidate_evidence_ready?: boolean
  db_recovery_candidate_class_reason?: string
  db_recovery_latest_verified_backup_path?: string
  db_recovery_latest_verified_backup_at?: number
  db_recovery_shadow_state_known?: boolean
  db_recovery_shadow_candidate_path?: string
  db_recovery_shadow_status?: string
  db_recovery_shadow_acted?: number
  db_recovery_shadow_resolved?: number
  db_recovery_shadow_total_pnl_usd?: number | null
  db_recovery_shadow_return_pct?: number | null
  db_recovery_shadow_profit_factor?: number | null
  db_recovery_shadow_expectancy_usd?: number | null
  db_recovery_shadow_data_warning?: string
  db_recovery_shadow_segment_total?: number
  db_recovery_shadow_segment_ready_count?: number
  db_recovery_shadow_segment_blocked_count?: number
  db_recovery_shadow_history_status?: string
  db_recovery_shadow_min_resolved?: number
  db_recovery_shadow_routed_resolved?: number
  db_recovery_shadow_legacy_resolved?: number
  db_recovery_shadow_total_resolved?: number
  db_recovery_shadow_routing_coverage_pct?: number | null
  db_recovery_shadow_ready?: boolean
  db_recovery_shadow_segment_summary_json?: string
  db_recovery_shadow_block_reason?: string
  loaded_scorer?: string
  loaded_model_backend?: string
  heuristic_enabled?: boolean
  xgboost_enabled?: boolean
  model_artifact_exists?: boolean
  model_artifact_path?: string
  model_artifact_backend?: string
  model_artifact_contract?: number | null
  runtime_contract?: number | null
  model_artifact_label_mode?: string
  runtime_label_mode?: string
  model_runtime_compatible?: boolean
  model_fallback_reason?: string
  model_load_error?: string
  model_prediction_mode?: string
  model_loaded_at?: number
  model_training_scope?: string
  model_training_since_ts?: number
  model_training_routed_only?: boolean
  model_training_provenance_trusted?: boolean
  model_training_block_reason?: string
  shadow_restart_pending?: boolean
  shadow_restart_kind?: string
  shadow_restart_message?: string
  api_base_url?: string
  api_error?: string
}

interface BotStateResponse {
  state?: BotState
}

let botStateCache: BotState = {api_base_url: apiBaseUrl, api_error: ''}
let shadowRestartPending = false
type ShadowRestartKind = '' | 'shadow_reset' | 'db_recovery'
let shadowRestartKind: ShadowRestartKind = ''
let shadowRestartRequestedAtMs = 0
let shadowRestartPreviousSessionId: string | null = null
let shadowRestartPreviousStartedAt: number | null = null
const SHADOW_RESTART_PENDING_TIMEOUT_MS = 45000

function normalizeShadowRestartKind(kind: unknown): ShadowRestartKind {
  const normalized = String(kind || '').trim().toLowerCase()
  return normalized === 'shadow_reset' || normalized === 'db_recovery' ? normalized : ''
}

function shadowRestartPendingMessage(kind: ShadowRestartKind = shadowRestartKind): string {
  if (!shadowRestartPending) {
    return ''
  }
  if (shadowRestartRequestedAtMs > 0 && Date.now() - shadowRestartRequestedAtMs > SHADOW_RESTART_PENDING_TIMEOUT_MS) {
    return kind === 'db_recovery'
      ? 'Shadow DB recovery is taking longer than expected. Waiting for a new backend session.'
      : 'Shadow restart is taking longer than expected. Waiting for a new backend session.'
  }
  return kind === 'db_recovery'
    ? 'Shadow DB recovery in progress. Waiting for backend to come back.'
    : 'Shadow restart in progress. Waiting for backend to come back.'
}

function clearShadowRestartPending(): void {
  shadowRestartPending = false
  shadowRestartKind = ''
  shadowRestartRequestedAtMs = 0
  shadowRestartPreviousSessionId = null
  shadowRestartPreviousStartedAt = null
}

function resolveShadowRestartState(nextState: BotState): BotState {
  if (!shadowRestartPending) {
    return nextState
  }

  if (Boolean(nextState.shadow_restart_pending)) {
    const nextKind = normalizeShadowRestartKind(nextState.shadow_restart_kind) || shadowRestartKind
    shadowRestartKind = nextKind
    return nextKind && nextState.shadow_restart_kind !== nextKind
      ? {...nextState, shadow_restart_kind: nextKind}
      : nextState
  }

  const nextSessionId = String(nextState.session_id || '').trim()
  if (nextSessionId) {
    if (shadowRestartPreviousSessionId == null || nextSessionId !== shadowRestartPreviousSessionId) {
      clearShadowRestartPending()
      return nextState
    }
  }
  const nextStartedAt = Number(nextState.started_at || 0)
  if (nextStartedAt <= 0) {
    return shadowRestartWaitingState(nextState)
  }
  if (shadowRestartPreviousSessionId == null && shadowRestartPreviousStartedAt == null) {
    clearShadowRestartPending()
    return nextState
  }
  if (nextStartedAt !== shadowRestartPreviousStartedAt) {
    clearShadowRestartPending()
    return nextState
  }
  // Successful backend reads are authoritative. If the backend no longer reports
  // a restart as pending, stop forcing the local placeholder state.
  clearShadowRestartPending()
  return nextState
}

function shadowRestartPlaceholderState(state: BotState, kind: ShadowRestartKind, message = ''): BotState {
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
  }
}

function shadowRestartWaitingState(nextState: BotState): BotState {
  const nextKind = normalizeShadowRestartKind(nextState.shadow_restart_kind) || shadowRestartKind
  shadowRestartKind = nextKind
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
  }
}

export function beginShadowRestartBotState(kind: ShadowRestartKind, message = ''): void {
  shadowRestartPending = true
  shadowRestartKind = kind
  shadowRestartRequestedAtMs = Date.now()
  shadowRestartPreviousSessionId = String(botStateCache.session_id || '').trim() || null
  shadowRestartPreviousStartedAt = Number(botStateCache.started_at || 0) || null
  botStateCache = shadowRestartPlaceholderState(botStateCache, kind, message)
}

export function isShadowRestartPending(): boolean {
  return shadowRestartPending || Boolean(botStateCache.shadow_restart_pending)
}

export function useBotState(intervalMs = 1000): BotState {
  const [state, setState] = useState<BotState>(() => ({...botStateCache}))
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let activeController: AbortController | null = null

    setState({...botStateCache})

    const schedule = () => {
      if (cancelled) {
        return
      }
      timer = setTimeout(() => {
        void read()
      }, Math.max(intervalMs, 250))
    }

    const read = async () => {
      const controller = new AbortController()
      activeController = controller
      try {
        const response = await fetchApiJson<BotStateResponse>('/api/bot-state', {signal: controller.signal})
        const responseState = response.state || {}
        const nextState = {
          ...responseState,
          api_base_url: apiBaseUrl,
          api_error: '',
          shadow_restart_pending: Boolean(responseState.shadow_restart_pending),
          shadow_restart_kind: normalizeShadowRestartKind(responseState.shadow_restart_kind),
          shadow_restart_message: String(responseState.shadow_restart_message || '')
        }
        const resolvedState = resolveShadowRestartState(nextState)
        botStateCache = resolvedState
        if (!cancelled) {
          setState(resolvedState)
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
          return
        }
        const message =
          error instanceof ApiError && error.status === 401
            ? `Backend API rejected the dashboard at ${apiBaseUrl}. Check KELLY_API_TOKEN.`
            : error instanceof Error && String(error.message || '').trim()
              ? String(error.message || '').trim()
              : `Could not reach backend API at ${apiBaseUrl}.`
        const cachedShadowRestartPending = Boolean(botStateCache.shadow_restart_pending)
        const effectiveShadowRestartPending = cachedShadowRestartPending || shadowRestartPending
        const effectiveShadowRestartKind = cachedShadowRestartPending
          ? normalizeShadowRestartKind(botStateCache.shadow_restart_kind)
          : shadowRestartKind
        const effectiveShadowRestartMessage = cachedShadowRestartPending
          ? String(botStateCache.shadow_restart_message || '')
          : shadowRestartPending
            ? shadowRestartPendingMessage(effectiveShadowRestartKind)
            : ''
        const nextState = {
          ...botStateCache,
          api_base_url: apiBaseUrl,
          api_error: message,
          shadow_restart_pending: effectiveShadowRestartPending,
          shadow_restart_kind: effectiveShadowRestartKind,
          shadow_restart_message: effectiveShadowRestartMessage
        }
        botStateCache = nextState
        if (!cancelled) {
          setState(nextState)
        }
      } finally {
        if (activeController === controller) {
          activeController = null
        }
        schedule()
      }
    }

    void read()

    return () => {
      cancelled = true
      if (timer) {
        clearTimeout(timer)
      }
      activeController?.abort()
    }
  }, [intervalMs, refreshToken])

  return state
}
