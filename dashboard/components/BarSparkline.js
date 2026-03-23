import React from 'react';
import { Text } from 'ink';
import { theme } from '../theme.js';
export function BarSparkline({ value, width = 20, label, positive, centered = false, axisChar = '|' }) {
    const magnitude = Math.max(0, Math.min(1, Math.abs(value)));
    const color = (positive ?? value >= 0) ? theme.green : theme.red;
    if (centered) {
        const halfWidth = Math.max(1, Math.floor((width - 1) / 2));
        const filled = Math.round(magnitude * halfWidth);
        const empty = Math.max(0, halfWidth - filled);
        const leftEmpty = ' '.repeat(empty);
        const rightEmpty = ' '.repeat(empty);
        const leftBlank = ' '.repeat(halfWidth);
        const rightBlank = ' '.repeat(halfWidth);
        const filledBar = ' '.repeat(filled);
        return (React.createElement(Text, null,
            (positive ?? value >= 0) ? (React.createElement(React.Fragment, null,
                React.createElement(Text, null, leftBlank),
                React.createElement(Text, { color: theme.dim }, axisChar),
                React.createElement(Text, { backgroundColor: color }, filledBar),
                React.createElement(Text, null, rightEmpty))) : (React.createElement(React.Fragment, null,
                React.createElement(Text, null, leftEmpty),
                React.createElement(Text, { backgroundColor: color }, filledBar),
                React.createElement(Text, { color: theme.dim }, axisChar),
                React.createElement(Text, null, rightBlank))),
            label ? React.createElement(Text, { color: theme.dim },
                "  ",
                label) : null));
    }
    const filled = Math.round(magnitude * width);
    const empty = Math.max(0, width - filled);
    const bar = '█'.repeat(filled) + '░'.repeat(empty);
    return (React.createElement(Text, null,
        React.createElement(Text, { color: color }, bar),
        label ? React.createElement(Text, { color: theme.dim },
            "  ",
            label) : null));
}
