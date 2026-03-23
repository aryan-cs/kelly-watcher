import React, { useEffect, useMemo } from 'react';
import { Box as InkBox, Text } from 'ink';
import { Box } from '../components/Box.js';
import { ModalOverlay } from '../components/ModalOverlay.js';
import { useDashboardConfig } from '../configEditor.js';
import { fit, fitRight, formatPct, secondsAgo, shortAddress, terminalHyperlink, truncate, wrapText } from '../format.js';
import { isPlaceholderUsername, useIdentityMap } from '../identities.js';
import { rowsForHeight } from '../responsive.js';
import { useTerminalSize } from '../terminal.js';
import { centeredGradientColor, negativeHeatColor, positiveDollarColor, probabilityColor, selectionBackgroundColor, theme } from '../theme.js';
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
const UNCOPYABLE_TIMING_WHERE = `
(
  market_veto LIKE 'expires in <%'
  OR market_veto LIKE 'beyond max horizon %'
)
`;
const UNCOPYABLE_LIQUIDITY_WHERE = `
(
  market_veto='missing order book'
  OR market_veto='no visible order book depth'
  OR skip_reason LIKE 'shadow simulation rejected the buy because the order book had no asks%'
  OR skip_reason LIKE 'shadow simulation rejected the buy because there was not enough ask depth%'
)
`;
const UNCOPYABLE_SKIP_WHERE = `
(
  ${UNCOPYABLE_TIMING_WHERE}
  OR ${UNCOPYABLE_LIQUIDITY_WHERE}
)
`;
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
`;
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
`;
const WALLET_CURSOR_SQL = `
SELECT wallet_address, last_source_ts
FROM wallet_cursors
`;
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
`;
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
`;
function shadowWalletPnl(row) {
    const pnl = Number(row.pnl ?? 0);
    return Number.isFinite(pnl) ? pnl : 0;
}
function pickShadowLeaderboards(rows, limit = 5) {
    const sortedByBest = [...rows]
        .filter((row) => shadowWalletPnl(row) >= 0)
        .sort((left, right) => shadowWalletPnl(right) - shadowWalletPnl(left) ||
        String(left.trader_address || '').localeCompare(String(right.trader_address || '')));
    const best = sortedByBest.slice(0, limit);
    const bestWallets = new Set(best.map((row) => row.trader_address.trim().toLowerCase()));
    const worst = [...rows]
        .filter((row) => !bestWallets.has(row.trader_address.trim().toLowerCase()))
        .sort((left, right) => shadowWalletPnl(left) - shadowWalletPnl(right) ||
        String(left.trader_address || '').localeCompare(String(right.trader_address || '')))
        .slice(0, limit);
    return { best, worst };
}
function readWatchConfig(envValues) {
    const wallets = String(envValues.WATCHED_WALLETS || '')
        .split(',')
        .map((wallet) => wallet.trim().toLowerCase())
        .filter(Boolean);
    let hotCount = 12;
    let warmCount = 24;
    let uncopyablePenaltyMinBuys = 12;
    let uncopyablePenaltyWeight = 0.25;
    const parsedHotCount = Number.parseInt(String(envValues.HOT_WALLET_COUNT || ''), 10);
    if (Number.isFinite(parsedHotCount) && parsedHotCount > 0) {
        hotCount = parsedHotCount;
    }
    const parsedWarmCount = Number.parseInt(String(envValues.WARM_WALLET_COUNT || ''), 10);
    if (Number.isFinite(parsedWarmCount) && parsedWarmCount >= 0) {
        warmCount = parsedWarmCount;
    }
    const parsedPenaltyMinBuys = Number.parseInt(String(envValues.WALLET_UNCOPYABLE_PENALTY_MIN_BUYS || ''), 10);
    if (Number.isFinite(parsedPenaltyMinBuys) && parsedPenaltyMinBuys >= 0) {
        uncopyablePenaltyMinBuys = parsedPenaltyMinBuys;
    }
    const parsedPenaltyWeight = Number.parseFloat(String(envValues.WALLET_UNCOPYABLE_PENALTY_WEIGHT || ''));
    if (Number.isFinite(parsedPenaltyWeight) && parsedPenaltyWeight >= 0) {
        uncopyablePenaltyWeight = parsedPenaltyWeight;
    }
    return { wallets, hotCount, warmCount, uncopyablePenaltyMinBuys, uncopyablePenaltyWeight };
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
function walletProfileUrl(wallet) {
    if (!wallet.username) {
        return null;
    }
    const normalizedWallet = wallet.trader_address.trim().toLowerCase();
    return normalizedWallet ? `https://polymarket.com/profile/${normalizedWallet}` : null;
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
function clip(value, low = 0, high = 1) {
    return Math.max(low, Math.min(high, value));
}
function scoreWalletForTier(params) {
    const winRate = params.winRate ?? 0.5;
    const nTrades = Math.max(0, params.nTrades ?? 0);
    const avgReturn = params.avgReturn ?? 0;
    const realizedPnlUsd = params.realizedPnlUsd ?? 0;
    const openPositions = params.openPositions ?? 0;
    const lastSourceTs = params.lastSourceTs ?? 0;
    const cacheUpdatedAt = params.cacheUpdatedAt ?? 0;
    const buySignals = Math.max(0, params.buySignals ?? 0);
    const uncopyableSkipRate = clip(params.uncopyableSkipRate ?? 0);
    const shrunkWinRate = ((nTrades * winRate) + (20 * 0.5)) / (nTrades + 20);
    const winScore = clip((shrunkWinRate - 0.45) / 0.25);
    const sampleScore = clip(Math.log1p(nTrades) / Math.log1p(80));
    const returnScore = clip((avgReturn + 0.05) / 0.2);
    const pnlScore = clip(Math.log1p(Math.max(realizedPnlUsd, 0)) / Math.log1p(5000));
    let activityScore = 0;
    if (lastSourceTs > 0) {
        const activityAgeHours = Math.max(params.nowTs - lastSourceTs, 0) / 3600;
        activityScore = clip(1 - (activityAgeHours / 72));
    }
    else if (cacheUpdatedAt > 0) {
        const cacheAgeHours = Math.max(params.nowTs - cacheUpdatedAt, 0) / 3600;
        activityScore = cacheAgeHours <= 24 ? 0.35 : cacheAgeHours <= 72 ? 0.15 : 0;
    }
    const openScore = clip(openPositions / 3);
    const freshnessPenalty = cacheUpdatedAt > 0 && (params.nowTs - cacheUpdatedAt) > 86400 ? 0.1 : 0;
    const uncopyablePenalty = buySignals >= params.uncopyablePenaltyMinBuys && params.uncopyablePenaltyWeight > 0
        ? params.uncopyablePenaltyWeight * clip(buySignals / Math.max(params.uncopyablePenaltyMinBuys * 3, 1)) * uncopyableSkipRate
        : 0;
    const qualityScore = (0.45 * winScore) +
        (0.2 * returnScore) +
        (0.2 * sampleScore) +
        (0.15 * pnlScore);
    return Number((((0.7 * qualityScore) +
        (0.25 * activityScore) +
        (0.05 * openScore)) - freshnessPenalty - uncopyablePenalty).toFixed(4));
}
function tierLabel(tier) {
    if (tier === 'HOT')
        return 'HOT';
    if (tier === 'WARM')
        return 'WARM';
    return 'SLOW';
}
function tierColor(tier) {
    if (tier === 'HOT')
        return theme.green;
    if (tier === 'WARM')
        return theme.yellow;
    return theme.dim;
}
function getWalletsLayout(width, wallets) {
    const trackingSinceWidth = 10;
    const tierWidth = 5;
    const skippedTradesWidth = 6;
    const seenTradesWidth = 6;
    const seenWinRateWidth = 8;
    const observedResolvedWidth = 7;
    const observedWinRateWidth = 8;
    const profileWinRateWidth = 8;
    const copyPnlWidth = 11;
    const lastSeenWidth = 10;
    const fixedWidths = trackingSinceWidth +
        tierWidth +
        skippedTradesWidth +
        seenTradesWidth +
        seenWinRateWidth +
        observedResolvedWidth +
        observedWinRateWidth +
        profileWinRateWidth +
        copyPnlWidth +
        lastSeenWidth;
    const gapCount = 11;
    const variableBudget = Math.max(40, width - fixedWidths - gapCount);
    const desiredUsernameWidth = Math.max(14, wallets.reduce((max, wallet) => Math.max(max, (wallet.username || '-').length + 2), 0));
    const desiredAddressWidth = Math.max(18, wallets.reduce((max, wallet) => Math.max(max, wallet.trader_address.length), 0));
    let usernameWidth = Math.max(14, Math.min(desiredUsernameWidth, variableBudget - Math.min(desiredAddressWidth, Math.max(18, variableBudget - 14))));
    let addressWidth = variableBudget - usernameWidth;
    if (variableBudget >= desiredUsernameWidth + desiredAddressWidth) {
        usernameWidth = desiredUsernameWidth;
        addressWidth = variableBudget - usernameWidth;
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
    };
}
function buildDetailColumns(sections, wide) {
    if (!sections.length) {
        return [];
    }
    if (wide) {
        const midpoint = Math.ceil(sections.length / 2);
        return [sections.slice(0, midpoint), sections.slice(midpoint)].filter((column) => column.length > 0);
    }
    const columns = [[], []];
    sections.forEach((section, index) => {
        columns[index % 2].push(section);
    });
    return columns.filter((column) => column.length > 0);
}
function getDroppedWalletsLayout(width, sharedLayout) {
    const lastSeenWidth = 10;
    const droppedWidth = 10;
    const gapCount = 4;
    const usernameWidth = sharedLayout.usernameWidth;
    const addressWidth = sharedLayout.addressWidth;
    const reasonWidth = Math.max(14, width - usernameWidth - addressWidth - lastSeenWidth - droppedWidth - gapCount);
    return {
        usernameWidth,
        addressWidth,
        reasonWidth,
        lastSeenWidth,
        droppedWidth
    };
}
export function Wallets({ activePane, bestSelectedIndex, worstSelectedIndex, trackedSelectedIndex, droppedSelectedIndex, detailOpen, onWalletMetaChange }) {
    const terminal = useTerminalSize();
    const selectedRowBackground = selectionBackgroundColor(terminal.backgroundColor);
    const footerRows = 1;
    const shadowLeaderboardRows = 5;
    const shadowPanelHeight = shadowLeaderboardRows + 4;
    const totalVisibleRows = Math.max(8, rowsForHeight(terminal.height, terminal.wide ? 18 : 24, 4) - footerRows);
    const profileChromeRows = 4;
    const profileVisibleRows = Math.max(2, Math.floor(totalVisibleRows / 2) - profileChromeRows);
    const trackedVisibleRows = profileVisibleRows;
    const droppedVisibleRows = profileVisibleRows;
    const tableWidth = Math.max(52, terminal.width - 8);
    const activityRows = useQuery(WALLET_ACTIVITY_SQL);
    const traderCacheRows = useQuery(TRADER_CACHE_SQL);
    const walletCursorRows = useQuery(WALLET_CURSOR_SQL);
    const watchStateRows = useQuery(WALLET_WATCH_STATE_SQL);
    const shadowWalletRows = useQuery(SHADOW_WALLETS_SQL);
    const events = useEventStream(1000);
    const config = useDashboardConfig();
    const identityMap = useIdentityMap();
    const usernames = useMemo(() => {
        const lookup = new Map(identityMap);
        for (let index = events.length - 1; index >= 0; index -= 1) {
            const event = events[index];
            const wallet = event.trader?.trim().toLowerCase();
            const username = event.username?.trim();
            if (!wallet || !username || isPlaceholderUsername(username, wallet) || lookup.has(wallet)) {
                continue;
            }
            lookup.set(wallet, username);
        }
        return lookup;
    }, [events, identityMap]);
    const watchConfig = useMemo(() => readWatchConfig(config.safeValues), [config.safeValues]);
    const watchedWallets = watchConfig.wallets;
    const sourceWallets = useMemo(() => {
        const fallbackWallets = Array.from(new Set([
            ...activityRows.map((row) => row.trader_address.toLowerCase()),
            ...traderCacheRows.map((row) => row.trader_address.toLowerCase()),
            ...walletCursorRows.map((row) => row.wallet_address.toLowerCase()),
            ...watchStateRows.map((row) => row.wallet_address.toLowerCase())
        ]));
        return watchedWallets.length ? watchedWallets : fallbackWallets;
    }, [activityRows, traderCacheRows, walletCursorRows, watchStateRows, watchedWallets]);
    const watchStateByWallet = useMemo(() => new Map(watchStateRows.map((row) => [
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
    ])), [watchStateRows]);
    const cursorByWallet = useMemo(() => new Map(walletCursorRows.map((row) => [row.wallet_address.toLowerCase(), row])), [walletCursorRows]);
    const tierByWallet = useMemo(() => {
        const cacheByWallet = new Map(traderCacheRows.map((row) => [row.trader_address.toLowerCase(), row]));
        const activityByWallet = new Map(activityRows.map((row) => [row.trader_address.toLowerCase(), row]));
        const activeWallets = sourceWallets.filter((wallet) => watchStateByWallet.get(wallet)?.status !== 'dropped');
        const nowTs = Math.floor(Date.now() / 1000);
        const ranked = activeWallets.map((wallet, index) => {
            const cached = cacheByWallet.get(wallet);
            const cursor = cursorByWallet.get(wallet);
            const activity = activityByWallet.get(wallet);
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
                    uncopyableSkipRate: (activity?.buy_signals ?? 0) > 0
                        ? (activity?.uncopyable_skips ?? 0) / (activity?.buy_signals ?? 0)
                        : 0,
                    uncopyablePenaltyMinBuys: watchConfig.uncopyablePenaltyMinBuys,
                    uncopyablePenaltyWeight: watchConfig.uncopyablePenaltyWeight,
                    nowTs
                }),
                lastSourceTs: cursor?.last_source_ts ?? 0,
                cacheUpdatedAt: cached?.updated_at ?? 0
            };
        });
        ranked.sort((left, right) => (right.followScore - left.followScore ||
            right.lastSourceTs - left.lastSourceTs ||
            right.cacheUpdatedAt - left.cacheUpdatedAt ||
            left.index - right.index));
        const hotCount = Math.min(ranked.length, watchConfig.hotCount);
        const warmCount = Math.min(Math.max(ranked.length - hotCount, 0), watchConfig.warmCount);
        const lookup = new Map();
        ranked.forEach((row, index) => {
            const tier = index < hotCount ? 'HOT' : index < hotCount + warmCount ? 'WARM' : 'DISC';
            lookup.set(row.wallet, tier);
        });
        return lookup;
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
    ]);
    const wallets = useMemo(() => {
        const activityByWallet = new Map(activityRows.map((row) => [row.trader_address.toLowerCase(), row]));
        const cacheByWallet = new Map(traderCacheRows.map((row) => [row.trader_address.toLowerCase(), row]));
        return sourceWallets.map((wallet, index) => {
            const activity = activityByWallet.get(wallet);
            const cached = cacheByWallet.get(wallet);
            const cursor = cursorByWallet.get(wallet);
            const watchState = watchStateByWallet.get(wallet);
            return {
                trader_address: wallet,
                username: usernames.get(wallet) || '',
                watch_tier: watchState?.status === 'dropped' ? 'DISC' : (tierByWallet.get(wallet) || 'DISC'),
                buy_signals: activity?.buy_signals ?? 0,
                skipped_trades: activity?.skipped_trades ?? 0,
                uncopyable_skips: activity?.uncopyable_skips ?? 0,
                skip_rate: (activity?.seen_trades ?? 0) > 0
                    ? (activity?.skipped_trades ?? 0) / (activity?.seen_trades ?? 0)
                    : null,
                uncopyable_skip_rate: (activity?.buy_signals ?? 0) > 0
                    ? (activity?.uncopyable_skips ?? 0) / (activity?.buy_signals ?? 0)
                    : null,
                seen_trades: activity?.seen_trades ?? 0,
                seen_resolved: activity?.seen_resolved ?? 0,
                seen_wins: activity?.seen_wins ?? 0,
                seen_win_rate: (activity?.seen_resolved ?? 0) > 0
                    ? (activity?.seen_wins ?? 0) / (activity?.seen_resolved ?? 0)
                    : null,
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
                last_source_ts: cursor?.last_source_ts ?? null,
                status: watchState?.status === 'dropped' ? 'dropped' : 'active',
                status_reason: watchState?.status_reason ?? null,
                dropped_at: watchState?.dropped_at ?? null,
                reactivated_at: watchState?.reactivated_at ?? null,
                tracking_started_at: watchState?.tracking_started_at ?? watchState?.reactivated_at ?? watchState?.updated_at ?? null,
                last_source_ts_at_status: watchState?.last_source_ts_at_status ?? null,
                watch_index: index
            };
        });
    }, [activityRows, cursorByWallet, sourceWallets, tierByWallet, traderCacheRows, usernames, watchStateByWallet]);
    const trackedWallets = useMemo(() => wallets.filter((wallet) => wallet.status !== 'dropped'), [wallets]);
    const droppedWallets = useMemo(() => wallets.filter((wallet) => wallet.status === 'dropped'), [wallets]);
    const layout = useMemo(() => getWalletsLayout(tableWidth, trackedWallets.length ? trackedWallets : wallets), [tableWidth, trackedWallets, wallets]);
    const droppedLayout = useMemo(() => getDroppedWalletsLayout(tableWidth, layout), [layout, tableWidth]);
    const clampedTrackedSelectedIndex = trackedWallets.length
        ? Math.max(0, Math.min(trackedSelectedIndex, trackedWallets.length - 1))
        : 0;
    const clampedDroppedSelectedIndex = droppedWallets.length
        ? Math.max(0, Math.min(droppedSelectedIndex, droppedWallets.length - 1))
        : 0;
    const trackedWindowStart = trackedWallets.length > trackedVisibleRows
        ? Math.min(Math.max(clampedTrackedSelectedIndex - Math.floor(trackedVisibleRows / 2), 0), Math.max(0, trackedWallets.length - trackedVisibleRows))
        : 0;
    const droppedWindowStart = droppedWallets.length > droppedVisibleRows
        ? Math.min(Math.max(clampedDroppedSelectedIndex - Math.floor(droppedVisibleRows / 2), 0), Math.max(0, droppedWallets.length - droppedVisibleRows))
        : 0;
    const visibleTrackedWallets = trackedWallets.slice(trackedWindowStart, trackedWindowStart + trackedVisibleRows);
    const visibleDroppedWallets = droppedWallets.slice(droppedWindowStart, droppedWindowStart + droppedVisibleRows);
    const trackedVisibleStart = trackedWallets.length ? trackedWindowStart + 1 : 0;
    const trackedVisibleEnd = trackedWindowStart + visibleTrackedWallets.length;
    const droppedVisibleStart = droppedWallets.length ? droppedWindowStart + 1 : 0;
    const droppedVisibleEnd = droppedWindowStart + visibleDroppedWallets.length;
    const tierCounts = useMemo(() => trackedWallets.reduce((counts, wallet) => {
        counts[wallet.watch_tier] += 1;
        return counts;
    }, { HOT: 0, WARM: 0, DISC: 0 }), [trackedWallets]);
    const trackedFooterText = trackedWallets.length
        ? `showing ${trackedVisibleStart}-${trackedVisibleEnd} of ${trackedWallets.length}  selected ${clampedTrackedSelectedIndex + 1}/${trackedWallets.length}  hot/warm/slow ${tierCounts.HOT}/${tierCounts.WARM}/${tierCounts.DISC}`
        : 'showing 0 of 0';
    const droppedFooterText = droppedWallets.length
        ? `showing ${droppedVisibleStart}-${droppedVisibleEnd} of ${droppedWallets.length}  selected ${clampedDroppedSelectedIndex + 1}/${droppedWallets.length}  auto-dropped until reactivated`
        : 'no dropped wallets';
    const { best: bestShadowWallets, worst: worstShadowWallets } = useMemo(() => pickShadowLeaderboards(shadowWalletRows), [shadowWalletRows]);
    const bestWalletAddresses = useMemo(() => bestShadowWallets.map((wallet) => wallet.trader_address.toLowerCase()), [bestShadowWallets]);
    const worstWalletAddresses = useMemo(() => worstShadowWallets.map((wallet) => wallet.trader_address.toLowerCase()), [worstShadowWallets]);
    const clampedBestSelectedIndex = bestWalletAddresses.length
        ? Math.max(0, Math.min(bestSelectedIndex, bestWalletAddresses.length - 1))
        : 0;
    const clampedWorstSelectedIndex = worstWalletAddresses.length
        ? Math.max(0, Math.min(worstSelectedIndex, worstWalletAddresses.length - 1))
        : 0;
    const selectedBestWalletAddress = bestWalletAddresses[clampedBestSelectedIndex] || '';
    const selectedWorstWalletAddress = worstWalletAddresses[clampedWorstSelectedIndex] || '';
    const selectedTrackedWalletAddress = trackedWallets[clampedTrackedSelectedIndex]?.trader_address || '';
    const selectedDroppedWalletAddress = droppedWallets[clampedDroppedSelectedIndex]?.trader_address || '';
    const selectedWalletAddress = activePane === 'best'
        ? selectedBestWalletAddress
        : activePane === 'worst'
            ? selectedWorstWalletAddress
            : activePane === 'dropped'
                ? selectedDroppedWalletAddress
                : selectedTrackedWalletAddress;
    const walletByAddress = useMemo(() => new Map(wallets.map((wallet) => [wallet.trader_address.toLowerCase(), wallet])), [wallets]);
    const selectedWallet = selectedWalletAddress ? walletByAddress.get(selectedWalletAddress.toLowerCase()) || null : null;
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
        });
    }, [
        bestWalletAddresses,
        droppedWallets,
        onWalletMetaChange,
        trackedWallets,
        worstWalletAddresses
    ]);
    const shadowPanelsWide = terminal.wide;
    const shadowPanelWidth = shadowPanelsWide ? Math.max(44, Math.floor((tableWidth - 1) / 2)) : tableWidth;
    const shadowPanelContentWidth = Math.max(24, shadowPanelWidth - 4);
    const shadowCopyWrWidth = 10;
    const shadowSkipWidth = 6;
    const shadowCopyPnlWidth = 10;
    const shadowNameWidth = Math.max(10, shadowPanelContentWidth - shadowCopyWrWidth - shadowSkipWidth - shadowCopyPnlWidth - 3);
    const maxAbsShadowPnl = useMemo(() => shadowWalletRows.reduce((max, wallet) => Math.max(max, Math.abs(wallet.pnl || 0)), 0), [shadowWalletRows]);
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
                        color: selectedWallet.local_pnl == null
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
                        value: formatUnsignedMoney(selectedWallet.volume_usd, 18),
                        color: selectedWallet.volume_usd == null ? theme.dim : positiveDollarColor(selectedWallet.volume_usd, maxVolume || 1)
                    },
                    {
                        label: 'Avg Trade',
                        value: formatUnsignedMoney(selectedWallet.avg_size_usd, 18),
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
                        value: formatUnsignedMoney(selectedWallet.open_value_usd, 18),
                        color: selectedWallet.open_value_usd == null
                            ? theme.dim
                            : positiveDollarColor(selectedWallet.open_value_usd, maxHeld || 1)
                    },
                    {
                        label: 'Open P&L',
                        value: formatSignedMoney(selectedWallet.open_pnl_usd, 18),
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
    const detailColumnGap = terminal.wide ? 4 : 2;
    const modalBackground = terminal.backgroundColor || theme.modalBackground;
    const modalWidth = Math.max(60, Math.min(terminal.width - 6, terminal.wide ? 132 : 90));
    const modalContentWidth = Math.max(36, modalWidth - 4);
    const detailColumnWidth = Math.max(20, Math.floor((modalContentWidth - detailColumnGap * (detailColumnCount - 1)) / detailColumnCount));
    const detailRowInnerWidth = detailColumnWidth * detailColumnCount + detailColumnGap * (detailColumnCount - 1);
    const detailRowRemainderWidth = Math.max(0, modalContentWidth - detailRowInnerWidth);
    const detailLabelWidth = Math.max(8, Math.floor(detailColumnWidth * 0.46));
    const detailValueWidth = Math.max(7, detailColumnWidth - detailLabelWidth - 1);
    const detailIndexLabel = activePane === 'best'
        ? `${clampedBestSelectedIndex + 1}/${Math.max(bestWalletAddresses.length, 1)}`
        : activePane === 'worst'
            ? `${clampedWorstSelectedIndex + 1}/${Math.max(worstWalletAddresses.length, 1)}`
            : activePane === 'dropped'
                ? `${clampedDroppedSelectedIndex + 1}/${Math.max(droppedWallets.length, 1)}`
                : `${clampedTrackedSelectedIndex + 1}/${Math.max(trackedWallets.length, 1)}`;
    const detailHeaderWidth = Math.max(1, modalContentWidth - detailIndexLabel.length - 1);
    const modalSpacerLine = ' '.repeat(modalWidth - 2);
    const detailTitle = selectedWallet?.username || (selectedWallet ? shortAddress(selectedWallet.trader_address) : '-');
    const detailAddressLines = selectedWallet
        ? wrapText(`Address ${selectedWallet.trader_address}`, Math.max(20, modalContentWidth))
        : [];
    const detailColumnLines = useMemo(() => detailColumns.map((column) => {
        const lines = [];
        column.forEach((section, sectionIndex) => {
            lines.push({ kind: 'heading', text: fit(section.title.toUpperCase(), detailColumnWidth) });
            section.metrics.forEach((metric) => {
                lines.push({
                    kind: 'metric',
                    label: fit(metric.label, detailLabelWidth),
                    value: fitRight(metric.value, detailValueWidth),
                    valueColor: metric.color ?? theme.white
                });
            });
            if (sectionIndex < column.length - 1) {
                lines.push({ kind: 'blank' });
            }
        });
        return lines;
    }), [detailColumns, detailColumnWidth, detailLabelWidth, detailValueWidth]);
    const detailLineCount = useMemo(() => detailColumnLines.reduce((max, column) => Math.max(max, column.length), 0), [detailColumnLines]);
    const renderShadowWalletBox = (title, pane, shadowWallets) => {
        const paddedRows = Array.from({ length: shadowLeaderboardRows }, (_, index) => shadowWallets[index] ?? null);
        const activeShadowAddress = pane === 'best' ? selectedBestWalletAddress : selectedWorstWalletAddress;
        const boxIsSelected = activePane === pane;
        return (React.createElement(InkBox, { width: shadowPanelsWide ? undefined : '100%', flexGrow: shadowPanelsWide ? 1 : 0, flexBasis: shadowPanelsWide ? 0 : undefined },
            React.createElement(Box, { title: title, width: "100%", height: shadowPanelHeight, accent: boxIsSelected },
                React.createElement(InkBox, { width: "100%" },
                    React.createElement(Text, { color: theme.dim }, fit('WALLET', shadowNameWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('COPY WR%', shadowCopyWrWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('SKIP %', shadowSkipWidth)),
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('COPY P&L', shadowCopyPnlWidth))),
                React.createElement(InkBox, { flexDirection: "column" }, shadowWallets.length ? (paddedRows.map((wallet, index) => {
                    if (!wallet) {
                        return (React.createElement(InkBox, { key: `${title}-empty-${index}`, width: "100%" },
                            React.createElement(Text, { color: theme.dim }, fit('', shadowNameWidth)),
                            React.createElement(Text, null, " "),
                            React.createElement(Text, { color: theme.dim }, fitRight('', shadowCopyWrWidth)),
                            React.createElement(Text, null, " "),
                            React.createElement(Text, { color: theme.dim }, fitRight('', shadowSkipWidth)),
                            React.createElement(Text, null, " "),
                            React.createElement(Text, { color: theme.dim }, fitRight('', shadowCopyPnlWidth))));
                    }
                    const username = usernames.get(wallet.trader_address.toLowerCase());
                    const label = username || shortAddress(wallet.trader_address);
                    const linkedWallet = walletByAddress.get(wallet.trader_address.toLowerCase());
                    const isDroppedWallet = linkedWallet?.status === 'dropped';
                    const copyWinRate = wallet.resolved > 0 ? wallet.wins / wallet.resolved : null;
                    const skipRate = wallet.seen_trades > 0 ? wallet.skipped_trades / wallet.seen_trades : null;
                    const copyWinRateColor = copyWinRate == null ? theme.dim : probabilityColor(copyWinRate);
                    const skipRateColor = skipRate == null ? theme.dim : negativeHeatColor(skipRate * 100, 100);
                    const pnlColor = wallet.pnl == null
                        ? theme.dim
                        : centeredGradientColor(wallet.pnl, maxAbsShadowPnl);
                    return (React.createElement(InkBox, { key: `${title}-${wallet.trader_address}`, width: "100%" }, (() => {
                        const isSelected = boxIsSelected && wallet.trader_address.toLowerCase() === activeShadowAddress;
                        const rowBackground = isSelected ? selectedRowBackground : undefined;
                        const displayLabel = `${isSelected ? '> ' : '  '}${label}`;
                        const linkedLabel = terminalHyperlink(fit(displayLabel, shadowNameWidth), username ? walletProfileUrl({ trader_address: wallet.trader_address, username }) : null);
                        return (React.createElement(React.Fragment, null,
                            React.createElement(Text, { color: isSelected
                                    ? theme.accent
                                    : isDroppedWallet
                                        ? theme.red
                                        : username
                                            ? theme.white
                                            : theme.dim, backgroundColor: rowBackground, bold: isSelected }, linkedLabel),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: isSelected ? theme.accent : copyWinRateColor, backgroundColor: rowBackground, bold: isSelected }, fitRight(copyWinRate == null ? '-' : formatPct(copyWinRate, 1), shadowCopyWrWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: isSelected ? theme.accent : skipRateColor, backgroundColor: rowBackground, bold: isSelected }, fitRight(skipRate == null ? '-' : formatPct(skipRate, 0), shadowSkipWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: isSelected ? theme.accent : pnlColor, backgroundColor: rowBackground, bold: isSelected }, fitRight(formatSignedMoney(wallet.pnl, shadowCopyPnlWidth), shadowCopyPnlWidth))));
                    })()));
                })) : (React.createElement(Text, { color: theme.dim }, "No wallet performance yet."))))));
    };
    const renderPageBody = () => (React.createElement(React.Fragment, null,
        React.createElement(InkBox, { width: "100%", flexDirection: shadowPanelsWide ? 'row' : 'column', columnGap: 1, rowGap: 1, flexShrink: 0 },
            renderShadowWalletBox('Best Wallets', 'best', bestShadowWallets),
            renderShadowWalletBox('Worst Wallets', 'worst', worstShadowWallets)),
        React.createElement(InkBox, { marginTop: 1, flexGrow: 1, flexDirection: "column" },
            React.createElement(InkBox, { flexGrow: 1 },
                React.createElement(Box, { title: `Tracked Wallet Profiles: ${trackedWallets.length}`, height: "100%", accent: activePane === 'tracked' },
                    React.createElement(InkBox, { width: "100%", height: 1 },
                        React.createElement(Text, { color: theme.dim }, fit('USERNAME', layout.usernameWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fit('ADDRESS', layout.addressWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('SINCE', layout.trackingSinceWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fit('TRACK', layout.tierWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('SKIP %', layout.skippedTradesWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('SEEN', layout.seenTradesWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('SEEN WR', layout.seenWinRateWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('COPIED', layout.observedResolvedWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('COPY WR', layout.observedWinRateWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('PROF WR', layout.profileWinRateWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('COPY P&L', layout.copyPnlWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('LAST TRADE', layout.lastSeenWidth))),
                    React.createElement(InkBox, { flexDirection: "column", width: "100%", flexGrow: 1, justifyContent: "flex-start" }, visibleTrackedWallets.length ? (visibleTrackedWallets.map((wallet) => {
                        const isSelected = wallet.trader_address === selectedTrackedWalletAddress;
                        const usernameLabel = wallet.username || '-';
                        const displayUsername = `${isSelected ? '> ' : '  '}${usernameLabel}`;
                        const linkedUsername = terminalHyperlink(fit(displayUsername, layout.usernameWidth), walletProfileUrl(wallet));
                        const rowBackground = isSelected ? selectedRowBackground : undefined;
                        const usernameColor = isSelected ? theme.accent : wallet.username ? theme.white : theme.dim;
                        const addressColor = isSelected ? theme.accent : theme.white;
                        const seenWinRateColor = wallet.seen_win_rate == null
                            ? theme.dim
                            : probabilityColor(wallet.seen_win_rate);
                        const observedWinRateColor = wallet.observed_win_rate == null
                            ? theme.dim
                            : probabilityColor(wallet.observed_win_rate);
                        const tierText = tierLabel(wallet.watch_tier);
                        const tierTextColor = isSelected ? theme.accent : tierColor(wallet.watch_tier);
                        const winRateColor = wallet.win_rate == null ? theme.dim : probabilityColor(wallet.win_rate);
                        const skippedTradesColor = wallet.skip_rate == null
                            ? theme.dim
                            : negativeHeatColor(wallet.skip_rate * 100, 100);
                        const localPnlColor = wallet.local_pnl == null
                            ? theme.dim
                            : centeredGradientColor(wallet.local_pnl, maxAbsLocalPnl);
                        return (React.createElement(InkBox, { key: wallet.trader_address, width: "100%", height: 1 },
                            React.createElement(Text, { color: usernameColor, backgroundColor: rowBackground, bold: isSelected }, linkedUsername),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: addressColor, backgroundColor: rowBackground, bold: isSelected }, formatAddress(wallet.trader_address, layout.addressWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: isSelected ? theme.white : theme.dim, backgroundColor: rowBackground, bold: isSelected }, fitRight(secondsAgo(wallet.tracking_started_at || undefined), layout.trackingSinceWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: tierTextColor, backgroundColor: rowBackground, bold: isSelected }, fit(tierText, layout.tierWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: skippedTradesColor, backgroundColor: rowBackground }, fitRight(wallet.skip_rate == null ? '-' : formatPct(wallet.skip_rate, 0), layout.skippedTradesWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { backgroundColor: rowBackground }, fitRight(formatCount(wallet.seen_trades, layout.seenTradesWidth), layout.seenTradesWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: seenWinRateColor, backgroundColor: rowBackground }, fitRight(wallet.seen_win_rate == null ? '-' : formatPct(wallet.seen_win_rate), layout.seenWinRateWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { backgroundColor: rowBackground }, fitRight(formatCount(wallet.observed_resolved, layout.observedResolvedWidth), layout.observedResolvedWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: observedWinRateColor, backgroundColor: rowBackground }, fitRight(wallet.observed_win_rate == null ? '-' : formatPct(wallet.observed_win_rate), layout.observedWinRateWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: winRateColor, backgroundColor: rowBackground }, fitRight(wallet.win_rate == null ? '-' : formatPct(wallet.win_rate), layout.profileWinRateWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: localPnlColor, backgroundColor: rowBackground }, fitRight(formatSignedMoney(wallet.local_pnl, layout.copyPnlWidth), layout.copyPnlWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: isSelected ? theme.white : theme.dim, backgroundColor: rowBackground, bold: isSelected }, fitRight(secondsAgo(wallet.last_seen || undefined), layout.lastSeenWidth))));
                    })) : (React.createElement(Text, { color: theme.dim }, "No watched wallets configured yet."))),
                    React.createElement(Text, { color: theme.dim }, trackedFooterText))),
            React.createElement(InkBox, { height: 1 }),
            React.createElement(InkBox, { flexGrow: 1 },
                React.createElement(Box, { title: `Dropped Wallet Profiles: ${droppedWallets.length}`, height: "100%", accent: activePane === 'dropped' },
                    React.createElement(InkBox, { width: "100%", height: 1 },
                        React.createElement(Text, { color: theme.dim }, fit('USERNAME', droppedLayout.usernameWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fit('ADDRESS', droppedLayout.addressWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fit('REASON', droppedLayout.reasonWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('LAST TRADE', droppedLayout.lastSeenWidth)),
                        React.createElement(Text, { color: theme.dim }, " "),
                        React.createElement(Text, { color: theme.dim }, fitRight('DROPPED', droppedLayout.droppedWidth))),
                    React.createElement(InkBox, { flexDirection: "column", width: "100%", flexGrow: 1, justifyContent: "flex-start" }, visibleDroppedWallets.length ? (visibleDroppedWallets.map((wallet) => {
                        const isSelected = wallet.trader_address === selectedDroppedWalletAddress;
                        const usernameLabel = wallet.username || '-';
                        const displayUsername = `${isSelected ? '> ' : '  '}${usernameLabel}`;
                        const linkedUsername = terminalHyperlink(fit(displayUsername, droppedLayout.usernameWidth), walletProfileUrl(wallet));
                        const rowBackground = isSelected ? selectedRowBackground : undefined;
                        const usernameColor = isSelected ? theme.accent : wallet.username ? theme.white : theme.dim;
                        const addressColor = isSelected ? theme.accent : theme.white;
                        return (React.createElement(InkBox, { key: wallet.trader_address, width: "100%", height: 1 },
                            React.createElement(Text, { color: usernameColor, backgroundColor: rowBackground, bold: isSelected }, linkedUsername),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: addressColor, backgroundColor: rowBackground, bold: isSelected }, formatAddress(wallet.trader_address, droppedLayout.addressWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: isSelected ? theme.white : theme.dim, backgroundColor: rowBackground, bold: isSelected }, fit(wallet.status_reason || '-', droppedLayout.reasonWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: isSelected ? theme.white : theme.dim, backgroundColor: rowBackground, bold: isSelected }, fitRight(secondsAgo(wallet.last_seen || undefined), droppedLayout.lastSeenWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: isSelected ? theme.accent : theme.red, backgroundColor: rowBackground, bold: isSelected }, fitRight(secondsAgo(wallet.dropped_at || undefined), droppedLayout.droppedWidth))));
                    })) : (React.createElement(Text, { color: theme.dim }, "No dropped wallets."))),
                    React.createElement(Text, { color: theme.dim }, droppedFooterText))))));
    return (React.createElement(InkBox, { flexDirection: "column", width: "100%", height: "100%" },
        renderPageBody(),
        detailOpen && selectedWallet ? (React.createElement(ModalOverlay, { backgroundColor: terminal.backgroundColor },
            React.createElement(InkBox, { borderStyle: "round", borderColor: theme.accent, flexDirection: "column", width: modalWidth },
                React.createElement(InkBox, { width: "100%" },
                    React.createElement(Text, { color: theme.accent, backgroundColor: modalBackground, bold: true }, ` ${fit('Wallet Detail', detailHeaderWidth)}`),
                    React.createElement(Text, { backgroundColor: modalBackground }, " "),
                    React.createElement(Text, { color: theme.dim, backgroundColor: modalBackground }, `${fitRight(detailIndexLabel, detailIndexLabel.length)} `)),
                React.createElement(Text, { color: theme.white, backgroundColor: modalBackground, bold: true }, ` ${fit(truncate(detailTitle, modalContentWidth), modalContentWidth)} `),
                detailAddressLines.map((line) => (React.createElement(Text, { key: line, color: theme.dim, backgroundColor: modalBackground }, ` ${fit(truncate(line, modalContentWidth), modalContentWidth)} `))),
                React.createElement(Text, { color: theme.dim, backgroundColor: modalBackground }, ` ${fit(truncate('Watch = live poll state   Local = trade log   Profile = cached history', modalContentWidth), modalContentWidth)} `),
                React.createElement(Text, { backgroundColor: modalBackground }, modalSpacerLine),
                React.createElement(InkBox, { flexDirection: "column", width: "100%" }, Array.from({ length: detailLineCount }, (_, rowIndex) => {
                    const left = detailColumnLines[0]?.[rowIndex] || { kind: 'blank' };
                    const right = detailColumnLines[1]?.[rowIndex] || { kind: 'blank' };
                    return (React.createElement(InkBox, { key: `detail-row-${rowIndex}`, width: "100%" },
                        React.createElement(Text, { backgroundColor: modalBackground }, " "),
                        left.kind === 'heading' ? (React.createElement(Text, { color: theme.accent, backgroundColor: modalBackground, bold: true }, left.text)) : left.kind === 'metric' ? (React.createElement(React.Fragment, null,
                            React.createElement(Text, { color: theme.dim, backgroundColor: modalBackground }, left.label),
                            React.createElement(Text, { backgroundColor: modalBackground }, " "),
                            React.createElement(Text, { color: left.valueColor, backgroundColor: modalBackground }, left.value))) : (React.createElement(Text, { backgroundColor: modalBackground }, ' '.repeat(detailColumnWidth))),
                        detailColumnCount > 1 ? (React.createElement(React.Fragment, null,
                            React.createElement(Text, { backgroundColor: modalBackground }, ' '.repeat(detailColumnGap)),
                            right.kind === 'heading' ? (React.createElement(Text, { color: theme.accent, backgroundColor: modalBackground, bold: true }, right.text)) : right.kind === 'metric' ? (React.createElement(React.Fragment, null,
                                React.createElement(Text, { color: theme.dim, backgroundColor: modalBackground }, right.label),
                                React.createElement(Text, { backgroundColor: modalBackground }, " "),
                                React.createElement(Text, { color: right.valueColor, backgroundColor: modalBackground }, right.value))) : (React.createElement(Text, { backgroundColor: modalBackground }, ' '.repeat(detailColumnWidth))))) : null,
                        detailRowRemainderWidth > 0 ? (React.createElement(Text, { backgroundColor: modalBackground }, ' '.repeat(detailRowRemainderWidth))) : null,
                        React.createElement(Text, { backgroundColor: modalBackground }, " ")));
                })),
                React.createElement(Text, { backgroundColor: modalBackground }, modalSpacerLine),
                React.createElement(Text, { color: theme.dim, backgroundColor: modalBackground }, ` ${fit(truncate(activePane === 'dropped'
                    ? 'Up/down switches dropped wallets. a reactivates. esc closes.'
                    : activePane === 'tracked'
                        ? 'Up/down switches tracked wallets. d drops. left/right switches panes. esc closes.'
                        : 'Up/down switches leaderboard wallets. f finds this wallet in profiles. esc closes.', modalContentWidth), modalContentWidth)} `)))) : null));
}
