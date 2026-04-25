import React from 'react';
import { Box, Spacer, Text } from 'ink';
import { truncate } from '../format.js';
import { theme } from '../theme.js';
import { useTerminalSize } from '../terminal.js';
export function StatRow({ label, value, color = theme.white, width }) {
    const terminal = useTerminalSize();
    const rowWidth = Math.max(1, Math.floor(width ?? (terminal.width - 4)));
    const maxLabel = Math.min(terminal.compact ? 16 : 24, Math.max(1, Math.floor(rowWidth * 0.58)));
    const maxValue = Math.min(terminal.compact ? 14 : 24, Math.max(0, rowWidth - maxLabel - 1));
    const hasGap = rowWidth > maxLabel + maxValue;
    return (React.createElement(Box, { width: rowWidth, flexShrink: 0 },
        React.createElement(Text, { color: theme.dim }, truncate(label, maxLabel)),
        hasGap ? React.createElement(Spacer, null) : null,
        React.createElement(Text, { color: color }, truncate(value, maxValue))));
}
