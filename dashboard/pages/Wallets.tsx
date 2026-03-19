import fs from 'fs'
import Database from 'better-sqlite3'
import React, {useEffect, useMemo} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {dbPath, envExamplePath, envPath} from '../paths.js'
import {fit, fitRight, formatPct, secondsAgo, shortAddress, truncate, wrapText} from '../format.js'
import {isPlaceholderUsername, readIdentityMap} from '../identities.js'
import {rowsForHeight} from '../responsive.js'
import {useRefreshToken} from '../refresh.js'
import {useTerminalSize} from '../terminal.js'
import {centeredGradientColor, negativeHeatColor, positiveDollarColor, probabilityColor, selectionBackgroundColor, theme} from '../theme.js'
import {useQuery} from '../useDb.js'
import {useEventStream} from '../useEventStream.js'

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

type WatchTier = 'HOT' | 'WARM' | 'DISC'
export type WalletPane = 'tracked' | 'dropped'

export interface WalletMeta {
  trackedCount: number
  droppedCount: number
  trackedWalletAddresses: string[]
  droppedWalletAddresses: string[]
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
  activePane: WalletPane
  trackedSelectedIndex: number
  droppedSelectedIndex: number
  detailOpen: boolean
  onWalletMetaChange?: (meta: WalletMeta) => void
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

let walletWatchStateSchemaReady = false

function ensureWalletWatchStateSchema(): void {
  if (walletWatchStateSchemaReady) {
    return
  }

  const db = new Database(dbPath)
  try {
    db.exec(`
      CREATE TABLE IF NOT EXISTS wallet_watch_state (
        wallet_address           TEXT PRIMARY KEY,
        status                   TEXT NOT NULL DEFAULT 'active',
        status_reason            TEXT,
        dropped_at               INTEGER,
        reactivated_at           INTEGER,
        tracking_started_at      INTEGER NOT NULL DEFAULT 0,
        last_source_ts_at_status INTEGER NOT NULL DEFAULT 0,
        updated_at               INTEGER NOT NULL
      )
    `)
    const columns = new Set(
      (db.prepare('PRAGMA table_info(wallet_watch_state)').all() as Array<{name: string}>)
        .map((row) => String(row.name))
    )
    if (!columns.has('tracking_started_at')) {
      db.exec("ALTER TABLE wallet_watch_state ADD COLUMN tracking_started_at INTEGER NOT NULL DEFAULT 0")
    }
    walletWatchStateSchemaReady = true
  } finally {
    db.close()
  }
}

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
GROUP BY trader_address
HAVING SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} THEN 1 ELSE 0 END) > 0
`

function readWatchConfig(): {
  wallets: string[]
  hotCount: number
  warmCount: number
  uncopyablePenaltyMinBuys: number
  uncopyablePenaltyWeight: number
} {
  const path = fs.existsSync(envPath) ? envPath : envExamplePath
  let wallets: string[] = []
  let hotCount = 12
  let warmCount = 24
  let uncopyablePenaltyMinBuys = 12
  let uncopyablePenaltyWeight = 0.25
  try {
    const lines = fs.readFileSync(path, 'utf8').split('\n')
    for (const rawLine of lines) {
      const line = rawLine.trim()
      if (!line || line.startsWith('#') || !line.includes('=')) {
        continue
      }
      const [key, ...valueParts] = line.split('=')
      const value = valueParts.join('=').trim()
      if (key === 'WATCHED_WALLETS') {
        wallets = value
          .split(',')
          .map((wallet) => wallet.trim().toLowerCase())
          .filter(Boolean)
      } else if (key === 'HOT_WALLET_COUNT') {
        const parsed = Number.parseInt(value, 10)
        if (Number.isFinite(parsed) && parsed > 0) {
          hotCount = parsed
        }
      } else if (key === 'WARM_WALLET_COUNT') {
        const parsed = Number.parseInt(value, 10)
        if (Number.isFinite(parsed) && parsed >= 0) {
          warmCount = parsed
        }
      } else if (key === 'WALLET_UNCOPYABLE_PENALTY_MIN_BUYS') {
        const parsed = Number.parseInt(value, 10)
        if (Number.isFinite(parsed) && parsed >= 0) {
          uncopyablePenaltyMinBuys = parsed
        }
      } else if (key === 'WALLET_UNCOPYABLE_PENALTY_WEIGHT') {
        const parsed = Number.parseFloat(value)
        if (Number.isFinite(parsed) && parsed >= 0) {
          uncopyablePenaltyWeight = parsed
        }
      }
    }
  } catch {
    return {wallets: [], hotCount, warmCount, uncopyablePenaltyMinBuys, uncopyablePenaltyWeight}
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

function clip(value: number, low = 0, high = 1): number {
  return Math.max(low, Math.min(high, value))
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
  const qualityScore =
    (0.45 * winScore) +
    (0.2 * returnScore) +
    (0.2 * sampleScore) +
    (0.15 * pnlScore)
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

export function Wallets({
  activePane,
  trackedSelectedIndex,
  droppedSelectedIndex,
  detailOpen,
  onWalletMetaChange
}: WalletsProps) {
  ensureWalletWatchStateSchema()
  const terminal = useTerminalSize()
  const selectedRowBackground = selectionBackgroundColor(terminal.backgroundColor)
  const footerRows = 1
  const shadowLeaderboardRows = 5
  const shadowPanelHeight = shadowLeaderboardRows + 4
  const totalVisibleRows = Math.max(8, rowsForHeight(terminal.height, terminal.wide ? 18 : 24, 4) - footerRows)
  const profileChromeRows = 4
  const profileVisibleRows = Math.max(2, Math.floor(totalVisibleRows / 2) - profileChromeRows)
  const trackedVisibleRows = profileVisibleRows
  const droppedVisibleRows = profileVisibleRows
  const tableWidth = Math.max(52, terminal.width - 8)
  const activityRows = useQuery<WalletActivityRow>(WALLET_ACTIVITY_SQL)
  const traderCacheRows = useQuery<TraderCacheRow>(TRADER_CACHE_SQL)
  const walletCursorRows = useQuery<WalletCursorRow>(WALLET_CURSOR_SQL)
  const watchStateRows = useQuery<WalletWatchStateRow>(WALLET_WATCH_STATE_SQL)
  const shadowWalletRows = useQuery<TopShadowRow>(SHADOW_WALLETS_SQL)
  const events = useEventStream(1000)
  const refreshToken = useRefreshToken()

  const usernames = useMemo(() => {
    const lookup = readIdentityMap()
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
  }, [events, refreshToken])

  const watchConfig = useMemo(() => readWatchConfig(), [refreshToken])
  const watchedWallets = watchConfig.wallets
  const sourceWallets = useMemo(() => {
    const fallbackWallets = Array.from(new Set([
      ...activityRows.map((row) => row.trader_address.toLowerCase()),
      ...traderCacheRows.map((row) => row.trader_address.toLowerCase()),
      ...walletCursorRows.map((row) => row.wallet_address.toLowerCase()),
      ...watchStateRows.map((row) => row.wallet_address.toLowerCase())
    ]))
    return watchedWallets.length ? watchedWallets : fallbackWallets
  }, [activityRows, traderCacheRows, walletCursorRows, watchStateRows, watchedWallets])
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
    const activeWallets = sourceWallets.filter((wallet) => watchStateByWallet.get(wallet)?.status !== 'dropped')
    const nowTs = Math.floor(Date.now() / 1000)
    const ranked = activeWallets.map((wallet, index) => {
      const cached = cacheByWallet.get(wallet)
      const cursor = cursorByWallet.get(wallet)
      const activity = activityByWallet.get(wallet)
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
    watchConfig.hotCount,
    watchConfig.uncopyablePenaltyMinBuys,
    watchConfig.uncopyablePenaltyWeight,
    watchConfig.warmCount,
    watchStateByWallet
  ])

  const wallets = useMemo(() => {
    const activityByWallet = new Map(activityRows.map((row) => [row.trader_address.toLowerCase(), row]))
    const cacheByWallet = new Map(traderCacheRows.map((row) => [row.trader_address.toLowerCase(), row]))

    return sourceWallets.map<WalletRow>((wallet, index) => {
      const activity = activityByWallet.get(wallet)
      const cached = cacheByWallet.get(wallet)
      const cursor = cursorByWallet.get(wallet)
      const watchState = watchStateByWallet.get(wallet)
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
        watch_index: index
      }
    })
  }, [activityRows, cursorByWallet, sourceWallets, tierByWallet, traderCacheRows, usernames, watchStateByWallet])
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

  useEffect(() => {
    onWalletMetaChange?.({
      trackedCount: trackedWallets.length,
      droppedCount: droppedWallets.length,
      trackedWalletAddresses: trackedWallets.map((wallet) => wallet.trader_address),
      droppedWalletAddresses: droppedWallets.map((wallet) => wallet.trader_address)
    })
  }, [droppedWallets, onWalletMetaChange, trackedWallets])

  const clampedTrackedSelectedIndex = trackedWallets.length
    ? Math.max(0, Math.min(trackedSelectedIndex, trackedWallets.length - 1))
    : 0
  const clampedDroppedSelectedIndex = droppedWallets.length
    ? Math.max(0, Math.min(droppedSelectedIndex, droppedWallets.length - 1))
    : 0
  const selectedWallet =
    activePane === 'dropped'
      ? droppedWallets[clampedDroppedSelectedIndex] || null
      : trackedWallets[clampedTrackedSelectedIndex] || null

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
  const trackedFooterText = trackedWallets.length
    ? `showing ${trackedVisibleStart}-${trackedVisibleEnd} of ${trackedWallets.length}  selected ${clampedTrackedSelectedIndex + 1}/${trackedWallets.length}  hot/warm/slow ${tierCounts.HOT}/${tierCounts.WARM}/${tierCounts.DISC}`
    : 'showing 0 of 0'
  const droppedFooterText = droppedWallets.length
    ? `showing ${droppedVisibleStart}-${droppedVisibleEnd} of ${droppedWallets.length}  selected ${clampedDroppedSelectedIndex + 1}/${droppedWallets.length}  auto-dropped until reactivated`
    : 'no dropped wallets'

  const bestShadowWallets = useMemo(
    () => [...shadowWalletRows].sort((left, right) => (right.pnl || 0) - (left.pnl || 0)).slice(0, 5),
    [shadowWalletRows]
  )
  const worstShadowWallets = useMemo(
    () => [...shadowWalletRows].sort((left, right) => (left.pnl || 0) - (right.pnl || 0)).slice(0, 5),
    [shadowWalletRows]
  )
  const shadowPanelsWide = terminal.wide
  const shadowPanelWidth = shadowPanelsWide ? Math.max(44, Math.floor((tableWidth - 1) / 2)) : tableWidth
  const shadowCopyWrWidth = 10
  const shadowSkipWidth = 6
  const shadowCopyPnlWidth = 10
  const shadowNameWidth = Math.max(
    10,
    shadowPanelWidth - shadowCopyWrWidth - shadowSkipWidth - shadowCopyPnlWidth - 3
  )
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
  const detailLabelWidth = Math.max(8, Math.floor(detailColumnWidth * 0.46))
  const detailValueWidth = Math.max(7, detailColumnWidth - detailLabelWidth - 1)
  const detailIndexLabel = activePane === 'dropped'
    ? `${clampedDroppedSelectedIndex + 1}/${Math.max(droppedWallets.length, 1)}`
    : `${clampedTrackedSelectedIndex + 1}/${Math.max(trackedWallets.length, 1)}`
  const detailHeaderWidth = Math.max(1, modalContentWidth - detailIndexLabel.length - 1)
  const modalSpacerLine = ' '.repeat(modalWidth - 2)
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
  const selectedTrackedWalletAddress = trackedWallets[clampedTrackedSelectedIndex]?.trader_address || ''
  const selectedDroppedWalletAddress = droppedWallets[clampedDroppedSelectedIndex]?.trader_address || ''

  const renderShadowWalletBox = (title: string, shadowWallets: TopShadowRow[]) => {
    const paddedRows = Array.from({length: shadowLeaderboardRows}, (_, index) => shadowWallets[index] ?? null)

    return (
    <Box title={title} width={shadowPanelsWide ? shadowPanelWidth : '100%'} height={shadowPanelHeight}>
      <InkBox width="100%">
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

            return (
              <InkBox key={`${title}-${wallet.trader_address}`} width="100%">
                <Text color={username ? theme.white : theme.dim}>{fit(label, shadowNameWidth)}</Text>
                <Text> </Text>
                <Text color={copyWinRateColor}>
                  {fitRight(copyWinRate == null ? '-' : formatPct(copyWinRate, 1), shadowCopyWrWidth)}
                </Text>
                <Text> </Text>
                <Text color={skipRateColor}>
                  {fitRight(skipRate == null ? '-' : formatPct(skipRate, 0), shadowSkipWidth)}
                </Text>
                <Text> </Text>
                <Text color={pnlColor}>
                  {fitRight(formatSignedMoney(wallet.pnl, shadowCopyPnlWidth), shadowCopyPnlWidth)}
                </Text>
              </InkBox>
            )
          })
        ) : (
          <Text color={theme.dim}>No wallet performance yet.</Text>
        )}
      </InkBox>
    </Box>
    )
  }

  return (
    <InkBox flexDirection="column" width="100%" height="100%">
      <InkBox flexDirection={shadowPanelsWide ? 'row' : 'column'} columnGap={1} rowGap={1} flexShrink={0}>
        {renderShadowWalletBox('Best Wallets', bestShadowWallets)}
        {renderShadowWalletBox('Worst Wallets', worstShadowWallets)}
      </InkBox>

      <InkBox marginTop={1} flexGrow={1} flexDirection="column">
        <InkBox flexGrow={1}>
          <Box title={`Tracked Wallet Profiles: ${trackedWallets.length}`} height="100%" accent={activePane === 'tracked'}>
            <InkBox width="100%" height={1}>
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

            <InkBox flexDirection="column" width="100%" flexGrow={1} justifyContent="flex-start">
              {visibleTrackedWallets.length ? (
                visibleTrackedWallets.map((wallet) => {
                  const isSelected = wallet.trader_address === selectedTrackedWalletAddress
                  const usernameLabel = wallet.username || '-'
                  const displayUsername = `${isSelected ? '> ' : '  '}${usernameLabel}`
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
                      <Text color={usernameColor} backgroundColor={rowBackground} bold={isSelected}>{fit(displayUsername, layout.usernameWidth)}</Text>
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
                <Text color={theme.dim}>No watched wallets configured yet.</Text>
              )}
            </InkBox>
            <Text color={theme.dim}>{trackedFooterText}</Text>
          </Box>
        </InkBox>

        <InkBox height={1} />

        <InkBox flexGrow={1}>
          <Box title={`Dropped Wallet Profiles: ${droppedWallets.length}`} height="100%" accent={activePane === 'dropped'}>
            <InkBox width="100%" height={1}>
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

            <InkBox flexDirection="column" width="100%">
              {visibleDroppedWallets.length ? (
                visibleDroppedWallets.map((wallet) => {
                  const isSelected = wallet.trader_address === selectedDroppedWalletAddress
                  const usernameLabel = wallet.username || '-'
                  const displayUsername = `${isSelected ? '> ' : '  '}${usernameLabel}`
                  const rowBackground = isSelected ? selectedRowBackground : undefined
                  const usernameColor = isSelected ? theme.accent : wallet.username ? theme.white : theme.dim
                  const addressColor = isSelected ? theme.accent : theme.white

                  return (
                    <InkBox key={wallet.trader_address} width="100%" height={1}>
                      <Text color={usernameColor} backgroundColor={rowBackground} bold={isSelected}>{fit(displayUsername, droppedLayout.usernameWidth)}</Text>
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
                <Text color={theme.dim}>No dropped wallets.</Text>
              )}
            </InkBox>
            <Text color={theme.dim}>{droppedFooterText}</Text>
          </Box>
        </InkBox>
      </InkBox>

      {detailOpen && selectedWallet ? (
        <InkBox position="absolute" width="100%" height="100%" justifyContent="center" alignItems="center">
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

            <InkBox flexDirection="column" width="100%">
              {Array.from({length: detailLineCount}, (_, rowIndex) => {
                const left = detailColumnLines[0]?.[rowIndex] || {kind: 'blank'}
                const right = detailColumnLines[1]?.[rowIndex] || {kind: 'blank'}

                return (
                  <InkBox key={`detail-row-${rowIndex}`} width="100%">
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
                  </InkBox>
                )
              })}
            </InkBox>
            <Text backgroundColor={modalBackground}>{modalSpacerLine}</Text>
            <Text color={theme.dim} backgroundColor={modalBackground}>
              {` ${fit(
                truncate(
                  activePane === 'dropped'
                    ? 'Up/down switches dropped wallets. a reactivates. esc closes.'
                    : 'Up/down switches tracked wallets. left/right switches panes. esc closes.',
                  modalContentWidth
                ),
                modalContentWidth
              )} `}
            </Text>
          </InkBox>
        </InkBox>
      ) : null}
    </InkBox>
  )
}
