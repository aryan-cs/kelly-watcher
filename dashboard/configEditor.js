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
export const editableConfigFields = [
    {
        key: 'POLL_INTERVAL_SECONDS',
        label: 'Poll interval',
        kind: 'float',
        description: 'How many seconds between wallet polls. Applies live on the next loop.',
        defaultValue: '45',
        liveApplies: true
    },
    {
        key: 'MAX_MARKET_HORIZON',
        label: 'Max market horizon',
        kind: 'duration',
        description: 'Longest time to resolution the bot will allow. Edit with left/right to toggle 5m, 1h, 24h, 7d, 30d, 180d, 365d, or unlimited.',
        defaultValue: '365d',
        liveApplies: true
    },
    {
        key: 'MIN_CONFIDENCE',
        label: 'Min confidence',
        kind: 'float',
        description: 'Minimum confidence needed to accept a copied trade. Restart bot to apply.',
        defaultValue: '0.60',
        liveApplies: false
    },
    {
        key: 'MIN_BET_USD',
        label: 'Min bet USD',
        kind: 'float',
        description: 'Lowest order size the bot will place. Restart bot to apply.',
        defaultValue: '1.00',
        liveApplies: false
    },
    {
        key: 'MAX_BET_FRACTION',
        label: 'Max bet fraction',
        kind: 'float',
        description: 'Kelly sizing cap as a fraction of bankroll. Restart bot to apply.',
        defaultValue: '0.05',
        liveApplies: false
    },
    {
        key: 'SHADOW_BANKROLL_USD',
        label: 'Shadow bankroll',
        kind: 'float',
        description: 'Paper bankroll used in shadow mode. Restart bot to apply.',
        defaultValue: '1000',
        liveApplies: false
    },
    {
        key: 'USE_REAL_MONEY',
        label: 'Live trading',
        kind: 'bool',
        description: 'Toggle between shadow and live mode. Restart bot to apply safely.',
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
    if (field.key === 'MAX_MARKET_HORIZON') {
        return normalized.toLowerCase();
    }
    if (field.key === 'SHADOW_BANKROLL_USD' || field.key === 'MIN_BET_USD') {
        return `$${normalized}`;
    }
    return normalized;
}
export function isPresetDurationField(field) {
    return field.key === 'MAX_MARKET_HORIZON';
}
export function cycleDurationPreset(field, currentValue, direction) {
    if (!isPresetDurationField(field)) {
        return null;
    }
    const normalized = (currentValue || field.defaultValue).trim().toLowerCase();
    const values = [...maxMarketHorizonPresets];
    const currentIndex = values.indexOf(values.includes(normalized)
        ? normalized
        : field.defaultValue.toLowerCase());
    const step = direction === 'right' ? 1 : -1;
    const nextIndex = (currentIndex + step + values.length) % values.length;
    return values[nextIndex];
}
