import React, {useEffect, useMemo} from 'react'
import {Box as InkBox, Text} from 'ink'
import {BarSparkline} from '../components/BarSparkline.js'
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
  truncate,
  terminalHyperlink,
  timeUntil,
  wrapText,
  shortAddress
} from '../format.js'
import {stackPanels} from '../responsive.js'
import {useTerminalSize} from '../terminal.js'
import {centeredGradientColor, outcomeColor, positiveDollarColor, probabilityColor, selectionBackgroundColor, theme} from '../theme.js'
import {useBotState} from '../useBotState.js'
import {useQuery} from '../useDb.js'
import {useEventStream} from '../useEventStream.js'
import {useTradeIdIndex} from '../useTradeIdIndex.js'
import {
  editablePositionStatuses,
  type PositionManualEditRow,
  type PositionManualEditStatus,
  type TradeLogManualEditRow
} from '../positionEditor.js'

export interface PositionRow {
  row_key: string
  source_kind: 'trade_log' | 'position'
  source_trade_log_id: number | null
  trade_id: string | null
  market_id: string
  market_url: string | null
  side: string
  token_id: string
  size_usd: number
  exit_size_usd: number | null
  entry_price: number
  shares: number | null
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

export type PerfPositionEditField = 'entry' | 'shares' | 'total' | 'status'

export interface PerfPositionEditState {
  row: PositionRow
  pane: 'current' | 'past'
  selectedField: PerfPositionEditField
  editingField: Exclude<PerfPositionEditField, 'status'> | null
  draftEntry: string
  draftShares: string
  draftTotal: string
  draftStatus: PositionManualEditStatus
  statusMessage?: string
  statusTone?: 'info' | 'success' | 'error'
}

export interface PerformanceSelectionMeta {
  currentCount: number
  pastCount: number
  selectedCurrentRow: PositionRow | null
  selectedPastRow: PositionRow | null
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

const SHADOW_OPEN_POSITIONS_SQL = `
SELECT
  ('o:' || tl.id) AS row_key,
  'trade_log' AS source_kind,
  tl.id AS source_trade_log_id,
  tl.trade_id,
  tl.market_id,
  tl.market_url,
  tl.side,
  COALESCE(tl.token_id, '') AS token_id,
  ROUND(COALESCE(tl.remaining_entry_size_usd, tl.actual_entry_size_usd), 3) AS size_usd,
  ROUND(COALESCE(tl.remaining_entry_shares, tl.actual_entry_shares, tl.source_shares), 6) AS shares,
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
  'position' AS source_kind,
  COALESCE(
    (
      SELECT tl.id
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.id
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    )
  ) AS source_trade_log_id,
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
  COALESCE(
    (
      SELECT tl.market_url
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.market_url
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND ${EXECUTED_ENTRY_WHERE}
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    )
  ) AS market_url,
  p.side,
  COALESCE(p.token_id, '') AS token_id,
  ROUND(p.size_usd, 3) AS size_usd,
  ROUND(
    CASE
      WHEN p.avg_price > 0 THEN p.size_usd / p.avg_price
      ELSE COALESCE(
        (
          SELECT tl.actual_entry_shares
          FROM trade_log tl
          WHERE tl.market_id = p.market_id
            AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
            AND ${EXECUTED_ENTRY_WHERE}
            AND tl.placed_at <= p.entered_at
          ORDER BY tl.placed_at DESC, tl.id DESC
          LIMIT 1
        ),
        (
          SELECT tl.actual_entry_shares
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
    6
  ) AS shares,
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
  'trade_log' AS source_kind,
  tl.id AS source_trade_log_id,
  tl.trade_id,
  tl.market_id,
  tl.market_url,
  tl.side,
  COALESCE(tl.token_id, '') AS token_id,
  ROUND(tl.actual_entry_size_usd, 3) AS size_usd,
  ROUND(tl.actual_entry_shares, 6) AS shares,
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

const TRADE_LOG_MANUAL_EDITS_SQL = `
SELECT
  trade_log_id,
  entry_price,
  shares,
  size_usd,
  status,
  updated_at
FROM trade_log_manual_edits
`

const POSITION_MANUAL_EDITS_SQL = `
SELECT
  market_id,
  token_id,
  LOWER(side) AS side,
  real_money,
  entry_price,
  shares,
  size_usd,
  status,
  updated_at
FROM position_manual_edits
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
  profitScaleRows?: PositionRow[]
}

export type PerfBox = 'summary' | 'daily' | 'current' | 'past'

interface PerformanceProps {
  currentScrollOffset: number
  pastScrollOffset: number
  activePane: 'current' | 'past'
  selectedBox: PerfBox
  dailyDetailOpen: boolean
  dailyDetailScrollOffset: number
  editState?: PerfPositionEditState | null
  onCurrentScrollOffsetChange?: (offset: number) => void
  onPastScrollOffsetChange?: (offset: number) => void
  onDailyDetailScrollOffsetChange?: (offset: number) => void
  onSelectionMetaChange?: (meta: PerformanceSelectionMeta) => void
}

interface DailyPnlEntry {
  day: string
  pnl: number
  label: string
}

interface DailyQueueLayout {
  dateWidth: number
  barWidth: number
  valueWidth: number
}

interface ComputedSummary {
  acted: number
  resolved: number
  wins: number
  total_pnl: number | null
  avg_confidence: number | null
  avg_size: number | null
}

function getPositionsLayout(width: number): PositionsLayout {
  const showId = width >= 132
  const showUser = width >= 110
  const idWidth = showId ? 6 : 0
  const userWidth = showUser ? (width >= 120 ? 14 : 10) : 0
  const actionWidth = 4
  const sideWidth = 6
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
  const paneHeight = Math.max(6, Math.floor((availableHeight - 3) / 2))
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
  const dateWidth = 14
  const resolvedValueWidth = Math.max(12, valueWidth)
  const minBarWidth = 9
  const rawBarWidth = Math.max(minBarWidth, contentWidth - dateWidth - resolvedValueWidth - 2)
  const centeredBarWidth = rawBarWidth % 2 === 0 ? rawBarWidth - 1 : rawBarWidth

  return {
    dateWidth,
    barWidth: Math.max(minBarWidth, centeredBarWidth),
    valueWidth: resolvedValueWidth
  }
}

function parseHourlyBucket(bucket: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):00$/.exec(String(bucket || '').trim())
  if (!match) {
    return null
  }

  const [, year, month, day, hour] = match
  return new Date(
    Number.parseInt(year, 10),
    Number.parseInt(month, 10) - 1,
    Number.parseInt(day, 10),
    Number.parseInt(hour, 10),
    0,
    0,
    0
  )
}

function formatHourlyBucketKey(date: Date): string {
  const year = String(date.getFullYear()).padStart(4, '0')
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  return `${year}-${month}-${day} ${hour}:00`
}

function floorToHour(date: Date): Date {
  const bucketDate = new Date(date.getTime())
  bucketDate.setMinutes(0, 0, 0)
  return bucketDate
}

function formatHourlyBucketLabel(bucket: string, compact = false): string {
  const bucketDate = parseHourlyBucket(bucket)
  if (!bucketDate) {
    const [datePart, timePart = ''] = String(bucket || '').split(' ')
    const shortDate = datePart.length >= 10 ? datePart.slice(5) : datePart
    const shortTime = timePart.slice(0, 5)

    if (compact) {
      return shortTime || shortDate
    }
    if (shortDate && shortTime) {
      return `${shortDate} ${shortTime}`
    }
    return shortDate || shortTime || bucket
  }

  const shortDate = `${String(bucketDate.getMonth() + 1).padStart(2, '0')}-${String(bucketDate.getDate()).padStart(2, '0')}`
  const hour24 = bucketDate.getHours()
  const suffix = hour24 >= 12 ? 'PM' : 'AM'
  const hour12 = hour24 % 12 || 12
  const timeText = `${hour12}:00 ${suffix}`

  return compact ? timeText : `${shortDate} ${timeText}`
}

function toneColor(tone?: PerfPositionEditState['statusTone']): string {
  if (tone === 'error') return theme.red
  if (tone === 'success') return theme.green
  return theme.dim
}

function normalizeManualStatus(raw: string | null | undefined): PositionManualEditStatus | null {
  const normalized = String(raw || '').trim().toLowerCase()
  if (editablePositionStatuses.includes(normalized as PositionManualEditStatus)) {
    return normalized as PositionManualEditStatus
  }
  return null
}

function positionEditKey(marketId: string, tokenId: string, side: string, realMoney: number): string {
  return `${realMoney}:${marketId}:${tokenId}:${side.trim().toLowerCase()}`
}

function roundTo(value: number, decimals: number): number {
  return Number(value.toFixed(decimals))
}

function computePositionProfit(row: PositionRow): number | null {
  if (row.status === 'open' || row.status === 'waiting') {
    return null
  }
  if (row.status === 'win') {
    return row.shares != null ? row.shares - row.size_usd : null
  }
  if (row.status === 'lose') {
    return -row.size_usd
  }
  const exitSizeUsd = row.exit_size_usd ?? row.size_usd
  return exitSizeUsd - row.size_usd
}

function normalizeEffectivePosition(
  row: PositionRow,
  nowTs: number,
  tradeLogEditLookup: Map<number, TradeLogManualEditRow>,
  positionEditLookup: Map<string, PositionManualEditRow>
): PositionRow {
  const tradeEdit =
    row.source_trade_log_id != null ? tradeLogEditLookup.get(row.source_trade_log_id) : undefined
  const positionEdit = positionEditLookup.get(positionEditKey(row.market_id, row.token_id, row.side, row.real_money))
  const edit = row.source_kind === 'position' ? (positionEdit ?? tradeEdit) : tradeEdit
  const entry_price = edit?.entry_price != null ? Number(edit.entry_price) : row.entry_price
  const shares = edit?.shares != null ? Number(edit.shares) : row.shares
  const size_usd = edit?.size_usd != null ? Number(edit.size_usd) : row.size_usd
  const statusOverride = normalizeManualStatus(edit?.status)
  const status =
    statusOverride ??
    (row.status === 'open' && row.market_close_ts > 0 && row.market_close_ts <= nowTs ? 'waiting' : row.status)
  const baseResolutionTs =
    row.resolution_ts ||
    row.market_close_ts ||
    (edit?.updated_at != null ? Number(edit.updated_at) : 0) ||
    row.entered_at
  const resolution_ts =
    status === 'open'
      ? 0
      : status === 'waiting'
        ? row.market_close_ts || baseResolutionTs
        : baseResolutionTs
  const exit_size_usd =
    status === 'exit'
      ? roundTo(Number(row.exit_size_usd ?? size_usd), 3)
      : null
  const normalizedRow: PositionRow = {
    ...row,
    entry_price: roundTo(Number(entry_price || 0), 3),
    shares: shares != null ? roundTo(Number(shares), 6) : null,
    size_usd: roundTo(Number(size_usd || 0), 3),
    status,
    resolution_ts,
    exit_size_usd
  }
  return {
    ...normalizedRow,
    pnl_usd: computePositionProfit(normalizedRow)
  }
}

function groupDailyPnl(rows: PositionRow[], nowTs: number): DailyPnlEntry[] {
  const totals = new Map<string, number>()

  rows.forEach((row) => {
    const pnl = row.pnl_usd
    if (pnl == null) {
      return
    }
    const ts = row.resolution_ts || row.market_close_ts || row.entered_at
    if (!ts) {
      return
    }
    const bucketDate = new Date(ts * 1000)
    bucketDate.setMinutes(0, 0, 0)
    const bucket = formatHourlyBucketKey(bucketDate)
    totals.set(bucket, roundTo((totals.get(bucket) || 0) + pnl, 3))
  })

  const parsedEntries = Array.from(totals.entries())
    .map(([day, pnl]) => ({
      day,
      pnl,
      label: formatDollar(pnl),
      bucketDate: parseHourlyBucket(day)
    }))
    .filter((row): row is DailyPnlEntry & {bucketDate: Date} => row.bucketDate != null)
    .sort((left, right) => right.bucketDate.getTime() - left.bucketDate.getTime())

  if (!parsedEntries.length) {
    return []
  }

  const entryByBucket = new Map(parsedEntries.map((entry) => [entry.day, entry]))
  const newestResolved = new Date(parsedEntries[0].bucketDate.getTime())
  const currentBucket = floorToHour(new Date(nowTs * 1000))
  const newest =
    currentBucket.getTime() > newestResolved.getTime() ? currentBucket : newestResolved
  const oldest = new Date(parsedEntries[parsedEntries.length - 1].bucketDate.getTime())
  const filledEntries: DailyPnlEntry[] = []

  for (let cursor = new Date(newest.getTime()); cursor >= oldest;) {
    const bucketKey = formatHourlyBucketKey(cursor)
    const existing = entryByBucket.get(bucketKey)
    filledEntries.push(
      existing
        ? {day: existing.day, pnl: existing.pnl, label: existing.label}
        : {day: bucketKey, pnl: 0, label: formatDollar(0)}
    )
    const nextCursor = new Date(cursor.getTime())
    nextCursor.setHours(nextCursor.getHours() - 1)
    cursor = nextCursor
  }

  return filledEntries
}

function DailyPnlPreviewChart({entries, width}: {entries: DailyPnlEntry[]; width: number}) {
  const levelCount = 4
  const gapWidth = 0
  const columnWidth = 2
  const chartWidth = Math.max(1, width)
  const leftPaddingWidth = Math.max(0, chartWidth - (entries.length * columnWidth))
  const maxAbsPnl = Math.max(1, ...entries.map((entry) => Math.abs(entry.pnl)))
  const heights = entries.map((entry) => {
    const magnitude = Math.abs(entry.pnl)
    if (magnitude <= 0) {
      return 0
    }
    return Math.max(1, Math.min(levelCount, Math.round((magnitude / maxAbsPnl) * levelCount)))
  })

  const renderRow = (rowIndex: number, negative: boolean) => (
    <InkBox width="100%">
      {leftPaddingWidth > 0 ? (
        <InkBox width={leftPaddingWidth}>
          <Text>{' '.repeat(leftPaddingWidth)}</Text>
        </InkBox>
      ) : null}
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
        <Text color={theme.dim}>{'─'.repeat(chartWidth)}</Text>
      </InkBox>
      {Array.from({length: levelCount}, (_, index) => renderRow(index + 1, true))}
    </InkBox>
  )
}

export function Performance({
  currentScrollOffset,
  pastScrollOffset,
  activePane,
  selectedBox,
  dailyDetailOpen,
  dailyDetailScrollOffset,
  editState,
  onCurrentScrollOffsetChange,
  onPastScrollOffsetChange,
  onDailyDetailScrollOffsetChange,
  onSelectionMetaChange
}: PerformanceProps) {
  const terminal = useTerminalSize()
  const stacked = stackPanels(terminal.width)
  const shadowOpenPositions = useQuery<PositionRow>(SHADOW_OPEN_POSITIONS_SQL)
  const livePositions = useQuery<PositionRow>(LIVE_POSITIONS_SQL)
  const resolvedPositions = useQuery<PositionRow>(RESOLVED_POSITIONS_SQL)
  const tradeLogManualEdits = useQuery<TradeLogManualEditRow>(TRADE_LOG_MANUAL_EDITS_SQL)
  const positionManualEdits = useQuery<PositionManualEditRow>(POSITION_MANUAL_EDITS_SQL)
  const events = useEventStream(1000)
  const {lookup: tradeIdLookup} = useTradeIdIndex()
  const botState = useBotState(1000)
  const positionsTableWidth = Math.max(72, terminal.width - 10)
  const positionsLayout = getPositionsLayout(positionsTableWidth)
  const nowTs = Date.now() / 1000
  const activeMode = botState.mode === 'live' ? 'live' : 'shadow'
  const activeRealMoney = activeMode === 'live' ? 1 : 0
  const activeTitle = activeMode === 'live' ? 'Live' : 'Tracker'
  const currentHourBucketTs = Math.floor(nowTs / 3600) * 3600
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
  const tradeLogEditLookup = useMemo(
    () =>
      new Map(
        tradeLogManualEdits.map((row) => [Number(row.trade_log_id), row])
      ),
    [tradeLogManualEdits]
  )
  const positionEditLookup = useMemo(
    () =>
      new Map(
        positionManualEdits.map((row) => [
          positionEditKey(row.market_id, row.token_id, row.side, Number(row.real_money || 0)),
          row
        ])
      ),
    [positionManualEdits]
  )
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
  const effectiveOpenPositions = useMemo(
    () =>
      activeOpenPositions.map((row) =>
        normalizeEffectivePosition(row, nowTs, tradeLogEditLookup, positionEditLookup)
      ),
    [activeOpenPositions, nowTs, positionEditLookup, tradeLogEditLookup]
  )
  const effectiveResolvedPositions = useMemo(
    () =>
      activeResolvedPositions.map((row) =>
        normalizeEffectivePosition(row, nowTs, tradeLogEditLookup, positionEditLookup)
      ),
    [activeResolvedPositions, nowTs, positionEditLookup, tradeLogEditLookup]
  )
  const effectivePositions = useMemo(
    () => [...effectiveOpenPositions, ...effectiveResolvedPositions],
    [effectiveOpenPositions, effectiveResolvedPositions]
  )
  const currentPositions = useMemo(
    () =>
      effectivePositions
        .filter((row) => row.status === 'open')
        .sort((left, right) => right.entered_at - left.entered_at),
    [effectivePositions]
  )
  const currentPositionsTotal = useMemo(
    () => currentPositions.reduce((sum, row) => sum + (row.size_usd || 0), 0),
    [currentPositions]
  )
  const waitingPositions = useMemo(
    () =>
      effectivePositions
        .filter((row) => row.status === 'waiting')
        .sort(
          (a, b) =>
            Math.max(b.market_close_ts || 0, b.entered_at || 0) -
            Math.max(a.market_close_ts || 0, a.entered_at || 0)
        ),
    [effectivePositions]
  )
  const waitingPositionsTotal = useMemo(
    () => waitingPositions.reduce((sum, row) => sum + (row.size_usd || 0), 0),
    [waitingPositions]
  )
  const pastPositions = useMemo(
    () =>
      effectivePositions
        .filter((row) => row.status !== 'open')
        .sort(
        (a, b) =>
          Math.max(b.resolution_ts || 0, b.market_close_ts || 0, b.entered_at || 0) -
          Math.max(a.resolution_ts || 0, a.market_close_ts || 0, a.entered_at || 0)
      ),
    [effectivePositions]
  )
  const activeSummary = useMemo<ComputedSummary>(
    () => {
      const acted = effectivePositions.length
      const resolved = effectivePositions.filter((row) => row.status === 'win' || row.status === 'lose' || row.status === 'exit')
      const wins = resolved.filter((row) => (row.pnl_usd ?? 0) > 0).length
      const totalPnl = roundTo(resolved.reduce((sum, row) => sum + (row.pnl_usd || 0), 0), 3)
      const confidenceRows = effectivePositions.filter((row) => row.confidence != null)
      const avgConfidence =
        confidenceRows.length > 0
          ? roundTo(confidenceRows.reduce((sum, row) => sum + Number(row.confidence || 0), 0) / confidenceRows.length, 3)
          : null
      const avgSize =
        acted > 0
          ? roundTo(effectivePositions.reduce((sum, row) => sum + Number(row.size_usd || 0), 0) / acted, 3)
          : null
      return {
        acted,
        resolved: resolved.length,
        wins,
        total_pnl: resolved.length ? totalPnl : 0,
        avg_confidence: avgConfidence,
        avg_size: avgSize
      }
    },
    [effectivePositions]
  )
  const dailyEntries = useMemo<DailyPnlEntry[]>(
    () =>
      groupDailyPnl(
        pastPositions.filter((row) => row.status === 'win' || row.status === 'lose' || row.status === 'exit'),
        currentHourBucketTs
      ),
    [currentHourBucketTs, pastPositions]
  )
  const dailyPanelContentWidth = useMemo(
    () => getDailyPanelContentWidth(terminal.width, stacked),
    [stacked, terminal.width]
  )
  const dailyPreviewCapacity = useMemo(
    () =>
      dailyEntries.length
        ? Math.min(dailyEntries.length, Math.max(1, Math.floor(dailyPanelContentWidth / 2)))
        : 0,
    [dailyEntries.length, dailyPanelContentWidth]
  )
  const dailyPreviewEntries = useMemo(
    () => dailyEntries.slice(0, dailyPreviewCapacity).reverse(),
    [dailyEntries, dailyPreviewCapacity]
  )
  const dailyValueWidth = useMemo(
    () => dailyEntries.reduce((max, row) => Math.max(max, row.label.length), 10),
    [dailyEntries]
  )
  const paneMetrics = getPositionPaneMetrics(terminal.height, stacked)
  const currentMaxOffset = Math.max(currentPositions.length - 1, 0)
  const pastMaxOffset = Math.max(pastPositions.length - 1, 0)
  const effectiveCurrentScrollOffset = Math.min(currentScrollOffset, currentMaxOffset)
  const effectivePastScrollOffset = Math.min(pastScrollOffset, pastMaxOffset)
  const currentWindowStart =
    currentPositions.length > paneMetrics.visibleRows
      ? Math.min(
          Math.max(effectiveCurrentScrollOffset - Math.floor(paneMetrics.visibleRows / 2), 0),
          Math.max(0, currentPositions.length - paneMetrics.visibleRows)
        )
      : 0
  const pastWindowStart =
    pastPositions.length > paneMetrics.visibleRows
      ? Math.min(
          Math.max(effectivePastScrollOffset - Math.floor(paneMetrics.visibleRows / 2), 0),
          Math.max(0, pastPositions.length - paneMetrics.visibleRows)
        )
      : 0
  const visibleCurrentPositions = currentPositions.slice(
    currentWindowStart,
    currentWindowStart + paneMetrics.visibleRows
  )
  const visiblePastPositions = pastPositions.slice(
    pastWindowStart,
    pastWindowStart + paneMetrics.visibleRows
  )
  const selectedCurrentRow = currentPositions[effectiveCurrentScrollOffset] ?? null
  const selectedPastRow = pastPositions[effectivePastScrollOffset] ?? null
  const shadowBalance =
    botState.mode === 'shadow' && botState.bankroll_usd != null ? botState.bankroll_usd : null
  const liveBalance =
    botState.mode === 'live' && botState.bankroll_usd != null ? botState.bankroll_usd : null
  const activeBalance = activeMode === 'live' ? liveBalance : shadowBalance
  const modalBackground = terminal.backgroundColor || theme.modalBackground
  const selectedRowBackground = selectionBackgroundColor(terminal.backgroundColor)
  const detailModalWidth = Math.max(60, Math.min(terminal.width - 8, terminal.wide ? 110 : 88))
  const detailModalContentWidth = Math.max(40, detailModalWidth - 4)
  const detailVisibleRows = Math.max(12, Math.min(21, terminal.height - 12))
  const detailMaxOffset = Math.max(0, dailyEntries.length - detailVisibleRows)
  const detailOffset = Math.min(dailyDetailScrollOffset, detailMaxOffset)
  const visibleDetailEntries = dailyEntries.slice(detailOffset, detailOffset + detailVisibleRows)

  useEffect(() => {
    if (currentScrollOffset !== effectiveCurrentScrollOffset) {
      onCurrentScrollOffsetChange?.(effectiveCurrentScrollOffset)
    }
  }, [currentScrollOffset, effectiveCurrentScrollOffset, onCurrentScrollOffsetChange])

  useEffect(() => {
    if (pastScrollOffset !== effectivePastScrollOffset) {
      onPastScrollOffsetChange?.(effectivePastScrollOffset)
    }
  }, [pastScrollOffset, effectivePastScrollOffset, onPastScrollOffsetChange])

  useEffect(() => {
    if (dailyDetailScrollOffset !== detailOffset) {
      onDailyDetailScrollOffsetChange?.(detailOffset)
    }
  }, [dailyDetailScrollOffset, detailOffset, onDailyDetailScrollOffsetChange])

  useEffect(() => {
    onSelectionMetaChange?.({
      currentCount: currentPositions.length,
      pastCount: pastPositions.length,
      selectedCurrentRow,
      selectedPastRow
    })
  }, [
    currentPositions.length,
    onSelectionMetaChange,
    pastPositions.length,
    selectedCurrentRow?.entry_price,
    selectedCurrentRow?.row_key,
    selectedCurrentRow?.shares,
    selectedCurrentRow?.size_usd,
    selectedCurrentRow?.status,
    selectedPastRow?.entry_price,
    selectedPastRow?.row_key,
    selectedPastRow?.shares,
    selectedPastRow?.size_usd,
    selectedPastRow?.status
  ])

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
  const detailMaxAbsPnl = useMemo(
    () => Math.max(1, ...dailyEntries.map((entry) => Math.abs(entry.pnl))),
    [dailyEntries]
  )
  const editModalWidth = Math.max(56, Math.min(terminal.width - 8, terminal.wide ? 88 : 74))
  const editModalContentWidth = Math.max(36, editModalWidth - 4)
  const editFieldLabelWidth = Math.max(8, Math.min(10, Math.floor(editModalContentWidth * 0.22)))
  const editFieldValueWidth = Math.max(12, editModalContentWidth - editFieldLabelWidth - 1)
  const editDraftEntryValue =
    editState && Number.isFinite(Number(editState.draftEntry)) && Number(editState.draftEntry) > 0
      ? Number(editState.draftEntry)
      : null
  const editDraftSharesValue =
    editState && Number.isFinite(Number(editState.draftShares)) && Number(editState.draftShares) > 0
      ? Number(editState.draftShares)
      : null
  const editDraftTotalValue =
    editState && Number.isFinite(Number(editState.draftTotal)) && Number(editState.draftTotal) > 0
      ? Number(editState.draftTotal)
      : null
  const editPreviewPnl =
    editState && editDraftTotalValue != null
      ? editState.draftStatus === 'win'
        ? editDraftSharesValue != null
          ? editDraftSharesValue - editDraftTotalValue
          : null
        : editState.draftStatus === 'lose'
          ? -editDraftTotalValue
          : editState.draftStatus === 'exit'
            ? Number(editState.row.exit_size_usd ?? editDraftTotalValue) - editDraftTotalValue
            : null
      : null
  const editQuestionLines = editState
    ? wrapText(
        truncate(editState.row.question || editState.row.market_id, editModalContentWidth),
        editModalContentWidth
      )
    : []
  const editInstructionText =
    editState?.editingField
      ? 'Type a positive number. Enter closes the field editor. Esc leaves the field editor.'
      : 'Up/down selects a field. Enter edits numbers. Left/right changes status. s saves. Esc cancels.'

  const renderPositionsTable = (
    rowsToRender: PositionRow[],
    {
      showStatus = false,
      showTtr = true,
      profitScaleRows = rowsToRender,
      selectedRowKey
    }: RenderPositionsOptions & {selectedRowKey?: string} = {}
  ) => {
    const trailingWidth = positionsLayout.ttrWidth
    const trailingDelta = trailingWidth - positionsLayout.ttrWidth
    const questionWidth = Math.max(14, positionsLayout.questionWidth - trailingDelta)
    const resolutionWidth = positionsLayout.resolutionWidth
    const maxAbsProfit = profitScaleRows.reduce(
      (max, row) => Math.max(max, Math.abs(computePositionProfit(row) ?? 0)),
      0
    )

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
        <InkBox flexDirection="column">
          {rowsToRender.map((row) => {
            const isSelected = row.row_key === selectedRowKey
            const rowBackground = isSelected ? selectedRowBackground : undefined
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
            const resolutionColor = row.status === 'waiting' ? theme.red : theme.dim
            const shares = row.shares
            const toWin =
              row.status === 'exit'
                ? row.exit_size_usd
                : row.status === 'lose'
                  ? 0
                  : shares != null
                    ? shares
                    : null
            const profit = computePositionProfit(row)
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
                : centeredGradientColor(profit, maxAbsProfit || 1)

            return (
              <InkBox key={row.row_key} width="100%">
                {positionsLayout.showId ? (
                  <>
                    <Text color={theme.dim} backgroundColor={rowBackground}>
                      {fitRight(displayIdText, positionsLayout.idWidth)}
                    </Text>
                    <Text backgroundColor={rowBackground}> </Text>
                  </>
                ) : null}
                {positionsLayout.showUser ? (
                  <>
                    <Text color={username ? theme.white : theme.dim} backgroundColor={rowBackground}>
                      {fit(userText, positionsLayout.userWidth)}
                    </Text>
                    <Text backgroundColor={rowBackground}> </Text>
                  </>
                ) : null}
                <Text
                  color={isSelected ? theme.accent : row.market_url ? theme.accent : undefined}
                  backgroundColor={rowBackground}
                  bold={isSelected}
                >
                  {terminalHyperlink(
                    fit(`${isSelected ? '> ' : '  '}${row.question || row.market_id}`, questionWidth),
                    row.market_url
                  )}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text color={theme.dim} backgroundColor={rowBackground} bold={isSelected}>
                  {fitRight(secondsAgo(row.entered_at), positionsLayout.ageWidth)}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text color={actionColor} backgroundColor={rowBackground} bold={isSelected}>
                  {fit(actionText, positionsLayout.actionWidth)}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text color={sideColor} backgroundColor={rowBackground} bold={isSelected}>
                  {fit(row.side.toUpperCase(), positionsLayout.sideWidth)}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text color={entryColor} backgroundColor={rowBackground} bold={isSelected}>
                  {fitRight(formatNumber(row.entry_price), positionsLayout.entryWidth)}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text backgroundColor={rowBackground} bold={isSelected}>
                  {fitRight(
                    shares != null
                      ? formatAdaptiveNumber(shares, positionsLayout.sharesWidth)
                      : '-',
                    positionsLayout.sharesWidth
                  )}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text backgroundColor={rowBackground} bold={isSelected}>
                  {fitRight(formatAdaptiveDollar(row.size_usd, positionsLayout.sizeWidth), positionsLayout.sizeWidth)}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text color={toWinColor} backgroundColor={rowBackground} bold={isSelected}>
                  {fitRight(
                    toWin != null
                      ? formatAdaptiveDollar(toWin, positionsLayout.toWinWidth)
                      : '-',
                    positionsLayout.toWinWidth
                  )}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text color={profitColor} backgroundColor={rowBackground} bold={isSelected}>
                  {fitRight(
                    profit != null
                      ? formatAdaptiveDollar(profit, positionsLayout.profitWidth)
                      : '-',
                    positionsLayout.profitWidth
                  )}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text color={confidenceColor} backgroundColor={rowBackground} bold={isSelected}>
                  {fitRight(formatPct(row.confidence, 1), positionsLayout.confidenceWidth)}
                </Text>
                <Text backgroundColor={rowBackground}> </Text>
                <Text color={resolutionColor} backgroundColor={rowBackground} bold={isSelected}>
                  {fitRight(formatShortDateTime(resolutionTs), resolutionWidth)}
                </Text>
                {showTtr || showStatus ? (
                  <>
                    <Text backgroundColor={rowBackground}> </Text>
                    <Text color={showStatus ? statusColor : resolutionColor} backgroundColor={rowBackground} bold={isSelected}>
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
        <Box title={`Hourly ${activeTitle} P&L`} width={stacked ? '100%' : '50%'} accent={selectedBox === 'daily'}>
          {dailyPreviewEntries.length ? (
            <DailyPnlPreviewChart entries={dailyPreviewEntries} width={dailyPanelContentWidth} />
          ) : (
            <Text color={theme.dim}>{`No resolved ${activeTitle.toLowerCase()} trades yet.`}</Text>
          )}
        </Box>
      </InkBox>

      <InkBox marginTop={1} flexDirection="column" height={paneMetrics.paneHeight * 2 + 1}>
        <InkBox height={paneMetrics.paneHeight}>
          <Box
            title={`Current Positions (${currentPositions.length}, holding $${currentPositionsTotal.toFixed(3)})`}
            height="100%"
            accent={selectedBox === 'current'}
          >
            {visibleCurrentPositions.length ? (
              renderPositionsTable(visibleCurrentPositions, {
                profitScaleRows: currentPositions,
                selectedRowKey: selectedCurrentRow?.row_key
              })
            ) : (
              <Text color={theme.dim}>No open positions right now.</Text>
            )}
          </Box>
        </InkBox>

        <InkBox height={1} />

        <InkBox height={paneMetrics.paneHeight}>
          <Box
            title={`Past Positions (${pastPositions.length}, waiting for $${waitingPositionsTotal.toFixed(2)})`}
            height="100%"
            accent={selectedBox === 'past'}
          >
            {visiblePastPositions.length ? (
              renderPositionsTable(visiblePastPositions, {
                showStatus: true,
                showTtr: false,
                profitScaleRows: pastPositions,
                selectedRowKey: selectedPastRow?.row_key
              })
            ) : (
              <Text color={theme.dim}>No past positions yet.</Text>
            )}
          </Box>
        </InkBox>
      </InkBox>

      {editState ? (
        <InkBox position="absolute" width="100%" height="100%" justifyContent="center" alignItems="center">
          <InkBox borderStyle="round" borderColor={theme.accent} flexDirection="column" width={editModalWidth}>
            <InkBox width="100%">
              <Text color={theme.accent} backgroundColor={modalBackground} bold>
                {` ${fit('Manual Position Edit', Math.max(1, editModalContentWidth - 1))}`}
              </Text>
              <Text backgroundColor={modalBackground}> </Text>
            </InkBox>
            <Text color={theme.white} backgroundColor={modalBackground} bold>
              {` ${fit(`${editState.row.side.toUpperCase()} ${formatAdaptiveDollar(editState.row.size_usd, 10)} ${activeTitle}`, editModalContentWidth)} `}
            </Text>
            {editQuestionLines.map((line, index) => (
              <Text key={`edit-question-${index}`} color={theme.dim} backgroundColor={modalBackground}>
                {` ${fit(line, editModalContentWidth)} `}
              </Text>
            ))}
            <Text color={theme.dim} backgroundColor={modalBackground}>
              {` ${fit(`Status ${editState.row.status}  Trade ${editState.row.trade_id || '-'}  Entered ${secondsAgo(editState.row.entered_at)}`, editModalContentWidth)} `}
            </Text>
            <Text backgroundColor={modalBackground}>{' '.repeat(editModalWidth - 2)}</Text>
            {([
              ['entry', editState.draftEntry, editState.editingField === 'entry'],
              ['shares', editState.draftShares, editState.editingField === 'shares'],
              ['total', editState.draftTotal, editState.editingField === 'total'],
              ['status', editState.draftStatus, false]
            ] as Array<[PerfPositionEditField, string, boolean]>).map(([field, value, editing]) => {
              const selected = editState.selectedField === field
              const rowBackground = selected ? selectedRowBackground : modalBackground
              const label = `${selected ? '>' : ' '} ${field.toUpperCase()}`
              const shownValue = field === 'status' ? value.toUpperCase() : editing ? `${value}_` : value
              return (
                <InkBox key={`edit-field-${field}`} width="100%">
                  <Text color={selected ? theme.accent : theme.dim} backgroundColor={rowBackground} bold={selected}>
                    {` ${fit(label, editFieldLabelWidth)} `}
                  </Text>
                  <Text color={selected ? theme.white : theme.dim} backgroundColor={rowBackground} bold={selected}>
                    {`${fitRight(shownValue, editFieldValueWidth)} `}
                  </Text>
                </InkBox>
              )
            })}
            <Text backgroundColor={modalBackground}>{' '.repeat(editModalWidth - 2)}</Text>
            <Text color={theme.dim} backgroundColor={modalBackground}>
              {` ${fit(
                `Preview P&L ${editPreviewPnl == null ? '-' : formatDollar(editPreviewPnl)}  To win ${editDraftSharesValue == null ? '-' : formatAdaptiveDollar(editDraftSharesValue, 10)}`,
                editModalContentWidth
              )} `}
            </Text>
            <Text color={toneColor(editState.statusTone)} backgroundColor={modalBackground}>
              {` ${fit((editState.statusMessage || editInstructionText).trim(), editModalContentWidth)} `}
            </Text>
          </InkBox>
        </InkBox>
      ) : null}

      {dailyDetailOpen ? (
        <InkBox position="absolute" width="100%" height="100%" justifyContent="center" alignItems="center">
          <InkBox borderStyle="round" borderColor={theme.accent} flexDirection="column" width={detailModalWidth}>
            <InkBox width="100%">
              <Text color={theme.accent} backgroundColor={modalBackground} bold>
                {` ${fit(`Hourly ${activeTitle} P&L Detail`, Math.max(1, detailModalContentWidth - detailRangeLabel.length - 1))}`}
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
                  {` ${fitRight(row ? formatHourlyBucketLabel(row.day) : '', detailQueueLayout.dateWidth)}`}
                </Text>
                <Text backgroundColor={modalBackground}> </Text>
                <InkBox width={detailQueueLayout.barWidth}>
                  <BarSparkline
                    value={row ? row.pnl / detailMaxAbsPnl : 0}
                    width={detailQueueLayout.barWidth}
                    positive={row ? row.pnl >= 0 : true}
                    centered
                    axisChar="│"
                  />
                </InkBox>
                <Text backgroundColor={modalBackground}> </Text>
                <Text
                  color={row ? (row.pnl >= 0 ? theme.green : theme.red) : theme.dim}
                  backgroundColor={modalBackground}
                >
                  {`${fitRight(row?.label || '', detailQueueLayout.valueWidth)} `}
                </Text>
              </InkBox>
            ))}
          </InkBox>
        </InkBox>
      ) : null}
    </InkBox>
  )
}
