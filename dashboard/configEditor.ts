import {useEffect, useState} from 'react'
import {fetchApiJson, postApiJson} from './api.js'
import {useRefreshToken} from './refresh.js'

export type EditableConfigKind = 'int' | 'float' | 'bool' | 'duration' | 'choice' | 'text'

export const maxMarketHorizonPresets = [
  '5m',
  '1h',
  '24h',
  '3d',
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
export const logLevelPresets = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const

export interface EditableConfigField {
  key: string
  label: string
  kind: EditableConfigKind
  description: string
  defaultValue: string
  liveApplies: boolean
  options?: readonly string[]
  allowUnlimited?: boolean
}

export const editableConfigFields: EditableConfigField[] = [
  {
    key: 'POLL_INTERVAL_SECONDS',
    label: 'Poll Interval',
    kind: 'float',
    description: 'How many seconds between wallet polls. Applies live on the next loop.',
    defaultValue: '1',
    liveApplies: true
  },
  {
    key: 'HOT_WALLET_COUNT',
    label: 'Hot Wallet Count',
    kind: 'int',
    description: 'How many top-priority wallets stay in the fastest polling tier. Applies live on the next loop.',
    defaultValue: '12',
    liveApplies: true
  },
  {
    key: 'WARM_WALLET_COUNT',
    label: 'Warm Wallet Count',
    kind: 'int',
    description: 'How many additional wallets stay in the warm polling tier after the hot set. Applies live on the next loop.',
    defaultValue: '24',
    liveApplies: true
  },
  {
    key: 'WARM_POLL_INTERVAL_MULTIPLIER',
    label: 'Warm Poll Mult',
    kind: 'int',
    description: 'How much slower warm-wallet polling runs than the hot tier. Applies live on the next loop.',
    defaultValue: '5',
    liveApplies: true
  },
  {
    key: 'DISCOVERY_POLL_INTERVAL_MULTIPLIER',
    label: 'Disc Poll Mult',
    kind: 'int',
    description: 'How much slower discovery-wallet polling runs than the hot tier. Applies live on the next loop.',
    defaultValue: '20',
    liveApplies: true
  },
  {
    key: 'MAX_MARKET_HORIZON',
    label: 'Max Market Horizon',
    kind: 'duration',
    description: 'Longest time to resolution the bot will allow. Edit this field to type a value or cycle 5m, 1h, 24h, 3d, 7d, 30d, 180d, 365d, or unlimited.',
    defaultValue: '3d',
    liveApplies: true,
    options: maxMarketHorizonPresets,
    allowUnlimited: true
  },
  {
    key: 'MAX_SOURCE_TRADE_AGE',
    label: 'Source Trade Age',
    kind: 'duration',
    description: 'Oldest source trade age the tracker will still consider copyable. Applies live on the next loop.',
    defaultValue: '90s',
    liveApplies: true
  },
  {
    key: 'MAX_FEED_STALENESS',
    label: 'Feed Staleness',
    kind: 'duration',
    description: 'Maximum age of fetched market data before it is treated as stale. Applies live on the next loop.',
    defaultValue: '90s',
    liveApplies: true
  },
  {
    key: 'MAX_ORDERBOOK_STALENESS',
    label: 'Book Staleness',
    kind: 'duration',
    description: 'Maximum age of order-book data before the market snapshot is rejected. Applies live on the next loop.',
    defaultValue: '3s',
    liveApplies: true
  },
  {
    key: 'MIN_EXECUTION_WINDOW',
    label: 'Exec Window Min',
    kind: 'duration',
    description: 'Minimum time remaining to resolution before a trade may still be opened. Applies live on the next loop.',
    defaultValue: '45s',
    liveApplies: true
  },
  {
    key: 'WALLET_INACTIVITY_LIMIT',
    label: 'Wallet Inactivity',
    kind: 'duration',
    description: 'Auto-drop a wallet after this much time without a new source trade, even if it never traded after tracking began. Edit this field to type a value or cycle 1h, 3h, 5h, 8h, 24h, 7d, or unlimited. Applies live on the next loop.',
    defaultValue: 'unlimited',
    liveApplies: true,
    options: walletInactivityPresets,
    allowUnlimited: true
  },
  {
    key: 'WALLET_SLOW_DROP_MAX_TRACKING_AGE',
    label: 'Slow Wallet Max Age',
    kind: 'duration',
    description: 'Auto-drop a wallet if it stays in the slow tier longer than this current tracking stint. Edit this field to type a value or cycle 1h, 5h, 8h, 24h, 3d, 7d, 14d, 30d, or unlimited. Applies live on the next loop.',
    defaultValue: 'unlimited',
    liveApplies: true,
    options: walletSlowDropPresets,
    allowUnlimited: true
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
    key: 'WALLET_UNCOPYABLE_PENALTY_MIN_BUYS',
    label: 'Uncopy Penalty Min Buys',
    kind: 'int',
    description: 'Minimum observed buys before repeated uncopyable behavior starts reducing wallet quality. Applies live on the next loop.',
    defaultValue: '12',
    liveApplies: true
  },
  {
    key: 'WALLET_UNCOPYABLE_PENALTY_WEIGHT',
    label: 'Uncopy Penalty Weight',
    kind: 'float',
    description: 'How strongly repeated uncopyable behavior penalizes wallet quality. Applies live on the next loop.',
    defaultValue: '0.25',
    liveApplies: true
  },
  {
    key: 'WALLET_UNCOPYABLE_DROP_MIN_BUYS',
    label: 'Uncopy Drop Min Buys',
    kind: 'int',
    description: 'Minimum observed buys before an uncopyable wallet can be fully dropped. Applies live on the next loop.',
    defaultValue: '24',
    liveApplies: true
  },
  {
    key: 'WALLET_UNCOPYABLE_DROP_MAX_SKIP_RATE',
    label: 'Uncopy Drop Max Skip',
    kind: 'float',
    description: 'Maximum tolerated skip rate before an uncopyable wallet is dropped. Applies live on the next loop.',
    defaultValue: '0.75',
    liveApplies: true
  },
  {
    key: 'WALLET_UNCOPYABLE_DROP_MAX_RESOLVED_COPIED',
    label: 'Uncopy Drop Max Copied',
    kind: 'int',
    description: 'Keeps a low-sample wallet from being dropped too early for uncopyable behavior. Applies live on the next loop.',
    defaultValue: '3',
    liveApplies: true
  },
  {
    key: 'WALLET_COLD_START_MIN_OBSERVED_BUYS',
    label: 'Cold Start Min Buys',
    kind: 'int',
    description: 'Minimum observed buys before a new wallet leaves cold-start handling. Applies live on the next loop.',
    defaultValue: '3',
    liveApplies: true
  },
  {
    key: 'WALLET_DISCOVERY_MIN_OBSERVED_BUYS',
    label: 'Disc Min Buys',
    kind: 'int',
    description: 'Minimum observed buys before a wallet qualifies for discovery scoring. Applies live on the next loop.',
    defaultValue: '8',
    liveApplies: true
  },
  {
    key: 'WALLET_DISCOVERY_MIN_RESOLVED_BUYS',
    label: 'Disc Min Resolved',
    kind: 'int',
    description: 'Minimum resolved buys before a discovery wallet is judged on realized outcomes. Applies live on the next loop.',
    defaultValue: '3',
    liveApplies: true
  },
  {
    key: 'WALLET_DISCOVERY_SIZE_MULTIPLIER',
    label: 'Disc Size Mult',
    kind: 'float',
    description: 'Sizing multiplier applied while a wallet is still in discovery. Applies live on the next loop.',
    defaultValue: '0.20',
    liveApplies: true
  },
  {
    key: 'WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS',
    label: 'Trusted Min Copied',
    kind: 'int',
    description: 'Minimum resolved copied buys before a wallet can qualify as trusted. Applies live on the next loop.',
    defaultValue: '15',
    liveApplies: true
  },
  {
    key: 'WALLET_PROBATION_SIZE_MULTIPLIER',
    label: 'Probation Mult',
    kind: 'float',
    description: 'Sizing multiplier used when a wallet falls into probation. Applies live on the next loop.',
    defaultValue: '0.50',
    liveApplies: true
  },
  {
    key: 'WALLET_LOCAL_PERFORMANCE_PENALTY_MIN_RESOLVED_COPIED_BUYS',
    label: 'Local Penalty Min',
    kind: 'int',
    description: 'Minimum resolved copied buys before local underperformance reduces sizing. Applies live on the next loop.',
    defaultValue: '15',
    liveApplies: true
  },
  {
    key: 'WALLET_LOCAL_PERFORMANCE_PENALTY_MAX_AVG_RETURN',
    label: 'Local Penalty Return',
    kind: 'float',
    description: 'Average copied return threshold that triggers the local performance sizing penalty. Applies live on the next loop.',
    defaultValue: '-0.10',
    liveApplies: true
  },
  {
    key: 'WALLET_LOCAL_PERFORMANCE_PENALTY_SIZE_MULTIPLIER',
    label: 'Local Penalty Mult',
    kind: 'float',
    description: 'Sizing multiplier applied after a wallet triggers the local performance penalty. Applies live on the next loop.',
    defaultValue: '0.25',
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
    defaultValue: '0.55',
    liveApplies: false
  },
  {
    key: 'HEURISTIC_MIN_ENTRY_PRICE',
    label: 'Heuristic Entry Min',
    kind: 'float',
    description: 'Lower bound of the heuristic entry-price band. The heuristic path only buys when the entry price lands within the configured band. Applies live on the next loop.',
    defaultValue: '0.45',
    liveApplies: true
  },
  {
    key: 'HEURISTIC_MAX_ENTRY_PRICE',
    label: 'Heuristic Entry Max',
    kind: 'float',
    description: 'Upper bound of the heuristic entry-price band. Prices at or above this level are rejected on the heuristic path. Applies live on the next loop.',
    defaultValue: '0.50',
    liveApplies: true
  },
  {
    key: 'MODEL_EDGE_MID_CONFIDENCE',
    label: 'Edge Mid Conf',
    kind: 'float',
    description: 'Confidence level where the model edge threshold relaxes from the model default to the mid-confidence override. Restart bot to apply.',
    defaultValue: '0.55',
    liveApplies: false
  },
  {
    key: 'MODEL_EDGE_HIGH_CONFIDENCE',
    label: 'Edge High Conf',
    kind: 'float',
    description: 'Confidence level where the model edge threshold relaxes to the high-confidence override. Restart bot to apply.',
    defaultValue: '0.65',
    liveApplies: false
  },
  {
    key: 'MODEL_EDGE_MID_THRESHOLD',
    label: 'Edge Mid Threshold',
    kind: 'float',
    description: 'Required model edge once confidence reaches the mid-confidence cutoff. Restart bot to apply.',
    defaultValue: '0.0125',
    liveApplies: false
  },
  {
    key: 'MODEL_EDGE_HIGH_THRESHOLD',
    label: 'Edge High Threshold',
    kind: 'float',
    description: 'Required model edge once confidence reaches the high-confidence cutoff. Restart bot to apply.',
    defaultValue: '0.0',
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
    key: 'ENTRY_FIXED_COST_USD',
    label: 'Entry Fee USD',
    kind: 'float',
    description: 'Fixed execution-cost estimate charged when sizing a new entry. Applies live on the next loop.',
    defaultValue: '0.00',
    liveApplies: true
  },
  {
    key: 'EXIT_FIXED_COST_USD',
    label: 'Exit Fee USD',
    kind: 'float',
    description: 'Fixed execution-cost estimate charged when sizing an exit. Applies live on the next loop.',
    defaultValue: '0.00',
    liveApplies: true
  },
  {
    key: 'APPROVAL_FIXED_COST_USD',
    label: 'Approval Fee USD',
    kind: 'float',
    description: 'Fixed approval-cost estimate used in the trading cost model. Applies live on the next loop.',
    defaultValue: '0.00',
    liveApplies: true
  },
  {
    key: 'SETTLEMENT_FIXED_COST_USD',
    label: 'Settle Fee USD',
    kind: 'float',
    description: 'Fixed settlement-cost estimate used in the trading cost model. Applies live on the next loop.',
    defaultValue: '0.00',
    liveApplies: true
  },
  {
    key: 'EXPECTED_CLOSE_FIXED_COST_USD',
    label: 'Exp Close Fee',
    kind: 'float',
    description: 'Override for the expected close cost used in sizing. Leave blank for auto. Applies live on the next loop.',
    defaultValue: '',
    liveApplies: true
  },
  {
    key: 'INCLUDE_EXPECTED_EXIT_FEE_IN_SIZING',
    label: 'Size With Exit Fee',
    kind: 'bool',
    description: 'Includes expected close cost when sizing entries. Applies live on the next loop.',
    defaultValue: 'true',
    liveApplies: true
  },
  {
    key: 'MAX_BET_FRACTION',
    label: 'Max Bet Fraction',
    kind: 'float',
    description: 'Kelly sizing cap as a fraction of bankroll. Restart bot to apply.',
    defaultValue: '0.04',
    liveApplies: false
  },
  {
    key: 'MAX_MARKET_EXPOSURE_FRACTION',
    label: 'Market Exposure Cap',
    kind: 'float',
    description: 'Caps deployed capital per market as a percent of bankroll. Enter 0 to disable or any percent from 1 through 100. Applies live on the next loop.',
    defaultValue: '20',
    liveApplies: true
  },
  {
    key: 'MAX_TRADER_EXPOSURE_FRACTION',
    label: 'Trader Exposure Cap',
    kind: 'float',
    description: 'Caps deployed capital per copied wallet as a percent of bankroll. Enter 0 to disable or any percent from 1 through 100. Applies live on the next loop.',
    defaultValue: '30',
    liveApplies: true
  },
  {
    key: 'MAX_TOTAL_OPEN_EXPOSURE_FRACTION',
    label: 'Open Exposure Cap',
    kind: 'float',
    description: 'Caps total deployed capital across all open positions as a percent of bankroll. Enter 0 to disable or any percent from 1 through 100. Applies live on the next loop.',
    defaultValue: '25',
    liveApplies: true
  },
  {
    key: 'EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION',
    label: 'Trusted Exposure Cap',
    kind: 'float',
    description: 'Higher total-open-exposure cap used only for wallets that qualify for the exposure override. Enter 0 to disable or any percent from 1 through 100. Applies live on the next loop.',
    defaultValue: '30',
    liveApplies: true
  },
  {
    key: 'DUPLICATE_SIDE_OVERRIDE_MIN_SKIPS',
    label: 'Dup Override Min Skips',
    kind: 'int',
    description: 'Minimum historical duplicate-side skips required before a wallet can qualify for duplicate-position adds. Applies live on the next loop.',
    defaultValue: '20',
    liveApplies: true
  },
  {
    key: 'DUPLICATE_SIDE_OVERRIDE_MIN_AVG_RETURN',
    label: 'Dup Override Min Return',
    kind: 'float',
    description: 'Minimum average counterfactual return required for duplicate-side override qualification. Applies live on the next loop.',
    defaultValue: '0.05',
    liveApplies: true
  },
  {
    key: 'EXPOSURE_OVERRIDE_MIN_SKIPS',
    label: 'Exp Override Min Skips',
    kind: 'int',
    description: 'Minimum historical exposure-cap skips required before a wallet can qualify for the higher trusted exposure cap. Applies live on the next loop.',
    defaultValue: '20',
    liveApplies: true
  },
  {
    key: 'EXPOSURE_OVERRIDE_MIN_AVG_RETURN',
    label: 'Exp Override Min Return',
    kind: 'float',
    description: 'Minimum average counterfactual return required for trusted exposure-cap qualification. Applies live on the next loop.',
    defaultValue: '0.03',
    liveApplies: true
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
    key: 'STOP_LOSS_ENABLED',
    label: 'Stop Loss Enabled',
    kind: 'bool',
    description: 'Turns the open-position stop-loss scanner on or off. Applies live on the next loop.',
    defaultValue: 'true',
    liveApplies: true
  },
  {
    key: 'STOP_LOSS_MAX_LOSS_PCT',
    label: 'Stop Loss Max Loss',
    kind: 'float',
    description: 'Stop-loss trigger level as a percent loss from entry. Enter 0 to disable effective exits or any percent from 1 through 100. Applies live on the next loop.',
    defaultValue: '15',
    liveApplies: true
  },
  {
    key: 'STOP_LOSS_MIN_HOLD',
    label: 'Stop Loss Min Hold',
    kind: 'duration',
    description: 'Minimum hold time before the stop-loss scanner may exit a position. Enter 0s to allow immediate exits. Applies live on the next loop.',
    defaultValue: '20m',
    liveApplies: true
  },
  {
    key: 'MAX_LIVE_DRAWDOWN_PCT',
    label: 'Live DD Limit',
    kind: 'float',
    description: 'Account-level live-trading drawdown stop, as a percent from session starting equity. Restart bot to apply.',
    defaultValue: '15',
    liveApplies: false
  },
  {
    key: 'MAX_LIVE_HEALTH_FAILURES',
    label: 'Live Health Fails',
    kind: 'int',
    description: 'Maximum consecutive live health-check failures allowed before trading is halted. Restart bot to apply.',
    defaultValue: '3',
    liveApplies: false
  },
  {
    key: 'LIVE_REQUIRE_SHADOW_HISTORY',
    label: 'Need Shadow Hist',
    kind: 'bool',
    description: 'Requires enough resolved shadow history before live mode is allowed. Restart bot to apply.',
    defaultValue: 'true',
    liveApplies: false
  },
  {
    key: 'LIVE_MIN_SHADOW_RESOLVED',
    label: 'Min Shadow Res',
    kind: 'int',
    description: 'Minimum resolved shadow trades required before live mode may start when the history gate is enabled. Restart bot to apply.',
    defaultValue: '100',
    liveApplies: false
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
  },
  {
    key: 'LOG_LEVEL',
    label: 'Log Level',
    kind: 'choice',
    description: 'Python logging level for the bot process. Restart bot to apply.',
    defaultValue: 'INFO',
    liveApplies: false,
    options: logLevelPresets
  },
  {
    key: 'MODEL_PATH',
    label: 'Model Path',
    kind: 'text',
    description: 'Relative path to the deployed model artifact. Restart bot to apply.',
    defaultValue: 'save/model.joblib',
    liveApplies: false
  }
]

export type EditableConfigValues = Record<string, string>
export interface DashboardConfigRow {
  key: string
  value: string
}

export interface DashboardConfigData {
  safeValues: Record<string, string>
  watchedWallets: string[]
  rows: DashboardConfigRow[]
  editableValues: EditableConfigValues
}

interface DashboardConfigResponse {
  safe_values?: Record<string, string>
  watched_wallets?: string[]
  rows?: DashboardConfigRow[]
}

const durationPattern = /^(\d+(\.\d+)?)([smhdw])$/i
const percentEditableFieldKeys = new Set([
  'MAX_DAILY_LOSS_PCT',
  'MAX_LIVE_DRAWDOWN_PCT',
  'STOP_LOSS_MAX_LOSS_PCT',
  'MAX_MARKET_EXPOSURE_FRACTION',
  'MAX_TRADER_EXPOSURE_FRACTION',
  'MAX_TOTAL_OPEN_EXPOSURE_FRACTION',
  'EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION'
])
let dashboardConfigCache: DashboardConfigData = {
  safeValues: {},
  watchedWallets: [],
  rows: [],
  editableValues: editableConfigFields.reduce<EditableConfigValues>((acc, field) => {
    acc[field.key] = field.defaultValue
    return acc
  }, {})
}

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

function normalizeDashboardConfig(payload: DashboardConfigResponse = {}): DashboardConfigData {
  const safeValues = {...(payload.safe_values || {})}
  const watchedWallets = Array.isArray(payload.watched_wallets)
    ? payload.watched_wallets
      .map((wallet) => String(wallet || '').trim().toLowerCase())
      .filter(Boolean)
    : []
  const rows = Array.isArray(payload.rows)
    ? payload.rows
      .map((row) => ({key: String(row.key || '').trim(), value: String(row.value || '')}))
      .filter((row) => row.key)
    : []

  const editableValues = editableConfigFields.reduce<EditableConfigValues>((acc, field) => {
    const rawValue = safeValues[field.key] || field.defaultValue
    acc[field.key] = editableValueFromStoredValue(field, rawValue)
    return acc
  }, {})

  return {safeValues, watchedWallets, rows, editableValues}
}

function updateDashboardConfigCache(payload: DashboardConfigResponse = {}): DashboardConfigData {
  dashboardConfigCache = normalizeDashboardConfig(payload)
  return dashboardConfigCache
}

export async function refreshDashboardConfig(): Promise<DashboardConfigData> {
  const payload = await fetchApiJson<DashboardConfigResponse>('/api/config')
  return updateDashboardConfigCache(payload)
}

export function useDashboardConfig(intervalMs = 1000): DashboardConfigData {
  const [config, setConfig] = useState<DashboardConfigData>(() => dashboardConfigCache)
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const schedule = () => {
      if (cancelled) {
        return
      }
      timer = setTimeout(() => {
        void read()
      }, Math.max(intervalMs, 250))
    }

    const read = async () => {
      try {
        const nextConfig = await refreshDashboardConfig()
        if (!cancelled) {
          setConfig(nextConfig)
        }
      } catch {
        if (!cancelled) {
          setConfig(dashboardConfigCache)
        }
      } finally {
        schedule()
      }
    }

    void read()

    return () => {
      cancelled = true
      if (timer) {
        clearTimeout(timer)
      }
    }
  }, [intervalMs, refreshToken])

  return config
}

export function readEnvValues(): Record<string, string> {
  return {...dashboardConfigCache.safeValues}
}

export function readEditableConfigValues(): EditableConfigValues {
  return {...dashboardConfigCache.editableValues}
}

export async function writeEditableConfigValue(key: string, value: string): Promise<DashboardConfigData> {
  const field = editableConfigFields.find((candidate) => candidate.key === key)
  const storedValue = field ? storedValueFromEditableValue(field, value) : value
  const payload = await postApiJson<DashboardConfigResponse>('/api/config/value', {key, value: storedValue})
  return updateDashboardConfigCache(payload)
}

export function validateEditableConfigValue(field: EditableConfigField, raw: string): {ok: true; value: string} | {ok: false; error: string} {
  const value = raw.trim()
  const normalizedPercentValue = isPercentEditableField(field) ? normalizePercentEditableValue(value) : value

  if (!normalizedPercentValue) {
    if (field.key === 'EXPECTED_CLOSE_FIXED_COST_USD') {
      return {ok: true, value: ''}
    }
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
    if (normalized === 'unlimited' && field.allowUnlimited) {
      return {ok: true, value: normalized}
    }
    const match = normalized.match(durationPattern)
    if (!match) {
      return {
        ok: false,
        error:
          field.key === 'STOP_LOSS_MIN_HOLD'
            ? `${field.label} must look like 0s, 5m, 1h, 24h, or 7d.`
            : field.allowUnlimited
              ? `${field.label} must look like 5m, 1h, 24h, 7d, or unlimited.`
              : `${field.label} must look like 5s, 45s, 5m, 1h, 24h, or 7d.`
      }
    }

    const numeric = Number(match[1])
    if (!Number.isFinite(numeric)) {
      return {ok: false, error: `${field.label} must be a valid duration.`}
    }
    if (field.key === 'STOP_LOSS_MIN_HOLD') {
      if (numeric < 0) {
        return {ok: false, error: `${field.label} must be 0 or greater.`}
      }
    } else if (numeric <= 0) {
      return {ok: false, error: `${field.label} must be greater than 0.`}
    }

    return {ok: true, value: `${match[1]}${match[3].toLowerCase()}`}
  }

  if (field.kind === 'choice') {
    const normalized = value.toLowerCase()
    const optionLookup = new Map((field.options || []).map((option) => [option.toLowerCase(), option]))
    if (!optionLookup.size || !optionLookup.has(normalized)) {
      return {ok: false, error: `${field.label} must be one of: ${(field.options || []).join(', ')}.`}
    }
    return {ok: true, value: optionLookup.get(normalized) || value}
  }

  if (field.kind === 'text') {
    return {ok: true, value}
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

  const isCostField =
    field.key === 'ENTRY_FIXED_COST_USD' ||
    field.key === 'EXIT_FIXED_COST_USD' ||
    field.key === 'APPROVAL_FIXED_COST_USD' ||
    field.key === 'SETTLEMENT_FIXED_COST_USD' ||
    field.key === 'EXPECTED_CLOSE_FIXED_COST_USD'

  if (
    (
      field.key === 'MIN_CONFIDENCE' ||
      isCostField ||
      field.key === 'MAX_BET_FRACTION' ||
      field.key === 'WALLET_UNCOPYABLE_PENALTY_WEIGHT' ||
      field.key === 'MODEL_EDGE_MID_CONFIDENCE' ||
      field.key === 'MODEL_EDGE_HIGH_CONFIDENCE' ||
      field.key === 'MODEL_EDGE_MID_THRESHOLD' ||
      field.key === 'MODEL_EDGE_HIGH_THRESHOLD' ||
      field.key === 'WALLET_UNCOPYABLE_DROP_MAX_SKIP_RATE'
    ) &&
    (numeric < 0 || (!isCostField && numeric > 1))
  ) {
    return {
      ok: false,
      error:
        isCostField
          ? `${field.label} must be 0 or greater.`
          : `${field.label} must be between 0 and 1.`
    }
  }

  if (
    (field.key === 'HEURISTIC_MIN_ENTRY_PRICE' && (numeric < 0 || numeric >= 1)) ||
    (field.key === 'HEURISTIC_MAX_ENTRY_PRICE' && (numeric <= 0 || numeric > 1))
  ) {
    return {ok: false, error: `${field.label} must be between 0 and 1.`}
  }

  if (field.key === 'WALLET_PERFORMANCE_DROP_MAX_WIN_RATE' && (numeric < 0 || numeric > 1)) {
    return {ok: false, error: `${field.label} must be between 0 and 1.`}
  }

  if (field.key === 'HOT_WALLET_COUNT' && numeric < 1) {
    return {ok: false, error: `${field.label} must be at least 1.`}
  }

  if (
    (
      field.key === 'WARM_WALLET_COUNT' ||
      field.key === 'WALLET_PERFORMANCE_DROP_MIN_TRADES' ||
      field.key === 'WALLET_UNCOPYABLE_PENALTY_MIN_BUYS' ||
      field.key === 'WALLET_UNCOPYABLE_DROP_MIN_BUYS' ||
      field.key === 'WALLET_UNCOPYABLE_DROP_MAX_RESOLVED_COPIED' ||
      field.key === 'WALLET_COLD_START_MIN_OBSERVED_BUYS' ||
      field.key === 'WALLET_DISCOVERY_MIN_OBSERVED_BUYS' ||
      field.key === 'WALLET_DISCOVERY_MIN_RESOLVED_BUYS' ||
      field.key === 'WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS' ||
      field.key === 'WALLET_LOCAL_PERFORMANCE_PENALTY_MIN_RESOLVED_COPIED_BUYS' ||
      field.key === 'LIVE_MIN_SHADOW_RESOLVED'
    ) &&
    numeric < 0
  ) {
    return {ok: false, error: `${field.label} must be 0 or greater.`}
  }

  if (
    (field.key === 'WARM_POLL_INTERVAL_MULTIPLIER' ||
      field.key === 'DISCOVERY_POLL_INTERVAL_MULTIPLIER' ||
      field.key === 'MAX_LIVE_HEALTH_FAILURES') &&
    numeric < 1
  ) {
    return {ok: false, error: `${field.label} must be at least 1.`}
  }

  if (field.key === 'WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN' && (numeric < -1 || numeric > 1)) {
    return {ok: false, error: `${field.label} must be between -1 and 1.`}
  }

  if (
    (field.key === 'DUPLICATE_SIDE_OVERRIDE_MIN_AVG_RETURN' ||
      field.key === 'EXPOSURE_OVERRIDE_MIN_AVG_RETURN' ||
      field.key === 'WALLET_LOCAL_PERFORMANCE_PENALTY_MAX_AVG_RETURN') &&
    (numeric < -1 || numeric > 1)
  ) {
    return {ok: false, error: `${field.label} must be between -1 and 1.`}
  }

  if (
    (field.key === 'WALLET_DISCOVERY_SIZE_MULTIPLIER' || field.key === 'WALLET_PROBATION_SIZE_MULTIPLIER') &&
    (numeric <= 0 || numeric > 1)
  ) {
    return {ok: false, error: `${field.label} must be greater than 0 and at most 1.`}
  }

  if (field.key === 'WALLET_LOCAL_PERFORMANCE_PENALTY_SIZE_MULTIPLIER' && (numeric < 0 || numeric > 1)) {
    return {ok: false, error: `${field.label} must be between 0 and 1.`}
  }

  if (field.key === 'WALLET_QUALITY_SIZE_MIN_MULTIPLIER' && (numeric < 0.1 || numeric > 1)) {
    return {ok: false, error: `${field.label} must be between 0.1 and 1.`}
  }

  if (field.key === 'WALLET_QUALITY_SIZE_MAX_MULTIPLIER' && (numeric < 1 || numeric > 3)) {
    return {ok: false, error: `${field.label} must be between 1 and 3.`}
  }

  if (
    (field.key === 'MAX_DAILY_LOSS_PCT' ||
      field.key === 'MAX_LIVE_DRAWDOWN_PCT' ||
      field.key === 'STOP_LOSS_MAX_LOSS_PCT' ||
      field.key === 'MAX_MARKET_EXPOSURE_FRACTION' ||
      field.key === 'MAX_TRADER_EXPOSURE_FRACTION' ||
      field.key === 'MAX_TOTAL_OPEN_EXPOSURE_FRACTION' ||
      field.key === 'EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION') &&
    (numeric < 0 || numeric > 100)
  ) {
    return {ok: false, error: `${field.label} must be between 0 and 100.`}
  }

  if (
    (field.key === 'DUPLICATE_SIDE_OVERRIDE_MIN_SKIPS' || field.key === 'EXPOSURE_OVERRIDE_MIN_SKIPS') &&
    numeric < 0
  ) {
    return {ok: false, error: `${field.label} must be 0 or greater.`}
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
    if (field.key === 'EXPECTED_CLOSE_FIXED_COST_USD') {
      return 'auto'
    }
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

  if (field.key === 'LOG_LEVEL') {
    return normalized.toUpperCase()
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

  if (
    field.key === 'SHADOW_BANKROLL_USD' ||
    field.key === 'MIN_BET_USD' ||
    field.key === 'ENTRY_FIXED_COST_USD' ||
    field.key === 'EXIT_FIXED_COST_USD' ||
    field.key === 'APPROVAL_FIXED_COST_USD' ||
    field.key === 'SETTLEMENT_FIXED_COST_USD' ||
    field.key === 'EXPECTED_CLOSE_FIXED_COST_USD'
  ) {
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
