import { endCodesSet, linkEndCode } from './node_modules/@alcalzone/ansi-tokenize/build/ansiCodes.js';
const DEFAULT_DECIMAL_PLACES = 3;
// Ink's ANSI tokenizer does not register OSC-8 link terminators as end codes,
// so hyperlink close sequences can leak into later cells and break cmd-click.
if (!endCodesSet.has(linkEndCode)) {
    endCodesSet.add(linkEndCode);
}
export function truncate(text, max) {
    if (max <= 0)
        return '';
    if (text.length <= max)
        return text;
    if (max <= 3)
        return '.'.repeat(max);
    return `${text.slice(0, max - 3)}...`;
}
export function wrapText(text, width) {
    if (width <= 0)
        return [''];
    const normalized = text.replace(/\s+/g, ' ').trim();
    if (!normalized)
        return [''];
    const lines = [];
    let current = '';
    for (const rawWord of normalized.split(' ')) {
        let word = rawWord;
        while (word.length > width) {
            if (current) {
                lines.push(current);
                current = '';
            }
            lines.push(word.slice(0, width));
            word = word.slice(width);
        }
        if (!current) {
            current = word;
            continue;
        }
        const next = `${current} ${word}`;
        if (next.length <= width) {
            current = next;
        }
        else {
            lines.push(current);
            current = word;
        }
    }
    if (current) {
        lines.push(current);
    }
    return lines.length ? lines : [''];
}
export function normalizeReasonText(text) {
    const normalized = text
        .replace(/\bmarket veto:\s*/gi, 'market veto, ')
        .replace(/\bKelly:\s*/g, 'Kelly, ')
        .trim();
    if (!normalized) {
        return 'trade was rejected for an unspecified reason';
    }
    const lower = normalized.toLowerCase();
    if (lower.startsWith('heuristic sizing, ')) {
        return normalizeReasonText(normalized.slice(normalized.indexOf(',') + 1).trim());
    }
    if (lower.startsWith('kelly, ')) {
        return normalizeReasonText(normalized.slice(normalized.indexOf(',') + 1).trim());
    }
    if (lower === 'observed sell - not copying exits yet') {
        return 'watched trader was exiting a position, and the bot only copies entries right now';
    }
    if (lower === 'missing market snapshot') {
        return 'market data was unavailable when this trade was observed';
    }
    if (lower === 'failed to build market features') {
        return 'could not build the market snapshot needed to score this trade';
    }
    if (lower === 'duplicate trade_id') {
        return 'this trade was already seen, so it was skipped as a duplicate';
    }
    if (lower === 'order in-flight') {
        return 'an order for this market was already being placed, so this trade was skipped';
    }
    if (lower === 'position already open') {
        return 'we already had this side of the market open, so the trade was skipped';
    }
    if (lower === 'passed heuristic threshold') {
        return 'signal confidence cleared the heuristic threshold';
    }
    if (lower === 'passed model edge threshold') {
        return 'model edge cleared the required threshold';
    }
    if (lower === 'passed all checks') {
        return 'signal cleared scoring, sizing, and risk checks';
    }
    if (lower === 'signal rejected') {
        return 'trade did not pass the signal checks';
    }
    if (lower === 'bankroll depleted') {
        return 'balance too low, no bankroll was available for a new trade';
    }
    if (lower === 'negative kelly - no edge at this price/confidence') {
        return 'Kelly sizing found no positive edge at this price, so the trade was skipped';
    }
    let match = normalized.match(/^market veto,\s*(.+)$/i);
    if (match) {
        const detail = match[1].trim();
        const expiresMatch = detail.match(/^expires in <(\d+)s$/i);
        if (expiresMatch) {
            return `too close to resolution, less than ${expiresMatch[1]} seconds remained to place the trade`;
        }
        const horizonMatch = detail.match(/^beyond max horizon ([0-9.]+[smhdw])$/i);
        if (horizonMatch) {
            return `market resolves too far out, beyond the ${horizonMatch[1]} maximum horizon`;
        }
        if (detail === 'crossed order book') {
            return 'market data looked invalid because the order book was crossed';
        }
        if (detail === 'missing order book') {
            return 'market data was incomplete because there was no order book snapshot';
        }
        if (detail === 'no visible order book depth') {
            return 'market looked too thin to trade because there was no visible order book depth';
        }
        if (detail === 'invalid market mid') {
            return 'market data looked invalid because the midpoint price was out of bounds';
        }
        if (detail === 'invalid order book values') {
            return 'market data looked invalid because the order book values were negative';
        }
    }
    match = normalized.match(/^heuristic conf ([0-9.]+) < min ([0-9.]+)$/i);
    if (match) {
        return `signal confidence was ${(Number(match[1]) * 100).toFixed(1)}%, below the ${(Number(match[2]) * 100).toFixed(1)}% minimum`;
    }
    match = normalized.match(/^model edge (-?[0-9.]+) < threshold ([0-9.]+)$/i);
    if (match) {
        return `model edge was ${(Number(match[1]) * 100).toFixed(1)}%, below the ${(Number(match[2]) * 100).toFixed(1)}% threshold`;
    }
    match = normalized.match(/^max size \$([0-9.]+) < min \$([0-9.]+)$/i);
    if (match) {
        return `balance too low, calculated size was $${match[1]} but minimum bet size is $${match[2]}`;
    }
    match = normalized.match(/^available bankroll \$([0-9.]+) < min \$([0-9.]+)$/i);
    if (match) {
        return `balance too low, available bankroll was $${match[1]} but minimum bet size is $${match[2]}`;
    }
    match = normalized.match(/^size \$([0-9.]+) <= 0$/i);
    if (match) {
        return `calculated trade size was $${match[1]}, so no order was placed`;
    }
    match = normalized.match(/^conf ([0-9.]+) < min ([0-9.]+)$/i);
    if (match) {
        return `confidence was ${(Number(match[1]) * 100).toFixed(1)}%, below the ${(Number(match[2]) * 100).toFixed(1)}% minimum needed to place a trade`;
    }
    match = normalized.match(/^score ([0-9.]+) < min ([0-9.]+)$/i);
    if (match) {
        return `heuristic score was ${(Number(match[1]) * 100).toFixed(1)}%, below the ${(Number(match[2]) * 100).toFixed(1)}% minimum needed to place a trade`;
    }
    match = normalized.match(/^invalid price ([0-9.]+)$/i);
    if (match) {
        return `trade was skipped because the market price looked invalid (${match[1]})`;
    }
    return normalized;
}
export function fit(text, width) {
    if (width <= 0)
        return '';
    return truncate(text, width).padEnd(width);
}
export function fitRight(text, width) {
    if (width <= 0)
        return '';
    return truncate(text, width).padStart(width);
}
export function terminalHyperlink(label, url) {
    const text = label || '';
    const sanitizedUrl = String(url || '').replace(/[\u0000-\u001f\u007f]/g, '').trim();
    if (!text || !/^https?:\/\//i.test(sanitizedUrl)) {
        return text;
    }
    return `\u001B]8;;${sanitizedUrl}\u0007${text}\u001B]8;;\u0007`;
}
export function formatDisplayId(value, width) {
    if (value == null || width <= 0)
        return '-';
    return String(value).padStart(width, '0');
}
export function shortAddress(value) {
    if (!value || value.length < 12)
        return value || '-';
    return `${value.slice(0, 6)}...${value.slice(-4)}`;
}
export function formatNumber(value, digits = DEFAULT_DECIMAL_PLACES) {
    if (value == null || !Number.isFinite(value))
        return '-';
    return value.toFixed(digits);
}
export function formatDollar(value) {
    if (value == null || !Number.isFinite(value))
        return '-';
    const sign = value < 0 ? '-' : value > 0 ? '+' : '';
    return `${sign}$${Math.abs(value).toFixed(DEFAULT_DECIMAL_PLACES)}`;
}
export function formatAdaptiveDollar(value, width) {
    if (value == null || !Number.isFinite(value))
        return '-';
    if (width <= 0)
        return '';
    const sign = value < 0 ? '-' : '';
    const absoluteValue = Math.abs(value);
    for (let digits = DEFAULT_DECIMAL_PLACES; digits >= 0; digits -= 1) {
        const formatted = `${sign}$${absoluteValue.toFixed(digits)}`;
        if (formatted.length <= width) {
            return formatted;
        }
    }
    const integerOnly = `${sign}$${Math.round(absoluteValue)}`;
    return integerOnly.length <= width
        ? integerOnly
        : integerOnly.slice(0, width);
}
export function formatAdaptiveNumber(value, width) {
    if (value == null || !Number.isFinite(value))
        return '-';
    if (width <= 0)
        return '';
    for (let digits = DEFAULT_DECIMAL_PLACES; digits >= 0; digits -= 1) {
        const formatted = value.toFixed(digits);
        if (formatted.length <= width) {
            return formatted;
        }
    }
    const integerOnly = Math.round(value).toString();
    return integerOnly.length <= width
        ? integerOnly
        : integerOnly.slice(0, width);
}
export function formatPct(value, digits = DEFAULT_DECIMAL_PLACES) {
    if (value == null || !Number.isFinite(value))
        return '-';
    return `${(value * 100).toFixed(digits)}%`;
}
export function formatClock(ts) {
    if (!ts)
        return '--:--:-- --';
    return new Date(ts * 1000).toLocaleTimeString([], {
        hour12: true,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}
export function formatShortDateTime(ts) {
    if (!ts)
        return '-';
    const date = new Date(ts * 1000);
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const hour24 = date.getHours();
    const suffix = hour24 >= 12 ? 'PM' : 'AM';
    const hour12 = hour24 % 12 || 12;
    return `${month}/${day} ${hour12}:${minutes}${suffix}`;
}
export function timeUntil(ts) {
    if (!ts)
        return '-';
    const delta = Math.floor(ts - Date.now() / 1000);
    if (delta <= 0)
        return 'due';
    if (delta < 60)
        return `${delta}s`;
    if (delta < 3600)
        return `${Math.floor(delta / 60)}m`;
    if (delta < 86400) {
        const hours = Math.floor(delta / 3600);
        const minutes = Math.floor((delta % 3600) / 60);
        return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
    }
    const days = Math.floor(delta / 86400);
    const hours = Math.floor((delta % 86400) / 3600);
    return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
}
export function secondsAgo(ts) {
    if (!ts)
        return '-';
    const delta = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (delta < 60)
        return `${delta}s ago`;
    if (delta < 3600)
        return `${Math.floor(delta / 60)}m ago`;
    if (delta < 86400)
        return `${Math.floor(delta / 3600)}h ago`;
    return `${Math.floor(delta / 86400)}d ago`;
}
