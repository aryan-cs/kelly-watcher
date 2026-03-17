import fs from 'fs'
import React, {useEffect, useMemo} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {envExamplePath, envPath, identityPath} from '../paths.js'
import {fit, fitRight, formatPct, secondsAgo, shortAddress, truncate, wrapText} from '../format.js'
import {isPlaceholderUsername, readIdentityMap} from '../identities.js'
import {rowsForHeight} from '../responsive.js'
import {useRefreshToken} from '../refresh.js'
import {useTerminalSize} from '../terminal.js'
import {centeredGradientColor, positiveDollarColor, probabilityColor, theme} from '../theme.js'
import {useQuery} from '../useDb.js'
import {useEventStream} from '../useEventStream.js'

interface WalletActivityRow {
  trader_address: string
  seen_trades: number | null
  local_pnl: number | null
  last_seen: number | null
  observed_resolved: number | null
  observed_wins: number | null
}

interface TraderCacheRow {
  trader_address: string
  win_rate: number | null
  n_trades: number | null
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

interface WalletRow {
  trader_address: string
  username: string
  seen_trades: number | null
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
  watch_index: number
}

interface TopShadowRow {
  trader_address: string
  n: number
  wins: number
  resolved: number
  pnl: number | null
}

interface WalletsLayout {
  usernameWidth: number
  addressWidth: number
  observedResolvedWidth: number
  observedWinRateWidth: number
  profileWinRateWidth: number
  copyPnlWidth: number
  lastSeenWidth: number
}

interface WalletsProps {
  selectedIndex: number
  detailOpen: boolean
  onWalletCountChange?: (count: number) => void
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

const WALLET_ACTIVITY_SQL = `
SELECT
  trader_address,
  COUNT(*) AS seen_trades,
  ROUND(SUM(COALESCE(shadow_pnl_usd, actual_pnl_usd)), 3) AS local_pnl,
  MAX(placed_at) AS last_seen,
  SUM(
    CASE
      WHEN outcome IS NOT NULL AND COALESCE(source_action, 'buy')='buy' THEN 1
      ELSE 0
    END
  ) AS observed_resolved,
  SUM(
    CASE
      WHEN outcome=1 AND COALESCE(source_action, 'buy')='buy' THEN 1
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

const SHADOW_WALLETS_SQL = `
SELECT
  trader_address,
  COUNT(*) AS n,
  SUM(CASE WHEN outcome=1 THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
  ROUND(SUM(shadow_pnl_usd), 3) AS pnl
FROM trade_log
WHERE real_money=0 AND skipped=0
GROUP BY trader_address
`

function readWatchedWallets(): string[] {
  const path = fs.existsSync(envPath) ? envPath : envExamplePath
  try {
    const lines = fs.readFileSync(path, 'utf8').split('\n')
    for (const rawLine of lines) {
      const line = rawLine.trim()
      if (!line || line.startsWith('#') || !line.startsWith('WATCHED_WALLETS=')) {
        continue
      }
      return line
        .slice('WATCHED_WALLETS='.length)
        .split(',')
        .map((wallet) => wallet.trim().toLowerCase())
        .filter(Boolean)
    }
  } catch {
    return []
  }
  return []
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

function getWalletsLayout(width: number, wallets: WalletRow[]): WalletsLayout {
  const observedResolvedWidth = 7
  const observedWinRateWidth = 8
  const profileWinRateWidth = 8
  const copyPnlWidth = 12
  const lastSeenWidth = 9
  const fixedWidths =
    observedResolvedWidth +
    observedWinRateWidth +
    profileWinRateWidth +
    copyPnlWidth +
    lastSeenWidth
  const gapCount = 6
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
    return sections.map((section) => [section])
  }

  return [
    [sections[0], sections[2]].filter(Boolean),
    [sections[1]].filter(Boolean)
  ]
}

export function Wallets({selectedIndex, detailOpen, onWalletCountChange}: WalletsProps) {
  const terminal = useTerminalSize()
  const visibleRows = rowsForHeight(terminal.height, 18, 4, 14)
  const tableWidth = Math.max(52, terminal.width - 8)
  const activityRows = useQuery<WalletActivityRow>(WALLET_ACTIVITY_SQL)
  const traderCacheRows = useQuery<TraderCacheRow>(TRADER_CACHE_SQL)
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

  const watchedWallets = useMemo(() => readWatchedWallets(), [refreshToken])

  const wallets = useMemo(() => {
    const activityByWallet = new Map(activityRows.map((row) => [row.trader_address.toLowerCase(), row]))
    const cacheByWallet = new Map(traderCacheRows.map((row) => [row.trader_address.toLowerCase(), row]))
    const fallbackWallets = Array.from(new Set([
      ...activityRows.map((row) => row.trader_address.toLowerCase()),
      ...traderCacheRows.map((row) => row.trader_address.toLowerCase())
    ]))
    const sourceWallets = watchedWallets.length ? watchedWallets : fallbackWallets

    return sourceWallets.map<WalletRow>((wallet, index) => {
      const activity = activityByWallet.get(wallet)
      const cached = cacheByWallet.get(wallet)
      return {
        trader_address: wallet,
        username: usernames.get(wallet) || '',
        seen_trades: activity?.seen_trades ?? 0,
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
        watch_index: index
      }
    })
  }, [activityRows, traderCacheRows, usernames, watchedWallets])
  const layout = useMemo(() => getWalletsLayout(tableWidth, wallets), [tableWidth, wallets])

  useEffect(() => {
    onWalletCountChange?.(wallets.length)
  }, [onWalletCountChange, wallets.length])

  const clampedSelectedIndex = wallets.length
    ? Math.max(0, Math.min(selectedIndex, wallets.length - 1))
    : 0
  const selectedWallet = wallets[clampedSelectedIndex] || null
  const windowStart =
    wallets.length > visibleRows
      ? Math.min(
          Math.max(clampedSelectedIndex - Math.floor(visibleRows / 2), 0),
          Math.max(0, wallets.length - visibleRows)
        )
      : 0
  const visibleWallets = wallets.slice(windowStart, windowStart + visibleRows)

  const traderCacheByWallet = useMemo(
    () => new Map(traderCacheRows.map((row) => [row.trader_address.toLowerCase(), row])),
    [traderCacheRows]
  )

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
  const shadowNameWidth = Math.max(14, Math.min(28, shadowPanelWidth - 22))
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
        title: 'Local',
        metrics: [
          {
            label: 'Seen Trades',
            value: formatFullCount(selectedWallet.seen_trades)
          },
          {
            label: 'Resolved Obs',
            value: formatFullCount(selectedWallet.observed_resolved)
          },
          {
            label: 'Observed Wins',
            value: formatFullCount(selectedWallet.observed_wins)
          },
          {
            label: 'Observed WR',
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
            label: 'Last Seen',
            value: secondsAgo(selectedWallet.last_seen || undefined)
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
  const modalWidth = Math.max(60, Math.min(terminal.width - 6, terminal.wide ? 132 : 90))
  const modalContentWidth = Math.max(36, modalWidth - 4)
  const detailColumnWidth = Math.max(
    20,
    Math.floor((modalContentWidth - detailColumnGap * (detailColumnCount - 1)) / detailColumnCount)
  )
  const detailLabelWidth = Math.max(8, Math.floor(detailColumnWidth * 0.46))
  const detailValueWidth = Math.max(7, detailColumnWidth - detailLabelWidth - 1)
  const detailTitle = selectedWallet?.username || (selectedWallet ? shortAddress(selectedWallet.trader_address) : '-')
  const detailAddressLines = selectedWallet
    ? wrapText(`Address ${selectedWallet.trader_address}`, Math.max(20, modalContentWidth))
    : []

  const renderShadowWalletBox = (title: string, shadowWallets: TopShadowRow[]) => (
    <Box title={title} width={shadowPanelsWide ? shadowPanelWidth : '100%'}>
      <InkBox width="100%">
        <Text color={theme.dim}>{fit('WALLET', shadowNameWidth)}</Text>
        <Text color={theme.dim}> </Text>
        <Text color={theme.dim}>{fitRight('PROFILE WR', 10)}</Text>
        <Text color={theme.dim}> </Text>
        <Text color={theme.dim}>{fitRight('COPY P&L', 10)}</Text>
      </InkBox>
      <InkBox flexDirection="column">
        {shadowWallets.length ? (
          shadowWallets.map((wallet) => {
            const username = usernames.get(wallet.trader_address.toLowerCase())
            const label = username || shortAddress(wallet.trader_address)
            const profile = traderCacheByWallet.get(wallet.trader_address.toLowerCase())
            const profileWinRate = profile?.win_rate ?? null
            const winRateColor =
              profileWinRate == null ? theme.dim : probabilityColor(profileWinRate)
            const pnlColor =
              wallet.pnl == null
                ? theme.dim
                : centeredGradientColor(wallet.pnl, maxAbsShadowPnl)

            return (
              <InkBox key={`${title}-${wallet.trader_address}`} width="100%">
                <Text color={username ? theme.white : theme.dim}>{fit(label, shadowNameWidth)}</Text>
                <Text> </Text>
                <Text color={winRateColor}>
                  {fitRight(profileWinRate == null ? '-' : formatPct(profileWinRate), 10)}
                </Text>
                <Text> </Text>
                <Text color={pnlColor}>{fitRight(formatSignedMoney(wallet.pnl, 10), 10)}</Text>
              </InkBox>
            )
          })
        ) : (
          <Text color={theme.dim}>No wallet performance yet.</Text>
        )}
      </InkBox>
    </Box>
  )

  return (
    <InkBox flexDirection="column" width="100%" height="100%">
      <InkBox flexDirection={shadowPanelsWide ? 'row' : 'column'} columnGap={1} rowGap={1}>
        {renderShadowWalletBox('Best Wallets', bestShadowWallets)}
        {renderShadowWalletBox('Worst Wallets', worstShadowWallets)}
      </InkBox>

      <InkBox marginTop={1} flexGrow={1}>
        <Box title={`Tracked Wallet Profiles: ${wallets.length}`} height="100%">
          <InkBox width="100%" height={1}>
            <Text color={theme.dim}>{fit('USERNAME', layout.usernameWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fit('ADDRESS', layout.addressWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fitRight('OBS', layout.observedResolvedWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fitRight('OBS WR', layout.observedWinRateWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fitRight('PROF WR', layout.profileWinRateWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fitRight('COPY P&L', layout.copyPnlWidth)}</Text>
            <Text color={theme.dim}> </Text>
            <Text color={theme.dim}>{fitRight('LAST', layout.lastSeenWidth)}</Text>
          </InkBox>

          <InkBox flexDirection="column" width="100%" flexGrow={1} justifyContent="flex-start">
            {visibleWallets.length ? (
              visibleWallets.map((wallet) => {
                const isSelected = wallet.watch_index === clampedSelectedIndex
                const usernameLabel = wallet.username || '-'
                const displayUsername = `${isSelected ? '> ' : '  '}${usernameLabel}`
                const usernameColor = isSelected ? theme.accent : wallet.username ? theme.white : theme.dim
                const addressColor = isSelected ? theme.accent : theme.white
                const observedWinRateColor =
                  wallet.observed_win_rate == null
                    ? theme.dim
                    : probabilityColor(wallet.observed_win_rate)
                const winRateColor =
                  wallet.win_rate == null ? theme.dim : probabilityColor(wallet.win_rate)
                const localPnlColor =
                  wallet.local_pnl == null
                    ? theme.dim
                    : centeredGradientColor(wallet.local_pnl, maxAbsLocalPnl)

                return (
                  <InkBox key={wallet.trader_address} width="100%" height={1}>
                    <Text color={usernameColor} bold={isSelected}>{fit(displayUsername, layout.usernameWidth)}</Text>
                    <Text> </Text>
                    <Text color={addressColor} bold={isSelected}>{formatAddress(wallet.trader_address, layout.addressWidth)}</Text>
                    <Text> </Text>
                    <Text>
                      {fitRight(formatCount(wallet.observed_resolved, layout.observedResolvedWidth), layout.observedResolvedWidth)}
                    </Text>
                    <Text> </Text>
                    <Text color={observedWinRateColor}>
                      {fitRight(
                        wallet.observed_win_rate == null ? '-' : formatPct(wallet.observed_win_rate),
                        layout.observedWinRateWidth
                      )}
                    </Text>
                    <Text> </Text>
                    <Text color={winRateColor}>
                      {fitRight(wallet.win_rate == null ? '-' : formatPct(wallet.win_rate), layout.profileWinRateWidth)}
                    </Text>
                    <Text> </Text>
                    <Text color={localPnlColor}>
                      {fitRight(formatSignedMoney(wallet.local_pnl, layout.copyPnlWidth), layout.copyPnlWidth)}
                    </Text>
                    <Text> </Text>
                    <Text color={isSelected ? theme.white : theme.dim} bold={isSelected}>
                      {fitRight(secondsAgo(wallet.last_seen || undefined), layout.lastSeenWidth)}
                    </Text>
                  </InkBox>
                )
              })
            ) : (
              <Text color={theme.dim}>No watched wallets configured yet.</Text>
            )}
          </InkBox>
        </Box>
      </InkBox>

      {detailOpen && selectedWallet ? (
        <InkBox position="absolute" width="100%" height="100%" justifyContent="center" alignItems="center">
          <InkBox borderStyle="round" borderColor={theme.accent} flexDirection="column" width={modalWidth} paddingX={1}>
            <InkBox justifyContent="space-between">
              <Text color={theme.accent} bold>Wallet Detail</Text>
              <Text color={theme.dim}>{`${selectedWallet.watch_index + 1}/${wallets.length}`}</Text>
            </InkBox>
            <Text color={theme.white} bold>{truncate(detailTitle, modalContentWidth)}</Text>
            {detailAddressLines.map((line) => (
              <Text key={line} color={theme.dim}>{truncate(line, modalContentWidth)}</Text>
            ))}
            <Text color={theme.dim}>
              {truncate('Local = trade log   Profile = cached history', modalContentWidth)}
            </Text>

            <InkBox marginTop={1} flexDirection="row" columnGap={detailColumnGap}>
              {detailColumns.map((column, columnIndex) => (
                <InkBox key={`detail-col-${columnIndex}`} flexDirection="column" width={detailColumnWidth}>
                  {column.map((section, sectionIndex) => (
                    <InkBox
                      key={`${section.title}-${sectionIndex}`}
                      flexDirection="column"
                      marginBottom={sectionIndex === column.length - 1 ? 0 : 1}
                    >
                      <Text color={theme.accent} bold>{fit(section.title.toUpperCase(), detailColumnWidth)}</Text>
                      {section.metrics.map((metric) => (
                        <InkBox key={`${section.title}-${metric.label}`} width={detailColumnWidth}>
                          <Text color={theme.dim}>{fit(metric.label, detailLabelWidth)}</Text>
                          <Text> </Text>
                          <Text color={metric.color ?? theme.white}>{fitRight(metric.value, detailValueWidth)}</Text>
                        </InkBox>
                      ))}
                    </InkBox>
                  ))}
                </InkBox>
              ))}
            </InkBox>

            <Text color={theme.dim}>{truncate('Up/down switches wallets. Esc closes.', modalContentWidth)}</Text>
          </InkBox>
        </InkBox>
      ) : null}
    </InkBox>
  )
}
