import React from 'react';
import { Box as InkBox, Text } from 'ink';
import { theme } from '../theme.js';
export function Box({ title, children, width = '100%', height, accent = false }) {
    return (React.createElement(InkBox, { borderStyle: "round", borderColor: accent ? theme.accent : theme.border, flexDirection: "column", width: width, height: height, paddingX: 1 },
        title ? (React.createElement(InkBox, null,
            React.createElement(Text, { color: theme.accent, bold: true }, title))) : null,
        children));
}
