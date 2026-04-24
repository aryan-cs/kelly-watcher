import React, { useEffect, useMemo } from 'react';
import { Box as InkBox, Text } from 'ink';
import { Box } from '../components/Box.js';
import { TradeRow } from '../components/TradeRow.js';
import { normalizeReasonText } from '../format.js';
import { rowsForHeight } from '../responsive.js';
import { getSignalsLayout, signalsHeader } from '../tableLayout.js';
import { useTerminalSize } from '../terminal.js';
import { theme } from '../theme.js';
import { useEventStream } from '../useEventStream.js';
import { useTradeIdIndex } from '../useTradeIdIndex.js';
export function Signals({ scrollOffset = 0, horizontalOffset = 0, onScrollOffsetChange, onHorizontalOffsetChange }) {
    const terminal = useTerminalSize();
    const lineBudget = rowsForHeight(terminal.height, 10, 4);
    const visibleWidth = Math.max(56, terminal.width - 8);
    const events = useEventStream(1000);
    const { lookup: tradeIdLookup } = useTradeIdIndex(events);
    const incomingActionByTradeId = useMemo(() => {
        const lookup = new Map();
        for (const event of events) {
            if (event.type === 'incoming' && event.action?.trim()) {
                lookup.set(event.trade_id, event.action.trim());
            }
        }
        return lookup;
    }, [events]);
    const allSignals = useMemo(() => events.filter((event) => event.type === 'signal').reverse(), [events]);
    const maxReasonLength = useMemo(() => allSignals.slice(0, 250).reduce((max, event) => Math.max(max, normalizeReasonText(event.reason || '-').length), 6), [allSignals]);
    const layout = useMemo(() => getSignalsLayout(visibleWidth), [visibleWidth]);
    const maxOffset = Math.max(0, allSignals.length - lineBudget);
    const effectiveOffset = Math.max(0, Math.min(scrollOffset, maxOffset));
    const maxHorizontalOffset = Math.max(0, maxReasonLength - layout.reasonWidth);
    const effectiveHorizontalOffset = Math.max(0, Math.min(horizontalOffset, maxHorizontalOffset));
    const signals = useMemo(() => allSignals.slice(effectiveOffset, effectiveOffset + lineBudget), [allSignals, effectiveOffset, lineBudget]);
    const headerText = useMemo(() => signalsHeader(visibleWidth), [visibleWidth]);
    useEffect(() => {
        if (scrollOffset !== effectiveOffset) {
            onScrollOffsetChange?.(effectiveOffset);
        }
    }, [effectiveOffset, onScrollOffsetChange, scrollOffset]);
    useEffect(() => {
        if (horizontalOffset !== effectiveHorizontalOffset) {
            onHorizontalOffsetChange?.(effectiveHorizontalOffset);
        }
    }, [effectiveHorizontalOffset, horizontalOffset, onHorizontalOffsetChange]);
    return (React.createElement(Box, { height: "100%" },
        React.createElement(Text, { color: theme.dim }, headerText),
        React.createElement(InkBox, { flexDirection: "column", marginTop: 1 }, signals.length ? (signals.map((event, index) => (React.createElement(TradeRow, { key: `signal-${effectiveOffset + index}-${event.trade_id}-${event.ts}`, layout: "signals", maxWidth: visibleWidth, viewportOffset: effectiveHorizontalOffset, displayId: tradeIdLookup.get(event.trade_id), ts: event.ts, username: event.username, trader: event.trader, question: event.question, marketUrl: event.market_url, side: event.side, action: event.action ?? incomingActionByTradeId.get(event.trade_id), price: event.price, shares: event.shares ?? (event.price > 0 ? event.size_usd / event.price : 0), sizeUsd: event.amount_usd ?? event.size_usd, decision: event.decision, confidence: event.confidence, reason: event.reason })))) : (React.createElement(Text, { color: theme.dim }, "No scored signals yet."))),
        React.createElement(InkBox, { marginTop: 1 },
            React.createElement(Text, { color: theme.dim },
                "showing ",
                signals.length,
                " of ",
                allSignals.length,
                " signals",
                effectiveOffset > 0 ? `  scroll: +${effectiveOffset}` : '',
                effectiveHorizontalOffset > 0 ? `  pan: +${effectiveHorizontalOffset}` : ''))));
}
