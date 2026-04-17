import {useEffect, useMemo, useState} from 'react'
import {ConfigPage, ModelPage, PerformancePage, WalletsPage} from './dashboardPages'
import {dashboardModel} from './mockDashboard'
import {SignalsFeed} from './signalsFeed'
import {TrackerFeed} from './trackerFeed'

function resolveConfidenceCutoff(configSnapshot: {rows?: Array<{key?: string; value?: string}>; safe_values?: Record<string, string>}): number | undefined {
  const rowValue = configSnapshot.rows?.find((row) => row.key === 'MIN_CONFIDENCE')?.value
  const rawValue = rowValue ?? configSnapshot.safe_values?.MIN_CONFIDENCE
  if (!rawValue) return undefined
  const parsed = Number.parseFloat(rawValue)
  if (!Number.isFinite(parsed)) return undefined
  return Math.min(1, Math.max(0, parsed))
}

export function App() {
  const [activePage, setActivePage] = useState(dashboardModel.pages[0]?.id ?? 'tracker')
  const [configSnapshot, setConfigSnapshot] = useState(dashboardModel.configSnapshot)
  const [botState, setBotState] = useState(dashboardModel.botState)
  const [managedWallets, setManagedWallets] = useState(dashboardModel.managedWallets)
  const confidenceCutoff = useMemo(
    () => resolveConfidenceCutoff(configSnapshot),
    [configSnapshot]
  )

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
            mode={dashboardModel.mode}
            mockEvents={dashboardModel.trackerEvents}
            bankrollUsd={botState.bankroll_usd}
          />
        ) : null}
        {activePage === 'signals' ? (
          <SignalsFeed mode={dashboardModel.mode} mockEvents={dashboardModel.signalEvents} />
        ) : null}
        {activePage === 'perf' ? (
          <PerformancePage
            mode={dashboardModel.mode}
            trackerEvents={dashboardModel.trackerEvents}
            signalEvents={dashboardModel.signalEvents}
            resolvedShadowTradeCount={botState.resolved_shadow_trade_count}
            bankrollUsd={botState.bankroll_usd}
            confidenceCutoff={confidenceCutoff}
            pollInterval={botState.poll_interval}
            lastPollDurationS={botState.last_poll_duration_s}
            lastEventCount={botState.last_event_count}
            shadowSnapshotStatus={botState.shadow_snapshot_status}
            shadowSnapshotResolved={botState.shadow_snapshot_resolved}
            shadowSnapshotRoutedResolved={botState.shadow_snapshot_routed_resolved}
          />
        ) : null}
        {activePage === 'models' ? (
          <ModelPage
            signalEvents={dashboardModel.signalEvents}
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
            mode={dashboardModel.mode}
            bankrollUsd={botState.bankroll_usd}
            managedWallets={managedWallets}
            discoveryCandidates={dashboardModel.discoveryCandidates}
            onManagedWalletsChange={setManagedWallets}
          />
        ) : null}
        {activePage === 'config' ? (
          <ConfigPage
            mode={dashboardModel.mode}
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
