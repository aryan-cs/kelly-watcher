import React from 'react';
import { Box, Text } from 'ink';
import { truncate } from '../format.js';
import { theme } from '../theme.js';
import { useTerminalSize } from '../terminal.js';
export function StatRow({ label, value, color = theme.white }) {
    const terminal = useTerminalSize();
    const maxLabel = terminal.compact ? 16 : 24;
    const maxValue = terminal.compact ? 14 : 24;
    return (React.createElement(Box, { justifyContent: "space-between" },
        React.createElement(Text, { color: theme.dim }, truncate(label, maxLabel)),
        React.createElement(Text, { color: color }, truncate(value, maxValue))));
}
