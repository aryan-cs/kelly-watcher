import fs from 'fs';
import React, { useEffect, useMemo } from 'react';
import { Box as InkBox, Text } from 'ink';
import { Box } from '../components/Box.js';
import { envExamplePath, envPath, identityPath } from '../paths.js';
import { fit, fitRight, formatPct, secondsAgo, shortAddress, truncate, wrapText } from '../format.js';
import { rowsForHeight } from '../responsive.js';
import { useRefreshToken } from '../refresh.js';
import { useTerminalSize } from '../terminal.js';
import { centeredGradientColor, positiveDollarColor, probabilityColor, theme } from '../theme.js';
import { useQuery } from '../useDb.js';
import { useEventStream } from '../useEventStream.js';
const EXECUTED_ENTRY_WHERE = `
skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
`;
const RESOLVED_EXECUTED_ENTRY_WHERE = `
${EXECUTED_ENTRY_WHERE}
AND COALESCE(actual_pnl_usd, shadow_pnl_usd) IS NOT NULL
`;
const WALLET_ACTIVITY_SQL = `
SELECT
  trader_address,
  COUNT(*) AS seen_trades,
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
`;
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
`;
const TOP_SHADOW_SQL = `
SELECT
  trader_address,
  COUNT(*) AS n,
  SUM(CASE WHEN shadow_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN shadow_pnl_usd IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
  ROUND(SUM(shadow_pnl_usd), 3) AS pnl
FROM trade_log
WHERE real_money=0
  AND ${RESOLVED_EXECUTED_ENTRY_WHERE}
GROUP BY trader_address
ORDER BY pnl DESC
LIMIT 5
`;
function readWatchedWallets() {
    const path = fs.existsSync(envPath) ? envPath : envExamplePath;
    try {
        const lines = fs.readFileSync(path, 'utf8').split('\n');
        for (const rawLine of lines) {
            const line = rawLine.trim();
            if (!line || line.startsWith('#') || !line.startsWith('WATCHED_WALLETS=')) {
                continue;
            }
            return line
                .slice('WATCHED_WALLETS='.length)
                .split(',')
                .map((wallet) => wallet.trim().toLowerCase())
                .filter(Boolean);
        }
    }
    catch {
        return [];
    }
    return [];
}
function readIdentityUsernames() {
    const lookup = new Map();
    try {
        const payload = JSON.parse(fs.readFileSync(identityPath, 'utf8'));
        for (const [wallet, entry] of Object.entries(payload.wallets || {})) {
            const username = entry?.username?.trim();
            if (!wallet || !username) {
                continue;
            }
            lookup.set(wallet.toLowerCase(), username);
        }
    }
    catch {
        return lookup;
    }
    return lookup;
}
function formatAddress(value, width) {
    if (width <= 0)
        return '';
    if (!value)
        return '-'.padEnd(width);
    if (value.length <= width)
        return value.padEnd(width);
    if (width <= 12)
        return fit(value, width);
    const visible = width - 3;
    const prefixWidth = Math.max(8, Math.ceil(visible * 0.65));
    const suffixWidth = Math.max(4, visible - prefixWidth);
    return `${value.slice(0, prefixWidth)}...${value.slice(-suffixWidth)}`;
}
function formatSignedMoney(value, width) {
    if (value == null || Number.isNaN(value))
        return '-';
    const sign = value > 0 ? '+' : value < 0 ? '-' : '';
    const abs = Math.abs(value);
    for (let digits = 3; digits >= 0; digits -= 1) {
        const formatted = `${sign}$${abs.toLocaleString('en-US', {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits
        })}`;
        if (formatted.length <= width) {
            return formatted;
        }
    }
    return `${sign}$${Math.round(abs).toLocaleString('en-US')}`;
}
function formatUnsignedMoney(value, width) {
    if (value == null || Number.isNaN(value))
        return '-';
    for (let digits = 3; digits >= 0; digits -= 1) {
        const formatted = `$${value.toLocaleString('en-US', {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits
        })}`;
        if (formatted.length <= width) {
            return formatted;
        }
    }
    return `$${Math.round(value).toLocaleString('en-US')}`;
}
function formatCount(value, width) {
    if (value == null || Number.isNaN(value))
        return '-';
    const whole = Math.round(value);
    const grouped = whole.toLocaleString('en-US');
    if (grouped.length <= width) {
        return grouped;
    }
    const thresholds = [
        [1_000_000_000, 'b'],
        [1_000_000, 'm'],
        [1_000, 'k']
    ];
    for (const [divisor, suffix] of thresholds) {
        if (whole < divisor) {
            continue;
        }
        const compact = `${(whole / divisor).toFixed(1).replace(/\\.0$/, '')}${suffix}`;
        if (compact.length <= width) {
            return compact;
        }
    }
    return String(whole);
}
function formatFullCount(value) {
    if (value == null || Number.isNaN(value))
        return '-';
    return Math.round(value).toLocaleString('en-US');
}
function formatShortValue(value, digits = 2) {
    if (value == null || Number.isNaN(value))
        return '-';
    return value.toFixed(digits);
}
function formatAge(days) {
    if (days == null || Number.isNaN(days))
        return '-';
    if (days >= 365) {
        const years = (days / 365).toFixed(1).replace(/\.0$/, '');
        return `${years}y`;
    }
    if (days >= 30) {
        const months = Math.floor(days / 30);
        return `${months}mo`;
    }
    return `${Math.max(0, Math.round(days))}d`;
}
function getWalletsLayout(width) {
    const seenWidth = 5;
    const observedResolvedWidth = 7;
    const observedWinRateWidth = 8;
    const closedWidth = 7;
    const winRateWidth = 8;
    const lifePnlWidth = 11;
    const volumeWidth = 11;
    const heldWidth = 11;
    const openPnlWidth = 11;
    const openWidth = 5;
    const avgWidth = 9;
    const ageWidth = 6;
    const lastSeenWidth = 9;
    const fixedWidths = seenWidth +
        observedResolvedWidth +
        observedWinRateWidth +
        closedWidth +
        winRateWidth +
        lifePnlWidth +
        volumeWidth +
        heldWidth +
        openPnlWidth +
        openWidth +
        avgWidth +
        ageWidth +
        lastSeenWidth;
    const gapCount = 14;
    const variableBudget = Math.max(30, width - fixedWidths - gapCount);
    const usernameWidth = Math.max(12, Math.min(20, Math.floor(variableBudget * 0.44)));
    const addressWidth = Math.max(14, Math.min(24, variableBudget - usernameWidth));
    return {
        usernameWidth,
        addressWidth,
        seenWidth,
        observedResolvedWidth,
        observedWinRateWidth,
        closedWidth,
        winRateWidth,
        lifePnlWidth,
        volumeWidth,
        heldWidth,
        openPnlWidth,
        openWidth,
        avgWidth,
        ageWidth,
        lastSeenWidth
    };
}
function buildDetailColumns(sections, wide) {
    if (!sections.length) {
        return [];
    }
    if (wide) {
        return sections.map((section) => [section]);
    }
    return [
        [sections[0], sections[2]].filter(Boolean),
        [sections[1]].filter(Boolean)
    ];
}
export function Wallets({ selectedIndex, detailOpen, onWalletCountChange }) {
    const terminal = useTerminalSize();
    const visibleRows = rowsForHeight(terminal.height, 18, 4, 14);
    const tableWidth = Math.max(122, terminal.width - 12);
    const activityRows = useQuery(WALLET_ACTIVITY_SQL);
    const traderCacheRows = useQuery(TRADER_CACHE_SQL);
    const topShadowWallets = useQuery(TOP_SHADOW_SQL);
    const events = useEventStream(1000);
    const refreshToken = useRefreshToken();
    const layout = getWalletsLayout(tableWidth);
    const usernames = useMemo(() => {
        const lookup = readIdentityUsernames();
        for (let index = events.length - 1; index >= 0; index -= 1) {
            const event = events[index];
            const wallet = event.trader?.trim().toLowerCase();
            const username = event.username?.trim();
            if (!wallet || !username || lookup.has(wallet)) {
                continue;
            }
            lookup.set(wallet, username);
        }
        return lookup;
    }, [events, refreshToken]);
    const watchedWallets = useMemo(() => readWatchedWallets(), [refreshToken]);
    const wallets = useMemo(() => {
        const activityByWallet = new Map(activityRows.map((row) => [row.trader_address.toLowerCase(), row]));
        const cacheByWallet = new Map(traderCacheRows.map((row) => [row.trader_address.toLowerCase(), row]));
        const fallbackWallets = Array.from(new Set([
            ...activityRows.map((row) => row.trader_address.toLowerCase()),
            ...traderCacheRows.map((row) => row.trader_address.toLowerCase())
        ]));
        const sourceWallets = watchedWallets.length ? watchedWallets : fallbackWallets;
        return sourceWallets.map((wallet, index) => {
            const activity = activityByWallet.get(wallet);
            const cached = cacheByWallet.get(wallet);
            return {
                trader_address: wallet,
                username: usernames.get(wallet) || '',
                seen_trades: activity?.seen_trades ?? 0,
                local_pnl: activity?.local_pnl ?? null,
                last_seen: activity?.last_seen ?? null,
                observed_resolved: activity?.observed_resolved ?? 0,
                observed_wins: activity?.observed_wins ?? 0,
                observed_win_rate: (activity?.observed_resolved ?? 0) > 0
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
            };
        });
    }, [activityRows, traderCacheRows, usernames, watchedWallets]);
    useEffect(() => {
        onWalletCountChange?.(wallets.length);
    }, [onWalletCountChange, wallets.length]);
    const clampedSelectedIndex = wallets.length
        ? Math.max(0, Math.min(selectedIndex, wallets.length - 1))
        : 0;
    const selectedWallet = wallets[clampedSelectedIndex] || null;
    const windowStart = wallets.length > visibleRows
        ? Math.min(Math.max(clampedSelectedIndex - Math.floor(visibleRows / 2), 0), Math.max(0, wallets.length - visibleRows))
        : 0;
    const visibleWallets = wallets.slice(windowStart, windowStart + visibleRows);
    const traderCacheByWallet = useMemo(() => new Map(traderCacheRows.map((row) => [row.trader_address.toLowerCase(), row])), [traderCacheRows]);
    const topShadowNameWidth = Math.max(18, Math.min(32, tableWidth - 22));
    const maxAbsTopShadowPnl = useMemo(() => topShadowWallets.reduce((max, wallet) => Math.max(max, Math.abs(wallet.pnl || 0)), 0), [topShadowWallets]);
    const maxAbsLocalPnl = useMemo(() => wallets.reduce((max, wallet) => Math.max(max, Math.abs(wallet.local_pnl || 0)), 0), [wallets]);
    const maxAbsRealizedPnl = useMemo(() => wallets.reduce((max, wallet) => Math.max(max, Math.abs(wallet.realized_pnl_usd || 0)), 0), [wallets]);
    const maxAbsOpenPnl = useMemo(() => wallets.reduce((max, wallet) => Math.max(max, Math.abs(wallet.open_pnl_usd || 0)), 0), [wallets]);
    const maxAbsConsistency = useMemo(() => wallets.reduce((max, wallet) => Math.max(max, Math.abs(wallet.consistency || 0)), 0), [wallets]);
    const maxVolume = useMemo(() => wallets.reduce((max, wallet) => Math.max(max, wallet.volume_usd || 0), 0), [wallets]);
    const maxHeld = useMemo(() => wallets.reduce((max, wallet) => Math.max(max, wallet.open_value_usd || 0), 0), [wallets]);
    const maxAvgSize = useMemo(() => wallets.reduce((max, wallet) => Math.max(max, wallet.avg_size_usd || 0), 0), [wallets]);
    const detailSections = useMemo(() => {
        if (!selectedWallet) {
            return [];
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
                        value: formatSignedMoney(selectedWallet.local_pnl, 16),
                        color: selectedWallet.local_pnl == null
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
                        value: formatSignedMoney(selectedWallet.realized_pnl_usd, 16),
                        color: selectedWallet.realized_pnl_usd == null
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
                        value: formatUnsignedMoney(selectedWallet.volume_usd, 16),
                        color: selectedWallet.volume_usd == null ? theme.dim : positiveDollarColor(selectedWallet.volume_usd, maxVolume || 1)
                    },
                    {
                        label: 'Avg Trade',
                        value: formatUnsignedMoney(selectedWallet.avg_size_usd, 16),
                        color: selectedWallet.avg_size_usd == null
                            ? theme.dim
                            : positiveDollarColor(selectedWallet.avg_size_usd, maxAvgSize || 1)
                    },
                    {
                        label: 'Open Count',
                        value: formatFullCount(selectedWallet.open_positions)
                    },
                    {
                        label: 'Open Value',
                        value: formatUnsignedMoney(selectedWallet.open_value_usd, 16),
                        color: selectedWallet.open_value_usd == null
                            ? theme.dim
                            : positiveDollarColor(selectedWallet.open_value_usd, maxHeld || 1)
                    },
                    {
                        label: 'Open P&L',
                        value: formatSignedMoney(selectedWallet.open_pnl_usd, 16),
                        color: selectedWallet.open_pnl_usd == null
                            ? theme.dim
                            : centeredGradientColor(selectedWallet.open_pnl_usd, maxAbsOpenPnl)
                    },
                    {
                        label: 'Consistency',
                        value: formatShortValue(selectedWallet.consistency),
                        color: selectedWallet.consistency == null
                            ? theme.dim
                            : centeredGradientColor(selectedWallet.consistency, maxAbsConsistency || 1)
                    }
                ]
            }
        ];
    }, [
        maxAbsConsistency,
        maxAbsLocalPnl,
        maxAbsOpenPnl,
        maxAbsRealizedPnl,
        maxAvgSize,
        maxHeld,
        maxVolume,
        selectedWallet
    ]);
    const detailColumns = useMemo(() => buildDetailColumns(detailSections, terminal.wide), [detailSections, terminal.wide]);
    const detailColumnCount = detailColumns.length || 1;
    const detailColumnGap = terminal.wide ? 3 : 2;
    const modalWidth = Math.max(52, Math.min(terminal.width - 8, terminal.wide ? 108 : 78));
    const modalContentWidth = Math.max(36, modalWidth - 4);
    const detailColumnWidth = Math.max(18, Math.floor((modalContentWidth - detailColumnGap * (detailColumnCount - 1)) / detailColumnCount));
    const detailLabelWidth = Math.max(7, Math.floor(detailColumnWidth * 0.54));
    const detailValueWidth = Math.max(7, detailColumnWidth - detailLabelWidth - 1);
    const detailTitle = selectedWallet?.username || (selectedWallet ? shortAddress(selectedWallet.trader_address) : '-');
    const detailAddressLines = selectedWallet
        ? wrapText(`Address ${selectedWallet.trader_address}`, Math.max(20, modalContentWidth))
        : [];
    return (React.createElement(InkBox, { flexDirection: "column", width: "100%", height: "100%" },
        React.createElement(Box, { title: "Top Shadow Wallets" },
            React.createElement(InkBox, { width: "100%" },
                React.createElement(Text, { color: theme.dim }, fit('WALLET', topShadowNameWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('PROFILE WR', 10)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('SHDW P&L', 10))),
            React.createElement(InkBox, { flexDirection: "column" }, topShadowWallets.length ? (topShadowWallets.map((wallet) => {
                const username = usernames.get(wallet.trader_address.toLowerCase());
                const label = username || shortAddress(wallet.trader_address);
                const profile = traderCacheByWallet.get(wallet.trader_address.toLowerCase());
                const profileWinRate = profile?.win_rate ?? null;
                const winRateColor = profileWinRate == null ? theme.dim : probabilityColor(profileWinRate);
                const pnlColor = wallet.pnl == null
                    ? theme.dim
                    : centeredGradientColor(wallet.pnl, maxAbsTopShadowPnl);
                return (React.createElement(InkBox, { key: wallet.trader_address, width: "100%" },
                    React.createElement(Text, { color: username ? theme.white : theme.dim }, fit(label, topShadowNameWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: winRateColor }, fitRight(profileWinRate == null ? '-' : formatPct(profileWinRate), 10)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: pnlColor }, fitRight(formatSignedMoney(wallet.pnl, 10), 10))));
            })) : (React.createElement(Text, { color: theme.dim }, "No shadow wallet performance yet.")))),
        React.createElement(InkBox, { marginTop: 1, flexGrow: 1 },
            React.createElement(Box, { title: `Tracked Wallet Profiles: ${wallets.length}`, height: "100%" },
                React.createElement(InkBox, { width: "100%", height: 1 },
                    React.createElement(Text, { color: theme.dim }, fit('USERNAME', layout.usernameWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fit('ADDRESS', layout.addressWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('SEEN', layout.seenWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('OBS', layout.observedResolvedWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('OBS WR', layout.observedWinRateWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('CLOSED', layout.closedWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('PUB WR', layout.winRateWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('LIFE P&L', layout.lifePnlWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('VOLUME', layout.volumeWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('HELD', layout.heldWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('OPEN P&L', layout.openPnlWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('OPEN', layout.openWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('AVG', layout.avgWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('AGE', layout.ageWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('LAST', layout.lastSeenWidth))),
                React.createElement(InkBox, { flexDirection: "column", width: "100%", flexGrow: 1, justifyContent: "flex-start" }, visibleWallets.length ? (visibleWallets.map((wallet) => {
                    const isSelected = wallet.watch_index === clampedSelectedIndex;
                    const usernameLabel = wallet.username || '-';
                    const displayUsername = `${isSelected ? '> ' : '  '}${usernameLabel}`;
                    const usernameColor = isSelected ? theme.blue : wallet.username ? theme.white : theme.dim;
                    const addressColor = isSelected ? theme.blue : theme.white;
                    const observedWinRateColor = wallet.observed_win_rate == null
                        ? theme.dim
                        : probabilityColor(wallet.observed_win_rate);
                    const winRateColor = wallet.win_rate == null ? theme.dim : probabilityColor(wallet.win_rate);
                    const realizedPnlColor = wallet.realized_pnl_usd == null
                        ? theme.dim
                        : centeredGradientColor(wallet.realized_pnl_usd, maxAbsRealizedPnl);
                    const volumeColor = wallet.volume_usd == null
                        ? theme.dim
                        : positiveDollarColor(wallet.volume_usd, maxVolume || 1);
                    const heldColor = wallet.open_value_usd == null
                        ? theme.dim
                        : positiveDollarColor(wallet.open_value_usd, maxHeld || 1);
                    const openPnlColor = wallet.open_pnl_usd == null
                        ? theme.dim
                        : centeredGradientColor(wallet.open_pnl_usd, maxAbsOpenPnl);
                    const avgSizeColor = wallet.avg_size_usd == null
                        ? theme.dim
                        : positiveDollarColor(wallet.avg_size_usd, maxAvgSize || 1);
                    return (React.createElement(InkBox, { key: wallet.trader_address, width: "100%", height: 1 },
                        React.createElement(Text, { color: usernameColor, bold: isSelected }, fit(displayUsername, layout.usernameWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: addressColor, bold: isSelected }, formatAddress(wallet.trader_address, layout.addressWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, null, fitRight(formatCount(wallet.seen_trades, layout.seenWidth), layout.seenWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, null, fitRight(formatCount(wallet.observed_resolved, layout.observedResolvedWidth), layout.observedResolvedWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: observedWinRateColor }, fitRight(wallet.observed_win_rate == null ? '-' : formatPct(wallet.observed_win_rate), layout.observedWinRateWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, null, fitRight(formatCount(wallet.closed_trades, layout.closedWidth), layout.closedWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: winRateColor }, fitRight(wallet.win_rate == null ? '-' : formatPct(wallet.win_rate), layout.winRateWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: realizedPnlColor }, fitRight(formatSignedMoney(wallet.realized_pnl_usd, layout.lifePnlWidth), layout.lifePnlWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: volumeColor }, fitRight(formatUnsignedMoney(wallet.volume_usd, layout.volumeWidth), layout.volumeWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: heldColor }, fitRight(formatUnsignedMoney(wallet.open_value_usd, layout.heldWidth), layout.heldWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: openPnlColor }, fitRight(formatSignedMoney(wallet.open_pnl_usd, layout.openPnlWidth), layout.openPnlWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, null, fitRight(formatCount(wallet.open_positions, layout.openWidth), layout.openWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: avgSizeColor }, fitRight(formatUnsignedMoney(wallet.avg_size_usd, layout.avgWidth), layout.avgWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight(formatAge(wallet.account_age_d), layout.ageWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: isSelected ? theme.white : theme.dim, bold: isSelected }, fitRight(secondsAgo(wallet.last_seen || undefined), layout.lastSeenWidth))));
                })) : (React.createElement(Text, { color: theme.dim }, "No watched wallets configured yet."))))),
        detailOpen && selectedWallet ? (React.createElement(InkBox, { position: "absolute", width: "100%", height: "100%", justifyContent: "center", alignItems: "center" },
            React.createElement(InkBox, { borderStyle: "round", borderColor: theme.blue, flexDirection: "column", width: modalWidth, paddingX: 1 },
                React.createElement(InkBox, { justifyContent: "space-between" },
                    React.createElement(Text, { color: theme.blue, bold: true }, "Wallet Detail"),
                    React.createElement(Text, { color: theme.dim }, `${selectedWallet.watch_index + 1}/${wallets.length}`)),
                React.createElement(Text, { color: theme.white, bold: true }, truncate(detailTitle, modalContentWidth)),
                detailAddressLines.map((line) => (React.createElement(Text, { key: line, color: theme.dim }, truncate(line, modalContentWidth)))),
                React.createElement(Text, { color: theme.dim }, truncate('Local = trade log   Profile = cached history', modalContentWidth)),
                React.createElement(InkBox, { marginTop: 1, flexDirection: "row", columnGap: detailColumnGap }, detailColumns.map((column, columnIndex) => (React.createElement(InkBox, { key: `detail-col-${columnIndex}`, flexDirection: "column", width: detailColumnWidth }, column.map((section, sectionIndex) => (React.createElement(InkBox, { key: `${section.title}-${sectionIndex}`, flexDirection: "column", marginBottom: sectionIndex === column.length - 1 ? 0 : 1 },
                    React.createElement(Text, { color: theme.blue, bold: true }, fit(section.title.toUpperCase(), detailColumnWidth)),
                    section.metrics.map((metric) => (React.createElement(InkBox, { key: `${section.title}-${metric.label}`, width: detailColumnWidth },
                        React.createElement(Text, { color: theme.dim }, fit(metric.label, detailLabelWidth)),
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: metric.color ?? theme.white }, fitRight(metric.value, detailValueWidth)))))))))))),
                React.createElement(Text, { color: theme.dim }, truncate('Up/down switches wallets. Esc closes.', modalContentWidth))))) : null));
}
