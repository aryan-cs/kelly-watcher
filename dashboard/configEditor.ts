import fs from 'fs'
import {envExamplePath, envPath} from './paths.js'

export type EditableConfigKind = 'int' | 'float' | 'bool' | 'duration' | 'choice'

export const maxMarketHorizonPresets = [
  '5m',
  '1h',
  '24h',
  '7d',
  '30d',
  '180d',
  '365d',
  'unlimited'
] as const

export const retrainCadencePresets = ['daily', 'weekly'] as const
export const retrainEarlyCheckPresets = ['6h', '12h', '24h', '48h'] as const
export const walletInactivityPresets = ['1h', '3h', '5h', '8h', '24h', '7d', 'unlimited'] as const
export const walletSlowDropPresets = ['1h', '5h', '8h', '24h', '3d', '7d', '14d', '30d', 'unlimited'] as const

export interface EditableConfigField {
  key: string
  label: string
  kind: EditableConfigKind
  description: string
  defaultValue: string
  liveApplies: boolean
  options?: readonly string[]
}

export const editableConfigFields: EditableConfigField[] = [
  {
    key: 'POLL_INTERVAL_SECONDS',
    label: 'Poll Interval',
    kind: 'float',
    description: 'How many seconds between wallet polls. Applies live on the next loop.',
    defaultValue: '45',
    liveApplies: true
  },
  {
    key: 'MAX_MARKET_HORIZON',
    label: 'Max Market Horizon',
    kind: 'duration',
    description: 'Longest time to resolution the bot will allow. Edit this field to type a value or cycle 5m, 1h, 24h, 7d, 30d, 180d, 365d, or unlimited.',
    defaultValue: '365d',
    liveApplies: true,
    options: maxMarketHorizonPresets
  },
  {
    key: 'WALLET_INACTIVITY_LIMIT',
    label: 'Wallet Inactivity',
    kind: 'duration',
    description: 'Auto-drop a wallet after this much time without a new source trade, even if it never traded after tracking began. Edit this field to type a value or cycle 1h, 3h, 5h, 8h, 24h, 7d, or unlimited. Applies live on the next loop.',
    defaultValue: 'unlimited',
    liveApplies: true,
    options: walletInactivityPresets
  },
  {
    key: 'WALLET_SLOW_DROP_MAX_TRACKING_AGE',
    label: 'Slow Wallet Max Age',
    kind: 'duration',
    description: 'Auto-drop a wallet if it stays in the slow tier longer than this current tracking stint. Edit this field to type a value or cycle 1h, 5h, 8h, 24h, 3d, 7d, 14d, 30d, or unlimited. Applies live on the next loop.',
    defaultValue: 'unlimited',
    liveApplies: true,
    options: walletSlowDropPresets
  },
  {
    key: 'WALLET_PERFORMANCE_DROP_MIN_TRADES',
    label: 'Wallet Drop Min Trades',
    kind: 'int',
    description: 'Minimum closed profile trades required before poor performance can auto-drop a wallet. Set to 0 to disable. Applies live on the next loop.',
    defaultValue: '40',
    liveApplies: true
  },
  {
    key: 'WALLET_PERFORMANCE_DROP_MAX_WIN_RATE',
    label: 'Wallet Drop Max Win Rate',
    kind: 'float',
    description: 'Auto-drop a wallet if its profile win rate is at or below this level after the minimum trade count is reached. Applies live on the next loop.',
    defaultValue: '0.40',
    liveApplies: true
  },
  {
    key: 'WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN',
    label: 'Wallet Drop Max Avg Return',
    kind: 'float',
    description: 'Auto-drop a wallet if its profile average return is at or below this level after the minimum trade count is reached. Applies live on the next loop.',
    defaultValue: '-0.03',
    liveApplies: true
  },
  {
    key: 'WALLET_QUALITY_SIZE_MIN_MULTIPLIER',
    label: 'Wallet Quality Min Multiplier',
    kind: 'float',
    description: 'Lowest sizing multiplier applied to lower-quality wallets after trust gating. A score near 0 maps toward this floor. Applies live on the next loop.',
    defaultValue: '0.75',
    liveApplies: true
  },
  {
    key: 'WALLET_QUALITY_SIZE_MAX_MULTIPLIER',
    label: 'Wallet Quality Max Multiplier',
    kind: 'float',
    description: 'Highest sizing multiplier applied to stronger wallets after trust gating. A score near 1 maps toward this ceiling. Applies live on the next loop.',
    defaultValue: '1.25',
    liveApplies: true
  },
  {
    key: 'MIN_CONFIDENCE',
    label: 'Min Confidence',
    kind: 'float',
    description: 'Minimum confidence needed to accept a copied trade. Restart bot to apply.',
    defaultValue: '0.60',
    liveApplies: false
  },
  {
    key: 'MIN_BET_USD',
    label: 'Min Bet USD',
    kind: 'float',
    description: 'Lowest order size the bot will place. Restart bot to apply.',
    defaultValue: '1.00',
    liveApplies: false
  },
  {
    key: 'MAX_BET_FRACTION',
    label: 'Max Bet Fraction',
    kind: 'float',
    description: 'Kelly sizing cap as a fraction of bankroll. Restart bot to apply.',
    defaultValue: '0.05',
    liveApplies: false
  },
  {
    key: 'SHADOW_BANKROLL_USD',
    label: 'Tracker Bankroll',
    kind: 'float',
    description: 'Paper bankroll used in tracker mode. Restart bot to apply.',
    defaultValue: '1000',
    liveApplies: false
  },
  {
    key: 'MAX_DAILY_LOSS_PCT',
    label: 'Daily Loss Drawdown',
    kind: 'float',
    description: 'Blocks new entries after this intraday drawdown. Enter 0 to disable the guard or any percent from 1 through 100. Applies live on the next loop.',
    defaultValue: '5',
    liveApplies: true
  },
  {
    key: 'RETRAIN_BASE_CADENCE',
    label: 'Retrain Cadence',
    kind: 'choice',
    description: 'How often the bot attempts a scheduled full retrain. Edit this field to type a value or cycle daily and weekly. Restart bot to apply.',
    defaultValue: 'daily',
    liveApplies: false,
    options: retrainCadencePresets
  },
  {
    key: 'RETRAIN_HOUR_LOCAL',
    label: 'Retrain Hour',
    kind: 'int',
    description: 'Local hour for the scheduled retrain window, from 0 through 23. Restart bot to apply.',
    defaultValue: '3',
    liveApplies: false
  },
  {
    key: 'RETRAIN_EARLY_CHECK_INTERVAL',
    label: 'Early Check',
    kind: 'duration',
    description: 'How often the bot checks whether enough new labels exist to retrain early. Edit this field to type a value or cycle 6h, 12h, 24h, or 48h. Restart bot to apply.',
    defaultValue: '24h',
    liveApplies: false,
    options: retrainEarlyCheckPresets
  },
  {
    key: 'RETRAIN_MIN_NEW_LABELS',
    label: 'Early Label Gate',
    kind: 'int',
    description: 'Minimum new resolved trades required before an unscheduled early retrain can fire. Restart bot to apply.',
    defaultValue: '100',
    liveApplies: false
  },
  {
    key: 'RETRAIN_MIN_SAMPLES',
    label: 'Train Min Samples',
    kind: 'int',
    description: 'Minimum labeled samples required before a retrain can run. Restart bot to apply.',
    defaultValue: '200',
    liveApplies: false
  }
]

export type EditableConfigValues = Record<string, string>
const durationPattern = /^(\d+(\.\d+)?)([smhdw])$/i
const percentEditableFieldKeys = new Set(['MAX_DAILY_LOSS_PCT'])

function isPercentEditableField(field: EditableConfigField): boolean {
  return percentEditableFieldKeys.has(field.key)
}

function serializeNumericValue(value: number): string {
  return String(Number(value.toFixed(6)))
}

function normalizePercentEditableValue(raw: string): string {
  const value = raw.trim()
  return value.endsWith('%') ? value.slice(0, -1).trim() : value
}

function editableValueFromStoredValue(field: EditableConfigField, raw: string): string {
  const value = raw.trim()
  if (!value || !isPercentEditableField(field)) {
    return value
  }

  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return value
  }

  return serializeNumericValue(numeric <= 1 ? numeric * 100 : numeric)
}

function storedValueFromEditableValue(field: EditableConfigField, raw: string): string {
  const value = isPercentEditableField(field) ? normalizePercentEditableValue(raw) : raw.trim()
  if (!value || !isPercentEditableField(field)) {
    return value
  }

  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return value
  }

  return serializeNumericValue(numeric / 100)
}

function sourcePath(): string {
  return fs.existsSync(envPath) ? envPath : envExamplePath
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function readEnvValues(): Record<string, string> {
  try {
    return fs
      .readFileSync(sourcePath(), 'utf8')
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#') && line.includes('='))
      .reduce<Record<string, string>>((acc, line) => {
        const [key, ...rest] = line.split('=')
        acc[key.trim()] = rest.join('=').trim()
        return acc
      }, {})
  } catch {
    return {}
  }
}

export function readEditableConfigValues(): EditableConfigValues {
  const envValues = readEnvValues()
  return editableConfigFields.reduce<EditableConfigValues>((acc, field) => {
    const rawValue = envValues[field.key] || field.defaultValue
    acc[field.key] = editableValueFromStoredValue(field, rawValue)
    return acc
  }, {})
}

export function writeEditableConfigValue(key: string, value: string): void {
  const field = editableConfigFields.find((candidate) => candidate.key === key)
  const storedValue = field ? storedValueFromEditableValue(field, value) : value
  const basePath = sourcePath()
  const lines = fs.existsSync(basePath) ? fs.readFileSync(basePath, 'utf8').split(/\r?\n/) : []
  const pattern = new RegExp(`^${escapeRegExp(key)}\\s*=`)
  let found = false

  const updated = lines.map((line) => {
    if (pattern.test(line.trim())) {
      found = true
      return `${key}=${storedValue}`
    }
    return line
  })

  if (!found) {
    if (updated.length && updated[updated.length - 1] !== '') {
      updated.push('')
    }
    updated.push(`${key}=${storedValue}`)
  }

  fs.writeFileSync(envPath, updated.join('\n'))
}

export function validateEditableConfigValue(field: EditableConfigField, raw: string): {ok: true; value: string} | {ok: false; error: string} {
  const value = raw.trim()
  const normalizedPercentValue = isPercentEditableField(field) ? normalizePercentEditableValue(value) : value

  if (!normalizedPercentValue) {
    return {ok: false, error: `${field.label} cannot be empty.`}
  }

  if (field.kind === 'bool') {
    const normalized = value.toLowerCase()
    if (normalized !== 'true' && normalized !== 'false') {
      return {ok: false, error: `${field.label} must be true or false.`}
    }
    return {ok: true, value: normalized}
  }

  if (field.kind === 'duration') {
    const normalized = value.toLowerCase()
    if (normalized === 'unlimited') {
      return {ok: true, value: normalized}
    }
    const match = normalized.match(durationPattern)
    if (!match) {
      return {ok: false, error: `${field.label} must look like 5m, 1h, 24h, 7d, or unlimited.`}
    }

    const numeric = Number(match[1])
    if (!Number.isFinite(numeric) || numeric <= 0) {
      return {ok: false, error: `${field.label} must be greater than 0.`}
    }

    return {ok: true, value: `${match[1]}${match[3].toLowerCase()}`}
  }

  if (field.kind === 'choice') {
    const normalized = value.toLowerCase()
    const options = (field.options || []).map((option) => option.toLowerCase())
    if (!options.length || !options.includes(normalized)) {
      return {ok: false, error: `${field.label} must be one of: ${(field.options || []).join(', ')}.`}
    }
    return {ok: true, value: normalized}
  }

  const numeric = Number(normalizedPercentValue)
  if (!Number.isFinite(numeric)) {
    return {ok: false, error: `${field.label} must be a valid number.`}
  }

  if (field.kind === 'int' && !Number.isInteger(numeric)) {
    return {ok: false, error: `${field.label} must be a whole number.`}
  }

  if (field.key === 'POLL_INTERVAL_SECONDS' && numeric < 0.05) {
    return {ok: false, error: 'Poll interval must be at least 0.05 seconds.'}
  }

  if ((field.key === 'MIN_CONFIDENCE' || field.key === 'MAX_BET_FRACTION') && (numeric <= 0 || numeric > 1)) {
    return {ok: false, error: `${field.label} must be between 0 and 1.`}
  }

  if (field.key === 'WALLET_PERFORMANCE_DROP_MAX_WIN_RATE' && (numeric < 0 || numeric > 1)) {
    return {ok: false, error: `${field.label} must be between 0 and 1.`}
  }

  if (field.key === 'WALLET_PERFORMANCE_DROP_MIN_TRADES' && numeric < 0) {
    return {ok: false, error: `${field.label} must be 0 or greater.`}
  }

  if (field.key === 'WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN' && (numeric < -1 || numeric > 1)) {
    return {ok: false, error: `${field.label} must be between -1 and 1.`}
  }

  if (field.key === 'MAX_DAILY_LOSS_PCT' && (numeric < 0 || numeric > 100)) {
    return {ok: false, error: `${field.label} must be between 0 and 100.`}
  }

  if ((field.key === 'MIN_BET_USD' || field.key === 'SHADOW_BANKROLL_USD') && numeric <= 0) {
    return {ok: false, error: `${field.label} must be greater than 0.`}
  }

  if (field.key === 'RETRAIN_HOUR_LOCAL' && (numeric < 0 || numeric > 23)) {
    return {ok: false, error: 'Retrain hour must be between 0 and 23.'}
  }

  if (field.key === 'RETRAIN_MIN_NEW_LABELS' && numeric < 1) {
    return {ok: false, error: 'Early label gate must be at least 1.'}
  }

  if (field.key === 'RETRAIN_MIN_SAMPLES' && numeric < 1) {
    return {ok: false, error: 'Train min samples must be at least 1.'}
  }

  return {ok: true, value: normalizedPercentValue}
}

export function formatEditableConfigValue(field: EditableConfigField, value: string): string {
  const normalized = value.trim()
  if (!normalized) {
    return '-'
  }

  if (field.kind === 'bool') {
    return normalized.toLowerCase() === 'true' ? 'true' : 'false'
  }

  if (field.key === 'POLL_INTERVAL_SECONDS') {
    return `${normalized}s`
  }

  if (field.kind === 'duration') {
    return normalized.toLowerCase()
  }

  if (field.key === 'RETRAIN_BASE_CADENCE') {
    return normalized.toLowerCase()
  }

  if (field.key === 'RETRAIN_HOUR_LOCAL') {
    const hour = Math.min(Math.max(Number.parseInt(normalized, 10) || 0, 0), 23)
    return `${String(hour).padStart(2, '0')}:00 local`
  }

  if (field.key === 'RETRAIN_EARLY_CHECK_INTERVAL') {
    return normalized.toLowerCase()
  }

  if (field.key === 'RETRAIN_MIN_NEW_LABELS') {
    return `${normalized} labels`
  }

  if (field.key === 'RETRAIN_MIN_SAMPLES') {
    return `${normalized} samples`
  }

  if (field.key === 'SHADOW_BANKROLL_USD' || field.key === 'MIN_BET_USD') {
    return `$${normalized}`
  }

  if (isPercentEditableField(field)) {
    return `${normalized}%`
  }

  return normalized
}

export function hasCyclableOptions(field: EditableConfigField): boolean {
  return Array.isArray(field.options) && field.options.length > 0
}

export function cycleFieldOption(
  field: EditableConfigField,
  currentValue: string,
  direction: 'previous' | 'next'
): string | null {
  if (!hasCyclableOptions(field)) {
    return null
  }

  const values = (field.options || []).map((option) => option.toLowerCase())
  const normalized = (currentValue || field.defaultValue).trim().toLowerCase()
  const fallback = field.defaultValue.toLowerCase()
  const currentIndex = values.indexOf(values.includes(normalized) ? normalized : fallback)
  const step = direction === 'next' ? 1 : -1
  const nextIndex = (currentIndex + step + values.length) % values.length
  return values[nextIndex]
}

export function isPresetDurationField(field: EditableConfigField): boolean {
  return hasCyclableOptions(field)
}

export function cycleDurationPreset(
  field: EditableConfigField,
  currentValue: string,
  direction: 'previous' | 'next'
): string | null {
  return cycleFieldOption(field, currentValue, direction)
}
