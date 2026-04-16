import React, {useEffect, useMemo} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {ModalOverlay} from '../components/ModalOverlay.js'
import {useDashboardConfig} from '../configEditor.js'
import {fit, fitRight, formatPct, formatShortDateTime, secondsAgo, shortAddress, terminalHyperlink, truncate, wrapText} from '../format.js'
import {isPlaceholderUsername, useIdentityMap} from '../identities.js'
import {rowsForHeight} from '../responsive.js'
import {useTerminalSize} from '../terminal.js'
import {centeredGradientColor, negativeHeatColor, positiveDollarColor, probabilityColor, selectionBackgroundColor, theme} from '../theme.js'
import {useQuery} from '../useDb.js'
import {useEventStream} from '../useEventStream.js'
import type {BotState} from '../useBotState.js'

interface WalletActivityRow {
  trader_address: string
  buy_signals: number | null
  skipped_trades: number | null
  uncopyable_skips: number | null
  seen_trades: number | null
  seen_resolved: number | null
  seen_wins: number | null
  local_pnl: number | null
  last_seen: number | null
  observed_resolved: number | null
  observed_wins: number | null
}

interface TraderCacheRow {
  trader_address: string
  win_rate: number | null
  n_trades: number | null
  avg_return: number | null
  consistency: number | null
  volume_usd: number | null
  avg_size_usd: number | null
  diversity: number | null
  account_age_d: number | null
  wins: number | null
  ties: number | null
  realized_pnl_usd: number | null
  open_positions: number | null
  open_value_usd: number | null
  open_pnl_usd: number | null
  updated_at: number | null
}

interface WalletCursorRow {
  wallet_address: string
  last_source_ts: number | null
}

interface WalletWatchStateRow {
  wallet_address: string
  status: string | null
  status_reason: string | null
  dropped_at: number | null
  reactivated_at: number | null
  tracking_started_at: number | null
  last_source_ts_at_status: number | null
  updated_at: number | null
}

interface WalletPolicyMetricRow {
  wallet_address: string
  total_buy_signals: number | null
  resolved_copied_count: number | null
  resolved_copied_wins: number | null
  resolved_copied_win_rate: number | null
  resolved_copied_avg_return: number | null
  resolved_copied_total_pnl_usd: number | null
  recent_window_seconds: number | null
  recent_resolved_copied_count: number | null
  recent_resolved_copied_wins: number | null
  recent_resolved_copied_win_rate: number | null
  recent_resolved_copied_avg_return: number | null
  recent_resolved_copied_total_pnl_usd: number | null
  last_resolved_at: number | null
  local_quality_score: number | null
  local_weight: number | null
  local_drop_ready: number | null
  local_drop_reason: string | null
  updated_at: number | null
}

type WatchTier = 'HOT' | 'WARM' | 'DISC'
export type WalletPane = 'best' | 'worst' | 'tracked' | 'dropped'

export interface WalletMeta {
  bestCount: number
  worstCount: number
  trackedCount: number
  droppedCount: number
  bestWalletAddresses: string[]
  worstWalletAddresses: string[]
  trackedWalletAddresses: string[]
  droppedWalletAddresses: string[]
}

export interface WalletDetailHistoryMeta {
  maxOffset: number
}

interface WalletRow {
  trader_address: string
  username: string
  watch_tier: WatchTier
  buy_signals: number | null
  skipped_trades: number | null
  skip_rate: number | null
  uncopyable_skips: number | null
  uncopyable_skip_rate: number | null
  seen_trades: number | null
  seen_resolved: number | null
  seen_wins: number | null
  seen_win_rate: number | null
  local_pnl: number | null
  last_seen: number | null
  observed_resolved: number | null
  observed_wins: number | null
  observed_win_rate: number | null
  closed_trades: number | null
  win_rate: number | null
  consistency: number | null
  volume_usd: number | null
  avg_size_usd: number | null
  diversity: number | null
  account_age_d: number | null
  wins: number | null
  ties: number | null
  realized_pnl_usd: number | null
  open_positions: number | null
  open_value_usd: number | null
  open_pnl_usd: number | null
  updated_at: number | null
  last_source_ts: number | null
  status: 'active' | 'dropped'
  status_reason: string | null
  dropped_at: number | null
  reactivated_at: number | null
  tracking_started_at: number | null
  last_source_ts_at_status: number | null
  watch_index: number
  resolved_copied_avg_return: number | null
  resolved_copied_total_pnl_usd: number | null
  recent_resolved_copied_count: number | null
  recent_resolved_copied_avg_return: number | null
  recent_resolved_copied_total_pnl_usd: number | null
  recent_window_seconds: number | null
  local_quality_score: number | null
  local_weight: number | null
  local_drop_ready: boolean
  local_drop_reason: string | null
}

interface TopShadowRow {
  trader_address: string
  n: number
  wins: number
  resolved: number
  seen_trades: number
  skipped_trades: number
  pnl: number | null
}

interface WalletPnlHistoryRow {
  resolved_ts: number | null
  pnl_usd: number | null
}

interface WalletPnlPoint {
  ts: number
  pnl: number
}

interface WalletChartCell {
  char: string
  color?: string
}

interface ShadowLeaderboards {
  best: TopShadowRow[]
  worst: TopShadowRow[]
}

interface WalletsLayout {
  usernameWidth: number
  addressWidth: number
  trackingSinceWidth: number
  tierWidth: number
  skippedTradesWidth: number
  seenTradesWidth: number
  seenWinRateWidth: number
  observedResolvedWidth: number
  observedWinRateWidth: number
  profileWinRateWidth: number
  copyPnlWidth: number
  lastSeenWidth: number
}

interface DroppedWalletsLayout {
  usernameWidth: number
  addressWidth: number
  reasonWidth: number
  lastSeenWidth: number
  droppedWidth: number
}

interface WalletsProps {
  botState: BotState
  activePane: WalletPane
  bestSelectedIndex: number
  worstSelectedIndex: number
  trackedSelectedIndex: number
  droppedSelectedIndex: number
  detailOpen: boolean
  detailHistoryOffset: number
  onWalletMetaChange?: (meta: WalletMeta) => void
  onDetailHistoryMetaChange?: (meta: WalletDetailHistoryMeta) => void
}

interface WalletDetailMetric {
  label: string
  value: string
  color?: string
}

interface WalletDetailSection {
  title: string
  metrics: WalletDetailMetric[]
}

interface WalletDetailLine {
  kind: 'blank' | 'heading' | 'metric'
  text?: string
  label?: string
  value?: string
  valueColor?: string
}

const EXECUTED_ENTRY_WHERE = `
skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
`

const RESOLVED_EXECUTED_ENTRY_WHERE = `
${EXECUTED_ENTRY_WHERE}
AND COALESCE(actual_pnl_usd, shadow_pnl_usd) IS NOT NULL
`

const CHAMPION_TRADE_LOG_WHERE = `
LOWER(COALESCE(experiment_arm, 'champion')) = 'champion'
`

const UNCOPYABLE_TIMING_WHERE = `
(
  market_veto LIKE 'expires in <%'
  OR market_veto LIKE 'beyond max horizon %'
)
`

const UNCOPYABLE_LIQUIDITY_WHERE = `
(
  market_veto='missing order book'
  OR market_veto='no visible order book depth'
  OR skip_reason LIKE 'shadow simulation rejected the buy because the order book had no asks%'
  OR skip_reason LIKE 'shadow simulation rejected the buy because there was not enough ask depth%'
)
`

const UNCOPYABLE_SKIP_WHERE = `
(
  ${UNCOPYABLE_TIMING_WHERE}
  OR ${UNCOPYABLE_LIQUIDITY_WHERE}
)
`

const WALLET_ACTIVITY_SQL = `
SELECT
  trader_address,
  SUM(CASE WHEN COALESCE(source_action, 'buy')='buy' THEN 1 ELSE 0 END) AS buy_signals,
  SUM(CASE WHEN skipped=1 THEN 1 ELSE 0 END) AS skipped_trades,
  SUM(
    CASE
      WHEN COALESCE(source_action, 'buy')='buy' AND ${UNCOPYABLE_SKIP_WHERE} THEN 1
      ELSE 0
    END
  ) AS uncopyable_skips,
  COUNT(*) AS seen_trades,
  SUM(
    CASE
      WHEN COALESCE(source_action, 'buy')='buy' AND outcome IS NOT NULL THEN 1
      ELSE 0
    END
  ) AS seen_resolved,
  SUM(
    CASE
      WHEN COALESCE(source_action, 'buy')='buy' AND outcome=1 THEN 1
      ELSE 0
    END
  ) AS seen_wins,
  ROUND(SUM(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN COALESCE(shadow_pnl_usd, actual_pnl_usd) ELSE 0 END), 3) AS local_pnl,
  MAX(placed_at) AS last_seen,
  SUM(
    CASE
      WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} THEN 1
      ELSE 0
    END
  ) AS observed_resolved,
  SUM(
    CASE
      WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} AND COALESCE(shadow_pnl_usd, actual_pnl_usd) > 0 THEN 1
      ELSE 0
    END
  ) AS observed_wins
FROM trade_log
WHERE ${CHAMPION_TRADE_LOG_WHERE}
GROUP BY trader_address
`

const TRADER_CACHE_SQL = `
SELECT
  trader_address,
  win_rate,
  n_trades,
  avg_return,
  consistency,
  volume_usd,
  avg_size_usd,
  diversity,
  account_age_d,
  wins,
  ties,
  realized_pnl_usd,
  open_positions,
  open_value_usd,
  open_pnl_usd,
  updated_at
FROM trader_cache
`

const WALLET_CURSOR_SQL = `
SELECT wallet_address, last_source_ts
FROM wallet_cursors
`

const WALLET_WATCH_STATE_SQL = `
SELECT
  wallet_address,
  status,
  status_reason,
  dropped_at,
  reactivated_at,
  tracking_started_at,
  last_source_ts_at_status,
  updated_at
FROM wallet_watch_state
`

const WALLET_POLICY_METRICS_SQL = `
SELECT
  wallet_address,
  total_buy_signals,
  resolved_copied_count,
  resolved_copied_wins,
  resolved_copied_win_rate,
  resolved_copied_avg_return,
  resolved_copied_total_pnl_usd,
  recent_window_seconds,
  recent_resolved_copied_count,
  recent_resolved_copied_wins,
  recent_resolved_copied_win_rate,
  recent_resolved_copied_avg_return,
  recent_resolved_copied_total_pnl_usd,
  last_resolved_at,
  local_quality_score,
  local_weight,
  local_drop_ready,
  local_drop_reason,
  updated_at
FROM wallet_policy_metrics
`

const SHADOW_WALLETS_SQL = `
SELECT
  trader_address,
  COUNT(*) AS n,
  COUNT(*) AS seen_trades,
  SUM(CASE WHEN skipped=1 THEN 1 ELSE 0 END) AS skipped_trades,
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} AND shadow_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} THEN 1 ELSE 0 END) AS resolved,
  ROUND(SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} THEN shadow_pnl_usd ELSE 0 END), 3) AS pnl
FROM trade_log
WHERE real_money=0
  AND ${CHAMPION_TRADE_LOG_WHERE}
GROUP BY trader_address
HAVING SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} THEN 1 ELSE 0 END) > 0
`

const WALLET_LOCAL_PNL_HISTORY_SQL = `
SELECT
  COALESCE(exited_at, resolved_at, placed_at) AS resolved_ts,
  ROUND(COALESCE(shadow_pnl_usd, actual_pnl_usd), 3) AS pnl_usd
FROM trade_log
WHERE trader_address=?
  AND COALESCE(source_action, 'buy')='buy'
  AND ${CHAMPION_TRADE_LOG_WHERE}
  AND ${RESOLVED_EXECUTED_ENTRY_WHERE}
ORDER BY resolved_ts ASC, id ASC
`

function readWatchConfig(envValues: Record<string, string>): {
  wallets: string[]
  hotCount: number
  warmCount: number
  uncopyablePenaltyMinBuys: number
  uncopyablePenaltyWeight: number
} {
  const wallets = String(envValues.WATCHED_WALLETS || '')
    .split(',')
    .map((wallet) => wallet.trim().toLowerCase())
    .filter(Boolean)

  let hotCount = 12
  let warmCount = 24
  let uncopyablePenaltyMinBuys = 12
  let uncopyablePenaltyWeight = 0.25
  const parsedHotCount = Number.parseInt(String(envValues.HOT_WALLET_COUNT || ''), 10)
  if (Number.isFinite(parsedHotCount) && parsedHotCount > 0) {
    hotCount = parsedHotCount
  }
  const parsedWarmCount = Number.parseInt(String(envValues.WARM_WALLET_COUNT || ''), 10)
  if (Number.isFinite(parsedWarmCount) && parsedWarmCount >= 0) {
    warmCount = parsedWarmCount
  }
  const parsedPenaltyMinBuys = Number.parseInt(String(envValues.WALLET_UNCOPYABLE_PENALTY_MIN_BUYS || ''), 10)
  if (Number.isFinite(parsedPenaltyMinBuys) && parsedPenaltyMinBuys >= 0) {
    uncopyablePenaltyMinBuys = parsedPenaltyMinBuys
  }
  const parsedPenaltyWeight = Number.parseFloat(String(envValues.WALLET_UNCOPYABLE_PENALTY_WEIGHT || ''))
  if (Number.isFinite(parsedPenaltyWeight) && parsedPenaltyWeight >= 0) {
    uncopyablePenaltyWeight = parsedPenaltyWeight
  }
  return {wallets, hotCount, warmCount, uncopyablePenaltyMinBuys, uncopyablePenaltyWeight}
}

function formatAddress(value: string, width: number): string {
  if (width <= 0) return ''
  if (!value) return '-'.padEnd(width)
  if (value.length <= width) return value.padEnd(width)
  if (width <= 12) return fit(value, width)

  const visible = width - 3
  const prefixWidth = Math.max(8, Math.ceil(visible * 0.65))
  const suffixWidth = Math.max(4, visible - prefixWidth)
  return `${value.slice(0, prefixWidth)}...${value.slice(-suffixWidth)}`
}

function walletProfileUrl(wallet: Pick<WalletRow, 'trader_address' | 'username'>): string | null {
  if (!wallet.username) {
    return null
  }

  const normalizedWallet = wallet.trader_address.trim().toLowerCase()
  return normalizedWallet ? `https://polymarket.com/profile/${normalizedWallet}` : null
}

function formatSignedMoney(value: number | null | undefined, width: number): string {
  if (value == null || Number.isNaN(value)) return '-'
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  const abs = Math.abs(value)
  for (let digits = 3; digits >= 0; digits -= 1) {
    const formatted = `${sign}$${abs.toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    })}`
    if (formatted.length <= width) {
      return formatted
    }
  }
  return `${sign}$${Math.round(abs).toLocaleString('en-US')}`
}

function formatUnsignedMoney(value: number | null | undefined, width: number): string {
  if (value == null || Number.isNaN(value)) return '-'
  for (let digits = 3; digits >= 0; digits -= 1) {
    const formatted = `$${value.toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    })}`
    if (formatted.length <= width) {
      return formatted
    }
  }
  return `$${Math.round(value).toLocaleString('en-US')}`
}

function formatCount(value: number | null | undefined, width: number): string {
  if (value == null || Number.isNaN(value)) return '-'
  const whole = Math.round(value)
  const grouped = whole.toLocaleString('en-US')
  if (grouped.length <= width) {
    return grouped
  }

  const thresholds: Array<[number, string]> = [
    [1_000_000_000, 'b'],
    [1_000_000, 'm'],
    [1_000, 'k']
  ]
  for (const [divisor, suffix] of thresholds) {
    if (whole < divisor) {
      continue
    }
    const compact = `${(whole / divisor).toFixed(1).replace(/\\.0$/, '')}${suffix}`
    if (compact.length <= width) {
      return compact
    }
  }

  return String(whole)
}

function formatFullCount(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-'
  return Math.round(value).toLocaleString('en-US')
}

function formatShortValue(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '-'
  return value.toFixed(digits)
}

function formatAge(days: number | null | undefined): string {
  if (days == null || Number.isNaN(days)) return '-'
  if (days >= 365) {
    const years = (days / 365).toFixed(1).replace(/\.0$/, '')
    return `${years}y`
  }
  if (days >= 30) {
    const months = Math.floor(days / 30)
    return `${months}mo`
  }
  return `${Math.max(0, Math.round(days))}d`
}

function shadowWalletPnl(row: TopShadowRow): number {
  const pnl = Number(row.pnl ?? 0)
  return Number.isFinite(pnl) ? pnl : 0
}

function pickShadowLeaderboards(rows: TopShadowRow[]): ShadowLeaderboards {
  const best = [...rows]
    .filter((row) => shadowWalletPnl(row) >= 0)
    .sort((left, right) => (
      shadowWalletPnl(right) - shadowWalletPnl(left) ||
      String(left.trader_address || '').localeCompare(String(right.trader_address || ''))
    ))
  const worst = [...rows]
    .filter((row) => shadowWalletPnl(row) <= 0)
    .sort((left, right) => (
      shadowWalletPnl(left) - shadowWalletPnl(right) ||
      String(left.trader_address || '').localeCompare(String(right.trader_address || ''))
    ))
  return {best, worst}
}

function clip(value: number, low = 0, high = 1): number {
  return Math.max(low, Math.min(high, value))
}

function scoreLocalCopiedPerformance(params: {
  resolvedCopiedCount: number | null | undefined
  resolvedCopiedWinRate: number | null | undefined
  resolvedCopiedAvgReturn: number | null | undefined
  resolvedCopiedTotalPnlUsd: number | null | undefined
  recentResolvedCopiedCount: number | null | undefined
  recentResolvedCopiedWinRate: number | null | undefined
  recentResolvedCopiedAvgReturn: number | null | undefined
  recentResolvedCopiedTotalPnlUsd: number | null | undefined
}): {qualityScore: number | null; localWeight: number} {
  const resolvedCopiedCount = Math.max(0, params.resolvedCopiedCount ?? 0)
  const recentResolvedCopiedCount = Math.max(0, params.recentResolvedCopiedCount ?? 0)
  if (resolvedCopiedCount <= 0 && recentResolvedCopiedCount <= 0) {
    return {qualityScore: null, localWeight: 0}
  }

  const scoreWindow = (
    count: number,
    winRate: number | null | undefined,
    avgReturn: number | null | undefined,
    totalPnlUsd: number | null | undefined
  ): number | null => {
    if (count <= 0 || winRate == null || avgReturn == null) {
      return null
    }
    const winScore = clip((winRate - 0.45) / 0.25)
    const returnScore = clip((avgReturn + 0.05) / 0.2)
    const pnlScore = clip(0.5 + (Math.atan((totalPnlUsd ?? 0) / 25) / Math.PI))
    const sampleScore = clip(Math.log1p(count) / Math.log1p(24))
    return (
      (0.30 * winScore) +
      (0.35 * returnScore) +
      (0.20 * pnlScore) +
      (0.15 * sampleScore)
    )
  }

  const allTimeQuality = scoreWindow(
    resolvedCopiedCount,
    params.resolvedCopiedWinRate,
    params.resolvedCopiedAvgReturn,
    params.resolvedCopiedTotalPnlUsd
  )
  const recentQuality = scoreWindow(
    recentResolvedCopiedCount,
    params.recentResolvedCopiedWinRate,
    params.recentResolvedCopiedAvgReturn,
    params.recentResolvedCopiedTotalPnlUsd
  )
  const qualityScore =
    recentQuality != null && allTimeQuality != null
      ? ((0.65 * recentQuality) + (0.35 * allTimeQuality))
      : recentQuality ?? allTimeQuality
  const localWeight = clip(
    (0.60 * clip(resolvedCopiedCount / 20)) +
    (0.40 * clip(recentResolvedCopiedCount / 8))
  )
  return {qualityScore, localWeight: Number(localWeight.toFixed(4))}
}

function scoreWalletForTier(params: {
  winRate: number | null | undefined
  nTrades: number | null | undefined
  avgReturn: number | null | undefined
  realizedPnlUsd: number | null | undefined
  openPositions: number | null | undefined
  lastSourceTs: number | null | undefined
  cacheUpdatedAt: number | null | undefined
  buySignals: number | null | undefined
  uncopyableSkipRate: number | null | undefined
  uncopyablePenaltyMinBuys: number
  uncopyablePenaltyWeight: number
  resolvedCopiedCount: number | null | undefined
  resolvedCopiedWinRate: number | null | undefined
  resolvedCopiedAvgReturn: number | null | undefined
  resolvedCopiedTotalPnlUsd: number | null | undefined
  recentResolvedCopiedCount: number | null | undefined
  recentResolvedCopiedWinRate: number | null | undefined
  recentResolvedCopiedAvgReturn: number | null | undefined
  recentResolvedCopiedTotalPnlUsd: number | null | undefined
  nowTs: number
}): number {
  const winRate = params.winRate ?? 0.5
  const nTrades = Math.max(0, params.nTrades ?? 0)
  const avgReturn = params.avgReturn ?? 0
  const realizedPnlUsd = params.realizedPnlUsd ?? 0
  const openPositions = params.openPositions ?? 0
  const lastSourceTs = params.lastSourceTs ?? 0
  const cacheUpdatedAt = params.cacheUpdatedAt ?? 0
  const buySignals = Math.max(0, params.buySignals ?? 0)
  const uncopyableSkipRate = clip(params.uncopyableSkipRate ?? 0)

  const shrunkWinRate = ((nTrades * winRate) + (20 * 0.5)) / (nTrades + 20)
  const winScore = clip((shrunkWinRate - 0.45) / 0.25)
  const sampleScore = clip(Math.log1p(nTrades) / Math.log1p(80))
  const returnScore = clip((avgReturn + 0.05) / 0.2)
  const pnlScore = clip(Math.log1p(Math.max(realizedPnlUsd, 0)) / Math.log1p(5000))

  let activityScore = 0
  if (lastSourceTs > 0) {
    const activityAgeHours = Math.max(params.nowTs - lastSourceTs, 0) / 3600
    activityScore = clip(1 - (activityAgeHours / 72))
  } else if (cacheUpdatedAt > 0) {
    const cacheAgeHours = Math.max(params.nowTs - cacheUpdatedAt, 0) / 3600
    activityScore = cacheAgeHours <= 24 ? 0.35 : cacheAgeHours <= 72 ? 0.15 : 0
  }

  const openScore = clip(openPositions / 3)
  const freshnessPenalty = cacheUpdatedAt > 0 && (params.nowTs - cacheUpdatedAt) > 86400 ? 0.1 : 0
  const uncopyablePenalty =
    buySignals >= params.uncopyablePenaltyMinBuys && params.uncopyablePenaltyWeight > 0
      ? params.uncopyablePenaltyWeight * clip(buySignals / Math.max(params.uncopyablePenaltyMinBuys * 3, 1)) * uncopyableSkipRate
      : 0
  const publicQualityScore =
    (0.45 * winScore) +
    (0.2 * returnScore) +
    (0.2 * sampleScore) +
    (0.15 * pnlScore)
  const {qualityScore: localQualityScore, localWeight} = scoreLocalCopiedPerformance({
    resolvedCopiedCount: params.resolvedCopiedCount,
    resolvedCopiedWinRate: params.resolvedCopiedWinRate,
    resolvedCopiedAvgReturn: params.resolvedCopiedAvgReturn,
    resolvedCopiedTotalPnlUsd: params.resolvedCopiedTotalPnlUsd,
    recentResolvedCopiedCount: params.recentResolvedCopiedCount,
    recentResolvedCopiedWinRate: params.recentResolvedCopiedWinRate,
    recentResolvedCopiedAvgReturn: params.recentResolvedCopiedAvgReturn,
    recentResolvedCopiedTotalPnlUsd: params.recentResolvedCopiedTotalPnlUsd
  })
  const qualityScore =
    localQualityScore == null
      ? publicQualityScore
      : (((1 - localWeight) * publicQualityScore) + (localWeight * localQualityScore))
  return Number(((
    (0.7 * qualityScore) +
    (0.25 * activityScore) +
    (0.05 * openScore)
  ) - freshnessPenalty - uncopyablePenalty).toFixed(4))
}

function tierLabel(tier: WatchTier): string {
  if (tier === 'HOT') return 'HOT'
  if (tier === 'WARM') return 'WARM'
  return 'SLOW'
}

function tierColor(tier: WatchTier): string {
  if (tier === 'HOT') return theme.green
  if (tier === 'WARM') return theme.yellow
  return theme.dim
}

function getWalletsLayout(width: number, wallets: WalletRow[]): WalletsLayout {
  const trackingSinceWidth = 10
  const tierWidth = 5
  const skippedTradesWidth = 6
  const seenTradesWidth = 6
  const seenWinRateWidth = 8
  const observedResolvedWidth = 7
  const observedWinRateWidth = 8
  const profileWinRateWidth = 8
  const copyPnlWidth = 11
  const lastSeenWidth = 10
  const fixedWidths =
    trackingSinceWidth +
    tierWidth +
    skippedTradesWidth +
    seenTradesWidth +
    seenWinRateWidth +
    observedResolvedWidth +
    observedWinRateWidth +
    profileWinRateWidth +
    copyPnlWidth +
    lastSeenWidth
  const gapCount = 11
  const variableBudget = Math.max(40, width - fixedWidths - gapCount)
  const desiredUsernameWidth = Math.max(
    14,
    wallets.reduce((max, wallet) => Math.max(max, (wallet.username || '-').length + 2), 0)
  )
  const desiredAddressWidth = Math.max(
    18,
    wallets.reduce((max, wallet) => Math.max(max, wallet.trader_address.length), 0)
  )

  let usernameWidth = Math.max(
    14,
    Math.min(desiredUsernameWidth, variableBudget - Math.min(desiredAddressWidth, Math.max(18, variableBudget - 14)))
  )
  let addressWidth = variableBudget - usernameWidth

  if (variableBudget >= desiredUsernameWidth + desiredAddressWidth) {
    usernameWidth = desiredUsernameWidth
    addressWidth = variableBudget - usernameWidth
  }

  return {
    usernameWidth,
    addressWidth,
    trackingSinceWidth,
    tierWidth,
    skippedTradesWidth,
    seenTradesWidth,
    seenWinRateWidth,
    observedResolvedWidth,
    observedWinRateWidth,
    profileWinRateWidth,
    copyPnlWidth,
    lastSeenWidth
  }
}

function buildDetailColumns(sections: WalletDetailSection[], wide: boolean): WalletDetailSection[][] {
  if (!sections.length) {
    return []
  }

  if (wide) {
    const midpoint = Math.ceil(sections.length / 2)
    return [sections.slice(0, midpoint), sections.slice(midpoint)].filter((column) => column.length > 0)
  }

  const columns: WalletDetailSection[][] = [[], []]
  sections.forEach((section, index) => {
    columns[index % 2].push(section)
  })
  return columns.filter((column) => column.length > 0)
}

function getDroppedWalletsLayout(width: number, sharedLayout: WalletsLayout): DroppedWalletsLayout {
  const lastSeenWidth = 10
  const droppedWidth = 10
  const gapCount = 4
  const usernameWidth = sharedLayout.usernameWidth
  const addressWidth = sharedLayout.addressWidth
  const reasonWidth = Math.max(14, width - usernameWidth - addressWidth - lastSeenWidth - droppedWidth - gapCount)
  return {
    usernameWidth,
    addressWidth,
    reasonWidth,
    lastSeenWidth,
    droppedWidth
  }
}

function makeWalletChartCell(char: string, color?: string): WalletChartCell {
  return {char, color}
}

function drawWalletSeriesCell(grid: WalletChartCell[][], x: number, y: number, char: string, color: string) {
  const row = grid[y]
  if (!row || !row[x]) {
    return
  }
  const current = row[x]
  if (current.char === ' ' || current.color === theme.dim) {
    row[x] = makeWalletChartCell(char, color)
    return
  }
  if (current.color === color) {
    row[x] = makeWalletChartCell(current.char === '.' ? current.char : char, color)
    return
  }
  row[x] = makeWalletChartCell('+', theme.white)
}

function drawWalletHorizontalSegment(grid: WalletChartCell[][], y: number, fromX: number, toX: number, color: string) {
  const start = Math.min(fromX, toX)
  const end = Math.max(fromX, toX)
  for (let x = start; x <= end; x += 1) {
    drawWalletSeriesCell(grid, x, y, '─', color)
  }
}

function drawWalletVerticalSegment(grid: WalletChartCell[][], x: number, fromY: number, toY: number, color: string) {
  const start = Math.min(fromY, toY)
  const end = Math.max(fromY, toY)
  for (let y = start; y <= end; y += 1) {
    drawWalletSeriesCell(grid, x, y, '│', color)
  }
}

function renderWalletCellSegments(cells: WalletChartCell[], key: string, backgroundColor?: string) {
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

function buildWalletPnlChart(points: WalletPnlPoint[], width: number, height = 7): WalletChartCell[][] {
  const chartHeight = Math.max(4, Math.floor(height))
  const yLabelWidth = 10
  const plotWidth = Math.max(18, Math.floor(width) - yLabelWidth - 1)
  const plotPaddingLeft = 2
  const plotPaddingRight = 2
  const plotDataWidth = Math.max(3, plotWidth - plotPaddingLeft - plotPaddingRight)

  if (!points.length) {
    return Array.from({length: chartHeight + 1}, () =>
      Array.from({length: yLabelWidth + 1 + plotWidth}, () => makeWalletChartCell(' ', undefined))
    )
  }

  const sampled =
    points.length <= plotDataWidth
      ? points
      : Array.from({length: plotDataWidth}, (_, index) => {
          const sourceIndex =
            plotDataWidth === 1
              ? points.length - 1
              : Math.round((index / Math.max(plotDataWidth - 1, 1)) * Math.max(points.length - 1, 0))
          return points[Math.max(0, Math.min(points.length - 1, sourceIndex))]
        })
  const values = sampled.map((point) => point.pnl)
  const rawMin = Math.min(...values, 0)
  const rawMax = Math.max(...values, 0)
  const pad = Math.max(1, (rawMax - rawMin) * 0.08)
  const minValue = rawMin === rawMax ? rawMin - pad : rawMin - pad
  const maxValue = rawMin === rawMax ? rawMax + pad : rawMax + pad
  const startTs = sampled[0]?.ts || 0
  const endTs = sampled[sampled.length - 1]?.ts || startTs
  const safeEndTs = endTs > startTs ? endTs : startTs + 3600
  const grid = Array.from({length: chartHeight}, () =>
    Array.from({length: plotWidth}, (_, column) => makeWalletChartCell(column === 0 ? '│' : ' ', column === 0 ? theme.dim : undefined))
  )
  const axisRow = chartHeight - 1
  for (let column = 0; column < plotWidth; column += 1) {
    grid[axisRow][column] = makeWalletChartCell(
      column === 0 ? '└' : column === plotWidth - 1 ? '┘' : '─',
      theme.dim
    )
  }

  const mapValueToY = (value: number) => {
    const normalized = Math.abs(maxValue - minValue) <= 1e-9 ? 0.5 : (value - minValue) / (maxValue - minValue)
    return Math.max(0, Math.min(chartHeight - 2, (chartHeight - 2) - Math.round(normalized * Math.max(chartHeight - 2, 1))))
  }
  const zeroRow = mapValueToY(0)
  if (zeroRow >= 0 && zeroRow < chartHeight - 1) {
    for (let column = 1; column < plotWidth; column += 1) {
      if (grid[zeroRow]?.[column]?.char === ' ') {
        grid[zeroRow][column] = makeWalletChartCell('┄', theme.dim)
      }
    }
  }

  const mapPoint = (point: WalletPnlPoint) => {
    const x =
      plotPaddingLeft +
      Math.round((((point.ts - startTs) / Math.max(safeEndTs - startTs, 1)) * Math.max(plotDataWidth - 1, 1)))
    const y = mapValueToY(point.pnl)
    return {x, y}
  }

  const color =
    (sampled[sampled.length - 1]?.pnl || 0) >= 0
      ? theme.green
      : theme.red
  const mapped = sampled.map(mapPoint)
  for (let index = 0; index < mapped.length - 1; index += 1) {
    const current = mapped[index]
    const next = mapped[index + 1]
    drawWalletHorizontalSegment(grid, current.y, current.x, next.x, color)
    if (next.y !== current.y) {
      drawWalletVerticalSegment(grid, next.x, current.y, next.y, color)
    }
  }
  for (const point of mapped) {
    drawWalletSeriesCell(grid, point.x, point.y, '.', color)
  }

  const rows = grid.map((plotRow, rowIndex) => {
    const value = maxValue - ((rowIndex / Math.max(chartHeight - 2, 1)) * (maxValue - minValue))
    const showLabel = rowIndex === 0 || rowIndex === chartHeight - 2 || rowIndex === Math.floor((chartHeight - 2) / 2)
    const label = showLabel ? fitRight(formatSignedMoney(value, yLabelWidth), yLabelWidth) : ' '.repeat(yLabelWidth)
    return [
      ...label.split('').map((char) => makeWalletChartCell(char, theme.dim)),
      makeWalletChartCell(' ', undefined),
      ...plotRow
    ]
  })

  const startLabel = startTs ? formatShortDateTime(startTs) : '-'
  const endLabel = endTs ? formatShortDateTime(endTs) : '-'
  const axisLabelWidth = yLabelWidth + 1 + plotWidth
  const leftWidth = Math.max(1, axisLabelWidth - endLabel.length)
  rows.push(`${fit(startLabel, leftWidth)}${fitRight(endLabel, endLabel.length)}`.split('').map((char) => makeWalletChartCell(char, theme.dim)))
  return rows
}

function WalletPnlHistoryChart({
  points,
  width,
  offset,
  backgroundColor
}: {
  points: WalletPnlPoint[]
  width: number
  offset: number
  backgroundColor?: string
}) {
  const yLabelWidth = 10
  const plotWidth = Math.max(18, Math.floor(width) - yLabelWidth - 1)
  const visiblePointCount = Math.max(12, plotWidth - 4)
  const maxOffset = Math.max(0, points.length - visiblePointCount)
  const clampedOffset = Math.max(0, Math.min(maxOffset, offset))
  const endExclusive = Math.max(1, points.length - clampedOffset)
  const startIndex = Math.max(0, endExclusive - visiblePointCount)
  const visiblePoints = points.slice(startIndex, endExclusive)
  const rows = buildWalletPnlChart(visiblePoints, width, 7)
  const latestValue = visiblePoints[visiblePoints.length - 1]?.pnl ?? 0
  const oldestValue = visiblePoints[0]?.pnl ?? 0
  const delta = latestValue - oldestValue
  const summaryColor = delta > 0 ? theme.green : delta < 0 ? theme.red : theme.dim

  return (
    <InkBox flexDirection="column" width="100%">
      {rows.map((row, index) => renderWalletCellSegments(row, `wallet-pnl-chart-${index}`, backgroundColor))}
      <InkBox width="100%">
        <Text backgroundColor={backgroundColor}> </Text>
        <Text color={theme.dim} backgroundColor={backgroundColor}>
          {fit(`Window ${startIndex + 1}-${endExclusive} of ${Math.max(points.length, 1)}`, Math.max(12, width - 20))}
        </Text>
        <Text color={summaryColor} backgroundColor={backgroundColor}>
          {fitRight(`Δ ${formatSignedMoney(delta, 12)}`, 14)}
        </Text>
        <Text backgroundColor={backgroundColor}> </Text>
      </InkBox>
    </InkBox>
  )
}

export function Wallets({
  botState,
  activePane,
  bestSelectedIndex,
  worstSelectedIndex,
  trackedSelectedIndex,
  droppedSelectedIndex,
  detailOpen,
  detailHistoryOffset,
  onWalletMetaChange,
  onDetailHistoryMetaChange
}: WalletsProps) {
  const terminal = useTerminalSize()
  const selectedRowBackground = selectionBackgroundColor(terminal.backgroundColor)
  const footerRows = 1
  const shadowLeaderboardRows = 5
  const shadowPanelHeight = shadowLeaderboardRows + 4
  const totalVisibleRows = Math.max(8, rowsForHeight(terminal.height, terminal.wide ? 18 : 24, 4) - footerRows)
  const profileChromeRows = 5
  const profileVisibleRows = Math.max(2, Math.floor(totalVisibleRows / 2) - profileChromeRows)
  const trackedVisibleRows = profileVisibleRows
  const droppedVisibleRows = profileVisibleRows
  const tableWidth = Math.max(52, terminal.width - 8)
  const startupDetail = String(botState.startup_detail || '').trim()
  const startupBlocked = Boolean(botState.startup_blocked) || /startup blocked/i.test(startupDetail)
  const startupRecoveryOnly = Boolean(botState.startup_recovery_only) || startupBlocked
  const startupBlockReason = String(botState.startup_block_reason || '').trim()
  const shadowRestartPending = Boolean(botState.shadow_restart_pending)
  const shadowRestartMessage = String(botState.shadow_restart_message || '').trim() || 'shadow restart in progress'
  const dbIntegrityKnown = Boolean(botState.db_integrity_known)
  const dbIntegrityOk = Boolean(botState.db_integrity_ok)
  const dbIntegrityMessage = String(botState.db_integrity_message || '').trim()
  const walletQueryBlockedMessage =
    shadowRestartPending
      ? `Wallet queries are blocked: ${shadowRestartMessage}`
      : startupRecoveryOnly
        ? `Wallet queries are blocked: ${startupBlockReason || startupDetail || 'startup blocked in recovery-only mode'}`
        : dbIntegrityKnown && !dbIntegrityOk
          ? `Wallet queries are blocked: ${dbIntegrityMessage || 'SQLite integrity check failed.'}`
          : ''
  const walletQueryBlockedLines =
    walletQueryBlockedMessage
      ? wrapText(walletQueryBlockedMessage, Math.max(24, tableWidth))
      : []
  const activityRows = useQuery<WalletActivityRow>(WALLET_ACTIVITY_SQL)
  const traderCacheRows = useQuery<TraderCacheRow>(TRADER_CACHE_SQL)
  const walletCursorRows = useQuery<WalletCursorRow>(WALLET_CURSOR_SQL)
  const watchStateRows = useQuery<WalletWatchStateRow>(WALLET_WATCH_STATE_SQL)
  const walletPolicyRows = useQuery<WalletPolicyMetricRow>(WALLET_POLICY_METRICS_SQL)
  const shadowWalletRows = useQuery<TopShadowRow>(SHADOW_WALLETS_SQL)
  const events = useEventStream(1000)
  const config = useDashboardConfig()
  const identityMap = useIdentityMap()

  const usernames = useMemo(() => {
    const lookup = new Map(identityMap)
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index]
      const wallet = event.trader?.trim().toLowerCase()
      const username = event.username?.trim()
      if (!wallet || !username || isPlaceholderUsername(username, wallet) || lookup.has(wallet)) {
        continue
      }
      lookup.set(wallet, username)
    }
    return lookup
  }, [events, identityMap])

  const watchConfig = useMemo(() => readWatchConfig(config.safeValues), [config.safeValues])
  const watchedWallets = watchConfig.wallets
  const sourceWallets = useMemo(() => {
    const fallbackWallets = Array.from(new Set([
      ...activityRows.map((row) => row.trader_address.toLowerCase()),
      ...traderCacheRows.map((row) => row.trader_address.toLowerCase()),
      ...walletCursorRows.map((row) => row.wallet_address.toLowerCase()),
      ...watchStateRows.map((row) => row.wallet_address.toLowerCase()),
      ...walletPolicyRows.map((row) => row.wallet_address.toLowerCase())
    ]))
    return watchedWallets.length ? watchedWallets : fallbackWallets
  }, [activityRows, traderCacheRows, walletCursorRows, walletPolicyRows, watchStateRows, watchedWallets])
  const watchStateByWallet = useMemo(
    () =>
      new Map(
        watchStateRows.map((row) => [
          row.wallet_address.toLowerCase(),
          {
            status: row.status?.trim().toLowerCase() === 'dropped' ? 'dropped' : 'active',
            status_reason: row.status_reason ?? null,
            dropped_at: row.dropped_at ?? null,
            reactivated_at: row.reactivated_at ?? null,
            tracking_started_at: row.tracking_started_at ?? null,
            last_source_ts_at_status: row.last_source_ts_at_status ?? null,
            updated_at: row.updated_at ?? null
          }
        ])
      ),
    [watchStateRows]
  )
  const cursorByWallet = useMemo(
    () => new Map(walletCursorRows.map((row) => [row.wallet_address.toLowerCase(), row])),
    [walletCursorRows]
  )

  const tierByWallet = useMemo(() => {
    const cacheByWallet = new Map(traderCacheRows.map((row) => [row.trader_address.toLowerCase(), row]))
    const activityByWallet = new Map(activityRows.map((row) => [row.trader_address.toLowerCase(), row]))
    const policyByWallet = new Map(walletPolicyRows.map((row) => [row.wallet_address.toLowerCase(), row]))
    const activeWallets = sourceWallets.filter((wallet) => watchStateByWallet.get(wallet)?.status !== 'dropped')
    const nowTs = Math.floor(Date.now() / 1000)
    const ranked = activeWallets.map((wallet, index) => {
      const cached = cacheByWallet.get(wallet)
      const cursor = cursorByWallet.get(wallet)
      const activity = activityByWallet.get(wallet)
      const policy = policyByWallet.get(wallet)
      return {
        wallet,
        index,
        followScore: scoreWalletForTier({
          winRate: cached?.win_rate,
          nTrades: cached?.n_trades,
          avgReturn: cached?.avg_return,
          realizedPnlUsd: cached?.realized_pnl_usd,
          openPositions: cached?.open_positions,
          lastSourceTs: cursor?.last_source_ts,
          cacheUpdatedAt: cached?.updated_at,
          buySignals: activity?.buy_signals,
          uncopyableSkipRate:
            (activity?.buy_signals ?? 0) > 0
              ? (activity?.uncopyable_skips ?? 0) / (activity?.buy_signals ?? 0)
              : 0,
          uncopyablePenaltyMinBuys: watchConfig.uncopyablePenaltyMinBuys,
          uncopyablePenaltyWeight: watchConfig.uncopyablePenaltyWeight,
          resolvedCopiedCount: policy?.resolved_copied_count,
          resolvedCopiedWinRate: policy?.resolved_copied_win_rate,
          resolvedCopiedAvgReturn: policy?.resolved_copied_avg_return,
          resolvedCopiedTotalPnlUsd: policy?.resolved_copied_total_pnl_usd,
          recentResolvedCopiedCount: policy?.recent_resolved_copied_count,
          recentResolvedCopiedWinRate: policy?.recent_resolved_copied_win_rate,
          recentResolvedCopiedAvgReturn: policy?.recent_resolved_copied_avg_return,
          recentResolvedCopiedTotalPnlUsd: policy?.recent_resolved_copied_total_pnl_usd,
          nowTs
        }),
        lastSourceTs: cursor?.last_source_ts ?? 0,
        cacheUpdatedAt: cached?.updated_at ?? 0
      }
    })

    ranked.sort((left, right) => (
      right.followScore - left.followScore ||
      right.lastSourceTs - left.lastSourceTs ||
      right.cacheUpdatedAt - left.cacheUpdatedAt ||
      left.index - right.index
    ))

    const hotCount = Math.min(ranked.length, watchConfig.hotCount)
    const warmCount = Math.min(Math.max(ranked.length - hotCount, 0), watchConfig.warmCount)
    const lookup = new Map<string, WatchTier>()
    ranked.forEach((row, index) => {
      const tier: WatchTier = index < hotCount ? 'HOT' : index < hotCount + warmCount ? 'WARM' : 'DISC'
      lookup.set(row.wallet, tier)
    })
    return lookup
  }, [
    activityRows,
    cursorByWallet,
    sourceWallets,
    traderCacheRows,
    walletPolicyRows,
    watchConfig.hotCount,
    watchConfig.uncopyablePenaltyMinBuys,
    watchConfig.uncopyablePenaltyWeight,
    watchConfig.warmCount,
    watchStateByWallet
  ])

  const wallets = useMemo(() => {
    if (walletQueryBlockedLines.length) {
      return []
    }

    const activityByWallet = new Map(activityRows.map((row) => [row.trader_address.toLowerCase(), row]))
    const cacheByWallet = new Map(traderCacheRows.map((row) => [row.trader_address.toLowerCase(), row]))
    const policyByWallet = new Map(walletPolicyRows.map((row) => [row.wallet_address.toLowerCase(), row]))

    return sourceWallets.map<WalletRow>((wallet, index) => {
      const activity = activityByWallet.get(wallet)
      const cached = cacheByWallet.get(wallet)
      const cursor = cursorByWallet.get(wallet)
      const watchState = watchStateByWallet.get(wallet)
      const policy = policyByWallet.get(wallet)
      return {
        trader_address: wallet,
        username: usernames.get(wallet) || '',
        watch_tier: watchState?.status === 'dropped' ? 'DISC' : (tierByWallet.get(wallet) || 'DISC'),
        buy_signals: activity?.buy_signals ?? 0,
        skipped_trades: activity?.skipped_trades ?? 0,
        uncopyable_skips: activity?.uncopyable_skips ?? 0,
        skip_rate:
          (activity?.seen_trades ?? 0) > 0
            ? (activity?.skipped_trades ?? 0) / (activity?.seen_trades ?? 0)
            : null,
        uncopyable_skip_rate:
          (activity?.buy_signals ?? 0) > 0
            ? (activity?.uncopyable_skips ?? 0) / (activity?.buy_signals ?? 0)
            : null,
        seen_trades: activity?.seen_trades ?? 0,
        seen_resolved: activity?.seen_resolved ?? 0,
        seen_wins: activity?.seen_wins ?? 0,
        seen_win_rate:
          (activity?.seen_resolved ?? 0) > 0
            ? (activity?.seen_wins ?? 0) / (activity?.seen_resolved ?? 0)
            : null,
        local_pnl: activity?.local_pnl ?? null,
        last_seen: activity?.last_seen ?? null,
        observed_resolved: activity?.observed_resolved ?? 0,
        observed_wins: activity?.observed_wins ?? 0,
        observed_win_rate:
          (activity?.observed_resolved ?? 0) > 0
            ? (activity?.observed_wins ?? 0) / (activity?.observed_resolved ?? 0)
            : null,
        closed_trades: cached?.n_trades ?? null,
        win_rate: cached?.win_rate ?? null,
        consistency: cached?.consistency ?? null,
        volume_usd: cached?.volume_usd ?? null,
        avg_size_usd: cached?.avg_size_usd ?? null,
        diversity: cached?.diversity ?? null,
        account_age_d: cached?.account_age_d ?? null,
        wins: cached?.wins ?? null,
        ties: cached?.ties ?? null,
        realized_pnl_usd: cached?.realized_pnl_usd ?? null,
        open_positions: cached?.open_positions ?? null,
        open_value_usd: cached?.open_value_usd ?? null,
        open_pnl_usd: cached?.open_pnl_usd ?? null,
        updated_at: cached?.updated_at ?? null,
        last_source_ts: cursor?.last_source_ts ?? null,
        status: watchState?.status === 'dropped' ? 'dropped' : 'active',
        status_reason: watchState?.status_reason ?? null,
        dropped_at: watchState?.dropped_at ?? null,
        reactivated_at: watchState?.reactivated_at ?? null,
        tracking_started_at: watchState?.tracking_started_at ?? watchState?.reactivated_at ?? watchState?.updated_at ?? null,
        last_source_ts_at_status: watchState?.last_source_ts_at_status ?? null,
        watch_index: index,
        resolved_copied_avg_return: policy?.resolved_copied_avg_return ?? null,
        resolved_copied_total_pnl_usd: policy?.resolved_copied_total_pnl_usd ?? null,
        recent_resolved_copied_count: policy?.recent_resolved_copied_count ?? null,
        recent_resolved_copied_avg_return: policy?.recent_resolved_copied_avg_return ?? null,
        recent_resolved_copied_total_pnl_usd: policy?.recent_resolved_copied_total_pnl_usd ?? null,
        recent_window_seconds: policy?.recent_window_seconds ?? null,
        local_quality_score: policy?.local_quality_score ?? null,
        local_weight: policy?.local_weight ?? null,
        local_drop_ready: Boolean(policy?.local_drop_ready ?? 0),
        local_drop_reason: policy?.local_drop_reason ?? null
      }
    })
  }, [
    activityRows,
    cursorByWallet,
    sourceWallets,
    tierByWallet,
    traderCacheRows,
    usernames,
    walletPolicyRows,
    walletQueryBlockedLines.length,
    watchStateByWallet
  ])
  const trackedWallets = useMemo(
    () => wallets.filter((wallet) => wallet.status !== 'dropped'),
    [wallets]
  )
  const droppedWallets = useMemo(
    () => wallets.filter((wallet) => wallet.status === 'dropped'),
    [wallets]
  )
  const layout = useMemo(
    () => getWalletsLayout(tableWidth, trackedWallets.length ? trackedWallets : wallets),
    [tableWidth, trackedWallets, wallets]
  )
  const droppedLayout = useMemo(() => getDroppedWalletsLayout(tableWidth, layout), [layout, tableWidth])

  const clampedTrackedSelectedIndex = trackedWallets.length
    ? Math.max(0, Math.min(trackedSelectedIndex, trackedWallets.length - 1))
    : 0
  const clampedDroppedSelectedIndex = droppedWallets.length
    ? Math.max(0, Math.min(droppedSelectedIndex, droppedWallets.length - 1))
    : 0

  const trackedWindowStart =
    trackedWallets.length > trackedVisibleRows
      ? Math.min(
          Math.max(clampedTrackedSelectedIndex - Math.floor(trackedVisibleRows / 2), 0),
          Math.max(0, trackedWallets.length - trackedVisibleRows)
        )
      : 0
  const droppedWindowStart =
    droppedWallets.length > droppedVisibleRows
      ? Math.min(
          Math.max(clampedDroppedSelectedIndex - Math.floor(droppedVisibleRows / 2), 0),
          Math.max(0, droppedWallets.length - droppedVisibleRows)
        )
      : 0
  const visibleTrackedWallets = trackedWallets.slice(trackedWindowStart, trackedWindowStart + trackedVisibleRows)
  const visibleDroppedWallets = droppedWallets.slice(droppedWindowStart, droppedWindowStart + droppedVisibleRows)
  const trackedVisibleStart = trackedWallets.length ? trackedWindowStart + 1 : 0
  const trackedVisibleEnd = trackedWindowStart + visibleTrackedWallets.length
  const droppedVisibleStart = droppedWallets.length ? droppedWindowStart + 1 : 0
  const droppedVisibleEnd = droppedWindowStart + visibleDroppedWallets.length
  const tierCounts = useMemo(
    () =>
      trackedWallets.reduce(
        (counts, wallet) => {
          counts[wallet.watch_tier] += 1
          return counts
        },
        {HOT: 0, WARM: 0, DISC: 0} as Record<WatchTier, number>
      ),
    [trackedWallets]
  )
  const trackedFooterText = walletQueryBlockedMessage
    ? walletQueryBlockedMessage
    : trackedWallets.length
      ? `showing ${trackedVisibleStart}-${trackedVisibleEnd} of ${trackedWallets.length}  selected ${clampedTrackedSelectedIndex + 1}/${trackedWallets.length}  hot/warm/slow ${tierCounts.HOT}/${tierCounts.WARM}/${tierCounts.DISC}`
      : 'showing 0 of 0'
  const droppedFooterText = walletQueryBlockedMessage
    ? walletQueryBlockedMessage
    : droppedWallets.length
      ? `showing ${droppedVisibleStart}-${droppedVisibleEnd} of ${droppedWallets.length}  selected ${clampedDroppedSelectedIndex + 1}/${droppedWallets.length}  auto-dropped until reactivated`
      : 'no dropped wallets'

  const {best: bestShadowWallets, worst: worstShadowWallets} = useMemo(
    () => pickShadowLeaderboards(shadowWalletRows),
    [shadowWalletRows]
  )
  const bestWalletAddresses = useMemo(
    () => bestShadowWallets.map((wallet) => wallet.trader_address.toLowerCase()),
    [bestShadowWallets]
  )
  const worstWalletAddresses = useMemo(
    () => worstShadowWallets.map((wallet) => wallet.trader_address.toLowerCase()),
    [worstShadowWallets]
  )
  const clampedBestSelectedIndex = bestWalletAddresses.length
    ? Math.max(0, Math.min(bestSelectedIndex, bestWalletAddresses.length - 1))
    : 0
  const clampedWorstSelectedIndex = worstWalletAddresses.length
    ? Math.max(0, Math.min(worstSelectedIndex, worstWalletAddresses.length - 1))
    : 0
  const selectedBestWalletAddress = bestWalletAddresses[clampedBestSelectedIndex] || ''
  const selectedWorstWalletAddress = worstWalletAddresses[clampedWorstSelectedIndex] || ''
  const selectedTrackedWalletAddress = trackedWallets[clampedTrackedSelectedIndex]?.trader_address || ''
  const selectedDroppedWalletAddress = droppedWallets[clampedDroppedSelectedIndex]?.trader_address || ''
  const selectedWalletAddress =
    activePane === 'best'
      ? selectedBestWalletAddress
      : activePane === 'worst'
        ? selectedWorstWalletAddress
        : activePane === 'dropped'
          ? selectedDroppedWalletAddress
          : selectedTrackedWalletAddress
  const walletByAddress = useMemo(
    () => new Map(wallets.map((wallet) => [wallet.trader_address.toLowerCase(), wallet])),
    [wallets]
  )
  const selectedWallet = selectedWalletAddress ? walletByAddress.get(selectedWalletAddress.toLowerCase()) || null : null
  const walletPnlHistoryRows = useQuery<WalletPnlHistoryRow>(
    WALLET_LOCAL_PNL_HISTORY_SQL,
    [selectedWallet?.trader_address || '__none__'],
    detailOpen && selectedWallet ? 5000 : 15000
  )
  const walletPnlHistoryPoints = useMemo<WalletPnlPoint[]>(() => {
    const sorted = walletPnlHistoryRows
      .map((row) => ({
        ts: Number(row.resolved_ts || 0),
        pnl: Number(row.pnl_usd || 0)
      }))
      .filter((row) => Number.isFinite(row.ts) && row.ts > 0 && Number.isFinite(row.pnl))
      .sort((left, right) => left.ts - right.ts)

    let cumulative = 0
    return sorted.map((row) => {
      cumulative = Number((cumulative + row.pnl).toFixed(3))
      return {ts: row.ts, pnl: cumulative}
    })
  }, [walletPnlHistoryRows])

  useEffect(() => {
    onWalletMetaChange?.({
      bestCount: bestWalletAddresses.length,
      worstCount: worstWalletAddresses.length,
      trackedCount: trackedWallets.length,
      droppedCount: droppedWallets.length,
      bestWalletAddresses,
      worstWalletAddresses,
      trackedWalletAddresses: trackedWallets.map((wallet) => wallet.trader_address),
      droppedWalletAddresses: droppedWallets.map((wallet) => wallet.trader_address)
    })
  }, [
    bestWalletAddresses,
    droppedWallets,
    onWalletMetaChange,
    trackedWallets,
    worstWalletAddresses
  ])

  const shadowPanelsWide = terminal.wide
  const shadowPanelWidth = shadowPanelsWide ? Math.max(44, Math.floor((tableWidth - 1) / 2)) : tableWidth
  const shadowPanelContentWidth = Math.max(24, shadowPanelWidth - 4)
  const shadowRankWidth = Math.max(
    1,
    Math.min(4, Math.max(String(Math.max(bestShadowWallets.length, worstShadowWallets.length, 1)).length, 1))
  )
  const shadowCopyWrWidth = 10
  const shadowSkipWidth = 6
  const shadowCopyPnlWidth = 10
  const shadowNameWidth = Math.max(
    8,
    shadowPanelContentWidth - shadowRankWidth - shadowCopyWrWidth - shadowSkipWidth - shadowCopyPnlWidth - 4
  )
  const renderWalletQueryBlockedNotice = (width: number, keyPrefix: string) =>
    walletQueryBlockedLines.map((line, index) => (
      <Text key={`${keyPrefix}-${index}`} color={theme.yellow}>
        {fit(line, width)}
      </Text>
    ))
  const maxAbsShadowPnl = useMemo(
    () => shadowWalletRows.reduce((max, wallet) => Math.max(max, Math.abs(wallet.pnl || 0)), 0),
    [shadowWalletRows]
  )
  const maxAbsLocalPnl = useMemo(
    () => wallets.reduce((max, wallet) => Math.max(max, Math.abs(wallet.local_pnl || 0)), 0),
    [wallets]
  )
  const maxAbsRealizedPnl = useMemo(
    () => wallets.reduce((max, wallet) => Math.max(max, Math.abs(wallet.realized_pnl_usd || 0)), 0),
    [wallets]
  )
  const maxAbsOpenPnl = useMemo(
    () => wallets.reduce((max, wallet) => Math.max(max, Math.abs(wallet.open_pnl_usd || 0)), 0),
    [wallets]
  )
  const maxAbsConsistency = useMemo(
    () => wallets.reduce((max, wallet) => Math.max(max, Math.abs(wallet.consistency || 0)), 0),
    [wallets]
  )
  const maxVolume = useMemo(
    () => wallets.reduce((max, wallet) => Math.max(max, wallet.volume_usd || 0), 0),
    [wallets]
  )
  const maxHeld = useMemo(
    () => wallets.reduce((max, wallet) => Math.max(max, wallet.open_value_usd || 0), 0),
    [wallets]
  )
  const maxAvgSize = useMemo(
    () => wallets.reduce((max, wallet) => Math.max(max, wallet.avg_size_usd || 0), 0),
    [wallets]
  )

  const detailSections = useMemo<WalletDetailSection[]>(() => {
    if (!selectedWallet) {
      return []
    }

    return [
      {
        title: 'Watch',
        metrics: [
          {
            label: 'Status',
            value: selectedWallet.status === 'dropped' ? 'Dropped' : 'Active',
            color: selectedWallet.status === 'dropped' ? theme.red : theme.green
          },
          {
            label: 'Reason',
            value: selectedWallet.status_reason || '-'
          },
          {
            label: 'Watch Tier',
            value: selectedWallet.status === 'dropped' ? '-' : tierLabel(selectedWallet.watch_tier),
            color: selectedWallet.status === 'dropped' ? theme.dim : tierColor(selectedWallet.watch_tier)
          },
          {
            label: 'Tracking Since',
            value: secondsAgo(selectedWallet.tracking_started_at || undefined)
          },
          {
            label: 'Logged Last',
            value: secondsAgo(selectedWallet.last_seen || undefined)
          },
          {
            label: 'Dropped',
            value: secondsAgo(selectedWallet.dropped_at || undefined)
          },
          {
            label: 'Reactivated',
            value: secondsAgo(selectedWallet.reactivated_at || undefined)
          }
        ]
      },
      {
        title: 'Local',
        metrics: [
          {
            label: 'Skipped',
            value: formatFullCount(selectedWallet.skipped_trades)
          },
          {
            label: 'Seen Trades',
            value: formatFullCount(selectedWallet.seen_trades)
          },
          {
            label: 'Seen Resolved',
            value: formatFullCount(selectedWallet.seen_resolved)
          },
          {
            label: 'Seen Wins',
            value: formatFullCount(selectedWallet.seen_wins)
          },
          {
            label: 'Seen WR',
            value: selectedWallet.seen_win_rate == null ? '-' : formatPct(selectedWallet.seen_win_rate, 2),
            color: selectedWallet.seen_win_rate == null ? theme.dim : probabilityColor(selectedWallet.seen_win_rate)
          },
          {
            label: 'Resolved Copied',
            value: formatFullCount(selectedWallet.observed_resolved)
          },
          {
            label: 'Copied Wins',
            value: formatFullCount(selectedWallet.observed_wins)
          },
          {
            label: 'Copy WR',
            value: selectedWallet.observed_win_rate == null ? '-' : formatPct(selectedWallet.observed_win_rate, 2),
            color: selectedWallet.observed_win_rate == null ? theme.dim : probabilityColor(selectedWallet.observed_win_rate)
          },
          {
            label: 'Copy P&L',
            value: formatSignedMoney(selectedWallet.local_pnl, 18),
            color:
              selectedWallet.local_pnl == null
                ? theme.dim
                : centeredGradientColor(selectedWallet.local_pnl, maxAbsLocalPnl)
          },
          {
            label: 'Copy Avg Ret',
            value: selectedWallet.resolved_copied_avg_return == null ? '-' : formatPct(selectedWallet.resolved_copied_avg_return, 2),
            color:
              selectedWallet.resolved_copied_avg_return == null
                ? theme.dim
                : probabilityColor(clip(0.5 + (selectedWallet.resolved_copied_avg_return / 2)))
          }
        ]
      },
      {
        title: 'Policy',
        metrics: [
          {
            label: 'Local Weight',
            value: selectedWallet.local_weight == null ? '-' : formatPct(selectedWallet.local_weight, 1),
            color: selectedWallet.local_weight == null ? theme.dim : probabilityColor(selectedWallet.local_weight)
          },
          {
            label: 'Local Score',
            value: selectedWallet.local_quality_score == null ? '-' : selectedWallet.local_quality_score.toFixed(3),
            color:
              selectedWallet.local_quality_score == null
                ? theme.dim
                : probabilityColor(selectedWallet.local_quality_score)
          },
          {
            label: 'Recent Window',
            value: secondsAgo(
              selectedWallet.recent_window_seconds == null
                ? undefined
                : Math.floor(Date.now() / 1000) - selectedWallet.recent_window_seconds
            )
          },
          {
            label: 'Recent Copied',
            value: formatFullCount(selectedWallet.recent_resolved_copied_count)
          },
          {
            label: 'Recent Avg Ret',
            value: selectedWallet.recent_resolved_copied_avg_return == null ? '-' : formatPct(selectedWallet.recent_resolved_copied_avg_return, 2),
            color:
              selectedWallet.recent_resolved_copied_avg_return == null
                ? theme.dim
                : probabilityColor(clip(0.5 + (selectedWallet.recent_resolved_copied_avg_return / 2)))
          },
          {
            label: 'Recent P&L',
            value: formatSignedMoney(selectedWallet.recent_resolved_copied_total_pnl_usd, 18),
            color:
              selectedWallet.recent_resolved_copied_total_pnl_usd == null
                ? theme.dim
                : centeredGradientColor(selectedWallet.recent_resolved_copied_total_pnl_usd, maxAbsLocalPnl)
          },
          {
            label: 'Drop Signal',
            value: selectedWallet.local_drop_ready ? 'Ready' : 'Clear',
            color: selectedWallet.local_drop_ready ? theme.red : theme.green
          },
          {
            label: 'Drop Reason',
            value: selectedWallet.local_drop_reason || '-'
          }
        ]
      },
      {
        title: 'Profile',
        metrics: [
          {
            label: 'Closed Trades',
            value: formatFullCount(selectedWallet.closed_trades)
          },
          {
            label: 'Profile Wins',
            value: formatFullCount(selectedWallet.wins)
          },
          {
            label: 'Profile Ties',
            value: formatFullCount(selectedWallet.ties)
          },
          {
            label: 'Profile WR',
            value: selectedWallet.win_rate == null ? '-' : formatPct(selectedWallet.win_rate, 2),
            color: selectedWallet.win_rate == null ? theme.dim : probabilityColor(selectedWallet.win_rate)
          },
          {
            label: 'Profile P&L',
            value: formatSignedMoney(selectedWallet.realized_pnl_usd, 18),
            color:
              selectedWallet.realized_pnl_usd == null
                ? theme.dim
                : centeredGradientColor(selectedWallet.realized_pnl_usd, maxAbsRealizedPnl)
          },
          {
            label: 'Account Age',
            value: formatAge(selectedWallet.account_age_d)
          },
          {
            label: 'Markets',
            value: formatFullCount(selectedWallet.diversity)
          },
          {
            label: 'Cache Age',
            value: secondsAgo(selectedWallet.updated_at || undefined)
          }
        ]
      },
      {
        title: 'Exposure',
        metrics: [
          {
            label: 'Total Volume',
            value: formatUnsignedMoney(selectedWallet.volume_usd, 18),
            color: selectedWallet.volume_usd == null ? theme.dim : positiveDollarColor(selectedWallet.volume_usd, maxVolume || 1)
          },
          {
            label: 'Avg Trade',
            value: formatUnsignedMoney(selectedWallet.avg_size_usd, 18),
            color:
              selectedWallet.avg_size_usd == null
                ? theme.dim
                : positiveDollarColor(selectedWallet.avg_size_usd, maxAvgSize || 1)
          },
          {
            label: 'Open Count',
            value: formatFullCount(selectedWallet.open_positions)
          },
          {
            label: 'Open Value',
            value: formatUnsignedMoney(selectedWallet.open_value_usd, 18),
            color:
              selectedWallet.open_value_usd == null
                ? theme.dim
                : positiveDollarColor(selectedWallet.open_value_usd, maxHeld || 1)
          },
          {
            label: 'Open P&L',
            value: formatSignedMoney(selectedWallet.open_pnl_usd, 18),
            color:
              selectedWallet.open_pnl_usd == null
                ? theme.dim
                : centeredGradientColor(selectedWallet.open_pnl_usd, maxAbsOpenPnl)
          },
          {
            label: 'Consistency',
            value: formatShortValue(selectedWallet.consistency),
            color:
              selectedWallet.consistency == null
                ? theme.dim
                : centeredGradientColor(selectedWallet.consistency, maxAbsConsistency || 1)
          }
        ]
      }
    ]
  }, [
    maxAbsConsistency,
    maxAbsLocalPnl,
    maxAbsOpenPnl,
    maxAbsRealizedPnl,
    maxAvgSize,
    maxHeld,
    maxVolume,
    selectedWallet
  ])

  const detailColumns = useMemo(
    () => buildDetailColumns(detailSections, terminal.wide),
    [detailSections, terminal.wide]
  )
  const detailColumnCount = detailColumns.length || 1
  const detailColumnGap = terminal.wide ? 4 : 2
  const modalBackground = terminal.backgroundColor || theme.modalBackground
  const modalWidth = Math.max(60, Math.min(terminal.width - 6, terminal.wide ? 132 : 90))
  const modalContentWidth = Math.max(36, modalWidth - 4)
  const detailColumnWidth = Math.max(
    20,
    Math.floor((modalContentWidth - detailColumnGap * (detailColumnCount - 1)) / detailColumnCount)
  )
  const detailRowInnerWidth =
    detailColumnWidth * detailColumnCount + detailColumnGap * (detailColumnCount - 1)
  const detailRowRemainderWidth = Math.max(0, modalContentWidth - detailRowInnerWidth)
  const detailLabelWidth = Math.max(8, Math.floor(detailColumnWidth * 0.46))
  const detailValueWidth = Math.max(7, detailColumnWidth - detailLabelWidth - 1)
  const detailIndexLabel =
    activePane === 'best'
      ? `${clampedBestSelectedIndex + 1}/${Math.max(bestWalletAddresses.length, 1)}`
      : activePane === 'worst'
        ? `${clampedWorstSelectedIndex + 1}/${Math.max(worstWalletAddresses.length, 1)}`
        : activePane === 'dropped'
          ? `${clampedDroppedSelectedIndex + 1}/${Math.max(droppedWallets.length, 1)}`
          : `${clampedTrackedSelectedIndex + 1}/${Math.max(trackedWallets.length, 1)}`
  const detailHeaderWidth = Math.max(1, modalContentWidth - detailIndexLabel.length - 1)
  const modalSpacerLine = ' '.repeat(modalWidth - 2)
  const walletChartWidth = Math.max(36, modalContentWidth)
  const walletChartVisiblePoints = Math.max(12, Math.max(18, Math.floor(walletChartWidth) - 11) - 4)
  const walletChartMaxOffset = Math.max(0, walletPnlHistoryPoints.length - walletChartVisiblePoints)
  const detailTitle = selectedWallet?.username || (selectedWallet ? shortAddress(selectedWallet.trader_address) : '-')
  const detailAddressLines = selectedWallet
    ? wrapText(`Address ${selectedWallet.trader_address}`, Math.max(20, modalContentWidth))
    : []
  const detailColumnLines = useMemo<WalletDetailLine[][]>(
    () =>
      detailColumns.map((column) => {
        const lines: WalletDetailLine[] = []
        column.forEach((section, sectionIndex) => {
          lines.push({kind: 'heading', text: fit(section.title.toUpperCase(), detailColumnWidth)})
          section.metrics.forEach((metric) => {
            lines.push({
              kind: 'metric',
              label: fit(metric.label, detailLabelWidth),
              value: fitRight(metric.value, detailValueWidth),
              valueColor: metric.color ?? theme.white
            })
          })
          if (sectionIndex < column.length - 1) {
            lines.push({kind: 'blank'})
          }
        })
        return lines
      }),
    [detailColumns, detailColumnWidth, detailLabelWidth, detailValueWidth]
  )
  const detailLineCount = useMemo(
    () => detailColumnLines.reduce((max, column) => Math.max(max, column.length), 0),
    [detailColumnLines]
  )

  useEffect(() => {
    onDetailHistoryMetaChange?.({maxOffset: walletChartMaxOffset})
  }, [onDetailHistoryMetaChange, walletChartMaxOffset])

  const renderShadowWalletBox = (title: string, pane: 'best' | 'worst', shadowWallets: TopShadowRow[]) => {
    const activeShadowAddress = pane === 'best' ? selectedBestWalletAddress : selectedWorstWalletAddress
    const activeShadowIndex = pane === 'best' ? clampedBestSelectedIndex : clampedWorstSelectedIndex
    const boxIsSelected = activePane === pane
    const shadowWindowStart =
      shadowWallets.length > shadowLeaderboardRows
        ? Math.min(
            Math.max(activeShadowIndex - Math.floor(shadowLeaderboardRows / 2), 0),
            Math.max(0, shadowWallets.length - shadowLeaderboardRows)
          )
        : 0
    const visibleShadowWallets = shadowWallets.slice(shadowWindowStart, shadowWindowStart + shadowLeaderboardRows)
    const paddedRows = Array.from({length: shadowLeaderboardRows}, (_, index) => visibleShadowWallets[index] ?? null)

    return (
      <InkBox
        width={shadowPanelsWide ? undefined : '100%'}
        flexGrow={shadowPanelsWide ? 1 : 0}
        flexBasis={shadowPanelsWide ? 0 : undefined}
      >
        <Box title={title} width="100%" height={shadowPanelHeight} accent={boxIsSelected}>
          <InkBox width="100%">
            <Text color={theme.dim}>{fitRight('#', shadowRankWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fit('WALLET', shadowNameWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fitRight('COPY WR%', shadowCopyWrWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fitRight('SKIP %', shadowSkipWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fitRight('COPY P&L', shadowCopyPnlWidth)}</Text>
          </InkBox>
          <InkBox flexDirection="column">
            {shadowWallets.length ? (
              paddedRows.map((wallet, index) => {
                if (!wallet) {
                  return (
                    <InkBox key={`${title}-empty-${index}`} width="100%">
                      <Text color={theme.dim}>{fitRight('', shadowRankWidth)}</Text>
                      <Text> </Text>
                      <Text color={theme.dim}>{fit('', shadowNameWidth)}</Text>
                      <Text> </Text>
                      <Text color={theme.dim}>{fitRight('', shadowCopyWrWidth)}</Text>
                      <Text> </Text>
                      <Text color={theme.dim}>{fitRight('', shadowSkipWidth)}</Text>
                      <Text> </Text>
                      <Text color={theme.dim}>{fitRight('', shadowCopyPnlWidth)}</Text>
                    </InkBox>
                  )
                }

                const username = usernames.get(wallet.trader_address.toLowerCase())
                const label = username || shortAddress(wallet.trader_address)
                const linkedWallet = walletByAddress.get(wallet.trader_address.toLowerCase())
                const isDroppedWallet = linkedWallet?.status === 'dropped'
                const copyWinRate = wallet.resolved > 0 ? wallet.wins / wallet.resolved : null
                const skipRate = wallet.seen_trades > 0 ? wallet.skipped_trades / wallet.seen_trades : null
                const copyWinRateColor =
                  copyWinRate == null ? theme.dim : probabilityColor(copyWinRate)
                const skipRateColor =
                  skipRate == null ? theme.dim : negativeHeatColor(skipRate * 100, 100)
                const pnlColor =
                  wallet.pnl == null
                    ? theme.dim
                    : centeredGradientColor(wallet.pnl, maxAbsShadowPnl)
                const rank = shadowWindowStart + index + 1

                return (
                  <InkBox key={`${title}-${wallet.trader_address}`} width="100%">
                    {(() => {
                      const isSelected = boxIsSelected && wallet.trader_address.toLowerCase() === activeShadowAddress
                      const rowBackground = isSelected ? selectedRowBackground : undefined
                      const displayLabel = `${isSelected ? '> ' : '  '}${label}`
                      const linkedLabel = terminalHyperlink(
                        fit(displayLabel, shadowNameWidth),
                        username ? walletProfileUrl({trader_address: wallet.trader_address, username}) : null
                      )

                      return (
                        <>
                          <Text
                            color={isSelected ? theme.accent : theme.dim}
                            backgroundColor={rowBackground}
                            bold={isSelected}
                          >
                            {fitRight(String(rank), shadowRankWidth)}
                          </Text>
                          <Text backgroundColor={rowBackground}> </Text>
                          <Text
                            color={
                              isSelected
                                ? theme.accent
                                : isDroppedWallet
                                  ? theme.red
                                  : username
                                    ? theme.white
                                    : theme.dim
                            }
                            backgroundColor={rowBackground}
                            bold={isSelected}
                          >
                            {linkedLabel}
                          </Text>
                          <Text backgroundColor={rowBackground}> </Text>
                          <Text color={isSelected ? theme.accent : copyWinRateColor} backgroundColor={rowBackground} bold={isSelected}>
                            {fitRight(copyWinRate == null ? '-' : formatPct(copyWinRate, 1), shadowCopyWrWidth)}
                          </Text>
                          <Text backgroundColor={rowBackground}> </Text>
                          <Text color={isSelected ? theme.accent : skipRateColor} backgroundColor={rowBackground} bold={isSelected}>
                            {fitRight(skipRate == null ? '-' : formatPct(skipRate, 0), shadowSkipWidth)}
                          </Text>
                          <Text backgroundColor={rowBackground}> </Text>
                          <Text color={isSelected ? theme.accent : pnlColor} backgroundColor={rowBackground} bold={isSelected}>
                            {fitRight(formatSignedMoney(wallet.pnl, shadowCopyPnlWidth), shadowCopyPnlWidth)}
                          </Text>
                        </>
                      )
                    })()}
                  </InkBox>
                )
              })
            ) : (
              walletQueryBlockedLines.length ? (
                <InkBox flexDirection="column">
                  {renderWalletQueryBlockedNotice(shadowPanelContentWidth, `${title}-blocked`)}
                </InkBox>
              ) : (
                <Text color={theme.dim}>No wallet performance yet.</Text>
              )
            )}
          </InkBox>
        </Box>
      </InkBox>
    )
  }

  const renderPageBody = () => (
    <>
      {walletQueryBlockedLines.length ? (
        <InkBox width="100%" marginBottom={1} flexDirection="column" flexShrink={0}>
          {renderWalletQueryBlockedNotice(tableWidth, 'wallet-query-blocked')}
        </InkBox>
      ) : null}
      <InkBox width="100%" flexDirection={shadowPanelsWide ? 'row' : 'column'} columnGap={1} rowGap={1} flexShrink={0}>
        {renderShadowWalletBox('Best Wallets', 'best', bestShadowWallets)}
        {renderShadowWalletBox('Worst Wallets', 'worst', worstShadowWallets)}
      </InkBox>

      <InkBox marginTop={1} flexGrow={1} flexDirection="column">
        <InkBox flexGrow={1}>
          <Box height="100%" accent={activePane === 'tracked'}>
            <InkBox width="100%" flexShrink={0}>
              <Text color={theme.accent} bold>{fit(`Tracked Wallet Profiles: ${trackedWallets.length}`, tableWidth)}</Text>
            </InkBox>
            <InkBox width="100%" height={1} flexShrink={0}>
              <Text color={theme.dim}>{fit('USERNAME', layout.usernameWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fit('ADDRESS', layout.addressWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('SINCE', layout.trackingSinceWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fit('TRACK', layout.tierWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('SKIP %', layout.skippedTradesWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('SEEN', layout.seenTradesWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('SEEN WR', layout.seenWinRateWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('COPIED', layout.observedResolvedWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('COPY WR', layout.observedWinRateWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('PROF WR', layout.profileWinRateWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('COPY P&L', layout.copyPnlWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('LAST TRADE', layout.lastSeenWidth)}</Text>
            </InkBox>

            <InkBox flexDirection="column" width="100%" height={trackedVisibleRows} flexShrink={0} justifyContent="flex-start">
              {visibleTrackedWallets.length ? (
                visibleTrackedWallets.map((wallet) => {
                  const isSelected = wallet.trader_address === selectedTrackedWalletAddress
                  const usernameLabel = wallet.username || '-'
                  const displayUsername = `${isSelected ? '> ' : '  '}${usernameLabel}`
                  const linkedUsername = terminalHyperlink(
                    fit(displayUsername, layout.usernameWidth),
                    walletProfileUrl(wallet)
                  )
                  const rowBackground = isSelected ? selectedRowBackground : undefined
                  const usernameColor = isSelected ? theme.accent : wallet.username ? theme.white : theme.dim
                  const addressColor = isSelected ? theme.accent : theme.white
                  const seenWinRateColor =
                    wallet.seen_win_rate == null
                      ? theme.dim
                      : probabilityColor(wallet.seen_win_rate)
                  const observedWinRateColor =
                    wallet.observed_win_rate == null
                      ? theme.dim
                      : probabilityColor(wallet.observed_win_rate)
                  const tierText = tierLabel(wallet.watch_tier)
                  const tierTextColor = isSelected ? theme.accent : tierColor(wallet.watch_tier)
                  const winRateColor =
                    wallet.win_rate == null ? theme.dim : probabilityColor(wallet.win_rate)
                  const skippedTradesColor =
                    wallet.skip_rate == null
                      ? theme.dim
                      : negativeHeatColor(wallet.skip_rate * 100, 100)
                  const localPnlColor =
                    wallet.local_pnl == null
                      ? theme.dim
                      : centeredGradientColor(wallet.local_pnl, maxAbsLocalPnl)

                  return (
                    <InkBox key={wallet.trader_address} width="100%" height={1}>
                      <Text color={usernameColor} backgroundColor={rowBackground} bold={isSelected}>{linkedUsername}</Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={addressColor} backgroundColor={rowBackground} bold={isSelected}>{formatAddress(wallet.trader_address, layout.addressWidth)}</Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={isSelected ? theme.white : theme.dim} backgroundColor={rowBackground} bold={isSelected}>
                        {fitRight(secondsAgo(wallet.tracking_started_at || undefined), layout.trackingSinceWidth)}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={tierTextColor} backgroundColor={rowBackground} bold={isSelected}>{fit(tierText, layout.tierWidth)}</Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={skippedTradesColor} backgroundColor={rowBackground}>
                        {fitRight(wallet.skip_rate == null ? '-' : formatPct(wallet.skip_rate, 0), layout.skippedTradesWidth)}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text backgroundColor={rowBackground}>
                        {fitRight(formatCount(wallet.seen_trades, layout.seenTradesWidth), layout.seenTradesWidth)}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={seenWinRateColor} backgroundColor={rowBackground}>
                        {fitRight(
                          wallet.seen_win_rate == null ? '-' : formatPct(wallet.seen_win_rate),
                          layout.seenWinRateWidth
                        )}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text backgroundColor={rowBackground}>
                        {fitRight(formatCount(wallet.observed_resolved, layout.observedResolvedWidth), layout.observedResolvedWidth)}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={observedWinRateColor} backgroundColor={rowBackground}>
                        {fitRight(
                          wallet.observed_win_rate == null ? '-' : formatPct(wallet.observed_win_rate),
                          layout.observedWinRateWidth
                        )}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={winRateColor} backgroundColor={rowBackground}>
                        {fitRight(wallet.win_rate == null ? '-' : formatPct(wallet.win_rate), layout.profileWinRateWidth)}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={localPnlColor} backgroundColor={rowBackground}>
                        {fitRight(formatSignedMoney(wallet.local_pnl, layout.copyPnlWidth), layout.copyPnlWidth)}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={isSelected ? theme.white : theme.dim} backgroundColor={rowBackground} bold={isSelected}>
                        {fitRight(secondsAgo(wallet.last_seen || undefined), layout.lastSeenWidth)}
                      </Text>
                    </InkBox>
                  )
                })
              ) : (
                walletQueryBlockedLines.length ? (
                  <InkBox flexDirection="column">
                    {renderWalletQueryBlockedNotice(tableWidth, 'tracked-wallets-blocked')}
                  </InkBox>
                ) : (
                  <Text color={theme.dim}>No watched wallets configured yet.</Text>
                )
              )}
            </InkBox>
            <InkBox width="100%" height={1} flexShrink={0}>
              <Text color={theme.dim}>{trackedFooterText}</Text>
            </InkBox>
          </Box>
        </InkBox>

        <InkBox height={1} />

        <InkBox flexGrow={1}>
          <Box height="100%" accent={activePane === 'dropped'}>
            <InkBox width="100%" flexShrink={0}>
              <Text color={theme.accent} bold>{fit(`Dropped Wallet Profiles: ${droppedWallets.length}`, tableWidth)}</Text>
            </InkBox>
            <InkBox width="100%" height={1} flexShrink={0}>
              <Text color={theme.dim}>{fit('USERNAME', droppedLayout.usernameWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fit('ADDRESS', droppedLayout.addressWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fit('REASON', droppedLayout.reasonWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('LAST TRADE', droppedLayout.lastSeenWidth)}</Text>
              <Text color={theme.dim}> </Text>
              <Text color={theme.dim}>{fitRight('DROPPED', droppedLayout.droppedWidth)}</Text>
            </InkBox>

            <InkBox flexDirection="column" width="100%" height={droppedVisibleRows} flexShrink={0} justifyContent="flex-start">
              {visibleDroppedWallets.length ? (
                visibleDroppedWallets.map((wallet) => {
                  const isSelected = wallet.trader_address === selectedDroppedWalletAddress
                  const usernameLabel = wallet.username || '-'
                  const displayUsername = `${isSelected ? '> ' : '  '}${usernameLabel}`
                  const linkedUsername = terminalHyperlink(
                    fit(displayUsername, droppedLayout.usernameWidth),
                    walletProfileUrl(wallet)
                  )
                  const rowBackground = isSelected ? selectedRowBackground : undefined
                  const usernameColor = isSelected ? theme.accent : wallet.username ? theme.white : theme.dim
                  const addressColor = isSelected ? theme.accent : theme.white

                  return (
                    <InkBox key={wallet.trader_address} width="100%" height={1}>
                      <Text color={usernameColor} backgroundColor={rowBackground} bold={isSelected}>{linkedUsername}</Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={addressColor} backgroundColor={rowBackground} bold={isSelected}>{formatAddress(wallet.trader_address, droppedLayout.addressWidth)}</Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={isSelected ? theme.white : theme.dim} backgroundColor={rowBackground} bold={isSelected}>
                        {fit(wallet.status_reason || '-', droppedLayout.reasonWidth)}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={isSelected ? theme.white : theme.dim} backgroundColor={rowBackground} bold={isSelected}>
                        {fitRight(secondsAgo(wallet.last_seen || undefined), droppedLayout.lastSeenWidth)}
                      </Text>
                      <Text backgroundColor={rowBackground}> </Text>
                      <Text color={isSelected ? theme.accent : theme.red} backgroundColor={rowBackground} bold={isSelected}>
                        {fitRight(secondsAgo(wallet.dropped_at || undefined), droppedLayout.droppedWidth)}
                      </Text>
                    </InkBox>
                  )
                })
              ) : (
                walletQueryBlockedLines.length ? (
                  <InkBox flexDirection="column">
                    {renderWalletQueryBlockedNotice(tableWidth, 'dropped-wallets-blocked')}
                  </InkBox>
                ) : (
                  <Text color={theme.dim}>No dropped wallets.</Text>
                )
              )}
            </InkBox>
            <InkBox width="100%" height={1} flexShrink={0}>
              <Text color={theme.dim}>{droppedFooterText}</Text>
            </InkBox>
          </Box>
        </InkBox>
      </InkBox>
    </>
  )

  return (
    <InkBox flexDirection="column" width="100%" height="100%">
      {renderPageBody()}
      {detailOpen && selectedWallet ? (
        <ModalOverlay backgroundColor={terminal.backgroundColor}>
          <InkBox borderStyle="round" borderColor={theme.accent} flexDirection="column" width={modalWidth}>
            <InkBox width="100%">
              <Text color={theme.accent} backgroundColor={modalBackground} bold>
                {` ${fit('Wallet Detail', detailHeaderWidth)}`}
              </Text>
              <Text backgroundColor={modalBackground}> </Text>
              <Text color={theme.dim} backgroundColor={modalBackground}>
                {`${fitRight(detailIndexLabel, detailIndexLabel.length)} `}
              </Text>
            </InkBox>
            <Text color={theme.white} backgroundColor={modalBackground} bold>
              {` ${fit(truncate(detailTitle, modalContentWidth), modalContentWidth)} `}
            </Text>
            {detailAddressLines.map((line) => (
              <Text key={line} color={theme.dim} backgroundColor={modalBackground}>
                {` ${fit(truncate(line, modalContentWidth), modalContentWidth)} `}
              </Text>
            ))}
            <Text color={theme.dim} backgroundColor={modalBackground}>
              {` ${fit(truncate('Watch = live poll state   Local = trade log   Profile = cached history', modalContentWidth), modalContentWidth)} `}
            </Text>
            <Text backgroundColor={modalBackground}>{modalSpacerLine}</Text>

            <Text color={theme.accent} backgroundColor={modalBackground} bold>
              {` ${fit('LOCAL COPY P&L OVER TIME', modalContentWidth)} `}
            </Text>
            {walletPnlHistoryPoints.length ? (
              <WalletPnlHistoryChart
                points={walletPnlHistoryPoints}
                width={walletChartWidth}
                offset={detailHistoryOffset}
                backgroundColor={modalBackground}
              />
            ) : (
              <Text color={theme.dim} backgroundColor={modalBackground}>
                {` ${fit('No resolved copied P&L history yet.', modalContentWidth)} `}
              </Text>
            )}
            <Text backgroundColor={modalBackground}>{modalSpacerLine}</Text>

            <InkBox flexDirection="column" width="100%">
              {Array.from({length: detailLineCount}, (_, rowIndex) => {
                const left = detailColumnLines[0]?.[rowIndex] || {kind: 'blank'}
                const right = detailColumnLines[1]?.[rowIndex] || {kind: 'blank'}

                return (
                  <InkBox key={`detail-row-${rowIndex}`} width="100%">
                    <Text backgroundColor={modalBackground}> </Text>
                    {left.kind === 'heading' ? (
                      <Text color={theme.accent} backgroundColor={modalBackground} bold>{left.text}</Text>
                    ) : left.kind === 'metric' ? (
                      <>
                        <Text color={theme.dim} backgroundColor={modalBackground}>{left.label}</Text>
                        <Text backgroundColor={modalBackground}> </Text>
                        <Text color={left.valueColor} backgroundColor={modalBackground}>{left.value}</Text>
                      </>
                    ) : (
                      <Text backgroundColor={modalBackground}>{' '.repeat(detailColumnWidth)}</Text>
                    )}
                    {detailColumnCount > 1 ? (
                      <>
                        <Text backgroundColor={modalBackground}>{' '.repeat(detailColumnGap)}</Text>
                        {right.kind === 'heading' ? (
                          <Text color={theme.accent} backgroundColor={modalBackground} bold>{right.text}</Text>
                        ) : right.kind === 'metric' ? (
                          <>
                            <Text color={theme.dim} backgroundColor={modalBackground}>{right.label}</Text>
                            <Text backgroundColor={modalBackground}> </Text>
                            <Text color={right.valueColor} backgroundColor={modalBackground}>{right.value}</Text>
                          </>
                        ) : (
                          <Text backgroundColor={modalBackground}>{' '.repeat(detailColumnWidth)}</Text>
                        )}
                      </>
                    ) : null}
                    {detailRowRemainderWidth > 0 ? (
                      <Text backgroundColor={modalBackground}>{' '.repeat(detailRowRemainderWidth)}</Text>
                    ) : null}
                    <Text backgroundColor={modalBackground}> </Text>
                  </InkBox>
                )
              })}
            </InkBox>
            <Text backgroundColor={modalBackground}>{modalSpacerLine}</Text>
            <Text color={theme.dim} backgroundColor={modalBackground}>
              {` ${fit(
                truncate(
                  activePane === 'dropped'
                    ? 'Up/down switches dropped wallets. left/right scrolls chart. a reactivates. esc closes.'
                    : activePane === 'tracked'
                      ? 'Up/down switches tracked wallets. left/right scrolls chart. d drops. esc closes.'
                      : 'Up/down switches leaderboard wallets. left/right scrolls chart. f finds this wallet in profiles. esc closes.',
                  modalContentWidth
                ),
                modalContentWidth
              )} `}
            </Text>
          </InkBox>
        </ModalOverlay>
      ) : null}
    </InkBox>
  )
}
