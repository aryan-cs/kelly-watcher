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

let botStateCache: BotState = {api_base_url: apiBaseUrl, api_error: ''}

export function beginShadowRestartBotState(): void {
  botStateCache = {
    ...botStateCache,
    api_base_url: apiBaseUrl,
    api_error: 'Shadow restart in progress. Waiting for backend to come back.',
    mode: 'shadow',
    loop_in_progress: false,
    last_poll_at: 0,
    last_activity_at: 0
  }
}

export function useBotState(intervalMs = 2000): BotState {
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
        botStateCache = nextState
        if (!cancelled) {
          setState(nextState)
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
          api_error: message
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
