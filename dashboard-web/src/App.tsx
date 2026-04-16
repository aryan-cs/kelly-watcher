import {useEffect, useState} from 'react'
import {
  ApiError,
  fetchBotState,
  fetchDiscoveryCandidates,
  fetchManagedWallets,
  fetchWalletMembershipEvents,
  requestDiscoveryScan,
  type BotState,
  type DiscoveryCandidatesResponse,
  type ManagedWalletsResponse,
  type WalletMembershipEventsResponse
} from './api'

type DashboardState = {
  botState: BotState | null
  managedWallets: ManagedWalletsResponse | null
  discovery: DiscoveryCandidatesResponse | null
  events: WalletMembershipEventsResponse | null
}

const emptyState: DashboardState = {
  botState: null,
  managedWallets: null,
  discovery: null,
  events: null
}

function formatTimestamp(value: unknown): string {
  const ts = Number(value || 0)
  if (!Number.isFinite(ts) || ts <= 0) {
    return 'n/a'
  }
  return new Date(ts * 1000).toLocaleString()
}

function formatPercent(value: unknown): string {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return 'n/a'
  }
  return `${(numeric * 100).toFixed(1)}%`
}

function formatNumber(value: unknown, digits = 2): string {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return 'n/a'
  }
  return numeric.toFixed(digits)
}

async function loadDashboardState(): Promise<DashboardState> {
  const [botState, managedWallets, discovery, events] = await Promise.all([
    fetchBotState(),
    fetchManagedWallets(),
    fetchDiscoveryCandidates(),
    fetchWalletMembershipEvents()
  ])
  return {
    botState,
    managedWallets,
    discovery,
    events
  }
}

export function App() {
  const [state, setState] = useState<DashboardState>(emptyState)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [scanRunning, setScanRunning] = useState(false)
  const [statusMessage, setStatusMessage] = useState('')
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
    let cancelled = false

    async function refresh(initial = false) {
      if (initial) {
        setLoading(true)
      } else {
        setRefreshing(true)
      }
      setErrorMessage('')
      try {
        const nextState = await loadDashboardState()
        if (!cancelled) {
          setState(nextState)
          setStatusMessage('')
        }
      } catch (error) {
        if (!cancelled) {
          const detail =
            error instanceof ApiError
              ? error.message
              : error instanceof Error
                ? error.message
                : 'Unknown dashboard error.'
          setErrorMessage(detail)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
          setRefreshing(false)
        }
      }
    }

    void refresh(true)
    return () => {
      cancelled = true
    }
  }, [])

  async function handleRefresh(): Promise<void> {
    setRefreshing(true)
    setErrorMessage('')
    try {
      setState(await loadDashboardState())
      setStatusMessage('Dashboard state refreshed from the backend API.')
    } catch (error) {
      const detail =
        error instanceof ApiError ? error.message : error instanceof Error ? error.message : 'Refresh failed.'
      setErrorMessage(detail)
    } finally {
      setRefreshing(false)
    }
  }

  async function handleScanNow(): Promise<void> {
    setScanRunning(true)
    setErrorMessage('')
    try {
      const response = await requestDiscoveryScan()
      setStatusMessage(String(response?.message || 'Discovery scan requested.').trim())
      setState(await loadDashboardState())
    } catch (error) {
      const detail =
        error instanceof ApiError ? error.message : error instanceof Error ? error.message : 'Scan failed.'
      setErrorMessage(detail)
    } finally {
      setScanRunning(false)
    }
  }

  const registryStatus = String(
    state.managedWallets?.managed_wallet_registry_status ||
      state.botState?.managed_wallet_registry_status ||
      'unknown'
  )
  const registryMessage = String(state.managedWallets?.message || '')
  const discoveryMessage = String(state.discovery?.message || '')
  const wallets = state.managedWallets?.wallets || []
  const candidates = state.discovery?.candidates || []
  const events = state.events?.events || state.managedWallets?.events || []

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__brand">kelly-watcher backend</div>
        <div className="topbar__nav">
          <button type="button" className="topbar__link" onClick={() => void handleRefresh()} disabled={refreshing || scanRunning}>
            {refreshing ? 'Refreshing...' : 'Refresh now'}
          </button>
          <button type="button" className="topbar__link" onClick={() => void handleScanNow()} disabled={scanRunning || loading}>
            {scanRunning ? 'Running scan...' : 'Scan now'}
          </button>
        </div>
      </header>

      <main className="page-canvas">
        <section>
          <h1>Backend Wallet Discovery</h1>
          <p>DB-backed managed wallets, discovery candidates, and lifecycle events from the backend API.</p>
          {loading ? <p>Loading backend state...</p> : null}
          {statusMessage ? <p>{statusMessage}</p> : null}
          {errorMessage ? <p>{errorMessage}</p> : null}
        </section>

        <section>
          <h2>System</h2>
          <p>
            Mode: <strong>{state.botState?.mode || 'unknown'}</strong> | Registry status:{' '}
            <strong>{registryStatus}</strong> | Last discovery scan:{' '}
            <strong>{formatTimestamp(state.botState?.wallet_discovery_last_scan_at)}</strong>
          </p>
          {registryMessage ? <p>{registryMessage}</p> : null}
          {discoveryMessage ? <p>{discoveryMessage}</p> : null}
        </section>

        <section>
          <h2>Wallet Registry</h2>
          <p>DB-backed managed wallets currently loaded from the canonical registry.</p>
          {wallets.length === 0 ? (
            <p>No managed wallets are currently available.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Wallet</th>
                  <th>Status</th>
                  <th>Source</th>
                  <th>Baseline</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {wallets.slice(0, 25).map((wallet) => (
                  <tr key={String(wallet.wallet_address || '')}>
                    <td>{wallet.wallet_address || 'n/a'}</td>
                    <td>{wallet.status || 'n/a'}</td>
                    <td>{wallet.source || wallet.registry_source || 'n/a'}</td>
                    <td>{formatTimestamp(wallet.post_promotion_baseline_at)}</td>
                    <td>{wallet.post_promotion_evidence_note || 'n/a'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section>
          <h2>Discovery</h2>
          <p>Copyability-first discovery candidates cached by the backend scheduler.</p>
          {candidates.length === 0 ? (
            <p>No discovery candidates are currently cached.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Wallet</th>
                  <th>Status</th>
                  <th>Score</th>
                  <th>Sources</th>
                  <th>Skip rate</th>
                </tr>
              </thead>
              <tbody>
                {candidates.slice(0, 25).map((candidate) => (
                  <tr key={String(candidate.wallet_address || '')}>
                    <td>{candidate.wallet_address || 'n/a'}</td>
                    <td>
                      {String(
                        candidate.copyability_gate_status ||
                          (candidate.accepted ? 'ready' : 'review')
                      )}
                    </td>
                    <td>{formatNumber(candidate.follow_score)}</td>
                    <td>{Array.isArray(candidate.source_labels) ? candidate.source_labels.join(', ') : 'n/a'}</td>
                    <td>{formatPercent(candidate.post_promotion_uncopyable_skip_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section>
          <h2>Membership Timeline</h2>
          <p>Lifecycle events emitted by the wallet registry and promotion flow.</p>
          {events.length === 0 ? (
            <p>No wallet membership events are currently available.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Wallet</th>
                  <th>Action</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {events.slice(0, 25).map((event, index) => (
                  <tr key={`${String(event.wallet_address || 'wallet')}-${index}`}>
                    <td>{formatTimestamp(event.created_at)}</td>
                    <td>{event.wallet_address || 'n/a'}</td>
                    <td>{event.action || 'n/a'}</td>
                    <td>{event.source || 'n/a'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </main>
    </div>
  )
}
