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
  shadow_history_state_known?: boolean
  resolved_shadow_trade_count?: number
  live_require_shadow_history_enabled?: boolean
  live_min_shadow_resolved?: number
  live_shadow_history_total_ready?: boolean
  resolved_shadow_since_last_promotion?: number
  live_min_shadow_resolved_since_last_promotion?: number
  live_shadow_history_ready?: boolean
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
  api_base_url?: string
  api_error?: string
}

interface BotStateResponse {
  state?: BotState
}

let botStateCache: BotState = {api_base_url: apiBaseUrl, api_error: ''}
let shadowRestartPending = false
let shadowRestartRequestedAtMs = 0
let shadowRestartPreviousSessionId: string | null = null
let shadowRestartPreviousStartedAt: number | null = null
const SHADOW_RESTART_PENDING_TIMEOUT_MS = 45000

function shadowRestartPendingMessage(): string {
  if (!shadowRestartPending) {
    return ''
  }
  if (shadowRestartRequestedAtMs > 0 && Date.now() - shadowRestartRequestedAtMs > SHADOW_RESTART_PENDING_TIMEOUT_MS) {
    return 'Shadow restart is taking longer than expected. Waiting for a new backend session.'
  }
  return 'Shadow restart in progress. Waiting for backend to come back.'
}

function hasShadowRestartCompleted(nextState: BotState): boolean {
  if (!shadowRestartPending) {
    return true
  }
  const nextSessionId = String(nextState.session_id || '').trim()
  if (nextSessionId) {
    if (shadowRestartPreviousSessionId == null || nextSessionId !== shadowRestartPreviousSessionId) {
      shadowRestartPending = false
      shadowRestartRequestedAtMs = 0
      shadowRestartPreviousSessionId = null
      shadowRestartPreviousStartedAt = null
      return true
    }
  }
  const nextStartedAt = Number(nextState.started_at || 0)
  if (nextStartedAt <= 0) {
    return false
  }
  if (shadowRestartPreviousSessionId == null && shadowRestartPreviousStartedAt == null) {
    shadowRestartPending = false
    shadowRestartRequestedAtMs = 0
    shadowRestartPreviousSessionId = null
    shadowRestartPreviousStartedAt = null
    return true
  }
  if (nextStartedAt !== shadowRestartPreviousStartedAt) {
    shadowRestartPending = false
    shadowRestartRequestedAtMs = 0
    shadowRestartPreviousSessionId = null
    shadowRestartPreviousStartedAt = null
    return true
  }
  return false
}

function shadowRestartPlaceholderState(state: BotState): BotState {
  return {
    ...state,
    api_base_url: apiBaseUrl,
    api_error: shadowRestartPendingMessage(),
    started_at: 0,
    startup_detail: 'Restarting shadow bot',
    startup_failed: false,
    startup_failure_message: '',
    startup_validation_failed: false,
    startup_validation_message: '',
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
    last_retrain_deployed: false,
    last_replay_search_started_at: 0,
    last_replay_search_finished_at: 0,
    last_replay_search_status: '',
    last_replay_search_message: '',
    last_replay_search_trigger: '',
    last_replay_search_run_id: 0,
    last_replay_search_candidate_count: 0,
    last_replay_search_feasible_count: 0,
    last_replay_search_best_score: null,
    last_replay_search_best_pnl_usd: null,
    last_replay_search_scope: 'shadow_only',
    last_replay_promotion_id: 0,
    last_replay_promotion_at: 0,
    last_replay_promotion_status: '',
    last_replay_promotion_message: '',
    last_replay_promotion_scope: 'shadow_only',
    last_replay_promotion_run_id: 0,
    last_replay_promotion_candidate_id: 0,
    last_replay_promotion_score_delta: null,
    last_replay_promotion_pnl_delta_usd: null,
    last_applied_replay_promotion_id: 0,
    last_applied_replay_promotion_at: 0,
    last_applied_replay_promotion_status: '',
    last_applied_replay_promotion_message: '',
    last_applied_replay_promotion_scope: 'shadow_only',
    last_applied_replay_promotion_run_id: 0,
    last_applied_replay_promotion_candidate_id: 0,
    last_applied_replay_promotion_score_delta: null,
    last_applied_replay_promotion_pnl_delta_usd: null,
    shadow_history_state_known: false,
    resolved_shadow_trade_count: 0,
    live_require_shadow_history_enabled: false,
    live_min_shadow_resolved: 0,
    live_shadow_history_total_ready: false,
    resolved_shadow_since_last_promotion: 0,
    live_min_shadow_resolved_since_last_promotion: 0,
    live_shadow_history_ready: false,
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
    model_loaded_at: 0
  }
}

function shadowRestartWaitingState(nextState: BotState): BotState {
  return {
    ...nextState,
    api_base_url: apiBaseUrl,
    api_error: shadowRestartPendingMessage(),
    startup_detail: String(nextState.startup_detail || '').trim() || 'Restarting shadow bot'
  }
}

export function beginShadowRestartBotState(): void {
  shadowRestartPending = true
  shadowRestartRequestedAtMs = Date.now()
  shadowRestartPreviousSessionId = String(botStateCache.session_id || '').trim() || null
  shadowRestartPreviousStartedAt = Number(botStateCache.started_at || 0) || null
  botStateCache = shadowRestartPlaceholderState(botStateCache)
}

export function isShadowRestartPending(): boolean {
  return shadowRestartPending
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
        const nextState = {
          ...(response.state || {}),
          api_base_url: apiBaseUrl,
          api_error: ''
        }
        const resolvedState = hasShadowRestartCompleted(nextState)
          ? nextState
          : shadowRestartWaitingState(nextState)
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
        const nextState = {
          ...botStateCache,
          api_base_url: apiBaseUrl,
          api_error: shadowRestartPending ? shadowRestartPendingMessage() : message
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
