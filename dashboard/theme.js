export const theme = {
    accent: 'white',
    modalBackground: '#05080d',
    green: '#24ff7b',
    red: '#ff0f0f',
    blue: '#17cdff',
    yellow: '#ffd84d',
    white: 'white',
    dim: 'gray',
    border: 'gray'
};
function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}
function hexToRgb(hex) {
    const normalized = hex.replace('#', '');
    const value = normalized.length === 3
        ? normalized.split('').map((part) => part + part).join('')
        : normalized;
    return [
        Number.parseInt(value.slice(0, 2), 16),
        Number.parseInt(value.slice(2, 4), 16),
        Number.parseInt(value.slice(4, 6), 16)
    ];
}
function rgbToHex(red, green, blue) {
    return `#${[red, green, blue]
        .map((channel) => clamp(Math.round(channel), 0, 255).toString(16).padStart(2, '0'))
        .join('')}`;
}
function blendHex(left, right, t) {
    const [lr, lg, lb] = hexToRgb(left);
    const [rr, rg, rb] = hexToRgb(right);
    const mix = clamp(t, 0, 1);
    return rgbToHex(lr + (rr - lr) * mix, lg + (rg - lg) * mix, lb + (rb - lb) * mix);
}
function normalizeHexColor(raw) {
    if (!raw)
        return undefined;
    const normalized = raw.trim().toLowerCase();
    if (/^#[0-9a-f]{6}$/.test(normalized))
        return normalized;
    if (/^#[0-9a-f]{3}$/.test(normalized)) {
        return `#${normalized
            .slice(1)
            .split('')
            .map((part) => part + part)
            .join('')}`;
    }
    return undefined;
}
export function selectionBackgroundColor(backgroundColor) {
    return blendHex(normalizeHexColor(backgroundColor) || theme.modalBackground, '#ffffff', 0.07);
}
export function probabilityColor(value) {
    const normalized = clamp(value, 0, 1);
    if (normalized <= 0.5) {
        return blendHex(theme.red, theme.yellow, normalized * 2);
    }
    return blendHex(theme.yellow, theme.green, (normalized - 0.5) * 2);
}
export function centeredGradientColor(value, maxAbsValue) {
    if (maxAbsValue <= 0) {
        return theme.yellow;
    }
    return probabilityColor((value / maxAbsValue + 1) / 2);
}
export function positiveDollarColor(value, greenAt = 100) {
    if (value <= 0) {
        return '#ffffff';
    }
    return blendHex('#ffffff', theme.green, clamp(value / greenAt, 0, 1));
}
export function outcomeColor(side) {
    const normalized = side.trim().toLowerCase();
    const positiveSides = new Set(['yes', 'up', 'buy', 'long']);
    const negativeSides = new Set(['no', 'down', 'sell', 'short']);
    if (positiveSides.has(normalized)) {
        return theme.green;
    }
    if (negativeSides.has(normalized)) {
        return theme.red;
    }
    return theme.blue;
}
