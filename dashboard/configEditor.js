import fs from 'fs';
import { envExamplePath, envPath } from './paths.js';
export const maxMarketHorizonPresets = [
    '5m',
    '1h',
    '24h',
    '7d',
    '30d',
    '180d',
    '365d',
    'unlimited'
];
export const walletInactivityPresets = ['1h', '3h', '5h', '8h', '24h', '7d', 'unlimited'];
export const editableConfigFields = [
    {
        key: 'POLL_INTERVAL_SECONDS',
        label: 'Poll Interval',
        kind: 'float',
        description: 'How Many Seconds Between Wallet Polls. Applies Live On The Next Loop.',
        defaultValue: '45',
        liveApplies: true
    },
    {
        key: 'MAX_MARKET_HORIZON',
        label: 'Max Market Horizon',
        kind: 'duration',
        description: 'Longest Time To Resolution The Bot Will Allow. Edit With Left/Right To Toggle 5m, 1h, 24h, 7d, 30d, 180d, 365d, Or Unlimited.',
        defaultValue: '365d',
        liveApplies: true
    },
    {
        key: 'WALLET_INACTIVITY_LIMIT',
        label: 'Wallet Inactivity',
        kind: 'duration',
        description: 'Auto-Drop A Wallet After This Much Time Without A New Source Trade. Edit With Left/Right To Toggle 1h, 3h, 5h, 8h, 24h, 7d, Or Unlimited. Applies Live On The Next Loop.',
        defaultValue: 'unlimited',
        liveApplies: true,
        options: walletInactivityPresets
    },
    {
        key: 'WALLET_PERFORMANCE_DROP_MIN_TRADES',
        label: 'Wallet Drop Min Trades',
        kind: 'int',
        description: 'Minimum Closed Profile Trades Required Before Poor Performance Can Auto-Drop A Wallet. Set To 0 To Disable. Applies Live On The Next Loop.',
        defaultValue: '40',
        liveApplies: true
    },
    {
        key: 'WALLET_PERFORMANCE_DROP_MAX_WIN_RATE',
        label: 'Wallet Drop Max Win Rate',
        kind: 'float',
        description: 'Auto-Drop A Wallet If Its Profile Win Rate Is At Or Below This Level After The Minimum Trade Count Is Reached. Applies Live On The Next Loop.',
        defaultValue: '0.40',
        liveApplies: true
    },
    {
        key: 'WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN',
        label: 'Wallet Drop Max Avg Return',
        kind: 'float',
        description: 'Auto-Drop A Wallet If Its Profile Average Return Is At Or Below This Level After The Minimum Trade Count Is Reached. Applies Live On The Next Loop.',
        defaultValue: '-0.03',
        liveApplies: true
    },
    {
        key: 'MIN_CONFIDENCE',
        label: 'Min Confidence',
        kind: 'float',
        description: 'Minimum Confidence Needed To Accept A Copied Trade. Restart Bot To Apply.',
        defaultValue: '0.60',
        liveApplies: false
    },
    {
        key: 'MIN_BET_USD',
        label: 'Min Bet USD',
        kind: 'float',
        description: 'Lowest Order Size The Bot Will Place. Restart Bot To Apply.',
        defaultValue: '1.00',
        liveApplies: false
    },
    {
        key: 'MAX_BET_FRACTION',
        label: 'Max Bet Fraction',
        kind: 'float',
        description: 'Kelly Sizing Cap As A Fraction Of Bankroll. Restart Bot To Apply.',
        defaultValue: '0.05',
        liveApplies: false
    },
    {
        key: 'SHADOW_BANKROLL_USD',
        label: 'Shadow Bankroll',
        kind: 'float',
        description: 'Paper Bankroll Used In Shadow Mode. Restart Bot To Apply.',
        defaultValue: '1000',
        liveApplies: false
    },
    {
        key: 'USE_REAL_MONEY',
        label: 'Live Trading',
        kind: 'bool',
        description: 'Toggle Between Shadow And Live Mode. Restart Bot To Apply Safely.',
        defaultValue: 'false',
        liveApplies: false
    }
];
const durationPattern = /^(\d+(\.\d+)?)([smhdw])$/i;
function sourcePath() {
    return fs.existsSync(envPath) ? envPath : envExamplePath;
}
function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
export function readEnvValues() {
    try {
        return fs
            .readFileSync(sourcePath(), 'utf8')
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => line && !line.startsWith('#') && line.includes('='))
            .reduce((acc, line) => {
            const [key, ...rest] = line.split('=');
            acc[key.trim()] = rest.join('=').trim();
            return acc;
        }, {});
    }
    catch {
        return {};
    }
}
export function readEditableConfigValues() {
    const envValues = readEnvValues();
    return editableConfigFields.reduce((acc, field) => {
        acc[field.key] = envValues[field.key] || field.defaultValue;
        return acc;
    }, {});
}
export function writeEditableConfigValue(key, value) {
    const basePath = sourcePath();
    const lines = fs.existsSync(basePath) ? fs.readFileSync(basePath, 'utf8').split(/\r?\n/) : [];
    const pattern = new RegExp(`^${escapeRegExp(key)}\\s*=`);
    let found = false;
    const updated = lines.map((line) => {
        if (pattern.test(line.trim())) {
            found = true;
            return `${key}=${value}`;
        }
        return line;
    });
    if (!found) {
        if (updated.length && updated[updated.length - 1] !== '') {
            updated.push('');
        }
        updated.push(`${key}=${value}`);
    }
    fs.writeFileSync(envPath, updated.join('\n'));
}
export function validateEditableConfigValue(field, raw) {
    const value = raw.trim();
    if (!value) {
        return { ok: false, error: `${field.label} cannot be empty.` };
    }
    if (field.kind === 'bool') {
        const normalized = value.toLowerCase();
        if (normalized !== 'true' && normalized !== 'false') {
            return { ok: false, error: `${field.label} must be true or false.` };
        }
        return { ok: true, value: normalized };
    }
    if (field.kind === 'duration') {
        const normalized = value.toLowerCase();
        if (normalized === 'unlimited') {
            return { ok: true, value: normalized };
        }
        const match = normalized.match(durationPattern);
        if (!match) {
            return { ok: false, error: `${field.label} must look like 5m, 1h, 24h, 7d, or unlimited.` };
        }
        const numeric = Number(match[1]);
        if (!Number.isFinite(numeric) || numeric <= 0) {
            return { ok: false, error: `${field.label} must be greater than 0.` };
        }
        return { ok: true, value: `${match[1]}${match[3].toLowerCase()}` };
    }
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
        return { ok: false, error: `${field.label} must be a valid number.` };
    }
    if (field.kind === 'int' && !Number.isInteger(numeric)) {
        return { ok: false, error: `${field.label} must be a whole number.` };
    }
    if (field.key === 'POLL_INTERVAL_SECONDS' && numeric < 0.05) {
        return { ok: false, error: 'Poll interval must be at least 0.05 seconds.' };
    }
    if ((field.key === 'MIN_CONFIDENCE' || field.key === 'MAX_BET_FRACTION') && (numeric <= 0 || numeric > 1)) {
        return { ok: false, error: `${field.label} must be between 0 and 1.` };
    }
    if (field.key === 'WALLET_PERFORMANCE_DROP_MAX_WIN_RATE' && (numeric < 0 || numeric > 1)) {
        return { ok: false, error: `${field.label} must be between 0 and 1.` };
    }
    if (field.key === 'WALLET_PERFORMANCE_DROP_MIN_TRADES' && numeric < 0) {
        return { ok: false, error: `${field.label} must be 0 or greater.` };
    }
    if (field.key === 'WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN' && (numeric < -1 || numeric > 1)) {
        return { ok: false, error: `${field.label} must be between -1 and 1.` };
    }
    if ((field.key === 'MIN_BET_USD' || field.key === 'SHADOW_BANKROLL_USD') && numeric <= 0) {
        return { ok: false, error: `${field.label} must be greater than 0.` };
    }
    return { ok: true, value };
}
export function formatEditableConfigValue(field, value) {
    const normalized = value.trim();
    if (!normalized) {
        return '-';
    }
    if (field.kind === 'bool') {
        return normalized.toLowerCase() === 'true' ? 'true' : 'false';
    }
    if (field.key === 'POLL_INTERVAL_SECONDS') {
        return `${normalized}s`;
    }
    if (field.kind === 'duration') {
        return normalized.toLowerCase();
    }
    if (field.key === 'SHADOW_BANKROLL_USD' || field.key === 'MIN_BET_USD') {
        return `$${normalized}`;
    }
    return normalized;
}
export function isPresetDurationField(field) {
    return Array.isArray(field.options) && field.options.length > 0;
}
export function cycleDurationPreset(field, currentValue, direction) {
    if (!isPresetDurationField(field)) {
        return null;
    }
    const normalized = (currentValue || field.defaultValue).trim().toLowerCase();
    const values = (field.options || []).map((option) => option.toLowerCase());
    const currentIndex = values.indexOf(values.includes(normalized)
        ? normalized
        : field.defaultValue.toLowerCase());
    const step = direction === 'right' ? 1 : -1;
    const nextIndex = (currentIndex + step + values.length) % values.length;
    return values[nextIndex];
}
