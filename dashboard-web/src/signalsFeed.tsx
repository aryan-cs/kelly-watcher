import {useMemo, type ReactNode} from 'react'
import {type LiveEvent} from './api'
import {
  buildTradeIdLookup,
  decisionColor,
  feedTheme,
  formatClock,
  formatDisplayId,
  formatFixedDollar,
  formatFixedNumber,
  formatFixedPercent,
  joinClasses,
  normalizeReasonText,
  outcomeColor,
  probabilityColor,
  resolveActionText,
  shortAddress,
  useEventFeed
} from './feedUtils'

interface SignalsFeedProps {
  mode: 'mock' | 'api'
  mockEvents: LiveEvent[]
}

type SignalsColumnKey =
  | 'id'
  | 'time'
  | 'username'
  | 'market'
  | 'action'
  | 'side'
  | 'price'
  | 'shares'
  | 'total'
  | 'decision'
  | 'confidence'
  | 'reason'

interface SignalsDisplayRow {
  key: string
  marketUrl?: string
  cells: Record<SignalsColumnKey, string>
  colors: Partial<Record<SignalsColumnKey, string>>
  titles: Partial<Record<SignalsColumnKey, string>>
}

interface SignalsColumn {
  key: SignalsColumnKey
  label: string
  colClassName: string
  cellClassName?: string
  render?: (row: SignalsDisplayRow) => ReactNode
}

const SIGNALS_COLUMNS: SignalsColumn[] = [
  {key: 'id', label: 'ID', colClassName: 'signals-col signals-col--compact', cellClassName: 'signals-cell--numeric signals-cell--muted'},
  {key: 'time', label: 'TIME', colClassName: 'signals-col signals-col--compact'},
  {key: 'username', label: 'USERNAME', colClassName: 'signals-col signals-col--compact'},
  {
    key: 'market',
    label: 'MARKET',
    colClassName: 'signals-col signals-col--market',
    cellClassName: 'signals-cell--market',
    render: (row) =>
      row.marketUrl ? (
        <a className="signals-link" href={row.marketUrl} rel="noreferrer" target="_blank" title={row.titles.market}>
          {row.cells.market}
        </a>
      ) : (
        row.cells.market
      )
  },
  {key: 'action', label: 'ACTN', colClassName: 'signals-col signals-col--compact'},
  {key: 'side', label: 'SIDE', colClassName: 'signals-col signals-col--compact'},
  {key: 'price', label: 'PRICE', colClassName: 'signals-col signals-col--compact', cellClassName: 'signals-cell--numeric'},
  {key: 'shares', label: 'SHARES', colClassName: 'signals-col signals-col--compact', cellClassName: 'signals-cell--numeric'},
  {key: 'total', label: 'TOTAL', colClassName: 'signals-col signals-col--compact', cellClassName: 'signals-cell--numeric'},
  {key: 'decision', label: 'DEC', colClassName: 'signals-col signals-col--compact'},
  {key: 'confidence', label: 'CONF', colClassName: 'signals-col signals-col--compact', cellClassName: 'signals-cell--numeric'},
  {key: 'reason', label: 'REASON', colClassName: 'signals-col signals-col--reason', cellClassName: 'signals-cell--reason'}
]

function buildSignalsRow(
  event: LiveEvent,
  displayId: number | undefined,
  incomingActionByTradeId: Map<string, string>
): SignalsDisplayRow {
  const username = event.username || shortAddress(event.trader || '-')
  const inheritedAction = incomingActionByTradeId.get(event.trade_id)
  const action = resolveActionText({...event, action: event.action ?? inheritedAction})
  const shares = event.shares ?? (event.price > 0 ? event.size_usd / event.price : 0)
  const totalUsd = event.amount_usd ?? event.size_usd
  const confidence = event.confidence ?? null
  const reason = normalizeReasonText(event.reason || '-')

  return {
    key: `${event.trade_id}-${event.ts}`,
    marketUrl: event.market_url,
    cells: {
      id: formatDisplayId(displayId),
      time: formatClock(event.ts),
      username,
      market: event.question || '-',
      action,
      side: String(event.side || '').toUpperCase(),
      price: formatFixedNumber(event.price),
      shares: formatFixedNumber(shares),
      total: formatFixedDollar(totalUsd),
      decision: String(event.decision || '-').toUpperCase(),
      confidence: confidence != null ? formatFixedPercent(confidence, 1) : '-',
      reason
    },
    colors: {
      action: outcomeColor(action),
      side: outcomeColor(event.side),
      price: probabilityColor(event.price),
      decision: decisionColor(event.decision),
      confidence: confidence != null ? probabilityColor(confidence) : feedTheme.white,
      reason: feedTheme.dim
    },
    titles: {
      market: event.question || '-',
      reason
    }
  }
}

function renderEmptyState(loading: boolean, error: string): string {
  if (loading) {
    return 'LOADING SCORED SIGNALS...'
  }
  if (error) {
    return error
  }
  return 'WAITING FOR SCORED SIGNALS...'
}

export function SignalsFeed({mode, mockEvents}: SignalsFeedProps) {
  const {events, error, loading} = useEventFeed(mode, mockEvents)
  const allSignals = useMemo(
    () => events.filter((event) => event.type === 'signal').reverse(),
    [events]
  )
  const incomingActionByTradeId = useMemo(() => {
    const lookup = new Map<string, string>()
    for (const event of events) {
      if (event.type === 'incoming' && event.action?.trim()) {
        lookup.set(event.trade_id, event.action.trim())
      }
    }
    return lookup
  }, [events])
  const tradeIdLookup = useMemo(() => buildTradeIdLookup(events), [events])
  const rows = useMemo(
    () =>
      allSignals.map((event) =>
        buildSignalsRow(event, tradeIdLookup.get(event.trade_id), incomingActionByTradeId)
      ),
    [allSignals, incomingActionByTradeId, tradeIdLookup]
  )
  const sourceLabel = mode === 'mock' ? 'MOCK FEED' : 'LIVE FEED'

  return (
    <section className="signals-page">
      <header className="signals-page__header">
        <div className="signals-page__title">SCORED SIGNALS</div>
        <div className="signals-page__meta">
          {sourceLabel} • SHOWING {rows.length} OF {allSignals.length} SIGNALS
        </div>
      </header>

      <div className="signals-page__viewport">
        <table className="signals-table">
          <colgroup>
            {SIGNALS_COLUMNS.map((column) => (
              <col key={column.key} className={column.colClassName} />
            ))}
          </colgroup>
          <thead>
            <tr className="signals-row signals-row--header">
              {SIGNALS_COLUMNS.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={joinClasses('signals-head', column.cellClassName)}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((row) => (
                <tr key={row.key} className="signals-row">
                  {SIGNALS_COLUMNS.map((column) => (
                    <td
                      key={column.key}
                      className={joinClasses('signals-cell', column.cellClassName)}
                      style={row.colors[column.key] ? {color: row.colors[column.key]} : undefined}
                      title={row.titles[column.key]}
                    >
                      {column.render ? column.render(row) : row.cells[column.key]}
                    </td>
                  ))}
                </tr>
              ))
            ) : (
              <tr className="signals-row">
                <td className="signals-empty" colSpan={SIGNALS_COLUMNS.length}>
                  {renderEmptyState(loading, error)}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {rows.length && error ? <div className="signals-page__status">{error}</div> : null}
    </section>
  )
}
