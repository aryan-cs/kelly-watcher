import fs from 'fs'
import {useEffect, useState} from 'react'
import {eventsPath, identityPath} from './paths.js'
import {useRefreshToken} from './refresh.js'

export interface LiveEvent {
  type: 'incoming' | 'signal'
  trade_id: string
  market_id: string
  question: string
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

interface IdentityCachePayload {
  wallets?: Record<string, {username?: string}>
}

function readIdentityMap(): Map<string, string> {
  try {
    const payload = JSON.parse(fs.readFileSync(identityPath, 'utf8')) as IdentityCachePayload
    const lookup = new Map<string, string>()
    for (const [wallet, entry] of Object.entries(payload.wallets || {})) {
      const username = (entry?.username || '').trim()
      if (wallet && username) {
        lookup.set(wallet.toLowerCase(), username)
      }
    }
    return lookup
  } catch {
    return new Map()
  }
}

export function useEventStream(maxEvents = 50): LiveEvent[] {
  const [events, setEvents] = useState<LiveEvent[]>([])
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let lastMtimeMs = 0
    let lastIdentityMtimeMs = 0

    const read = () => {
      try {
        const stat = fs.statSync(eventsPath)
        const identityMtimeMs = fs.existsSync(identityPath) ? fs.statSync(identityPath).mtimeMs : 0
        if (stat.mtimeMs === lastMtimeMs && identityMtimeMs === lastIdentityMtimeMs) return
        lastMtimeMs = stat.mtimeMs
        lastIdentityMtimeMs = identityMtimeMs
        const identities = readIdentityMap()
        const lines = fs.readFileSync(eventsPath, 'utf8').trim().split('\n').filter(Boolean)
        const parsed = lines.map((line) => {
          const event = JSON.parse(line) as LiveEvent
          const wallet = event.trader?.trim().toLowerCase()
          if (wallet && !event.username?.trim()) {
            event.username = identities.get(wallet) || event.username
          }
          return event
        })
        setEvents(parsed.slice(-maxEvents))
      } catch {
        setEvents([])
      }
    }

    lastMtimeMs = 0
    lastIdentityMtimeMs = 0
    read()
    fs.watchFile(eventsPath, {interval: 500}, read)
    fs.watchFile(identityPath, {interval: 500}, read)
    return () => {
      fs.unwatchFile(eventsPath, read)
      fs.unwatchFile(identityPath, read)
    }
  }, [maxEvents, refreshToken])

  return events
}
