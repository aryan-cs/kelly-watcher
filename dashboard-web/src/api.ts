export class ApiError extends Error {
  status: number

  constructor(message: string, status = 500) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const rawApiBaseUrl = String(import.meta.env.VITE_KELLY_API_BASE_URL || '').trim()
export const apiBaseUrl = rawApiBaseUrl.replace(/\/+$/, '')
const envApiToken = String(import.meta.env.VITE_KELLY_API_TOKEN || '').trim()
export const hasEnvironmentApiToken = Boolean(envApiToken)
const tokenStorageKey = 'kelly-watcher.dashboard-api-token'

export interface ApiHealth {
  ok?: boolean
  host?: string
  port?: number
  auth_required?: boolean
}

export interface BotState {
  session_id?: string
  started_at?: number
  last_loop_started_at?: number
  last_activity_at?: number
  loop_in_progress?: boolean
  mode?: 'shadow' | 'live'
  configured_mode?: 'shadow' | 'live'
  mode_block_reason?: string
  startup_detail?: string
  startup_failed?: boolean
  startup_validation_failed?: boolean
  startup_failure_message?: string
  startup_validation_message?: string
  startup_blocked?: boolean
  startup_recovery_only?: boolean
  startup_block_reason?: string
  n_wallets?: number
  poll_interval?: number
  last_poll_at?: number
  last_poll_duration_s?: number
  bankroll_usd?: number
  last_event_count?: number
  retrain_in_progress?: boolean
  last_retrain_started_at?: number
  last_retrain_finished_at?: number
  last_retrain_status?: string
  last_retrain_message?: string
  last_replay_search_started_at?: number
  last_replay_search_finished_at?: number
  last_replay_search_status?: string
  last_replay_search_message?: string
  resolved_shadow_trade_count?: number
  live_require_shadow_history_enabled?: boolean
  live_shadow_history_ready?: boolean
  live_shadow_history_total_ready?: boolean
  shadow_snapshot_state_known?: boolean
  shadow_snapshot_scope?: string
  shadow_snapshot_status?: string
  shadow_snapshot_resolved?: number
  shadow_snapshot_routed_resolved?: number
  shadow_snapshot_ready?: boolean
  shadow_snapshot_block_reason?: string
  loaded_scorer?: string
  loaded_model_backend?: string
  model_runtime_compatible?: boolean
  model_fallback_reason?: string
  model_prediction_mode?: string
  model_load_error?: string
  model_loaded_at?: number
  manual_retrain_pending?: boolean
  manual_trade_pending?: boolean
  shadow_restart_pending?: boolean
  shadow_restart_kind?: string
  shadow_restart_message?: string
  db_integrity_known?: boolean
  db_integrity_ok?: boolean
  db_integrity_message?: string
  db_recovery_state_known?: boolean
  db_recovery_candidate_ready?: boolean
  db_recovery_candidate_path?: string
  db_recovery_candidate_source_path?: string
  db_recovery_candidate_mode?: string
  db_recovery_candidate_message?: string
  db_recovery_candidate_class_reason?: string
  db_recovery_latest_verified_backup_path?: string
  db_recovery_latest_verified_backup_at?: number
  db_recovery_inventory?: DbRecoveryInventoryEntry[]
  db_recovery_inventory_count?: number
  wallet_discovery_last_scan_at?: number
  wallet_discovery_last_scan_ok?: boolean
  wallet_discovery_scanned_count?: number
  wallet_discovery_candidate_count?: number
  wallet_discovery_last_scan_message?: string
  trade_log_archive_enabled?: boolean
  trade_log_archive_state_known?: boolean
  trade_log_archive_status?: string
  trade_log_archive_pending?: boolean
  trade_log_archive_requested_at?: number
  trade_log_archive_request_message?: string
  trade_log_archive_active_db_size_bytes?: number
  trade_log_archive_active_db_allocated_bytes?: number
  trade_log_archive_archive_db_size_bytes?: number
  trade_log_archive_archive_db_allocated_bytes?: number
  trade_log_archive_active_row_count?: number
  trade_log_archive_archive_row_count?: number
  trade_log_archive_eligible_row_count?: number
  trade_log_archive_cutoff_ts?: number
  trade_log_archive_preserve_since_ts?: number
  trade_log_archive_last_run_at?: number
  trade_log_archive_last_candidate_count?: number
  trade_log_archive_last_archived_count?: number
  trade_log_archive_last_deleted_count?: number
  trade_log_archive_last_vacuumed?: boolean
  trade_log_archive_message?: string
  trade_log_archive_block_reason?: string
  storage_state_known?: boolean
  storage_save_dir_size_bytes?: number
  storage_data_dir_size_bytes?: number
  storage_log_dir_size_bytes?: number
  storage_trading_db_size_bytes?: number
  storage_trading_db_allocated_bytes?: number
  storage_trade_log_archive_db_size_bytes?: number
  storage_trade_log_archive_db_allocated_bytes?: number
  storage_message?: string
  api_error?: string
}

export interface BotStateResponse {
  state?: BotState
}

export interface DbRecoveryInventoryEntry {
  path?: string
  kind?: string
  compressed?: boolean
  ready?: boolean
  selected?: boolean
  mtime?: number
  message?: string
}

export interface LiveEvent {
  type: 'incoming' | 'signal'
  trade_id: string
  market_id: string
  question: string
  market_url?: string
  side: string
  action?: string
  price: number
  shares?: number
  amount_usd?: number
  size_usd: number
  username?: string
  trader?: string
  decision?: string
  confidence?: number
  reason?: string
  shadow?: boolean
  order_id?: string | null
  ts: number
}

export interface EventsResponse {
  events?: LiveEvent[]
}

export interface DiscoveryCandidate {
  wallet_address?: string
  username?: string
  source_labels?: string[]
  follow_score?: number
  accepted?: boolean
  reject_reason?: string
  style?: string
  watch_style?: string
  leaderboard_rank?: number
  recent_buys?: number
  median_buy_lead_hours?: number
  late_buy_ratio?: number
  realized_pnl_usd?: number
  updated_at?: number
  [key: string]: unknown
}

export interface DiscoveryCandidatesResponse {
  ok?: boolean
  source?: string
  count?: number
  ready_count?: number
  review_count?: number
  candidates?: DiscoveryCandidate[]
  message?: string
  scanned_count?: number
  accepted_count?: number
  stored_count?: number
  started_at?: number
  finished_at?: number
}

export interface ManagedWallet {
  wallet_address?: string
  username?: string
  registry_source?: string
  source?: string
  tracking_enabled?: boolean
  status?: string
  status_reason?: string
  added_at?: number
  updated_at?: number
  disabled_at?: number
  disabled_reason?: string
  tracking_started_at?: number
  last_source_ts_at_status?: number
  discovery_score?: number
  discovery_accepted?: boolean
  discovery_reason?: string
  discovery_style?: string
  discovery_rank?: number
  discovery_sources?: string[]
  discovery_updated_at?: number
  post_promotion_promoted_at?: number
  post_promotion_baseline_at?: number
  post_promotion_boundary_action?: string
  post_promotion_boundary_source?: string
  post_promotion_boundary_reason?: string
  post_promotion_source?: string
  post_promotion_reason?: string
  post_promotion_total_buy_signals?: number
  post_promotion_uncopyable_skips?: number
  post_promotion_timing_skips?: number
  post_promotion_liquidity_skips?: number
  post_promotion_uncopyable_skip_rate?: number
  post_promotion_resolved_copied_count?: number
  post_promotion_resolved_copied_win_rate?: number
  post_promotion_resolved_copied_avg_return?: number
  post_promotion_resolved_copied_total_pnl_usd?: number
  post_promotion_last_resolved_at?: number
  post_promotion_evidence_ready?: boolean
  post_promotion_evidence_note?: string
  trust_tier?: string
  trust_size_multiplier?: number
  trust_note?: string
}

export interface ManagedWalletsResponse {
  ok?: boolean
  source?: string
  count?: number
  wallets?: ManagedWallet[]
  events?: WalletMembershipEvent[]
  event_source?: string
  event_count?: number
  message?: string
}

export interface WalletMembershipEvent {
  wallet_address?: string
  action?: string
  source?: string
  reason?: string
  created_at?: number
  payload?: Record<string, unknown>
}

export interface WalletMembershipEventsResponse {
  ok?: boolean
  source?: string
  count?: number
  events?: WalletMembershipEvent[]
  message?: string
}

export function getApiToken(): string {
  if (envApiToken) {
    return envApiToken
  }
  if (typeof window === 'undefined') {
    return ''
  }
  return String(window.localStorage.getItem(tokenStorageKey) || '').trim()
}

export function setApiToken(token: string): void {
  if (typeof window === 'undefined') {
    return
  }
  const normalized = token.trim()
  if (normalized) {
    window.localStorage.setItem(tokenStorageKey, normalized)
    return
  }
  window.localStorage.removeItem(tokenStorageKey)
}

function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path
  }
  if (!apiBaseUrl) {
    return path.startsWith('/') ? path : `/${path}`
  }
  return `${apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text()
  let payload: unknown = {}

  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      if (!response.ok) {
        throw new ApiError(text, response.status)
      }
      throw new ApiError('Invalid JSON response from backend API.', response.status)
    }
  }

  if (!response.ok) {
    const message =
      typeof payload === 'object' && payload && 'message' in payload
        ? String((payload as {message?: unknown}).message || '')
        : ''
    throw new ApiError(message || `Backend API request failed with status ${response.status}.`, response.status)
  }

  return payload as T
}

export async function fetchApiJson<T>(
  path: string,
  init: RequestInit = {},
  token = getApiToken()
): Promise<T> {
  const headers = new Headers(init.headers || {})
  headers.set('Accept', 'application/json')
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  let response: Response
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      headers
    })
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      throw error
    }
    const detail = error instanceof Error ? String(error.message || '').trim() : ''
    const suffix = detail ? ` ${detail}` : ''
    throw new ApiError(`Could not reach backend API at ${apiBaseUrl || 'this host'}.${suffix}`.trim(), 0)
  }
  return parseJsonResponse<T>(response)
}

export async function postApiJson<T>(
  path: string,
  body: Record<string, unknown> = {},
  token = getApiToken()
): Promise<T> {
  return fetchApiJson<T>(
    path,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    },
    token
  )
}
