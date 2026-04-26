import {useEffect, useState} from 'react'
import {fetchApiJson} from './api.js'
import {createPollingHealthStore, pollingErrorMessage, type PollingHealth} from './pollingHealth.js'
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
const EVENT_POLL_INTERVAL_MS = 2000
const eventStreamHealthStore = createPollingHealthStore()

export type EventStreamHealth = PollingHealth

export function clearEventStreamCache(): void {
  eventCache.clear()
  eventStreamHealthStore.clear()
}

export function useEventStreamHealth(): EventStreamHealth {
  return eventStreamHealthStore.useHealth()
}

export function useEventStream(maxEvents = 50): LiveEvent[] {
  const sourceKey = String(maxEvents)
  const [events, setEvents] = useState<LiveEvent[]>(() => eventCache.get(maxEvents) || [])
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let activeController: AbortController | null = null

    setEvents(eventCache.get(maxEvents) || [])
    eventStreamHealthStore.register(sourceKey)

    const schedule = () => {
      if (cancelled) {
        return
      }
      timer = setTimeout(() => {
        void read()
      }, EVENT_POLL_INTERVAL_MS)
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
      eventStreamHealthStore.recordAttempt(sourceKey)
      try {
        const response = await fetchApiJson<EventsResponse>(
          `/api/events?max=${Math.max(1, Math.min(maxEvents, 1000))}`,
          {signal: controller.signal}
        )
        const nextEvents = Array.isArray(response.events) ? response.events : []
        eventCache.set(maxEvents, nextEvents)
        eventStreamHealthStore.recordSuccess(sourceKey)
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
        eventStreamHealthStore.recordFailure(sourceKey, pollingErrorMessage(error, 'Event stream request failed.'))
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
      eventStreamHealthStore.unregister(sourceKey)
    }
  }, [maxEvents, refreshToken, sourceKey])

  return events
}
