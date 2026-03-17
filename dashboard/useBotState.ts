import fs from 'fs'
import {useEffect, useState} from 'react'
import {botStatePath} from './paths.js'
import {useRefreshToken} from './refresh.js'

export interface BotState {
  started_at?: number
  mode?: 'shadow' | 'live'
  n_wallets?: number
  poll_interval?: number
  last_poll_at?: number
  last_poll_duration_s?: number
  bankroll_usd?: number
  last_event_count?: number
}

export function useBotState(intervalMs = 2000): BotState {
  const [state, setState] = useState<BotState>({})
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let lastMtimeMs = 0

    const read = () => {
      try {
        const stat = fs.statSync(botStatePath)
        if (stat.mtimeMs === lastMtimeMs) return
        lastMtimeMs = stat.mtimeMs
        const payload = JSON.parse(fs.readFileSync(botStatePath, 'utf8')) as BotState
        setState(payload)
      } catch {
        setState({})
      }
    }

    lastMtimeMs = 0
    read()
    fs.watchFile(botStatePath, {interval: Math.min(intervalMs, 500)}, read)
    return () => fs.unwatchFile(botStatePath, read)
  }, [intervalMs, refreshToken])

  return state
}
