import {useEffect, useMemo, useState} from 'react'
import {
  fetchBotState,
  fetchConfigSnapshot,
  fetchDiscoveryCandidates,
  fetchManagedWallets,
  fetchPerformanceSnapshot,
  type PerformanceSnapshot
} from './api'
import {ConfigPage, ModelPage, PerformancePage, WalletsPage} from './dashboardPages'
import {dashboardDataMode, dashboardModel} from './mockDashboard'
import {useEventFeed} from './feedUtils'
import {SignalsFeed} from './signalsFeed'
import {TrackerFeed} from './trackerFeed'

const BOT_STATE_POLL_INTERVAL_MS = 2000
const SNAPSHOT_POLL_INTERVAL_MS = 5000

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
  const [botState, setBotState] = useState(
    mode === 'mock' ? dashboardModel.botState : {}
  )
  const [managedWallets, setManagedWallets] = useState(
    mode === 'mock' ? dashboardModel.managedWallets : {wallets: [], count: 0}
  )
  const [discoveryCandidates, setDiscoveryCandidates] = useState(
    mode === 'mock' ? dashboardModel.discoveryCandidates : {candidates: [], count: 0}
  )
  const [performanceSnapshot, setPerformanceSnapshot] = useState<PerformanceSnapshot | null>(null)
  const {events: pageEvents, loading: pageEventsLoading, error: pageEventsError} = useEventFeed(
    mode,
    mockPageEvents
  )
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

  useEffect(() => {
    if (mode !== 'api') {
      return
    }

    let cancelled = false

    async function refreshBotState() {
      try {
        const nextState = await fetchBotState()
        if (!cancelled && nextState) {
          setBotState(nextState)
        }
      } catch (error) {
        if (!cancelled) {
          console.warn('Failed to refresh bot state', error)
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
        const [nextConfig, nextWallets, nextDiscovery] = await Promise.all([
          fetchConfigSnapshot(),
          fetchManagedWallets(),
          fetchDiscoveryCandidates()
        ])
        if (cancelled) {
          return
        }
        if (nextConfig) {
          setConfigSnapshot(nextConfig)
        }
        if (nextWallets) {
          setManagedWallets(nextWallets)
        }
        if (nextDiscovery) {
          setDiscoveryCandidates(nextDiscovery)
        }
      } catch (error) {
        if (!cancelled) {
          console.warn('Failed to refresh dashboard snapshots', error)
        }
      }

      try {
        const nextPerformance = await fetchPerformanceSnapshot()
        if (!cancelled && nextPerformance) {
          setPerformanceSnapshot(nextPerformance)
        }
      } catch (error) {
        if (!cancelled) {
          console.warn('Failed to refresh performance snapshot', error)
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
            performanceSnapshot={performanceSnapshot}
          />
        ) : null}
        {activePage === 'models' ? (
          <ModelPage
            signalEvents={signalEvents}
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
            managedWallets={managedWallets}
            discoveryCandidates={discoveryCandidates}
            onManagedWalletsChange={setManagedWallets}
          />
        ) : null}
        {activePage === 'config' ? (
          <ConfigPage
            mode={mode}
            configSnapshot={configSnapshot}
            managedWallets={managedWallets}
            botState={botState}
            onConfigSnapshotChange={setConfigSnapshot}
            onBotStateChange={setBotState}
          />
        ) : null}
      </main>
    </div>
  )
}
