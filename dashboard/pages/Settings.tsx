import fs from 'fs'
import React, {useMemo} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {StatRow} from '../components/StatRow.js'
import {
  editableConfigFields,
  formatEditableConfigValue,
  type EditableConfigValues
} from '../configEditor.js'
import {fit, fitRight, formatNumber, shortAddress, truncate} from '../format.js'
import {isPlaceholderUsername, readIdentityMap} from '../identities.js'
import {envExamplePath, envPath} from '../paths.js'
import {rowsForHeight, stackPanels} from '../responsive.js'
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
}

interface SettingsProps {
  editor: SettingsEditorState
}

const COUNT_SQL = `SELECT COUNT(*) AS n FROM trade_log`

interface EnvData {
  rows: Array<{key: string; value: string}>
  watchedWallets: string[]
}

function readEnvData(): EnvData {
  const path = fs.existsSync(envPath) ? envPath : envExamplePath
  try {
    return fs
      .readFileSync(path, 'utf8')
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#') && line.includes('='))
      .reduce<EnvData>((acc, line) => {
        const [key, ...rest] = line.split('=')
        const value = rest.join('=')
        if (key === 'WATCHED_WALLETS') {
          acc.watchedWallets = value
            .split(',')
            .map((wallet) => wallet.trim())
            .filter(Boolean)
          return acc
        }
        const redacted = /(KEY|TOKEN|PRIVATE)/.test(key) ? '************' : (value || 'unset')
        acc.rows.push({key, value: redacted})
        return acc
      }, {rows: [], watchedWallets: []})
  } catch {
    return {rows: [], watchedWallets: []}
  }
}

export function Settings({editor}: SettingsProps) {
  const terminal = useTerminalSize()
  const stacked = stackPanels(terminal.width)
  const state = useBotState()
  const counts = useQuery<CountRow>(COUNT_SQL)
  const events = useEventStream(1000)
  const envData = readEnvData()
  const environmentBudget = rowsForHeight(terminal.height, stacked ? 28 : 22, 6, 14)
  const walletSectionHeaderRows = envData.watchedWallets.length ? 2 : 0
  const maxWalletLines = envData.watchedWallets.length ? Math.max(2, Math.min(6, Math.floor(environmentBudget / 2))) : 0
  const walletSectionRows = envData.watchedWallets.length
    ? walletSectionHeaderRows + Math.min(envData.watchedWallets.length, maxWalletLines) + (envData.watchedWallets.length > maxWalletLines ? 1 : 0)
    : 0
  const envRows = envData.rows.slice(0, Math.max(0, environmentBudget - walletSectionRows))
  const visibleWallets = envData.watchedWallets.slice(0, maxWalletLines)
  const hiddenWalletCount = Math.max(0, envData.watchedWallets.length - visibleWallets.length)
  const selectedField = editableConfigFields[editor.selectedIndex]
  const helperWidth = Math.max(24, terminal.width - 14)
  const statusColor =
    editor.statusTone === 'error' ? theme.red : editor.statusTone === 'success' ? theme.green : theme.dim
  const usernames = useMemo(() => {
    const lookup = readIdentityMap()
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
  }, [events])
  const walletTableWidth = Math.max(24, helperWidth - 2)
  const walletIndexWidth = Math.max(3, String(Math.max(1, envData.watchedWallets.length)).length + 1)
  const walletAddressWidth = Math.max(18, Math.min(42, Math.floor(walletTableWidth * 0.62)))
  const walletUsernameWidth = Math.max(8, walletTableWidth - walletIndexWidth - walletAddressWidth - 2)
  const configRowWidth = Math.max(30, helperWidth - 2)
  const configValueWidth = Math.max(12, Math.min(30, Math.floor(configRowWidth * 0.42)))
  const configLabelWidth = Math.max(10, configRowWidth - configValueWidth - 1)
  const selectedRowBackground = selectionBackgroundColor(terminal.backgroundColor)

  return (
    <InkBox flexDirection="column" width="100%">
      <InkBox flexDirection={stacked ? 'column' : 'row'}>
        <Box title="Bot State" width={stacked ? '100%' : '50%'}>
          <StatRow label="Mode" value={(state.mode || 'unknown').toUpperCase()} color={state.mode === 'live' ? theme.green : theme.dim} />
          <StatRow label="Wallets watched" value={String(state.n_wallets || 0)} />
          <StatRow label="Poll interval" value={state.poll_interval ? `${state.poll_interval}s` : '-'} />
          <StatRow label="Bankroll" value={state.bankroll_usd != null ? `$${formatNumber(state.bankroll_usd)}` : '-'} />
        </Box>
        {!stacked ? <InkBox width={1} /> : <InkBox height={1} />}
        <Box title="Database" width={stacked ? '100%' : '50%'}>
          <StatRow label="trade_log rows" value={String(counts[0]?.n || 0)} />
          <StatRow label="Started at" value={state.started_at ? new Date(state.started_at * 1000).toLocaleString() : '-'} />
          <StatRow label="Last poll" value={state.last_poll_at ? new Date(state.last_poll_at * 1000).toLocaleTimeString() : '-'} />
          <StatRow label="Poll duration" value={state.last_poll_duration_s != null ? `${formatNumber(state.last_poll_duration_s)}s` : '-'} />
        </Box>
      </InkBox>

      <InkBox marginTop={1}>
        <Box title="Editable Config" accent>
          {editableConfigFields.map((field, index) => {
            const selected = index === editor.selectedIndex
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
              <InkBox key={field.key} width="100%">
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

          <InkBox flexDirection="column" marginTop={1}>
            <Text color={theme.dim}>{truncate(`${selectedField.key} - ${selectedField.description}`, helperWidth)}</Text>
            <Text color={statusColor}>
              {truncate(
                editor.statusMessage ||
                  (editor.isEditing
                    ? 'Type a value, then press Enter to save or Esc to cancel.'
                    : 'Use j/k or arrows to select. Press e or Enter to edit.'),
                helperWidth
              )}
            </Text>
            <Text color={theme.dim}>
              {editor.isEditing ? 'editing mode active' : 'poll interval applies live; most other changes need a bot restart'}
            </Text>
          </InkBox>
        </Box>
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
            <Text color={theme.dim}>No .env file found yet.</Text>
          )}
        </Box>
      </InkBox>
    </InkBox>
  )
}
