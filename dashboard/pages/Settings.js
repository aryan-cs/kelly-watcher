import fs from 'fs';
import React, { useMemo } from 'react';
import { Box as InkBox, Text } from 'ink';
import { Box } from '../components/Box.js';
import { StatRow } from '../components/StatRow.js';
import { editableConfigFields, formatEditableConfigValue, readEnvValues } from '../configEditor.js';
import { fit, fitRight, formatNumber, shortAddress, truncate, wrapText } from '../format.js';
import { isPlaceholderUsername, readIdentityMap } from '../identities.js';
import { envExamplePath, envPath } from '../paths.js';
import { rowsForHeight, stackPanels } from '../responsive.js';
import { dangerActions, isLiveTradingEnabled } from '../settingsDanger.js';
import { useTerminalSize } from '../terminal.js';
import { selectionBackgroundColor, theme } from '../theme.js';
import { useBotState } from '../useBotState.js';
import { useQuery } from '../useDb.js';
import { useEventStream } from '../useEventStream.js';
const COUNT_SQL = `SELECT COUNT(*) AS n FROM trade_log`;
function readEnvData() {
    const path = fs.existsSync(envPath) ? envPath : envExamplePath;
    const rawValues = readEnvValues();
    try {
        return fs
            .readFileSync(path, 'utf8')
            .split('\n')
            .map((line) => line.trim())
            .filter((line) => line && !line.startsWith('#') && line.includes('='))
            .reduce((acc, line) => {
            const [key, ...rest] = line.split('=');
            const value = rest.join('=');
            if (key === 'WATCHED_WALLETS') {
                acc.watchedWallets = value
                    .split(',')
                    .map((wallet) => wallet.trim())
                    .filter(Boolean);
                return acc;
            }
            const redacted = /(KEY|TOKEN|PRIVATE)/.test(key) ? '************' : (value || 'unset');
            acc.rows.push({ key, value: redacted });
            return acc;
        }, { rows: [], watchedWallets: [], rawValues });
    }
    catch {
        return { rows: [], watchedWallets: [], rawValues };
    }
}
function splitIntoColumns(items, columnCount) {
    if (columnCount <= 1 || items.length <= 1) {
        return [items];
    }
    const perColumn = Math.ceil(items.length / columnCount);
    const columns = [];
    for (let index = 0; index < items.length; index += perColumn) {
        columns.push(items.slice(index, index + perColumn));
    }
    return columns;
}
function dangerToneColor(tone) {
    if (tone === 'error')
        return theme.red;
    if (tone === 'success')
        return theme.green;
    return theme.dim;
}
export function Settings({ editor }) {
    const terminal = useTerminalSize();
    const stacked = stackPanels(terminal.width);
    const state = useBotState();
    const counts = useQuery(COUNT_SQL);
    const events = useEventStream(1000);
    const envData = readEnvData();
    const environmentBudget = rowsForHeight(terminal.height, stacked ? 40 : 30, 6, 14);
    const walletSectionHeaderRows = envData.watchedWallets.length ? 2 : 0;
    const maxWalletLines = envData.watchedWallets.length ? Math.max(2, Math.min(6, Math.floor(environmentBudget / 2))) : 0;
    const walletSectionRows = envData.watchedWallets.length
        ? walletSectionHeaderRows + Math.min(envData.watchedWallets.length, maxWalletLines) + (envData.watchedWallets.length > maxWalletLines ? 1 : 0)
        : 0;
    const envRows = envData.rows.slice(0, Math.max(0, environmentBudget - walletSectionRows));
    const visibleWallets = envData.watchedWallets.slice(0, maxWalletLines);
    const hiddenWalletCount = Math.max(0, envData.watchedWallets.length - visibleWallets.length);
    const safeSelectedIndex = Math.max(0, Math.min(editor.selectedIndex, Math.max(editableConfigFields.length - 1, 0)));
    const selectedField = editableConfigFields[safeSelectedIndex];
    const safeDangerIndex = Math.max(0, Math.min(editor.dangerSelectedIndex, Math.max(dangerActions.length - 1, 0)));
    const selectedDangerAction = dangerActions[safeDangerIndex];
    const panelContentWidth = Math.max(24, terminal.width - 10);
    const configBoxWidth = stacked ? undefined : Math.max(56, Math.floor((terminal.width - 11) * 0.68));
    const dangerBoxWidth = stacked ? undefined : Math.max(28, terminal.width - (configBoxWidth || 0) - 11);
    const configContentWidth = Math.max(28, (configBoxWidth || terminal.width - 10) - 4);
    const dangerContentWidth = Math.max(24, (dangerBoxWidth || terminal.width - 10) - 4);
    const configColumnCount = configContentWidth >= 78 ? 2 : 1;
    const configColumns = useMemo(() => splitIntoColumns(editableConfigFields.map((field, index) => ({ field, index })), configColumnCount), [configColumnCount]);
    const configColumnWidth = configColumnCount === 1
        ? configContentWidth
        : Math.max(24, Math.floor((configContentWidth - 2) / configColumnCount));
    const configValueWidth = Math.max(10, Math.min(18, Math.floor(configColumnWidth * 0.36)));
    const configLabelWidth = Math.max(12, configColumnWidth - configValueWidth - 1);
    const dangerValueWidth = Math.max(8, Math.min(12, Math.floor(dangerContentWidth * 0.28)));
    const dangerLabelWidth = Math.max(12, dangerContentWidth - dangerValueWidth - 1);
    const helperWidth = Math.max(24, configContentWidth);
    const selectedRowBackground = selectionBackgroundColor(terminal.backgroundColor);
    const statusColor = dangerToneColor(editor.statusTone);
    const configDefaultStatusMessage = editor.isEditing
        ? 'Type a value. Up/down cycles preset fields. Enter saves. Esc cancels.'
        : 'Use up/down to move. Enter edits config. Continue down into Danger Zone.';
    const dangerDefaultStatusMessage = editor.dangerConfirm
        ? 'Use up/down to choose an action. Enter confirms. Esc cancels.'
        : 'Use up/down to reach this box. Enter opens the selected danger action.';
    const configDescription = selectedField ? `${selectedField.key} - ${selectedField.description}` : '';
    const configStatusMessage = editor.focusArea === 'config' || editor.isEditing
        ? (editor.statusMessage || '').trim() || configDefaultStatusMessage
        : configDefaultStatusMessage;
    const configDescriptionLines = wrapText(configDescription, helperWidth);
    const configStatusLines = wrapText(configStatusMessage, helperWidth);
    const dangerHeaderText = editor.dangerConfirm
        ? editor.dangerConfirm.title
        : selectedDangerAction?.label || 'Danger Zone';
    const dangerDescription = editor.dangerConfirm
        ? editor.dangerConfirm.message
        : selectedDangerAction?.description || '';
    const dangerStatusMessage = editor.focusArea === 'danger' || editor.dangerConfirm
        ? (editor.statusMessage || '').trim() || dangerDefaultStatusMessage
        : dangerDefaultStatusMessage;
    const dangerDescriptionLines = wrapText(dangerDescription, dangerContentWidth);
    const dangerStatusLines = wrapText(dangerStatusMessage, dangerContentWidth);
    const usernames = useMemo(() => {
        const lookup = readIdentityMap();
        for (let index = events.length - 1; index >= 0; index -= 1) {
            const event = events[index];
            const wallet = event.trader?.trim().toLowerCase();
            const username = event.username?.trim();
            if (!wallet || !username || isPlaceholderUsername(username, wallet) || lookup.has(wallet)) {
                continue;
            }
            lookup.set(wallet, username);
        }
        return lookup;
    }, [events]);
    const walletTableWidth = Math.max(24, panelContentWidth);
    const walletIndexWidth = Math.max(3, String(Math.max(1, envData.watchedWallets.length)).length + 1);
    const walletAddressWidth = Math.max(18, Math.min(42, Math.floor(walletTableWidth * 0.62)));
    const walletUsernameWidth = Math.max(8, walletTableWidth - walletIndexWidth - walletAddressWidth - 2);
    const liveTradingEnabled = isLiveTradingEnabled(envData.rawValues);
    return (React.createElement(InkBox, { flexDirection: "column", width: "100%" },
        React.createElement(InkBox, { flexDirection: stacked ? 'column' : 'row' },
            React.createElement(Box, { title: "Bot State", width: stacked ? '100%' : '50%' },
                React.createElement(StatRow, { label: "Mode", value: (state.mode || 'unknown').toUpperCase(), color: state.mode === 'live' ? theme.green : theme.dim }),
                React.createElement(StatRow, { label: "Wallets watched", value: String(state.n_wallets || 0) }),
                React.createElement(StatRow, { label: "Poll interval", value: state.poll_interval ? `${state.poll_interval}s` : '-' }),
                React.createElement(StatRow, { label: "Bankroll", value: state.bankroll_usd != null ? `$${formatNumber(state.bankroll_usd)}` : '-' })),
            !stacked ? React.createElement(InkBox, { width: 1 }) : React.createElement(InkBox, { height: 1 }),
            React.createElement(Box, { title: "Database", width: stacked ? '100%' : '50%' },
                React.createElement(StatRow, { label: "trade_log rows", value: String(counts[0]?.n || 0) }),
                React.createElement(StatRow, { label: "Started at", value: state.started_at ? new Date(state.started_at * 1000).toLocaleString() : '-' }),
                React.createElement(StatRow, { label: "Last poll", value: state.last_poll_at ? new Date(state.last_poll_at * 1000).toLocaleTimeString() : '-' }),
                React.createElement(StatRow, { label: "Poll duration", value: state.last_poll_duration_s != null ? `${formatNumber(state.last_poll_duration_s)}s` : '-' }))),
        React.createElement(InkBox, { marginTop: 1, flexDirection: stacked ? 'column' : 'row' },
            React.createElement(Box, { title: "Editable Config", width: stacked ? '100%' : configBoxWidth, accent: true },
                React.createElement(InkBox, { width: "100%" }, configColumns.map((column, columnIndex) => (React.createElement(React.Fragment, { key: `config-column-${columnIndex}` },
                    React.createElement(InkBox, { flexDirection: "column", flexGrow: 1 }, column.map(({ field, index }) => {
                        const selected = editor.focusArea === 'config' && index === safeSelectedIndex;
                        const currentValue = editor.values[field.key] || field.defaultValue;
                        const shownValue = selected && editor.isEditing
                            ? `${editor.draft || ''}_`
                            : formatEditableConfigValue(field, currentValue);
                        const label = `${selected ? '>' : ' '} ${field.label}`;
                        const labelColor = selected ? theme.accent : theme.dim;
                        const valueColor = selected && editor.isEditing
                            ? theme.accent
                            : field.kind === 'bool' && currentValue.toLowerCase() === 'true'
                                ? theme.green
                                : theme.white;
                        const rowBackground = selected ? selectedRowBackground : undefined;
                        return (React.createElement(InkBox, { key: field.key, width: configColumnWidth },
                            React.createElement(Text, { color: labelColor, backgroundColor: rowBackground, bold: selected }, fit(label, configLabelWidth)),
                            React.createElement(Text, { backgroundColor: rowBackground }, " "),
                            React.createElement(Text, { color: valueColor, backgroundColor: rowBackground, bold: selected }, fitRight(truncate(shownValue, configValueWidth), configValueWidth))));
                    })),
                    columnIndex < configColumns.length - 1 ? React.createElement(InkBox, { width: 2 }) : null)))),
                React.createElement(InkBox, { flexDirection: "column", marginTop: 1 },
                    configDescriptionLines.map((line, index) => (React.createElement(Text, { key: `config-desc-${index}`, color: theme.dim }, line))),
                    configStatusLines.map((line, index) => (React.createElement(Text, { key: `config-status-${index}`, color: statusColor }, line))))),
            !stacked ? React.createElement(InkBox, { width: 1 }) : React.createElement(InkBox, { height: 1 }),
            React.createElement(InkBox, { borderStyle: "round", borderColor: theme.red, flexDirection: "column", width: stacked ? '100%' : dangerBoxWidth, paddingX: 1 },
                React.createElement(InkBox, null,
                    React.createElement(Text, { color: theme.red, bold: true }, "Danger Zone")),
                editor.dangerConfirm ? (React.createElement(React.Fragment, null,
                    React.createElement(Text, { color: theme.yellow, bold: true }, truncate(dangerHeaderText, dangerContentWidth)),
                    dangerDescriptionLines.map((line, index) => (React.createElement(Text, { key: `danger-desc-${index}`, color: theme.dim }, line))),
                    React.createElement(InkBox, { flexDirection: "column", marginTop: 1 }, editor.dangerConfirm.options.map((option, index) => {
                        const selected = index === editor.dangerConfirm?.selectedIndex;
                        const rowBackground = selected ? selectedRowBackground : undefined;
                        const label = `${selected ? '>' : ' '} ${option.label}`;
                        return (React.createElement(InkBox, { key: `${editor.dangerConfirm?.actionId}-${option.id}`, width: "100%" },
                            React.createElement(Text, { color: selected ? theme.accent : theme.white, backgroundColor: rowBackground, bold: selected }, fit(label, dangerContentWidth))));
                    })))) : (React.createElement(React.Fragment, null, dangerActions.map((action, index) => {
                    const selected = editor.focusArea === 'danger' && index === safeDangerIndex;
                    const rowBackground = selected ? selectedRowBackground : undefined;
                    const value = action.value(envData.rawValues);
                    const valueColor = action.id === 'live_trading'
                        ? liveTradingEnabled ? theme.green : theme.red
                        : theme.yellow;
                    return (React.createElement(InkBox, { key: action.id, width: "100%" },
                        React.createElement(Text, { color: selected ? theme.accent : theme.dim, backgroundColor: rowBackground, bold: selected }, fit(`${selected ? '>' : ' '} ${action.label}`, dangerLabelWidth)),
                        React.createElement(Text, { backgroundColor: rowBackground }, " "),
                        React.createElement(Text, { color: valueColor, backgroundColor: rowBackground, bold: selected }, fitRight(truncate(value, dangerValueWidth), dangerValueWidth))));
                }))),
                React.createElement(InkBox, { flexDirection: "column", marginTop: 1 },
                    editor.dangerConfirm ? null : dangerDescriptionLines.map((line, index) => (React.createElement(Text, { key: `danger-help-${index}`, color: theme.dim }, line))),
                    dangerStatusLines.map((line, index) => (React.createElement(Text, { key: `danger-status-${index}`, color: statusColor }, line)))))),
        React.createElement(InkBox, { marginTop: 1 },
            React.createElement(Box, { title: "Environment" }, envRows.length || envData.watchedWallets.length ? (React.createElement(React.Fragment, null,
                envRows.map((row) => (React.createElement(StatRow, { key: row.key, label: row.key, value: row.value }))),
                React.createElement(InkBox, { flexDirection: "column", marginTop: envRows.length ? 1 : 0 },
                    React.createElement(Text, { color: theme.dim }, truncate(`WATCHED_WALLETS (${envData.watchedWallets.length})`, helperWidth)),
                    visibleWallets.length ? (React.createElement(React.Fragment, null,
                        React.createElement(InkBox, { width: "100%" },
                            React.createElement(Text, { color: theme.dim }, fit('#', walletIndexWidth)),
                            React.createElement(Text, { color: theme.dim }, " "),
                            React.createElement(Text, { color: theme.dim }, fit('USERNAME', walletUsernameWidth)),
                            React.createElement(Text, { color: theme.dim }, " "),
                            React.createElement(Text, { color: theme.dim }, fit('WALLET', walletAddressWidth))),
                        visibleWallets.map((wallet, index) => (React.createElement(InkBox, { key: wallet, width: "100%" },
                            React.createElement(Text, { color: theme.white }, fit(`${index + 1}.`, walletIndexWidth)),
                            React.createElement(Text, null, " "),
                            React.createElement(Text, { color: theme.white }, fit(usernames.get(wallet.toLowerCase()) || shortAddress(wallet), walletUsernameWidth)),
                            React.createElement(Text, null, " "),
                            React.createElement(Text, { color: theme.white }, fit(wallet, walletAddressWidth))))))) : (React.createElement(Text, { color: theme.dim }, "No watched wallets configured.")),
                    hiddenWalletCount > 0 ? (React.createElement(Text, { color: theme.dim }, truncate(`... and ${hiddenWalletCount} more`, helperWidth))) : null))) : (React.createElement(Text, { color: theme.dim }, "No .env file found yet."))))));
}
