import {startTransition, useEffect, useMemo, useRef, useState, type FormEvent} from 'react'
import {
  ApiError,
  apiBaseUrl,
  fetchApiJson,
  getApiToken,
  hasEnvironmentApiToken,
  setApiToken,
  type ApiHealth,
  type BotState,
  type BotStateResponse,
  type EventsResponse,
  type LiveEvent
} from './api'

interface PollState<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
  lastLoadedAt: number | null
}

interface MetricCardProps {
  label: string
  value: string
  meta: string
  tone?: 'default' | 'live' | 'shadow' | 'warning'
}

interface EventCardProps {
  event: LiveEvent
}

function humanizeStatus(value: string | undefined, fallback = 'Waiting'): string {
  const normalized = String(value || '').trim()
  if (!normalized) {
    return fallback
  }
  return normalized
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function formatCurrency(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return 'N/A'
  }
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: Math.abs(value) >= 100 ? 0 : 2
  }).format(value)
}

function formatNumber(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return 'N/A'
  }
  return new Intl.NumberFormat().format(value)
}

function formatPercent(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return 'N/A'
  }
  return `${value.toFixed(1)}%`
}

function formatTimestamp(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) {
    return 'N/A'
  }
  return new Date(seconds * 1000).toLocaleString()
}

function formatRelativeSeconds(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) {
    return 'N/A'
  }

  const deltaSeconds = Math.max(0, Math.round(Date.now() / 1000 - seconds))
  if (deltaSeconds < 10) {
    return 'just now'
  }
  if (deltaSeconds < 60) {
    return `${deltaSeconds}s ago`
  }

  const deltaMinutes = Math.floor(deltaSeconds / 60)
  if (deltaMinutes < 60) {
    return `${deltaMinutes}m ago`
  }

  const deltaHours = Math.floor(deltaMinutes / 60)
  if (deltaHours < 24) {
    return `${deltaHours}h ago`
  }

  const deltaDays = Math.floor(deltaHours / 24)
  return `${deltaDays}d ago`
}

function formatDurationFromStart(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) {
    return 'N/A'
  }
  const elapsedSeconds = Math.max(0, Math.round(Date.now() / 1000 - seconds))
  const hours = Math.floor(elapsedSeconds / 3600)
  const minutes = Math.floor((elapsedSeconds % 3600) / 60)
  if (hours <= 0) {
    return `${minutes}m`
  }
  return `${hours}h ${minutes}m`
}

function formatConfidence(value: number | undefined): string {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return 'N/A'
  }
  return value <= 1 ? formatPercent(value * 100) : formatPercent(value)
}

function shortId(value: string | undefined, prefixLength = 8): string {
  const normalized = String(value || '').trim()
  if (!normalized) {
    return 'N/A'
  }
  return normalized.length <= prefixLength ? normalized : normalized.slice(0, prefixLength)
}

function usePolledJson<T>(
  load: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  deps: ReadonlyArray<unknown>,
  enabled = true
): PollState<T> {
  const loadRef = useRef(load)
  loadRef.current = load
  const [state, setState] = useState<PollState<T>>({
    data: null,
    error: null,
    loading: enabled,
    lastLoadedAt: null
  })

  useEffect(() => {
    if (!enabled) {
      setState((current) => ({
        ...current,
        loading: false,
        error: null
      }))
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
        void run()
      }, Math.max(500, intervalMs))
    }

    const run = async () => {
      const controller = new AbortController()
      activeController = controller
      setState((current) => ({
        ...current,
        loading: true
      }))

      try {
        const data = await loadRef.current(controller.signal)
        if (cancelled) {
          return
        }
        setState({
          data,
          error: null,
          loading: false,
          lastLoadedAt: Date.now()
        })
      } catch (error) {
        if (cancelled || controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
          return
        }
        const apiError =
          error instanceof ApiError
            ? error
            : new ApiError(error instanceof Error ? error.message : 'Unknown API error')
        setState((current) => ({
          ...current,
          error: apiError,
          loading: false
        }))
      } finally {
        if (activeController === controller) {
          activeController = null
        }
        schedule()
      }
    }

    void run()

    return () => {
      cancelled = true
      if (timer !== null) {
        window.clearTimeout(timer)
      }
      activeController?.abort()
    }
  }, [enabled, intervalMs, ...deps])

  return state
}

function MetricCard({label, value, meta, tone = 'default'}: MetricCardProps) {
  return (
    <article className={`metric-card metric-card--${tone}`}>
      <p className="metric-card__label">{label}</p>
      <p className="metric-card__value">{value}</p>
      <p className="metric-card__meta">{meta}</p>
    </article>
  )
}

function EventCard({event}: EventCardProps) {
  const title = event.question || 'Untitled market'
  const actor = event.username || event.trader || 'unknown wallet'
  const decision = humanizeStatus(event.decision, event.type === 'incoming' ? 'Incoming' : 'Pending')

  return (
    <article className="event-card">
      <div className="event-card__header">
        <span className={`pill pill--${event.type}`}>{event.type}</span>
        <span className={`pill pill--decision-${String(event.decision || event.type).toLowerCase().replace(/\s+/g, '-')}`}>
          {decision}
        </span>
        <span className="event-card__time">{formatRelativeSeconds(event.ts)}</span>
      </div>
      <h3 className="event-card__title">
        {event.market_url ? (
          <a href={event.market_url} target="_blank" rel="noreferrer">
            {title}
          </a>
        ) : (
          title
        )}
      </h3>
      <div className="event-card__meta">
        <span>{actor}</span>
        <span>{String(event.side || '').toUpperCase() || 'N/A'}</span>
        <span>{formatCurrency(event.amount_usd ?? event.size_usd)}</span>
        <span>@ {event.price?.toFixed(3) ?? 'N/A'}</span>
      </div>
      {event.type === 'signal' ? (
        <div className="event-card__footer">
          <span>confidence {formatConfidence(event.confidence)}</span>
          <span>{event.reason || 'No reason provided.'}</span>
        </div>
      ) : null}
    </article>
  )
}

export function App() {
  const [savedToken, setSavedToken] = useState(() => getApiToken())
  const [tokenInput, setTokenInput] = useState(() => getApiToken())
  const [tokenVersion, setTokenVersion] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  const [authRejected, setAuthRejected] = useState(false)

  const healthState = usePolledJson<ApiHealth>(
    (signal) => fetchApiJson<ApiHealth>('/api/health', {signal}, ''),
    15000,
    [refreshKey],
    true
  )

  const authRequired = Boolean(healthState.data?.auth_required)
  const protectedRequestsEnabled = (!authRequired || Boolean(savedToken)) && !authRejected

  const botStateResource = usePolledJson<BotStateResponse>(
    (signal) => fetchApiJson<BotStateResponse>('/api/bot-state', {signal}),
    2500,
    [refreshKey, tokenVersion],
    protectedRequestsEnabled
  )

  const eventsResource = usePolledJson<EventsResponse>(
    (signal) => fetchApiJson<EventsResponse>('/api/events?max=80', {signal}),
    1250,
    [refreshKey, tokenVersion],
    protectedRequestsEnabled
  )

  useEffect(() => {
    if (botStateResource.error?.status === 401 || eventsResource.error?.status === 401) {
      setAuthRejected(true)
    }
  }, [botStateResource.error, eventsResource.error])

  useEffect(() => {
    if (!authRequired) {
      setAuthRejected(false)
    }
  }, [authRequired])

  const botState: BotState | null = botStateResource.data?.state || null
  const orderedEvents = useMemo(
    () => [...(eventsResource.data?.events || [])].reverse(),
    [eventsResource.data]
  )
  const incomingEvents = useMemo(
    () => orderedEvents.filter((event) => event.type === 'incoming').slice(0, 8),
    [orderedEvents]
  )
  const signalEvents = useMemo(
    () => orderedEvents.filter((event) => event.type === 'signal').slice(0, 8),
    [orderedEvents]
  )

  const dashboardStatus = useMemo(() => {
    if (healthState.error) {
      return {label: 'Offline', tone: 'warning' as const}
    }
    if (authRequired && (!savedToken || authRejected)) {
      return {label: 'Auth Required', tone: 'warning' as const}
    }
    if (botState?.loop_in_progress) {
      return {label: 'Polling', tone: botState.mode === 'live' ? 'live' as const : 'shadow' as const}
    }
    if (botState) {
      return {label: 'Connected', tone: botState.mode === 'live' ? 'live' as const : 'shadow' as const}
    }
    return {label: 'Connecting', tone: 'default' as const}
  }, [authRequired, authRejected, botState, healthState.error, savedToken])

  const metricCards = useMemo(() => {
    const state = botState
    return [
      {
        label: 'Mode',
        value: state?.mode ? state.mode.toUpperCase() : 'WAITING',
        meta: `${formatNumber(state?.n_wallets)} watched wallets`,
        tone: state?.mode === 'live' ? 'live' : 'shadow'
      },
      {
        label: 'Runtime',
        value: formatDurationFromStart(state?.started_at),
        meta: `session ${shortId(state?.session_id)}`,
        tone: 'default'
      },
      {
        label: 'Bankroll',
        value: formatCurrency(state?.bankroll_usd),
        meta: `${formatNumber(state?.last_event_count)} recent events`,
        tone: 'default'
      },
      {
        label: 'Polling',
        value: state?.loop_in_progress ? 'Active' : 'Idle',
        meta: `last poll ${formatRelativeSeconds(state?.last_poll_at)}`,
        tone: 'default'
      },
      {
        label: 'Model',
        value: humanizeStatus(state?.loaded_scorer || state?.model_prediction_mode, 'Heuristic'),
        meta: state?.model_runtime_compatible
          ? humanizeStatus(state?.loaded_model_backend, 'Runtime compatible')
          : state?.model_fallback_reason || state?.model_load_error || 'No compatible artifact loaded',
        tone: state?.model_runtime_compatible ? 'default' : 'warning'
      },
      {
        label: 'Shadow Readiness',
        value: state?.live_shadow_history_ready ? 'Ready' : 'Building',
        meta: `${formatNumber(state?.resolved_shadow_trade_count)} resolved shadow trades`,
        tone: state?.live_shadow_history_ready ? 'shadow' : 'warning'
      }
    ] satisfies MetricCardProps[]
  }, [botState])

  const detailRows = useMemo(
    () => [
      {label: 'API target', value: apiBaseUrl || 'same origin /api'},
      {
        label: 'Backend address',
        value:
          healthState.data?.host && healthState.data?.port
            ? `${healthState.data.host}:${healthState.data.port}`
            : 'N/A'
      },
      {label: 'Auth', value: authRequired ? (savedToken && !authRejected ? 'Bearer token attached' : 'Token required') : 'Open'},
      {label: 'Started', value: formatTimestamp(botState?.started_at)},
      {label: 'Last activity', value: formatRelativeSeconds(botState?.last_activity_at)},
      {
        label: 'Last poll duration',
        value:
          botState?.last_poll_duration_s !== undefined && botState?.last_poll_duration_s !== null
            ? `${botState.last_poll_duration_s.toFixed(2)}s`
            : 'N/A'
      },
      {
        label: 'Retrain',
        value: botState?.retrain_in_progress
          ? 'Running'
          : humanizeStatus(botState?.last_retrain_status, botState?.manual_retrain_pending ? 'Queued' : 'Idle')
      },
      {
        label: 'Replay Search',
        value: humanizeStatus(botState?.last_replay_search_status, 'Idle')
      },
      {
        label: 'Shadow Restart',
        value: botState?.shadow_restart_pending
          ? humanizeStatus(botState?.shadow_restart_kind, 'Pending')
          : 'No restart pending'
      },
      {
        label: 'Confidence Gate',
        value: botState?.live_require_shadow_history_enabled
          ? botState?.live_shadow_history_total_ready
            ? 'Live-ready history met'
            : 'History requirement not met'
          : 'Not enforced'
      }
    ],
    [apiBaseUrl, authRejected, authRequired, botState, healthState.data, savedToken]
  )

  const handleTokenSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setApiToken(tokenInput)
    startTransition(() => {
      const nextToken = getApiToken()
      setSavedToken(nextToken)
      setTokenInput(nextToken)
      setAuthRejected(false)
      setTokenVersion((current) => current + 1)
      setRefreshKey((current) => current + 1)
    })
  }

  const clearToken = () => {
    setApiToken('')
    startTransition(() => {
      setSavedToken('')
      setTokenInput('')
      setAuthRejected(false)
      setTokenVersion((current) => current + 1)
      setRefreshKey((current) => current + 1)
    })
  }

  const refreshNow = () => {
    startTransition(() => {
      setRefreshKey((current) => current + 1)
    })
  }

  const activeError =
    (authRequired && (!savedToken || authRejected) ? null : botStateResource.error) ||
    (authRequired && (!savedToken || authRejected) ? null : eventsResource.error) ||
    healthState.error

  return (
    <main className="dashboard-shell">
      <section className="hero">
        <div className="hero__copy">
          <p className="eyebrow">Kelly Watcher</p>
          <h1>Web command center for the trading bot</h1>
          <p className="hero__lede">
            One backend, two frontends: keep the Ink CLI for terminal operators and expose the same data contract to any
            phone or laptop through the browser.
          </p>
        </div>
        <div className="hero__controls">
          <div className={`status-chip status-chip--${dashboardStatus.tone}`}>
            <span className="status-chip__dot" />
            <span>{dashboardStatus.label}</span>
          </div>
          <button type="button" className="button button--ghost" onClick={refreshNow}>
            Refresh now
          </button>
          {savedToken && !hasEnvironmentApiToken ? (
            <button type="button" className="button button--ghost" onClick={clearToken}>
              Clear token
            </button>
          ) : null}
        </div>
      </section>

      <section className="summary-grid">
        {metricCards.map((card) => (
          <MetricCard key={card.label} {...card} />
        ))}
      </section>

      {authRequired ? (
        <section className="panel panel--auth">
          <div className="panel__header">
            <div>
              <p className="panel__eyebrow">API Auth</p>
              <h2>{savedToken && !authRejected ? 'Token loaded' : 'Bearer token required'}</h2>
            </div>
            <p className="panel__subtle">
              {hasEnvironmentApiToken
                ? 'The build already includes a token through Vite environment variables.'
                : 'The backend reports token auth is enabled. Store the token in your browser for this device only.'}
            </p>
          </div>
          {hasEnvironmentApiToken ? null : (
            <form className="token-form" onSubmit={handleTokenSubmit}>
              <label className="token-form__field">
                <span>Dashboard API token</span>
                <input
                  type="password"
                  value={tokenInput}
                  onChange={(event) => setTokenInput(event.target.value)}
                  placeholder="Paste DASHBOARD_API_TOKEN"
                  autoComplete="off"
                />
              </label>
              <button type="submit" className="button">
                Save token
              </button>
            </form>
          )}
          {authRejected ? <p className="panel__warning">The current token was rejected by the backend.</p> : null}
        </section>
      ) : null}

      {activeError ? (
        <section className="panel panel--warning">
          <div className="panel__header">
            <div>
              <p className="panel__eyebrow">Connection</p>
              <h2>Backend communication issue</h2>
            </div>
          </div>
          <p className="panel__warning">{activeError.message}</p>
        </section>
      ) : null}

      <section className="content-grid">
        <article className="panel">
          <div className="panel__header">
            <div>
              <p className="panel__eyebrow">Incoming Feed</p>
              <h2>Recent watched-wallet trades</h2>
            </div>
            <p className="panel__subtle">{incomingEvents.length} cards</p>
          </div>
          <div className="stack">
            {incomingEvents.length ? incomingEvents.map((event) => <EventCard key={`incoming-${event.trade_id}-${event.ts}`} event={event} />) : <p className="empty-state">Waiting for incoming trade events.</p>}
          </div>
        </article>

        <article className="panel">
          <div className="panel__header">
            <div>
              <p className="panel__eyebrow">Decision Stream</p>
              <h2>Recent scored signals</h2>
            </div>
            <p className="panel__subtle">{signalEvents.length} cards</p>
          </div>
          <div className="stack">
            {signalEvents.length ? signalEvents.map((event) => <EventCard key={`signal-${event.trade_id}-${event.ts}`} event={event} />) : <p className="empty-state">No scored signals yet.</p>}
          </div>
        </article>

        <article className="panel">
          <div className="panel__header">
            <div>
              <p className="panel__eyebrow">System Detail</p>
              <h2>Operator-facing runtime facts</h2>
            </div>
            <p className="panel__subtle">
              {healthState.lastLoadedAt ? `Updated ${formatRelativeSeconds(Math.round(healthState.lastLoadedAt / 1000))}` : 'Loading'}
            </p>
          </div>
          <dl className="detail-list">
            {detailRows.map((row) => (
              <div key={row.label} className="detail-list__row">
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
          <div className="footnote">
            <p>Latest retrain message: {botState?.last_retrain_message || 'None recorded.'}</p>
            <p>Latest replay message: {botState?.last_replay_search_message || 'None recorded.'}</p>
            <p>Model loaded at: {formatTimestamp(botState?.model_loaded_at)}</p>
          </div>
        </article>
      </section>
    </main>
  )
}
