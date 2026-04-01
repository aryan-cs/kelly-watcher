import React, { useMemo } from 'react';
import { Box as InkBox, Text } from 'ink';
import { Box } from '../components/Box.js';
import { StatRow } from '../components/StatRow.js';
import { editableConfigFields, formatEditableConfigValue, useDashboardConfig } from '../configEditor.js';
import { fit, fitRight, formatNumber, shortAddress, truncate, wrapText } from '../format.js';
import { isPlaceholderUsername, useIdentityMap } from '../identities.js';
import { rowsForHeight, stackPanels } from '../responsive.js';
import { dangerActions, isLiveTradingEnabled } from '../settingsDanger.js';
import { useTerminalSize } from '../terminal.js';
import { selectionBackgroundColor, theme } from '../theme.js';
import { useBotState } from '../useBotState.js';
import { useQuery } from '../useDb.js';
import { useEventStream } from '../useEventStream.js';
const COUNT_SQL = `SELECT COUNT(*) AS n FROM trade_log`;
function envDataFromConfig(config) {
    return {
        rows: config.rows,
        watchedWallets: config.watchedWallets,
        rawValues: config.safeValues
    };
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
function formatSettingsDateTime(timestamp) {
    if (!timestamp)
        return '-';
    return new Date(timestamp * 1000).toLocaleString([], {
        month: 'numeric',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit'
    });
}
function formatSettingsTime(timestamp) {
    if (!timestamp)
        return '-';
    return new Date(timestamp * 1000).toLocaleTimeString([], {
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit'
    });
}
const CONFIG_BLURBS = {
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
};
const DANGER_ACTION_BLURBS = {
    live_trading: 'Toggles real-money mode in config.',
    restart_shadow: 'Resets shadow state, history, and models.'
};
const DANGER_OPTION_BLURBS = {
    confirm_disable: 'Turns live trading off in config.',
    confirm_enable: 'Turns live trading on in config.',
    keep_active: 'Resets shadow data and keeps active wallets.',
    keep_all: 'Resets shadow data and keeps all wallets.',
    clear_all: 'Resets shadow data and clears watched wallets.',
    cancel: 'Leaves everything unchanged.'
};
function SettingsSummaryBox({ title, width, items, columnCount }) {
    const columns = splitIntoColumns(items, columnCount);
    const contentWidth = typeof width === 'number' ? Math.max(24, width - 4) : 32;
    const columnWidth = columnCount <= 1
        ? contentWidth
        : Math.max(16, Math.floor((contentWidth - (columnCount - 1) * 2) / columnCount));
    const valueWidth = Math.max(8, Math.min(16, Math.floor(columnWidth * 0.38)));
    const labelWidth = Math.max(8, columnWidth - valueWidth - 1);
    return (React.createElement(Box, { title: title, width: width },
        React.createElement(InkBox, { width: "100%", marginTop: 1, flexDirection: "column" },
            React.createElement(InkBox, { width: "100%" }, columns.map((column, columnIndex) => (React.createElement(React.Fragment, { key: `${title}-column-${columnIndex}` },
                React.createElement(InkBox, { flexDirection: "column", width: columnWidth }, column.map((item) => (React.createElement(InkBox, { key: `${title}-${item.label}`, width: columnWidth },
                    React.createElement(Text, { color: theme.dim }, fit(item.label, labelWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: item.color ?? theme.white }, fitRight(item.value, valueWidth)))))),
                columnIndex < columns.length - 1 ? React.createElement(InkBox, { width: 2 }) : null)))),
            React.createElement(Text, null, " "))));
}
export function Settings({ editor }) {
    const terminal = useTerminalSize();
    const stacked = stackPanels(terminal.width);
    const state = useBotState();
    const counts = useQuery(COUNT_SQL);
    const events = useEventStream(1000);
    const config = useDashboardConfig();
    const envData = useMemo(() => envDataFromConfig(config), [config]);
    const identityMap = useIdentityMap();
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
    const middleRowGap = stacked ? 0 : 2;
    const middleRowWidth = Math.max(24, terminal.width - 4);
    const configBoxWidth = stacked ? middleRowWidth : Math.max(56, Math.floor((middleRowWidth - middleRowGap) * 0.68));
    const dangerBoxWidth = stacked ? middleRowWidth : Math.max(28, middleRowWidth - configBoxWidth - middleRowGap);
    const configContentWidth = Math.max(28, configBoxWidth - 4);
    const dangerContentWidth = Math.max(24, dangerBoxWidth - 4);
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
    const configHelperLine = CONFIG_BLURBS[selectedField?.key || ''] || '';
    const dangerHeaderText = editor.dangerConfirm
        ? editor.dangerConfirm.title
        : selectedDangerAction?.label || 'Danger Zone';
    const selectedDangerOption = editor.dangerConfirm?.options[editor.dangerConfirm.selectedIndex];
    const dangerBlurb = DANGER_OPTION_BLURBS[selectedDangerOption?.id || '']
        || DANGER_ACTION_BLURBS[editor.dangerConfirm?.actionId || selectedDangerAction?.id || '']
        || '';
    const dangerHelperLines = wrapText(dangerBlurb, dangerContentWidth);
    const usernames = useMemo(() => {
        const lookup = new Map(identityMap);
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
    }, [events, identityMap]);
    const walletTableWidth = Math.max(24, panelContentWidth);
    const walletIndexWidth = Math.max(3, String(Math.max(1, envData.watchedWallets.length)).length + 1);
    const walletAddressWidth = Math.max(18, Math.min(42, Math.floor(walletTableWidth * 0.62)));
    const walletUsernameWidth = Math.max(8, walletTableWidth - walletIndexWidth - walletAddressWidth - 2);
    const liveTradingEnabled = isLiveTradingEnabled(envData.rawValues);
    const topRowGap = stacked ? 0 : 1;
    const topRowWidth = Math.max(24, terminal.width - 4);
    const topBoxWidth = stacked ? '100%' : Math.max(28, Math.floor((topRowWidth - topRowGap) / 2));
    const topBoxContentWidth = typeof topBoxWidth === 'number' ? Math.max(24, topBoxWidth - 4) : 24;
    const topBoxColumnCount = 1;
    const botStateStats = [
        { label: 'Mode', value: (state.mode || 'unknown').toUpperCase(), color: state.mode === 'live' ? theme.green : theme.dim },
        { label: 'Wallets watched', value: String(state.n_wallets || 0) },
        { label: 'Poll interval', value: state.poll_interval ? `${state.poll_interval}s` : '-' },
        { label: 'Bankroll', value: state.bankroll_usd != null ? `$${formatNumber(state.bankroll_usd)}` : '-' }
    ];
    const databaseStats = [
        { label: 'trade_log rows', value: String(counts[0]?.n || 0) },
        { label: 'Started at', value: formatSettingsDateTime(state.started_at) },
        { label: 'Last poll', value: formatSettingsTime(state.last_poll_at) },
        { label: 'Poll duration', value: state.last_poll_duration_s != null ? `${formatNumber(state.last_poll_duration_s)}s` : '-' }
    ];
    return (React.createElement(InkBox, { flexDirection: "column", width: "100%" },
        React.createElement(InkBox, { flexDirection: stacked ? 'column' : 'row' },
            React.createElement(SettingsSummaryBox, { title: "Bot State", width: topBoxWidth, items: botStateStats, columnCount: topBoxColumnCount }),
            !stacked ? React.createElement(InkBox, { width: topRowGap }) : React.createElement(InkBox, { height: 1 }),
            React.createElement(SettingsSummaryBox, { title: "Database", width: topBoxWidth, items: databaseStats, columnCount: topBoxColumnCount })),
        React.createElement(InkBox, { marginTop: 1, flexDirection: stacked ? 'column' : 'row', width: "100%" },
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
                React.createElement(InkBox, { flexDirection: "column", marginTop: 1, marginBottom: 1 },
                    React.createElement(Text, { color: statusColor }, configHelperLine))),
            !stacked ? React.createElement(InkBox, { width: middleRowGap }) : React.createElement(InkBox, { height: 1 }),
            React.createElement(InkBox, { borderStyle: "round", borderColor: theme.red, flexDirection: "column", width: stacked ? '100%' : undefined, flexGrow: stacked ? 0 : 1, flexShrink: 1, paddingX: 1 },
                React.createElement(InkBox, null,
                    React.createElement(Text, { color: theme.red, bold: true }, "Danger Zone")),
                editor.dangerConfirm ? (React.createElement(React.Fragment, null,
                    React.createElement(Text, { color: theme.yellow, bold: true }, truncate(dangerHeaderText, dangerContentWidth)),
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
                    dangerHelperLines.map((line, index) => (React.createElement(Text, { key: `danger-status-${index}`, color: statusColor }, line)))))),
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
                    hiddenWalletCount > 0 ? (React.createElement(Text, { color: theme.dim }, truncate(`... and ${hiddenWalletCount} more`, helperWidth))) : null))) : (React.createElement(Text, { color: theme.dim }, "No active env file found yet."))))));
}
