import fs from 'fs';
import React, { useMemo } from 'react';
import { Box as InkBox, Text } from 'ink';
import { Box } from '../components/Box.js';
import { StatRow } from '../components/StatRow.js';
import { editableConfigFields, formatEditableConfigValue } from '../configEditor.js';
import { fit, formatNumber, truncate } from '../format.js';
import { envExamplePath, envPath } from '../paths.js';
import { rowsForHeight, stackPanels } from '../responsive.js';
import { useTerminalSize } from '../terminal.js';
import { theme } from '../theme.js';
import { useBotState } from '../useBotState.js';
import { useQuery } from '../useDb.js';
import { useEventStream } from '../useEventStream.js';
const COUNT_SQL = `SELECT COUNT(*) AS n FROM trade_log`;
function readEnvData() {
    const path = fs.existsSync(envPath) ? envPath : envExamplePath;
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
        }, { rows: [], watchedWallets: [] });
    }
    catch {
        return { rows: [], watchedWallets: [] };
    }
}
export function Settings({ editor }) {
    const terminal = useTerminalSize();
    const stacked = stackPanels(terminal.width);
    const state = useBotState();
    const counts = useQuery(COUNT_SQL);
    const events = useEventStream(1000);
    const envData = readEnvData();
    const environmentBudget = rowsForHeight(terminal.height, stacked ? 28 : 22, 6, 14);
    const walletSectionHeaderRows = envData.watchedWallets.length ? 2 : 0;
    const maxWalletLines = envData.watchedWallets.length ? Math.max(2, Math.min(6, Math.floor(environmentBudget / 2))) : 0;
    const walletSectionRows = envData.watchedWallets.length
        ? walletSectionHeaderRows + Math.min(envData.watchedWallets.length, maxWalletLines) + (envData.watchedWallets.length > maxWalletLines ? 1 : 0)
        : 0;
    const envRows = envData.rows.slice(0, Math.max(0, environmentBudget - walletSectionRows));
    const visibleWallets = envData.watchedWallets.slice(0, maxWalletLines);
    const hiddenWalletCount = Math.max(0, envData.watchedWallets.length - visibleWallets.length);
    const selectedField = editableConfigFields[editor.selectedIndex];
    const helperWidth = Math.max(24, terminal.width - 14);
    const statusColor = editor.statusTone === 'error' ? theme.red : editor.statusTone === 'success' ? theme.green : theme.dim;
    const usernames = useMemo(() => {
        const lookup = new Map();
        for (let index = events.length - 1; index >= 0; index -= 1) {
            const event = events[index];
            const wallet = event.trader?.trim().toLowerCase();
            const username = event.username?.trim();
            if (!wallet || !username || lookup.has(wallet)) {
                continue;
            }
            lookup.set(wallet, username);
        }
        return lookup;
    }, [events]);
    const walletTableWidth = Math.max(24, helperWidth - 2);
    const walletIndexWidth = Math.max(3, String(Math.max(1, envData.watchedWallets.length)).length + 1);
    const walletAddressWidth = Math.max(18, Math.min(42, Math.floor(walletTableWidth * 0.62)));
    const walletUsernameWidth = Math.max(8, walletTableWidth - walletIndexWidth - walletAddressWidth - 2);
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
        React.createElement(InkBox, { marginTop: 1 },
            React.createElement(Box, { title: "Editable Config", accent: true },
                editableConfigFields.map((field, index) => {
                    const selected = index === editor.selectedIndex;
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
                    return (React.createElement(InkBox, { key: field.key, justifyContent: "space-between" },
                        React.createElement(Text, { color: labelColor, bold: selected }, truncate(label, terminal.compact ? 18 : 24)),
                        React.createElement(Text, { color: valueColor, bold: selected }, truncate(shownValue, terminal.compact ? 18 : 28))));
                }),
                React.createElement(InkBox, { flexDirection: "column", marginTop: 1 },
                    React.createElement(Text, { color: theme.dim }, truncate(`${selectedField.key} - ${selectedField.description}`, helperWidth)),
                    React.createElement(Text, { color: statusColor }, truncate(editor.statusMessage ||
                        (editor.isEditing
                            ? 'Type a value, then press Enter to save or Esc to cancel.'
                            : 'Use j/k or arrows to select. Press e or Enter to edit.'), helperWidth)),
                    React.createElement(Text, { color: theme.dim }, editor.isEditing ? 'editing mode active' : 'poll interval applies live; most other changes need a bot restart')))),
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
                            React.createElement(Text, { color: theme.white }, fit(usernames.get(wallet.toLowerCase()) || '-', walletUsernameWidth)),
                            React.createElement(Text, null, " "),
                            React.createElement(Text, { color: theme.white }, fit(wallet, walletAddressWidth))))))) : (React.createElement(Text, { color: theme.dim }, "No watched wallets configured.")),
                    hiddenWalletCount > 0 ? (React.createElement(Text, { color: theme.dim }, truncate(`... and ${hiddenWalletCount} more`, helperWidth))) : null))) : (React.createElement(Text, { color: theme.dim }, "No .env file found yet."))))));
}
