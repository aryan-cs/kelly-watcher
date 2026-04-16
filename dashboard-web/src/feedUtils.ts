import {useEffect, useState} from 'react'
import {fetchApiJson, type EventsResponse, type LiveEvent} from './api'

export interface FeedEventsState {
  events: LiveEvent[]
  error: string
  loading: boolean
}

export const FEED_POLL_INTERVAL_MS = 1000
export const DISPLAY_ID_WIDTH = 6
export const DEFAULT_DECIMAL_PLACES = 3

export const feedTheme = {
  green: '#24ff7b',
  red: '#ff0f0f',
  blue: '#17cdff',
  yellow: '#ffd84d',
  white: '#ffffff',
  dim: '#c2ccd8'
} as const

export function joinClasses(...values: Array<string | undefined>): string {
  return values.filter(Boolean).join(' ')
}

export function formatDisplayId(value: number | null | undefined): string {
  if (value == null) return '-'
  return String(value).padStart(DISPLAY_ID_WIDTH, '0')
}

export function shortAddress(value: string): string {
  if (!value || value.length < 12) return value || '-'
  return `${value.slice(0, 6)}...${value.slice(-4)}`
}

export function formatFixedNumber(value: number | null | undefined, digits = DEFAULT_DECIMAL_PLACES): string {
  if (value == null || Number.isNaN(value)) return '-'
  return value.toFixed(digits)
}

export function formatFixedDollar(value: number | null | undefined, digits = DEFAULT_DECIMAL_PLACES): string {
  if (value == null || Number.isNaN(value)) return '-'
  return `$${value.toFixed(digits)}`
}

export function formatFixedPercent(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '-'
  return `${(value * 100).toFixed(digits)}%`
}

export function formatClock(ts: number | null | undefined): string {
  if (!ts) return '--:--:-- --'
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour12: true,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.replace('#', '')
  return [
    Number.parseInt(normalized.slice(0, 2), 16),
    Number.parseInt(normalized.slice(2, 4), 16),
    Number.parseInt(normalized.slice(4, 6), 16)
  ]
}

function rgbToHex(red: number, green: number, blue: number): string {
  return `#${[red, green, blue]
    .map((channel) => clamp(Math.round(channel), 0, 255).toString(16).padStart(2, '0'))
    .join('')}`
}

function blendHex(left: string, right: string, t: number): string {
  const [lr, lg, lb] = hexToRgb(left)
  const [rr, rg, rb] = hexToRgb(right)
  const mix = clamp(t, 0, 1)
  return rgbToHex(lr + (rr - lr) * mix, lg + (rg - lg) * mix, lb + (rb - lb) * mix)
}

export function probabilityColor(value: number): string {
  const normalized = clamp(value, 0, 1)
  if (normalized <= 0.5) {
    return blendHex(feedTheme.red, feedTheme.yellow, normalized * 2)
  }
  return blendHex(feedTheme.yellow, feedTheme.green, (normalized - 0.5) * 2)
}

export function positiveDollarColor(value: number, greenAt = 100): string {
  if (value <= 0) {
    return feedTheme.white
  }
  return blendHex(feedTheme.white, feedTheme.green, clamp(value / greenAt, 0, 1))
}

export function outcomeColor(side: string): string {
  const normalized = side.trim().toLowerCase()
  if (['yes', 'up', 'buy', 'long'].includes(normalized)) {
    return feedTheme.green
  }
  if (['no', 'down', 'sell', 'short'].includes(normalized)) {
    return feedTheme.red
  }
  return feedTheme.blue
}

export function decisionColor(decision: string | undefined): string {
  if (decision === 'ACCEPT') return feedTheme.green
  if (decision === 'REJECT') return feedTheme.red
  if (decision === 'SKIP' || decision === 'PAUSE') return feedTheme.yellow
  if (decision === 'IGNORE') return feedTheme.dim
  return feedTheme.white
}

export function normalizeReasonText(text: string): string {
  const normalized = text
    .replace(/\bmarket veto:\s*/gi, 'market veto, ')
    .replace(/\bKelly:\s*/g, 'Kelly, ')
    .trim()

  if (!normalized) {
    return 'trade was rejected for an unspecified reason'
  }

  const lower = normalized.toLowerCase()
  if (lower.startsWith('heuristic sizing, ')) {
    return normalizeReasonText(normalized.slice(normalized.indexOf(',') + 1).trim())
  }
  if (lower.startsWith('kelly, ')) {
    return normalizeReasonText(normalized.slice(normalized.indexOf(',') + 1).trim())
  }
  if (lower === 'observed sell - not copying exits yet') {
    return 'watched trader was exiting a position, and the bot only copies entries right now'
  }
  if (lower === 'missing market snapshot') {
    return 'market data was unavailable when this trade was observed'
  }
  if (lower === 'failed to build market features') {
    return 'could not build the market snapshot needed to score this trade'
  }
  if (lower === 'duplicate trade_id') {
    return 'this trade was already seen, so it was skipped as a duplicate'
  }
  if (lower === 'order in-flight') {
    return 'an order for this market was already being placed, so this trade was skipped'
  }
  if (lower === 'position already open') {
    return 'we already had this side of the market open, so the trade was skipped'
  }
  if (lower === 'passed heuristic threshold') {
    return 'signal confidence cleared the heuristic threshold'
  }
  if (lower === 'passed model edge threshold') {
    return 'model edge cleared the required threshold'
  }
  if (lower === 'passed all checks') {
    return 'signal cleared scoring, sizing, and risk checks'
  }
  if (lower === 'signal rejected') {
    return 'trade did not pass the signal checks'
  }
  if (lower === 'bankroll depleted') {
    return 'balance too low, no bankroll was available for a new trade'
  }
  if (lower === 'negative kelly - no edge at this price/confidence') {
    return 'Kelly sizing found no positive edge at this price, so the trade was skipped'
  }

  let match = normalized.match(/^market veto,\s*(.+)$/i)
  if (match) {
    const detail = match[1].trim()
    const expiresMatch = detail.match(/^expires in <(\d+)s$/i)
    if (expiresMatch) {
      return `too close to resolution, less than ${expiresMatch[1]} seconds remained to place the trade`
    }
    const horizonMatch = detail.match(/^beyond max horizon ([0-9.]+[smhdw])$/i)
    if (horizonMatch) {
      return `market resolves too far out, beyond the ${horizonMatch[1]} maximum horizon`
    }
    if (detail === 'crossed order book') {
      return 'market data looked invalid because the order book was crossed'
    }
    if (detail === 'missing order book') {
      return 'market data was incomplete because there was no order book snapshot'
    }
    if (detail === 'no visible order book depth') {
      return 'market looked too thin to trade because there was no visible order book depth'
    }
    if (detail === 'invalid market mid') {
      return 'market data looked invalid because the midpoint price was out of bounds'
    }
    if (detail === 'invalid order book values') {
      return 'market data looked invalid because the order book values were negative'
    }
  }

  match = normalized.match(/^heuristic conf ([0-9.]+) < min ([0-9.]+)$/i)
  if (match) {
    return `signal confidence was ${(Number(match[1]) * 100).toFixed(1)}%, below the ${(Number(match[2]) * 100).toFixed(1)}% minimum`
  }

  match = normalized.match(/^model edge (-?[0-9.]+) < threshold ([0-9.]+)$/i)
  if (match) {
    return `model edge was ${(Number(match[1]) * 100).toFixed(1)}%, below the ${(Number(match[2]) * 100).toFixed(1)}% threshold`
  }

  match = normalized.match(/^max size \$([0-9.]+) < min \$([0-9.]+)$/i)
  if (match) {
    return `balance too low, calculated size was $${match[1]} but minimum bet size is $${match[2]}`
  }

  match = normalized.match(/^available bankroll \$([0-9.]+) < min \$([0-9.]+)$/i)
  if (match) {
    return `balance too low, available bankroll was $${match[1]} but minimum bet size is $${match[2]}`
  }

  match = normalized.match(/^size \$([0-9.]+) <= 0$/i)
  if (match) {
    return `calculated trade size was $${match[1]}, so no order was placed`
  }

  match = normalized.match(/^conf ([0-9.]+) < min ([0-9.]+)$/i)
  if (match) {
    return `confidence was ${(Number(match[1]) * 100).toFixed(1)}%, below the ${(Number(match[2]) * 100).toFixed(1)}% minimum needed to place a trade`
  }

  match = normalized.match(/^score ([0-9.]+) < min ([0-9.]+)$/i)
  if (match) {
    return `heuristic score was ${(Number(match[1]) * 100).toFixed(1)}%, below the ${(Number(match[2]) * 100).toFixed(1)}% minimum needed to place a trade`
  }

  match = normalized.match(/^invalid price ([0-9.]+)$/i)
  if (match) {
    return `trade was skipped because the market price looked invalid (${match[1]})`
  }

  return normalized
}

export function resolveActionText(event: LiveEvent): string {
  const normalizedAction = String(event.action || '').trim().toLowerCase()
  if (normalizedAction === 'buy' || normalizedAction === 'entry') return 'BUY'
  if (normalizedAction === 'sell' || normalizedAction === 'exit') return 'SELL'
  if (normalizedAction) return normalizedAction.toUpperCase()
  if (event.decision === 'EXIT') {
    return 'SELL'
  }
  const normalizedReason = normalizeReasonText(event.reason || '').toLowerCase()
  if (
    normalizedReason.includes('exiting a position') ||
    normalizedReason.includes('unsupported trader action, sell')
  ) {
    return 'SELL'
  }
  return 'BUY'
}

export function buildTradeIdLookup(events: LiveEvent[]): Map<string, number> {
  const lookup = new Map<string, number>()
  let currentId = 0
  for (const event of events) {
    const tradeId = String(event.trade_id || '').trim()
    if (!tradeId || lookup.has(tradeId)) {
      continue
    }
    currentId += 1
    lookup.set(tradeId, currentId)
  }
  return lookup
}

export function useEventFeed(mode: 'mock' | 'api', mockEvents: LiveEvent[]): FeedEventsState {
  const [state, setState] = useState<FeedEventsState>({
    events: mode === 'mock' ? mockEvents : [],
    error: '',
    loading: mode === 'api'
  })

  useEffect(() => {
    if (mode !== 'api') {
      setState({events: mockEvents, error: '', loading: false})
      return undefined
    }

    let cancelled = false
    let timer: number | null = null
    let activeController: AbortController | null = null

    const schedule = () => {
      if (cancelled) {
        return
      }
      timer = window.setTimeout(() => {
        void read()
      }, FEED_POLL_INTERVAL_MS)
    }

    const read = async () => {
      const controller = new AbortController()
      activeController = controller
      try {
        const response = await fetchApiJson<EventsResponse>('/api/events?max=250', {signal: controller.signal})
        if (cancelled) {
          return
        }
        setState({
          events: Array.isArray(response.events) ? response.events : [],
          error: '',
          loading: false
        })
      } catch (error) {
        if (cancelled || controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
          return
        }
        setState((current) => ({
          events: current.events,
          error: error instanceof Error ? error.message : 'FAILED TO LOAD EVENTS.',
          loading: false
        }))
      } finally {
        if (activeController === controller) {
          activeController = null
        }
        schedule()
      }
    }

    setState((current) => ({
      ...current,
      loading: current.events.length <= 0
    }))
    void read()

    return () => {
      cancelled = true
      if (timer !== null) {
        window.clearTimeout(timer)
      }
      activeController?.abort()
    }
  }, [mockEvents, mode])

  return state
}
