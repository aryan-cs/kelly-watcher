import {useMemo, type ReactNode} from 'react'
import {type LiveEvent} from './api'
import {useResizableColumns} from './columnResize'
import {
  buildTradeIdLookup,
  formatClock,
  formatDisplayId,
  formatFixedDollar,
  formatFixedNumber,
  joinClasses,
  outcomeColor,
  probabilityColor,
  resolveActionText,
  shortAddress,
  useEventFeed,
  feedTheme
} from './feedUtils'
import {moneyMetricColor} from './uiFormat'

interface TrackerFeedProps {
  mode: 'mock' | 'api'
  mockEvents: LiveEvent[]
  bankrollUsd?: number
}

type TrackerColumnKey =
  | 'id'
  | 'time'
  | 'username'
  | 'market'
  | 'action'
  | 'side'
  | 'price'
  | 'shares'
  | 'paid'
  | 'toWin'
  | 'profit'

interface TrackerDisplayRow {
  key: string
  marketUrl?: string
  cells: Record<TrackerColumnKey, string>
  colors: Partial<Record<TrackerColumnKey, string>>
  titles: Partial<Record<TrackerColumnKey, string>>
}

interface TrackerColumn {
  key: TrackerColumnKey
  label: string
  colClassName: string
  cellClassName?: string
  resizable?: boolean
  render?: (row: TrackerDisplayRow) => ReactNode
}

const TRACKER_COLUMNS: TrackerColumn[] = [
  {key: 'id', label: 'ID', colClassName: 'tracker-col tracker-col--compact', cellClassName: 'tracker-cell--numeric tracker-cell--muted'},
  {key: 'time', label: 'TIME', colClassName: 'tracker-col tracker-col--compact'},
  {key: 'username', label: 'USERNAME', colClassName: 'tracker-col tracker-col--compact'},
  {
    key: 'market',
    label: 'MARKET',
    colClassName: 'tracker-col tracker-col--market',
    cellClassName: 'tracker-cell--market',
    render: (row) =>
      row.marketUrl ? (
        <a className="tracker-link" href={row.marketUrl} rel="noreferrer" target="_blank" title={row.titles.market}>
          {row.cells.market}
        </a>
      ) : (
        row.cells.market
      )
  },
  {key: 'action', label: 'ACTN', colClassName: 'tracker-col tracker-col--compact'},
  {key: 'side', label: 'SIDE', colClassName: 'tracker-col tracker-col--compact'},
  {key: 'price', label: 'PRICE', colClassName: 'tracker-col tracker-col--compact', cellClassName: 'tracker-cell--numeric'},
  {key: 'shares', label: 'SHARES', colClassName: 'tracker-col tracker-col--compact', cellClassName: 'tracker-cell--numeric'},
  {key: 'paid', label: 'PAID', colClassName: 'tracker-col tracker-col--compact', cellClassName: 'tracker-cell--numeric'},
  {key: 'toWin', label: 'TO WIN', colClassName: 'tracker-col tracker-col--compact', cellClassName: 'tracker-cell--numeric'},
  {key: 'profit', label: 'PROFIT', colClassName: 'tracker-col tracker-col--compact', cellClassName: 'tracker-cell--numeric'}
]

function buildTrackerRow(event: LiveEvent, displayId?: number, bankrollUsd?: number): TrackerDisplayRow {
  const username = event.username || shortAddress(event.trader || '-')
  const action = resolveActionText(event)
  const effectiveShares = event.shares ?? event.size_usd
  const paidUsd = event.amount_usd ?? event.size_usd * event.price
  const shares = effectiveShares ?? (event.price > 0 ? paidUsd / event.price : 0)
  const isBuyLike = !event.action || String(event.action).toLowerCase() === 'buy'
  const toWinUsd = isBuyLike && shares > 0 ? shares : null
  const profitUsd = toWinUsd != null ? toWinUsd - paidUsd : null

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
      paid: formatFixedDollar(paidUsd),
      toWin: toWinUsd != null ? formatFixedDollar(toWinUsd) : '-',
      profit: profitUsd != null ? formatFixedDollar(profitUsd) : '-'
    },
    colors: {
      action: outcomeColor(action),
      side: outcomeColor(event.side),
      price: probabilityColor(event.price),
      toWin: toWinUsd != null ? moneyMetricColor(toWinUsd, bankrollUsd, paidUsd) : feedTheme.dim,
      profit: profitUsd != null ? moneyMetricColor(profitUsd, bankrollUsd, paidUsd) : feedTheme.dim
    },
    titles: {
      market: event.question || '-'
    }
  }
}

function renderEmptyState(loading: boolean, error: string): string {
  if (loading) {
    return 'LOADING INCOMING TRADE EVENTS...'
  }
  if (error) {
    return error
  }
  return 'WAITING FOR INCOMING TRADE EVENTS...'
}

export function TrackerFeed({mode, mockEvents, bankrollUsd}: TrackerFeedProps) {
  const {events, error, loading} = useEventFeed(mode, mockEvents)
  const {widths, tableWidth, startResize, fitColumnsToViewport} = useResizableColumns('tracker-feed', TRACKER_COLUMNS)
  const allIncoming = useMemo(
    () => events.filter((event) => event.type === 'incoming').reverse(),
    [events]
  )
  const tradeIdLookup = useMemo(() => buildTradeIdLookup(events), [events])
  const rows = useMemo(
    () => allIncoming.map((event) => buildTrackerRow(event, tradeIdLookup.get(event.trade_id), bankrollUsd)),
    [allIncoming, bankrollUsd, tradeIdLookup]
  )
  const sourceLabel = mode === 'mock' ? 'MOCK FEED' : 'LIVE FEED'

  return (
    <section className="tracker-page">
      <header className="tracker-page__header">
        <div className="tracker-page__title">INCOMING TRADES</div>
        <div className="tracker-page__meta">
          {sourceLabel} • SHOWING {rows.length} OF {allIncoming.length} EVENTS
        </div>
      </header>

      <div className="tracker-page__viewport">
        <table
          className="tracker-table"
          data-resizable-table-id="tracker-feed"
          style={tableWidth ? {width: `${tableWidth}px`} : undefined}
        >
          <colgroup>
            {TRACKER_COLUMNS.map((column) => (
              <col
                key={column.key}
                className={column.colClassName}
                style={widths?.[column.key] ? {width: `${widths[column.key]}px`} : undefined}
              />
            ))}
          </colgroup>
          <thead>
            <tr className="tracker-row tracker-row--header">
              {TRACKER_COLUMNS.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  data-column-key={column.key}
                  className={joinClasses('tracker-head', column.cellClassName)}
                  onClick={() => fitColumnsToViewport()}
                >
                  <div className="resize-head">
                    <span className="resize-head__label">{column.label}</span>
                    {column.resizable === false ? null : (
                      <button
                        type="button"
                        className="resize-head__handle"
                        aria-label={`Resize ${column.label} column`}
                        onPointerDown={(event) => startResize(column, event)}
                        onClick={(event) => event.stopPropagation()}
                      />
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((row) => (
                <tr key={row.key} className="tracker-row">
                  {TRACKER_COLUMNS.map((column) => (
                    <td
                      key={column.key}
                      className={joinClasses('tracker-cell', column.cellClassName)}
                      style={row.colors[column.key] ? {color: row.colors[column.key]} : undefined}
                      title={row.titles[column.key]}
                    >
                      <div className="tracker-cell__content">
                        {column.render ? column.render(row) : row.cells[column.key]}
                      </div>
                    </td>
                  ))}
                </tr>
              ))
            ) : (
              <tr className="tracker-row">
                <td className="tracker-empty" colSpan={TRACKER_COLUMNS.length}>
                  {renderEmptyState(loading, error)}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {rows.length && error ? <div className="tracker-page__status">{error}</div> : null}
    </section>
  )
}
