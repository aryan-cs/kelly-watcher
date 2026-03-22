import {useEffect, useState} from 'react'
import {ApiError, apiBaseUrl, fetchApiJson} from './api.js'
import {useRefreshToken} from './refresh.js'

export interface BotState {
  started_at?: number
  last_loop_started_at?: number
  last_activity_at?: number
  loop_in_progress?: boolean
  startup_detail?: string
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
  api_base_url?: string
  api_error?: string
}

interface BotStateResponse {
  state?: BotState
}

export function useBotState(intervalMs = 2000): BotState {
  const [state, setState] = useState<BotState>({})
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false

    const read = async () => {
      try {
        const response = await fetchApiJson<BotStateResponse>('/api/bot-state')
        if (!cancelled) {
          setState({
            ...(response.state || {}),
            api_base_url: apiBaseUrl,
            api_error: ''
          })
        }
      } catch (error) {
        const message =
          error instanceof ApiError && error.status === 401
            ? `Backend API rejected the dashboard at ${apiBaseUrl}. Check KELLY_API_TOKEN.`
            : error instanceof Error && String(error.message || '').trim()
              ? String(error.message || '').trim()
              : `Could not reach backend API at ${apiBaseUrl}.`
        if (!cancelled) {
          setState({
            api_base_url: apiBaseUrl,
            api_error: message
          })
        }
      }
    }

    void read()
    const timer = setInterval(() => {
      void read()
    }, Math.max(intervalMs, 250))

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [intervalMs, refreshToken])

  return state
}
