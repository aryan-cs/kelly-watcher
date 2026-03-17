import React from 'react';
import { Box as InkBox, Text } from 'ink';
import { Box } from '../components/Box.js';
import { TradeRow } from '../components/TradeRow.js';
import { rowsForHeight } from '../responsive.js';
import { feedHeader } from '../tableLayout.js';
import { useTerminalSize } from '../terminal.js';
import { theme } from '../theme.js';
import { useEventStream } from '../useEventStream.js';
import { useTradeIdIndex } from '../useTradeIdIndex.js';
export function LiveFeed({ scrollOffset = 0 }) {
    const terminal = useTerminalSize();
    const rowCount = rowsForHeight(terminal.height, 10, 4);
    const tableWidth = Math.max(56, terminal.width - 8);
    const { lookup: tradeIdLookup } = useTradeIdIndex();
    const allIncoming = useEventStream(1000).filter((event) => event.type === 'incoming').reverse();
    const maxOffset = Math.max(0, allIncoming.length - rowCount);
    const effectiveOffset = Math.min(scrollOffset, maxOffset);
    const events = allIncoming.slice(effectiveOffset, effectiveOffset + rowCount);
    return (React.createElement(Box, { height: "100%" },
        React.createElement(Text, { color: theme.dim }, feedHeader(tableWidth)),
        React.createElement(InkBox, { flexDirection: "column", marginTop: 1 }, events.length ? (events.map((event) => (React.createElement(TradeRow, { key: `${event.trade_id}-${event.ts}`, layout: "feed", maxWidth: tableWidth, displayId: tradeIdLookup.get(event.trade_id), ts: event.ts, username: event.username, trader: event.trader, question: event.question, side: event.side, action: event.action, price: event.price, shares: event.shares ?? event.size_usd, sizeUsd: event.amount_usd ?? event.size_usd * event.price })))) : (React.createElement(Text, { color: theme.dim }, "Waiting for incoming trade events..."))),
        React.createElement(InkBox, { marginTop: 1 },
            React.createElement(Text, { color: theme.dim },
                "showing ",
                events.length,
                " of ",
                allIncoming.length,
                " events",
                effectiveOffset > 0 ? `  scroll: +${effectiveOffset}` : ''))));
}
