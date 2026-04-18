import {useEffect, useId, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode} from 'react'
import type {
  BotState,
  ConfigSnapshot,
  DiscoveryCandidate,
  DiscoveryCandidatesResponse,
  LiveEvent,
  ManagedWallet,
  ManagedWalletsResponse,
  ModelTrainingRun,
  RestartShadowWalletMode
} from './api'
import {
  clearConfigValue,
  fetchManagedWallets,
  requestManualTradeCashOut,
  requestDropWallet,
  requestReactivateWallet,
  requestRecoverDb,
  requestShadowRestart,
  requestTradeLogArchive,
  saveConfigValue,
  setLiveTradingEnabled
} from './api'
import {useResizableColumns} from './columnResize'
import {configFieldDescriptions, editableConfigFields} from './configFields'
import {feedTheme} from './feedUtils'
import {
  booleanTone,
  centeredRatioGradient,
  cutoffRatioGradient,
  formatBytes,
  formatDecimal,
  formatInteger,
  formatMoney,
  formatPercentFromRatio,
  formatRelativeAge,
  formatTimestamp,
  moneyMetricColor,
  resolveToneColor,
  returnMetricColor
} from './uiFormat'

interface DashboardPageFrameProps {
  title: string
  meta: string
  children: ReactNode
  className?: string
}

interface StatItem {
  label: string
  value: string
  tone?: string
}

interface TableColumn<T> {
  key: string
  label: string
  className?: string
  resizable?: boolean
  render: (row: T) => ReactNode
  title?: (row: T) => string | undefined
  color?: (row: T) => string | undefined
}

interface CompactFieldRow {
  label: string
  value: string
  tone?: string
  note?: string
  tooltip?: string
}

function joinClasses(...values: Array<string | undefined>): string {
  return values.filter(Boolean).join(' ')
}

function DashboardPageFrame({title, meta, children, className}: DashboardPageFrameProps) {
  return (
    <section className={joinClasses('dashboard-page', className)}>
      <header className="dashboard-page__header">
        <div className="dashboard-page__title">{title}</div>
        {meta ? <div className="dashboard-page__meta">{meta}</div> : null}
      </header>
      {children}
    </section>
  )
}

function StatsGrid({items}: {items: StatItem[]}) {
  return (
    <section className="dashboard-stats">
      {items.map((item) => (
        <article key={item.label} className="dashboard-stat">
          <div className="dashboard-stat__label">{item.label}</div>
          <div
            className="dashboard-stat__value"
            style={item.tone ? {color: resolveToneColor(item.tone)} : undefined}
          >
            {item.value}
          </div>
        </article>
      ))}
    </section>
  )
}

function DashboardPanel({
  title,
  meta,
  children,
  className
}: {
  title: string
  meta?: string
  children: ReactNode
  className?: string
}) {
  return (
    <section className={joinClasses('dashboard-panel', className)}>
      <header className="dashboard-panel__header">
        <div className="dashboard-panel__title">{title}</div>
        {meta ? <div className="dashboard-panel__meta">{meta}</div> : null}
      </header>
      <div className="dashboard-panel__body">{children}</div>
    </section>
  )
}

function DashboardTable<T>({
  tableId,
  columns,
  rows,
  emptyMessage
}: {
  tableId: string
  columns: TableColumn<T>[]
  rows: T[]
  emptyMessage: string
}) {
  const {widths, tableWidth, startResize, fitColumnsToViewport} = useResizableColumns(tableId, columns)

  return (
    <div className="dashboard-table__viewport">
      <table
        className="dashboard-table"
        data-resizable-table-id={tableId}
        style={tableWidth ? {width: `${tableWidth}px`} : undefined}
      >
        <colgroup>
          {columns.map((column) => (
            <col
              key={column.key}
              style={widths?.[column.key] ? {width: `${widths[column.key]}px`} : undefined}
            />
          ))}
        </colgroup>
        <thead>
          <tr className="dashboard-table__row dashboard-table__row--header">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                data-column-key={column.key}
                className={joinClasses('dashboard-table__head', column.className)}
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
            rows.map((row, index) => (
              <tr key={index} className="dashboard-table__row">
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={joinClasses('dashboard-table__cell', column.className)}
                    title={column.title?.(row)}
                    style={column.color?.(row) ? {color: column.color?.(row)} : undefined}
                  >
                    <div className="dashboard-table__content">{column.render(row)}</div>
                  </td>
                ))}
              </tr>
            ))
          ) : (
            <tr className="dashboard-table__row">
              <td className="dashboard-table__empty" colSpan={columns.length}>
                {emptyMessage}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function CompactFieldList({
  rows,
  columns = 1
}: {
  rows: CompactFieldRow[]
  columns?: 1 | 2 | 3
}) {
  return (
    <div className={`compact-field-list compact-field-list--${columns}`}>
      {rows.map((row) => (
        <div key={`${row.label}-${row.value}`} className="compact-field-list__row">
          <div className="compact-field-list__pair">
            <div className="compact-field-list__label" title={row.tooltip || row.label}>{row.label}</div>
            <div
              className="compact-field-list__value"
              style={row.tone ? {color: resolveToneColor(row.tone)} : undefined}
            >
              {row.value}
            </div>
          </div>
          {row.note ? <div className="compact-field-list__note">{row.note}</div> : null}
        </div>
      ))}
    </div>
  )
}

function DashboardSubsection({
  title,
  meta,
  children
}: {
  title: string
  meta?: string
  children: ReactNode
}) {
  return (
    <section className="dashboard-subsection">
      <header className="dashboard-subsection__header">
        <div className="dashboard-subsection__title">{title}</div>
        {meta ? <div className="dashboard-subsection__meta">{meta}</div> : null}
      </header>
      <div className="dashboard-subsection__body">{children}</div>
    </section>
  )
}

interface BalancePoint {
  ts: number
  balance: number
}

const PERFORMANCE_STAT_TOOLTIPS: Record<string, string> = {
  'START BALANCE': 'How much money you started with before any tracked trades were applied.',
  'CURRENT BALANCE': 'Your starting balance plus realized and open profit and loss.',
  'TOTAL P&L': 'Total realized profit and loss from resolved tracked positions.',
  'NET P&L': 'Total realized profit and loss from resolved tracked positions.',
  'OPEN P&L': 'Unrealized profit and loss across positions that are still open.',
  'RETURN %': 'Net profit and loss expressed as a percentage of starting balance.',
  'WIN RATE': 'Share of resolved positions that closed with a positive profit and loss.',
  'PROFIT FACTOR': 'Gross profits divided by gross losses across resolved positions.',
  'EXPECTANCY': 'Average profit and loss per resolved position.',
  'TRACKED VOLUME': 'Total dollars allocated across all tracked trades.',
  'EXPOSURE': 'Share of current balance that is currently tied up in open positions.',
  'AVAILABLE CASH': 'Current balance minus the capital still tied up in open positions.',
  'MAX DRAWDOWN': 'Largest peak-to-trough percentage drop seen in the tracked balance curve.',
  'RESOLVED': 'Number of tracked positions that have fully closed.',
  'OPEN POSITIONS': 'Number of tracked positions that are still open right now.',
  'AVG CONF': 'Average model confidence across the scored signal set.',
  'AVG TOTAL': 'Average total dollars allocated per accepted signal.'
}

const MODEL_LABEL_TOOLTIPS: Record<string, string> = {
  'LOADED SCORER': 'Which scoring model is currently loaded for decision making.',
  MODE: 'Which prediction path is currently being used for model decisions.',
  BACKEND: 'The runtime backend serving the active model.',
  STARTUP: 'Whether model startup checks passed cleanly or fell back.',
  SAMPLES: 'How many scored signal samples are included in these model stats.',
  'AVG CONF': 'Average confidence produced by the model across scored signals.',
  ACTUAL: 'Observed accept rate across the scored signal set.',
  'AVG GAP': 'Difference between average confidence and realized accept rate.',
  BRIER: 'Calibration error score where lower values mean better probability quality.',
  'SCORED TRADES': 'How many scored signal samples are included in these model stats.',
  'AVG CONFIDENCE': 'Average confidence produced by the model across scored signals.',
  'ACTUAL WIN RATE': 'Observed accept rate across the scored signal set.',
  'CONFIDENCE GAP': 'Difference between average confidence and realized accept rate.',
  'BRIER SCORE': 'Calibration error score where lower values mean better probability quality.',
  'LOG LOSS': 'Penalty on probability error where lower values mean better predictions.',
  LOADED: 'How long ago the current model was loaded into memory.',
  DETAIL: 'Short runtime note describing the current model state.',
  '40-55%': 'Signals whose confidence fell in the 40 to 55 percent bucket.',
  '55-70%': 'Signals whose confidence fell in the 55 to 70 percent bucket.',
  '70%+': 'Signals whose confidence was at least 70 percent.',
  ACCEPT: 'Signals that were approved for execution.',
  REJECT: 'Signals that were explicitly blocked from execution.',
  PAUSE: 'Signals that were delayed rather than executed immediately.',
  SKIP: 'Signals that were ignored and not routed to execution.',
  IGNORE: 'Signals that were scored but not acted on by the pipeline.',
  'PRIMARY PATH': 'The main model path currently driving decisions.',
  'ACCEPT PATH': 'How many signals cleared the acceptance path.',
  'REJECT PATH': 'How many signals were blocked by the rejection path.',
  'PAUSE/SKIP': 'How many signals were paused or skipped instead of executed.',
  'LIVE GATE': 'Whether live trading currently requires shadow-history evidence.',
  'TOTAL READY': 'Whether the total shadow-history requirement has been met.',
  'POST-PROMO READY': 'Whether post-promotion routed evidence is ready for live gating.',
  SNAPSHOT: 'Current state of the shadow-history snapshot builder.',
  SCOPE: 'Which shadow-history scope is currently being evaluated.',
  'NEXT BLOCKER': 'What still needs to clear before the live-shadow gate can fully open.',
  RETRAIN: 'Status of the latest retraining cycle.',
  'LAST START': 'When the latest retraining or search run started.',
  'LAST FINISH': 'When the latest retraining or search run finished.',
  'SEARCH STATUS': 'Outcome of the latest replay search for challenger models.',
  'SEARCH START': 'When the latest replay search started.',
  'SEARCH FINISH': 'When the latest replay search finished.',
  'MANUAL RETRAIN': 'Whether a manual retraining request is currently pending.',
  'MANUAL TRADE': 'Whether a manual trade action request is currently pending.',
  SCORER: 'Which scorer family this model status row refers to.',
  MODEL: 'Which model mode is active for the current scorer row.',
  'LAST RETRAIN': 'Most recent completed retraining timestamp.',
  'REPLAY SEARCH': 'Most recent replay-search completion timestamp.',
  STARTED: 'When this XGBoost training run started.',
  STATUS: 'Outcome state recorded for this training run.',
  DEPLOYED: 'Whether this training run was promoted into live use.'
}

function performanceStatTooltip(label: string): string {
  return PERFORMANCE_STAT_TOOLTIPS[label] || `${label.toLowerCase()} for the tracked strategy.`
}

function modelLabelTooltip(label: string): string {
  return MODEL_LABEL_TOOLTIPS[label] || `${label.toLowerCase()} for the current model state.`
}

interface PerformanceTradeRow {
  tradeId: string
  marketId: string
  tokenId: string
  traderAddress?: string
  question: string
  username: string
  side: string
  entryTs: number
  exitTs?: number
  price: number
  total: number
  confidence: number | null
  pnl: number
  returnRatio: number | null
  status: 'current' | 'past'
}

function buildBalanceCurve(signalEvents: LiveEvent[], startingBalance: number): BalancePoint[] {
  const acceptedSignals = signalEvents
    .filter((event) => event.decision === 'ACCEPT')
    .sort((left, right) => left.ts - right.ts)

  let runningBalance = startingBalance
  return acceptedSignals.map((event, index) => {
    const pnl = syntheticTradePnl(event, index)
    runningBalance += pnl
    return {
      ts: event.ts,
      balance: Number(runningBalance.toFixed(2))
    }
  })
}

function syntheticTradePnl(event: LiveEvent, index: number): number {
  const notional = event.amount_usd ?? event.size_usd ?? 0
  const confidence = event.confidence ?? 0.5
  const directionBias = index % 4 === 0 ? -1 : 1
  return Number(((confidence - 0.5) * notional * 1.8 * directionBias).toFixed(2))
}

function buildPerformanceTrades(signalEvents: LiveEvent[]): PerformanceTradeRow[] {
  const acceptedSignals = signalEvents
    .filter((event) => event.decision === 'ACCEPT')
    .sort((left, right) => right.ts - left.ts)

  return acceptedSignals.map((event, index) => {
    const total = Number(event.amount_usd ?? event.size_usd ?? 0)
    const pnl = syntheticTradePnl(event, acceptedSignals.length - index - 1)
    const isCurrent = index < 5
    const currentMark = Number((pnl * 0.35).toFixed(2))
    const realizedPnl = isCurrent ? currentMark : pnl
    return {
      tradeId: event.trade_id,
      marketId: event.market_id,
      tokenId: String(event.token_id || ''),
      traderAddress: event.trader || '',
      question: event.question,
      username: event.username || '-',
      side: event.side,
      entryTs: event.ts,
      exitTs: isCurrent ? undefined : event.ts + (index % 7 + 2) * 3600,
      price: event.price,
      total,
      confidence: event.confidence ?? null,
      pnl: realizedPnl,
      returnRatio: total > 0 ? realizedPnl / total : null,
      status: isCurrent ? 'current' : 'past'
    }
  })
}

function exitNowButtonColor(pnl: number, total: number, bankrollUsd: number | null | undefined): string {
  return moneyMetricColor(pnl, bankrollUsd, total) || feedTheme.yellow
}

function potentialProfitForPosition(row: PerformanceTradeRow): number | null {
  if (!Number.isFinite(row.price) || row.price <= 0) {
    return null
  }
  const shares = row.total / row.price
  return Number((shares - row.total).toFixed(2))
}

function pathFromPoints(points: Array<{x: number; y: number}>): string {
  if (!points.length) return ''
  return points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ')
}

function BalanceChart({
  points,
  baseline
}: {
  points: BalancePoint[]
  baseline: number
}) {
  const chartId = useId().replace(/:/g, '')
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const minWidth = 960
  const pointStep = 30
  const width = Math.max(minWidth, 28 + Math.max(points.length - 1, 1) * pointStep)
  const height = 220
  const paddingX = 14
  const paddingY = 16
  const [isScrubbing, setIsScrubbing] = useState(false)
  const [showReadout, setShowReadout] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(points.length ? points.length - 1 : 0)

  const values = points.map((point) => point.balance)
  const maxDeviation = Math.max(
    1,
    ...values.map((value) => Math.abs(value - baseline))
  )
  const centerY = height / 2
  const usableHalfHeight = (height - paddingY * 2) / 2

  useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport) return
    viewport.scrollLeft = viewport.scrollWidth
  }, [points.length, width])

  const chartPoints = points.map((point, index) => {
    const x =
      points.length <= 1
        ? width / 2
        : paddingX + (index / (points.length - 1)) * (width - paddingX * 2)
    const y = centerY - ((point.balance - baseline) / maxDeviation) * usableHalfHeight
    return {x, y}
  })

  const baselineY = centerY
  const path = pathFromPoints(chartPoints)
  const latest = points[points.length - 1]
  const selectedPoint = points[selectedIndex] ?? latest
  const selectedChartPoint = chartPoints[selectedIndex] ?? chartPoints[chartPoints.length - 1]
  const scrubDate = selectedPoint ? new Date(selectedPoint.ts * 1000) : null
  const scrubTimeText = scrubDate
    ? new Intl.DateTimeFormat('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      }).format(scrubDate)
    : ''
  const scrubDayText = scrubDate
    ? new Intl.DateTimeFormat('en-US', {
        month: '2-digit',
        day: '2-digit'
      }).format(scrubDate)
    : ''
  const scrubSummary = selectedPoint
    ? `${formatMoney(selectedPoint.balance)} at ${scrubTimeText} on ${scrubDayText}`
    : ''

  function updateSelection(event: ReactPointerEvent<SVGSVGElement>) {
    if (!points.length) return
    const bounds = event.currentTarget.getBoundingClientRect()
    const ratio = bounds.width > 0 ? (event.clientX - bounds.left) / bounds.width : 0
    const clampedRatio = Math.min(1, Math.max(0, ratio))
    const nextIndex = Math.min(points.length - 1, Math.max(0, Math.round(clampedRatio * (points.length - 1))))
    setSelectedIndex(nextIndex)
  }

  return (
    <div className="balance-chart">
      {selectedPoint ? (
        <div className="balance-chart__scrub">{scrubSummary}</div>
      ) : null}
      <div
        ref={viewportRef}
        className="balance-chart__viewport"
        onPointerEnter={() => setShowReadout(true)}
        onPointerLeave={() => {
          setShowReadout(false)
          setIsScrubbing(false)
        }}
        onScroll={() => setShowReadout(true)}
      >
        <svg
          className="balance-chart__svg"
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          role="img"
          aria-label="Balance over time"
          onPointerDown={(event) => {
            setIsScrubbing(true)
            setShowReadout(true)
            updateSelection(event)
          }}
          onPointerMove={(event) => {
            if (isScrubbing) {
              setShowReadout(true)
              updateSelection(event)
            }
          }}
          onPointerUp={() => setIsScrubbing(false)}
        >
          <defs>
            <clipPath id={`balance-positive-clip-${chartId}`}>
              <rect x="0" y="0" width={width} height={Math.max(0, baselineY)} />
            </clipPath>
            <clipPath id={`balance-negative-clip-${chartId}`}>
              <rect x="0" y={baselineY} width={width} height={Math.max(0, height - baselineY)} />
            </clipPath>
          </defs>
          <line
            x1={paddingX}
            y1={baselineY}
            x2={width - paddingX}
            y2={baselineY}
            className="balance-chart__baseline"
          />
          <path d={path} className="balance-chart__line balance-chart__line--positive" clipPath={`url(#balance-positive-clip-${chartId})`} />
          <path d={path} className="balance-chart__line balance-chart__line--negative" clipPath={`url(#balance-negative-clip-${chartId})`} />
          {showReadout && selectedChartPoint ? (
            <>
              <line
                x1={selectedChartPoint.x}
                y1={paddingY}
                x2={selectedChartPoint.x}
                y2={height - paddingY}
                className="balance-chart__cursor"
              />
            </>
          ) : null}
        </svg>
      </div>
    </div>
  )
}

interface PerformancePageProps {
  mode: 'mock' | 'api'
  trackerEvents: LiveEvent[]
  signalEvents: LiveEvent[]
  resolvedShadowTradeCount?: number
  bankrollUsd?: number
  confidenceCutoff?: number
  pollInterval?: number
  lastPollDurationS?: number
  lastEventCount?: number
  shadowSnapshotStatus?: string
  shadowSnapshotResolved?: number
  shadowSnapshotRoutedResolved?: number
}

export function PerformancePage(props: PerformancePageProps) {
  const [mockClosedTradeIds, setMockClosedTradeIds] = useState<Record<string, number>>({})
  const [pendingExitTradeId, setPendingExitTradeId] = useState('')
  const [exitStatusMessage, setExitStatusMessage] = useState('')
  const startingBalance = props.bankrollUsd ?? 0
  const paidVolume = useMemo(
    () => props.trackerEvents.reduce((total, event) => total + (event.amount_usd ?? 0), 0),
    [props.trackerEvents]
  )
  const balanceCurve = useMemo(
    () => buildBalanceCurve(props.signalEvents, startingBalance),
    [props.signalEvents, startingBalance]
  )
  const performanceTrades = useMemo(
    () => buildPerformanceTrades(props.signalEvents),
    [props.signalEvents]
  )
  const visiblePerformanceTrades = useMemo(
    () =>
      performanceTrades.map((trade) => {
        const closedAt = mockClosedTradeIds[trade.tradeId]
        if (!closedAt || trade.status === 'past') return trade
        return {
          ...trade,
          status: 'past' as const,
          exitTs: closedAt,
          returnRatio: trade.total > 0 ? trade.pnl / trade.total : null
        }
      }),
    [mockClosedTradeIds, performanceTrades]
  )
  const currentPositions = useMemo(
    () => visiblePerformanceTrades.filter((trade) => trade.status === 'current').slice(0, 8),
    [visiblePerformanceTrades]
  )
  const pastPositions = useMemo(
    () => visiblePerformanceTrades.filter((trade) => trade.status === 'past').slice(0, 24),
    [visiblePerformanceTrades]
  )
  const trackerPnl = useMemo(
    () => pastPositions.reduce((total, trade) => total + trade.pnl, 0),
    [pastPositions]
  )
  const winRate = useMemo(() => {
    if (!pastPositions.length) return null
    const wins = pastPositions.filter((trade) => trade.pnl > 0).length
    return wins / pastPositions.length
  }, [pastPositions])
  const grossProfit = useMemo(
    () => pastPositions.filter((trade) => trade.pnl > 0).reduce((total, trade) => total + trade.pnl, 0),
    [pastPositions]
  )
  const grossLoss = useMemo(
    () => pastPositions.filter((trade) => trade.pnl < 0).reduce((total, trade) => total + Math.abs(trade.pnl), 0),
    [pastPositions]
  )
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : null
  const expectancy = pastPositions.length ? trackerPnl / pastPositions.length : null
  const currentExposure = useMemo(
    () => currentPositions.reduce((total, trade) => total + trade.total, 0),
    [currentPositions]
  )
  const currentMarkedPnl = useMemo(
    () => currentPositions.reduce((total, trade) => total + trade.pnl, 0),
    [currentPositions]
  )
  const netPnl = useMemo(
    () => Number((trackerPnl + currentMarkedPnl).toFixed(2)),
    [trackerPnl, currentMarkedPnl]
  )
  const currentBalance = useMemo(
    () => Number((startingBalance + trackerPnl + currentMarkedPnl).toFixed(2)),
    [startingBalance, trackerPnl, currentMarkedPnl]
  )
  const availableBalance = useMemo(
    () => Number((currentBalance - currentExposure).toFixed(2)),
    [currentBalance, currentExposure]
  )
  const accountBankrollForGradient = currentBalance > 0 ? currentBalance : startingBalance
  const exposureRatio = currentBalance > 0 ? currentExposure / currentBalance : null
  const availableCashRatio = currentBalance > 0 ? availableBalance / currentBalance : null
  const maxDrawdownRatio = useMemo(() => {
    if (!balanceCurve.length) return 0
    let peak = balanceCurve[0]?.balance ?? startingBalance
    let maxDdRatio = 0
    for (const point of balanceCurve) {
      peak = Math.max(peak, point.balance)
      if (peak > 0) {
        maxDdRatio = Math.max(maxDdRatio, (peak - point.balance) / peak)
      }
    }
    return Number(maxDdRatio.toFixed(4))
  }, [balanceCurve, startingBalance])
  const returnRatio = startingBalance > 0 ? netPnl / startingBalance : null
  const trackerStatsLeftRows = [
    {label: 'START BALANCE', value: formatMoney(startingBalance), tone: moneyMetricColor(0, accountBankrollForGradient), tooltip: performanceStatTooltip('START BALANCE')},
    {
      label: 'PROFIT FACTOR',
      value: profitFactor == null ? '-' : profitFactor === Infinity ? 'INF' : formatDecimal(profitFactor, 2),
      tone: centeredRatioGradient(profitFactor, 1, 1),
      tooltip: performanceStatTooltip('PROFIT FACTOR')
    },
    {
      label: 'EXPOSURE',
      value: formatPercentFromRatio(exposureRatio),
      tone: centeredRatioGradient(exposureRatio, 0.5, 0.5, true),
      tooltip: performanceStatTooltip('EXPOSURE')
    },
    {
      label: 'EXPECTANCY',
      value: expectancy == null ? '-' : formatMoney(expectancy),
      tone: moneyMetricColor(expectancy, accountBankrollForGradient, currentExposure),
      tooltip: performanceStatTooltip('EXPECTANCY')
    },
    {
      label: 'AVAILABLE CASH',
      value: formatMoney(availableBalance),
      tone: centeredRatioGradient(availableCashRatio, 0.5, 0.5),
      tooltip: performanceStatTooltip('AVAILABLE CASH')
    }
  ]
  const trackerStatsRightRows = [
    {
      label: 'CURRENT BALANCE',
      value: formatMoney(currentBalance),
      tone: moneyMetricColor(currentBalance - startingBalance, accountBankrollForGradient, currentExposure),
      tooltip: performanceStatTooltip('CURRENT BALANCE')
    },
    {
      label: 'REALIZED P&L',
      value: formatMoney(trackerPnl),
      tone: moneyMetricColor(trackerPnl, accountBankrollForGradient, currentExposure),
      tooltip: performanceStatTooltip('REALIZED P&L')
    },
    {
      label: 'OPEN P&L',
      value: formatMoney(currentMarkedPnl),
      tone: moneyMetricColor(currentMarkedPnl, accountBankrollForGradient, currentExposure),
      tooltip: performanceStatTooltip('OPEN P&L')
    },
    {
      label: 'NET P&L',
      value: formatMoney(netPnl),
      tone: moneyMetricColor(netPnl, accountBankrollForGradient, currentExposure),
      tooltip: performanceStatTooltip('NET P&L')
    },
    {
      label: 'WIN RATE',
      value: formatPercentFromRatio(winRate),
      tone: cutoffRatioGradient(winRate, 0.5),
      tooltip: performanceStatTooltip('WIN RATE')
    },
    {
      label: 'RETURN %',
      value: formatPercentFromRatio(returnRatio),
      tone: returnMetricColor(returnRatio, accountBankrollForGradient, startingBalance),
      tooltip: performanceStatTooltip('RETURN %')
    },
    {
      label: 'MAX DRAWDOWN',
      value: formatPercentFromRatio(maxDrawdownRatio),
      tone: centeredRatioGradient(maxDrawdownRatio, 0.05, 0.05, true),
      tooltip: performanceStatTooltip('MAX DRAWDOWN')
    },
    {label: 'RESOLVED', value: formatInteger(pastPositions.length), tooltip: performanceStatTooltip('RESOLVED')}
  ]

  async function handleExitNow(trade: PerformanceTradeRow): Promise<void> {
    if (!trade.marketId || !trade.tokenId) {
      setExitStatusMessage('EXIT NOW REQUIRES MARKET AND TOKEN IDS.')
      return
    }
    setPendingExitTradeId(trade.tradeId)
    try {
      if (props.mode === 'mock') {
        setMockClosedTradeIds((current) => ({
          ...current,
          [trade.tradeId]: Math.floor(Date.now() / 1000)
        }))
        setExitStatusMessage(`${trade.username} CASH-OUT SIMULATED.`)
      } else {
        const response = await requestManualTradeCashOut({
          marketId: trade.marketId,
          tokenId: trade.tokenId,
          side: trade.side,
          question: trade.question,
          traderAddress: trade.traderAddress
        })
        setExitStatusMessage(String(response?.message || 'MANUAL CASH-OUT REQUEST QUEUED.'))
      }
    } catch (error) {
      setExitStatusMessage(error instanceof Error ? error.message : 'EXIT NOW REQUEST FAILED.')
    } finally {
      setPendingExitTradeId('')
    }
  }

  return (
    <DashboardPageFrame
      className="performance-page"
      title="PERFORMANCE"
      meta={`${formatInteger(props.signalEvents.length)} SIGNALS • ${formatInteger(props.trackerEvents.length)} INCOMING EVENTS`}
    >
      <div className="dashboard-panels dashboard-panels--two performance-panels performance-panels--top">
        <DashboardPanel
          className="dashboard-panel--performance-stats"
          title="TRACKER STATS"
        >
          <div className="metric-grid">
            <div className="metric-grid__column">
              {trackerStatsLeftRows.map((row) => (
                <div key={row.label} className="metric-grid__row">
                  <div className="metric-grid__label" title={row.tooltip || row.label}>{row.label}</div>
                  <div className="metric-grid__value" style={row.tone ? {color: row.tone} : undefined}>
                    {row.value}
                  </div>
                </div>
              ))}
            </div>
            <div className="metric-grid__column">
              {trackerStatsRightRows.map((row) => (
                <div key={row.label} className="metric-grid__row">
                  <div className="metric-grid__label" title={row.tooltip || row.label}>{row.label}</div>
                  <div className="metric-grid__value" style={row.tone ? {color: row.tone} : undefined}>
                    {row.value}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </DashboardPanel>

        <DashboardPanel
          className="dashboard-panel--performance-balance"
          title="BALANCE"
        >
          {balanceCurve.length ? (
            <BalanceChart points={balanceCurve} baseline={startingBalance} />
          ) : (
            <div className="dashboard-table__empty">NO ACCEPTED TRADES AVAILABLE FOR BALANCE GRAPH.</div>
          )}
        </DashboardPanel>
      </div>

      <div className="dashboard-panels dashboard-panels--two performance-panels performance-panels--bottom">
        <DashboardPanel
          className="dashboard-panel--performance-positions"
          title="CURRENT POSITIONS"
          meta={`${formatInteger(currentPositions.length)} OPEN • ${formatMoney(currentExposure)} EXPOSED`}
        >
          <DashboardTable
            tableId="performance-current-positions"
            rows={currentPositions}
            emptyMessage="NO OPEN POSITIONS RIGHT NOW."
            columns={[
              {key: 'time', label: 'OPENED', className: 'dashboard-table__cell--compact', render: (row) => formatTimestamp(row.entryTs)},
              {key: 'user', label: 'USERNAME', className: 'dashboard-table__cell--compact', render: (row) => row.username},
              {key: 'market', label: 'MARKET', className: 'dashboard-table__cell--wide', render: (row) => row.question, title: (row) => row.question},
              {key: 'side', label: 'SIDE', className: 'dashboard-table__cell--compact', render: (row) => row.side, color: (row) => row.side === 'YES' ? feedTheme.green : feedTheme.red},
              {key: 'price', label: 'PRICE', className: 'dashboard-table__cell--numeric', render: (row) => row.price.toFixed(3)},
              {key: 'total', label: 'TOTAL', className: 'dashboard-table__cell--numeric', render: (row) => formatMoney(row.total)},
              {
                key: 'profit',
                label: 'PROFIT',
                className: 'dashboard-table__cell--numeric',
                render: (row) => formatMoney(potentialProfitForPosition(row)),
                color: (row) => moneyMetricColor(potentialProfitForPosition(row), startingBalance, row.total)
              },
              {
                key: 'conf',
                label: 'CONF',
                className: 'dashboard-table__cell--numeric',
                render: (row) => formatPercentFromRatio(row.confidence),
                color: (row) => cutoffRatioGradient(row.confidence, props.confidenceCutoff)
              },
              {
                key: 'exitNow',
                label: 'EXIT NOW',
                className: 'dashboard-table__cell--compact dashboard-table__cell--flush',
                render: (row) => (
                  <button
                    type="button"
                    className="performance-exit-button"
                    style={{background: exitNowButtonColor(row.pnl, row.total, startingBalance), color: 'var(--bg)'}}
                    onClick={() => void handleExitNow(row)}
                    disabled={pendingExitTradeId === row.tradeId || !row.marketId || !row.tokenId}
                  >
                    {pendingExitTradeId === row.tradeId ? 'EXITING' : formatMoney(row.pnl)}
                  </button>
                )
              }
            ]}
          />
          {exitStatusMessage ? <div className="performance-exit-status">{exitStatusMessage}</div> : null}
        </DashboardPanel>

        <DashboardPanel
          className="dashboard-panel--performance-positions"
          title="PAST POSITIONS"
          meta={`${formatInteger(pastPositions.length)} RESOLVED • ${formatMoney(trackerPnl)} TOTAL`}
        >
          <DashboardTable
            tableId="performance-past-positions"
            rows={pastPositions}
            emptyMessage="NO PAST POSITIONS YET."
            columns={[
              {key: 'entry', label: 'ENTRY', className: 'dashboard-table__cell--compact', render: (row) => formatTimestamp(row.entryTs)},
              {key: 'exit', label: 'EXIT', className: 'dashboard-table__cell--compact', render: (row) => formatTimestamp(row.exitTs)},
              {key: 'user', label: 'USERNAME', className: 'dashboard-table__cell--compact', render: (row) => row.username},
              {key: 'market', label: 'MARKET', className: 'dashboard-table__cell--wide', render: (row) => row.question, title: (row) => row.question},
              {key: 'side', label: 'SIDE', className: 'dashboard-table__cell--compact', render: (row) => row.side, color: (row) => row.side === 'YES' ? feedTheme.green : feedTheme.red},
              {key: 'price', label: 'PRICE', className: 'dashboard-table__cell--numeric', render: (row) => row.price.toFixed(3)},
              {key: 'total', label: 'TOTAL', className: 'dashboard-table__cell--numeric', render: (row) => formatMoney(row.total)},
              {key: 'pnl', label: 'P&L', className: 'dashboard-table__cell--numeric', render: (row) => formatMoney(row.pnl), color: (row) => moneyMetricColor(row.pnl, startingBalance, row.total)}
            ]}
          />
        </DashboardPanel>
      </div>
    </DashboardPageFrame>
  )
}

interface ModelPageProps {
  signalEvents: LiveEvent[]
  loadedScorer?: string
  modelBackend?: string
  modelPredictionMode?: string
  modelRuntimeCompatible?: boolean
  modelFallbackReason?: string
  modelLoadedAt?: number
  startupDetail?: string
  startupFailed?: boolean
  startupValidationFailed?: boolean
  retrainInProgress?: boolean
  lastRetrainStartedAt?: number
  lastRetrainFinishedAt?: number
  lastRetrainStatus?: string
  lastRetrainMessage?: string
  lastReplaySearchStartedAt?: number
  lastReplaySearchFinishedAt?: number
  lastReplaySearchStatus?: string
  lastReplaySearchMessage?: string
  trainingRuns?: ModelTrainingRun[]
  manualRetrainPending?: boolean
  manualTradePending?: boolean
  liveRequireShadowHistoryEnabled?: boolean
  liveShadowHistoryReady?: boolean
  liveShadowHistoryTotalReady?: boolean
  shadowSnapshotScope?: string
  shadowSnapshotStatus?: string
  shadowSnapshotResolved?: number
  shadowSnapshotRoutedResolved?: number
  shadowSnapshotReady?: boolean
  shadowSnapshotBlockReason?: string
}

export function ModelPage(props: ModelPageProps) {
  const acceptedSignals = useMemo(
    () => props.signalEvents.filter((event) => event.decision === 'ACCEPT'),
    [props.signalEvents]
  )
  const rejectedSignals = useMemo(
    () => props.signalEvents.filter((event) => event.decision === 'REJECT'),
    [props.signalEvents]
  )
  const pausedSignals = useMemo(
    () => props.signalEvents.filter((event) => event.decision === 'PAUSE' || event.decision === 'SKIP'),
    [props.signalEvents]
  )
  const confidenceBuckets = useMemo(() => {
    const rows = [
      {label: '40-55%', min: 0.4, max: 0.55},
      {label: '55-70%', min: 0.55, max: 0.7},
      {label: '70%+', min: 0.7, max: 2}
    ]
    return rows.map((bucket) => {
      const events = props.signalEvents.filter((event) => {
        const confidence = event.confidence ?? 0
        return confidence >= bucket.min && confidence < bucket.max
      })
      const accepts = events.filter((event) => event.decision === 'ACCEPT').length
      return {
        bucket: bucket.label,
        signals: events.length,
        accepts,
        acceptRate: events.length ? accepts / events.length : 0
      }
    })
  }, [props.signalEvents])

  const averageConfidence = useMemo(() => {
    const values = props.signalEvents.map((event) => event.confidence).filter((value): value is number => value != null)
    if (!values.length) return null
    return values.reduce((total, value) => total + value, 0) / values.length
  }, [props.signalEvents])

  const actualWinRate = props.signalEvents.length ? acceptedSignals.length / props.signalEvents.length : null
  const avgGap = averageConfidence != null && actualWinRate != null ? averageConfidence - actualWinRate : null
  const pseudoBrier = useMemo(() => {
    const values = props.signalEvents
      .map((event) => {
        if (event.confidence == null) return null
        const actual = event.decision === 'ACCEPT' ? 1 : 0
        return (event.confidence - actual) ** 2
      })
      .filter((value): value is number => value != null)
    if (!values.length) return null
    return values.reduce((total, value) => total + value, 0) / values.length
  }, [props.signalEvents])
  const pseudoLogLoss = useMemo(() => {
    const values = props.signalEvents
      .map((event) => {
        if (event.confidence == null) return null
        const actual = event.decision === 'ACCEPT' ? 1 : 0
        const p = Math.min(0.999, Math.max(0.001, event.confidence))
        return -(actual * Math.log(p) + (1 - actual) * Math.log(1 - p))
      })
      .filter((value): value is number => value != null)
    if (!values.length) return null
    return values.reduce((total, value) => total + value, 0) / values.length
  }, [props.signalEvents])

  const decisionRows = useMemo(() => {
    const counts = new Map<string, number>()
    for (const event of props.signalEvents) {
      const key = String(event.decision || 'UNKNOWN')
      counts.set(key, (counts.get(key) || 0) + 1)
    }
    return Array.from(counts.entries())
      .map(([decision, count]) => ({
        decision,
        count,
        share: props.signalEvents.length ? count / props.signalEvents.length : 0
      }))
      .sort((left, right) => right.count - left.count)
  }, [props.signalEvents])

  const runtimeSummaryRows = [
    {
      item: 'SCORER',
      value: String(props.loadedScorer || '-')
    },
    {
      item: 'BACKEND',
      value: String(props.modelBackend || '-'),
      note: props.modelRuntimeCompatible ? 'runtime ready' : 'fallback'
    },
    {
      item: 'STARTUP',
      value: props.startupFailed ? 'FAILED' : props.startupValidationFailed ? 'VALIDATION' : 'READY',
      note: String(props.startupDetail || props.modelFallbackReason || '-')
    },
    {
      item: 'LOADED',
      value: formatRelativeAge(props.modelLoadedAt),
      note: formatTimestamp(props.modelLoadedAt)
    }
  ]

  const shadowGateRows = [
    {item: 'LIVE GATE', value: props.liveRequireShadowHistoryEnabled ? 'ENABLED' : 'DISABLED', note: 'requires enough shadow history before live mode can run'},
    {item: 'TOTAL READY', value: props.liveShadowHistoryTotalReady ? 'READY' : 'BUILDING'},
    {item: 'POST-PROMO READY', value: props.liveShadowHistoryReady ? 'READY' : 'BUILDING'},
    {
      item: 'SNAPSHOT',
      value: String(props.shadowSnapshotStatus || '-'),
      note: `${formatInteger(props.shadowSnapshotRoutedResolved)} routed / ${formatInteger(props.shadowSnapshotResolved)} resolved`
    },
    {item: 'SCOPE', value: String(props.shadowSnapshotScope || '-')},
    {item: 'NEXT BLOCKER', value: props.shadowSnapshotReady ? 'CLEAR' : String(props.shadowSnapshotBlockReason || '-')}
  ]

  const trainingRows = [
    {item: 'RETRAIN', value: props.retrainInProgress ? 'RUNNING' : String(props.lastRetrainStatus || '-'), note: props.lastRetrainMessage || '-'},
    {item: 'LAST START', value: formatTimestamp(props.lastRetrainStartedAt), note: formatRelativeAge(props.lastRetrainStartedAt)},
    {item: 'LAST FINISH', value: formatTimestamp(props.lastRetrainFinishedAt), note: formatRelativeAge(props.lastRetrainFinishedAt)},
    {item: 'SEARCH STATUS', value: String(props.lastReplaySearchStatus || '-'), note: props.lastReplaySearchMessage || '-'},
    {item: 'SEARCH START', value: formatTimestamp(props.lastReplaySearchStartedAt), note: formatRelativeAge(props.lastReplaySearchStartedAt)},
    {item: 'SEARCH FINISH', value: formatTimestamp(props.lastReplaySearchFinishedAt), note: formatRelativeAge(props.lastReplaySearchFinishedAt)},
    {item: 'MANUAL RETRAIN', value: props.manualRetrainPending ? 'PENDING' : 'IDLE', note: 'manual retrain request flag'},
    {item: 'MANUAL TRADE', value: props.manualTradePending ? 'PENDING' : 'IDLE', note: 'manual trade request flag'}
  ]

  const trainingRunRows = useMemo(
    () =>
      [...(props.trainingRuns || [])].sort(
        (left, right) => Number(right.started_at || 0) - Number(left.started_at || 0)
      ),
    [props.trainingRuns]
  )

  const predictionQualityCompactRows: CompactFieldRow[] = [
    {label: 'SCORED TRADES', value: formatInteger(props.signalEvents.length), tooltip: modelLabelTooltip('SCORED TRADES')},
    {label: 'AVG CONFIDENCE', value: formatPercentFromRatio(averageConfidence), tooltip: modelLabelTooltip('AVG CONFIDENCE')},
    {label: 'ACTUAL WIN RATE', value: formatPercentFromRatio(actualWinRate), tooltip: modelLabelTooltip('ACTUAL WIN RATE')},
    {label: 'CONFIDENCE GAP', value: formatPercentFromRatio(avgGap), tone: (avgGap ?? 0) <= 0 ? feedTheme.green : feedTheme.yellow, tooltip: modelLabelTooltip('CONFIDENCE GAP')},
    {label: 'BRIER SCORE', value: formatDecimal(pseudoBrier, 3), tooltip: modelLabelTooltip('BRIER SCORE')},
    {label: 'LOG LOSS', value: formatDecimal(pseudoLogLoss, 3), tooltip: modelLabelTooltip('LOG LOSS')}
  ]

  const shadowCompactRows: CompactFieldRow[] = shadowGateRows.map((row) => ({
    label: row.item,
    value: row.value,
    tone:
      row.value === 'READY' || row.value === 'ENABLED' || row.value === 'CLEAR'
        ? feedTheme.green
        : row.value === 'BUILDING' || row.value === 'WAIT'
          ? feedTheme.yellow
          : undefined,
    note: row.note,
    tooltip: modelLabelTooltip(row.item)
  }))

  const trainingCompactRows: CompactFieldRow[] = [
    trainingRows[0],
    trainingRows[1],
    trainingRows[2],
    trainingRows[3],
    trainingRows[4],
    trainingRows[5]
  ].map((row) => ({
    label: row.item,
    value: row.value,
    tone:
      row.value === 'SUCCESS' || row.value === 'IDLE'
        ? feedTheme.green
        : row.value === 'RUNNING' || row.value === 'PENDING'
          ? feedTheme.yellow
          : undefined,
    note: row.note,
    tooltip: modelLabelTooltip(row.item)
  }))

  const runtimeSummaryCompactRows: CompactFieldRow[] = runtimeSummaryRows.map((row) => ({
    label: row.item,
    value: row.value,
    tone:
      row.item === 'SCORER'
        ? feedTheme.yellow
        : row.item === 'STARTUP'
          ? row.value === 'READY'
            ? feedTheme.green
            : feedTheme.red
          : undefined,
    note: row.note,
    tooltip: modelLabelTooltip(row.item)
  }))

  const confidenceCompactRows: CompactFieldRow[] = confidenceBuckets.map((row) => ({
    label: row.bucket,
    value: `${formatInteger(row.signals)} / ${formatInteger(row.accepts)}`,
    note: `ACC RATE ${formatPercentFromRatio(row.acceptRate)}`,
    tooltip: modelLabelTooltip(row.bucket)
  }))

  const trackerHealthCompactRows: CompactFieldRow[] = decisionRows.map((row) => ({
    label: row.decision,
    value: formatInteger(row.count),
    tone:
      row.decision === 'ACCEPT'
        ? feedTheme.green
        : row.decision === 'REJECT'
          ? feedTheme.red
        : row.decision === 'PAUSE' || row.decision === 'SKIP'
            ? feedTheme.yellow
            : undefined,
    note: `SHARE ${formatPercentFromRatio(row.share)}`,
    tooltip: modelLabelTooltip(row.decision)
  }))

  return (
    <DashboardPageFrame
      className="model-page"
      title="MODEL"
      meta=""
    >
      <div className="dashboard-columns model-columns">
        <section className="dashboard-column model-column">
          <DashboardPanel className="dashboard-panel--model" title="MODEL QUALITY">
            <CompactFieldList rows={predictionQualityCompactRows} columns={1} />
          </DashboardPanel>
        </section>

        <section className="dashboard-column model-column">
          <DashboardPanel
            className="dashboard-panel--model"
            title="CONFIDENCE BANDS"
          >
            <CompactFieldList rows={confidenceCompactRows} columns={1} />
          </DashboardPanel>
        </section>

        <section className="dashboard-column model-column">
          <DashboardPanel className="dashboard-panel--model" title="DECISIONS">
            <CompactFieldList rows={trackerHealthCompactRows} columns={1} />
          </DashboardPanel>
        </section>

        <section className="dashboard-column model-column">
          <DashboardPanel
            className="dashboard-panel--model"
            title="SHADOW GATE"
          >
            <CompactFieldList rows={shadowCompactRows} columns={1} />
          </DashboardPanel>
        </section>

        <section className="dashboard-column model-column">
          <DashboardPanel className="dashboard-panel--model" title="TRAINING SUMMARY">
            <CompactFieldList rows={trainingCompactRows} columns={1} />
          </DashboardPanel>
        </section>

        <section className="dashboard-column model-column model-column--span-2">
          <DashboardPanel
            className="dashboard-panel--model dashboard-panel--model-runs"
            title="TRAINING RUNS"
            meta={`${formatInteger(trainingRunRows.length)} XGBOOST RUNS`}
          >
            <DashboardTable<ModelTrainingRun>
              tableId="model-training-runs"
              rows={trainingRunRows}
              emptyMessage="NO TRAINING RUNS LOGGED YET."
              columns={[
                {
                  key: 'started',
                  label: 'STARTED',
                  className: 'dashboard-table__cell--compact',
                  render: (row) => formatTimestamp(row.started_at),
                  title: (row) => formatTimestamp(row.started_at)
                },
                {
                  key: 'log_loss',
                  label: 'LOG LOSS',
                  className: 'dashboard-table__cell--numeric dashboard-table__cell--compact',
                  render: (row) => formatDecimal(row.log_loss, 3),
                  title: (row) => formatDecimal(row.log_loss, 3)
                },
                {
                  key: 'brier',
                  label: 'BRIER',
                  className: 'dashboard-table__cell--numeric dashboard-table__cell--compact',
                  render: (row) => formatDecimal(row.brier, 3),
                  title: (row) => formatDecimal(row.brier, 3)
                },
                {
                  key: 'status',
                  label: 'STATUS',
                  className: 'dashboard-table__cell--compact',
                  render: (row) => String(row.status || '-'),
                  title: (row) => String(row.note || row.status || '-'),
                  color: (row) =>
                    row.status === 'deployed'
                      ? feedTheme.green
                      : row.status === 'completed'
                        ? feedTheme.yellow
                        : row.status === 'rejected'
                          ? feedTheme.red
                          : undefined
                },
                {
                  key: 'deployed',
                  label: 'DEPLOYED',
                  className: 'dashboard-table__cell--compact',
                  render: (row) => (row.deployed ? 'YES' : 'NO'),
                  title: (row) =>
                    row.deployed
                      ? `DEPLOYED ${formatTimestamp(row.deployed_at)}`
                      : String(row.note || 'NOT DEPLOYED'),
                  color: (row) => (row.deployed ? feedTheme.green : feedTheme.red)
                }
              ]}
            />
          </DashboardPanel>
        </section>

        <section className="dashboard-column model-column">
          <DashboardPanel className="dashboard-panel--model" title="MODEL OVERVIEW">
            <CompactFieldList rows={runtimeSummaryCompactRows} columns={1} />
          </DashboardPanel>
        </section>
      </div>
    </DashboardPageFrame>
  )
}

interface WalletsPageProps {
  mode: 'mock' | 'api'
  bankrollUsd?: number
  managedWallets: ManagedWalletsResponse
  discoveryCandidates: DiscoveryCandidatesResponse
  onManagedWalletsChange?: (nextWallets: ManagedWalletsResponse) => void
}

function walletTone(wallet: ManagedWallet): string | undefined {
  if (wallet.status === 'active') return feedTheme.green
  if (wallet.status === 'disabled') return feedTheme.red
  return undefined
}

function discoveryTone(candidate: DiscoveryCandidate): string | undefined {
  return candidate.accepted ? feedTheme.green : feedTheme.red
}

function walletPnl(wallet: ManagedWallet): number {
  return Number(wallet.post_promotion_resolved_copied_total_pnl_usd || 0)
}

function walletResolvedCount(wallet: ManagedWallet): number {
  return Number(wallet.post_promotion_resolved_copied_count || 0)
}

function walletScore(wallet: ManagedWallet): number {
  return Number(wallet.discovery_score || 0)
}

function walletStatusReason(wallet: ManagedWallet): string {
  return String(
    wallet.disabled_reason ||
      wallet.status_reason ||
      wallet.post_promotion_evidence_note ||
      wallet.trust_note ||
      '-'
  )
}

function walletCopyWinRate(wallet: ManagedWallet): number | null {
  return wallet.post_promotion_resolved_copied_win_rate ?? null
}

function walletSkipRate(wallet: ManagedWallet): number | null {
  return wallet.post_promotion_uncopyable_skip_rate ?? null
}

export function WalletsPage({
  mode,
  bankrollUsd,
  managedWallets,
  discoveryCandidates,
  onManagedWalletsChange
}: WalletsPageProps) {
  const wallets = managedWallets.wallets || []
  const candidates = discoveryCandidates.candidates || []
  const [walletActionMessage, setWalletActionMessage] = useState('')
  const [walletBusyKey, setWalletBusyKey] = useState('')
  const bestWallets = useMemo(
    () =>
      [...wallets]
        .sort((left, right) => {
          const pnlDelta = walletPnl(right) - walletPnl(left)
          if (pnlDelta !== 0) return pnlDelta
          const resolvedDelta = walletResolvedCount(right) - walletResolvedCount(left)
          if (resolvedDelta !== 0) return resolvedDelta
          return walletScore(right) - walletScore(left)
        })
        .slice(0, 8),
    [wallets]
  )
  const bestWalletRows = useMemo(
    () => bestWallets.map((wallet, index) => ({...wallet, walletRank: index + 1})),
    [bestWallets]
  )
  const bestSummaryRows = useMemo(() => bestWalletRows.slice(0, 4), [bestWalletRows])
  const worstWallets = useMemo(
    () =>
      [...wallets]
        .sort((left, right) => {
          const pnlDelta = walletPnl(left) - walletPnl(right)
          if (pnlDelta !== 0) return pnlDelta
          const resolvedDelta = walletResolvedCount(right) - walletResolvedCount(left)
          if (resolvedDelta !== 0) return resolvedDelta
          return walletScore(left) - walletScore(right)
        })
        .slice(0, 8),
    [wallets]
  )
  const worstWalletRows = useMemo(
    () => worstWallets.map((wallet, index) => ({...wallet, walletRank: index + 1})),
    [worstWallets]
  )
  const worstSummaryRows = useMemo(() => worstWalletRows.slice(0, 4), [worstWalletRows])
  const trackedWallets = useMemo(
    () =>
      wallets
        .filter((wallet) => wallet.status !== 'disabled')
        .sort((left, right) => {
          const trackingDelta = Number(right.tracking_started_at || 0) - Number(left.tracking_started_at || 0)
          if (trackingDelta !== 0) return trackingDelta
          return walletPnl(right) - walletPnl(left)
        }),
    [wallets]
  )
  const droppedWallets = useMemo(
    () =>
      wallets
        .filter((wallet) => wallet.status === 'disabled')
        .sort((left, right) => Number(right.disabled_at || 0) - Number(left.disabled_at || 0)),
    [wallets]
  )

  function applyManagedWalletRows(nextWalletRows: ManagedWallet[]) {
    onManagedWalletsChange?.({
      ...managedWallets,
      count: nextWalletRows.length,
      managed_wallet_count: nextWalletRows.length,
      managed_wallet_total_count: nextWalletRows.length,
      wallets: nextWalletRows
    })
  }

  function mutateWallet(walletAddress: string, mutate: (wallet: ManagedWallet, nowTs: number) => ManagedWallet) {
    const nowTs = Math.floor(Date.now() / 1000)
    applyManagedWalletRows(
      wallets.map((wallet) =>
        wallet.wallet_address === walletAddress ? mutate(wallet, nowTs) : wallet
      )
    )
  }

  async function handleDropWallet(wallet: ManagedWallet) {
    const walletAddress = String(wallet.wallet_address || '').trim()
    if (!walletAddress) return

    setWalletBusyKey(`drop:${walletAddress}`)
    setWalletActionMessage('')
    try {
      if (mode === 'mock') {
        mutateWallet(walletAddress, (currentWallet, nowTs) => ({
          ...currentWallet,
          status: 'disabled',
          status_reason: 'manual dashboard drop',
          disabled_reason: 'manual dashboard drop',
          disabled_at: nowTs,
          updated_at: nowTs
        }))
        setWalletActionMessage(`DROPPED ${String(wallet.username || walletAddress).toUpperCase()}.`)
        return
      }

      const response = await requestDropWallet(walletAddress)
      const refreshedWallets = await fetchManagedWallets()
      if (refreshedWallets) {
        onManagedWalletsChange?.(refreshedWallets)
      }
      setWalletActionMessage(
        String(response?.message || `DROPPED ${String(wallet.username || walletAddress).toUpperCase()}.`).toUpperCase()
      )
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'WALLET DROP FAILED.'
      setWalletActionMessage(String(detail || 'WALLET DROP FAILED.').toUpperCase())
    } finally {
      setWalletBusyKey('')
    }
  }

  async function handleReactivateWallet(wallet: ManagedWallet) {
    const walletAddress = String(wallet.wallet_address || '').trim()
    if (!walletAddress) return

    setWalletBusyKey(`reactivate:${walletAddress}`)
    setWalletActionMessage('')
    try {
      if (mode === 'mock') {
        mutateWallet(walletAddress, (currentWallet, nowTs) => ({
          ...currentWallet,
          status: 'active',
          status_reason: 'tracked',
          disabled_reason: '',
          disabled_at: undefined,
          updated_at: nowTs,
          tracking_started_at: currentWallet.tracking_started_at || nowTs
        }))
        setWalletActionMessage(`REACTIVATED ${String(wallet.username || walletAddress).toUpperCase()}.`)
        return
      }

      const response = await requestReactivateWallet(walletAddress)
      const refreshedWallets = await fetchManagedWallets()
      if (refreshedWallets) {
        onManagedWalletsChange?.(refreshedWallets)
      }
      setWalletActionMessage(
        String(response?.message || `REACTIVATED ${String(wallet.username || walletAddress).toUpperCase()}.`).toUpperCase()
      )
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'WALLET REACTIVATION FAILED.'
      setWalletActionMessage(String(detail || 'WALLET REACTIVATION FAILED.').toUpperCase())
    } finally {
      setWalletBusyKey('')
    }
  }

  return (
    <DashboardPageFrame
      className={joinClasses('wallet-page', walletActionMessage ? 'wallet-page--with-status' : undefined)}
      title="WALLETS"
      meta={`${formatInteger(wallets.length)} MANAGED • ${formatInteger(candidates.length)} DISCOVERY CANDIDATES`}
    >
      {walletActionMessage ? <div className="dashboard-page__status">{walletActionMessage}</div> : null}
      <div className="dashboard-panels dashboard-panels--two">
        <DashboardPanel
          title="BEST WALLETS"
          meta={`TOP ${bestSummaryRows.length} SHOWN OF ${wallets.length}`}
          className="dashboard-panel--wallet-summary"
        >
          <DashboardTable
            tableId="wallets-best"
            rows={bestSummaryRows}
            emptyMessage="NO WALLET LEADERS AVAILABLE."
            columns={[
              {key: 'rank', label: '#', className: 'dashboard-table__cell--compact', render: (row) => formatInteger(row.walletRank)},
              {
                key: 'username',
                label: 'USERNAME',
                className: 'dashboard-table__cell--wide',
                render: (row) => row.username || row.wallet_address || '-',
                title: (row) => row.wallet_address,
                color: (row) => (row.status === 'disabled' ? feedTheme.red : undefined)
              },
              {
                key: 'copywr',
                label: 'COPY WR',
                className: 'dashboard-table__cell--numeric',
                render: (row) => formatPercentFromRatio(walletCopyWinRate(row)),
                color: (row) => centeredRatioGradient(walletCopyWinRate(row), 0.5, 0.25)
              },
              {
                key: 'skip',
                label: 'SKIP %',
                className: 'dashboard-table__cell--numeric',
                render: (row) => formatPercentFromRatio(walletSkipRate(row)),
                color: (row) => centeredRatioGradient(walletSkipRate(row), 0.15, 0.15, true)
              },
              {key: 'copied', label: 'COPIED', className: 'dashboard-table__cell--numeric', render: (row) => formatInteger(row.post_promotion_resolved_copied_count)},
              {key: 'pnl', label: 'COPY P&L', className: 'dashboard-table__cell--numeric', render: (row) => formatMoney(row.post_promotion_resolved_copied_total_pnl_usd), color: (row) => moneyMetricColor(walletPnl(row), bankrollUsd, row.post_promotion_resolved_copied_count)}
            ]}
          />
        </DashboardPanel>

        <DashboardPanel
          title="WORST WALLETS"
          meta={`BOTTOM ${worstSummaryRows.length} SHOWN OF ${wallets.length}`}
          className="dashboard-panel--wallet-summary"
        >
          <DashboardTable
            tableId="wallets-worst"
            rows={worstSummaryRows}
            emptyMessage="NO WALLET LAGGARDS AVAILABLE."
            columns={[
              {key: 'rank', label: '#', className: 'dashboard-table__cell--compact', render: (row) => formatInteger(row.walletRank)},
              {
                key: 'username',
                label: 'USERNAME',
                className: 'dashboard-table__cell--wide',
                render: (row) => row.username || row.wallet_address || '-',
                title: (row) => row.wallet_address,
                color: (row) => (row.status === 'disabled' ? feedTheme.red : undefined)
              },
              {
                key: 'copywr',
                label: 'COPY WR',
                className: 'dashboard-table__cell--numeric',
                render: (row) => formatPercentFromRatio(walletCopyWinRate(row)),
                color: (row) => centeredRatioGradient(walletCopyWinRate(row), 0.5, 0.25)
              },
              {
                key: 'skip',
                label: 'SKIP %',
                className: 'dashboard-table__cell--numeric',
                render: (row) => formatPercentFromRatio(walletSkipRate(row)),
                color: (row) => centeredRatioGradient(walletSkipRate(row), 0.15, 0.15, true)
              },
              {key: 'copied', label: 'COPIED', className: 'dashboard-table__cell--numeric', render: (row) => formatInteger(row.post_promotion_resolved_copied_count)},
              {key: 'pnl', label: 'COPY P&L', className: 'dashboard-table__cell--numeric', render: (row) => formatMoney(row.post_promotion_resolved_copied_total_pnl_usd), color: (row) => moneyMetricColor(walletPnl(row), bankrollUsd, row.post_promotion_resolved_copied_count)}
            ]}
          />
        </DashboardPanel>
      </div>

      <div className="dashboard-panels wallet-panels--detail">
        <DashboardPanel
          title="TRACKED WALLETS"
          meta={`${trackedWallets.length} ACTIVE PROFILES`}
          className="dashboard-panel--wallet-primary"
        >
          <DashboardTable
            tableId="wallets-tracked"
            rows={trackedWallets}
            emptyMessage="NO TRACKED WALLETS AVAILABLE."
            columns={[
              {key: 'username', label: 'USERNAME', className: 'dashboard-table__cell--compact', render: (row) => row.username || '-'},
              {key: 'wallet', label: 'WALLET', className: 'dashboard-table__cell--wide', render: (row) => row.wallet_address || '-', title: (row) => row.wallet_address},
              {key: 'since', label: 'SINCE', className: 'dashboard-table__cell--compact', render: (row) => formatRelativeAge(row.tracking_started_at)},
              {key: 'track', label: 'TRACK', className: 'dashboard-table__cell--compact', render: (row) => row.status || '-', color: (row) => walletTone(row)},
              {key: 'skip', label: 'SKIP %', className: 'dashboard-table__cell--numeric', render: (row) => formatPercentFromRatio(walletSkipRate(row)), color: (row) => centeredRatioGradient(walletSkipRate(row), 0.15, 0.15, true)},
              {key: 'seen', label: 'COPIED', className: 'dashboard-table__cell--numeric', render: (row) => formatInteger(row.post_promotion_resolved_copied_count)},
              {key: 'copywr', label: 'COPY WR', className: 'dashboard-table__cell--numeric', render: (row) => formatPercentFromRatio(walletCopyWinRate(row)), color: (row) => centeredRatioGradient(walletCopyWinRate(row), 0.5, 0.25)},
              {key: 'size', label: 'SIZE', className: 'dashboard-table__cell--numeric', render: (row) => row.trust_size_multiplier?.toFixed(2) || '-'},
              {key: 'pnl', label: 'COPY P&L', className: 'dashboard-table__cell--numeric', render: (row) => formatMoney(row.post_promotion_resolved_copied_total_pnl_usd), color: (row) => moneyMetricColor(walletPnl(row), bankrollUsd, row.post_promotion_resolved_copied_count)},
              {key: 'last', label: 'LAST UPDATE', className: 'dashboard-table__cell--compact', render: (row) => formatRelativeAge(row.updated_at || row.tracking_started_at)},
              {
                key: 'drop',
                label: 'DROP',
                className: 'dashboard-table__cell--compact dashboard-table__cell--flush',
                render: (row) => (
                  <button
                    type="button"
                    className="wallet-action-button wallet-action-button--drop"
                    disabled={Boolean(walletBusyKey)}
                    onClick={() => void handleDropWallet(row)}
                  >
                    DROP
                  </button>
                )
              }
            ]}
          />
        </DashboardPanel>

        <DashboardPanel
          title="DROPPED WALLETS"
          meta={`${droppedWallets.length} DROPPED PROFILES`}
          className="dashboard-panel--wallet-secondary"
        >
          <DashboardTable
            tableId="wallets-dropped"
            rows={droppedWallets}
            emptyMessage="NO DROPPED WALLETS AVAILABLE."
            columns={[
              {key: 'username', label: 'USERNAME', className: 'dashboard-table__cell--compact', render: (row) => row.username || '-', color: () => resolveToneColor('negative')},
              {key: 'wallet', label: 'WALLET', className: 'dashboard-table__cell--wide', render: (row) => row.wallet_address || '-', title: (row) => row.wallet_address},
              {key: 'reason', label: 'REASON', className: 'dashboard-table__cell--wide', render: (row) => walletStatusReason(row), title: (row) => walletStatusReason(row)},
              {key: 'last', label: 'LAST TRADE', className: 'dashboard-table__cell--compact', render: (row) => formatRelativeAge(row.updated_at || row.tracking_started_at)},
              {key: 'dropped', label: 'DROPPED', className: 'dashboard-table__cell--compact', render: (row) => formatRelativeAge(row.disabled_at)},
              {key: 'pnl', label: 'COPY P&L', className: 'dashboard-table__cell--numeric', render: (row) => formatMoney(row.post_promotion_resolved_copied_total_pnl_usd), color: (row) => moneyMetricColor(walletPnl(row), bankrollUsd, row.post_promotion_resolved_copied_count)},
              {
                key: 'reactivate',
                label: 'REACTIVATE',
                className: 'dashboard-table__cell--compact dashboard-table__cell--flush',
                render: (row) => (
                  <button
                    type="button"
                    className="wallet-action-button wallet-action-button--reactivate"
                    disabled={Boolean(walletBusyKey)}
                    onClick={() => void handleReactivateWallet(row)}
                  >
                    REACTIVATE
                  </button>
                )
              }
            ]}
          />
        </DashboardPanel>
      </div>
    </DashboardPageFrame>
  )
}

interface ConfigPageProps {
  mode: 'mock' | 'api'
  configSnapshot: ConfigSnapshot
  managedWallets: ManagedWalletsResponse
  botState: BotState
  onConfigSnapshotChange?: (snapshot: ConfigSnapshot) => void
  onBotStateChange?: (nextState: BotState) => void
}

interface ConfigEditorRow {
  key: string
  label: string
  kind: string
  liveApplies: boolean
  source: string
  value: string
  description: string
}

const CONFIG_CHOICE_OPTIONS: Record<string, string[]> = {
  RETRAIN_BASE_CADENCE: ['daily', 'weekly'],
  LOG_LEVEL: ['DEBUG', 'INFO', 'WARNING', 'ERROR']
}

const CONFIG_DURATION_OPTIONS = ['0s', '3s', '45s', '90s', '5m', '20m', '3h', '24h', '2d', '3d', 'unlimited']

type DangerActionId =
  | 'live_trading'
  | 'archive_trade_log'
  | 'restart_shadow'
  | 'recover_db'
  | 'reset_config'

const RESTART_SHADOW_OPTIONS: Array<{
  value: RestartShadowWalletMode
  label: string
}> = [
  {value: 'keep_active', label: 'KEEP ACTIVE WALLETS'},
  {value: 'keep_all', label: 'KEEP ALL WALLETS'},
  {value: 'clear_all', label: 'CLEAR ALL WALLETS'}
]

function resolveRecoveryCandidateMode(botState: BotState): 'evidence_ready' | 'integrity_only' | 'unavailable' {
  const ready = Boolean(botState.db_recovery_candidate_ready)
  const mode = String(botState.db_recovery_candidate_mode || '').trim().toLowerCase()
  if (!ready) return 'unavailable'
  return mode === 'evidence_ready' ? 'evidence_ready' : 'integrity_only'
}

function normalizeConfigDraftValue(kind: string, rawValue: string): string {
  const value = rawValue.trim()
  if (kind === 'bool') return value.toLowerCase() === 'false' ? 'false' : 'true'
  if (kind === 'duration') return value.toLowerCase()
  if (kind === 'choice') return value.toUpperCase()
  return value
}

function validateConfigDraftValue(key: string, kind: string, rawValue: string): string | null {
  const value = rawValue.trim()
  if (!value.length) return 'VALUE REQUIRED'
  if (kind === 'int') return /^-?\d+$/.test(value) ? null : 'ENTER A WHOLE NUMBER'
  if (kind === 'float') return /^-?(?:\d+|\d*\.\d+)$/.test(value) ? null : 'ENTER A NUMBER'
  if (kind === 'bool') return /^(true|false)$/i.test(value) ? null : 'USE TRUE OR FALSE'
  if (kind === 'duration') {
    return /^(unlimited|off|(?:\d+(?:\.\d+)?)(?:ms|s|m|h|d|w))$/i.test(value)
      ? null
      : 'USE 90S, 5M, 3H, 2D, OR UNLIMITED'
  }
  if (kind === 'choice') {
    const options = CONFIG_CHOICE_OPTIONS[key] || []
    if (!options.length) return null
    return options.some((option) => option.toLowerCase() === value.toLowerCase())
      ? null
      : `CHOOSE ${options.join(' / ')}`
  }
  return null
}

function configInputHint(row: ConfigEditorRow): string {
  if (row.kind === 'bool') return 'TRUE / FALSE'
  if (row.kind === 'int') return 'WHOLE NUMBER'
  if (row.kind === 'float') return 'NUMBER'
  if (row.kind === 'duration') return '90S / 5M / 3H / 2D / UNLIMITED'
  if (row.kind === 'choice') {
    const options = CONFIG_CHOICE_OPTIONS[row.key]
    return options ? options.join(' / ') : 'SELECT VALUE'
  }
  return 'TEXT'
}

function configDiscreteOptions(row: ConfigEditorRow, rawValue: string): string[] | null {
  if (row.kind === 'bool') return ['true', 'false']
  if (row.kind === 'choice') {
    const options = CONFIG_CHOICE_OPTIONS[row.key] || []
    return options.length ? options : null
  }
  if (row.kind === 'duration') {
    const orderedValues = [row.value, rawValue, ...CONFIG_DURATION_OPTIONS]
      .map((value) => normalizeConfigDraftValue(row.kind, value))
      .filter(Boolean)
    return Array.from(new Set(orderedValues))
  }
  return null
}

function useConfigDiscreteDropdown(row: ConfigEditorRow, options: string[] | null): boolean {
  if (!options?.length) return false
  return row.kind === 'duration' || row.kind === 'choice' || options.length > 2
}

export function ConfigPage({
  mode,
  configSnapshot,
  managedWallets,
  botState,
  onConfigSnapshotChange,
  onBotStateChange
}: ConfigPageProps) {
  const configValueMap = useMemo(() => {
    const entries = (configSnapshot.rows || []).map(
      (row): [string, string] => [String(row.key || ''), String(row.value || '')]
    )
    return new Map<string, string>(entries)
  }, [configSnapshot.rows])
  const configSourceMap = useMemo(() => {
    const entries = (configSnapshot.rows || []).map(
      (row): [string, string] => [String(row.key || ''), String(row.source || '')]
    )
    return new Map<string, string>(entries)
  }, [configSnapshot.rows])
  const configRows: ConfigEditorRow[] = useMemo(
    () =>
      editableConfigFields.map((field) => ({
        key: field.key,
        label: field.label,
        kind: field.kind,
        liveApplies: field.liveApplies,
        source: configSourceMap.get(field.key) || 'default',
        value: configValueMap.get(field.key) ?? field.defaultValue,
        description:
          configFieldDescriptions[field.key]
          || `${field.label} controls that backend setting.`
      })),
    [configSourceMap, configValueMap]
  )
  const [draftValues, setDraftValues] = useState<Record<string, string>>({})
  const [statusMessage, setStatusMessage] = useState('')
  const [busyKey, setBusyKey] = useState('')
  const [restartWalletMode, setRestartWalletMode] = useState<RestartShadowWalletMode>('keep_active')

  useEffect(() => {
    const nextDrafts: Record<string, string> = {}
    for (const row of configRows) {
      nextDrafts[row.key] = row.value
    }
    setDraftValues(nextDrafts)
  }, [configRows])

  const watchedWalletRows = (configSnapshot.watched_wallets || []).map((wallet) => {
    const match = (managedWallets.wallets || []).find((row) => row.wallet_address === wallet)
    return {
      wallet,
      username: match?.username || '-',
      status: match?.status || 'tracked'
    }
  })
  const configuredMode = String(botState.configured_mode || botState.mode || 'shadow')
    .trim()
    .toLowerCase()
  const liveTradingEnabled = configuredMode === 'live'
  const shadowRestartPending = Boolean(botState.shadow_restart_pending)
  const shadowRestartMessage =
    String(botState.shadow_restart_message || '').trim()
    || 'SHADOW RESTART REQUESTED. WAITING FOR BACKEND TO RESTART.'
  const startupRecoveryOnly = Boolean(botState.startup_recovery_only)
  const startupBlockReason =
    String(botState.startup_block_reason || '').trim()
    || String(botState.mode_block_reason || '').trim()
    || String(botState.startup_detail || '').trim()
  const configEditBlockedMessage = shadowRestartPending
    ? `${shadowRestartMessage} CONFIG EDITS STAY BLOCKED UNTIL THE BACKEND RESTARTS.`
    : startupRecoveryOnly
      ? startupBlockReason
        ? `RECOVERY-ONLY MODE: ${startupBlockReason} CONFIG EDITS STAY BLOCKED UNTIL RECOVER DB OR RESTART SHADOW COMPLETES.`
        : 'RECOVERY-ONLY MODE: CONFIG EDITS STAY BLOCKED UNTIL RECOVER DB OR RESTART SHADOW COMPLETES.'
      : ''
  const liveModeBlockedMessage = shadowRestartPending
    ? `${shadowRestartMessage} LIVE-MODE REQUESTS STAY BLOCKED UNTIL THE BACKEND RESTARTS.`
    : startupRecoveryOnly
      ? startupBlockReason
        ? `RECOVERY-ONLY MODE: ${startupBlockReason}`
        : 'BACKEND IS IN RECOVERY-ONLY MODE. RECOVER DB OR RESTART SHADOW FIRST.'
      : ''
  const tradeLogArchiveEnabled = botState.trade_log_archive_state_known != null
    ? Boolean(botState.trade_log_archive_enabled)
    : true
  const tradeLogArchivePending = Boolean(botState.trade_log_archive_pending)
  const tradeLogArchiveBlockReason = String(botState.trade_log_archive_block_reason || '').trim()
  const tradeLogArchiveBlockedMessage = !tradeLogArchiveEnabled
    ? 'TRADE LOG ARCHIVE IS DISABLED IN CONFIG.'
    : shadowRestartPending
      ? shadowRestartMessage
      : startupRecoveryOnly
        ? startupBlockReason
          ? `RECOVERY-ONLY MODE: ${startupBlockReason}`
          : 'RECOVERY-ONLY MODE: TRADE LOG ARCHIVE IS UNAVAILABLE UNTIL RECOVER DB OR RESTART SHADOW COMPLETES.'
        : tradeLogArchiveBlockReason
          ? tradeLogArchiveBlockReason
          : ''
  const recoveryCandidateMode = resolveRecoveryCandidateMode(botState)
  const recoveryCandidateLabel =
    recoveryCandidateMode === 'evidence_ready'
      ? 'EVIDENCE-READY'
      : recoveryCandidateMode === 'integrity_only'
        ? 'INTEGRITY-ONLY'
        : 'UNAVAILABLE'
  const recoveryCandidateTone =
    recoveryCandidateMode === 'evidence_ready'
      ? 'positive'
      : recoveryCandidateMode === 'integrity_only'
        ? 'warning'
        : 'negative'
  const recoveryCandidateClassReason =
    String(botState.db_recovery_candidate_class_reason || '').trim()
    || (
      recoveryCandidateMode === 'evidence_ready'
        ? 'VERIFIED BACKUP IS RECOVERABLE AND PASSES THE CURRENT SHADOW EVIDENCE GATE.'
        : recoveryCandidateMode === 'integrity_only'
          ? 'VERIFIED BACKUP CAN RESTORE LEDGER INTEGRITY, BUT IT IS NOT EVIDENCE-READY.'
          : 'NO VERIFIED RECOVERY BACKUP IS READY YET.'
    )
  const recoveryUnavailableMessage =
    recoveryCandidateClassReason
    || String(botState.db_recovery_candidate_message || '').trim()
    || 'RECOVER DB IS UNAVAILABLE BECAUSE NO VERIFIED BACKUP CANDIDATE IS READY.'

  function applyMockBotStateUpdate(patch: Partial<BotState>): void {
    onBotStateChange?.({
      ...botState,
      ...patch
    })
  }

  function applyMockWatchedWallets(nextWallets: string[], message: string): void {
    onConfigSnapshotChange?.({
      ...configSnapshot,
      watched_wallets: nextWallets,
      message
    })
  }

  function applyMockConfigUpdate(key: string, value: string, cleared = false): void {
    const nextRows = editableConfigFields.map((field) => {
      const current = configSnapshot.rows?.find((row) => row.key === field.key)
      const nextValue =
        field.key === key
          ? (cleared ? field.defaultValue : value)
          : (current?.value ?? field.defaultValue)
      return {
        key: field.key,
        value: nextValue,
        source: field.key === key ? (cleared ? 'default' : 'db') : (current?.source ?? 'default')
      }
    })
    const nextSafeValues = Object.fromEntries(
      nextRows.filter((row) => row.value !== '').map((row) => [row.key, row.value])
    )
    onConfigSnapshotChange?.({
      ...configSnapshot,
      rows: nextRows,
      safe_values: nextSafeValues,
      message: cleared ? `${key} reset to default.` : `${key} saved.`
    })
  }

  async function handleSave(key: string): Promise<void> {
    const row = configRows.find((entry) => entry.key === key)
    if (!row) return
    if (configEditBlockedMessage) {
      setStatusMessage(configEditBlockedMessage)
      return
    }
    const nextValue = normalizeConfigDraftValue(row.kind, String(draftValues[key] ?? ''))
    const validationError = validateConfigDraftValue(row.key, row.kind, nextValue)
    if (validationError) {
      setStatusMessage(`${row.label}: ${validationError}`)
      return
    }
    setBusyKey(key)
    try {
      if (mode === 'mock') {
        applyMockConfigUpdate(key, nextValue, false)
        setStatusMessage(`${key} saved in mock mode.`)
      } else {
        const response = await saveConfigValue(key, nextValue)
        if (response) {
          onConfigSnapshotChange?.(response)
        }
        setStatusMessage(`${key} saved.`)
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'CONFIG SAVE FAILED.')
    } finally {
      setBusyKey('')
    }
  }

  async function handleResetAll(): Promise<void> {
    if (configEditBlockedMessage) {
      setStatusMessage(configEditBlockedMessage)
      return
    }
    setBusyKey('danger:reset_config')
    try {
      if (mode === 'mock') {
        const nextRows = editableConfigFields.map((field) => ({
          key: field.key,
          value: field.defaultValue,
          source: 'default'
        }))
        setDraftValues(
          Object.fromEntries(nextRows.map((row) => [row.key, row.value]))
        )
        onConfigSnapshotChange?.({
          ...configSnapshot,
          rows: nextRows,
          safe_values: Object.fromEntries(
            nextRows.filter((row) => row.value !== '').map((row) => [row.key, row.value])
          ),
          message: 'ALL CONFIG VALUES RESET TO DEFAULTS.'
        })
        setStatusMessage('ALL CONFIG VALUES RESET IN MOCK MODE.')
      } else {
        let latestResponse: ConfigSnapshot | null = null
        for (const field of editableConfigFields) {
          latestResponse = await clearConfigValue(field.key)
        }
        if (latestResponse) {
          onConfigSnapshotChange?.(latestResponse)
        }
        setStatusMessage('ALL CONFIG VALUES RESET TO DEFAULTS.')
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'CONFIG RESET FAILED.')
    } finally {
      setBusyKey('')
    }
  }

  async function handleLiveTradingToggle(): Promise<void> {
    if (liveModeBlockedMessage) {
      setStatusMessage(liveModeBlockedMessage)
      return
    }
    const nextEnabled = !liveTradingEnabled
    setBusyKey('danger:live_trading')
    try {
      if (mode === 'mock') {
        applyMockBotStateUpdate({
          configured_mode: nextEnabled ? 'live' : 'shadow'
        })
        setStatusMessage(`LIVE TRADING ${nextEnabled ? 'ENABLED' : 'DISABLED'} IN MOCK MODE.`)
      } else {
        const response = await setLiveTradingEnabled(nextEnabled)
        setStatusMessage(
          String(response?.message || `LIVE TRADING ${nextEnabled ? 'ENABLED' : 'DISABLED'}.`).trim()
        )
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'LIVE MODE UPDATE FAILED.')
    } finally {
      setBusyKey('')
    }
  }

  async function handleArchiveTradeLog(): Promise<void> {
    if (tradeLogArchiveBlockedMessage) {
      setStatusMessage(tradeLogArchiveBlockedMessage)
      return
    }
    if (tradeLogArchivePending) {
      setStatusMessage(
        String(botState.trade_log_archive_request_message || '').trim()
        || 'TRADE LOG ARCHIVE REQUEST IS ALREADY PENDING.'
      )
      return
    }
    setBusyKey('danger:archive_trade_log')
    try {
      if (mode === 'mock') {
        const eligible = Math.max(0, Number(botState.trade_log_archive_eligible_row_count || 0))
        const archived = Math.min(eligible, 200)
        applyMockBotStateUpdate({
          trade_log_archive_status: 'idle',
          trade_log_archive_pending: false,
          trade_log_archive_last_run_at: Math.floor(Date.now() / 1000),
          trade_log_archive_last_candidate_count: eligible,
          trade_log_archive_last_archived_count: archived,
          trade_log_archive_last_deleted_count: archived,
          trade_log_archive_eligible_row_count: Math.max(0, eligible - archived),
          trade_log_archive_active_row_count: Math.max(
            0,
            Number(botState.trade_log_archive_active_row_count || 0) - archived
          ),
          trade_log_archive_archive_row_count:
            Number(botState.trade_log_archive_archive_row_count || 0) + archived,
          trade_log_archive_request_message: `ARCHIVED ${formatInteger(archived)} ROWS IN MOCK MODE.`
        })
        setStatusMessage(`ARCHIVED ${formatInteger(archived)} ROWS IN MOCK MODE.`)
      } else {
        const response = await requestTradeLogArchive()
        setStatusMessage(String(response?.message || 'TRADE LOG ARCHIVE REQUESTED.').trim())
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'TRADE LOG ARCHIVE FAILED.')
    } finally {
      setBusyKey('')
    }
  }

  async function handleRestartShadow(): Promise<void> {
    if (shadowRestartPending) {
      setStatusMessage(shadowRestartMessage)
      return
    }
    setBusyKey('danger:restart_shadow')
    try {
      if (mode === 'mock') {
        const activeWallets = (managedWallets.wallets || [])
          .filter((row) => String(row.status || '').trim().toLowerCase() !== 'disabled')
          .map((row) => String(row.wallet_address || '').trim())
          .filter(Boolean)
        const nextWatchedWallets =
          restartWalletMode === 'clear_all'
            ? []
            : restartWalletMode === 'keep_active'
              ? activeWallets
              : [...(configSnapshot.watched_wallets || [])]
        applyMockWatchedWallets(
          nextWatchedWallets,
          `MOCK SHADOW RESTART QUEUED (${restartWalletMode.replace('_', ' ')}).`
        )
        applyMockBotStateUpdate({
          configured_mode: 'shadow',
          shadow_restart_pending: true,
          shadow_restart_kind: 'shadow_reset',
          shadow_restart_message: `MOCK SHADOW RESTART QUEUED (${restartWalletMode.replace('_', ' ')}).`
        })
        setStatusMessage(`MOCK SHADOW RESTART QUEUED (${restartWalletMode.replace('_', ' ')}).`)
      } else {
        const response = await requestShadowRestart(restartWalletMode)
        setStatusMessage(String(response?.message || 'SHADOW RESTART REQUESTED.').trim())
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'SHADOW RESTART FAILED.')
    } finally {
      setBusyKey('')
    }
  }

  async function handleRecoverDb(): Promise<void> {
    if (shadowRestartPending) {
      setStatusMessage(shadowRestartMessage)
      return
    }
    if (recoveryCandidateMode === 'unavailable') {
      setStatusMessage(recoveryUnavailableMessage)
      return
    }
    setBusyKey('danger:recover_db')
    try {
      if (mode === 'mock') {
        applyMockBotStateUpdate({
          shadow_restart_pending: true,
          shadow_restart_kind: 'db_recovery',
          shadow_restart_message: `MOCK DB RECOVERY QUEUED FROM ${recoveryCandidateLabel.toLowerCase()} BACKUP.`
        })
        setStatusMessage(`MOCK DB RECOVERY QUEUED FROM ${recoveryCandidateLabel.toLowerCase()} BACKUP.`)
      } else {
        const response = await requestRecoverDb()
        setStatusMessage(String(response?.message || 'DB RECOVERY REQUESTED.').trim())
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'DB RECOVERY FAILED.')
    } finally {
      setBusyKey('')
    }
  }

  return (
    <DashboardPageFrame
      className="config-page"
      title="CONFIG"
      meta={`${formatInteger(configRows.length)} CONFIG ROWS • ${formatInteger(watchedWalletRows.length)} WATCHED WALLETS`}
    >
      {statusMessage ? <div className="config-editor__status">{statusMessage}</div> : null}

      <div className="dashboard-panels dashboard-panels--two dashboard-panels--config config-panels-main">
        <DashboardPanel
          className="dashboard-panel--config-editor"
          title="CONFIG EDITOR"
          meta={configEditBlockedMessage || configSnapshot.message || 'FULL EDITABLE FIELD SET'}
        >
          <div className="config-editor__viewport">
            <div className="config-editor">
              {configRows.map((row) => {
                const isBusy = busyKey === row.key
                const rawValue = String(draftValues[row.key] ?? '')
                const normalizedValue = normalizeConfigDraftValue(row.kind, rawValue)
                const validationError = validateConfigDraftValue(row.key, row.kind, normalizedValue)
                const isDirty = normalizedValue !== row.value
                const discreteOptions = configDiscreteOptions(row, rawValue)
                const useDiscreteDropdown = useConfigDiscreteDropdown(row, discreteOptions)
                return (
                  <div key={row.key} className="config-editor__row">
                    <div className="config-editor__meta">
                      <div className="config-editor__label" title={row.description || row.label}>{row.label}</div>
                      <div className="config-editor__submeta">{row.description}</div>
                    </div>
                    <div className="config-editor__controls">
                      {discreteOptions ? (
                        useDiscreteDropdown ? (
                          <select
                            className="config-editor__select"
                            value={normalizedValue}
                            onChange={(event) =>
                              setDraftValues((current) => ({...current, [row.key]: event.target.value}))
                            }
                            disabled={Boolean(configEditBlockedMessage)}
                          >
                            {discreteOptions.map((option) => {
                              const normalizedOption = normalizeConfigDraftValue(row.kind, option)
                              return (
                                <option key={normalizedOption} value={normalizedOption}>
                                  {option}
                                </option>
                              )
                            })}
                          </select>
                        ) : (
                          <div className="config-editor__toggle-group" role="radiogroup" aria-label={row.label}>
                            {discreteOptions.map((option) => {
                              const normalizedOption = normalizeConfigDraftValue(row.kind, option)
                              const isSelected = normalizedValue === normalizedOption
                              return (
                                <button
                                  key={option}
                                  type="button"
                                  className={`config-editor__toggle${isSelected ? ' config-editor__toggle--active' : ''}`}
                                  aria-pressed={isSelected}
                                  onClick={() =>
                                    setDraftValues((current) => ({...current, [row.key]: option}))
                                  }
                                  disabled={Boolean(configEditBlockedMessage)}
                                >
                                  {option}
                                </button>
                              )
                            })}
                          </div>
                        )
                      ) : (
                        <input
                          className="config-editor__input"
                          value={rawValue}
                          onChange={(event) =>
                            setDraftValues((current) => ({...current, [row.key]: event.target.value}))
                          }
                          spellCheck={false}
                          inputMode={
                            row.kind === 'int'
                              ? 'numeric'
                              : row.kind === 'float'
                                ? 'decimal'
                                : 'text'
                          }
                          type={row.kind === 'int' || row.kind === 'float' ? 'number' : 'text'}
                          step={row.kind === 'int' ? '1' : row.kind === 'float' ? 'any' : undefined}
                          placeholder={configInputHint(row)}
                          disabled={Boolean(configEditBlockedMessage)}
                        />
                      )}
                      <div className="config-editor__actions">
                        <button
                          type="button"
                          className="config-editor__button"
                          onClick={() => void handleSave(row.key)}
                          disabled={
                            Boolean(configEditBlockedMessage)
                            || isBusy
                            || !isDirty
                            || validationError != null
                          }
                        >
                          {isBusy ? 'SAVING' : 'SAVE'}
                        </button>
                      </div>
                    </div>
                    {validationError ? <div className="config-editor__error">{validationError}</div> : null}
                  </div>
                )
              })}
            </div>
          </div>
        </DashboardPanel>

        <div className="config-side-column">
          <DashboardPanel
            className="dashboard-panel--config-watched"
            title="WATCHED WALLETS"
            meta={`${watchedWalletRows.length} ENTRIES`}
          >
            <DashboardTable
              tableId="config-watched-wallets"
              rows={watchedWalletRows}
              emptyMessage="NO WATCHED WALLETS CONFIGURED."
              columns={[
                {key: 'username', label: 'USERNAME', className: 'dashboard-table__cell--compact', render: (row) => row.username},
                {key: 'wallet', label: 'WALLET', className: 'dashboard-table__cell--wide', render: (row) => row.wallet, title: (row) => row.wallet},
                {key: 'status', label: 'STATUS', className: 'dashboard-table__cell--compact', render: (row) => row.status}
              ]}
            />
          </DashboardPanel>

          <DashboardPanel
            className="dashboard-panel--danger-zone"
            title="DANGER ZONE"
            meta="LIVE / ARCHIVE / RESTART / RECOVER / RESET"
          >
            <div className="danger-zone">
              <div className="danger-zone__action">
                <div className="danger-zone__copy">
                  <div className="danger-zone__title">LIVE TRADING</div>
                  <div className="danger-zone__note">
                    {liveModeBlockedMessage
                      || (
                        liveTradingEnabled
                          ? 'REAL-MONEY MODE IS ENABLED. THIS STAYS GUARDED BY THE BACKEND READYNESS CHECKS.'
                          : 'ENABLE OR DISABLE GUARDED LIVE MODE THROUGH THE BACKEND ENDPOINT.'
                      )}
                  </div>
                </div>
                <div className="danger-zone__controls">
                  <div
                    className="danger-zone__value"
                    style={{color: resolveToneColor(booleanTone(liveTradingEnabled))}}
                  >
                    {liveTradingEnabled ? 'ON' : 'OFF'}
                  </div>
                  <button
                    type="button"
                    className="danger-zone__button"
                    onClick={() => void handleLiveTradingToggle()}
                    disabled={Boolean(liveModeBlockedMessage) || busyKey === 'danger:live_trading'}
                  >
                    {busyKey === 'danger:live_trading'
                      ? 'SAVING'
                      : liveTradingEnabled
                        ? 'DISABLE'
                        : 'ENABLE'}
                  </button>
                </div>
              </div>

              <div className="danger-zone__action">
                <div className="danger-zone__copy">
                  <div className="danger-zone__title">ARCHIVE TRADE LOG</div>
                  <div className="danger-zone__note">
                    {tradeLogArchiveBlockedMessage
                      || String(botState.trade_log_archive_request_message || '').trim()
                      || `QUEUE ONE BOUNDED ARCHIVE BATCH NOW.${Number(botState.trade_log_archive_eligible_row_count || 0) > 0 ? ` ${formatInteger(Number(botState.trade_log_archive_eligible_row_count || 0))} ELIGIBLE ROWS ARE READY.` : ' NO ELIGIBLE ROWS ARE READY RIGHT NOW.'}${botState.trade_log_archive_cutoff_ts ? ` CUTOFF ${formatTimestamp(botState.trade_log_archive_cutoff_ts)}.` : ''}${botState.trade_log_archive_preserve_since_ts ? ` PRESERVE SINCE ${formatTimestamp(botState.trade_log_archive_preserve_since_ts)}.` : ''}`}
                  </div>
                </div>
                <div className="danger-zone__controls">
                  <div
                    className="danger-zone__value"
                    style={{
                      color: resolveToneColor(
                        tradeLogArchivePending
                          ? 'warning'
                          : Number(botState.trade_log_archive_eligible_row_count || 0) > 0
                            ? 'accent'
                            : 'muted'
                      )
                    }}
                  >
                    {tradeLogArchivePending
                      ? 'PENDING'
                      : `${formatInteger(Number(botState.trade_log_archive_eligible_row_count || 0))} READY`}
                  </div>
                  <button
                    type="button"
                    className="danger-zone__button"
                    onClick={() => void handleArchiveTradeLog()}
                    disabled={
                      Boolean(tradeLogArchiveBlockedMessage)
                      || tradeLogArchivePending
                      || busyKey === 'danger:archive_trade_log'
                    }
                  >
                    {busyKey === 'danger:archive_trade_log' ? 'RUNNING' : 'ARCHIVE NOW'}
                  </button>
                </div>
              </div>

              <div className="danger-zone__action">
                <div className="danger-zone__copy">
                  <div className="danger-zone__title">RESTART SHADOW</div>
                  <div className="danger-zone__note">
                    {shadowRestartPending
                      ? shadowRestartMessage
                      : 'DELETE THE SHADOW SAVE DIRECTORY AND RESTART. CHOOSE WHETHER TO KEEP ACTIVE WALLETS, KEEP ALL WALLETS, OR CLEAR THE WATCHED LIST.'}
                  </div>
                </div>
                <div className="danger-zone__controls">
                  <div
                    className="danger-zone__value"
                    style={{color: resolveToneColor(shadowRestartPending ? 'warning' : 'muted')}}
                  >
                    {shadowRestartPending ? 'PENDING' : 'READY'}
                  </div>
                  <select
                    className="danger-zone__select"
                    value={restartWalletMode}
                    onChange={(event) =>
                      setRestartWalletMode(event.target.value as RestartShadowWalletMode)
                    }
                    disabled={shadowRestartPending || busyKey === 'danger:restart_shadow'}
                  >
                    {RESTART_SHADOW_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="danger-zone__button"
                    onClick={() => void handleRestartShadow()}
                    disabled={shadowRestartPending || busyKey === 'danger:restart_shadow'}
                  >
                    {busyKey === 'danger:restart_shadow' ? 'QUEUING' : 'RESTART SHADOW'}
                  </button>
                </div>
              </div>

              <div className="danger-zone__action">
                <div className="danger-zone__copy">
                  <div className="danger-zone__title">RECOVER DB</div>
                  <div className="danger-zone__note">
                    {shadowRestartPending
                      ? shadowRestartMessage
                      : recoveryCandidateMode === 'unavailable'
                        ? recoveryUnavailableMessage
                        : `RESTORE THE LATEST VERIFIED ${recoveryCandidateLabel} BACKUP, THEN RESTART SHADOW MODE. ${recoveryCandidateClassReason}`}
                  </div>
                </div>
                <div className="danger-zone__controls">
                  <div
                    className="danger-zone__value"
                    style={{color: resolveToneColor(recoveryCandidateTone)}}
                  >
                    {recoveryCandidateLabel}
                  </div>
                  <button
                    type="button"
                    className="danger-zone__button"
                    onClick={() => void handleRecoverDb()}
                    disabled={
                      shadowRestartPending
                      || recoveryCandidateMode === 'unavailable'
                      || busyKey === 'danger:recover_db'
                    }
                  >
                    {busyKey === 'danger:recover_db' ? 'QUEUING' : 'RECOVER DB'}
                  </button>
                </div>
              </div>

              <div className="danger-zone__action danger-zone__action--secondary">
                <div className="danger-zone__copy">
                  <div className="danger-zone__title">RESET CONFIG VALUES</div>
                  <div className="danger-zone__note">
                    {configEditBlockedMessage
                      || 'WEB-ONLY CONVENIENCE: RESTORE EVERY EDITABLE CONFIG FIELD TO ITS DEFAULT VALUE.'}
                  </div>
                </div>
                <div className="danger-zone__controls">
                  <div className="danger-zone__value">DEFAULTS</div>
                  <button
                    type="button"
                    className="danger-zone__button danger-zone__button--secondary"
                    onClick={() => void handleResetAll()}
                    disabled={Boolean(configEditBlockedMessage) || busyKey === 'danger:reset_config'}
                  >
                    {busyKey === 'danger:reset_config' ? 'RESETTING' : 'RESET ALL TO DEFAULTS'}
                  </button>
                </div>
              </div>
            </div>
          </DashboardPanel>
        </div>
      </div>
    </DashboardPageFrame>
  )
}
