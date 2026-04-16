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
  api_error?: string
}

export interface BotStateResponse {
  state?: BotState
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
