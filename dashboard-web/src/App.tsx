import {startTransition, useEffect, useMemo, useRef, useState, type FormEvent} from 'react'
import {
  ApiError,
  apiBaseUrl,
  fetchApiJson,
  getApiToken,
  hasEnvironmentApiToken,
  postApiJson,
  setApiToken,
  type ApiHealth,
  type BotState,
  type BotStateResponse,
  type DbRecoveryInventoryEntry,
  type DiscoveryCandidate,
  type DiscoveryCandidatesResponse,
  type EventsResponse,
  type LiveEvent,
  type ManagedWallet,
  type ManagedWalletsResponse,
  type WalletMembershipEvent,
  type WalletMembershipEventsResponse
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

interface DiscoveryCandidateCardProps {
  candidate: DiscoveryCandidate
}

interface ManagedWalletCardProps {
  wallet: ManagedWallet
  onDrop: (walletAddress: string) => void
  onReactivate: (walletAddress: string) => void
  busyWallet: string | null
}

interface WalletEventCardProps {
  event: WalletMembershipEvent
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

function formatBytes(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value) || value < 0) {
    return 'N/A'
  }
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let amount = value
  let unitIndex = 0
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024
    unitIndex += 1
  }
  const maximumFractionDigits = amount >= 100 || unitIndex === 0 ? 0 : amount >= 10 ? 1 : 2
  return `${new Intl.NumberFormat(undefined, {maximumFractionDigits}).format(amount)} ${units[unitIndex]}`
}

function formatLogicalAllocated(logical: number | undefined | null, allocated: number | undefined | null): string {
  if (
    logical !== undefined &&
    logical !== null &&
    allocated !== undefined &&
    allocated !== null &&
    !Number.isNaN(logical) &&
    !Number.isNaN(allocated)
  ) {
    return `${formatBytes(logical)} / ${formatBytes(allocated)}`
  }
  return formatBytes(logical ?? allocated)
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

function basename(value: string | undefined | null): string {
  const normalized = String(value || '').trim()
  if (!normalized) {
    return 'N/A'
  }
  const parts = normalized.split(/[\\/]/).filter(Boolean)
  return parts.length ? parts[parts.length - 1] : normalized
}

function formatLabelList(values: string[] | undefined): string {
  const labels = (values || [])
    .map((value) => String(value || '').trim())
    .filter(Boolean)
  return labels.length ? labels.join(', ') : 'N/A'
}

function firstLine(value: string | undefined | null, fallback = ''): string {
  const normalized = String(value || '').trim()
  if (!normalized) {
    return fallback
  }
  const [line] = normalized.split(/\r?\n/, 1)
  return line.trim() || fallback
}

function parseStartupDiagnostics(
  value: string | undefined | null
): {issues: string[]; warnings: string[]} {
  const normalized = String(value || '').trim()
  if (!normalized) {
    return {issues: [], warnings: []}
  }
  const issues: string[] = []
  const warnings: string[] = []
  let section: 'issues' | 'warnings' = 'issues'
  for (const rawLine of normalized.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) {
      continue
    }
    if (/^warnings$/i.test(line)) {
      section = 'warnings'
      continue
    }
    if (!line.startsWith('-')) {
      continue
    }
    const detail = line.replace(/^-+\s*/, '').trim()
    if (!detail) {
      continue
    }
    if (section === 'warnings') {
      warnings.push(detail)
    } else {
      issues.push(detail)
    }
  }
  return {issues, warnings}
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

function DiscoveryCandidateCard({candidate}: DiscoveryCandidateCardProps) {
  const wallet = String(candidate.wallet_address || '').trim().toLowerCase()
  const accepted = Boolean(candidate.accepted)
  const sourceLabels = formatLabelList(candidate.source_labels)
  const score = Number.isFinite(Number(candidate.follow_score)) ? Number(candidate.follow_score) : 0
  return (
    <article className="event-card">
      <div className="event-card__header">
        <span className={`pill ${accepted ? 'pill--decision-accepted' : 'pill--decision-blocked'}`}>
          {accepted ? 'ready' : 'review'}
        </span>
        <span className="pill pill--signal">{score.toFixed(3)}</span>
        <span className="event-card__time">{formatRelativeSeconds(candidate.updated_at)}</span>
      </div>
      <h3 className="event-card__title">{wallet || 'Unknown wallet'}</h3>
      <div className="event-card__meta">
        <span>{candidate.username || 'unknown'}</span>
        <span>{candidate.style || candidate.watch_style || 'N/A'}</span>
        <span>{sourceLabels}</span>
      </div>
      <div className="event-card__footer">
        <span>{accepted ? 'passes copyability gate' : candidate.reject_reason || 'Awaiting review'}</span>
        <span>buys {formatNumber(candidate.recent_buys)}</span>
        <span>lead {candidate.median_buy_lead_hours !== undefined ? `${candidate.median_buy_lead_hours.toFixed(1)}h` : 'N/A'}</span>
        <span>pnl {formatCurrency(candidate.realized_pnl_usd)}</span>
      </div>
    </article>
  )
}

function ManagedWalletCard({wallet, onDrop, onReactivate, busyWallet}: ManagedWalletCardProps) {
  const walletAddress = String(wallet.wallet_address || '').trim().toLowerCase()
  const trackingEnabled = wallet.tracking_enabled !== false
  const isDropped = String(wallet.status || '').trim().toLowerCase() === 'dropped'
  const actionLabel = trackingEnabled && !isDropped ? 'Drop' : 'Reactivate'
  const actionHandler = trackingEnabled && !isDropped ? onDrop : onReactivate
  const statusLabel = isDropped ? 'dropped' : trackingEnabled ? 'tracked' : 'disabled'
  const postPromotionActive = Boolean(wallet.post_promotion_baseline_at)
  const proofBoundaryAction = String(wallet.post_promotion_boundary_action || '').trim().toLowerCase()
  const proofBoundarySource = humanizeStatus(wallet.post_promotion_boundary_source, 'Wallet Discovery')
  const proofBoundaryReason = firstLine(wallet.post_promotion_boundary_reason, '')
  const trustTierLabel = humanizeStatus(wallet.trust_tier, 'Trust pending')
  const trustSizeLabel =
    wallet.trust_size_multiplier === undefined || wallet.trust_size_multiplier === null
      ? 'N/A'
      : formatPercent(wallet.trust_size_multiplier * 100)
  const postPromotionSummary = postPromotionActive
    ? `${
        wallet.post_promotion_evidence_ready ? 'evidence ready' : 'awaiting proof'
      } • copied ${formatNumber(wallet.post_promotion_resolved_copied_count)} • buys ${formatNumber(
        wallet.post_promotion_total_buy_signals
      )} • skip ${formatPercent((wallet.post_promotion_uncopyable_skip_rate || 0) * 100)} • pnl ${formatCurrency(
        wallet.post_promotion_resolved_copied_total_pnl_usd
      )}`
    : ''
  const postPromotionBoundaryLabel = postPromotionActive
    ? proofBoundaryAction === 'reactivate'
      ? `proof reset by reactivation ${formatRelativeSeconds(wallet.post_promotion_baseline_at)}`
      : proofBoundaryAction === 'restore'
        ? `proof reset by shadow reset ${formatRelativeSeconds(wallet.post_promotion_baseline_at)}`
        : `promoted ${formatRelativeSeconds(wallet.post_promotion_baseline_at)}`
    : ''
  const originalPromotionLabel =
    postPromotionActive &&
    proofBoundaryAction &&
    proofBoundaryAction !== 'promote' &&
    wallet.post_promotion_promoted_at &&
    wallet.post_promotion_promoted_at > 0
      ? `auto-promoted ${formatRelativeSeconds(wallet.post_promotion_promoted_at)}`
      : ''
  const postPromotionBoundaryDetail = postPromotionActive
    ? `Boundary: ${humanizeStatus(wallet.post_promotion_boundary_action, 'Promote')} • ${proofBoundarySource}${
        proofBoundaryReason ? ` • ${proofBoundaryReason}` : ''
      }`
    : ''

  return (
    <article className="event-card">
      <div className="event-card__header">
        <span className={`pill ${trackingEnabled && !isDropped ? 'pill--decision-accepted' : 'pill--decision-blocked'}`}>
          {statusLabel}
        </span>
        <span className="pill">{trustTierLabel}</span>
        <span className="pill">{humanizeStatus(wallet.registry_source || wallet.source, 'wallet snapshot')}</span>
        <span className="event-card__time">{formatRelativeSeconds(wallet.updated_at || wallet.added_at || wallet.tracking_started_at)}</span>
      </div>
      <h3 className="event-card__title">{wallet.username || walletAddress || 'Unknown wallet'}</h3>
      <div className="event-card__meta">
        <span>{walletAddress || 'N/A'}</span>
        <span>{wallet.discovery_score !== undefined ? `score ${wallet.discovery_score.toFixed(3)}` : 'no discovery score'}</span>
        <span>{wallet.discovery_accepted ? 'ready' : wallet.discovery_reason || 'snapshot only'}</span>
        {postPromotionActive ? <span>{postPromotionBoundaryLabel}</span> : null}
        {originalPromotionLabel ? <span>{originalPromotionLabel}</span> : null}
      </div>
      <div className="event-card__footer">
        <span>
          {`Trust: ${trustTierLabel} • size ${trustSizeLabel}${wallet.trust_note ? ` • ${wallet.trust_note}` : ''}`}
        </span>
        <span>
          {wallet.post_promotion_evidence_note || postPromotionBoundaryDetail || wallet.status_reason || wallet.disabled_reason || 'No lifecycle note recorded.'}
        </span>
        {postPromotionActive ? <span>{postPromotionSummary}</span> : null}
        <button
          type="button"
          className="button button--ghost"
          disabled={Boolean(busyWallet)}
          onClick={() => actionHandler(walletAddress)}
        >
          {busyWallet === walletAddress ? `${actionLabel}...` : actionLabel}
        </button>
      </div>
    </article>
  )
}

function WalletEventCard({event}: WalletEventCardProps) {
  const action = humanizeStatus(event.action, 'Update')
  const source = humanizeStatus(event.source, 'wallet snapshot')
  return (
    <article className="event-card">
      <div className="event-card__header">
        <span className="pill pill--signal">{action}</span>
        <span className="pill">{source}</span>
        <span className="event-card__time">{formatRelativeSeconds(event.created_at)}</span>
      </div>
      <h3 className="event-card__title">{shortId(event.wallet_address, 12)}</h3>
      <div className="event-card__meta">
        <span>{event.reason || 'No reason recorded.'}</span>
      </div>
      <div className="event-card__footer">
        <span>{event.payload && typeof event.payload === 'object' ? JSON.stringify(event.payload) : 'No payload.'}</span>
      </div>
    </article>
  )
}

export function App() {
  const [savedToken, setSavedToken] = useState(() => getApiToken())
  const [tokenInput, setTokenInput] = useState(() => getApiToken())
  const [tokenVersion, setTokenVersion] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  const [authRejected, setAuthRejected] = useState(false)
  const [shadowRestartWalletMode, setShadowRestartWalletMode] = useState<'keep_all' | 'keep_active' | 'clear_all'>(
    'keep_active'
  )

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

  const managedWalletsResource = usePolledJson<ManagedWalletsResponse>(
    (signal) => fetchApiJson<ManagedWalletsResponse>('/api/wallets?limit=40', {signal}),
    5000,
    [refreshKey, tokenVersion],
    protectedRequestsEnabled
  )

  const discoveryCandidatesResource = usePolledJson<DiscoveryCandidatesResponse>(
    (signal) => fetchApiJson<DiscoveryCandidatesResponse>('/api/discovery/candidates?limit=16', {signal}),
    5000,
    [refreshKey, tokenVersion],
    protectedRequestsEnabled
  )

  const walletEventsResource = usePolledJson<WalletMembershipEventsResponse>(
    (signal) => fetchApiJson<WalletMembershipEventsResponse>('/api/wallets/events?limit=16', {signal}),
    7000,
    [refreshKey, tokenVersion],
    protectedRequestsEnabled
  )

  useEffect(() => {
    if (
      botStateResource.error?.status === 401 ||
      eventsResource.error?.status === 401 ||
      managedWalletsResource.error?.status === 401 ||
      discoveryCandidatesResource.error?.status === 401 ||
      walletEventsResource.error?.status === 401
    ) {
      setAuthRejected(true)
    }
  }, [
    botStateResource.error,
    discoveryCandidatesResource.error,
    eventsResource.error,
    managedWalletsResource.error,
    walletEventsResource.error
  ])

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
  const managedWallets = managedWalletsResource.data?.wallets || []
  const discoveryCandidates = discoveryCandidatesResource.data?.candidates || []
  const walletMembershipEvents = walletEventsResource.data?.events || []
  const [dashboardActionMessage, setDashboardActionMessage] = useState('')
  const [busyAction, setBusyAction] = useState<string | null>(null)

  const promotedManagedWallets = useMemo(
    () => managedWallets.filter((wallet) => Boolean(wallet.post_promotion_baseline_at)),
    [managedWallets]
  )
  const promotionProbationWallets = useMemo(
    () => promotedManagedWallets.filter((wallet) => String(wallet.trust_tier || '').trim().toLowerCase() === 'promotion_probation'),
    [promotedManagedWallets]
  )
  const promotionReadyWallets = useMemo(
    () => promotedManagedWallets.filter((wallet) => Boolean(wallet.post_promotion_evidence_ready)),
    [promotedManagedWallets]
  )
  const promotionProbationWeight = useMemo(
    () =>
      promotionProbationWallets.reduce((total, wallet) => {
        const multiplier = Number(wallet.trust_size_multiplier)
        return total + (Number.isFinite(multiplier) ? multiplier : 0)
      }, 0),
    [promotionProbationWallets]
  )
  const promotedResolvedCopiedCount = useMemo(
    () =>
      promotedManagedWallets.reduce((total, wallet) => {
        const count = Number(wallet.post_promotion_resolved_copied_count)
        return total + (Number.isFinite(count) ? count : 0)
      }, 0),
    [promotedManagedWallets]
  )
  const promotedResolvedCopiedPnl = useMemo(
    () =>
      promotedManagedWallets.reduce((total, wallet) => {
        const pnl = Number(wallet.post_promotion_resolved_copied_total_pnl_usd)
        return total + (Number.isFinite(pnl) ? pnl : 0)
      }, 0),
    [promotedManagedWallets]
  )
  const promotedWeightedWinRate = useMemo(() => {
    let winsWeight = 0
    let totalWeight = 0
    for (const wallet of promotedManagedWallets) {
      const count = Number(wallet.post_promotion_resolved_copied_count)
      const winRate = Number(wallet.post_promotion_resolved_copied_win_rate)
      if (!Number.isFinite(count) || count <= 0 || !Number.isFinite(winRate)) {
        continue
      }
      winsWeight += count * winRate
      totalWeight += count
    }
    return totalWeight > 0 ? winsWeight / totalWeight : null
  }, [promotedManagedWallets])
  const promotionPositiveReadyCount = useMemo(
    () =>
      promotionReadyWallets.filter((wallet) => Number(wallet.post_promotion_resolved_copied_total_pnl_usd || 0) > 0).length,
    [promotionReadyWallets]
  )
  const promotionReadyRate = useMemo(
    () => (promotedManagedWallets.length > 0 ? promotionReadyWallets.length / promotedManagedWallets.length : null),
    [promotedManagedWallets.length, promotionReadyWallets.length]
  )
  const activePromotedCount = useMemo(
    () =>
      promotedManagedWallets.filter((wallet) => {
        const status = String(wallet.status || '').trim().toLowerCase()
        return wallet.tracking_enabled !== false && status !== 'dropped'
      }).length,
    [promotedManagedWallets]
  )
  const droppedPromotedCount = useMemo(
    () =>
      promotedManagedWallets.filter((wallet) => String(wallet.status || '').trim().toLowerCase() === 'dropped').length,
    [promotedManagedWallets]
  )
  const promotedUncopyableSkips = useMemo(
    () =>
      promotedManagedWallets.reduce((total, wallet) => {
        const skips = Number(wallet.post_promotion_uncopyable_skips)
        return total + (Number.isFinite(skips) ? skips : 0)
      }, 0),
    [promotedManagedWallets]
  )
  const promotedTimingSkips = useMemo(
    () =>
      promotedManagedWallets.reduce((total, wallet) => {
        const skips = Number(wallet.post_promotion_timing_skips)
        return total + (Number.isFinite(skips) ? skips : 0)
      }, 0),
    [promotedManagedWallets]
  )
  const promotedLiquiditySkips = useMemo(
    () =>
      promotedManagedWallets.reduce((total, wallet) => {
        const skips = Number(wallet.post_promotion_liquidity_skips)
        return total + (Number.isFinite(skips) ? skips : 0)
      }, 0),
    [promotedManagedWallets]
  )
  const promotedTotalBuySignals = useMemo(
    () =>
      promotedManagedWallets.reduce((total, wallet) => {
        const count = Number(wallet.post_promotion_total_buy_signals)
        return total + (Number.isFinite(count) ? count : 0)
      }, 0),
    [promotedManagedWallets]
  )
  const promotedWeightedSkipRate = useMemo(
    () => (promotedTotalBuySignals > 0 ? promotedUncopyableSkips / promotedTotalBuySignals : null),
    [promotedTotalBuySignals, promotedUncopyableSkips]
  )
  const promotionBlockerPreview = useMemo(() => {
    const notes = promotionProbationWallets
      .map((wallet) => firstLine(wallet.post_promotion_evidence_note || wallet.trust_note || ''))
      .filter(Boolean)
    if (!notes.length) {
      return 'No promoted wallets are currently blocked on post-promotion proof.'
    }
    const unique = Array.from(new Set(notes))
    const preview = unique.slice(0, 2).join(' • ')
    return unique.length > 2 ? `${preview} • +${unique.length - 2} more` : preview
  }, [promotionProbationWallets])

  const startupFailed = Boolean(botState?.startup_failed || botState?.startup_validation_failed)
  const startupValidationFailed = Boolean(botState?.startup_validation_failed)
  const startupBlocked = Boolean(botState?.startup_blocked || botState?.startup_recovery_only)
  const startupRecoveryOnly = Boolean(botState?.startup_recovery_only)
  const startupBlockReason = firstLine(
    botState?.startup_block_reason,
    startupRecoveryOnly ? 'Startup is blocked in recovery-only mode.' : 'Startup is currently blocked.'
  )
  const startupFailureMessage = firstLine(botState?.startup_failure_message, startupBlockReason)
  const startupDiagnosticsSource =
    botState?.startup_failure_message || botState?.startup_validation_message || botState?.startup_detail || ''
  const startupDiagnostics = useMemo(
    () => parseStartupDiagnostics(startupDiagnosticsSource),
    [startupDiagnosticsSource]
  )
  const startupIssuePreview = startupDiagnostics.issues.slice(0, 3)
  const startupWarningPreview = startupDiagnostics.warnings.slice(0, 2)
  const dbIntegrityBlocked = Boolean(botState?.db_integrity_known) && botState?.db_integrity_ok === false
  const dbIntegrityMessage = firstLine(botState?.db_integrity_message, 'SQLite integrity check failed.')
  const shadowRestartPending = Boolean(botState?.shadow_restart_pending)
  const shadowRestartMessage = firstLine(botState?.shadow_restart_message, 'Shadow restart pending.')
  const tradeLogArchivePending = Boolean(botState?.trade_log_archive_pending)
  const tradeLogArchiveStatus = humanizeStatus(
    botState?.trade_log_archive_status,
    botState?.trade_log_archive_enabled ? 'Checking' : 'Disabled'
  )
  const tradeLogArchiveMessage = firstLine(
    tradeLogArchivePending
      ? botState?.trade_log_archive_request_message
      : botState?.trade_log_archive_block_reason ||
          botState?.trade_log_archive_message ||
          botState?.storage_message ||
          '',
    botState?.trade_log_archive_enabled ? 'Trade log archive state pending.' : 'Trade log archiving is disabled.'
  )
  const recoveryCandidateMode = humanizeStatus(botState?.db_recovery_candidate_mode, 'Unavailable')
  const recoveryCandidateMessage = firstLine(
    botState?.db_recovery_candidate_message,
    'No verified recovery candidate has been classified yet.'
  )
  const recoveryCandidateClassReason = firstLine(botState?.db_recovery_candidate_class_reason, recoveryCandidateMessage)
  const configuredModeLabel = humanizeStatus(botState?.configured_mode || botState?.mode, 'Shadow')
  const effectiveModeLabel = humanizeStatus(botState?.mode, 'Shadow')
  const modeOverrideDetail = firstLine(
    botState?.mode_block_reason,
    configuredModeLabel === effectiveModeLabel ? 'No override active.' : 'Effective runtime mode differs from configured mode.'
  )
  const recoveryCandidateFile = basename(botState?.db_recovery_candidate_path)
  const recoverySourceFile = basename(botState?.db_recovery_candidate_source_path)
  const latestVerifiedBackupFile = basename(botState?.db_recovery_latest_verified_backup_path)
  const recoveryPathDetail = firstLine(botState?.db_recovery_candidate_path, recoveryCandidateFile || 'Unavailable')
  const latestVerifiedBackupDetail = firstLine(
    botState?.db_recovery_latest_verified_backup_path,
    latestVerifiedBackupFile || 'Unavailable'
  )
  const recoveryInventory = Array.isArray(botState?.db_recovery_inventory)
    ? (botState?.db_recovery_inventory as DbRecoveryInventoryEntry[])
    : []
  const recoveryInventorySummary = recoveryInventory.length
    ? `${formatNumber(botState?.db_recovery_inventory_count ?? recoveryInventory.length)} checked`
    : 'No retained verified backups checked yet.'
  const recoveryInventoryPreview = recoveryInventory.slice(0, 4).map((entry) => {
    const label = humanizeStatus(entry.kind, 'Backup')
    const fileName = basename(entry.path)
    const status = entry.ready ? (entry.selected ? 'selected' : 'ready') : 'failed'
    const detail = firstLine(entry.message, entry.ready ? 'integrity OK' : 'integrity check failed')
    return `${label}: ${fileName} — ${status}${detail ? ` • ${detail}` : ''}`
  })
  const archiveWindowDetail =
    botState?.trade_log_archive_state_known
      ? `cutoff ${formatTimestamp(botState?.trade_log_archive_cutoff_ts)} • preserve since ${formatTimestamp(
          botState?.trade_log_archive_preserve_since_ts
        )}`
      : 'Archive window still loading.'
  const archiveResultDetail =
    botState?.trade_log_archive_state_known
      ? `candidates ${formatNumber(botState?.trade_log_archive_last_candidate_count)} • archived ${formatNumber(
          botState?.trade_log_archive_last_archived_count
        )} • deleted ${formatNumber(botState?.trade_log_archive_last_deleted_count)}${
          botState?.trade_log_archive_last_vacuumed ? ' • vacuumed' : ''
        }`
      : 'Archive result still loading.'
  const walletRegistrySource = humanizeStatus(managedWalletsResource.data?.source, 'Wallet registry pending')
  const discoveryScanStatus = botState?.wallet_discovery_last_scan_ok
    ? 'Ready'
    : botState?.wallet_discovery_last_scan_message
      ? 'Review'
      : 'Pending'
  const discoveryScanMessage = firstLine(
    botState?.wallet_discovery_last_scan_message,
    botState?.wallet_discovery_last_scan_at
      ? `${formatNumber(botState?.wallet_discovery_scanned_count)} analyzed / ${formatNumber(
          botState?.wallet_discovery_candidate_count
        )} stored`
      : 'Discovery scan has not run yet.'
  )
  const shadowSnapshotDetail = botState?.shadow_snapshot_ready
    ? `${formatNumber(botState?.shadow_snapshot_routed_resolved)} routed / ${formatNumber(botState?.shadow_snapshot_resolved)} resolved`
    : firstLine(
        botState?.shadow_snapshot_block_reason,
        humanizeStatus(botState?.shadow_snapshot_status, 'Checking')
      )
  const storageMessage = firstLine(botState?.storage_message, 'Storage snapshot pending.')
  const modeCardValue = startupRecoveryOnly
    ? 'RECOVERY ONLY'
    : startupBlocked || dbIntegrityBlocked
      ? 'BLOCKED'
      : shadowRestartPending
        ? 'RESTART PENDING'
        : botState?.mode
          ? botState.mode.toUpperCase()
          : 'WAITING'
  const modeCardMeta =
    botState?.configured_mode === 'live' && modeCardValue !== 'LIVE'
      ? firstLine(
          botState?.mode_block_reason,
          `configured LIVE • ${formatNumber(botState?.n_wallets)} watched wallets`
        )
      : `${formatNumber(botState?.n_wallets)} watched wallets`
  const modeCardTone =
    startupBlocked || dbIntegrityBlocked || shadowRestartPending
      ? 'warning'
      : botState?.mode === 'live'
        ? 'live'
        : 'shadow'
  const eventsBlockedMessage = useMemo(() => {
    if (shadowRestartPending) {
      return `Event feed is paused: ${shadowRestartMessage}`
    }
    if (startupBlocked) {
      return `Event feed is paused: ${startupBlockReason}`
    }
    if (dbIntegrityBlocked) {
      return `Event feed is paused: ${dbIntegrityMessage}`
    }
    return ''
  }, [dbIntegrityBlocked, dbIntegrityMessage, shadowRestartMessage, shadowRestartPending, startupBlocked, startupBlockReason])
  const dataPanelsBlockedReason = useMemo(() => {
    if (shadowRestartPending) {
      return shadowRestartMessage
    }
    if (startupBlocked) {
      return startupBlockReason
    }
    if (dbIntegrityBlocked) {
      return dbIntegrityMessage
    }
    return ''
  }, [dbIntegrityBlocked, dbIntegrityMessage, shadowRestartMessage, shadowRestartPending, startupBlocked, startupBlockReason])
  const walletRegistryEmptyMessage = dataPanelsBlockedReason
    ? `Wallet registry queries are paused: ${dataPanelsBlockedReason}`
    : managedWalletsResource.data?.message?.trim() ||
      'No wallet registry rows available yet. Once the backend imports or stores managed wallets, they will appear here.'
  const discoveryEmptyMessage = dataPanelsBlockedReason
    ? `Discovery queries are paused: ${dataPanelsBlockedReason}`
    : 'No discovery candidates have been cached yet.'
  const walletTimelineEmptyMessage = dataPanelsBlockedReason
    ? `Wallet timeline queries are paused: ${dataPanelsBlockedReason}`
    : 'No lifecycle events are available yet.'
  const walletPromotionSummaryBlockedMessage = dataPanelsBlockedReason
    ? `Queries paused • ${dataPanelsBlockedReason}`
    : ''
  const walletPromotionRows = useMemo(
    () => [
      {
        label: 'Auto-Promoted',
        value: walletPromotionSummaryBlockedMessage
          ? walletPromotionSummaryBlockedMessage
          : promotedManagedWallets.length
            ? `${formatNumber(promotedManagedWallets.length)} wallet(s)`
            : 'No promoted wallets tracked yet'
      },
      {
        label: 'Awaiting Proof',
        value: walletPromotionSummaryBlockedMessage
          ? walletPromotionSummaryBlockedMessage
          : promotionProbationWallets.length
            ? `${formatNumber(promotionProbationWallets.length)} wallet(s) • size ${formatPercent(
                promotionProbationWeight * 100
              )}`
            : 'No promoted wallets are currently in probation'
      },
      {
        label: 'Evidence Ready',
        value: walletPromotionSummaryBlockedMessage
          ? walletPromotionSummaryBlockedMessage
          : promotionReadyWallets.length
            ? `${formatNumber(promotionReadyWallets.length)} wallet(s) • ${formatNumber(
                promotionPositiveReadyCount
              )} positive P&L`
            : 'No promoted wallets have cleared post-promotion proof yet'
      },
      {
        label: 'Ready Rate',
        value: walletPromotionSummaryBlockedMessage
          ? walletPromotionSummaryBlockedMessage
          : promotionReadyRate !== null
            ? `${formatPercent(promotionReadyRate * 100)} • ${formatNumber(
                promotionReadyWallets.length
              )}/${formatNumber(promotedManagedWallets.length)} promoted wallets`
            : 'No promoted wallets tracked yet'
      },
      {
        label: 'Copy Quality',
        value: walletPromotionSummaryBlockedMessage
          ? walletPromotionSummaryBlockedMessage
          : promotedResolvedCopiedCount > 0
            ? `${formatPercent((promotedWeightedWinRate || 0) * 100)} weighted win rate • ${formatNumber(
                promotedResolvedCopiedCount
              )} resolved copied trades`
            : 'No resolved copied post-promotion trades yet'
      },
      {
        label: 'Post-Promotion P&L',
        value: walletPromotionSummaryBlockedMessage
          ? walletPromotionSummaryBlockedMessage
          : promotedResolvedCopiedCount > 0
            ? `${formatCurrency(promotedResolvedCopiedPnl)} total copied P&L`
            : 'No post-promotion copied P&L yet'
      },
      {
        label: 'Active Promoted',
        value: walletPromotionSummaryBlockedMessage
          ? walletPromotionSummaryBlockedMessage
          : promotedManagedWallets.length
            ? `${formatNumber(activePromotedCount)} active • ${formatNumber(droppedPromotedCount)} dropped`
            : 'No promoted wallets tracked yet'
      },
      {
        label: 'Execution Drag',
        value: walletPromotionSummaryBlockedMessage
          ? walletPromotionSummaryBlockedMessage
          : promotedTotalBuySignals > 0
            ? `${formatPercent((promotedWeightedSkipRate || 0) * 100)} weighted skip rate • ${formatNumber(
                promotedUncopyableSkips
              )}/${formatNumber(promotedTotalBuySignals)} uncopyable skips`
            : 'No post-promotion buy signals yet'
      },
      {
        label: 'Drag Causes',
        value: walletPromotionSummaryBlockedMessage
          ? walletPromotionSummaryBlockedMessage
          : promotedUncopyableSkips > 0
            ? `${formatNumber(promotedTimingSkips)} timing (${formatPercent(
                (promotedTimingSkips / promotedUncopyableSkips) * 100
              )}) • ${formatNumber(promotedLiquiditySkips)} liquidity (${formatPercent(
                (promotedLiquiditySkips / promotedUncopyableSkips) * 100
              )})`
            : 'No post-promotion uncopyable skips yet'
      },
      {
        label: 'Probation Blockers',
        value: walletPromotionSummaryBlockedMessage || promotionBlockerPreview
      }
    ],
    [
      activePromotedCount,
      droppedPromotedCount,
      promotedManagedWallets.length,
      promotedResolvedCopiedCount,
      promotedResolvedCopiedPnl,
      promotedLiquiditySkips,
      promotedTotalBuySignals,
      promotedTimingSkips,
      promotedUncopyableSkips,
      promotedWeightedSkipRate,
      promotedWeightedWinRate,
      promotionBlockerPreview,
      promotionPositiveReadyCount,
      promotionReadyRate,
      promotionProbationWallets.length,
      promotionProbationWeight,
      promotionReadyWallets.length,
      walletPromotionSummaryBlockedMessage
    ]
  )

  const refreshDashboardData = () => {
    startTransition(() => {
      setRefreshKey((current) => current + 1)
    })
  }

  const runDashboardAction = async (label: string, action: () => Promise<string>) => {
    if (busyAction) {
      return
    }
    setBusyAction(label)
    setDashboardActionMessage('')
    try {
      const message = await action()
      setDashboardActionMessage(message)
      refreshDashboardData()
    } catch (error) {
      setDashboardActionMessage(error instanceof Error ? error.message : 'Unknown dashboard action error')
    } finally {
      setBusyAction(null)
    }
  }

  const triggerDiscoveryScan = async () =>
    runDashboardAction('scan', async () => {
      const result = await postApiJson<DiscoveryCandidatesResponse>('/api/discovery/scan', {})
      return result.message || 'Discovery scan requested.'
    })

  const dropWallet = async (walletAddress: string) =>
    runDashboardAction(walletAddress, async () => {
      const result = await postApiJson<{ok?: boolean; message?: string}>('/api/wallets/drop', {
        walletAddress,
        reason: 'dashboard wallet registry action'
      })
      return result.message || 'Wallet dropped.'
    })

  const reactivateWallet = async (walletAddress: string) =>
    runDashboardAction(walletAddress, async () => {
      const result = await postApiJson<{ok?: boolean; message?: string}>('/api/wallets/reactivate', {
        walletAddress
      })
      return result.message || 'Wallet reactivated.'
    })

  const requestShadowRestart = async () =>
    runDashboardAction('shadow-restart', async () => {
      const result = await postApiJson<{ok?: boolean; message?: string}>('/api/shadow/restart', {
        wallet_mode: shadowRestartWalletMode
      })
      return result.message || 'Shadow restart requested.'
    })

  const requestDbRecovery = async () =>
    runDashboardAction('db-recovery', async () => {
      const result = await postApiJson<{ok?: boolean; message?: string}>('/api/shadow/recover-db', {
        source: 'dashboard-web'
      })
      return result.message || 'DB recovery requested.'
    })

  const requestTradeLogArchive = async () =>
    runDashboardAction('trade-log-archive', async () => {
      const result = await postApiJson<{ok?: boolean; message?: string}>('/api/shadow/archive-trade-log', {
        source: 'dashboard-web'
      })
      return result.message || 'Trade log archive requested.'
    })

  const requestForceShadowMode = async () =>
    runDashboardAction('force-shadow-mode', async () => {
      const result = await postApiJson<{ok?: boolean; message?: string}>('/api/live-mode', {
        enabled: false
      })
      return result.message || 'Live Trading saved as OFF.'
    })

  const actionBusyReason = 'Another dashboard action is already in progress.'
  const liveConfigNeedsShadow = botState?.configured_mode === 'live' || botState?.mode === 'live'
  const restartShadowDisabledReason =
    busyAction && busyAction !== 'shadow-restart'
      ? actionBusyReason
      : shadowRestartPending
        ? shadowRestartMessage
        : !botState?.started_at
          ? 'Bot state is still loading.'
          : ''
  const recoverDbDisabledReason =
    busyAction && busyAction !== 'db-recovery'
      ? actionBusyReason
      : shadowRestartPending
        ? shadowRestartMessage
        : startupBlocked
          ? startupBlockReason
          : botState?.configured_mode === 'live' || botState?.mode === 'live'
            ? 'DB recovery is blocked while live trading is configured or active.'
            : !botState?.db_recovery_state_known
              ? 'Recovery candidate is still being evaluated.'
              : !botState?.db_recovery_candidate_ready
                ? recoveryCandidateClassReason || recoveryCandidateMessage || 'No verified recovery candidate is ready.'
                : ''
  const tradeLogArchiveDisabledReason =
    busyAction && busyAction !== 'trade-log-archive'
      ? actionBusyReason
      : shadowRestartPending
        ? shadowRestartMessage
        : startupBlocked
          ? startupBlockReason
          : !botState?.trade_log_archive_enabled
            ? 'Trade log archiving is disabled in config.'
            : tradeLogArchivePending
              ? tradeLogArchiveMessage
              : dbIntegrityBlocked
                ? `SQLite integrity check failed: ${dbIntegrityMessage}`
                : !botState?.trade_log_archive_state_known
                  ? 'Trade log archive state is still loading.'
                  : ''
  const forceShadowModeDisabledReason =
    busyAction && busyAction !== 'force-shadow-mode'
      ? actionBusyReason
      : !botState?.started_at
        ? 'Bot state is still loading.'
        : !liveConfigNeedsShadow
          ? 'Already configured for shadow-only.'
          : ''
  const restartShadowActionDetail = restartShadowDisabledReason || 'Available now.'
  const recoverDbActionDetail =
    recoverDbDisabledReason ||
    `${recoveryCandidateMode}${recoveryCandidateClassReason ? ` • ${recoveryCandidateClassReason}` : ''}`
  const tradeLogArchiveActionDetail =
    tradeLogArchiveDisabledReason ||
    `${tradeLogArchiveStatus} • eligible ${formatNumber(botState?.trade_log_archive_eligible_row_count)}`
  const forceShadowModeActionDetail =
    forceShadowModeDisabledReason ||
    (botState?.configured_mode === 'live' && botState?.mode === 'shadow'
      ? 'Available now. Clears the persisted live config without waiting for recovery to finish.'
      : 'Available now. Saves Live Trading as OFF and keeps the project shadow-only.')

  const operationalWarnings = useMemo(() => {
    const warnings: string[] = []
    if (startupBlocked) {
      warnings.push(
        startupRecoveryOnly
          ? `Recovery-only startup: ${startupBlockReason}`
          : startupFailed
            ? `${startupValidationFailed ? 'Startup validation failed' : 'Startup failed'}: ${startupFailureMessage}`
            : `Startup blocked: ${startupBlockReason}`
      )
      for (const issue of startupIssuePreview) {
        warnings.push(`Startup issue: ${issue}`)
      }
      for (const warning of startupWarningPreview) {
        warnings.push(`Startup warning: ${warning}`)
      }
    }
    if (dbIntegrityBlocked) {
      warnings.push(`DB integrity failure: ${dbIntegrityMessage}`)
    }
    if (shadowRestartPending) {
      warnings.push(`Shadow restart pending: ${shadowRestartMessage}`)
    }
    if (tradeLogArchivePending) {
      warnings.push(`Trade log archive pending: ${tradeLogArchiveMessage}`)
    }
    return warnings
  }, [
    dbIntegrityBlocked,
    dbIntegrityMessage,
    shadowRestartMessage,
    shadowRestartPending,
    startupBlocked,
    startupBlockReason,
    startupIssuePreview,
    startupWarningPreview,
    startupFailed,
    startupFailureMessage,
    startupRecoveryOnly,
    startupValidationFailed,
    tradeLogArchiveMessage,
    tradeLogArchivePending
  ])

  const dashboardStatus = useMemo(() => {
    if (healthState.error) {
      return {label: 'Offline', tone: 'warning' as const}
    }
    if (authRequired && (!savedToken || authRejected)) {
      return {label: 'Auth Required', tone: 'warning' as const}
    }
    if (startupRecoveryOnly) {
      return {label: 'Recovery Only', tone: 'warning' as const}
    }
    if (startupBlocked) {
      return {label: startupFailed ? 'Startup Failed' : 'Startup Blocked', tone: 'warning' as const}
    }
    if (dbIntegrityBlocked) {
      return {label: 'Integrity Failed', tone: 'warning' as const}
    }
    if (shadowRestartPending) {
      return {label: 'Restart Pending', tone: 'warning' as const}
    }
    if (tradeLogArchivePending) {
      return {label: 'Archiving', tone: 'warning' as const}
    }
    if (botState?.loop_in_progress) {
      return {label: 'Polling', tone: botState.mode === 'live' ? 'live' as const : 'shadow' as const}
    }
    if (botState) {
      return {label: 'Connected', tone: botState.mode === 'live' ? 'live' as const : 'shadow' as const}
    }
    return {label: 'Connecting', tone: 'default' as const}
  }, [
    authRequired,
    authRejected,
    botState,
    dbIntegrityBlocked,
    healthState.error,
    savedToken,
    shadowRestartPending,
    startupBlocked,
    startupFailed,
    startupRecoveryOnly,
    tradeLogArchivePending
  ])

  const metricCards = useMemo(() => {
    const state = botState
    return [
      {
        label: 'Mode',
        value: modeCardValue,
        meta: modeCardMeta,
        tone: modeCardTone
      },
      {
        label: 'Wallet Registry',
        value: formatNumber(managedWalletsResource.data?.count ?? managedWallets.length),
        meta: walletRegistrySource,
        tone: 'default'
      },
      {
        label: 'Discovery',
        value: discoveryScanStatus,
        meta: discoveryScanMessage,
        tone: botState?.wallet_discovery_last_scan_ok === false ? 'warning' : botState?.wallet_discovery_last_scan_at ? 'shadow' : 'default'
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
        label: 'Operations',
        value: startupRecoveryOnly
          ? 'Recovery Only'
          : startupBlocked
            ? 'Blocked'
            : dbIntegrityBlocked
              ? 'Integrity Failed'
              : shadowRestartPending
                ? 'Restart Pending'
                : tradeLogArchivePending
                  ? 'Archiving'
                  : 'Healthy',
        meta: startupBlocked
          ? startupBlockReason
          : dbIntegrityBlocked
            ? dbIntegrityMessage
            : shadowRestartPending
              ? shadowRestartMessage
              : tradeLogArchivePending
                ? tradeLogArchiveMessage
                : `last poll ${formatRelativeSeconds(state?.last_poll_at)}`,
        tone: startupBlocked || dbIntegrityBlocked || shadowRestartPending || tradeLogArchivePending ? 'warning' : 'default'
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
        meta: state?.shadow_snapshot_state_known
          ? `${shadowSnapshotDetail} • ${humanizeStatus(state?.shadow_snapshot_scope, 'current window')}`
          : `${formatNumber(state?.resolved_shadow_trade_count)} resolved shadow trades`,
        tone: state?.live_shadow_history_ready ? 'shadow' : 'warning'
      },
      {
        label: 'Storage',
        value: state?.storage_state_known ? formatBytes(state?.storage_save_dir_size_bytes) : 'Checking',
        meta: state?.storage_state_known
          ? `DB ${formatLogicalAllocated(state?.storage_trading_db_size_bytes, state?.storage_trading_db_allocated_bytes)}`
          : 'Storage snapshot pending',
        tone: dbIntegrityBlocked ? 'warning' : 'default'
      },
      {
        label: 'Recovery Inventory',
        value: recoveryInventorySummary,
        meta: recoveryInventory.length
          ? recoveryInventoryPreview[0] || 'Retained backup candidates checked'
          : recoveryCandidateMessage,
        tone: recoveryInventory.some((entry) => entry.ready) ? 'shadow' : 'warning'
      }
    ] satisfies MetricCardProps[]
  }, [
    botState,
    dbIntegrityBlocked,
    dbIntegrityMessage,
    discoveryScanMessage,
    discoveryScanStatus,
    managedWallets.length,
    managedWalletsResource.data?.count,
    walletRegistrySource,
    shadowRestartMessage,
    shadowRestartPending,
    shadowSnapshotDetail,
    startupBlocked,
    startupBlockReason,
    startupRecoveryOnly,
    modeCardMeta,
    modeCardTone,
    modeCardValue,
    tradeLogArchiveMessage,
    tradeLogArchivePending,
    recoveryCandidateMessage,
    recoveryInventory,
    recoveryInventoryPreview,
    recoveryInventorySummary
  ])

  const operationsRows = useMemo(
    () => [
      {
        label: 'Wallet Registry',
        value: `${walletRegistrySource} • ${formatNumber(managedWallets.length)} live wallet(s)`
      },
      {
        label: 'Startup',
        value: startupBlocked
          ? startupRecoveryOnly
            ? `Recovery-only • ${startupBlockReason}`
            : startupFailed
              ? `${startupValidationFailed ? 'Validation failed' : 'Startup failed'} • ${startupFailureMessage}`
              : `Blocked • ${startupBlockReason}`
          : 'Normal startup'
      },
      {
        label: 'Startup Issues',
        value: startupFailed
          ? `${formatNumber(startupDiagnostics.issues.length)} issue(s) • ${formatNumber(startupDiagnostics.warnings.length)} warning(s)`
          : 'None reported'
      },
      {
        label: 'DB Integrity',
        value: !botState?.db_integrity_known ? 'Checking' : dbIntegrityBlocked ? `FAILED • ${dbIntegrityMessage}` : 'OK'
      },
      {
        label: 'Shadow Restart',
        value: shadowRestartPending ? shadowRestartMessage : 'No restart pending'
      },
      {
        label: 'Shadow Snapshot',
        value: botState?.shadow_snapshot_state_known
          ? botState?.shadow_snapshot_ready
            ? `Ready • ${shadowSnapshotDetail}`
            : `${humanizeStatus(botState?.shadow_snapshot_status, 'Checking')} • ${shadowSnapshotDetail}`
          : 'Checking'
      },
      {
        label: 'Trade Log Archive',
        value: botState?.trade_log_archive_state_known
          ? `${tradeLogArchiveStatus} • eligible ${formatNumber(botState?.trade_log_archive_eligible_row_count)}`
          : 'Checking'
      },
      {
        label: 'Mode Policy',
        value: `${configuredModeLabel} configured • ${effectiveModeLabel} effective`
      },
      {
        label: 'Recovery Candidate',
        value: botState?.db_recovery_state_known
          ? `${recoveryCandidateMode} • ${
              botState?.db_recovery_candidate_ready ? 'verified' : recoveryCandidateMessage
            }`
          : 'Checking'
      },
      {
        label: 'Recovery Files',
        value: botState?.db_recovery_state_known
          ? `${recoveryCandidateFile} ← ${recoverySourceFile}`
          : 'Checking'
      },
      {
        label: 'Latest Backup',
        value: botState?.db_recovery_latest_verified_backup_at
          ? `${latestVerifiedBackupFile} • ${formatTimestamp(botState?.db_recovery_latest_verified_backup_at)}`
          : 'No verified backup recorded'
      },
      {
        label: 'Archive Window',
        value: archiveWindowDetail
      },
      {
        label: 'Archive Result',
        value: archiveResultDetail
      },
      {
        label: 'Discovery Scan',
        value: botState?.wallet_discovery_last_scan_at
          ? `${discoveryScanStatus} • ${discoveryScanMessage}`
          : 'Pending'
      },
      {
        label: 'Storage',
        value: botState?.storage_state_known
          ? `${formatBytes(botState?.storage_save_dir_size_bytes)} save/ • DB ${formatLogicalAllocated(
              botState?.storage_trading_db_size_bytes,
              botState?.storage_trading_db_allocated_bytes
            )}`
          : 'Checking'
      }
    ],
    [
      botState,
      archiveResultDetail,
      archiveWindowDetail,
      configuredModeLabel,
      dbIntegrityBlocked,
      dbIntegrityMessage,
      managedWallets.length,
      recoveryCandidateMessage,
      recoveryCandidateFile,
      recoveryCandidateMode,
      recoverySourceFile,
      shadowRestartMessage,
      shadowRestartPending,
      shadowSnapshotDetail,
      startupBlocked,
      startupBlockReason,
      startupDiagnostics.issues.length,
      startupDiagnostics.warnings.length,
      startupRecoveryOnly,
      startupFailed,
      startupFailureMessage,
      startupValidationFailed,
      discoveryScanMessage,
      discoveryScanStatus,
      effectiveModeLabel,
      latestVerifiedBackupFile,
      tradeLogArchiveStatus,
      walletRegistrySource
    ]
  )

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
      },
      {
        label: 'Wallet Registry',
        value: `${walletRegistrySource} • ${formatNumber(managedWalletsResource.data?.count ?? managedWallets.length)} wallets`
      },
      {
        label: 'Discovery Scan',
        value: botState?.wallet_discovery_last_scan_at
          ? `${discoveryScanStatus} • ${formatRelativeSeconds(botState.wallet_discovery_last_scan_at)}`
          : 'Pending'
      }
    ],
    [
      apiBaseUrl,
      authRejected,
      authRequired,
      botState,
      discoveryScanStatus,
      healthState.data,
      managedWallets.length,
      managedWalletsResource.data?.count,
      savedToken,
      walletRegistrySource
    ]
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
    (authRequired && (!savedToken || authRejected) ? null : managedWalletsResource.error) ||
    (authRequired && (!savedToken || authRejected) ? null : discoveryCandidatesResource.error) ||
    (authRequired && (!savedToken || authRejected) ? null : walletEventsResource.error) ||
    healthState.error

  return (
    <main className="dashboard-shell">
      <section className="hero">
        <div className="hero__copy">
          <p className="eyebrow">Kelly Watcher</p>
          <h1>Web command center for shadow operations</h1>
          <p className="hero__lede">
            Browser-first operator view for the trading bot. It surfaces runtime state, shadow readiness, recovery
            posture, storage pressure, and recent wallet activity from the backend API.
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

      <section className={`panel panel-block${operationalWarnings.length ? ' panel--warning' : ''}`}>
        <div className="panel__header">
          <div>
            <p className="panel__eyebrow">Operational Status</p>
            <h2>Recovery, integrity, and storage truth</h2>
          </div>
          <div className="hero__controls">
            <p className="panel__subtle">
              {botState?.storage_state_known
                ? `${formatBytes(botState?.storage_save_dir_size_bytes)} in save/`
                : 'Waiting for storage snapshot'}
            </p>
            <label className="panel__subtle panel__subtle--control">
              <span>Reset wallets</span>
              <select
                className="control-select"
                value={shadowRestartWalletMode}
                onChange={(event) =>
                  setShadowRestartWalletMode(event.target.value as 'keep_all' | 'keep_active' | 'clear_all')
                }
                disabled={busyAction !== null || Boolean(restartShadowDisabledReason)}
              >
                <option value="keep_active">Keep active</option>
                <option value="keep_all">Keep all</option>
                <option value="clear_all">Clear all</option>
              </select>
            </label>
            <button
              type="button"
              className="button button--ghost"
              onClick={requestShadowRestart}
              disabled={busyAction !== null || Boolean(restartShadowDisabledReason)}
            >
              {busyAction === 'shadow-restart' ? 'Restarting...' : 'Restart Shadow'}
            </button>
            <button
              type="button"
              className="button button--ghost"
              onClick={requestDbRecovery}
              disabled={busyAction !== null || Boolean(recoverDbDisabledReason)}
            >
              {busyAction === 'db-recovery' ? 'Recovering...' : 'Recover DB'}
            </button>
            <button
              type="button"
              className="button button--ghost"
              onClick={requestTradeLogArchive}
              disabled={busyAction !== null || Boolean(tradeLogArchiveDisabledReason)}
            >
              {busyAction === 'trade-log-archive' ? 'Archiving...' : 'Archive Trade Log'}
            </button>
            {liveConfigNeedsShadow ? (
              <button
                type="button"
                className="button button--ghost"
                onClick={requestForceShadowMode}
                disabled={busyAction !== null || Boolean(forceShadowModeDisabledReason)}
              >
                {busyAction === 'force-shadow-mode' ? 'Saving...' : 'Set Shadow-Only'}
              </button>
            ) : null}
          </div>
        </div>
        {operationalWarnings.length ? (
          <div className="stack">
            {operationalWarnings.map((warning) => (
              <p key={warning} className="panel__warning">
                {warning}
              </p>
            ))}
          </div>
        ) : null}
        <div className="stack">
          <p className="panel__subtle">
            Restart Shadow ({humanizeStatus(shadowRestartWalletMode)}): {restartShadowActionDetail}
          </p>
          <p className="panel__subtle">Recover DB: {recoverDbActionDetail}</p>
          <p className="panel__subtle">Archive Trade Log: {tradeLogArchiveActionDetail}</p>
          <p className="panel__subtle">Set Shadow-Only: {forceShadowModeActionDetail}</p>
          <p className="panel__subtle">Mode policy: {configuredModeLabel} configured, {effectiveModeLabel} effective.</p>
          <p className="panel__subtle">Mode override: {modeOverrideDetail}</p>
          <p className="panel__subtle">Recovery path: {recoveryPathDetail}</p>
          <p className="panel__subtle">Latest verified backup: {latestVerifiedBackupDetail}</p>
          <p className="panel__subtle">Recovery Inventory: {recoveryInventorySummary}</p>
          {recoveryInventoryPreview.map((line) => (
            <p key={line} className="panel__subtle">
              Recovery Candidate: {line}
            </p>
          ))}
        </div>
        <dl className="detail-list">
          {operationsRows.map((row) => (
            <div key={row.label} className="detail-list__row">
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
        <div className="footnote">
          <p>
            Trade log DB: {formatLogicalAllocated(
              botState?.trade_log_archive_active_db_size_bytes,
              botState?.trade_log_archive_active_db_allocated_bytes
            )}
          </p>
          <p>
            Archive DB: {formatLogicalAllocated(
              botState?.trade_log_archive_archive_db_size_bytes,
              botState?.trade_log_archive_archive_db_allocated_bytes
            )}
          </p>
          <p>
            Archive rows: active {formatNumber(botState?.trade_log_archive_active_row_count)} / archived{' '}
            {formatNumber(botState?.trade_log_archive_archive_row_count)}
          </p>
          <p>Archive last run: {formatTimestamp(botState?.trade_log_archive_last_run_at)}</p>
          <p>Archive note: {tradeLogArchiveMessage}</p>
          <p>Storage note: {storageMessage}</p>
        </div>
      </section>

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
            {incomingEvents.length ? (
              incomingEvents.map((event) => <EventCard key={`incoming-${event.trade_id}-${event.ts}`} event={event} />)
            ) : (
              <p className="empty-state">{eventsBlockedMessage || 'Waiting for incoming trade events.'}</p>
            )}
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
            {signalEvents.length ? (
              signalEvents.map((event) => <EventCard key={`signal-${event.trade_id}-${event.ts}`} event={event} />)
            ) : (
              <p className="empty-state">{eventsBlockedMessage || 'No scored signals yet.'}</p>
            )}
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

      {dashboardActionMessage ? (
        <section className="panel panel--warning">
          <div className="panel__header">
            <div>
              <p className="panel__eyebrow">Dashboard Action</p>
              <h2>Latest wallet or discovery action</h2>
            </div>
          </div>
          <p className="panel__warning">{dashboardActionMessage}</p>
        </section>
      ) : null}

      <section className="panel">
        <div className="panel__header">
          <div>
            <p className="panel__eyebrow">Wallet Registry</p>
            <h2>DB-backed managed wallets</h2>
          </div>
          <p className="panel__subtle">
            {formatNumber(managedWalletsResource.data?.count ?? managedWallets.length)} wallets • {walletRegistrySource}
          </p>
        </div>
        {managedWalletsResource.error ? <p className="panel__warning">{managedWalletsResource.error.message}</p> : null}
        <dl className="detail-list">
          {walletPromotionRows.map((row) => (
            <div key={row.label} className="detail-list__row">
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
        <div className="stack">
          {managedWallets.length ? (
            managedWallets.map((wallet) => (
              <ManagedWalletCard
                key={String(wallet.wallet_address || '').toLowerCase()}
                wallet={wallet}
                onDrop={dropWallet}
                onReactivate={reactivateWallet}
                busyWallet={busyAction}
              />
            ))
          ) : (
            <p className="empty-state">{walletRegistryEmptyMessage}</p>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <div>
            <p className="panel__eyebrow">Discovery</p>
            <h2>Candidate wallets</h2>
          </div>
          <div className="hero__controls">
            <p className="panel__subtle">
              {formatNumber(discoveryCandidatesResource.data?.count ?? discoveryCandidates.length)} candidates • {discoveryScanStatus}
            </p>
            <button
              type="button"
              className="button button--ghost"
              onClick={triggerDiscoveryScan}
              disabled={busyAction !== null}
            >
              {busyAction === 'scan' ? 'Scanning...' : 'Scan now'}
            </button>
          </div>
        </div>
        <p className="panel__subtle">{discoveryScanMessage}</p>
        {discoveryCandidatesResource.error ? (
          <p className="panel__warning">{discoveryCandidatesResource.error.message}</p>
        ) : null}
        <div className="stack">
          {discoveryCandidates.length ? (
            discoveryCandidates.map((candidate) => (
              <DiscoveryCandidateCard
                key={String(candidate.wallet_address || '').toLowerCase()}
                candidate={candidate}
              />
            ))
          ) : (
            <p className="empty-state">{discoveryEmptyMessage}</p>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <div>
            <p className="panel__eyebrow">Membership Timeline</p>
            <h2>Wallet lifecycle events</h2>
          </div>
          <p className="panel__subtle">
            {formatNumber(walletEventsResource.data?.count ?? walletMembershipEvents.length)} events •{' '}
            {humanizeStatus(walletEventsResource.data?.source, 'snapshot')}
          </p>
        </div>
        {walletEventsResource.error ? <p className="panel__warning">{walletEventsResource.error.message}</p> : null}
        <div className="stack">
          {walletMembershipEvents.length ? (
            walletMembershipEvents.map((event, index) => (
              <WalletEventCard
                key={`${String(event.wallet_address || '').toLowerCase()}-${String(event.action || index)}-${String(
                  event.created_at || index
                )}`}
                event={event}
              />
            ))
          ) : (
            <p className="empty-state">{walletTimelineEmptyMessage}</p>
          )}
        </div>
      </section>
    </main>
  )
}
