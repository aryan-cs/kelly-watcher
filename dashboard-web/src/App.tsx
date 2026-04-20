import {useEffect, useMemo, useState} from 'react'
import {
  type BotState,
  fetchBotState,
  fetchConfigSnapshot,
  fetchPerformanceSnapshot,
  fetchDroppedWallets,
  fetchTrackedWallets,
  fetchWalletSummary,
  type ManagedWallet,
  type ManagedWalletsResponse,
  type PerformanceSnapshot,
  type WalletRowsResponse,
  type WalletSummaryResponse
} from './api'
import {ConfigPage, ModelPage, PerformancePage, WalletsPage} from './dashboardPages'
import {dashboardDataMode, dashboardModel} from './mockDashboard'
import {useEventFeed} from './feedUtils'
import {SignalsFeed} from './signalsFeed'
import {TrackerFeed} from './trackerFeed'

const BOT_STATE_POLL_INTERVAL_MS = 2000
const SNAPSHOT_POLL_INTERVAL_MS = 5000

type ResourceStatus = 'loading' | 'ready' | 'stale' | 'error'

interface ResourceState<T> {
  status: ResourceStatus
  data: T | null
  error: string
}

function readyResource<T>(data: T): ResourceState<T> {
  return {
    status: 'ready',
    data,
    error: ''
  }
}

function loadingResource<T>(data: T | null = null): ResourceState<T> {
  return {
    status: 'loading',
    data,
    error: ''
  }
}

function failureResource<T>(current: ResourceState<T>, error: unknown): ResourceState<T> {
  const message = error instanceof Error ? String(error.message || '').trim() : 'REQUEST FAILED.'
  if (current.data) {
    return {
      status: 'stale',
      data: current.data,
      error: message
    }
  }
  return {
    status: 'error',
    data: null,
    error: message
  }
}

function walletPnl(wallet: ManagedWallet): number {
  return Number(wallet.post_promotion_resolved_copied_total_pnl_usd || 0)
}

function walletResolvedCount(wallet: ManagedWallet): number {
  return Number(wallet.post_promotion_resolved_copied_count || 0)
}

function buildMockWalletSummary(
  managedWallets: ManagedWalletsResponse,
  discoveryCandidateCount: number
): WalletSummaryResponse {
  const wallets = managedWallets.wallets || []
  const tracked = wallets.filter((wallet) => wallet.status !== 'disabled')
  const dropped = wallets.filter((wallet) => wallet.status === 'disabled')
  const bestWallets = [...wallets]
    .sort((left, right) => {
      const pnlDelta = walletPnl(right) - walletPnl(left)
      if (pnlDelta !== 0) return pnlDelta
      return walletResolvedCount(right) - walletResolvedCount(left)
    })
    .slice(0, 8)
  const worstWallets = [...wallets]
    .sort((left, right) => {
      const pnlDelta = walletPnl(left) - walletPnl(right)
      if (pnlDelta !== 0) return pnlDelta
      return walletResolvedCount(right) - walletResolvedCount(left)
    })
    .slice(0, 8)

  return {
    ok: true,
    source: 'mock',
    managed_wallet_count: wallets.length,
    managed_wallet_total_count: wallets.length,
    tracked_count: tracked.length,
    dropped_count: dropped.length,
    discovery_candidate_count: discoveryCandidateCount,
    best_wallets: bestWallets,
    worst_wallets: worstWallets
  }
}

function buildMockWalletRows(category: 'tracked' | 'dropped', managedWallets: ManagedWalletsResponse): WalletRowsResponse {
  const wallets = (managedWallets.wallets || []).filter((wallet) =>
    category === 'tracked' ? wallet.status !== 'disabled' : wallet.status === 'disabled'
  )
  return {
    ok: true,
    source: 'mock',
    category,
    count: wallets.length,
    wallets
  }
}

function resolveConfidenceCutoff(configSnapshot: {rows?: Array<{key?: string; value?: string}>; safe_values?: Record<string, string>}): number | undefined {
  const rowValue = configSnapshot.rows?.find((row) => row.key === 'MIN_CONFIDENCE')?.value
  const rawValue = rowValue ?? configSnapshot.safe_values?.MIN_CONFIDENCE
  if (!rawValue) return undefined
  const parsed = Number.parseFloat(rawValue)
  if (!Number.isFinite(parsed)) return undefined
  return Math.min(1, Math.max(0, parsed))
}

export function App() {
  const mode = dashboardDataMode
  const mockPageEvents = useMemo(
    () => [...dashboardModel.trackerEvents, ...dashboardModel.signalEvents],
    []
  )
  const [activePage, setActivePage] = useState(dashboardModel.pages[0]?.id ?? 'tracker')
  const [configSnapshot, setConfigSnapshot] = useState(
    mode === 'mock' ? dashboardModel.configSnapshot : {}
  )
  const [botStateResource, setBotStateResource] = useState<ResourceState<BotState>>(
    mode === 'mock' ? readyResource(dashboardModel.botState) : loadingResource()
  )
  const [mockManagedWallets, setMockManagedWallets] = useState<ManagedWalletsResponse>(
    mode === 'mock' ? dashboardModel.managedWallets : {wallets: [], count: 0}
  )
  const [walletSummaryResource, setWalletSummaryResource] = useState<ResourceState<WalletSummaryResponse>>(
    mode === 'mock'
      ? readyResource(
          buildMockWalletSummary(
            dashboardModel.managedWallets,
            dashboardModel.discoveryCandidates.count || 0
          )
        )
      : loadingResource()
  )
  const [trackedWalletsResource, setTrackedWalletsResource] = useState<ResourceState<WalletRowsResponse>>(
    mode === 'mock' ? readyResource(buildMockWalletRows('tracked', dashboardModel.managedWallets)) : loadingResource()
  )
  const [droppedWalletsResource, setDroppedWalletsResource] = useState<ResourceState<WalletRowsResponse>>(
    mode === 'mock' ? readyResource(buildMockWalletRows('dropped', dashboardModel.managedWallets)) : loadingResource()
  )
  const [performanceResource, setPerformanceResource] = useState<ResourceState<PerformanceSnapshot>>(
    mode === 'mock' ? loadingResource() : loadingResource()
  )
  const {events: pageEvents, loading: pageEventsLoading, error: pageEventsError} = useEventFeed(
    mode,
    mockPageEvents
  )
  const botState = botStateResource.data || {}
  const trackerEvents = useMemo(
    () => (mode === 'api' ? pageEvents.filter((event) => event.type === 'incoming') : dashboardModel.trackerEvents),
    [mode, pageEvents]
  )
  const signalEvents = useMemo(
    () => (mode === 'api' ? pageEvents.filter((event) => event.type === 'signal') : dashboardModel.signalEvents),
    [mode, pageEvents]
  )
  const confidenceCutoff = useMemo(
    () => resolveConfidenceCutoff(configSnapshot),
    [configSnapshot]
  )
  const walletSummary = useMemo(
    () =>
      mode === 'mock'
        ? buildMockWalletSummary(mockManagedWallets, dashboardModel.discoveryCandidates.count || 0)
        : walletSummaryResource.data,
    [mockManagedWallets, mode, walletSummaryResource.data]
  )
  const trackedWallets = useMemo(
    () =>
      mode === 'mock'
        ? buildMockWalletRows('tracked', mockManagedWallets)
        : trackedWalletsResource.data,
    [mockManagedWallets, mode, trackedWalletsResource.data]
  )
  const droppedWallets = useMemo(
    () =>
      mode === 'mock'
        ? buildMockWalletRows('dropped', mockManagedWallets)
        : droppedWalletsResource.data,
    [mockManagedWallets, mode, droppedWalletsResource.data]
  )
  const managedWalletsForConfig = useMemo<ManagedWalletsResponse>(() => {
    if (mode === 'mock') {
      return mockManagedWallets
    }
    const trackedRows = trackedWallets?.wallets || []
    const droppedRows = droppedWallets?.wallets || []
    const wallets = [...trackedRows, ...droppedRows]
    return {
      ok: Boolean(walletSummary?.ok),
      source: walletSummary?.source,
      managed_wallet_registry_status: walletSummary?.managed_wallet_registry_status,
      managed_wallet_registry_available: walletSummary?.managed_wallet_registry_available,
      managed_wallet_registry_error: walletSummary?.managed_wallet_registry_error,
      managed_wallet_count: walletSummary?.managed_wallet_count ?? wallets.length,
      managed_wallet_total_count: walletSummary?.managed_wallet_total_count ?? wallets.length,
      count: wallets.length,
      wallets
    }
  }, [droppedWallets, mode, mockManagedWallets, trackedWallets, walletSummary])

  function applyBotStateError(error: unknown): void {
    setBotStateResource((current) => failureResource(current, error))
  }

  function applySnapshotError<T>(
    setter: React.Dispatch<React.SetStateAction<ResourceState<T>>>,
    error: unknown
  ): void {
    setter((current) => failureResource(current, error))
  }

  async function refreshWalletResources(): Promise<void> {
    const [summaryResult, trackedResult, droppedResult] = await Promise.allSettled([
      fetchWalletSummary(),
      fetchTrackedWallets(),
      fetchDroppedWallets()
    ])

    if (summaryResult.status === 'fulfilled' && summaryResult.value) {
      setWalletSummaryResource(readyResource(summaryResult.value))
    } else if (summaryResult.status === 'rejected') {
      applySnapshotError(setWalletSummaryResource, summaryResult.reason)
    }

    if (trackedResult.status === 'fulfilled' && trackedResult.value) {
      setTrackedWalletsResource(readyResource(trackedResult.value))
    } else if (trackedResult.status === 'rejected') {
      applySnapshotError(setTrackedWalletsResource, trackedResult.reason)
    }

    if (droppedResult.status === 'fulfilled' && droppedResult.value) {
      setDroppedWalletsResource(readyResource(droppedResult.value))
    } else if (droppedResult.status === 'rejected') {
      applySnapshotError(setDroppedWalletsResource, droppedResult.reason)
    }
  }

  useEffect(() => {
    if (mode !== 'api') {
      return
    }

    let cancelled = false

    async function refreshBotState() {
      try {
        const nextState = await fetchBotState()
        if (!cancelled && nextState) {
          setBotStateResource(readyResource(nextState))
        }
      } catch (error) {
        if (!cancelled) {
          console.warn('Failed to refresh bot state', error)
          applyBotStateError(error)
        }
      }
    }

    void refreshBotState()
    const timer = window.setInterval(() => {
      void refreshBotState()
    }, BOT_STATE_POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [mode])

  useEffect(() => {
    if (mode !== 'api') {
      return
    }

    let cancelled = false

    async function refreshSnapshots() {
      try {
        const nextConfig = await fetchConfigSnapshot()
        if (cancelled) {
          return
        }
        if (nextConfig) {
          setConfigSnapshot(nextConfig)
        }
      } catch (error) {
        if (!cancelled) {
          console.warn('Failed to refresh dashboard snapshots', error)
        }
      }

      try {
        await refreshWalletResources()
      } catch (error) {
        if (!cancelled) {
          console.warn('Failed to refresh wallet resources', error)
        }
      }

      try {
        const nextPerformance = await fetchPerformanceSnapshot()
        if (!cancelled && nextPerformance) {
          setPerformanceResource(readyResource(nextPerformance))
        }
      } catch (error) {
        if (!cancelled) {
          console.warn('Failed to refresh performance snapshot', error)
          applySnapshotError(setPerformanceResource, error)
        }
      }
    }

    void refreshSnapshots()
    const timer = window.setInterval(() => {
      void refreshSnapshots()
    }, SNAPSHOT_POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [mode])

  useEffect(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) {
        return
      }
      const target = event.target
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.tagName === 'BUTTON')
      ) {
        return
      }
      const pageIndex = Number.parseInt(event.key, 10)
      if (Number.isNaN(pageIndex) || pageIndex < 1 || pageIndex > dashboardModel.pages.length) {
        return
      }
      const nextPage = dashboardModel.pages[pageIndex - 1]
      if (!nextPage) return
      setActivePage(nextPage.id)
    }

    window.addEventListener('keydown', handleKeydown)
    return () => window.removeEventListener('keydown', handleKeydown)
  }, [])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__brand">kelly-watcher</div>
        <nav aria-label="Dashboard pages" className="topbar__nav">
          {dashboardModel.pages.map((page) => {
            const isActive = page.id === activePage
            return (
              <button
                key={page.id}
                type="button"
                className={`topbar__link${isActive ? ' topbar__link--active' : ''}`}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => setActivePage(page.id)}
              >
                {page.label}
              </button>
            )
          })}
        </nav>
      </header>
      <main aria-label={`${activePage} page`} className="page-canvas">
        {activePage === 'tracker' ? (
          <TrackerFeed
            events={trackerEvents}
            loading={pageEventsLoading}
            error={pageEventsError}
            bankrollUsd={botState.bankroll_usd}
            sourceLabel={mode === 'mock' ? 'MOCK FEED' : 'LIVE FEED'}
          />
        ) : null}
        {activePage === 'signals' ? (
          <SignalsFeed
            events={signalEvents}
            loading={pageEventsLoading}
            error={pageEventsError}
            sourceLabel={mode === 'mock' ? 'MOCK FEED' : 'LIVE FEED'}
          />
        ) : null}
        {activePage === 'perf' ? (
          <PerformancePage
            mode={mode}
            trackerEvents={trackerEvents}
            signalEvents={signalEvents}
            resolvedShadowTradeCount={botState.resolved_shadow_trade_count}
            bankrollUsd={botState.bankroll_usd}
            confidenceCutoff={confidenceCutoff}
            pollInterval={botState.poll_interval}
            lastPollDurationS={botState.last_poll_duration_s}
            lastEventCount={botState.last_event_count}
            shadowSnapshotStatus={botState.shadow_snapshot_status}
            shadowSnapshotResolved={botState.shadow_snapshot_resolved}
            shadowSnapshotRoutedResolved={botState.shadow_snapshot_routed_resolved}
            performanceResource={performanceResource}
          />
        ) : null}
        {activePage === 'models' ? (
          <ModelPage
            mode={mode}
            botStateStatus={botStateResource.status}
            botStateError={botStateResource.error}
            signalEvents={mode === 'mock' ? signalEvents : []}
            loadedScorer={botState.loaded_scorer}
            modelBackend={botState.loaded_model_backend}
            modelPredictionMode={botState.model_prediction_mode}
            modelRuntimeCompatible={botState.model_runtime_compatible}
            modelFallbackReason={botState.model_fallback_reason}
            modelLoadedAt={botState.model_loaded_at}
            startupDetail={botState.startup_detail}
            startupFailed={botState.startup_failed}
            startupValidationFailed={botState.startup_validation_failed}
            retrainInProgress={botState.retrain_in_progress}
            lastRetrainStartedAt={botState.last_retrain_started_at}
            lastRetrainFinishedAt={botState.last_retrain_finished_at}
            lastRetrainStatus={botState.last_retrain_status}
            lastRetrainMessage={botState.last_retrain_message}
            lastReplaySearchStartedAt={botState.last_replay_search_started_at}
            lastReplaySearchFinishedAt={botState.last_replay_search_finished_at}
            lastReplaySearchStatus={botState.last_replay_search_status}
            lastReplaySearchMessage={botState.last_replay_search_message}
            trainingRuns={botState.training_runs}
            manualRetrainPending={botState.manual_retrain_pending}
            manualTradePending={botState.manual_trade_pending}
            liveRequireShadowHistoryEnabled={botState.live_require_shadow_history_enabled}
            liveShadowHistoryReady={botState.live_shadow_history_ready}
            liveShadowHistoryTotalReady={botState.live_shadow_history_total_ready}
            shadowSnapshotScope={botState.shadow_snapshot_scope}
            shadowSnapshotStatus={botState.shadow_snapshot_status}
            shadowSnapshotResolved={botState.shadow_snapshot_resolved}
            shadowSnapshotRoutedResolved={botState.shadow_snapshot_routed_resolved}
            shadowSnapshotReady={botState.shadow_snapshot_ready}
            shadowSnapshotBlockReason={botState.shadow_snapshot_block_reason}
          />
        ) : null}
        {activePage === 'wallets' ? (
          <WalletsPage
            mode={mode}
            bankrollUsd={botState.bankroll_usd}
            walletSummaryResource={mode === 'mock' ? readyResource(walletSummary || {ok: true}) : walletSummaryResource}
            trackedWalletsResource={mode === 'mock' ? readyResource(trackedWallets || {ok: true, wallets: []}) : trackedWalletsResource}
            droppedWalletsResource={mode === 'mock' ? readyResource(droppedWallets || {ok: true, wallets: []}) : droppedWalletsResource}
            onMockManagedWalletsChange={setMockManagedWallets}
            refreshWalletResources={refreshWalletResources}
          />
        ) : null}
        {activePage === 'config' ? (
          <ConfigPage
            mode={mode}
            configSnapshot={configSnapshot}
            managedWallets={managedWalletsForConfig}
            botState={botState}
            onConfigSnapshotChange={setConfigSnapshot}
            onBotStateChange={(nextState) => setBotStateResource(readyResource(nextState))}
          />
        ) : null}
      </main>
    </div>
  )
}
