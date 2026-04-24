import React, {useMemo} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {StatRow} from '../components/StatRow.js'
import {
  editableConfigFields,
  formatEditableConfigValue,
  useDashboardConfig,
  type EditableConfigValues
} from '../configEditor.js'
import {fit, fitRight, formatNumber, shortAddress, truncate, wrapText} from '../format.js'
import {isPlaceholderUsername, useIdentityMap} from '../identities.js'
import {rowsForHeight, stackPanels} from '../responsive.js'
import {
  dangerActions,
  isLiveTradingEnabled,
  type DangerConfirmState
} from '../settingsDanger.js'
import {useTerminalSize} from '../terminal.js'
import {selectionBackgroundColor, theme} from '../theme.js'
import {useBotState} from '../useBotState.js'
import {useQuery} from '../useDb.js'
import {useEventStream} from '../useEventStream.js'

interface CountRow {
  n: number
}

export interface SettingsEditorState {
  values: EditableConfigValues
  selectedIndex: number
  isEditing: boolean
  draft: string
  replaceDraftOnInput?: boolean
  statusMessage?: string
  statusTone?: 'info' | 'success' | 'error'
  focusArea: 'config' | 'danger'
  dangerSelectedIndex: number
  dangerConfirm: DangerConfirmState | null
}

interface SettingsProps {
  editor: SettingsEditorState
}

const COUNT_SQL = `SELECT COUNT(*) AS n FROM trade_log`

interface EnvData {
  rows: Array<{key: string; value: string}>
  watchedWallets: string[]
  rawValues: Record<string, string>
}

interface SummaryStat {
  label: string
  value: string
  color?: string
}

function envDataFromConfig(config: ReturnType<typeof useDashboardConfig>): EnvData {
  return {
    rows: config.rows,
    watchedWallets: config.watchedWallets,
    rawValues: config.safeValues
  }
}

function splitIntoColumns<T>(items: T[], columnCount: number): T[][] {
  if (columnCount <= 1 || items.length <= 1) {
    return [items]
  }

  const perColumn = Math.ceil(items.length / columnCount)
  const columns: T[][] = []
  for (let index = 0; index < items.length; index += perColumn) {
    columns.push(items.slice(index, index + perColumn))
  }
  return columns
}

function dangerToneColor(tone: SettingsEditorState['statusTone']): string {
  if (tone === 'error') return theme.red
  if (tone === 'success') return theme.green
  return theme.dim
}

function formatSettingsDateTime(timestamp: number | null | undefined): string {
  if (!timestamp) return '-'
  return new Date(timestamp * 1000).toLocaleString([], {
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true
  })
}

function formatSettingsTime(timestamp: number | null | undefined): string {
  if (!timestamp) return '-'
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit'
  })
}

function formatSettingsPercent(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '-'
  return `${formatNumber(value * 100, digits)}%`
}

function isMigratedRecoveryShadowStatus(status: string): boolean {
  return /(?:^|[_\s-])(migrated|legacy_migrated|mixed)(?:$|[_\s-])/i.test(status)
}

function formatSegmentSummaryValue(value: unknown): string {
  if (value == null) return '-'
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]'
    const items = value.slice(0, 4).map((item) => formatSegmentSummaryValue(item))
    return `[${items.join(', ')}${value.length > 4 ? ', ...' : ''}]`
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return '{}'
    const parts = entries.slice(0, 4).map(([key, item]) => `${key}=${formatSegmentSummaryValue(item)}`)
    return `{${parts.join(', ')}${entries.length > 4 ? ', ...' : ''}}`
  }
  if (typeof value === 'string') {
    const trimmed = value.trim()
    return trimmed.length > 64 ? `${trimmed.slice(0, 61)}...` : trimmed
  }
  return String(value)
}

function segmentSummaryLinesFromJson(raw: string): string[] {
  if (!raw) return []

  const parsed = JSON.parse(raw) as unknown
  if (Array.isArray(parsed)) {
    return parsed.slice(0, 6).map((item, index) => `${index + 1}. ${formatSegmentSummaryValue(item)}`)
  }
  if (parsed != null && typeof parsed === 'object') {
    return Object.entries(parsed as Record<string, unknown>)
      .slice(0, 8)
      .map(([key, value]) => `${key}: ${formatSegmentSummaryValue(value)}`)
  }
  return [formatSegmentSummaryValue(parsed)]
}

const CONFIG_BLURBS: Record<string, string> = {
  POLL_INTERVAL_SECONDS: 'Sets seconds between wallet polling cycles.',
  HOT_WALLET_COUNT: 'Sets how many wallets stay in the fastest polling tier.',
  WARM_WALLET_COUNT: 'Sets how many additional wallets stay in the warm polling tier.',
  WARM_POLL_INTERVAL_MULTIPLIER: 'Sets the warm-tier polling slowdown versus hot wallets.',
  DISCOVERY_POLL_INTERVAL_MULTIPLIER: 'Sets the discovery-tier polling slowdown versus hot wallets.',
  SOURCE_EVENT_PROCESS_BATCH_SIZE: 'Claims this many queued source events per loop; remaining events stay pending.',
  MAX_MARKET_HORIZON: 'Limits how far out copied markets may resolve.',
  MAX_SOURCE_TRADE_AGE: 'Skips source trades that are too old to copy.',
  MAX_FEED_STALENESS: 'Rejects stale market-data snapshots.',
  MAX_ORDERBOOK_STALENESS: 'Rejects stale order-book snapshots.',
  MIN_EXECUTION_WINDOW: 'Requires this much time left before resolution to open a trade.',
  WALLET_INACTIVITY_LIMIT: 'Drops wallets after too much source inactivity.',
  WALLET_SLOW_DROP_MAX_TRACKING_AGE: 'Drops slow wallets after this tracking age.',
  WALLET_PERFORMANCE_DROP_MIN_TRADES: 'Requires this many trades before performance-based drops.',
  WALLET_PERFORMANCE_DROP_MAX_WIN_RATE: 'Drops wallets below this profile win rate.',
  WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN: 'Drops wallets below this average profile return.',
  WALLET_UNCOPYABLE_PENALTY_MIN_BUYS: 'Starts penalizing uncopyable wallets after this many observed buys.',
  WALLET_UNCOPYABLE_PENALTY_WEIGHT: 'Sets how strongly uncopyable behavior reduces wallet quality.',
  WALLET_UNCOPYABLE_DROP_MIN_BUYS: 'Allows full uncopyable drops only after this many buys.',
  WALLET_UNCOPYABLE_DROP_MAX_SKIP_RATE: 'Drops wallets whose uncopyable skip rate stays above this level.',
  WALLET_UNCOPYABLE_DROP_MAX_RESOLVED_COPIED: 'Protects low-sample wallets from early uncopyable drops.',
  WALLET_COLD_START_MIN_OBSERVED_BUYS: 'Keeps new wallets in cold start until this many buys are seen.',
  WALLET_DISCOVERY_MIN_OBSERVED_BUYS: 'Requires this many buys before discovery scoring kicks in.',
  WALLET_DISCOVERY_MIN_RESOLVED_BUYS: 'Requires this many resolved buys before discovery wallets are judged on outcomes.',
  WALLET_DISCOVERY_SIZE_MULTIPLIER: 'Sets the smaller sizing multiplier for discovery wallets.',
  WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS: 'Requires this many resolved copied buys before a wallet becomes trusted.',
  WALLET_PROBATION_SIZE_MULTIPLIER: 'Sets sizing while a wallet is on probation.',
  WALLET_LOCAL_PERFORMANCE_PENALTY_MIN_RESOLVED_COPIED_BUYS: 'Needs this many copied trades before local underperformance penalizes size.',
  WALLET_LOCAL_PERFORMANCE_PENALTY_MAX_AVG_RETURN: 'Triggers the local penalty below this copied-trade average return.',
  WALLET_LOCAL_PERFORMANCE_PENALTY_SIZE_MULTIPLIER: 'Sets sizing after the local performance penalty triggers.',
  WALLET_QUALITY_SIZE_MIN_MULTIPLIER: 'Sets the minimum size multiplier for weaker wallets.',
  WALLET_QUALITY_SIZE_MAX_MULTIPLIER: 'Sets the maximum size multiplier for stronger wallets.',
  MIN_CONFIDENCE: 'Requires this minimum confidence before taking trades.',
  HEURISTIC_MIN_ENTRY_PRICE: 'Sets the heuristic entry price floor.',
  HEURISTIC_MAX_ENTRY_PRICE: 'Sets the heuristic entry price ceiling.',
  MODEL_EDGE_MID_CONFIDENCE: 'Mid-confidence trades start using a looser edge gate.',
  MODEL_EDGE_HIGH_CONFIDENCE: 'High-confidence trades use the loosest edge gate.',
  MODEL_EDGE_MID_THRESHOLD: 'Sets required edge for mid-confidence model trades.',
  MODEL_EDGE_HIGH_THRESHOLD: 'Sets required edge for high-confidence model trades.',
  MIN_BET_USD: 'Sets the smallest order size allowed.',
  ENTRY_FIXED_COST_USD: 'Adds a fixed entry cost estimate to the sizing model.',
  EXIT_FIXED_COST_USD: 'Adds a fixed exit cost estimate to the sizing model.',
  APPROVAL_FIXED_COST_USD: 'Adds a fixed approval cost estimate to the sizing model.',
  SETTLEMENT_FIXED_COST_USD: 'Adds a fixed settlement cost estimate to the sizing model.',
  EXPECTED_CLOSE_FIXED_COST_USD: 'Overrides expected close cost for sizing; blank means auto.',
  INCLUDE_EXPECTED_EXIT_FEE_IN_SIZING: 'Controls whether expected close cost is baked into entry sizing.',
  MAX_BET_FRACTION: 'Caps each bet as bankroll fraction.',
  MAX_MARKET_EXPOSURE_FRACTION: 'Caps bankroll exposure in any one market.',
  MAX_TRADER_EXPOSURE_FRACTION: 'Caps bankroll exposure to any one copied wallet.',
  MAX_TOTAL_OPEN_EXPOSURE_FRACTION: 'Caps total bankroll deployed across open positions.',
  EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION: 'Raises exposure cap for trusted wallets only.',
  DUPLICATE_SIDE_OVERRIDE_MIN_SKIPS: 'Needs this many skips before duplicate adds.',
  DUPLICATE_SIDE_OVERRIDE_MIN_AVG_RETURN: 'Needs this return before duplicate adds qualify.',
  EXPOSURE_OVERRIDE_MIN_SKIPS: 'Needs this many skips before trusted exposure applies.',
  EXPOSURE_OVERRIDE_MIN_AVG_RETURN: 'Needs this return before trusted exposure applies.',
  SHADOW_BANKROLL_USD: 'Sets the paper bankroll for tracker mode.',
  MAX_DAILY_LOSS_PCT: 'Stops new entries after this daily drawdown.',
  STOP_LOSS_ENABLED: 'Turns the stop-loss scanner on or off.',
  STOP_LOSS_MAX_LOSS_PCT: 'Sets the maximum allowed loss before stop-loss exits.',
  STOP_LOSS_MIN_HOLD: 'Sets how long a trade must be held before stop-loss can fire.',
  MAX_LIVE_DRAWDOWN_PCT: 'Sets the hard live-trading session drawdown stop.',
  MAX_LIVE_HEALTH_FAILURES: 'Stops live trading after this many consecutive health-check failures.',
  LIVE_REQUIRE_SHADOW_HISTORY: 'Requires enough resolved shadow history before live mode can start.',
  LIVE_MIN_SHADOW_RESOLVED: 'Sets the resolved-shadow minimum for live-mode eligibility.',
  RETRAIN_BASE_CADENCE: 'Sets how often scheduled retraining runs.',
  RETRAIN_HOUR_LOCAL: 'Sets the local hour for scheduled retraining.',
  RETRAIN_EARLY_CHECK_INTERVAL: 'Checks for early retraining on this interval.',
  RETRAIN_MIN_NEW_LABELS: 'Needs this many new labels for early retraining.',
  RETRAIN_MIN_SAMPLES: 'Needs this many samples before retraining starts.',
  LOG_LEVEL: 'Sets the bot process log verbosity.',
  MODEL_PATH: 'Sets the deployed model artifact path.'
}

const DANGER_ACTION_BLURBS: Record<string, string> = {
  live_trading: 'Uses the guarded backend live-mode endpoint.',
  restart_shadow: 'Resets shadow state, history, and models.',
  recover_db: 'Restores the shadow ledger from the latest verified backup. A backup can be integrity-only or evidence-ready.'
}

const DANGER_OPTION_BLURBS: Record<string, string> = {
  confirm_disable: 'Requests live mode off through the backend endpoint.',
  confirm_enable: 'Requests live mode on through the backend endpoint after all readiness checks pass.',
  keep_active: 'Resets shadow data and keeps active wallets.',
  keep_all: 'Resets shadow data and keeps all wallets.',
  clear_all: 'Resets shadow data and clears watched wallets.',
  cancel: 'Leaves everything unchanged.'
}

function SettingsSummaryBox({
  title,
  width,
  items,
  columnCount
}: {
  title: string
  width: string | number
  items: SummaryStat[]
  columnCount: number
}) {
  const columns = splitIntoColumns(items, columnCount)
  const contentWidth = typeof width === 'number' ? Math.max(24, width - 5) : 31
  const columnWidth = columnCount <= 1
    ? contentWidth
    : Math.max(16, Math.floor((contentWidth - (columnCount - 1) * 2) / columnCount))
  const rowWidth = Math.max(1, columnWidth - 1)
  const valueWidth = Math.max(8, Math.min(14, Math.floor(rowWidth * 0.52)))
  const labelWidth = Math.max(8, rowWidth - valueWidth - 1)

  return (
    <Box title={title} width={width}>
      <InkBox width="100%" marginTop={1} flexDirection="column">
        <InkBox width="100%">
          {columns.map((column, columnIndex) => (
            <React.Fragment key={`${title}-column-${columnIndex}`}>
              <InkBox flexDirection="column" width={columnWidth}>
                {column.map((item) => (
                  <InkBox key={`${title}-${item.label}`} width={rowWidth}>
                    <Text color={theme.dim}>{fit(item.label, labelWidth)}</Text>
                    <Text> </Text>
                    <Text color={item.color ?? theme.white}>{fitRight(item.value, valueWidth)}</Text>
                  </InkBox>
                ))}
              </InkBox>
              {columnIndex < columns.length - 1 ? <InkBox width={2} /> : null}
            </React.Fragment>
          ))}
        </InkBox>
        <Text> </Text>
      </InkBox>
    </Box>
  )
}

export function Settings({editor}: SettingsProps) {
  const terminal = useTerminalSize()
  const stacked = stackPanels(terminal.width)
  const state = useBotState()
  const counts = useQuery<CountRow>(COUNT_SQL)
  const events = useEventStream(1000)
  const config = useDashboardConfig()
  const envData = useMemo(() => envDataFromConfig(config), [config])
  const identityMap = useIdentityMap()
  const environmentBudget = rowsForHeight(terminal.height, stacked ? 40 : 30, 6, 14)
  const walletSectionHeaderRows = envData.watchedWallets.length ? 2 : 0
  const maxWalletLines = envData.watchedWallets.length ? Math.max(2, Math.min(6, Math.floor(environmentBudget / 2))) : 0
  const walletSectionRows = envData.watchedWallets.length
    ? walletSectionHeaderRows + Math.min(envData.watchedWallets.length, maxWalletLines) + (envData.watchedWallets.length > maxWalletLines ? 1 : 0)
    : 0
  const envRows = envData.rows.slice(0, Math.max(0, environmentBudget - walletSectionRows))
  const visibleWallets = envData.watchedWallets.slice(0, maxWalletLines)
  const hiddenWalletCount = Math.max(0, envData.watchedWallets.length - visibleWallets.length)
  const safeSelectedIndex = Math.max(0, Math.min(editor.selectedIndex, Math.max(editableConfigFields.length - 1, 0)))
  const selectedField = editableConfigFields[safeSelectedIndex]
  const safeDangerIndex = Math.max(0, Math.min(editor.dangerSelectedIndex, Math.max(dangerActions.length - 1, 0)))
  const selectedDangerAction = dangerActions[safeDangerIndex]
  const panelContentWidth = Math.max(24, terminal.width - 10)
  const middleRowGap = stacked ? 0 : 2
  const middleRowWidth = Math.max(24, terminal.width - 4)
  const configBoxWidth = stacked ? middleRowWidth : Math.max(56, Math.floor((middleRowWidth - middleRowGap) * 0.68))
  const dangerBoxWidth = stacked ? middleRowWidth : Math.max(28, middleRowWidth - configBoxWidth - middleRowGap)
  const configContentWidth = Math.max(28, configBoxWidth - 4)
  const dangerContentWidth = Math.max(24, dangerBoxWidth - 4)
  const configColumnCount = configContentWidth >= 78 ? 2 : 1
  const configVisibleRows = rowsForHeight(terminal.height, stacked ? 34 : 27, 8, 16)
  const configPageSize = Math.max(1, configVisibleRows * configColumnCount)
  const configPageIndex = Math.floor(safeSelectedIndex / configPageSize)
  const configPageStart = configPageIndex * configPageSize
  const visibleConfigEntries = useMemo(
    () => editableConfigFields
      .map((field, index) => ({field, index}))
      .slice(configPageStart, configPageStart + configPageSize),
    [configPageSize, configPageStart]
  )
  const configColumns = useMemo(
    () => splitIntoColumns(visibleConfigEntries, configColumnCount),
    [visibleConfigEntries, configColumnCount]
  )
  const configColumnWidth = configColumnCount === 1
    ? configContentWidth
    : Math.max(24, Math.floor((configContentWidth - 2) / configColumnCount))
  const configValueWidth = Math.max(10, Math.min(18, Math.floor(configColumnWidth * 0.36)))
  const configLabelWidth = Math.max(12, configColumnWidth - configValueWidth - 1)
  const dangerValueWidth = Math.max(8, Math.min(12, Math.floor(dangerContentWidth * 0.28)))
  const dangerLabelWidth = Math.max(12, dangerContentWidth - dangerValueWidth - 1)
  const helperWidth = Math.max(24, configContentWidth)
  const selectedRowBackground = selectionBackgroundColor(terminal.backgroundColor)
  const statusColor = dangerToneColor(editor.statusTone)
  const dangerHeaderText = editor.dangerConfirm
    ? editor.dangerConfirm.title
    : selectedDangerAction?.label || 'Danger Zone'
  const selectedDangerOption = editor.dangerConfirm?.options[editor.dangerConfirm.selectedIndex]
  const startupDetail = String(state.startup_detail || '').trim()
  const startupFailed = Boolean(state.startup_failed || state.startup_validation_failed)
  const startupBlocked = Boolean(state.startup_blocked) || /startup blocked/i.test(startupDetail)
  const startupRecoveryOnly = Boolean(state.startup_recovery_only) || startupBlocked
  const startupBlockReason = String(state.startup_block_reason || '').trim() || startupDetail
  const shadowRestartPending = Boolean(state.shadow_restart_pending)
  const shadowRestartKind = String(state.shadow_restart_kind || '').trim().toLowerCase()
  const shadowRestartMessage = String(state.shadow_restart_message || '').trim() || 'Shadow restart requested. Waiting for backend to restart.'
  const configEditBlocked = shadowRestartPending || startupRecoveryOnly
  const configEditBlockedMessage = shadowRestartPending
    ? `${shadowRestartMessage} Config edits stay blocked until the backend restarts.`
    : startupRecoveryOnly
      ? startupBlockReason
        ? `Recovery-only mode: ${startupBlockReason} Config edits stay blocked until Recover DB or Restart Shadow completes.`
        : 'Recovery-only mode: config edits stay blocked until Recover DB or Restart Shadow completes.'
      : ''
  const configHelperLine = configEditBlocked
    ? configEditBlockedMessage
    : CONFIG_BLURBS[selectedField?.key || ''] || ''
  const configHelperLines = wrapText(configHelperLine, helperWidth)
  const configHelperColor = configEditBlocked ? theme.yellow : statusColor
  const dbRecoveryCandidateReady = Boolean(state.db_recovery_candidate_ready)
  const dbRecoveryCandidateModeRaw = String(state.db_recovery_candidate_mode || '').trim().toLowerCase()
  const dbRecoveryCandidateEvidenceReady = Boolean(state.db_recovery_candidate_evidence_ready)
  const dbRecoveryCandidateMode = !dbRecoveryCandidateReady
    ? 'unavailable'
    : dbRecoveryCandidateModeRaw === 'evidence_ready' || dbRecoveryCandidateEvidenceReady || Boolean(state.db_recovery_shadow_ready)
      ? 'evidence_ready'
      : 'integrity_only'
  const dbRecoveryCandidateModeLabel = dbRecoveryCandidateMode === 'evidence_ready'
    ? 'evidence-ready'
    : dbRecoveryCandidateMode === 'integrity_only'
      ? 'integrity-only'
      : 'unavailable'
  const dbRecoveryCandidateModeShortLabel = dbRecoveryCandidateMode === 'evidence_ready'
    ? 'EV-READY'
    : dbRecoveryCandidateMode === 'integrity_only'
      ? 'INT-ONLY'
      : 'UNAVAIL'
  const dbRecoveryCandidateModeColor = dbRecoveryCandidateMode === 'evidence_ready'
    ? theme.green
    : dbRecoveryCandidateMode === 'integrity_only'
      ? theme.yellow
      : theme.red
  const dbRecoveryCandidateClassReason = String(state.db_recovery_candidate_class_reason || '').trim()
    || (
      dbRecoveryCandidateMode === 'evidence_ready'
        ? 'Verified backup is recoverable and its shadow evaluation passes the current evidence gate.'
        : dbRecoveryCandidateMode === 'integrity_only'
          ? 'Verified backup can restore the ledger, but its shadow evidence is not ready for readiness claims.'
          : 'No recoverable verified backup is available yet.'
    )
  const dbRecoveryCandidateUnavailableMessage = dbRecoveryCandidateClassReason
    || String(state.db_recovery_candidate_message || '').trim()
    || 'Recover DB is unavailable because no verified backup candidate is ready.'
  const recoverDbDangerBlurb =
    !editor.dangerConfirm && selectedDangerAction?.id === 'recover_db'
      ? shadowRestartPending
        ? shadowRestartMessage
        : dbRecoveryCandidateMode === 'evidence_ready'
        ? `Recover DB will restore an evidence-ready verified backup. ${dbRecoveryCandidateClassReason}`
        : dbRecoveryCandidateMode === 'integrity_only'
          ? `Recover DB will restore an integrity-only verified backup. ${dbRecoveryCandidateClassReason}`
          : dbRecoveryCandidateUnavailableMessage
      : ''
  const restartShadowDangerBlurb =
    !editor.dangerConfirm && selectedDangerAction?.id === 'restart_shadow' && shadowRestartPending
      ? shadowRestartMessage
      : ''
  const dangerBlurb =
    shadowRestartPending && !editor.dangerConfirm && selectedDangerAction?.id === 'live_trading'
      ? `${shadowRestartMessage} Live-mode requests stay blocked until the backend restarts.`
      : startupRecoveryOnly && !editor.dangerConfirm && selectedDangerAction?.id === 'live_trading'
        ? 'Backend is in recovery-only mode. Recover DB or Restart Shadow first; live-mode requests stay blocked until startup recovers.'
      : recoverDbDangerBlurb
        || restartShadowDangerBlurb
        || DANGER_OPTION_BLURBS[selectedDangerOption?.id || '']
        || DANGER_ACTION_BLURBS[editor.dangerConfirm?.actionId || selectedDangerAction?.id || '']
        || ''
  const dangerHelperLines = wrapText(dangerBlurb, dangerContentWidth)
  const startupBlockedHelperLines = startupRecoveryOnly
    ? wrapText(
        startupBlockReason
          ? `Recovery-only mode: ${startupBlockReason}`
          : 'Recovery-only mode: backend startup is blocked until Recover DB or Restart Shadow completes.',
        dangerContentWidth
      )
    : []
  const usernames = useMemo(() => {
    const lookup = new Map(identityMap)
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index]
      const wallet = event.trader?.trim().toLowerCase()
      const username = event.username?.trim()
      if (!wallet || !username || isPlaceholderUsername(username, wallet) || lookup.has(wallet)) {
        continue
      }
      lookup.set(wallet, username)
    }
    return lookup
  }, [events, identityMap])
  const walletTableWidth = Math.max(24, panelContentWidth)
  const walletIndexWidth = Math.max(3, String(Math.max(1, envData.watchedWallets.length)).length + 1)
  const walletAddressWidth = Math.max(18, Math.min(42, Math.floor(walletTableWidth * 0.62)))
  const walletUsernameWidth = Math.max(8, walletTableWidth - walletIndexWidth - walletAddressWidth - 2)
  const liveTradingEnabled = isLiveTradingEnabled(envData.rawValues)
  const topRowGap = stacked ? 0 : 1
  const topRowWidth = Math.max(24, terminal.width - 4)
  const topBoxWidth = stacked ? '100%' : Math.max(28, Math.floor((topRowWidth - topRowGap) / 2))
  const topBoxContentWidth = typeof topBoxWidth === 'number' ? Math.max(24, topBoxWidth - 4) : 24
  const topBoxColumnCount = topBoxContentWidth >= 34 ? 2 : 1
  const shadowHistoryStateKnown = Boolean(state.shadow_history_state_known)
  const resolvedShadowTradeCount = Math.max(0, Number(state.resolved_shadow_trade_count || 0))
  const requireTotalShadowHistory = Boolean(state.live_require_shadow_history_enabled)
  const minShadowResolved = Math.max(0, Number(state.live_min_shadow_resolved || 0))
  const shadowHistoryTotalReady = Boolean(state.live_shadow_history_total_ready)
  const resolvedShadowSincePromotion = Math.max(0, Number(state.resolved_shadow_since_last_promotion || 0))
  const minShadowSincePromotion = Math.max(0, Number(state.live_min_shadow_resolved_since_last_promotion || 0))
  const shadowHistoryReady = Boolean(state.live_shadow_history_ready)
  const appliedPromotionAt = Math.max(0, Number(state.last_applied_replay_promotion_at || 0))
  const shadowGateRequired = (requireTotalShadowHistory && minShadowResolved > 0) || minShadowSincePromotion > 0
  const shadowGateReady = (!requireTotalShadowHistory || minShadowResolved <= 0 || shadowHistoryTotalReady)
    && (minShadowSincePromotion <= 0 || shadowHistoryReady)
  const shadowGateProgress = (requireTotalShadowHistory && resolvedShadowTradeCount > 0)
    || (minShadowSincePromotion > 0 && resolvedShadowSincePromotion > 0)
  const shadowGateStatus = !shadowHistoryStateKnown
    ? 'checking'
    : !liveTradingEnabled
      ? 'blocked'
      : shadowGateReady
        ? 'ready'
        : shadowGateProgress
          ? 'blocked (progress)'
          : 'blocked'
  const shadowGateColor = !shadowHistoryStateKnown
    ? theme.dim
    : !liveTradingEnabled
      ? theme.red
      : shadowGateReady
        ? theme.green
        : shadowGateProgress
          ? theme.yellow
          : theme.red
  const shadowReadinessSummary = useMemo(() => {
    if (!shadowHistoryStateKnown) {
      return 'waiting for the bot to publish shadow-history readiness'
    }

    const summaryParts: string[] = []
    if (requireTotalShadowHistory && minShadowResolved > 0) {
      summaryParts.push(
        `all-time base ${shadowHistoryTotalReady ? 'ready' : 'need'} ${formatNumber(resolvedShadowTradeCount)}/${formatNumber(minShadowResolved)}`
      )
    } else {
      summaryParts.push('all-time base off')
    }

    if (minShadowSincePromotion > 0) {
      const promotionLabel = appliedPromotionAt > 0 ? formatSettingsDateTime(appliedPromotionAt) : 'initial policy'
      summaryParts.push(
        `since-promotion ${shadowHistoryReady ? 'ready' : 'need'} ${formatNumber(resolvedShadowSincePromotion)}/${formatNumber(minShadowSincePromotion)} since ${promotionLabel}`
      )
    } else {
      summaryParts.push('since-promotion off')
    }

    summaryParts.push(`${formatNumber(resolvedShadowTradeCount)} total`)
    return summaryParts.join(' | ')
  }, [
    appliedPromotionAt,
    minShadowResolved,
    minShadowSincePromotion,
    requireTotalShadowHistory,
    resolvedShadowSincePromotion,
    resolvedShadowTradeCount,
    shadowHistoryReady,
    shadowHistoryStateKnown,
    shadowHistoryTotalReady
  ])
  const shadowReadinessReason = useMemo(() => {
    if (!shadowHistoryStateKnown) {
      return 'waiting for the bot to publish all-time shadow-history readiness'
    }
    if (!liveTradingEnabled) {
      return 'live trading is disabled in config, so the bot stays shadow-only'
    }
    if (!shadowGateRequired) {
      return 'all-time shadow-history gating is disabled in config'
    }
    if (shadowGateReady) {
      return 'all-time shadow history satisfies the configured live gate, but the project remains shadow-only'
    }
    if (requireTotalShadowHistory && minShadowResolved > 0 && !shadowHistoryTotalReady) {
      return `need ${formatNumber(resolvedShadowTradeCount)}/${formatNumber(minShadowResolved)} resolved all-time shadow trades`
    }
    if (minShadowSincePromotion > 0 && !shadowHistoryReady) {
      const promotionLabel = appliedPromotionAt > 0 ? `last promotion at ${formatSettingsDateTime(appliedPromotionAt)}` : 'the initial policy'
      return `need ${formatNumber(resolvedShadowSincePromotion)}/${formatNumber(minShadowSincePromotion)} resolved shadow trades since ${promotionLabel}`
    }
    return 'live mode is blocked by the configured all-time shadow-history gate'
  }, [
    appliedPromotionAt,
    liveTradingEnabled,
    minShadowResolved,
    minShadowSincePromotion,
    requireTotalShadowHistory,
    resolvedShadowSincePromotion,
    resolvedShadowTradeCount,
    shadowGateReady,
    shadowGateRequired,
    shadowHistoryReady,
    shadowHistoryStateKnown,
    shadowHistoryTotalReady
  ])
  const shadowReadinessWidth = Math.max(24, panelContentWidth)
  const shadowReadinessSummaryLines = wrapText(shadowReadinessSummary, shadowReadinessWidth)
  const shadowReadinessReasonLines = wrapText(shadowReadinessReason, shadowReadinessWidth)
  const shadowHistoryEpochKnown = Boolean(state.shadow_history_epoch_known)
  const shadowHistoryEpochStartedAt = Math.max(0, Number(state.shadow_history_epoch_started_at || 0))
  const shadowHistoryEpochSourceLabel = String(state.shadow_history_epoch_source_label || '').trim()
  const shadowHistoryEpochActiveScopeLabel = String(state.shadow_history_epoch_active_scope_label || '').trim()
  const shadowHistoryEpochStatusRaw = String(state.shadow_history_epoch_status || '').trim()
  const shadowHistoryEpochTotalResolved = Math.max(0, Number(state.shadow_history_epoch_total_resolved || 0))
  const shadowHistoryEpochReadyCount = Math.max(0, Number(state.shadow_history_epoch_ready_count || 0))
  const shadowHistoryEpochBlockedCount = Math.max(0, Number(state.shadow_history_epoch_blocked_count || 0))
  const shadowHistoryEpochMinResolved = Math.max(0, Number(state.shadow_history_epoch_min_resolved || 0))
  const shadowHistoryEpochRoutedResolved = Math.max(0, Number(state.shadow_history_epoch_routed_resolved || 0))
  const shadowHistoryEpochLegacyResolved = Math.max(0, Number(state.shadow_history_epoch_legacy_resolved || 0))
  const shadowHistoryEpochCoveragePct = state.shadow_history_epoch_coverage_pct
  const shadowHistoryEpochReady = Boolean(state.shadow_history_epoch_ready)
  const shadowHistoryEpochBlockReason = String(state.shadow_history_epoch_block_reason || '').trim()
  const shadowHistoryEpochStatus = !shadowHistoryEpochKnown
    ? 'checking'
    : shadowHistoryEpochStatusRaw || (shadowHistoryEpochReady ? 'ready' : shadowHistoryEpochTotalResolved > 0 ? 'partial' : 'idle')
  const shadowHistoryEpochColor = !shadowHistoryEpochKnown
    ? theme.dim
    : /active|ready|open/i.test(shadowHistoryEpochStatus)
      ? theme.green
      : /legacy|mixed|partial|inactive|idle/i.test(shadowHistoryEpochStatus)
        ? theme.yellow
        : /blocked|block|fail|error|corrupt|invalid/i.test(shadowHistoryEpochStatus)
          ? theme.red
          : shadowHistoryEpochReady || shadowHistoryEpochTotalResolved > 0
            ? theme.green
            : theme.dim
  const shadowHistoryEpochGateSufficient = shadowHistoryEpochKnown
    && (shadowHistoryEpochReady || (shadowHistoryEpochMinResolved <= 0 ? shadowHistoryEpochTotalResolved > 0 : shadowHistoryEpochTotalResolved >= shadowHistoryEpochMinResolved))
    && !/legacy_only|insufficient|blocked|block|fail|error|corrupt|invalid/i.test(shadowHistoryEpochStatus)
  const shadowHistoryEpochCoverageLabel = shadowHistoryEpochCoveragePct == null
    ? '-'
    : formatSettingsPercent(shadowHistoryEpochCoveragePct)
  const shadowHistoryEpochSummaryLines = useMemo(() => {
    if (!shadowHistoryEpochKnown) {
      return ['bot has not published a shadow-history epoch yet']
    }
    if (shadowHistoryEpochStartedAt <= 0 && !shadowHistoryEpochSourceLabel && !shadowHistoryEpochActiveScopeLabel && shadowHistoryEpochTotalResolved <= 0) {
      return ['shadow-history epoch metadata is available, but no readiness counts are published yet']
    }
    return [
      `status: ${shadowHistoryEpochStatus}`,
      `started: ${shadowHistoryEpochStartedAt > 0 ? formatSettingsDateTime(shadowHistoryEpochStartedAt) : '-'}`,
      `source: ${shadowHistoryEpochSourceLabel || 'legacy/unassigned source'}`,
      `active scope: ${shadowHistoryEpochActiveScopeLabel || 'active shadow-history scope not published'}`,
      `current window: ${formatNumber(shadowHistoryEpochReadyCount)}/${formatNumber(shadowHistoryEpochTotalResolved)} ready`,
      `coverage: ${shadowHistoryEpochCoverageLabel}`,
      `legacy/all-time resolved: ${formatNumber(shadowHistoryEpochLegacyResolved)}`,
      `routed resolved: ${formatNumber(shadowHistoryEpochRoutedResolved)}`,
      `gate: ${shadowHistoryEpochGateSufficient ? 'sufficient' : 'insufficient'}`,
      'source label describes where the evidence came from; active scope describes what the live gate uses',
      shadowHistoryEpochReady
        ? 'current evidence-window readiness is separate from legacy/all-time history'
        : 'current evidence-window readiness is still insufficient even if legacy/all-time history exists'
    ]
  }, [
    shadowHistoryEpochActiveScopeLabel,
    shadowHistoryEpochCoverageLabel,
    shadowHistoryEpochGateSufficient,
    shadowHistoryEpochKnown,
    shadowHistoryEpochLegacyResolved,
    shadowHistoryEpochReady,
    shadowHistoryEpochReadyCount,
    shadowHistoryEpochRoutedResolved,
    shadowHistoryEpochSourceLabel,
    shadowHistoryEpochStartedAt,
    shadowHistoryEpochStatus,
    shadowHistoryEpochTotalResolved
  ])
  const shadowHistoryEpochDetailLines = [
    shadowHistoryEpochBlockReason ? `block reason: ${shadowHistoryEpochBlockReason}` : '',
    shadowHistoryEpochLegacyResolved > shadowHistoryEpochReadyCount && shadowHistoryEpochTotalResolved > 0
      ? 'legacy/all-time history dominates; current evidence-window counts are still insufficient'
      : '',
    !shadowHistoryEpochGateSufficient
      ? 'replay/live optimization should not treat the current evidence window as sufficient yet'
      : 'replay/live optimization may treat the current evidence window as sufficient'
  ].filter(Boolean)
  const shadowHistoryEpochDetailWrappedLines = shadowHistoryEpochDetailLines.flatMap((line) =>
    wrapText(line, Math.max(24, panelContentWidth))
  )
  const routedShadowStateKnown = Boolean(state.routed_shadow_state_known)
  const routedShadowStatusRaw = String(state.routed_shadow_status || '').trim()
  const routedShadowRoutedResolved = Math.max(0, Number(state.routed_shadow_routed_resolved || 0))
  const routedShadowLegacyResolved = Math.max(0, Number(state.routed_shadow_legacy_resolved || 0))
  const routedShadowMinResolved = Math.max(0, Number(state.routed_shadow_min_resolved || 0))
  const routedShadowTotalResolved = Math.max(
    0,
    Number(state.routed_shadow_total_resolved || (routedShadowRoutedResolved + routedShadowLegacyResolved) || 0)
  )
  const routedShadowCoveragePct = state.routed_shadow_coverage_pct
  const routedShadowReady = Boolean(state.routed_shadow_ready)
  const routedShadowBlockReason = String(state.routed_shadow_block_reason || '').trim()
  const routedShadowLegacyDominant = routedShadowLegacyResolved > routedShadowRoutedResolved
  const routedShadowStatus = !routedShadowStateKnown
    ? 'checking'
    : routedShadowStatusRaw || (routedShadowReady || routedShadowTotalResolved > 0 ? 'ready' : 'idle')
  const routedShadowColor = !routedShadowStateKnown
    ? theme.dim
    : /block|fail|error|corrupt|invalid/i.test(routedShadowStatus)
      ? theme.red
      : /mixed|legacy_only|legacy|insufficient|partial/i.test(routedShadowStatus)
        ? theme.yellow
      : /ready|ok|healthy/i.test(routedShadowStatus)
          ? theme.green
          : routedShadowTotalResolved > 0
            ? routedShadowLegacyDominant
              ? theme.yellow
              : theme.green
            : theme.dim
  const routedShadowGateSufficient = routedShadowStateKnown
    && (routedShadowReady || (routedShadowMinResolved <= 0 ? routedShadowTotalResolved > 0 : routedShadowTotalResolved >= routedShadowMinResolved))
    && !/legacy_only|insufficient|blocked|block|fail|error|corrupt|invalid/i.test(routedShadowStatus)
  const routedShadowGateLabel = !routedShadowStateKnown
    ? 'checking'
    : routedShadowGateSufficient
      ? 'sufficient'
      : 'insufficient'
  const routedShadowCoverageLabel = routedShadowCoveragePct == null
    ? '-'
    : formatSettingsPercent(routedShadowCoveragePct)
  const routedShadowPerformanceStateKnown = Boolean(state.routed_shadow_state_known)
    || state.routed_shadow_total_pnl_usd != null
    || state.routed_shadow_return_pct != null
    || state.routed_shadow_profit_factor != null
    || state.routed_shadow_expectancy_usd != null
  const routedShadowTotalPnlUsd = state.routed_shadow_total_pnl_usd
  const routedShadowReturnPct = state.routed_shadow_return_pct
  const routedShadowProfitFactor = state.routed_shadow_profit_factor
  const routedShadowExpectancyUsd = state.routed_shadow_expectancy_usd
  const routedShadowDataWarning = String(state.routed_shadow_data_warning || '').trim()
  const routedShadowPerformanceReady = routedShadowGateSufficient
    && !/legacy_only|insufficient|blocked|block|fail|error|corrupt|invalid/i.test(routedShadowStatus)
  const routedShadowPerformanceStatus = !routedShadowPerformanceStateKnown
    ? 'checking'
    : routedShadowStatus
  const routedShadowPerformanceColor = !routedShadowPerformanceStateKnown
    ? theme.dim
    : routedShadowPerformanceReady
      ? theme.green
      : /legacy_only|legacy|insufficient|partial/i.test(routedShadowPerformanceStatus)
        ? theme.yellow
        : /blocked|block|fail|error|corrupt|invalid/i.test(routedShadowPerformanceStatus)
          ? theme.red
          : routedShadowGateSufficient
            ? theme.green
            : theme.yellow
  const routedShadowPerformanceSummaryLines = useMemo(() => {
    if (!routedShadowPerformanceStateKnown) {
      return ['bot has not published routed shadow performance yet']
    }
    if (
      routedShadowTotalResolved <= 0
      && routedShadowTotalPnlUsd == null
      && routedShadowReturnPct == null
      && routedShadowProfitFactor == null
      && routedShadowExpectancyUsd == null
    ) {
      return [
        'no routed shadow performance data is currently available',
        'this box reflects routed post-segmentation performance, not legacy shadow history'
      ]
    }
    const summaryParts = [
      `status: ${routedShadowPerformanceStatus}`,
      `gate: ${routedShadowGateLabel}`,
      `pnl: ${routedShadowTotalPnlUsd == null ? '-' : `$${formatNumber(routedShadowTotalPnlUsd)}`}`,
      `pnl / bankroll: ${formatSettingsPercent(routedShadowReturnPct)}`,
      `profit factor: ${formatNumber(routedShadowProfitFactor, 2)}`,
      `expectancy: ${routedShadowExpectancyUsd == null ? '-' : `$${formatNumber(routedShadowExpectancyUsd)}`}`,
      `routed resolved: ${formatNumber(routedShadowRoutedResolved)}`,
      `legacy resolved: ${formatNumber(routedShadowLegacyResolved)}`,
      routedShadowLegacyDominant
        ? 'legacy history dominates, so routed performance remains insufficient'
        : 'routed performance is based on routed post-segmentation evidence'
    ]
    return summaryParts
  }, [
    routedShadowExpectancyUsd,
    routedShadowGateLabel,
    routedShadowLegacyDominant,
    routedShadowLegacyResolved,
    routedShadowPerformanceStateKnown,
    routedShadowPerformanceStatus,
    routedShadowProfitFactor,
    routedShadowRoutedResolved,
    routedShadowReturnPct,
    routedShadowTotalPnlUsd,
    routedShadowTotalResolved
  ])
  const routedShadowPerformanceDetailLines = [
    routedShadowDataWarning ? `warning: ${routedShadowDataWarning}` : '',
    routedShadowBlockReason ? `block reason: ${routedShadowBlockReason}` : '',
    !routedShadowPerformanceReady
      ? 'replay/live optimization should not treat routed performance as sufficient yet'
      : 'replay/live optimization may treat routed performance as sufficient'
  ].filter(Boolean)
  const routedShadowPerformanceDetailWrappedLines = routedShadowPerformanceDetailLines.flatMap((line) =>
    wrapText(line, Math.max(24, panelContentWidth))
  )
  const routedShadowEpochKnown = Boolean(state.routed_shadow_epoch_known)
  const routedShadowEpochStartedAt = Math.max(0, Number(state.routed_shadow_epoch_started_at || 0))
  const routedShadowEpochSourceLabel = String(state.routed_shadow_epoch_source_label || '').trim()
  const routedShadowEpochActiveScopeLabel = String(state.routed_shadow_epoch_active_scope_label || '').trim()
  const routedShadowEpochStatusRaw = String(state.routed_shadow_epoch_status || '').trim()
  const routedShadowEpochStatus = !routedShadowEpochKnown
    ? 'checking'
    : routedShadowEpochStatusRaw || (routedShadowEpochStartedAt > 0 ? 'active' : 'idle')
  const routedShadowEpochColor = !routedShadowEpochKnown
    ? theme.dim
    : /active|ready|open/i.test(routedShadowEpochStatus)
      ? theme.green
      : /legacy|mixed|partial|inactive|idle/i.test(routedShadowEpochStatus)
        ? theme.yellow
        : /blocked|block|fail|error|corrupt|invalid/i.test(routedShadowEpochStatus)
          ? theme.red
          : routedShadowEpochStartedAt > 0
            ? theme.green
            : theme.dim
  const routedShadowEpochSummaryLines = useMemo(() => {
    if (!routedShadowEpochKnown) {
      return ['bot has not published a routed shadow evidence epoch yet']
    }
    if (routedShadowEpochStartedAt <= 0 && !routedShadowEpochSourceLabel && !routedShadowEpochActiveScopeLabel) {
      return ['routed shadow evidence epoch is not active yet']
    }
    return [
      `status: ${routedShadowEpochStatus}`,
      `started: ${routedShadowEpochStartedAt > 0 ? formatSettingsDateTime(routedShadowEpochStartedAt) : '-'}`,
      `source: ${routedShadowEpochSourceLabel || 'legacy/unassigned source'}`,
      `active scope: ${routedShadowEpochActiveScopeLabel || 'active routed scope not published'}`,
      'source label describes where the evidence came from; active scope describes what the live gate uses'
    ]
  }, [
    routedShadowEpochActiveScopeLabel,
    routedShadowEpochKnown,
    routedShadowEpochSourceLabel,
    routedShadowEpochStartedAt,
    routedShadowEpochStatus
  ])
  const routedShadowSummaryLines = useMemo(() => {
    if (!routedShadowStateKnown) {
      return ['bot has not published routed-shadow evidence yet']
    }
    if (routedShadowTotalResolved <= 0) {
      return [
        'no routed-shadow evidence is available yet',
        'this section separates routed post-segmentation evidence from legacy/unassigned shadow history'
      ]
    }
    const summaryParts = [
      `status: ${routedShadowStatus}`,
      `threshold: ${routedShadowMinResolved > 0 ? formatNumber(routedShadowMinResolved) : '-'}`,
      `total resolved: ${formatNumber(routedShadowTotalResolved)}`,
      `coverage: ${routedShadowCoverageLabel}`,
      `gate: ${routedShadowGateLabel}`,
      `routed resolved: ${formatNumber(routedShadowRoutedResolved)}`,
      `legacy resolved: ${formatNumber(routedShadowLegacyResolved)}`,
      routedShadowLegacyDominant
        ? 'history is mostly legacy; fixed-segment evidence is insufficient'
        : 'routed evidence is present',
      'routed evidence is separate from legacy/unassigned shadow history'
    ]
    return summaryParts
  }, [
    routedShadowLegacyDominant,
    routedShadowLegacyResolved,
    routedShadowCoverageLabel,
    routedShadowGateLabel,
    routedShadowMinResolved,
    routedShadowRoutedResolved,
    routedShadowStateKnown,
    routedShadowStatus,
    routedShadowTotalResolved
  ])
  const routedShadowDetailLines = [
    routedShadowBlockReason ? `block reason: ${routedShadowBlockReason}` : '',
    routedShadowLegacyDominant ? 'fixed-segment evidence is still insufficient because legacy history dominates' : '',
    !routedShadowGateSufficient ? 'replay/live optimization should treat routed evidence as insufficient' : 'replay/live optimization may treat routed evidence as sufficient'
  ].filter(Boolean)
  const routedShadowDetailWrappedLines = routedShadowDetailLines.flatMap((line) =>
    wrapText(line, Math.max(24, panelContentWidth))
  )
  const shadowSegmentStateKnown = Boolean(state.shadow_segment_state_known)
  const shadowSegmentReportedStatus = String(state.shadow_segment_status || '').trim()
  const shadowSegmentScope = String(state.shadow_segment_scope || '').trim()
  const shadowSegmentScopeStartedAt = Math.max(0, Number(state.shadow_segment_scope_started_at || 0))
  const shadowSegmentMinResolved = Math.max(0, Number(state.shadow_segment_min_resolved || 0))
  const shadowSegmentTotal = Math.max(0, Number(state.shadow_segment_total || 0))
  const shadowSegmentReadyCount = Math.max(0, Number(state.shadow_segment_ready_count || 0))
  const shadowSegmentPositiveCount = Math.max(0, Number(state.shadow_segment_positive_count || 0))
  const shadowSegmentNegativeCount = Math.max(0, Number(state.shadow_segment_negative_count || 0))
  const shadowSegmentBlockedCount = Math.max(0, Number(state.shadow_segment_blocked_count || 0))
  const shadowSegmentStatus = shadowSegmentReportedStatus
    || (!shadowSegmentStateKnown
      ? 'checking'
      : shadowSegmentBlockedCount > 0
        ? 'blocked'
        : shadowSegmentTotal > 0 && shadowSegmentReadyCount >= shadowSegmentTotal
          ? 'ready'
          : shadowSegmentTotal > 0
            ? 'partial'
            : 'empty')
  const shadowSegmentColor = !shadowSegmentStateKnown
    ? theme.dim
    : shadowSegmentStatus === 'blocked' || shadowSegmentBlockedCount > 0
      ? theme.red
      : shadowSegmentStatus === 'ready'
        ? theme.green
        : shadowSegmentStatus === 'mixed' || shadowSegmentStatus === 'partial' || shadowSegmentStatus === 'insufficient' || shadowSegmentStatus === 'legacy_only'
          ? theme.yellow
          : theme.dim
  const shadowSegmentScopeLabel = shadowSegmentScope === 'since_ts' && shadowSegmentScopeStartedAt > 0
    ? `since ${formatSettingsDateTime(shadowSegmentScopeStartedAt)}`
    : 'all history'
  const shadowSegmentSummary = useMemo(() => {
    if (!shadowSegmentStateKnown) {
      return 'waiting for the bot to publish segment shadow readiness'
    }

    const parts: string[] = []
    parts.push(`${formatNumber(shadowSegmentReadyCount)}/${formatNumber(shadowSegmentTotal)} ready`)
    if (shadowSegmentMinResolved > 0) {
      parts.push(`min ${formatNumber(shadowSegmentMinResolved)} resolved`)
    }
    parts.push(`${formatNumber(shadowSegmentPositiveCount)} positive`)
    parts.push(`${formatNumber(shadowSegmentNegativeCount)} negative`)
    parts.push(`${formatNumber(shadowSegmentBlockedCount)} blocked`)
    return parts.join(' | ')
  }, [
    shadowSegmentBlockedCount,
    shadowSegmentMinResolved,
    shadowSegmentNegativeCount,
    shadowSegmentPositiveCount,
    shadowSegmentReadyCount,
    shadowSegmentStateKnown,
    shadowSegmentTotal
  ])
  const shadowSegmentSummaryLines = wrapText(shadowSegmentSummary, Math.max(24, panelContentWidth))
  const shadowSegmentBlockReason = String(state.shadow_segment_block_reason || '').trim()
  const shadowSegmentBlockReasonLines = shadowSegmentBlockReason
    ? wrapText(shadowSegmentBlockReason, Math.max(24, panelContentWidth))
    : []
  const shadowSegmentSummaryJsonLines = useMemo(() => {
    const raw = String(state.shadow_segment_summary_json || '').trim()
    if (!shadowSegmentStateKnown || !raw) {
      return []
    }

    try {
      return segmentSummaryLinesFromJson(raw)
    } catch {
      return wrapText(raw, Math.max(24, panelContentWidth))
    }
  }, [panelContentWidth, shadowSegmentStateKnown, state.shadow_segment_summary_json])
  const dbIntegrityKnown = Boolean(state.db_integrity_known)
  const dbIntegrityOk = !dbIntegrityKnown || Boolean(state.db_integrity_ok)
  const dbIntegrityStatus = !dbIntegrityKnown ? 'checking' : dbIntegrityOk ? 'ok' : 'corrupt'
  const dbIntegrityColor = !dbIntegrityKnown ? theme.dim : dbIntegrityOk ? theme.green : theme.red
  const dbIntegrityMessage = String(state.db_integrity_message || '').trim()
  const dbIntegrityLines =
    dbIntegrityKnown && dbIntegrityMessage
      ? wrapText(dbIntegrityMessage, Math.max(24, panelContentWidth))
      : []
  const dbIntegrityReadyForLive = dbIntegrityKnown && dbIntegrityOk
  const shadowHistoryReadyForLive = shadowHistoryStateKnown && shadowGateReady
  const segmentShadowReadyForLive = shadowSegmentStateKnown
    && shadowSegmentStatus === 'ready'
    && shadowSegmentTotal > 0
    && shadowSegmentReadyCount >= shadowSegmentTotal
    && shadowSegmentBlockedCount === 0
  const liveModeStartupBlocked = startupRecoveryOnly || shadowRestartPending
  const liveModeReady = !liveModeStartupBlocked
    && liveTradingEnabled
    && dbIntegrityReadyForLive
    && shadowHistoryReadyForLive
    && segmentShadowReadyForLive
  const liveReadinessStatus = !liveTradingEnabled
    ? 'disabled'
    : shadowRestartPending
      ? 'pending_restart'
      : liveModeReady
        ? 'ready'
        : 'blocked'
  const liveReadinessColor = !liveTradingEnabled
    ? theme.red
    : shadowRestartPending
      ? theme.yellow
      : liveModeReady ? theme.green : theme.red
  const liveReadinessSummary = useMemo(() => {
    if (!liveTradingEnabled) {
      return 'live mode is disabled in config, so the guarded backend endpoint will stay idle'
    }
    if (shadowRestartPending) {
      return `${shadowRestartMessage} Live mode remains blocked until the backend restarts on the updated shadow base`
    }
    if (startupRecoveryOnly) {
      return 'live mode remains blocked while the backend is in recovery-only startup mode'
    }
    if (liveModeReady) {
      return 'live mode can be enabled because DB integrity, shadow-history, and segment-shadow readiness are satisfied'
    }
    return 'live mode is blocked until DB integrity, shadow-history, and segment-shadow readiness are satisfied'
  }, [liveModeReady, liveTradingEnabled, shadowRestartMessage, shadowRestartPending, startupRecoveryOnly])
  const dbRecoveryStateKnown = Boolean(state.db_recovery_state_known)
  const dbRecoveryCandidatePath = String(state.db_recovery_candidate_path || '').trim()
  const dbRecoveryCandidateSourcePath = String(state.db_recovery_candidate_source_path || '').trim()
  const dbRecoveryCandidateMessage = String(state.db_recovery_candidate_message || '').trim()
  const dbRecoveryLatestVerifiedBackupPath = String(state.db_recovery_latest_verified_backup_path || '').trim()
  const dbRecoveryLatestVerifiedBackupAt = Math.max(0, Number(state.db_recovery_latest_verified_backup_at || 0))
  const dbRecoveryPendingState = shadowRestartPending && shadowRestartKind === 'db_recovery'
  const dbRecoveryStatus = !dbRecoveryStateKnown
    ? 'checking'
    : dbRecoveryPendingState
      ? 'pending_restart'
      : dbRecoveryCandidateReady
      ? 'ready'
      : dbRecoveryCandidatePath || dbRecoveryCandidateSourcePath || dbRecoveryCandidateMessage
        ? 'blocked'
        : 'idle'
  const dbRecoveryColor = !dbRecoveryStateKnown
    ? theme.dim
    : dbRecoveryPendingState
      ? theme.yellow
      : dbRecoveryCandidateReady
      ? theme.green
      : dbRecoveryCandidatePath || dbRecoveryCandidateSourcePath || dbRecoveryCandidateMessage
        ? theme.yellow
        : theme.dim
  const dbRecoverySummaryLines = useMemo(() => {
    if (!dbRecoveryStateKnown) {
      return ['waiting for the bot to publish DB recovery readiness']
    }
    if (dbRecoveryPendingState) {
      return [
        `status: ${dbRecoveryStatus}`,
        shadowRestartMessage,
        'candidate evaluation is frozen until the queued shadow restart or recovery completes'
      ]
    }
    return [
      `recoverable backup: ${dbRecoveryCandidateReady ? 'yes' : 'no'}`,
      `candidate use: ${dbRecoveryCandidateModeLabel}`,
      `latest verified backup: ${dbRecoveryLatestVerifiedBackupPath || '-'}`,
      `latest verified backup at: ${dbRecoveryLatestVerifiedBackupAt > 0 ? formatSettingsDateTime(dbRecoveryLatestVerifiedBackupAt) : '-'}`
    ]
  }, [
    dbRecoveryCandidateModeLabel,
    dbRecoveryCandidateReady,
    dbRecoveryPendingState,
    dbRecoveryLatestVerifiedBackupAt,
    dbRecoveryLatestVerifiedBackupPath,
    dbRecoveryStateKnown,
    dbRecoveryStatus,
    shadowRestartMessage
  ])
  const dbRecoveryDetailLines = [
    dbRecoveryPendingState ? `pending: ${shadowRestartMessage}` : '',
    dbRecoveryCandidatePath ? `candidate path: ${dbRecoveryCandidatePath}` : '',
    dbRecoveryCandidateSourcePath ? `source path: ${dbRecoveryCandidateSourcePath}` : '',
    dbRecoveryCandidateClassReason ? `use reason: ${dbRecoveryCandidateClassReason}` : '',
    dbRecoveryCandidateMessage ? `message: ${dbRecoveryCandidateMessage}` : ''
  ].filter(Boolean)
  const dbRecoveryDetailWrappedLines = dbRecoveryDetailLines.flatMap((line) =>
    wrapText(line, Math.max(24, panelContentWidth))
  )
  const dbRecoveryShadowStateKnown = Boolean(state.db_recovery_shadow_state_known)
  const dbRecoveryShadowCandidatePath = String(state.db_recovery_shadow_candidate_path || '').trim()
  const dbRecoveryShadowStatusRaw = String(state.db_recovery_shadow_status || '').trim()
  const dbRecoveryShadowActed = Math.max(0, Number(state.db_recovery_shadow_acted || 0))
  const dbRecoveryShadowResolved = Math.max(0, Number(state.db_recovery_shadow_resolved || 0))
  const dbRecoveryShadowTotalPnlUsd = state.db_recovery_shadow_total_pnl_usd
  const dbRecoveryShadowReturnPct = state.db_recovery_shadow_return_pct
  const dbRecoveryShadowProfitFactor = state.db_recovery_shadow_profit_factor
  const dbRecoveryShadowExpectancyUsd = state.db_recovery_shadow_expectancy_usd
  const dbRecoveryShadowDataWarning = String(state.db_recovery_shadow_data_warning || '').trim()
  const dbRecoveryShadowSegmentTotal = Math.max(0, Number(state.db_recovery_shadow_segment_total || 0))
  const dbRecoveryShadowSegmentReadyCount = Math.max(0, Number(state.db_recovery_shadow_segment_ready_count || 0))
  const dbRecoveryShadowSegmentBlockedCount = Math.max(0, Number(state.db_recovery_shadow_segment_blocked_count || 0))
  const dbRecoveryShadowBlockReason = String(state.db_recovery_shadow_block_reason || '').trim()
  const dbRecoveryShadowStatus = !dbRecoveryShadowStateKnown
    ? 'checking'
    : dbRecoveryPendingState
      ? 'pending_restart'
      : dbRecoveryShadowStatusRaw || (dbRecoveryShadowCandidatePath ? 'pending_evaluation' : 'idle')
  const dbRecoveryShadowMigratedEvaluation =
    isMigratedRecoveryShadowStatus(dbRecoveryShadowStatusRaw)
    || /migrated temp clone/i.test(dbRecoveryShadowDataWarning)
    || /migrated temp clone/i.test(dbRecoveryShadowBlockReason)
  const dbRecoveryShadowColor = !dbRecoveryShadowStateKnown
    ? theme.dim
    : dbRecoveryPendingState
      ? theme.yellow
      : /block|fail|error|corrupt|invalid/i.test(dbRecoveryShadowStatus)
      ? theme.red
      : dbRecoveryShadowMigratedEvaluation || /migrated|legacy_migrated|mixed/i.test(dbRecoveryShadowStatus)
        ? theme.yellow
        : /ready|ok|healthy|positive/i.test(dbRecoveryShadowStatus)
          ? theme.green
          : dbRecoveryShadowCandidatePath
            ? theme.yellow
            : theme.dim
  const dbRecoveryShadowEvaluationLabel = !dbRecoveryShadowStateKnown
    ? 'checking'
    : dbRecoveryPendingState
      ? 'restart pending'
      : dbRecoveryShadowMigratedEvaluation
      ? 'migrated temp clone'
      : dbRecoveryShadowCandidatePath
        ? 'verified backup candidate'
        : 'no candidate'
  const dbRecoveryShadowMigrationNote = dbRecoveryShadowMigratedEvaluation
    ? 'evaluated from a migrated temp clone; the backup file itself was not modified'
    : ''
  const dbRecoveryShadowSummaryLines = useMemo(() => {
    if (!dbRecoveryShadowStateKnown) {
      return ['bot has not published recovery-candidate shadow evaluation yet']
    }
    if (dbRecoveryPendingState) {
      return [
        `status: ${dbRecoveryShadowStatus}`,
        shadowRestartMessage,
        'recovery-candidate shadow evaluation stays informational until the queued shadow restart or recovery completes'
      ]
    }
    if (!dbRecoveryShadowCandidatePath) {
      return [
        'no recovery-candidate shadow data is currently available',
        'this section reflects the verified backup candidate, not active runtime data'
      ]
    }
    const summaryParts = [
      `candidate use: ${dbRecoveryCandidateModeLabel}`,
      `status: ${dbRecoveryShadowStatus}`,
      `evaluation: ${dbRecoveryShadowEvaluationLabel}`,
      `candidate path: ${dbRecoveryShadowCandidatePath}`,
      `acted/resolved: ${formatNumber(dbRecoveryShadowActed)}/${formatNumber(dbRecoveryShadowResolved)}`,
      `pnl: ${dbRecoveryShadowTotalPnlUsd == null ? '-' : `$${formatNumber(dbRecoveryShadowTotalPnlUsd)}`}`,
      `return: ${formatSettingsPercent(dbRecoveryShadowReturnPct)}`,
      `profit factor: ${formatNumber(dbRecoveryShadowProfitFactor, 2)}`,
      `expectancy: ${dbRecoveryShadowExpectancyUsd == null ? '-' : `$${formatNumber(dbRecoveryShadowExpectancyUsd)}`}`,
      `segments ready: ${formatNumber(dbRecoveryShadowSegmentReadyCount)}/${formatNumber(dbRecoveryShadowSegmentTotal)}`,
      `segments blocked: ${formatNumber(dbRecoveryShadowSegmentBlockedCount)}`,
      'backup-candidate shadow data only; not active runtime data',
      dbRecoveryShadowMigrationNote
    ]
    return summaryParts.filter(Boolean)
  }, [
    dbRecoveryShadowActed,
    dbRecoveryShadowCandidatePath,
    dbRecoveryCandidateModeLabel,
    dbRecoveryShadowExpectancyUsd,
    dbRecoveryShadowEvaluationLabel,
    dbRecoveryShadowMigrationNote,
    dbRecoveryPendingState,
    dbRecoveryShadowProfitFactor,
    dbRecoveryShadowResolved,
    dbRecoveryShadowSegmentBlockedCount,
    dbRecoveryShadowSegmentReadyCount,
    dbRecoveryShadowSegmentTotal,
    dbRecoveryShadowStateKnown,
    dbRecoveryShadowStatus,
    dbRecoveryShadowTotalPnlUsd,
    dbRecoveryShadowReturnPct,
    shadowRestartMessage
  ])
  const dbRecoveryShadowDetailLines = [
    dbRecoveryPendingState ? `pending: ${shadowRestartMessage}` : '',
    dbRecoveryShadowDataWarning ? `warning: ${dbRecoveryShadowDataWarning}` : '',
    dbRecoveryShadowBlockReason ? `block reason: ${dbRecoveryShadowBlockReason}` : ''
  ].filter(Boolean)
  const dbRecoveryShadowDetailWrappedLines = dbRecoveryShadowDetailLines.flatMap((line) =>
    wrapText(line, Math.max(24, panelContentWidth))
  )
  const dbRecoveryShadowSegmentSummaryLines = useMemo(() => {
    if (!dbRecoveryShadowStateKnown || !dbRecoveryShadowCandidatePath) {
      return []
    }
    const raw = String(state.db_recovery_shadow_segment_summary_json || '').trim()
    if (!raw) {
      return []
    }
    try {
      return segmentSummaryLinesFromJson(raw)
    } catch {
      return ['segment summary: unavailable']
    }
  }, [dbRecoveryShadowCandidatePath, dbRecoveryShadowStateKnown, state.db_recovery_shadow_segment_summary_json])
  const botStateStats: SummaryStat[] = [
    {label: 'Mode', value: (state.mode || 'unknown').toUpperCase(), color: state.mode === 'live' ? theme.green : theme.dim},
    {label: 'Service', value: startupRecoveryOnly ? 'RECOVERY-ONLY' : startupFailed ? 'FAILED' : 'RUNNING', color: startupRecoveryOnly ? theme.yellow : startupFailed ? theme.red : theme.green},
    {label: 'Wallets', value: String(state.n_wallets || 0)},
    {label: 'Poll int', value: state.poll_interval ? `${state.poll_interval}s` : '-'},
    {label: 'Bankroll', value: state.bankroll_usd != null ? `$${formatNumber(state.bankroll_usd)}` : '-'},
    {label: 'Shadow gate', value: shadowGateStatus, color: shadowGateColor}
  ]
  const databaseStats: SummaryStat[] = [
    {label: 'Rows', value: String(counts[0]?.n || 0)},
    {label: 'Started', value: formatSettingsDateTime(state.started_at)},
    {label: 'Last poll', value: formatSettingsTime(state.last_poll_at)},
    {label: 'Duration', value: state.last_poll_duration_s != null ? `${formatNumber(state.last_poll_duration_s)}s` : '-'},
    {label: 'Integrity', value: dbIntegrityStatus, color: dbIntegrityColor}
  ]

  return (
    <InkBox flexDirection="column" width="100%">
      <InkBox flexDirection={stacked ? 'column' : 'row'}>
        <SettingsSummaryBox title="Bot State" width={topBoxWidth} items={botStateStats} columnCount={topBoxColumnCount} />
        {!stacked ? <InkBox width={topRowGap} /> : <InkBox height={1} />}
        <SettingsSummaryBox title="Database" width={topBoxWidth} items={databaseStats} columnCount={topBoxColumnCount} />
      </InkBox>

      <InkBox marginTop={1} flexDirection={stacked ? 'column' : 'row'} width="100%">
        <Box title="Editable Config" width={stacked ? '100%' : configBoxWidth} accent>
          <InkBox width="100%">
            {configColumns.map((column, columnIndex) => (
              <React.Fragment key={`config-column-${columnIndex}`}>
                <InkBox flexDirection="column" flexGrow={1}>
                  {column.map(({field, index}) => {
                    const selected = editor.focusArea === 'config' && index === safeSelectedIndex
                    const currentValue = editor.values[field.key] || field.defaultValue
                    const shownValue =
                      selected && editor.isEditing
                        ? `${editor.draft || ''}_`
                        : formatEditableConfigValue(field, currentValue)
                    const label = `${selected ? '>' : ' '} ${field.label}`
                    const labelColor = selected ? theme.accent : theme.dim
                    const valueColor =
                      selected && editor.isEditing
                        ? theme.accent
                        : field.kind === 'bool' && currentValue.toLowerCase() === 'true'
                          ? theme.green
                          : theme.white
                    const rowBackground = selected ? selectedRowBackground : undefined

                    return (
                      <InkBox key={field.key} width={configColumnWidth}>
                        <Text color={labelColor} backgroundColor={rowBackground} bold={selected}>
                          {fit(label, configLabelWidth)}
                        </Text>
                        <Text backgroundColor={rowBackground}> </Text>
                        <Text color={valueColor} backgroundColor={rowBackground} bold={selected}>
                          {fitRight(truncate(shownValue, configValueWidth), configValueWidth)}
                        </Text>
                      </InkBox>
                    )
                  })}
                </InkBox>
                {columnIndex < configColumns.length - 1 ? <InkBox width={2} /> : null}
              </React.Fragment>
            ))}
          </InkBox>

          <InkBox flexDirection="column" marginTop={1}>
            <Text color={theme.dim}>
              {truncate(
                `Showing ${configPageStart + 1}-${Math.min(configPageStart + configPageSize, editableConfigFields.length)} of ${editableConfigFields.length}`,
                helperWidth
              )}
            </Text>
            {configHelperLines.map((line, index) => (
              <Text key={`config-status-${index}`} color={configHelperColor}>
                {line}
              </Text>
            ))}
          </InkBox>
        </Box>

        {!stacked ? <InkBox width={middleRowGap} /> : <InkBox height={1} />}

        <InkBox
          borderStyle="round"
          borderColor={theme.red}
          flexDirection="column"
          width={stacked ? '100%' : undefined}
          flexGrow={stacked ? 0 : 1}
          flexShrink={1}
          paddingX={1}
        >
          <InkBox>
            <Text color={theme.red} bold>Danger Zone</Text>
          </InkBox>

          {editor.dangerConfirm ? (
            <>
              <Text color={theme.yellow} bold>{truncate(dangerHeaderText, dangerContentWidth)}</Text>
              <InkBox flexDirection="column" marginTop={1}>
                {editor.dangerConfirm.options.map((option, index) => {
                  const selected = index === editor.dangerConfirm?.selectedIndex
                  const rowBackground = selected ? selectedRowBackground : undefined
                  const label = `${selected ? '>' : ' '} ${option.label}`
                  return (
                    <InkBox key={`${editor.dangerConfirm?.actionId}-${option.id}`} width="100%">
                      <Text color={selected ? theme.accent : theme.white} backgroundColor={rowBackground} bold={selected}>
                        {fit(label, dangerContentWidth)}
                      </Text>
                    </InkBox>
                  )
                })}
              </InkBox>
            </>
          ) : (
            <>
              {dangerActions.map((action, index) => {
                const selected = editor.focusArea === 'danger' && index === safeDangerIndex
                const rowBackground = selected ? selectedRowBackground : undefined
                const value =
                  action.id === 'live_trading' && (startupRecoveryOnly || shadowRestartPending)
                    ? 'BLOCKED'
                    : shadowRestartPending && (action.id === 'restart_shadow' || action.id === 'recover_db')
                      ? 'PENDING'
                    : action.id === 'recover_db'
                      ? dbRecoveryCandidateModeShortLabel
                      : action.value(envData.rawValues)
                const valueColor =
                  action.id === 'live_trading'
                    ? startupRecoveryOnly
                      ? theme.red
                      : shadowRestartPending
                        ? theme.yellow
                      : liveTradingEnabled ? theme.green : theme.red
                    : shadowRestartPending && (action.id === 'restart_shadow' || action.id === 'recover_db')
                      ? theme.yellow
                    : action.id === 'recover_db'
                      ? dbRecoveryCandidateModeColor
                      : theme.yellow

                return (
                  <InkBox key={action.id} width="100%">
                    <Text color={selected ? theme.accent : theme.dim} backgroundColor={rowBackground} bold={selected}>
                      {fit(`${selected ? '>' : ' '} ${action.label}`, dangerLabelWidth)}
                    </Text>
                    <Text backgroundColor={rowBackground}> </Text>
                    <Text color={valueColor} backgroundColor={rowBackground} bold={selected}>
                      {fitRight(truncate(value, dangerValueWidth), dangerValueWidth)}
                    </Text>
                  </InkBox>
                )
              })}
            </>
          )}

          <InkBox flexDirection="column" marginTop={1}>
            {startupBlockedHelperLines.map((line, index) => (
              <Text key={`danger-blocked-${index}`} color={theme.yellow}>
                {line}
              </Text>
            ))}
            {dangerHelperLines.map((line, index) => (
              <Text key={`danger-status-${index}`} color={statusColor}>
                {line}
              </Text>
            ))}
          </InkBox>
        </InkBox>
      </InkBox>

      <InkBox marginTop={1}>
        <Box title="Environment">
          {envRows.length || envData.watchedWallets.length ? (
            <>
              {envRows.map((row) => (
                <StatRow key={row.key} label={row.key} value={row.value} />
              ))}
              <InkBox flexDirection="column" marginTop={envRows.length ? 1 : 0}>
                <Text color={theme.dim}>
                  {truncate(`WATCHED_WALLETS (${envData.watchedWallets.length})`, helperWidth)}
                </Text>
                {visibleWallets.length ? (
                  <>
                    <InkBox width="100%">
                      <Text color={theme.dim}>{fit('#', walletIndexWidth)}</Text>
                      <Text color={theme.dim}> </Text>
                      <Text color={theme.dim}>{fit('USERNAME', walletUsernameWidth)}</Text>
                      <Text color={theme.dim}> </Text>
                      <Text color={theme.dim}>{fit('WALLET', walletAddressWidth)}</Text>
                    </InkBox>
                    {visibleWallets.map((wallet, index) => (
                      <InkBox key={wallet} width="100%">
                        <Text color={theme.white}>{fit(`${index + 1}.`, walletIndexWidth)}</Text>
                        <Text> </Text>
                        <Text color={theme.white}>
                          {fit(usernames.get(wallet.toLowerCase()) || shortAddress(wallet), walletUsernameWidth)}
                        </Text>
                        <Text> </Text>
                        <Text color={theme.white}>{fit(wallet, walletAddressWidth)}</Text>
                      </InkBox>
                    ))}
                  </>
                ) : (
                  <Text color={theme.dim}>No watched wallets configured.</Text>
                )}
                {hiddenWalletCount > 0 ? (
                  <Text color={theme.dim}>
                    {truncate(`... and ${hiddenWalletCount} more`, helperWidth)}
                  </Text>
                ) : null}
              </InkBox>
            </>
          ) : (
            <Text color={theme.dim}>No active env file found yet.</Text>
          )}
        </Box>
      </InkBox>
    </InkBox>
  )
}
