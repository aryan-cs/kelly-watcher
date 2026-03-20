import fs from 'fs'
import {useEffect, useState} from 'react'
import {readIdentityMap} from './identities.js'
import {eventsPath, identityPath} from './paths.js'
import {useRefreshToken} from './refresh.js'

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

function eventIdentity(event: LiveEvent): string {
  return `${event.type}|${event.trade_id}|${event.ts}`
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
        const deduped: LiveEvent[] = []
        const seen = new Set<string>()
        for (const event of parsed) {
          const key = eventIdentity(event)
          if (seen.has(key)) continue
          seen.add(key)
          deduped.push(event)
        }
        setEvents(deduped.slice(-maxEvents))
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
