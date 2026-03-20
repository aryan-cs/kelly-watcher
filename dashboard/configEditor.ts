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
export const walletSlowDropPresets = ['24h', '3d', '7d', '14d', '30d', 'unlimited'] as const

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
    liveApplies: true,
    options: maxMarketHorizonPresets
  },
  {
    key: 'WALLET_INACTIVITY_LIMIT',
    label: 'Wallet Inactivity',
    kind: 'duration',
    description: 'Auto-Drop A Wallet After This Much Time Without A New Source Trade, Even If It Never Traded After Tracking Began. Edit With Left/Right To Toggle 1h, 3h, 5h, 8h, 24h, 7d, Or Unlimited. Applies Live On The Next Loop.',
    defaultValue: 'unlimited',
    liveApplies: true,
    options: walletInactivityPresets
  },
  {
    key: 'WALLET_SLOW_DROP_MAX_TRACKING_AGE',
    label: 'Slow Wallet Max Age',
    kind: 'duration',
    description: 'Auto-Drop A Wallet If It Stays In The Slow Tier Longer Than This Current Tracking Stint. Edit With Left/Right To Toggle 24h, 3d, 7d, 14d, 30d, Or Unlimited. Applies Live On The Next Loop.',
    defaultValue: 'unlimited',
    liveApplies: true,
    options: walletSlowDropPresets
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
    key: 'WALLET_QUALITY_SIZE_MIN_MULTIPLIER',
    label: 'Wallet Quality Min Multiplier',
    kind: 'float',
    description: 'Lowest Sizing Multiplier Applied To Lower-Quality Wallets After Trust Gating. A Score Near 0 Maps Toward This Floor. Applies Live On The Next Loop.',
    defaultValue: '0.75',
    liveApplies: true
  },
  {
    key: 'WALLET_QUALITY_SIZE_MAX_MULTIPLIER',
    label: 'Wallet Quality Max Multiplier',
    kind: 'float',
    description: 'Highest Sizing Multiplier Applied To Stronger Wallets After Trust Gating. A Score Near 1 Maps Toward This Ceiling. Applies Live On The Next Loop.',
    defaultValue: '1.25',
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
    label: 'Tracker Bankroll',
    kind: 'float',
    description: 'Paper Bankroll Used In Tracker Mode. Restart Bot To Apply.',
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
    key: 'USE_REAL_MONEY',
    label: 'Live Trading',
    kind: 'bool',
    description: 'Toggle Between Tracker And Live Mode. Restart Bot To Apply Safely.',
    defaultValue: 'false',
    liveApplies: false
  },
  {
    key: 'RETRAIN_BASE_CADENCE',
    label: 'Retrain Cadence',
    kind: 'choice',
    description: 'How Often The Bot Attempts A Scheduled Full Retrain. Use Left/Right To Toggle Daily Or Weekly. Restart Bot To Apply.',
    defaultValue: 'daily',
    liveApplies: false,
    options: retrainCadencePresets
  },
  {
    key: 'RETRAIN_HOUR_LOCAL',
    label: 'Retrain Hour',
    kind: 'int',
    description: 'Local Hour For The Scheduled Retrain Window, From 0 Through 23. Restart Bot To Apply.',
    defaultValue: '3',
    liveApplies: false
  },
  {
    key: 'RETRAIN_EARLY_CHECK_INTERVAL',
    label: 'Early Check',
    kind: 'duration',
    description: 'How Often The Bot Checks Whether Enough New Labels Exist To Retrain Early. Use Left/Right To Toggle 6h, 12h, 24h, Or 48h. Restart Bot To Apply.',
    defaultValue: '24h',
    liveApplies: false,
    options: retrainEarlyCheckPresets
  },
  {
    key: 'RETRAIN_MIN_NEW_LABELS',
    label: 'Early Label Gate',
    kind: 'int',
    description: 'Minimum New Resolved Trades Required Before An Unscheduled Early Retrain Can Fire. Restart Bot To Apply.',
    defaultValue: '100',
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
  direction: 'left' | 'right'
): string | null {
  if (!hasCyclableOptions(field)) {
    return null
  }

  const values = (field.options || []).map((option) => option.toLowerCase())
  const normalized = (currentValue || field.defaultValue).trim().toLowerCase()
  const fallback = field.defaultValue.toLowerCase()
  const currentIndex = values.indexOf(values.includes(normalized) ? normalized : fallback)
  const step = direction === 'right' ? 1 : -1
  const nextIndex = (currentIndex + step + values.length) % values.length
  return values[nextIndex]
}

export function isPresetDurationField(field: EditableConfigField): boolean {
  return hasCyclableOptions(field)
}

export function cycleDurationPreset(
  field: EditableConfigField,
  currentValue: string,
  direction: 'left' | 'right'
): string | null {
  return cycleFieldOption(field, currentValue, direction)
}
