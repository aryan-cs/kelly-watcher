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

const CONFIG_BLURBS: Record<string, string> = {
  POLL_INTERVAL_SECONDS: 'Sets seconds between wallet polling cycles.',
  MAX_MARKET_HORIZON: 'Limits how far out copied markets may resolve.',
  WALLET_INACTIVITY_LIMIT: 'Drops wallets after too much source inactivity.',
  WALLET_SLOW_DROP_MAX_TRACKING_AGE: 'Drops slow wallets after this tracking age.',
  WALLET_PERFORMANCE_DROP_MIN_TRADES: 'Requires this many trades before performance-based drops.',
  WALLET_PERFORMANCE_DROP_MAX_WIN_RATE: 'Drops wallets below this profile win rate.',
  WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN: 'Drops wallets below this average profile return.',
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
  MAX_BET_FRACTION: 'Caps each bet as bankroll fraction.',
  MAX_TOTAL_OPEN_EXPOSURE_FRACTION: 'Caps total bankroll deployed across open positions.',
  EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION: 'Raises exposure cap for trusted wallets only.',
  DUPLICATE_SIDE_OVERRIDE_MIN_SKIPS: 'Needs this many skips before duplicate adds.',
  DUPLICATE_SIDE_OVERRIDE_MIN_AVG_RETURN: 'Needs this return before duplicate adds qualify.',
  EXPOSURE_OVERRIDE_MIN_SKIPS: 'Needs this many skips before trusted exposure applies.',
  EXPOSURE_OVERRIDE_MIN_AVG_RETURN: 'Needs this return before trusted exposure applies.',
  SHADOW_BANKROLL_USD: 'Sets the paper bankroll for tracker mode.',
  MAX_DAILY_LOSS_PCT: 'Stops new entries after this daily drawdown.',
  RETRAIN_BASE_CADENCE: 'Sets how often scheduled retraining runs.',
  RETRAIN_HOUR_LOCAL: 'Sets the local hour for scheduled retraining.',
  RETRAIN_EARLY_CHECK_INTERVAL: 'Checks for early retraining on this interval.',
  RETRAIN_MIN_NEW_LABELS: 'Needs this many new labels for early retraining.',
  RETRAIN_MIN_SAMPLES: 'Needs this many samples before retraining starts.'
}

const DANGER_ACTION_BLURBS: Record<string, string> = {
  live_trading: 'Toggles real-money mode in config.',
  restart_shadow: 'Resets shadow state, history, and models.'
}

const DANGER_OPTION_BLURBS: Record<string, string> = {
  confirm_disable: 'Turns live trading off in config.',
  confirm_enable: 'Turns live trading on in config.',
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
  const configColumns = useMemo(
    () => splitIntoColumns(editableConfigFields.map((field, index) => ({field, index})), configColumnCount),
    [configColumnCount]
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
  const configHelperLine = CONFIG_BLURBS[selectedField?.key || ''] || ''
  const configHelperLines = wrapText(configHelperLine, helperWidth)
  const dangerHeaderText = editor.dangerConfirm
    ? editor.dangerConfirm.title
    : selectedDangerAction?.label || 'Danger Zone'
  const selectedDangerOption = editor.dangerConfirm?.options[editor.dangerConfirm.selectedIndex]
  const dangerBlurb =
    DANGER_OPTION_BLURBS[selectedDangerOption?.id || '']
    || DANGER_ACTION_BLURBS[editor.dangerConfirm?.actionId || selectedDangerAction?.id || '']
    || ''
  const dangerHelperLines = wrapText(dangerBlurb, dangerContentWidth)
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
  const botStateStats: SummaryStat[] = [
    {label: 'Mode', value: (state.mode || 'unknown').toUpperCase(), color: state.mode === 'live' ? theme.green : theme.dim},
    {label: 'Wallets', value: String(state.n_wallets || 0)},
    {label: 'Poll int', value: state.poll_interval ? `${state.poll_interval}s` : '-'},
    {label: 'Bankroll', value: state.bankroll_usd != null ? `$${formatNumber(state.bankroll_usd)}` : '-'}
  ]
  const databaseStats: SummaryStat[] = [
    {label: 'Rows', value: String(counts[0]?.n || 0)},
    {label: 'Started', value: formatSettingsDateTime(state.started_at)},
    {label: 'Last poll', value: formatSettingsTime(state.last_poll_at)},
    {label: 'Duration', value: state.last_poll_duration_s != null ? `${formatNumber(state.last_poll_duration_s)}s` : '-'}
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
            <Text> </Text>
            {configHelperLines.map((line, index) => (
              <Text key={`config-status-${index}`} color={statusColor}>
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
                const value = action.value(envData.rawValues)
                const valueColor =
                  action.id === 'live_trading'
                    ? liveTradingEnabled ? theme.green : theme.red
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
