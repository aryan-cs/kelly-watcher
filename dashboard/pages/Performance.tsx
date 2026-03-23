import React, {useEffect, useMemo, useState} from 'react'
import {Box as InkBox, Text} from 'ink'
import {BarSparkline} from '../components/BarSparkline.js'
import {Box} from '../components/Box.js'
import {ModalOverlay} from '../components/ModalOverlay.js'
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

export type PerfPositionEditField = 'chart' | 'entry' | 'shares' | 'total' | 'status'

export interface PerfPositionEditState {
  row: PositionRow
  pane: 'current' | 'past'
  selectedField: PerfPositionEditField
  editingField: Exclude<PerfPositionEditField, 'chart' | 'status'> | null
  draftEntry: string
  draftShares: string
  draftTotal: string
  draftStatus: PositionManualEditStatus
  historyCursorOffset: number
  statusMessage?: string
  statusTone?: 'info' | 'success' | 'error'
}

export type PerfPositionAction = 'buy_more' | 'cash_out'
export type PerfPositionActionField = 'chart' | 'action' | 'amount' | 'execute' | 'edit'

export interface PerfPositionActionState {
  row: PositionRow
  selectedField: PerfPositionActionField
  action: PerfPositionAction
  editingAmount: boolean
  draftAmountUsd: string
  historyCursorOffset: number
  statusMessage?: string
  statusTone?: 'info' | 'success' | 'error'
}

export interface PerformanceSelectionMeta {
  currentCount: number
  pastCount: number
  selectedCurrentRow: PositionRow | null
  selectedPastRow: PositionRow | null
}

export interface PerformanceDetailHistoryMeta {
  timelineCount: number
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
  cashOutWidth: number
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
  actionState?: PerfPositionActionState | null
  editState?: PerfPositionEditState | null
  onCurrentScrollOffsetChange?: (offset: number) => void
  onPastScrollOffsetChange?: (offset: number) => void
  onDailyDetailScrollOffsetChange?: (offset: number) => void
  onSelectionMetaChange?: (meta: PerformanceSelectionMeta) => void
  onDetailHistoryMetaChange?: (meta: PerformanceDetailHistoryMeta) => void
}

interface PriceHistoryPoint {
  price: number
  ts: number
}

interface PositionPriceSeries {
  tokenId: string
  label: string
  color: string
  isSelected: boolean
  points: PriceHistoryPoint[]
}

interface PositionPriceHistoryState {
  status: 'idle' | 'loading' | 'ready' | 'error'
  message?: string
  fetchedAt?: number
  series: PositionPriceSeries[]
}

interface HistoryCursorValue {
  series: PositionPriceSeries
  point: PriceHistoryPoint | null
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
  const cashOutWidth = 8
  const confidenceWidth = 6
  const ttrWidth = 11
  const ageWidth = 8
  const gaps = 12 + (showId ? 1 : 0) + (showUser ? 1 : 0)
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
    cashOutWidth +
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
    cashOutWidth,
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

function actionToneColor(tone?: PerfPositionActionState['statusTone']): string {
  if (tone === 'error') return theme.red
  if (tone === 'success') return theme.green
  return theme.dim
}

function emptyPriceHistoryState(status: PositionPriceHistoryState['status']): PositionPriceHistoryState {
  return {
    status,
    series: []
  }
}

function parseMetaList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || '').trim()).filter(Boolean)
  }
  if (typeof value === 'string') {
    const text = value.trim()
    if (!text) {
      return []
    }
    try {
      const parsed = JSON.parse(text)
      if (Array.isArray(parsed)) {
        return parsed.map((item) => String(item || '').trim()).filter(Boolean)
      }
    } catch {
      // Fall through to comma-split handling.
    }
    if (text.includes(',')) {
      return text.split(',').map((part) => part.trim()).filter(Boolean)
    }
    return [text]
  }
  return []
}

function seriesColorForOutcome(label: string, index: number, isSelected: boolean): string {
  const semanticColor = outcomeColor(label)
  if (semanticColor !== theme.blue) {
    return semanticColor
  }
  const palette = [theme.purple, theme.yellow, theme.blue, theme.green, theme.red, theme.dim]
  return palette[index % palette.length] || theme.white
}

function extractOutcomeSeries(row: PositionRow, meta: Record<string, unknown> | null | undefined): Array<{tokenId: string; label: string}> {
  const definitions: Array<{tokenId: string; label: string}> = []
  const outcomes = parseMetaList(meta?.outcomes ?? meta?.outcomeNames ?? meta?.outcome_names)
  const tokenIds = parseMetaList(meta?.clobTokenIds ?? meta?.clobTokenIDs ?? meta?.tokenIds ?? meta?.token_ids)

  if (outcomes.length > 0 && outcomes.length === tokenIds.length) {
    for (let index = 0; index < outcomes.length; index += 1) {
      const tokenId = String(tokenIds[index] || '').trim()
      const label = String(outcomes[index] || '').trim()
      if (tokenId && label) {
        definitions.push({tokenId, label})
      }
    }
  }

  const tokens = Array.isArray(meta?.tokens) ? meta?.tokens : []
  for (const token of tokens) {
    if (!token || typeof token !== 'object') {
      continue
    }
    const tokenRecord = token as Record<string, unknown>
    const tokenId = String(tokenRecord.token_id ?? tokenRecord.tokenId ?? tokenRecord.clobTokenId ?? '').trim()
    const label = String(tokenRecord.outcome ?? tokenRecord.name ?? tokenRecord.title ?? '').trim()
    if (tokenId && label) {
      definitions.push({tokenId, label})
    }
  }

  const deduped = new Map<string, {tokenId: string; label: string}>()
  for (const definition of definitions) {
    if (!deduped.has(definition.tokenId)) {
      deduped.set(definition.tokenId, definition)
    }
  }

  const selectedTokenId = String(row.token_id || '').trim()
  const selectedLabel = String(row.side || '').trim().toUpperCase()
  if (selectedTokenId && !deduped.has(selectedTokenId)) {
    deduped.set(selectedTokenId, {
      tokenId: selectedTokenId,
      label: selectedLabel || 'SELECTED'
    })
  }

  return Array.from(deduped.values())
}

function normalizePriceHistoryRows(payload: unknown): PriceHistoryPoint[] {
  const history =
    Array.isArray(payload)
      ? payload
      : payload && typeof payload === 'object' && Array.isArray((payload as {history?: unknown[]}).history)
        ? (payload as {history: unknown[]}).history
        : []

  const normalized = history
    .map((row) => {
      if (!row || typeof row !== 'object') {
        return null
      }
      const price = Number(
        (row as {p?: unknown; price?: unknown; value?: unknown}).p ??
        (row as {p?: unknown; price?: unknown; value?: unknown}).price ??
        (row as {p?: unknown; price?: unknown; value?: unknown}).value
      )
      const ts = Number(
        (row as {t?: unknown; timestamp?: unknown; time?: unknown}).t ??
        (row as {t?: unknown; timestamp?: unknown; time?: unknown}).timestamp ??
        (row as {t?: unknown; timestamp?: unknown; time?: unknown}).time
      )
      if (!Number.isFinite(price) || !Number.isFinite(ts) || price < 0 || price > 1 || ts <= 0) {
        return null
      }
      return {
        price,
        ts: ts > 10_000_000_000 ? Math.floor(ts / 1000) : Math.floor(ts)
      }
    })
    .filter((row): row is PriceHistoryPoint => row != null)

  normalized.sort((left, right) => left.ts - right.ts)
  return normalized
}

async function fetchClobPriceHistory(tokenId: string): Promise<PriceHistoryPoint[]> {
  const encodedTokenId = encodeURIComponent(String(tokenId || '').trim())
  const attempts = [
    `https://clob.polymarket.com/prices-history?market=${encodedTokenId}&interval=max&fidelity=15`,
    `https://clob.polymarket.com/prices-history?market=${encodedTokenId}&interval=max&fidelity=60`,
    `https://clob.polymarket.com/prices-history?market=${encodedTokenId}&interval=max`
  ]

  let lastError: Error | null = null

  for (let index = 0; index < attempts.length; index += 1) {
    const response = await fetch(attempts[index] || '')
    if (response.ok) {
      const payload = await response.json()
      return normalizePriceHistoryRows(payload)
    }
    lastError = new Error(`HTTP ${response.status}`)
    if (response.status !== 400 || index === attempts.length - 1) {
      break
    }
  }

  throw lastError || new Error('unknown error')
}

interface PositionMarkQuote {
  price: number | null
  fetchedAt: number
}

function extractBestBookPrice(levels: unknown, strategy: 'highest' | 'lowest'): number | null {
  if (!Array.isArray(levels)) {
    return null
  }
  const prices = levels
    .map((level) => {
      if (!level || typeof level !== 'object') {
        return null
      }
      const price = Number((level as {price?: unknown}).price)
      return Number.isFinite(price) && price > 0 && price <= 1 ? price : null
    })
    .filter((price): price is number => price != null)

  if (!prices.length) {
    return null
  }
  return strategy === 'highest' ? Math.max(...prices) : Math.min(...prices)
}

async function fetchPositionMarkQuote(tokenId: string): Promise<PositionMarkQuote | null> {
  const encodedTokenId = encodeURIComponent(String(tokenId || '').trim())
  const fetchedAt = Date.now()

  try {
    const response = await fetch(`https://clob.polymarket.com/book?token_id=${encodedTokenId}`)
    if (response.ok) {
      const payload = await response.json()
      const bestBid = extractBestBookPrice((payload as {bids?: unknown[]}).bids, 'highest')
      const bestAsk = extractBestBookPrice((payload as {asks?: unknown[]}).asks, 'lowest')
      const price = bestBid ?? bestAsk
      if (price != null) {
        return {price, fetchedAt}
      }
    }
  } catch {
    // Fall back to the latest traded price below.
  }

  try {
    const history = await fetchClobPriceHistory(tokenId)
    const lastPoint = history[history.length - 1]
    if (lastPoint) {
      return {price: lastPoint.price, fetchedAt}
    }
  } catch {
    return null
  }

  return null
}

function useLivePositionProfitLookup(rows: PositionRow[]): Map<string, number | null> {
  const tokenIds = useMemo(
    () =>
      Array.from(
        new Set(
          rows
            .map((row) => String(row.token_id || '').trim())
            .filter(Boolean)
        )
      ).sort(),
    [rows]
  )
  const tokenIdsKey = useMemo(() => tokenIds.join('|'), [tokenIds])
  const [quoteByToken, setQuoteByToken] = useState<Record<string, PositionMarkQuote | null>>({})

  useEffect(() => {
    let cancelled = false

    if (!tokenIds.length) {
      setQuoteByToken({})
      return undefined
    }

    const loadQuotes = async () => {
      const entries = await Promise.all(
        tokenIds.map(async (tokenId) => [tokenId, await fetchPositionMarkQuote(tokenId)] as const)
      )
      if (cancelled) {
        return
      }
      setQuoteByToken(Object.fromEntries(entries))
    }

    void loadQuotes()
    const timer = setInterval(() => {
      void loadQuotes()
    }, 15_000)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [tokenIdsKey])

  return useMemo(() => {
    const lookup = new Map<string, number | null>()
    rows.forEach((row) => {
      const tokenId = String(row.token_id || '').trim()
      const quote = tokenId ? quoteByToken[tokenId] : null
      const shares = row.shares
      const profit =
        quote?.price != null && shares != null
          ? roundTo((Number(shares) * quote.price) - Number(row.size_usd || 0), 3)
          : null
      lookup.set(row.row_key, profit)
    })
    return lookup
  }, [quoteByToken, rows])
}

interface ChartCell {
  char: string
  color?: string
}

function makeCell(char: string, color?: string): ChartCell {
  return {char, color}
}

function sampleSeriesPoints(points: PriceHistoryPoint[], targetCount: number): PriceHistoryPoint[] {
  if (points.length <= targetCount) {
    return points
  }
  return Array.from({length: targetCount}, (_, index) => {
    const sourceIndex =
      targetCount === 1
        ? points.length - 1
        : Math.round((index / Math.max(targetCount - 1, 1)) * Math.max(points.length - 1, 0))
    return points[Math.max(0, Math.min(points.length - 1, sourceIndex))]
  })
}

function drawSeriesCell(grid: ChartCell[][], x: number, y: number, char: string, color: string) {
  const row = grid[y]
  if (!row || !row[x]) {
    return
  }
  const current = row[x]
  const isAxis = current.color === theme.dim
  const isReferenceLine = current.char === '┄'
  if (isAxis || isReferenceLine || current.char === ' ') {
    row[x] = makeCell(char, color)
    return
  }
  if (current.color === color) {
    row[x] = makeCell(current.char === '.' ? current.char : char, color)
    return
  }
  row[x] = makeCell('+', theme.white)
}

function drawHorizontalSegment(grid: ChartCell[][], y: number, fromX: number, toX: number, color: string) {
  const start = Math.min(fromX, toX)
  const end = Math.max(fromX, toX)
  for (let x = start; x <= end; x += 1) {
    drawSeriesCell(grid, x, y, '─', color)
  }
}

function drawVerticalSegment(grid: ChartCell[][], x: number, fromY: number, toY: number, color: string) {
  const start = Math.min(fromY, toY)
  const end = Math.max(fromY, toY)
  for (let y = start; y <= end; y += 1) {
    drawSeriesCell(grid, x, y, '│', color)
  }
}

function buildHistoryTimeline(series: PositionPriceSeries[]): number[] {
  const unique = new Set<number>()
  for (const entry of series) {
    for (const point of entry.points) {
      unique.add(point.ts)
    }
  }
  return Array.from(unique).sort((left, right) => left - right)
}

function findNearestHistoryPoint(points: PriceHistoryPoint[], ts: number): PriceHistoryPoint | null {
  if (!points.length) {
    return null
  }

  let low = 0
  let high = points.length - 1
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    const middlePoint = points[middle]
    if ((middlePoint?.ts || 0) < ts) {
      low = middle + 1
    } else {
      high = middle
    }
  }

  const candidateIndexes = Array.from(new Set([low - 1, low, low + 1])).filter(
    (index) => index >= 0 && index < points.length
  )
  let bestIndex = candidateIndexes[0] || 0
  let bestDistance = Math.abs((points[bestIndex]?.ts || 0) - ts)
  for (const index of candidateIndexes) {
    const distance = Math.abs((points[index]?.ts || 0) - ts)
    if (distance < bestDistance) {
      bestIndex = index
      bestDistance = distance
    }
  }
  return points[bestIndex] || points[0] || null
}

function renderCellSegments(cells: ChartCell[], key: string, backgroundColor?: string) {
  const segments: Array<{text: string; color?: string}> = []
  for (const cell of cells) {
    const last = segments[segments.length - 1]
    if (last && last.color === cell.color) {
      last.text += cell.char
    } else {
      segments.push({text: cell.char, color: cell.color})
    }
  }
  return (
    <Text key={key} backgroundColor={backgroundColor}>
      {segments.map((segment, index) => (
        <Text key={`${key}-segment-${index}`} color={segment.color} backgroundColor={backgroundColor}>
          {segment.text}
        </Text>
      ))}
    </Text>
  )
}

interface DetailMetricColumn {
  label: string
  value: string
  color?: string
  bold?: boolean
  ratio?: number
  valueAlign?: 'left' | 'right'
}

interface DetailValueColumn {
  value: string
  color?: string
  bold?: boolean
  ratio?: number
  valueAlign?: 'left' | 'right'
}

function weightedColumnWidths<T extends {ratio?: number}>(totalWidth: number, columns: T[], gapWidth: number): number[] {
  if (!columns.length) {
    return []
  }
  const usableWidth = Math.max(columns.length, totalWidth - Math.max(0, columns.length - 1) * gapWidth)
  const ratios = columns.map((column) => Math.max(0.5, column.ratio ?? 1))
  const ratioSum = ratios.reduce((sum, value) => sum + value, 0)
  const widths = ratios.map((ratio) => Math.max(1, Math.floor((usableWidth * ratio) / Math.max(ratioSum, 1))))
  let remaining = usableWidth - widths.reduce((sum, value) => sum + value, 0)
  for (let index = widths.length - 1; index >= 0 && remaining > 0; index -= 1) {
    widths[index] = (widths[index] || 1) + 1
    remaining -= 1
  }
  return widths
}

function DetailMetricColumns({
  columns,
  width,
  backgroundColor
}: {
  columns: DetailMetricColumn[]
  width: number
  backgroundColor?: string
}) {
  const gapWidth = columns.length > 1 ? 2 : 0
  const widths = weightedColumnWidths(width, columns, gapWidth)
  const sharedLabelWidth = Math.max(
    4,
    Math.min(
      12,
      columns.reduce((max, column) => Math.max(max, column.label.length + 1), 4)
    )
  )

  return (
    <InkBox width="100%">
      <Text backgroundColor={backgroundColor}> </Text>
      {columns.map((column, index) => {
        const columnWidth = widths[index] || 1
        const labelWidth = Math.max(4, Math.min(columnWidth - 1, sharedLabelWidth))
        const valueWidth = Math.max(1, columnWidth - labelWidth)
        const formattedValue =
          column.valueAlign === 'left'
            ? fit(column.value, valueWidth)
            : fitRight(column.value, valueWidth)
        return (
          <React.Fragment key={`detail-column-${index}-${column.label}`}>
            <InkBox width={columnWidth}>
              <Text color={theme.dim} backgroundColor={backgroundColor}>
                {fit(column.label, labelWidth)}
              </Text>
              <Text color={column.color ?? theme.white} backgroundColor={backgroundColor} bold={column.bold}>
                {formattedValue}
              </Text>
            </InkBox>
            {index < columns.length - 1 ? (
              <Text backgroundColor={backgroundColor}>{' '.repeat(gapWidth)}</Text>
            ) : null}
          </React.Fragment>
        )
      })}
      <Text backgroundColor={backgroundColor}> </Text>
    </InkBox>
  )
}

function DetailValueColumns({
  columns,
  width,
  backgroundColor
}: {
  columns: DetailValueColumn[]
  width: number
  backgroundColor?: string
}) {
  const gapWidth = columns.length > 1 ? 2 : 0
  const widths = weightedColumnWidths(width, columns, gapWidth)

  return (
    <InkBox width="100%">
      <Text backgroundColor={backgroundColor}> </Text>
      {columns.map((column, index) => {
        const columnWidth = widths[index] || 1
        const formattedValue =
          column.valueAlign === 'right'
            ? fitRight(column.value, columnWidth)
            : fit(column.value, columnWidth)
        return (
          <React.Fragment key={`detail-value-${index}-${column.value}`}>
            <Text color={column.color ?? theme.white} backgroundColor={backgroundColor} bold={column.bold}>
              {formattedValue}
            </Text>
            {index < columns.length - 1 ? (
              <Text backgroundColor={backgroundColor}>{' '.repeat(gapWidth)}</Text>
            ) : null}
          </React.Fragment>
        )
      })}
      <Text backgroundColor={backgroundColor}> </Text>
    </InkBox>
  )
}

function PriceHistoryLegendRow({
  sample,
  label,
  color,
  width,
  backgroundColor,
  bold = false
}: {
  sample: string
  label: string
  color: string
  width: number
  backgroundColor?: string
  bold?: boolean
}) {
  const sampleWidth = Math.max(4, Math.min(8, sample.length))
  const labelWidth = Math.max(1, width - sampleWidth - 2)
  return (
    <InkBox width="100%">
      <Text backgroundColor={backgroundColor}> </Text>
      <Text color={color} backgroundColor={backgroundColor} bold={bold}>
        {fit(sample, sampleWidth)}
      </Text>
      <Text backgroundColor={backgroundColor}> </Text>
      <Text color={color} backgroundColor={backgroundColor} bold={bold}>
        {fit(label, labelWidth)}
      </Text>
      <Text backgroundColor={backgroundColor}> </Text>
    </InkBox>
  )
}

function PriceHistoryLegendEntryRow({
  sample,
  label,
  color,
  price,
  seenAt,
  width,
  backgroundColor,
  bold = false
}: {
  sample: string
  label: string
  color: string
  price: string
  seenAt: string
  width: number
  backgroundColor?: string
  bold?: boolean
}) {
  const sampleWidth = Math.max(4, Math.min(8, sample.length))
  const meta = `At ${price}  Seen ${seenAt}`
  const metaWidth = Math.min(meta.length, Math.max(10, width - sampleWidth - 3))
  const labelWidth = Math.max(1, width - sampleWidth - metaWidth - 2)
  return (
    <InkBox width="100%">
      <Text backgroundColor={backgroundColor}> </Text>
      <Text color={color} backgroundColor={backgroundColor} bold={bold}>
        {fit(sample, sampleWidth)}
      </Text>
      <Text backgroundColor={backgroundColor}> </Text>
      <Text color={color} backgroundColor={backgroundColor} bold={bold}>
        {fit(label, labelWidth)}
      </Text>
      <Text backgroundColor={backgroundColor}> </Text>
      <Text color={theme.dim} backgroundColor={backgroundColor}>
        {fitRight(meta, metaWidth)}
      </Text>
      <Text backgroundColor={backgroundColor}> </Text>
    </InkBox>
  )
}

function buildMultiSeriesChart(
  series: PositionPriceSeries[],
  width: number,
  height = 11,
  cursorTs?: number | null,
  referencePrice?: number | null,
  referenceTs?: number | null,
  cursorSelected = false
): {
  rows: ChartCell[][]
  minPrice: number | null
  maxPrice: number | null
  startTs: number | null
  endTs: number | null
  selectedSeries: PositionPriceSeries | null
} {
  const chartHeight = Math.max(4, Math.floor(height))
  const yLabelWidth = 8
  const plotWidth = Math.max(18, Math.floor(width) - yLabelWidth - 1)
  const plotPaddingLeft = 3
  const plotPaddingRight = 3
  const plotDataWidth = Math.max(3, plotWidth - plotPaddingLeft - plotPaddingRight)
  const validSeries = series.filter((entry) => entry.points.length > 0)
  const allPoints = validSeries.flatMap((entry) => entry.points)
  const selectedSeries = validSeries.find((entry) => entry.isSelected) || validSeries[0] || null

  if (!allPoints.length) {
    return {
      rows: Array.from({length: chartHeight + 1}, () =>
        Array.from({length: yLabelWidth + plotWidth}, () => makeCell(' ', undefined))
      ),
      minPrice: null,
      maxPrice: null,
      startTs: null,
      endTs: null,
      selectedSeries
    }
  }

  const rawMinPrice = Math.min(
    ...allPoints.map((point) => point.price),
    referencePrice != null && Number.isFinite(referencePrice) ? referencePrice : 1
  )
  const rawMaxPrice = Math.max(
    ...allPoints.map((point) => point.price),
    referencePrice != null && Number.isFinite(referencePrice) ? referencePrice : 0
  )
  const pricePad = Math.max(0.01, (rawMaxPrice - rawMinPrice) * 0.08)
  const minPrice = Math.max(0, rawMinPrice - pricePad)
  const maxPrice = Math.min(1, rawMaxPrice + pricePad)
  const startTs = Math.min(...allPoints.map((point) => point.ts))
  const endTs = Math.max(...allPoints.map((point) => point.ts))
  const safeEndTs = endTs > startTs ? endTs : startTs + 3600

  const grid = Array.from({length: chartHeight}, () =>
    Array.from({length: plotWidth}, (_, column) => makeCell(column === 0 ? '│' : ' ', column === 0 ? theme.dim : undefined))
  )
  const axisRow = chartHeight - 1
  const tickColumns = new Set([
    plotPaddingLeft,
    plotPaddingLeft + Math.floor(Math.max(plotDataWidth - 1, 0) / 2),
    plotPaddingLeft + Math.max(plotDataWidth - 1, 0)
  ])
  for (let column = 0; column < plotWidth; column += 1) {
    const char =
      column === 0
        ? '└'
        : column === plotWidth - 1
          ? '┘'
          : tickColumns.has(column)
            ? '┴'
            : '─'
    grid[axisRow][column] = makeCell(char, theme.dim)
  }

  const mapPriceToY = (price: number) => {
    const normalizedPrice =
      Math.abs(maxPrice - minPrice) <= 1e-9 ? 0.5 : (price - minPrice) / (maxPrice - minPrice)
    return Math.max(0, Math.min(chartHeight - 2, (chartHeight - 2) - Math.round(normalizedPrice * Math.max(chartHeight - 2, 1))))
  }

  const mapPoint = (point: PriceHistoryPoint) => {
    const x =
      plotPaddingLeft +
      Math.round(
        (((point.ts - startTs) / Math.max(safeEndTs - startTs, 1)) * Math.max(plotDataWidth - 1, 1))
      )
    const y = mapPriceToY(point.price)
    return {x, y}
  }

  const mapTsToX = (ts: number) =>
    plotPaddingLeft +
    Math.round(
      (((ts - startTs) / Math.max(safeEndTs - startTs, 1)) * Math.max(plotDataWidth - 1, 1))
    )

  if (referencePrice != null && Number.isFinite(referencePrice)) {
    const referenceY = mapPriceToY(referencePrice)
    for (let column = 1; column < plotWidth; column += 1) {
      if (column === 0) {
        continue
      }
      grid[referenceY][column] = makeCell('┄', theme.white)
    }
  }

  const referenceColumn =
    referenceTs != null && Number.isFinite(referenceTs) && referenceTs >= startTs && referenceTs <= safeEndTs
      ? Math.max(
          plotPaddingLeft,
          Math.min(plotPaddingLeft + Math.max(plotDataWidth - 1, 0), mapTsToX(referenceTs))
        )
      : null

  if (referenceColumn != null) {
    for (let rowIndex = 0; rowIndex < chartHeight - 1; rowIndex += 1) {
      const current = grid[rowIndex]?.[referenceColumn]
      if (!current || current.char !== ' ') {
        continue
      }
      grid[rowIndex][referenceColumn] = makeCell('┊', theme.white)
    }
    if (grid[axisRow]?.[referenceColumn]) {
      grid[axisRow][referenceColumn] = makeCell('┼', theme.white)
    }
  }

  const cursorColumn =
    cursorTs != null
      ? Math.max(
          plotPaddingLeft,
          Math.min(
            plotPaddingLeft + Math.max(plotDataWidth - 1, 0),
            mapTsToX(cursorTs)
          )
        )
      : null

  if (cursorColumn != null) {
    const cursorColor = cursorSelected ? theme.white : theme.dim
    for (let rowIndex = 0; rowIndex < chartHeight - 1; rowIndex += 1) {
      const current = grid[rowIndex]?.[cursorColumn]
      if (!current || current.char !== ' ') {
        continue
      }
      grid[rowIndex][cursorColumn] = makeCell('┆', cursorColor)
    }
    if (grid[axisRow]?.[cursorColumn]) {
      grid[axisRow][cursorColumn] = makeCell('┼', cursorColor)
    }
  }

  for (const entry of validSeries) {
    const sampled = sampleSeriesPoints(entry.points, plotDataWidth)
    const mapped = sampled.map(mapPoint)
    if (!mapped.length) {
      continue
    }
    for (let index = 0; index < mapped.length - 1; index += 1) {
      const current = mapped[index]
      const next = mapped[index + 1]
      drawHorizontalSegment(grid, current.y, current.x, next.x, entry.color)
      if (next.y !== current.y) {
        drawVerticalSegment(grid, next.x, current.y, next.y, entry.color)
      }
    }
    const changePoints = mapped.filter((point, index) => {
      if (index === 0 || index === mapped.length - 1) {
        return true
      }
      const previous = mapped[index - 1]
      const next = mapped[index + 1]
      return previous?.y !== point.y || next?.y !== point.y
    })
    for (const point of changePoints) {
      drawSeriesCell(grid, point.x, point.y, '.', entry.color)
    }
  }

  const rows = grid.map((plotRow, rowIndex) => {
    const referencePrice =
      maxPrice - ((rowIndex / Math.max(chartHeight - 2, 1)) * (maxPrice - minPrice))
    const showLabel =
      rowIndex === 0 ||
      rowIndex === chartHeight - 2 ||
      rowIndex === Math.floor((chartHeight - 2) / 2)
    const label = showLabel ? fitRight(formatNumber(referencePrice), yLabelWidth) : ' '.repeat(yLabelWidth)
    return [
      ...label.split('').map((char) => makeCell(char, theme.dim)),
      makeCell(' ', undefined),
      ...plotRow
    ]
  })

  const startLabel = startTs ? formatShortDateTime(startTs) : '-'
  const midTs = startTs && endTs ? Math.floor((startTs + endTs) / 2) : 0
  const midLabel = midTs ? formatShortDateTime(midTs) : '-'
  const endLabel = endTs ? formatShortDateTime(endTs) : '-'
  const axisLabelWidth = yLabelWidth + 1 + plotWidth
  const leftWidth = Math.max(1, Math.floor((axisLabelWidth - midLabel.length - endLabel.length) / 2))
  const middleWidth = Math.max(1, axisLabelWidth - leftWidth - endLabel.length)
  const axisLabelRow = `${fit(startLabel, leftWidth)}${fit(midLabel, middleWidth)}${fitRight(endLabel, endLabel.length)}`
  rows.push(axisLabelRow.split('').map((char) => makeCell(char, theme.dim)))

  return {
    rows,
    minPrice: rawMinPrice,
    maxPrice: rawMaxPrice,
    startTs,
    endTs,
    selectedSeries
  }
}

function formatSignedPriceChange(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return '-'
  }
  const rounded = Number(value.toFixed(3))
  return `${rounded > 0 ? '+' : ''}${formatNumber(rounded)}`
}

function PriceHistoryPreview({
  history,
  width,
  backgroundColor,
  selected = false,
  cursorOffsetFromEnd = 0,
  entryPrice = null,
  entryTs = null,
  shares = null
}: {
  history: PositionPriceHistoryState
  width: number
  backgroundColor?: string
  selected?: boolean
  cursorOffsetFromEnd?: number
  entryPrice?: number | null
  entryTs?: number | null
  shares?: number | null
}) {
  const validSeries = history.series.filter((entry) => entry.points.length > 0)
  const timeline = buildHistoryTimeline(validSeries)
  const clampedCursorOffset = timeline.length
    ? Math.max(0, Math.min(Math.floor(cursorOffsetFromEnd), timeline.length - 1))
    : 0
  const cursorIndex = timeline.length ? Math.max(0, timeline.length - 1 - clampedCursorOffset) : null
  const cursorTs = cursorIndex != null ? (timeline[cursorIndex] || null) : null
  const cursorValues: HistoryCursorValue[] =
    cursorTs == null
      ? []
      : validSeries.map((entry) => {
          const point = findNearestHistoryPoint(entry.points, cursorTs)
          return {
            series: entry,
            point: point || null
          }
        })
  const chartRenderHeight = width >= 112 ? 23 : 20
  const chart = buildMultiSeriesChart(validSeries, width, chartRenderHeight, cursorTs, entryPrice, entryTs, selected)
  const selectedSeries = chart.selectedSeries
  const selectedCursorValue =
    cursorValues.find((entry) => entry.series.tokenId === selectedSeries?.tokenId) || cursorValues[0] || null
  const selectedCursorPoint = selectedCursorValue?.point || null
  const selectedCursorChangeFromEntry =
    selectedCursorPoint && entryPrice != null
      ? Number((selectedCursorPoint.price - entryPrice).toFixed(3))
      : null
  const selectedCursorCashoutPnl =
    selectedCursorPoint && entryPrice != null && shares != null && Number.isFinite(shares) && shares > 0
      ? Number((shares * (selectedCursorPoint.price - entryPrice)).toFixed(3))
      : null
  const selectedSeriesColor = selectedSeries?.color ?? theme.white
  const selectedChangeFromEntryLabel = formatSignedPriceChange(selectedCursorChangeFromEntry)
  const selectedCashoutPnlLabel = formatDollar(selectedCursorCashoutPnl)
  const selectedCashoutPnlColor =
    selectedCursorCashoutPnl == null
      ? theme.dim
      : selectedCursorCashoutPnl > 0
        ? theme.green
        : selectedCursorCashoutPnl < 0
          ? theme.red
          : theme.white
  const selectedBackgroundColor = backgroundColor

  return (
    <InkBox flexDirection="column" width="100%">
      {history.status === 'error' ? (
        <Text color={theme.red} backgroundColor={selectedBackgroundColor}>
          {` ${fit(history.message || 'Price history failed to load.', width)} `}
        </Text>
      ) : (
        <Text backgroundColor={selectedBackgroundColor}>{` ${' '.repeat(Math.max(1, width))} `}</Text>
      )}
      {chart.rows.map((row, index) => (
        <InkBox key={`price-history-line-${index}`} width="100%">
          <Text backgroundColor={selectedBackgroundColor}> </Text>
          {renderCellSegments(row, `price-history-cells-${index}`, selectedBackgroundColor)}
          <Text backgroundColor={selectedBackgroundColor}> </Text>
        </InkBox>
      ))}
      <Text backgroundColor={selectedBackgroundColor}>{` ${' '.repeat(Math.max(1, width))} `}</Text>
      <DetailValueColumns
        columns={[
          {
            value: cursorTs ? formatShortDateTime(cursorTs) : '-',
            ratio: 1.6,
            valueAlign: 'left'
          },
          {
            value: selectedSeries?.label || '-',
            color: selectedSeriesColor,
            bold: selectedSeries != null,
            ratio: 1.4,
            valueAlign: 'left'
          },
          {
            value: selectedCursorPoint ? formatNumber(selectedCursorPoint.price) : '-',
            color: selectedSeriesColor,
            valueAlign: 'right'
          },
          {
            value: selectedChangeFromEntryLabel,
            color: theme.white,
            ratio: 1,
            valueAlign: 'right'
          },
          {
            value: selectedCashoutPnlLabel,
            color: selectedCashoutPnlColor,
            ratio: 1.1,
            valueAlign: 'right'
          }
        ]}
        width={width}
        backgroundColor={selectedBackgroundColor}
      />
      <Text backgroundColor={selectedBackgroundColor}>{` ${' '.repeat(Math.max(1, width))} `}</Text>
      <Text color={theme.dim} backgroundColor={selectedBackgroundColor}>
        {` ${fit('Legend', width)} `}
      </Text>
      {entryPrice != null ? (
        <PriceHistoryLegendRow
          sample={'┄┄┄┄'}
          label={`Entry  ${formatNumber(entryPrice)}  ${entryTs ? formatShortDateTime(entryTs) : ''}`.trim()}
          color={theme.white}
          width={width}
          backgroundColor={selectedBackgroundColor}
        />
      ) : null}
      {validSeries.map((entry) => {
        const cursorEntry = cursorValues.find((value) => value.series.tokenId === entry.tokenId)
        const point = cursorEntry?.point || null
        return (
          <PriceHistoryLegendEntryRow
            key={`price-series-legend-${entry.tokenId}`}
            sample={entry.isSelected ? '.-.-.' : '. . .'}
            label={`${entry.isSelected ? '* ' : ''}${entry.label}`}
            color={entry.color}
            bold={entry.isSelected}
            price={point ? formatNumber(point.price) : '-'}
            seenAt={point ? formatShortDateTime(point.ts) : '-'}
            width={width}
            backgroundColor={selectedBackgroundColor}
          />
        )
      })}
    </InkBox>
  )
}

function usePositionPriceHistory(row: PositionRow | null | undefined): PositionPriceHistoryState {
  const [history, setHistory] = useState<PositionPriceHistoryState>(() => emptyPriceHistoryState('idle'))

  useEffect(() => {
    if (!row) {
      setHistory(emptyPriceHistoryState('idle'))
      return
    }

    const marketId = String(row.market_id || '').trim()
    const tokenId = String(row.token_id || '').trim()
    if (!marketId || !tokenId) {
      setHistory({
        ...emptyPriceHistoryState('error'),
        message: 'This position is missing market metadata keys, so price history is unavailable.'
      })
      return
    }

    let cancelled = false

    const loadHistory = async (showLoading: boolean) => {
      if (showLoading) {
        setHistory((current) => ({
          ...emptyPriceHistoryState('loading'),
          message: current.status === 'error' ? current.message : undefined
        }))
      }

      try {
        const marketResponse = await fetch(
          `https://gamma-api.polymarket.com/markets?condition_ids=${encodeURIComponent(marketId)}`
        )
        if (!marketResponse.ok) {
          throw new Error(`metadata HTTP ${marketResponse.status}`)
        }
        const marketPayload = await marketResponse.json()
        const markets = Array.isArray(marketPayload)
          ? marketPayload
          : marketPayload && typeof marketPayload === 'object' && Array.isArray((marketPayload as {markets?: unknown[]}).markets)
            ? (marketPayload as {markets: unknown[]}).markets
            : []
        const meta =
          (markets.find(
            (entry) => entry && typeof entry === 'object' && String((entry as {conditionId?: unknown}).conditionId || '').trim().toLowerCase() === marketId.toLowerCase()
          ) as Record<string, unknown> | undefined) ||
          (markets[0] && typeof markets[0] === 'object' ? (markets[0] as Record<string, unknown>) : null)
        const outcomeDefs = extractOutcomeSeries(row, meta)
        const seriesResults = await Promise.allSettled(
          outcomeDefs.map(async (definition, index) => {
            const points = await fetchClobPriceHistory(definition.tokenId)
            return {
              tokenId: definition.tokenId,
              label: definition.label,
              color: seriesColorForOutcome(definition.label, index, definition.tokenId === tokenId),
              isSelected: definition.tokenId === tokenId,
              points
            } satisfies PositionPriceSeries
          })
        )
        if (cancelled) {
          return
        }
        const populatedSeries = seriesResults
          .flatMap((result) => (result.status === 'fulfilled' ? [result.value] : []))
          .filter((entry) => entry.points.length > 0)
        setHistory({
          status: 'ready',
          fetchedAt: Math.floor(Date.now() / 1000),
          series: populatedSeries,
          message: populatedSeries.length ? undefined : 'No market history was returned for this market.'
        })
      } catch (error) {
        if (cancelled) {
          return
        }
        setHistory({
          ...emptyPriceHistoryState('error'),
          message: `Price history failed: ${error instanceof Error ? error.message : 'unknown error'}`
        })
      }
    }

    void loadHistory(true)
    const timer = setInterval(() => {
      void loadHistory(false)
    }, 60000)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [row?.row_key, row?.token_id])

  return history
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
  actionState,
  editState,
  onCurrentScrollOffsetChange,
  onPastScrollOffsetChange,
  onDailyDetailScrollOffsetChange,
  onSelectionMetaChange,
  onDetailHistoryMetaChange
}: PerformanceProps) {
  const terminal = useTerminalSize()
  const stacked = stackPanels(terminal.width)
  const shadowOpenPositions = useQuery<PositionRow>(SHADOW_OPEN_POSITIONS_SQL)
  const livePositions = useQuery<PositionRow>(LIVE_POSITIONS_SQL)
  const resolvedPositions = useQuery<PositionRow>(RESOLVED_POSITIONS_SQL)
  const tradeLogManualEdits = useQuery<TradeLogManualEditRow>(TRADE_LOG_MANUAL_EDITS_SQL)
  const positionManualEdits = useQuery<PositionManualEditRow>(POSITION_MANUAL_EDITS_SQL)
  const events = useEventStream(1000)
  const {lookup: tradeIdLookup} = useTradeIdIndex(events)
  const botState = useBotState(1000)
  const detailHistoryRow = actionState?.row ?? editState?.row ?? null
  const positionPriceHistory = usePositionPriceHistory(detailHistoryRow)
  const detailSelectedSeriesColor = useMemo(
    () => positionPriceHistory.series.find((entry) => entry.isSelected)?.color ?? outcomeColor(detailHistoryRow?.side || ''),
    [detailHistoryRow?.side, positionPriceHistory.series]
  )
  const detailHistoryTimelineCount = useMemo(
    () => buildHistoryTimeline(positionPriceHistory.series).length,
    [positionPriceHistory.series]
  )
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
  const livePositionProfitLookup = useLivePositionProfitLookup(currentPositions)
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
  const resolvedPerformancePositions = useMemo(
    () =>
      effectivePositions.filter(
        (row) => row.status === 'win' || row.status === 'lose' || row.status === 'exit'
      ),
    [effectivePositions]
  )
  const confidenceRows = useMemo(
    () => effectivePositions.filter((row) => row.confidence != null),
    [effectivePositions]
  )
  const activeSummary = useMemo<ComputedSummary>(
    () => {
      const acted = effectivePositions.length
      const wins = resolvedPerformancePositions.filter((row) => (row.pnl_usd ?? 0) > 0).length
      const totalPnl = roundTo(
        resolvedPerformancePositions.reduce((sum, row) => sum + (row.pnl_usd || 0), 0),
        3
      )
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
        resolved: resolvedPerformancePositions.length,
        wins,
        total_pnl: resolvedPerformancePositions.length ? totalPnl : 0,
        avg_confidence: avgConfidence,
        avg_size: avgSize
      }
    },
    [confidenceRows, effectivePositions, resolvedPerformancePositions]
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
  const deployedCapital = useMemo(
    () => roundTo(currentPositionsTotal + waitingPositionsTotal, 3),
    [currentPositionsTotal, waitingPositionsTotal]
  )
  const activeEquity = useMemo(
    () => (activeBalance == null ? null : roundTo(activeBalance + deployedCapital, 3)),
    [activeBalance, deployedCapital]
  )
  const activeStartingBankroll = useMemo(
    () =>
      activeEquity != null
        ? roundTo(activeEquity - Number(activeSummary?.total_pnl || 0), 3)
        : null,
    [activeEquity, activeSummary?.total_pnl]
  )
  const activePerformanceStats = useMemo(() => {
    const totalPnl = Number(activeSummary?.total_pnl || 0)
    const grossProfit = roundTo(
      resolvedPerformancePositions.reduce((sum, row) => sum + Math.max(Number(row.pnl_usd || 0), 0), 0),
      3
    )
    const grossLoss = roundTo(
      resolvedPerformancePositions.reduce((sum, row) => sum + Math.abs(Math.min(Number(row.pnl_usd || 0), 0)), 0),
      3
    )
    const profitFactor =
      grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Number.POSITIVE_INFINITY : null
    const expectancyUsd =
      resolvedPerformancePositions.length > 0 ? roundTo(totalPnl / resolvedPerformancePositions.length, 3) : null
    const expectancyReturns = resolvedPerformancePositions
      .filter((row) => row.pnl_usd != null && row.size_usd > 0)
      .map((row) => Number(row.pnl_usd || 0) / row.size_usd)
    const expectancyPct =
      expectancyReturns.length > 0
        ? roundTo(
            expectancyReturns.reduce((sum, value) => sum + value, 0) / expectancyReturns.length,
            4
          )
        : null
    const returnPct =
      activeStartingBankroll != null && activeStartingBankroll > 0
        ? roundTo(totalPnl / activeStartingBankroll, 4)
        : null
    const exposurePct =
      activeEquity != null && activeEquity > 0 ? roundTo(deployedCapital / activeEquity, 4) : null
    const orderedResolved = [...resolvedPerformancePositions].sort((left, right) => {
      const leftTs = left.resolution_ts || left.market_close_ts || left.entered_at || 0
      const rightTs = right.resolution_ts || right.market_close_ts || right.entered_at || 0
      if (leftTs !== rightTs) {
        return leftTs - rightTs
      }
      if (left.entered_at !== right.entered_at) {
        return left.entered_at - right.entered_at
      }
      return left.row_key.localeCompare(right.row_key)
    })
    let maxDrawdownPct = 0
    if (activeStartingBankroll != null && activeStartingBankroll > 0) {
      let runningEquity = activeStartingBankroll
      let peakEquity = activeStartingBankroll
      for (const row of orderedResolved) {
        runningEquity += Number(row.pnl_usd || 0)
        peakEquity = Math.max(peakEquity, runningEquity)
        if (peakEquity > 0) {
          maxDrawdownPct = Math.max(maxDrawdownPct, (peakEquity - runningEquity) / peakEquity)
        }
      }
    }
    return {
      exposurePct,
      expectancyPct,
      expectancyUsd,
      maxDrawdownPct:
        activeStartingBankroll != null && activeStartingBankroll > 0
          ? roundTo(maxDrawdownPct, 4)
          : null,
      profitFactor,
      returnPct
    }
  }, [activeEquity, activeStartingBankroll, activeSummary?.total_pnl, deployedCapital, resolvedPerformancePositions])
  const expectancyValue =
    activePerformanceStats.expectancyUsd == null && activePerformanceStats.expectancyPct == null
      ? '-'
      : [
          activePerformanceStats.expectancyUsd != null
            ? formatDollar(activePerformanceStats.expectancyUsd)
            : null,
          activePerformanceStats.expectancyPct != null
            ? formatPct(activePerformanceStats.expectancyPct, 1)
            : null
        ]
          .filter(Boolean)
          .join(' / ')
  const profitFactorValue =
    activePerformanceStats.profitFactor == null
      ? '-'
      : Number.isFinite(activePerformanceStats.profitFactor)
        ? formatNumber(activePerformanceStats.profitFactor, 2)
        : 'inf'
  const summaryLeftStats = [
    {
      label: 'Total P&L',
      value: formatDollar(activeSummary?.total_pnl),
      color: (activeSummary?.total_pnl || 0) >= 0 ? theme.green : theme.red
    },
    {
      label: 'Return %',
      value: formatPct(activePerformanceStats.returnPct, 1),
      color:
        activePerformanceStats.returnPct == null
          ? theme.dim
          : activePerformanceStats.returnPct >= 0
            ? theme.green
            : theme.red
    },
    {
      label: 'Win rate',
      value: activeSummary ? formatPct(activeSummary.resolved ? activeSummary.wins / activeSummary.resolved : 0) : '-'
    },
    {
      label: 'Profit factor',
      value: profitFactorValue,
      color:
        activePerformanceStats.profitFactor == null
          ? theme.dim
          : activePerformanceStats.profitFactor >= 1
            ? theme.green
            : theme.red
    },
    {
      label: 'Expectancy',
      value: expectancyValue,
      color:
        activePerformanceStats.expectancyUsd == null
          ? theme.dim
          : activePerformanceStats.expectancyUsd >= 0
            ? theme.green
            : theme.red
    },
    {
      label: 'Resolved',
      value: String(activeSummary?.resolved || 0)
    }
  ]
  const summaryRightStats = [
    {
      label: 'Current balance',
      value: activeBalance == null ? '-' : `$${activeBalance.toFixed(3)}`,
      color: activeBalance != null ? theme.white : theme.dim
    },
    {
      label: 'Exposure',
      value: formatPct(activePerformanceStats.exposurePct, 1),
      color: activePerformanceStats.exposurePct != null ? theme.yellow : theme.dim
    },
    {
      label: 'Max drawdown',
      value: formatPct(activePerformanceStats.maxDrawdownPct, 1),
      color:
        activePerformanceStats.maxDrawdownPct == null
          ? theme.dim
          : activePerformanceStats.maxDrawdownPct > 0
            ? theme.red
            : theme.white
    },
    {
      label: 'Avg confidence',
      value: formatPct(activeSummary?.avg_confidence)
    },
    {
      label: 'Avg total',
      value: formatDollar(activeSummary?.avg_size)
    }
  ]
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

  useEffect(() => {
    onDetailHistoryMetaChange?.({
      timelineCount: detailHistoryTimelineCount
    })
  }, [detailHistoryTimelineCount, onDetailHistoryMetaChange])

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
  const actionModalWidth = Math.max(82, Math.min(terminal.width - 4, terminal.wide ? 128 : 105))
  const actionModalContentWidth = Math.max(57, actionModalWidth - 4)
  const actionFieldLabelWidth = Math.max(10, Math.min(12, Math.floor(actionModalContentWidth * 0.26)))
  const actionFieldValueWidth = Math.max(14, actionModalContentWidth - actionFieldLabelWidth - 1)
  const actionQuestionText = actionState
    ? truncate(actionState.row.question || actionState.row.market_id, actionModalContentWidth)
    : ''
  const actionInstructionText =
    actionState?.editingAmount
      ? 'Type a positive USD amount. Enter closes the field editor. Esc leaves the field editor.'
      : actionState?.selectedField === 'chart'
        ? 'Chart selected. Use left/right to scrub through time. Up/down moves to the operator controls.'
        : 'Up/down selects a field. Left/right changes action. Enter edits or triggers the selected row. s submits. Esc cancels.'
  const editModalWidth = Math.max(78, Math.min(terminal.width - 4, terminal.wide ? 123 : 100))
  const editModalContentWidth = Math.max(53, editModalWidth - 4)
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
  const editQuestionText = editState
    ? truncate(editState.row.question || editState.row.market_id, editModalContentWidth)
    : ''
  const editInstructionText =
    editState?.editingField
      ? 'Type a positive number. Enter closes the field editor. Esc leaves the field editor.'
      : editState?.selectedField === 'chart'
        ? 'Chart selected. Use left/right to scrub through time. Up/down moves to the edit fields.'
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
    const displayProfit = (row: PositionRow): number | null => {
      if (row.status === 'open' || row.status === 'waiting') {
        return row.shares != null ? roundTo(Number(row.shares) - Number(row.size_usd || 0), 3) : null
      }
      return computePositionProfit(row)
    }
    const displayCashOutNow = (row: PositionRow): number | null => {
      if (row.status === 'open' || row.status === 'waiting') {
        return livePositionProfitLookup.get(row.row_key) ?? null
      }
      return null
    }
    const trailingWidth = positionsLayout.ttrWidth
    const trailingDelta = trailingWidth - positionsLayout.ttrWidth
    const questionWidth = Math.max(14, positionsLayout.questionWidth - trailingDelta)
    const resolutionWidth = positionsLayout.resolutionWidth
    const maxAbsProfit = profitScaleRows.reduce(
      (max, row) => Math.max(max, Math.abs(displayProfit(row) ?? 0)),
      0
    )
    const maxAbsCashOut = profitScaleRows.reduce(
      (max, row) => Math.max(max, Math.abs(displayCashOutNow(row) ?? 0)),
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
          <Text color={theme.dim}>{fitRight('CASH NOW', positionsLayout.cashOutWidth)}</Text>
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
            const profit = displayProfit(row)
            const cashOutNow = displayCashOutNow(row)
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
            const cashOutColor =
              cashOutNow == null
                ? theme.dim
                : centeredGradientColor(cashOutNow, maxAbsCashOut || 1)

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
                <Text color={cashOutColor} backgroundColor={rowBackground} bold={isSelected}>
                  {fitRight(
                    cashOutNow != null
                      ? formatAdaptiveDollar(cashOutNow, positionsLayout.cashOutWidth)
                      : '-',
                    positionsLayout.cashOutWidth
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

  const renderPageBody = () => (
    <>
      <InkBox flexDirection={stacked ? 'column' : 'row'}>
        <Box title={activeTitle} width={stacked ? '100%' : '50%'} accent={selectedBox === 'summary'}>
          <InkBox width="100%" flexDirection="row">
            <InkBox flexDirection="column" flexGrow={1}>
              {summaryLeftStats.map((stat) => (
                <StatRow key={stat.label} label={stat.label} value={stat.value} color={stat.color} />
              ))}
            </InkBox>
            <InkBox width={2} />
            <InkBox flexDirection="column" flexGrow={1}>
              {summaryRightStats.map((stat) => (
                <StatRow key={stat.label} label={stat.label} value={stat.value} color={stat.color} />
              ))}
            </InkBox>
          </InkBox>
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
    </>
  )

  return (
    <InkBox flexDirection="column" width="100%">
      {renderPageBody()}
      {actionState ? (
        <ModalOverlay backgroundColor={terminal.backgroundColor}>
          <InkBox borderStyle="round" borderColor={theme.accent} flexDirection="column" width={actionModalWidth}>
            <Text
              color={actionState.row.market_url ? theme.accent : theme.dim}
              backgroundColor={modalBackground}
              bold={Boolean(actionState.row.market_url)}
            >
              {` ${actionState.row.market_url ? terminalHyperlink(fit(actionQuestionText, actionModalContentWidth), actionState.row.market_url) : fit(actionQuestionText, actionModalContentWidth)} `}
            </Text>
            <DetailMetricColumns
              columns={[
                {label: 'Side', value: actionState.row.side.toUpperCase(), color: detailSelectedSeriesColor},
                {label: 'Size', value: formatAdaptiveDollar(actionState.row.size_usd, 10)},
                {
                  label: 'Pos',
                  value: actionState.row.shares != null ? `${formatAdaptiveNumber(actionState.row.shares, 10)} sh` : '-',
                  ratio: 1.2
                },
                {label: 'Entry', value: formatNumber(actionState.row.entry_price)},
                {label: 'Entered', value: secondsAgo(actionState.row.entered_at)}
              ]}
              width={actionModalContentWidth}
              backgroundColor={modalBackground}
            />
            <PriceHistoryPreview
              history={positionPriceHistory}
              width={actionModalContentWidth}
              backgroundColor={modalBackground}
              selected={actionState.selectedField === 'chart'}
              cursorOffsetFromEnd={actionState.historyCursorOffset}
              entryPrice={actionState.row.entry_price}
              entryTs={actionState.row.entered_at}
              shares={actionState.row.shares}
            />
            <Text backgroundColor={modalBackground}>{' '.repeat(actionModalWidth - 2)}</Text>
            {([
              ['action', actionState.action === 'buy_more' ? 'BUY MORE' : 'CASH OUT', false],
              ...(actionState.action === 'buy_more'
                ? ([['amount', actionState.editingAmount ? `${actionState.draftAmountUsd}_` : actionState.draftAmountUsd, actionState.editingAmount]] as Array<[PerfPositionActionField, string, boolean]>)
                : []),
              ['execute', actionState.action === 'buy_more' ? 'SEND BUY REQUEST' : 'SEND CASH-OUT REQUEST', false],
              ['edit', 'OPEN MANUAL EDIT', false]
            ] as Array<[PerfPositionActionField, string, boolean]>).map(([field, value]) => {
              const selected = actionState.selectedField === field
              const rowBackground = selected ? selectedRowBackground : modalBackground
              const label =
                field === 'action'
                  ? 'ACTION'
                  : field === 'amount'
                    ? 'BUY USD'
                    : field === 'execute'
                      ? 'EXECUTE'
                      : 'EDIT'
              return (
                <InkBox key={`action-field-${field}`} width="100%">
                  <Text color={selected ? theme.accent : theme.dim} backgroundColor={rowBackground} bold={selected}>
                    {` ${fit(`${selected ? '>' : ' '} ${label}`, actionFieldLabelWidth)} `}
                  </Text>
                  <Text color={selected ? theme.white : theme.dim} backgroundColor={rowBackground} bold={selected}>
                    {`${fitRight(value, actionFieldValueWidth)} `}
                  </Text>
                </InkBox>
              )
            })}
            <Text backgroundColor={modalBackground}>{' '.repeat(actionModalWidth - 2)}</Text>
            <Text color={actionToneColor(actionState.statusTone)} backgroundColor={modalBackground}>
              {` ${fit((actionState.statusMessage || actionInstructionText).trim(), actionModalContentWidth)} `}
            </Text>
          </InkBox>
        </ModalOverlay>
      ) : null}

      {editState ? (
        <ModalOverlay backgroundColor={terminal.backgroundColor}>
          <InkBox borderStyle="round" borderColor={theme.accent} flexDirection="column" width={editModalWidth}>
            <Text
              color={editState.row.market_url ? theme.accent : theme.dim}
              backgroundColor={modalBackground}
              bold={Boolean(editState.row.market_url)}
            >
              {` ${editState.row.market_url ? terminalHyperlink(fit(editQuestionText, editModalContentWidth), editState.row.market_url) : fit(editQuestionText, editModalContentWidth)} `}
            </Text>
            <DetailMetricColumns
              columns={[
                {label: 'Side', value: editState.row.side.toUpperCase(), color: detailSelectedSeriesColor},
                {label: 'Size', value: formatAdaptiveDollar(editState.row.size_usd, 10)},
                {label: 'Status', value: editState.row.status.toUpperCase()},
                {label: 'Entered', value: secondsAgo(editState.row.entered_at), ratio: 1.1}
              ]}
              width={editModalContentWidth}
              backgroundColor={modalBackground}
            />
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
            <DetailMetricColumns
              columns={[
                {
                  label: 'Preview P&L',
                  value: editPreviewPnl == null ? '-' : formatDollar(editPreviewPnl),
                  ratio: 1.2
                },
                {
                  label: 'To win',
                  value: editDraftSharesValue == null ? '-' : formatAdaptiveDollar(editDraftSharesValue, 10),
                  ratio: 1.1
                }
              ]}
              width={editModalContentWidth}
              backgroundColor={modalBackground}
            />
            <PriceHistoryPreview
              history={positionPriceHistory}
              width={editModalContentWidth}
              backgroundColor={modalBackground}
              selected={editState.selectedField === 'chart'}
              cursorOffsetFromEnd={editState.historyCursorOffset}
              entryPrice={editDraftEntryValue ?? editState.row.entry_price}
              entryTs={editState.row.entered_at}
              shares={editDraftSharesValue ?? editState.row.shares}
            />
            <Text color={toneColor(editState.statusTone)} backgroundColor={modalBackground}>
              {` ${fit((editState.statusMessage || editInstructionText).trim(), editModalContentWidth)} `}
            </Text>
          </InkBox>
        </ModalOverlay>
      ) : null}

      {dailyDetailOpen ? (
        <ModalOverlay backgroundColor={terminal.backgroundColor}>
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
        </ModalOverlay>
      ) : null}
    </InkBox>
  )
}
