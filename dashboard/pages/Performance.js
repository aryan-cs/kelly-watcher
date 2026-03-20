import React, { useEffect, useMemo } from 'react';
import { Box as InkBox, Text } from 'ink';
import { BarSparkline } from '../components/BarSparkline.js';
import { Box } from '../components/Box.js';
import { StatRow } from '../components/StatRow.js';
import { fit, fitRight, formatAdaptiveDollar, formatAdaptiveNumber, formatDisplayId, formatShortDateTime, formatDollar, formatNumber, formatPct, secondsAgo, terminalHyperlink, timeUntil, shortAddress } from '../format.js';
import { stackPanels } from '../responsive.js';
import { useTerminalSize } from '../terminal.js';
import { centeredGradientColor, outcomeColor, positiveDollarColor, probabilityColor, theme } from '../theme.js';
import { useBotState } from '../useBotState.js';
import { useQuery } from '../useDb.js';
import { useEventStream } from '../useEventStream.js';
import { useTradeIdIndex } from '../useTradeIdIndex.js';
const EXECUTED_ENTRY_WHERE = `
skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
`;
const OPEN_EXECUTED_ENTRY_WHERE = `
${EXECUTED_ENTRY_WHERE}
AND COALESCE(remaining_entry_shares, actual_entry_shares, source_shares, 0) > 1e-9
AND COALESCE(remaining_entry_size_usd, actual_entry_size_usd, signal_size_usd, 0) > 1e-9
AND outcome IS NULL
AND exited_at IS NULL
`;
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
`;
const DAILY_SQL = `
SELECT
  real_money,
  strftime('%Y-%m-%d', datetime(COALESCE(resolved_at, placed_at), 'unixepoch')) AS day,
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
`;
const SHADOW_OPEN_POSITIONS_SQL = `
SELECT
  ('o:' || tl.id) AS row_key,
  tl.trade_id,
  tl.market_id,
  tl.market_url,
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
`;
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
`;
const RESOLVED_POSITIONS_SQL = `
SELECT
  ('t:' || tl.id) AS row_key,
  tl.trade_id,
  tl.market_id,
  tl.market_url,
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
`;
const DAILY_PNL_BAR_SCALE = 500;
function getPositionsLayout(width) {
    const showId = width >= 132;
    const showUser = width >= 110;
    const idWidth = showId ? 6 : 0;
    const userWidth = showUser ? (width >= 120 ? 14 : 10) : 0;
    const actionWidth = 4;
    const sideWidth = 6;
    const entryWidth = 5;
    const sharesWidth = 7;
    const sizeWidth = 8;
    const toWinWidth = 8;
    const profitWidth = 8;
    const confidenceWidth = 6;
    const ttrWidth = 11;
    const ageWidth = 8;
    const gaps = 11 + (showId ? 1 : 0) + (showUser ? 1 : 0);
    const fixedStatic = idWidth +
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
        ageWidth;
    const variableWidth = Math.max(24, width - fixedStatic - gaps);
    const questionMinWidth = 14;
    const resolutionMinWidth = 10;
    let resolutionWidth = Math.max(resolutionMinWidth, Math.min(20, Math.floor(variableWidth * 0.29)));
    let questionWidth = variableWidth - resolutionWidth;
    if (questionWidth < questionMinWidth) {
        questionWidth = questionMinWidth;
        resolutionWidth = Math.max(resolutionMinWidth, variableWidth - questionWidth);
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
    };
}
function getPositionPaneMetrics(terminalHeight, stacked, dailyCount) {
    const outerReserve = 10;
    const statsHeight = 9;
    const dailyHeight = 3 + Math.max(1, dailyCount);
    const topRowHeight = stacked ? statsHeight + 1 + dailyHeight : Math.max(statsHeight, dailyHeight);
    const sectionGaps = stacked ? 3 : 2;
    const availableHeight = Math.max(12, terminalHeight - outerReserve - topRowHeight - sectionGaps);
    const paneHeight = Math.max(6, Math.floor((availableHeight - 3) / 2));
    const visibleRows = Math.max(1, paneHeight - 5);
    return { paneHeight, visibleRows };
}
function getDailyPnlLayout(terminalWidth, stacked, valueWidth) {
    const dateWidth = 10;
    const minBarWidth = 9;
    const minTotalWidth = dateWidth + valueWidth + minBarWidth + 2;
    const panelContentWidth = stacked
        ? Math.max(minTotalWidth, terminalWidth - 10)
        : Math.max(minTotalWidth, Math.floor((terminalWidth - 15) / 2));
    return {
        dateWidth,
        valueWidth,
        barWidth: Math.max(minBarWidth, panelContentWidth - dateWidth - valueWidth - 2)
    };
}
export function Performance({ currentScrollOffset, pastScrollOffset, activePane, onCurrentScrollOffsetChange, onPastScrollOffsetChange }) {
    const terminal = useTerminalSize();
    const stacked = stackPanels(terminal.width);
    const rows = useQuery(SUMMARY_SQL);
    const daily = useQuery(DAILY_SQL);
    const shadowOpenPositions = useQuery(SHADOW_OPEN_POSITIONS_SQL);
    const livePositions = useQuery(LIVE_POSITIONS_SQL);
    const resolvedPositions = useQuery(RESOLVED_POSITIONS_SQL);
    const events = useEventStream(1000);
    const { lookup: tradeIdLookup } = useTradeIdIndex();
    const botState = useBotState(1000);
    const positionsTableWidth = Math.max(72, terminal.width - 10);
    const positionsLayout = getPositionsLayout(positionsTableWidth);
    const nowTs = Date.now() / 1000;
    const activeMode = botState.mode === 'live' ? 'live' : 'shadow';
    const activeRealMoney = activeMode === 'live' ? 1 : 0;
    const shadow = rows.find((row) => row.real_money === 0);
    const live = rows.find((row) => row.real_money === 1);
    const activeSummary = activeMode === 'live' ? live : shadow;
    const activeTitle = activeMode === 'live' ? 'Live' : 'Shadow';
    const usernames = useMemo(() => {
        const lookup = new Map();
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
    }, [events]);
    const activeOpenPositions = useMemo(() => activeMode === 'live'
        ? livePositions.filter((row) => row.real_money === activeRealMoney)
        : shadowOpenPositions, [activeMode, activeRealMoney, livePositions, shadowOpenPositions]);
    const activeResolvedPositions = useMemo(() => resolvedPositions.filter((row) => row.real_money === activeRealMoney), [activeRealMoney, resolvedPositions]);
    const currentPositions = useMemo(() => activeOpenPositions
        .filter((row) => row.market_close_ts <= 0 || row.market_close_ts > nowTs)
        .sort((left, right) => right.entered_at - left.entered_at), [activeOpenPositions, nowTs]);
    const currentPositionsTotal = useMemo(() => currentPositions.reduce((sum, row) => sum + (row.size_usd || 0), 0), [currentPositions]);
    const waitingPositions = useMemo(() => activeOpenPositions
        .filter((row) => row.market_close_ts > 0 && row.market_close_ts <= nowTs)
        .map((row) => ({ ...row, status: 'waiting' })), [activeOpenPositions, nowTs]);
    const waitingPositionsTotal = useMemo(() => waitingPositions.reduce((sum, row) => sum + (row.size_usd || 0), 0), [waitingPositions]);
    const pastPositions = useMemo(() => [...activeResolvedPositions, ...waitingPositions].sort((a, b) => Math.max(b.resolution_ts || 0, b.market_close_ts || 0, b.entered_at || 0) -
        Math.max(a.resolution_ts || 0, a.market_close_ts || 0, a.entered_at || 0)), [activeResolvedPositions, waitingPositions]);
    const activeDaily = useMemo(() => daily.filter((row) => row.real_money === activeRealMoney).slice(0, 7), [activeRealMoney, daily]);
    const dailyEntries = useMemo(() => activeDaily.slice().reverse().map((row) => {
        const pnl = Number(row.pnl || 0);
        return {
            day: row.day,
            pnl,
            label: formatDollar(pnl),
            normalized: Math.min(1, Math.abs(pnl) / DAILY_PNL_BAR_SCALE)
        };
    }), [activeDaily]);
    const dailyValueWidth = useMemo(() => dailyEntries.reduce((max, row) => Math.max(max, row.label.length), 10), [dailyEntries]);
    const dailyLayout = useMemo(() => getDailyPnlLayout(terminal.width, stacked, dailyValueWidth), [dailyValueWidth, stacked, terminal.width]);
    const paneMetrics = getPositionPaneMetrics(terminal.height, stacked, activeDaily.length);
    const currentMaxOffset = Math.max(0, currentPositions.length - paneMetrics.visibleRows);
    const pastMaxOffset = Math.max(0, pastPositions.length - paneMetrics.visibleRows);
    const effectiveCurrentScrollOffset = Math.min(currentScrollOffset, currentMaxOffset);
    const effectivePastScrollOffset = Math.min(pastScrollOffset, pastMaxOffset);
    const visibleCurrentPositions = currentPositions.slice(effectiveCurrentScrollOffset, effectiveCurrentScrollOffset + paneMetrics.visibleRows);
    const visiblePastPositions = pastPositions.slice(effectivePastScrollOffset, effectivePastScrollOffset + paneMetrics.visibleRows);
    const shadowBalance = botState.mode === 'shadow' && botState.bankroll_usd != null ? botState.bankroll_usd : null;
    const liveBalance = botState.mode === 'live' && botState.bankroll_usd != null ? botState.bankroll_usd : null;
    const activeBalance = activeMode === 'live' ? liveBalance : shadowBalance;
    useEffect(() => {
        if (currentScrollOffset !== effectiveCurrentScrollOffset) {
            onCurrentScrollOffsetChange?.(effectiveCurrentScrollOffset);
        }
    }, [currentScrollOffset, effectiveCurrentScrollOffset, onCurrentScrollOffsetChange]);
    useEffect(() => {
        if (pastScrollOffset !== effectivePastScrollOffset) {
            onPastScrollOffsetChange?.(effectivePastScrollOffset);
        }
    }, [pastScrollOffset, effectivePastScrollOffset, onPastScrollOffsetChange]);
    const getPositionProfit = (row) => {
        const shares = row.entry_price > 0 ? row.size_usd / row.entry_price : null;
        const toWin = row.status === 'exit'
            ? row.exit_size_usd
            : row.status === 'lose'
                ? 0
                : shares != null
                    ? shares
                    : null;
        return row.status === 'win' || row.status === 'lose' || row.status === 'exit'
            ? (row.pnl_usd ?? null)
            : toWin != null
                ? toWin - row.size_usd
                : null;
    };
    const renderPositionsTable = (rowsToRender, { showStatus = false, showTtr = true, profitScaleRows = rowsToRender } = {}) => {
        const trailingWidth = positionsLayout.ttrWidth;
        const trailingDelta = trailingWidth - positionsLayout.ttrWidth;
        const questionWidth = Math.max(14, positionsLayout.questionWidth - trailingDelta);
        const resolutionWidth = positionsLayout.resolutionWidth;
        const maxAbsProfit = profitScaleRows.reduce((max, row) => Math.max(max, Math.abs(getPositionProfit(row) ?? 0)), 0);
        return (React.createElement(React.Fragment, null,
            React.createElement(InkBox, { width: "100%" },
                positionsLayout.showId ? (React.createElement(React.Fragment, null,
                    React.createElement(Text, { color: theme.dim }, fitRight('ID', positionsLayout.idWidth)),
                    React.createElement(Text, { color: theme.dim }, " "))) : null,
                positionsLayout.showUser ? (React.createElement(React.Fragment, null,
                    React.createElement(Text, { color: theme.dim }, fit('FROM USER', positionsLayout.userWidth)),
                    React.createElement(Text, { color: theme.dim }, " "))) : null,
                React.createElement(Text, { color: theme.dim }, fit('IN MARKET', questionWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('AGE', positionsLayout.ageWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fit('ACTN', positionsLayout.actionWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fit('SIDE', positionsLayout.sideWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('ENTRY', positionsLayout.entryWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('SHARES', positionsLayout.sharesWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('TOTAL', positionsLayout.sizeWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('TO WIN', positionsLayout.toWinWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('PROFIT', positionsLayout.profitWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('CONF', positionsLayout.confidenceWidth)),
                React.createElement(Text, { color: theme.dim }, " "),
                React.createElement(Text, { color: theme.dim }, fitRight('RESOLUTION', resolutionWidth)),
                showTtr || showStatus ? (React.createElement(React.Fragment, null,
                    React.createElement(Text, { color: theme.dim }, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight(showStatus ? 'STATUS' : 'TTR', trailingWidth)))) : null),
            React.createElement(InkBox, { flexDirection: "column" }, rowsToRender.map((row) => {
                const sideColor = outcomeColor(row.side);
                const displayId = row.trade_id ? tradeIdLookup.get(row.trade_id) ?? null : null;
                const username = row.trader_address ? usernames.get(row.trader_address.toLowerCase()) : undefined;
                const userText = username || shortAddress(row.trader_address || '-');
                const displayIdText = formatDisplayId(displayId, positionsLayout.idWidth);
                const actionText = row.status === 'exit' ? 'SELL' : 'BUY';
                const actionColor = outcomeColor(actionText);
                const entryColor = row.entry_price > 0 ? probabilityColor(row.entry_price) : theme.dim;
                const confidenceColor = row.confidence != null ? probabilityColor(row.confidence) : theme.dim;
                const resolutionTs = row.resolution_ts || row.market_close_ts;
                const resolutionPassed = row.market_close_ts > 0 && row.market_close_ts <= nowTs;
                const resolutionColor = row.status === 'waiting' ? theme.red : theme.dim;
                const shares = row.entry_price > 0 ? row.size_usd / row.entry_price : null;
                const toWin = row.status === 'exit'
                    ? row.exit_size_usd
                    : row.status === 'lose'
                        ? 0
                        : shares != null
                            ? shares
                            : null;
                const profit = getPositionProfit(row);
                const statusText = row.status === 'win'
                    ? 'win'
                    : row.status === 'lose'
                        ? 'lose'
                        : row.status === 'exit'
                            ? profit != null && profit > 0
                                ? 'exit up'
                                : profit != null && profit < 0
                                    ? 'exit down'
                                    : 'exited'
                            : 'waiting';
                const statusColor = row.status === 'win'
                    ? theme.green
                    : row.status === 'lose'
                        ? theme.red
                        : row.status === 'exit'
                            ? profit != null && profit > 0
                                ? theme.green
                                : profit != null && profit < 0
                                    ? theme.red
                                    : theme.yellow
                            : theme.yellow;
                const toWinColor = toWin != null ? positiveDollarColor(toWin, 100) : theme.dim;
                const profitColor = profit == null
                    ? theme.dim
                    : centeredGradientColor(profit, maxAbsProfit || 1);
                return (React.createElement(InkBox, { key: row.row_key, width: "100%" },
                    positionsLayout.showId ? (React.createElement(React.Fragment, null,
                        React.createElement(Text, { color: theme.dim }, fitRight(displayIdText, positionsLayout.idWidth)),
                        React.createElement(Text, null, " "))) : null,
                    positionsLayout.showUser ? (React.createElement(React.Fragment, null,
                        React.createElement(Text, { color: username ? theme.white : theme.dim }, fit(userText, positionsLayout.userWidth)),
                        React.createElement(Text, null, " "))) : null,
                React.createElement(Text, { color: row.market_url ? theme.accent : undefined }, terminalHyperlink(fit(row.question || row.market_id, questionWidth), row.market_url)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight(secondsAgo(row.entered_at), positionsLayout.ageWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: actionColor }, fit(actionText, positionsLayout.actionWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: sideColor }, fit(row.side.toUpperCase(), positionsLayout.sideWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: entryColor }, fitRight(formatNumber(row.entry_price), positionsLayout.entryWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, null, fitRight(shares != null
                        ? formatAdaptiveNumber(shares, positionsLayout.sharesWidth)
                        : '-', positionsLayout.sharesWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, null, fitRight(formatAdaptiveDollar(row.size_usd, positionsLayout.sizeWidth), positionsLayout.sizeWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: toWinColor }, fitRight(toWin != null
                        ? formatAdaptiveDollar(toWin, positionsLayout.toWinWidth)
                        : '-', positionsLayout.toWinWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: profitColor }, fitRight(profit != null
                        ? formatAdaptiveDollar(profit, positionsLayout.profitWidth)
                        : '-', positionsLayout.profitWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: confidenceColor }, fitRight(formatPct(row.confidence, 1), positionsLayout.confidenceWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: resolutionColor }, fitRight(formatShortDateTime(resolutionTs), resolutionWidth)),
                    showTtr || showStatus ? (React.createElement(React.Fragment, null,
                        React.createElement(Text, null, " "),
                        React.createElement(Text, { color: showStatus ? statusColor : resolutionColor }, fitRight(showStatus ? statusText : timeUntil(row.market_close_ts), trailingWidth)))) : null));
            }))));
    };
    return (React.createElement(InkBox, { flexDirection: "column", width: "100%" },
        React.createElement(InkBox, { flexDirection: stacked ? 'column' : 'row' },
            React.createElement(Box, { title: activeTitle, width: stacked ? '100%' : '50%' },
                React.createElement(StatRow, { label: "Total P&L", value: formatDollar(activeSummary?.total_pnl), color: (activeSummary?.total_pnl || 0) >= 0 ? theme.green : theme.red }),
                React.createElement(StatRow, { label: "Current balance", value: activeBalance == null ? '-' : `$${activeBalance.toFixed(3)}`, color: activeBalance != null ? theme.white : theme.dim }),
                React.createElement(StatRow, { label: "Win rate", value: activeSummary ? formatPct(activeSummary.resolved ? activeSummary.wins / activeSummary.resolved : 0) : '-' }),
                React.createElement(StatRow, { label: "Resolved", value: String(activeSummary?.resolved || 0) }),
                React.createElement(StatRow, { label: "Avg confidence", value: formatPct(activeSummary?.avg_confidence) }),
                React.createElement(StatRow, { label: "Avg total", value: formatDollar(activeSummary?.avg_size) })),
            !stacked ? React.createElement(InkBox, { width: 1 }) : React.createElement(InkBox, { height: 1 }),
            React.createElement(Box, { title: `Daily ${activeTitle} P&L`, width: stacked ? '100%' : '50%' }, dailyEntries.length ? (dailyEntries.map((row) => {
                return (React.createElement(InkBox, { key: row.day, width: "100%" },
                    React.createElement(InkBox, { width: dailyLayout.dateWidth },
                        React.createElement(Text, null, fit(row.day, dailyLayout.dateWidth))),
                    React.createElement(Text, null, " "),
                    React.createElement(InkBox, { width: dailyLayout.barWidth },
                        React.createElement(BarSparkline, { value: row.normalized, width: dailyLayout.barWidth, positive: row.pnl >= 0, centered: true })),
                    React.createElement(Text, null, " "),
                    React.createElement(InkBox, { width: dailyLayout.valueWidth, justifyContent: "flex-end" },
                        React.createElement(Text, { color: theme.dim }, fitRight(row.label, dailyLayout.valueWidth)))));
            })) : (React.createElement(Text, { color: theme.dim }, `No resolved ${activeMode} trades yet.`)))),
        React.createElement(InkBox, { marginTop: 1, flexDirection: "column", height: paneMetrics.paneHeight * 2 + 1 },
            React.createElement(InkBox, { height: paneMetrics.paneHeight },
                React.createElement(Box, { title: `Current Positions (${currentPositions.length}, holding $${currentPositionsTotal.toFixed(3)})`, height: "100%", accent: activePane === 'current' }, visibleCurrentPositions.length ? (renderPositionsTable(visibleCurrentPositions, { profitScaleRows: currentPositions })) : (React.createElement(Text, { color: theme.dim }, "No open positions right now.")))),
            React.createElement(InkBox, { height: 1 }),
            React.createElement(InkBox, { height: paneMetrics.paneHeight },
                React.createElement(Box, { title: `Past Positions (${pastPositions.length}, waiting for $${waitingPositionsTotal.toFixed(2)})`, height: "100%", accent: activePane === 'past' }, visiblePastPositions.length ? (renderPositionsTable(visiblePastPositions, { showStatus: true, showTtr: false, profitScaleRows: pastPositions })) : (React.createElement(Text, { color: theme.dim }, "No past positions yet.")))))));
}
