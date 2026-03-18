import React, {useMemo} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {StatRow} from '../components/StatRow.js'
import {
  fit,
  fitRight,
  formatAdaptiveDollar,
  formatAdaptiveNumber,
  formatDisplayId,
  formatShortDateTime,
  formatDollar,
  formatNumber,
  formatPct,
  secondsAgo,
  timeUntil,
  shortAddress
} from '../format.js'
import {stackPanels} from '../responsive.js'
import {useTerminalSize} from '../terminal.js'
import {outcomeColor, positiveDollarColor, probabilityColor, theme} from '../theme.js'
import {useBotState} from '../useBotState.js'
import {useQuery} from '../useDb.js'
import {useEventStream} from '../useEventStream.js'
import {useTradeIdIndex} from '../useTradeIdIndex.js'

interface SummaryRow {
  real_money: number
  acted: number
  resolved: number
  wins: number
  total_pnl: number | null
  avg_confidence: number | null
  avg_size: number | null
}

interface DailyRow {
  real_money: number
  day: string
  pnl: number | null
}

interface PositionRow {
  row_key: string
  trade_id: string | null
  market_id: string
  side: string
  size_usd: number
  exit_size_usd: number | null
  entry_price: number
  confidence: number | null
  entered_at: number
  market_close_ts: number
  resolution_ts: number
  real_money: number
  question: string
  trader_address: string | null
  status: 'open' | 'waiting' | 'win' | 'lose' | 'exit'
  outcome: number | null
  pnl_usd: number | null
}

const EXECUTED_ENTRY_WHERE = `
skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
`

const REALIZED_CLOSE_TS_SQL = `COALESCE(exited_at, resolved_at, placed_at)`

const OPEN_EXECUTED_ENTRY_WHERE = `
${EXECUTED_ENTRY_WHERE}
AND COALESCE(remaining_entry_shares, actual_entry_shares, source_shares, 0) > 1e-9
AND COALESCE(remaining_entry_size_usd, actual_entry_size_usd, signal_size_usd, 0) > 1e-9
AND outcome IS NULL
AND exited_at IS NULL
`

const SUMMARY_SQL = `
SELECT
  real_money,
  SUM(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN 1 ELSE 0 END) AS acted,
  SUM(CASE WHEN ${EXECUTED_ENTRY_WHERE} AND (CASE WHEN real_money=0 THEN shadow_pnl_usd ELSE actual_pnl_usd END) IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
  SUM(CASE WHEN ${EXECUTED_ENTRY_WHERE} AND (CASE WHEN real_money=0 THEN shadow_pnl_usd ELSE actual_pnl_usd END) > 0 THEN 1 ELSE 0 END) AS wins,
  ROUND(SUM(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN COALESCE(shadow_pnl_usd, actual_pnl_usd) ELSE 0 END), 3) AS total_pnl,
  ROUND(AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN confidence END), 3) AS avg_confidence,
  ROUND(AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN actual_entry_size_usd END), 3) AS avg_size
FROM trade_log
GROUP BY real_money
`

const DAILY_SQL = `
SELECT
  real_money,
  strftime('%Y-%m-%d', datetime(${REALIZED_CLOSE_TS_SQL}, 'unixepoch', 'localtime')) AS day,
  ROUND(
    SUM(
      CASE
        WHEN real_money=0 THEN shadow_pnl_usd
        ELSE actual_pnl_usd
      END
    ),
    3
  ) AS pnl
FROM trade_log
WHERE ${EXECUTED_ENTRY_WHERE}
  AND (
    CASE
      WHEN real_money=0 THEN shadow_pnl_usd
      ELSE actual_pnl_usd
    END
  ) IS NOT NULL
GROUP BY real_money, day
ORDER BY day DESC
`

const SHADOW_OPEN_POSITIONS_SQL = `
SELECT
  ('o:' || tl.id) AS row_key,
  tl.trade_id,
  tl.market_id,
  tl.side,
  ROUND(COALESCE(tl.remaining_entry_size_usd, tl.actual_entry_size_usd), 3) AS size_usd,
  ROUND(
    CASE
      WHEN COALESCE(tl.remaining_entry_shares, 0) > 1e-9 THEN tl.remaining_entry_size_usd / tl.remaining_entry_shares
      ELSE tl.actual_entry_price
    END,
    3
  ) AS entry_price,
  ROUND(tl.confidence, 3) AS confidence,
  tl.placed_at AS entered_at,
  COALESCE(NULLIF(tl.market_close_ts, 0), 0) AS market_close_ts,
  COALESCE(NULLIF(tl.market_close_ts, 0), 0) AS resolution_ts,
  tl.real_money,
  COALESCE(tl.question, tl.market_id) AS question,
  tl.trader_address,
  'open' AS status,
  NULL AS outcome,
  NULL AS exit_size_usd,
  NULL AS pnl_usd
FROM trade_log tl
WHERE tl.real_money = 0
  AND ${OPEN_EXECUTED_ENTRY_WHERE}
ORDER BY tl.placed_at DESC, tl.id DESC
`

const LIVE_POSITIONS_SQL = `
SELECT
  ('p:' || p.market_id || ':' || p.token_id || ':' || p.entered_at || ':' || p.real_money) AS row_key,
  COALESCE(
    (
      SELECT tl.trade_id
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.trade_id
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    )
  ) AS trade_id,
  p.market_id,
  p.side,
  ROUND(p.size_usd, 3) AS size_usd,
  ROUND(
    CASE
      WHEN p.avg_price > 0 THEN p.avg_price
      ELSE COALESCE(
        (
          SELECT tl.actual_entry_price
          FROM trade_log tl
          WHERE tl.market_id = p.market_id
            AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
            AND ${EXECUTED_ENTRY_WHERE}
            AND tl.placed_at <= p.entered_at
          ORDER BY tl.placed_at DESC, tl.id DESC
          LIMIT 1
        ),
        (
          SELECT tl.actual_entry_price
          FROM trade_log tl
          WHERE tl.market_id = p.market_id
            AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
            AND ${EXECUTED_ENTRY_WHERE}
          ORDER BY tl.placed_at DESC, tl.id DESC
          LIMIT 1
        ),
        0
      )
    END,
    3
  ) AS entry_price,
  ROUND(
    COALESCE(
      (
        SELECT tl.confidence
        FROM trade_log tl
        WHERE tl.market_id = p.market_id
          AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
          AND ${EXECUTED_ENTRY_WHERE}
          AND tl.placed_at <= p.entered_at
        ORDER BY tl.placed_at DESC, tl.id DESC
        LIMIT 1
      ),
      (
        SELECT tl.confidence
        FROM trade_log tl
        WHERE tl.market_id = p.market_id
          AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
          AND ${EXECUTED_ENTRY_WHERE}
        ORDER BY tl.placed_at DESC, tl.id DESC
        LIMIT 1
      )
    ),
    3
  ) AS confidence,
  p.entered_at,
  p.real_money,
  COALESCE(
    (
      SELECT tl.question
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.question
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    p.market_id
  ) AS question,
  COALESCE(
    (
      SELECT tl.trader_address
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.trader_address
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    )
  ) AS trader_address,
  COALESCE(
    (
      SELECT tl.market_close_ts
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
        AND tl.market_close_ts IS NOT NULL
        AND tl.market_close_ts > 0
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.market_close_ts
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
        AND tl.market_close_ts IS NOT NULL
        AND tl.market_close_ts > 0
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    0
  ) AS market_close_ts,
  COALESCE(
    (
      SELECT tl.market_close_ts
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
        AND tl.market_close_ts IS NOT NULL
        AND tl.market_close_ts > 0
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.market_close_ts
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
        AND tl.market_close_ts IS NOT NULL
        AND tl.market_close_ts > 0
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    0
  ) AS resolution_ts,
  'open' AS status,
  NULL AS outcome,
  NULL AS exit_size_usd,
  NULL AS pnl_usd
FROM positions p
ORDER BY p.entered_at DESC
`

const RESOLVED_POSITIONS_SQL = `
SELECT
  ('t:' || tl.id) AS row_key,
  tl.trade_id,
  tl.market_id,
  tl.side,
  ROUND(tl.actual_entry_size_usd, 3) AS size_usd,
  ROUND(tl.actual_entry_price, 3) AS entry_price,
  ROUND(tl.confidence, 3) AS confidence,
  tl.placed_at AS entered_at,
  COALESCE(NULLIF(tl.market_close_ts, 0), tl.resolved_at, tl.placed_at) AS market_close_ts,
  COALESCE(NULLIF(tl.exited_at, 0), NULLIF(tl.resolved_at, 0), NULLIF(tl.market_close_ts, 0), tl.placed_at) AS resolution_ts,
  tl.real_money,
  COALESCE(tl.question, tl.market_id) AS question,
  tl.trader_address,
  CASE
    WHEN tl.exited_at IS NOT NULL THEN 'exit'
    WHEN (CASE WHEN tl.real_money = 0 THEN tl.shadow_pnl_usd ELSE tl.actual_pnl_usd END) > 0 THEN 'win'
    ELSE 'lose'
  END AS status,
  tl.outcome,
  ROUND(tl.exit_size_usd, 3) AS exit_size_usd,
  ROUND(CASE WHEN tl.real_money = 0 THEN tl.shadow_pnl_usd ELSE tl.actual_pnl_usd END, 3) AS pnl_usd
FROM trade_log tl
WHERE ${EXECUTED_ENTRY_WHERE}
  AND (CASE WHEN tl.real_money = 0 THEN tl.shadow_pnl_usd ELSE tl.actual_pnl_usd END) IS NOT NULL
ORDER BY COALESCE(NULLIF(tl.exited_at, 0), NULLIF(tl.resolved_at, 0), NULLIF(tl.market_close_ts, 0), tl.placed_at) DESC, tl.id DESC
`

interface PositionsLayout {
  idWidth: number
  userWidth: number
  actionWidth: number
  sideWidth: number
  entryWidth: number
  sharesWidth: number
  sizeWidth: number
  toWinWidth: number
  profitWidth: number
  confidenceWidth: number
  resolutionWidth: number
  ttrWidth: number
  ageWidth: number
  questionWidth: number
  showId: boolean
  showUser: boolean
}

interface PositionRowBudget {
  current: number
  past: number
}

interface RenderPositionsOptions {
  showStatus?: boolean
  showTtr?: boolean
}

export type PerfBox = 'summary' | 'daily' | 'current' | 'past'

interface PerformanceProps {
  currentScrollOffset: number
  pastScrollOffset: number
  activePane: 'current' | 'past'
  selectedBox: PerfBox
  dailyDetailOpen: boolean
  dailyDetailScrollOffset: number
}

interface DailyPnlEntry {
  day: string
  pnl: number
  label: string
}

interface DailyQueueLayout {
  leftWidth: number
  rightWidth: number
}

function getPositionsLayout(width: number): PositionsLayout {
  const showId = width >= 132
  const showUser = width >= 110
  const idWidth = showId ? 6 : 0
  const userWidth = showUser ? (width >= 120 ? 14 : 10) : 0
  const actionWidth = 4
  const sideWidth = 4
  const entryWidth = 5
  const sharesWidth = 7
  const sizeWidth = 8
  const toWinWidth = 8
  const profitWidth = 8
  const confidenceWidth = 6
  const ttrWidth = 11
  const ageWidth = 8
  const gaps = 11 + (showId ? 1 : 0) + (showUser ? 1 : 0)
  const fixedStatic =
    idWidth +
    userWidth +
    actionWidth +
    sideWidth +
    entryWidth +
    sharesWidth +
    sizeWidth +
    toWinWidth +
    profitWidth +
    confidenceWidth +
    ttrWidth +
    ageWidth
  const variableWidth = Math.max(24, width - fixedStatic - gaps)
  const questionMinWidth = 14
  const resolutionMinWidth = 10
  let resolutionWidth = Math.max(
    resolutionMinWidth,
    Math.min(20, Math.floor(variableWidth * 0.29))
  )
  let questionWidth = variableWidth - resolutionWidth
  if (questionWidth < questionMinWidth) {
    questionWidth = questionMinWidth
    resolutionWidth = Math.max(resolutionMinWidth, variableWidth - questionWidth)
  }

  return {
    idWidth,
    userWidth,
    actionWidth,
    sideWidth,
    entryWidth,
    sharesWidth,
    sizeWidth,
    toWinWidth,
    profitWidth,
    confidenceWidth,
    resolutionWidth,
    ttrWidth,
    ageWidth,
    questionWidth,
    showId,
    showUser
  }
}

function getPositionPaneMetrics(terminalHeight: number, stacked: boolean) {
  const outerReserve = 10
  const statsHeight = 9
  const dailyHeight = 9
  const topRowHeight = stacked ? statsHeight + 1 + dailyHeight : Math.max(statsHeight, dailyHeight)
  const sectionGaps = stacked ? 3 : 2
  const availableHeight = Math.max(
    12,
    terminalHeight - outerReserve - topRowHeight - sectionGaps
  )
  const paneHeight = Math.max(6, Math.floor((availableHeight - 1) / 2))
  const visibleRows = Math.max(1, paneHeight - 5)

  return {paneHeight, visibleRows}
}

function getDailyPanelContentWidth(terminalWidth: number, stacked: boolean): number {
  const minContentWidth = 24
  return stacked
    ? Math.max(minContentWidth, terminalWidth - 10)
    : Math.max(minContentWidth, Math.floor((terminalWidth - 15) / 2))
}

function getDailyQueueLayout(contentWidth: number, valueWidth: number): DailyQueueLayout {
  const minLeftWidth = 10
  const minRightWidth = Math.max(12, valueWidth)
  const usableWidth = Math.max(minLeftWidth + minRightWidth, contentWidth - 3)
  const sideWidth = Math.max(minLeftWidth, Math.floor(usableWidth / 2))

  return {
    leftWidth: sideWidth,
    rightWidth: sideWidth
  }
}

function DailyPnlPreviewChart({entries, width}: {entries: DailyPnlEntry[]; width: number}) {
  const levelCount = 3
  const gapWidth = entries.length <= 5 ? 2 : 1
  const totalGapWidth = Math.max(0, entries.length - 1) * gapWidth
  const columnWidth = Math.max(2, Math.floor((Math.max(width, entries.length * 3) - totalGapWidth) / Math.max(entries.length, 1)))
  const maxAbsPnl = Math.max(1, ...entries.map((entry) => Math.abs(entry.pnl)))
  const heights = entries.map((entry) => Math.round((Math.abs(entry.pnl) / maxAbsPnl) * levelCount))

  const renderRow = (rowIndex: number, negative: boolean) => (
    <InkBox width="100%">
      {entries.map((entry, index) => {
        const filled =
          negative
            ? entry.pnl < 0 && heights[index] >= rowIndex
            : entry.pnl > 0 && heights[index] >= rowIndex
        const color = negative ? theme.red : theme.green
        return (
          <React.Fragment key={`${entry.day}-${negative ? 'neg' : 'pos'}-${rowIndex}`}>
            <InkBox width={columnWidth}>
              <Text color={filled ? color : undefined}>{filled ? '█'.repeat(columnWidth) : ' '.repeat(columnWidth)}</Text>
            </InkBox>
            {index < entries.length - 1 ? <Text>{' '.repeat(gapWidth)}</Text> : null}
          </React.Fragment>
        )
      })}
    </InkBox>
  )

  return (
    <InkBox flexDirection="column">
      {Array.from({length: levelCount}, (_, index) => renderRow(levelCount - index, false))}
      <InkBox width="100%">
        {entries.map((entry, index) => (
          <React.Fragment key={`${entry.day}-axis`}>
            <InkBox width={columnWidth}>
              <Text color={theme.dim}>{'─'.repeat(columnWidth)}</Text>
            </InkBox>
            {index < entries.length - 1 ? <Text>{' '.repeat(gapWidth)}</Text> : null}
          </React.Fragment>
        ))}
      </InkBox>
      {Array.from({length: levelCount}, (_, index) => renderRow(index + 1, true))}
      <InkBox width="100%">
        {entries.map((entry, index) => {
          const label = columnWidth >= 5 ? entry.day.slice(5) : entry.day.slice(8)
          return (
            <React.Fragment key={`${entry.day}-label`}>
              <InkBox width={columnWidth}>
                <Text color={theme.dim}>{fit(label, columnWidth)}</Text>
              </InkBox>
              {index < entries.length - 1 ? <Text>{' '.repeat(gapWidth)}</Text> : null}
            </React.Fragment>
          )
        })}
      </InkBox>
    </InkBox>
  )
}

export function Performance({
  currentScrollOffset,
  pastScrollOffset,
  activePane,
  selectedBox,
  dailyDetailOpen,
  dailyDetailScrollOffset
}: PerformanceProps) {
  const terminal = useTerminalSize()
  const stacked = stackPanels(terminal.width)
  const rows = useQuery<SummaryRow>(SUMMARY_SQL)
  const daily = useQuery<DailyRow>(DAILY_SQL)
  const shadowOpenPositions = useQuery<PositionRow>(SHADOW_OPEN_POSITIONS_SQL)
  const livePositions = useQuery<PositionRow>(LIVE_POSITIONS_SQL)
  const resolvedPositions = useQuery<PositionRow>(RESOLVED_POSITIONS_SQL)
  const events = useEventStream(1000)
  const {lookup: tradeIdLookup} = useTradeIdIndex()
  const botState = useBotState(1000)
  const positionsTableWidth = Math.max(72, terminal.width - 10)
  const positionsLayout = getPositionsLayout(positionsTableWidth)
  const nowTs = Date.now() / 1000
  const activeMode = botState.mode === 'live' ? 'live' : 'shadow'
  const activeRealMoney = activeMode === 'live' ? 1 : 0

  const shadow = rows.find((row) => row.real_money === 0)
  const live = rows.find((row) => row.real_money === 1)
  const activeSummary = activeMode === 'live' ? live : shadow
  const activeTitle = activeMode === 'live' ? 'Live' : 'Tracker'
  const usernames = useMemo(() => {
    const lookup = new Map<string, string>()
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index]
      const wallet = event.trader?.trim().toLowerCase()
      const username = event.username?.trim()
      if (!wallet || !username || lookup.has(wallet)) {
        continue
      }
      lookup.set(wallet, username)
    }
    return lookup
  }, [events])
  const activeOpenPositions = useMemo(
    () =>
      activeMode === 'live'
        ? livePositions.filter((row) => row.real_money === activeRealMoney)
        : shadowOpenPositions,
    [activeMode, activeRealMoney, livePositions, shadowOpenPositions]
  )
  const activeResolvedPositions = useMemo(
    () => resolvedPositions.filter((row) => row.real_money === activeRealMoney),
    [activeRealMoney, resolvedPositions]
  )
  const currentPositions = useMemo(
    () =>
      activeOpenPositions
        .filter((row) => row.market_close_ts <= 0 || row.market_close_ts > nowTs)
        .sort((left, right) => right.entered_at - left.entered_at),
    [activeOpenPositions, nowTs]
  )
  const currentPositionsTotal = useMemo(
    () => currentPositions.reduce((sum, row) => sum + (row.size_usd || 0), 0),
    [currentPositions]
  )
  const waitingPositions = useMemo(
    () =>
      activeOpenPositions
        .filter((row) => row.market_close_ts > 0 && row.market_close_ts <= nowTs)
        .map((row) => ({...row, status: 'waiting' as const})),
    [activeOpenPositions, nowTs]
  )
  const waitingPositionsTotal = useMemo(
    () => waitingPositions.reduce((sum, row) => sum + (row.size_usd || 0), 0),
    [waitingPositions]
  )
  const pastPositions = useMemo(
    () =>
      [...activeResolvedPositions, ...waitingPositions].sort(
        (a, b) =>
          Math.max(b.resolution_ts || 0, b.market_close_ts || 0, b.entered_at || 0) -
          Math.max(a.resolution_ts || 0, a.market_close_ts || 0, a.entered_at || 0)
      ),
    [activeResolvedPositions, waitingPositions]
  )
  const activeDailyRows = useMemo(
    () => daily.filter((row) => row.real_money === activeRealMoney),
    [activeRealMoney, daily]
  )
  const dailyEntries = useMemo<DailyPnlEntry[]>(
    () =>
      activeDailyRows.map((row) => {
        const pnl = Number(row.pnl || 0)
        return {
          day: row.day,
          pnl,
          label: formatDollar(pnl)
        }
      }),
    [activeDailyRows]
  )
  const dailyPreviewEntries = useMemo(
    () => dailyEntries.slice(0, 7).reverse(),
    [dailyEntries]
  )
  const dailyValueWidth = useMemo(
    () => dailyEntries.reduce((max, row) => Math.max(max, row.label.length), 10),
    [dailyEntries]
  )
  const dailyPanelContentWidth = useMemo(
    () => getDailyPanelContentWidth(terminal.width, stacked),
    [stacked, terminal.width]
  )
  const paneMetrics = getPositionPaneMetrics(terminal.height, stacked)
  const currentMaxOffset = Math.max(0, currentPositions.length - paneMetrics.visibleRows)
  const pastMaxOffset = Math.max(0, pastPositions.length - paneMetrics.visibleRows)
  const effectiveCurrentScrollOffset = Math.min(currentScrollOffset, currentMaxOffset)
  const effectivePastScrollOffset = Math.min(pastScrollOffset, pastMaxOffset)
  const visibleCurrentPositions = currentPositions.slice(
    effectiveCurrentScrollOffset,
    effectiveCurrentScrollOffset + paneMetrics.visibleRows
  )
  const visiblePastPositions = pastPositions.slice(
    effectivePastScrollOffset,
    effectivePastScrollOffset + paneMetrics.visibleRows
  )
  const shadowBalance =
    botState.mode === 'shadow' && botState.bankroll_usd != null ? botState.bankroll_usd : null
  const liveBalance =
    botState.mode === 'live' && botState.bankroll_usd != null ? botState.bankroll_usd : null
  const activeBalance = activeMode === 'live' ? liveBalance : shadowBalance
  const modalBackground = terminal.backgroundColor || theme.modalBackground
  const detailModalWidth = Math.max(60, Math.min(terminal.width - 8, terminal.wide ? 110 : 88))
  const detailModalContentWidth = Math.max(40, detailModalWidth - 4)
  const detailVisibleRows = Math.max(12, Math.min(21, terminal.height - 12))
  const detailMaxOffset = Math.max(0, dailyEntries.length - detailVisibleRows)
  const detailOffset = Math.min(dailyDetailScrollOffset, detailMaxOffset)
  const visibleDetailEntries = dailyEntries.slice(detailOffset, detailOffset + detailVisibleRows)
  const paddedDetailEntries = useMemo(
    () =>
      Array.from({length: detailVisibleRows}, (_, index) => visibleDetailEntries[index] ?? null),
    [detailVisibleRows, visibleDetailEntries]
  )
  const detailRangeLabel = dailyEntries.length
    ? `${detailOffset + 1}-${Math.min(detailOffset + visibleDetailEntries.length, dailyEntries.length)}/${dailyEntries.length}`
    : '0/0'
  const detailQueueLayout = useMemo(
    () => getDailyQueueLayout(detailModalContentWidth, dailyValueWidth),
    [detailModalContentWidth, dailyValueWidth]
  )

  const renderPositionsTable = (
    rowsToRender: PositionRow[],
    {showStatus = false, showTtr = true}: RenderPositionsOptions = {}
  ) => {
    const trailingWidth = positionsLayout.ttrWidth
    const trailingDelta = trailingWidth - positionsLayout.ttrWidth
    const questionWidth = Math.max(14, positionsLayout.questionWidth - trailingDelta)
    const resolutionWidth = positionsLayout.resolutionWidth

    return (
      <>
        <InkBox width="100%">
          {positionsLayout.showId ? (
            <>
              <Text color={theme.dim}>{fitRight('ID', positionsLayout.idWidth)}</Text>
              <Text color={theme.dim}> </Text>
            </>
          ) : null}
          {positionsLayout.showUser ? (
            <>
              <Text color={theme.dim}>{fit('FROM USER', positionsLayout.userWidth)}</Text>
              <Text color={theme.dim}> </Text>
            </>
          ) : null}
          <Text color={theme.dim}>{fit('IN MARKET', questionWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fitRight('AGE', positionsLayout.ageWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fit('ACTN', positionsLayout.actionWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fit('SIDE', positionsLayout.sideWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fitRight('ENTRY', positionsLayout.entryWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fitRight('SHARES', positionsLayout.sharesWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fitRight('TOTAL', positionsLayout.sizeWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fitRight('TO WIN', positionsLayout.toWinWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fitRight('PROFIT', positionsLayout.profitWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fitRight('CONF', positionsLayout.confidenceWidth)}</Text>
          <Text color={theme.dim}> </Text>
          <Text color={theme.dim}>{fitRight('RESOLUTION', resolutionWidth)}</Text>
          {showTtr || showStatus ? (
            <>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>
                {fitRight(showStatus ? 'STATUS' : 'TTR', trailingWidth)}
              </Text>
            </>
          ) : null}
        </InkBox>
        <InkBox flexDirection="column" marginTop={1}>
          {rowsToRender.map((row) => {
            const sideColor = outcomeColor(row.side)
            const displayId = row.trade_id ? tradeIdLookup.get(row.trade_id) ?? null : null
            const username = row.trader_address ? usernames.get(row.trader_address.toLowerCase()) : undefined
            const userText = username || shortAddress(row.trader_address || '-')
            const displayIdText = formatDisplayId(displayId, positionsLayout.idWidth)
            const actionText = row.status === 'exit' ? 'SELL' : 'BUY'
            const actionColor = outcomeColor(actionText)
            const entryColor = row.entry_price > 0 ? probabilityColor(row.entry_price) : theme.dim
            const confidenceColor =
              row.confidence != null ? probabilityColor(row.confidence) : theme.dim
            const resolutionTs = row.resolution_ts || row.market_close_ts
            const resolutionPassed = row.market_close_ts > 0 && row.market_close_ts <= nowTs
            const resolutionColor = row.status === 'waiting' ? theme.red : theme.dim
            const shares = row.entry_price > 0 ? row.size_usd / row.entry_price : null
            const toWin =
              row.status === 'exit'
                ? row.exit_size_usd
                : row.status === 'lose'
                  ? 0
                  : shares != null
                    ? shares
                    : null
            const profit =
              row.status === 'win' || row.status === 'lose' || row.status === 'exit'
                ? (row.pnl_usd ?? null)
                : toWin != null
                  ? toWin - row.size_usd
                  : null
            const statusText =
              row.status === 'win'
                ? 'win'
                : row.status === 'lose'
                  ? 'lose'
                  : row.status === 'exit'
                    ? profit != null && profit > 0
                      ? 'exit up'
                      : profit != null && profit < 0
                        ? 'exit down'
                        : 'exited'
                    : 'waiting'
            const statusColor =
              row.status === 'win'
                ? theme.green
                : row.status === 'lose'
                  ? theme.red
                  : row.status === 'exit'
                    ? profit != null && profit > 0
                      ? theme.green
                      : profit != null && profit < 0
                        ? theme.red
                        : theme.yellow
                    : theme.yellow
            const toWinColor =
              toWin != null ? positiveDollarColor(toWin, 100) : theme.dim
            const profitColor =
              profit == null
                ? theme.dim
                : profit < 0
                  ? theme.red
                  : positiveDollarColor(profit, 100)

            return (
              <InkBox key={row.row_key} width="100%">
                {positionsLayout.showId ? (
                  <>
                    <Text color={theme.dim}>{fitRight(displayIdText, positionsLayout.idWidth)}</Text>
                    <Text> </Text>
                  </>
                ) : null}
                {positionsLayout.showUser ? (
                <>
                  <Text color={username ? theme.white : theme.dim}>{fit(userText, positionsLayout.userWidth)}</Text>
                  <Text> </Text>
                </>
              ) : null}
                <Text>{fit(row.question || row.market_id, questionWidth)}</Text>
                <Text> </Text>
                <Text color={theme.dim}>{fitRight(secondsAgo(row.entered_at), positionsLayout.ageWidth)}</Text>
                <Text> </Text>
                <Text color={actionColor}>{fit(actionText, positionsLayout.actionWidth)}</Text>
                <Text> </Text>
                <Text color={sideColor}>{fit(row.side.toUpperCase(), positionsLayout.sideWidth)}</Text>
                <Text> </Text>
                <Text color={entryColor}>{fitRight(formatNumber(row.entry_price), positionsLayout.entryWidth)}</Text>
                <Text> </Text>
                <Text>
                  {fitRight(
                    shares != null
                      ? formatAdaptiveNumber(shares, positionsLayout.sharesWidth)
                      : '-',
                    positionsLayout.sharesWidth
                  )}
                </Text>
                <Text> </Text>
                <Text>{fitRight(formatAdaptiveDollar(row.size_usd, positionsLayout.sizeWidth), positionsLayout.sizeWidth)}</Text>
                <Text> </Text>
                <Text color={toWinColor}>
                  {fitRight(
                    toWin != null
                      ? formatAdaptiveDollar(toWin, positionsLayout.toWinWidth)
                      : '-',
                    positionsLayout.toWinWidth
                  )}
                </Text>
                <Text> </Text>
                <Text color={profitColor}>
                  {fitRight(
                    profit != null
                      ? formatAdaptiveDollar(profit, positionsLayout.profitWidth)
                      : '-',
                    positionsLayout.profitWidth
                  )}
                </Text>
                <Text> </Text>
                <Text color={confidenceColor}>{fitRight(formatPct(row.confidence, 1), positionsLayout.confidenceWidth)}</Text>
                <Text> </Text>
                <Text color={resolutionColor}>{fitRight(formatShortDateTime(resolutionTs), resolutionWidth)}</Text>
                {showTtr || showStatus ? (
                  <>
                    <Text> </Text>
                    <Text color={showStatus ? statusColor : resolutionColor}>
                      {fitRight(
                        showStatus ? statusText : timeUntil(row.market_close_ts),
                        trailingWidth
                      )}
                    </Text>
                  </>
                ) : null}
              </InkBox>
            )
          })}
        </InkBox>
      </>
    )
  }

  return (
    <InkBox flexDirection="column" width="100%">
      <InkBox flexDirection={stacked ? 'column' : 'row'}>
        <Box title={activeTitle} width={stacked ? '100%' : '50%'} accent={selectedBox === 'summary'}>
          <StatRow label="Total P&L" value={formatDollar(activeSummary?.total_pnl)} color={(activeSummary?.total_pnl || 0) >= 0 ? theme.green : theme.red} />
          <StatRow
            label="Current balance"
            value={activeBalance == null ? '-' : `$${activeBalance.toFixed(3)}`}
            color={activeBalance != null ? theme.white : theme.dim}
          />
          <StatRow label="Win rate" value={activeSummary ? formatPct(activeSummary.resolved ? activeSummary.wins / activeSummary.resolved : 0) : '-'} />
          <StatRow label="Resolved" value={String(activeSummary?.resolved || 0)} />
          <StatRow label="Avg confidence" value={formatPct(activeSummary?.avg_confidence)} />
          <StatRow label="Avg total" value={formatDollar(activeSummary?.avg_size)} />
        </Box>
        {!stacked ? <InkBox width={1} /> : <InkBox height={1} />}
        <Box title={`Daily ${activeTitle} P&L`} width={stacked ? '100%' : '50%'} accent={selectedBox === 'daily'}>
          {dailyPreviewEntries.length ? (
            <DailyPnlPreviewChart entries={dailyPreviewEntries} width={dailyPanelContentWidth} />
          ) : (
            <Text color={theme.dim}>{`No resolved ${activeTitle.toLowerCase()} trades yet.`}</Text>
          )}
        </Box>
      </InkBox>

      <InkBox marginTop={1} flexDirection="column" flexGrow={1}>
        <InkBox flexGrow={1}>
          <Box
            title={`Current Positions (${currentPositions.length}, holding $${currentPositionsTotal.toFixed(3)})`}
            height="100%"
            accent={selectedBox === 'current'}
          >
            {visibleCurrentPositions.length ? (
              renderPositionsTable(visibleCurrentPositions)
            ) : (
              <Text color={theme.dim}>No open positions right now.</Text>
            )}
          </Box>
        </InkBox>

        <InkBox height={1} />

        <InkBox flexGrow={1}>
          <Box
            title={`Past Positions (${pastPositions.length}, waiting for $${waitingPositionsTotal.toFixed(2)})`}
            height="100%"
            accent={selectedBox === 'past'}
          >
            {visiblePastPositions.length ? (
              renderPositionsTable(visiblePastPositions, {showStatus: true, showTtr: false})
            ) : (
              <Text color={theme.dim}>No past positions yet.</Text>
            )}
          </Box>
        </InkBox>
      </InkBox>

      {dailyDetailOpen ? (
        <InkBox position="absolute" width="100%" height="100%" justifyContent="center" alignItems="center">
          <InkBox borderStyle="round" borderColor={theme.accent} flexDirection="column" width={detailModalWidth}>
            <InkBox width="100%">
              <Text color={theme.accent} backgroundColor={modalBackground} bold>
                {` ${fit(`Daily ${activeTitle} P&L Detail`, Math.max(1, detailModalContentWidth - detailRangeLabel.length - 1))}`}
              </Text>
              <Text backgroundColor={modalBackground}> </Text>
              <Text color={theme.dim} backgroundColor={modalBackground}>
                {`${fitRight(detailRangeLabel, detailRangeLabel.length)} `}
              </Text>
            </InkBox>
            <Text backgroundColor={modalBackground}>{' '.repeat(detailModalWidth - 2)}</Text>
            {paddedDetailEntries.map((row, index) => (
              <InkBox key={`detail-${row?.day || `empty-${index}`}`} width="100%">
                <Text color={row ? theme.white : theme.dim} backgroundColor={modalBackground}>
                  {` ${fitRight(row?.day || '', detailQueueLayout.leftWidth)}`}
                </Text>
                <Text backgroundColor={modalBackground}> </Text>
                <Text color={theme.dim} backgroundColor={modalBackground}>│</Text>
                <Text backgroundColor={modalBackground}> </Text>
                <Text color={row ? (row.pnl >= 0 ? theme.green : theme.red) : theme.dim} backgroundColor={modalBackground}>
                  {`${fit(row?.label || '', detailQueueLayout.rightWidth)} `}
                </Text>
              </InkBox>
            ))}
          </InkBox>
        </InkBox>
      ) : null}
    </InkBox>
  )
}
