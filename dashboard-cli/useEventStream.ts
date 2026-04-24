import {useEffect, useState} from 'react'
import {fetchApiJson} from './api.js'
import {useRefreshToken} from './refresh.js'
import {isShadowRestartPending} from './useBotState.js'

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

const eventCache = new Map<number, LiveEvent[]>()

export function clearEventStreamCache(): void {
  eventCache.clear()
}

export function useEventStream(maxEvents = 50): LiveEvent[] {
  const [events, setEvents] = useState<LiveEvent[]>(() => eventCache.get(maxEvents) || [])
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let activeController: AbortController | null = null

    setEvents(eventCache.get(maxEvents) || [])

    const schedule = () => {
      if (cancelled) {
        return
      }
      timer = setTimeout(() => {
        void read()
      }, Math.max(500, 250))
    }

    const read = async () => {
      if (isShadowRestartPending()) {
        if (!cancelled) {
          setEvents(eventCache.get(maxEvents) || [])
        }
        schedule()
        return
      }
      const controller = new AbortController()
      activeController = controller
      try {
        const response = await fetchApiJson<EventsResponse>(
          `/api/events?max=${Math.max(1, Math.min(maxEvents, 1000))}`,
          {signal: controller.signal}
        )
        const nextEvents = Array.isArray(response.events) ? response.events : []
        eventCache.set(maxEvents, nextEvents)
        if (!cancelled) {
          setEvents(nextEvents)
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
          return
        }
        const cachedEvents = eventCache.get(maxEvents)
        if (!cancelled && cachedEvents) {
          setEvents(cachedEvents)
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
  }, [maxEvents, refreshToken])

  return events
}
