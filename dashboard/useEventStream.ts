import {useEffect, useState} from 'react'
import {fetchApiJson} from './api.js'
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

interface EventsResponse {
  events?: LiveEvent[]
}

export function useEventStream(maxEvents = 50): LiveEvent[] {
  const [events, setEvents] = useState<LiveEvent[]>([])
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false

    const read = async () => {
      try {
        const response = await fetchApiJson<EventsResponse>(`/api/events?max=${Math.max(1, Math.min(maxEvents, 1000))}`)
        if (!cancelled) {
          setEvents(Array.isArray(response.events) ? response.events : [])
        }
      } catch {
        if (!cancelled) {
          setEvents([])
        }
      }
    }

    void read()
    const timer = setInterval(() => {
      void read()
    }, 500)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [maxEvents, refreshToken])

  return events
}
