import React from 'react';
import { Box as InkBox, Transform } from 'ink';
import { modalScrimColor } from '../theme.js';
const ANSI_DIM_ON = '\u001b[2m';
const ANSI_DIM_OFF = '\u001b[22m';
const ANSI_BG_RESET = '\u001b[49m';
const ANSI_FULL_RESET_PATTERN = /\u001b\[(?:0)?m/g;
const ANSI_DIM_RESET_PATTERN = /\u001b\[22m/g;
const ANSI_BG_RESET_PATTERN = /\u001b\[49m/g;
function hexToRgb(hex) {
    const normalized = hex.replace('#', '');
    return [
        Number.parseInt(normalized.slice(0, 2), 16),
        Number.parseInt(normalized.slice(2, 4), 16),
        Number.parseInt(normalized.slice(4, 6), 16)
    ];
}
function backgroundAnsiCode(hex) {
    const [red, green, blue] = hexToRgb(hex);
    return `\u001b[48;2;${red};${green};${blue}m`;
}
function applyOverlayLine(line, backgroundCode) {
    return `${backgroundCode}${ANSI_DIM_ON}${line}`
        .replace(ANSI_FULL_RESET_PATTERN, (match) => `${match}${backgroundCode}${ANSI_DIM_ON}`)
        .replace(ANSI_BG_RESET_PATTERN, (match) => `${match}${backgroundCode}`)
        .replace(ANSI_DIM_RESET_PATTERN, (match) => `${match}${ANSI_DIM_ON}`)
        .concat(ANSI_BG_RESET, ANSI_DIM_OFF);
}
export function ModalOverlay({ children, backdrop, backgroundColor }) {
    const scrimColor = modalScrimColor(backgroundColor);
    const backgroundCode = backgroundAnsiCode(scrimColor);
    return (React.createElement(InkBox, { position: "absolute", width: "100%", height: "100%" },
        backdrop ? (React.createElement(InkBox, { position: "absolute", width: "100%", height: "100%" },
            React.createElement(Transform, { transform: (line) => applyOverlayLine(line, backgroundCode) }, backdrop))) : null,
        React.createElement(InkBox, { position: "absolute", width: "100%", height: "100%", justifyContent: "center", alignItems: "center" }, children)));
}
