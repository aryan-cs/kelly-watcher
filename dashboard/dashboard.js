import React, { startTransition, useEffect, useRef, useState } from 'react';
import { Box, Spacer, Text, render, useInput } from 'ink';
import { cycleDurationPreset, editableConfigFields, isPresetDurationField, readEnvValues, readEditableConfigValues, validateEditableConfigValue, writeEditableConfigValue } from './configEditor.js';
import { MODEL_PANEL_DEFS, Models } from './pages/Models.js';
import { stackPanels } from './responsive.js';
import { dangerActions, restartShadowAccount, setLiveTradingEnabled } from './settingsDanger.js';
import { theme } from './theme.js';
import { LiveFeed } from './pages/LiveFeed.js';
import { Signals } from './pages/Signals.js';
import { Performance } from './pages/Performance';
import { Wallets } from './pages/Wallets';
import { Settings } from './pages/Settings.js';
import { secondsAgo } from './format.js';
import { ManualRefreshProvider } from './refresh.js';
import { detectTerminalBackgroundColor, TerminalSizeProvider, useTerminalSize } from './terminal.js';
import { useBotState } from './useBotState.js';
import { dropTrackedWallet, reactivateDroppedWallet } from './walletWatchState.js';
const DOUBLE_UP_JUMP_MS = 350;
const DOUBLE_UP_CONFIRM_MS = 140;
const HORIZONTAL_SCROLL_STEP = 8;
const PAGES = {
    1: { label: 'Tracker' },
    2: { label: 'Signals' },
    3: { label: 'Perf' },
    4: { label: 'Models' },
    5: { label: 'Wallets' },
    6: { label: 'Config' }
};
function formatCurrentPollElapsedSeconds(nowSeconds, startedAtSeconds) {
    if (!startedAtSeconds || startedAtSeconds <= 0) {
        return null;
    }
    return `${Math.max(0, Math.floor(nowSeconds - startedAtSeconds))}s`;
}
function formatRetrainStatus(status) {
    const value = String(status || '').trim().toLowerCase();
    if (!value) {
        return null;
    }
    if (value === 'deployed')
        return 'train deployed';
    if (value === 'completed_not_deployed')
        return 'train no deploy';
    if (value === 'skipped_not_enough_samples')
        return 'train waiting';
    if (value === 'already_running')
        return 'train busy';
    if (value === 'failed')
        return 'train failed';
    if (value.startsWith('skipped_'))
        return 'train skipped';
    if (value === 'running')
        return 'training';
    return `train ${value.replace(/_/g, ' ')}`;
}
function describeBackendStatus({ startedAt, lastPollAt, activityIsFresh, pollIsFresh, loopInProgress }) {
    if (pollIsFresh) {
        return loopInProgress ? 'polling' : 'online';
    }
    if (startedAt <= 0) {
        return 'waiting to start';
    }
    if (activityIsFresh && lastPollAt <= 0) {
        return 'starting up';
    }
    if (activityIsFresh && loopInProgress) {
        return 'polling now';
    }
    if (lastPollAt > 0) {
        return 'poll stalled';
    }
    return 'offline';
}
function formatHeaderStatusTag(status) {
    return `[${status.trim().toUpperCase()}]`;
}
function renderPage(page, settingsEditor, feedScrollOffset, onFeedScrollOffsetChange, signalsScrollOffset, onSignalsScrollOffsetChange, signalsHorizontalOffset, onSignalsHorizontalOffsetChange, perfCurrentScrollOffset, perfPastScrollOffset, perfActivePane, perfSelectedBox, perfDailyDetailOpen, perfDailyDetailScrollOffset, onPerfCurrentScrollOffsetChange, onPerfPastScrollOffsetChange, onPerfDailyDetailScrollOffsetChange, modelSelectionIndex, modelDetailOpen, modelSettingSelectionIndex, settingsValues, walletPane, walletBestSelectionIndex, walletWorstSelectionIndex, walletTrackedSelectionIndex, walletDroppedSelectionIndex, walletDetailOpen, onWalletMetaChange) {
    switch (page) {
        case 1:
            return React.createElement(LiveFeed, { scrollOffset: feedScrollOffset, onScrollOffsetChange: onFeedScrollOffsetChange });
        case 2:
            return (React.createElement(Signals, { scrollOffset: signalsScrollOffset, horizontalOffset: signalsHorizontalOffset, onScrollOffsetChange: onSignalsScrollOffsetChange, onHorizontalOffsetChange: onSignalsHorizontalOffsetChange }));
        case 3:
            return (React.createElement(Performance, { currentScrollOffset: perfCurrentScrollOffset, pastScrollOffset: perfPastScrollOffset, activePane: perfActivePane, selectedBox: perfSelectedBox, dailyDetailOpen: perfDailyDetailOpen, dailyDetailScrollOffset: perfDailyDetailScrollOffset, onCurrentScrollOffsetChange: onPerfCurrentScrollOffsetChange, onPastScrollOffsetChange: onPerfPastScrollOffsetChange, onDailyDetailScrollOffsetChange: onPerfDailyDetailScrollOffsetChange }));
        case 4:
            return (React.createElement(Models, { selectedPanelIndex: modelSelectionIndex, detailOpen: modelDetailOpen, selectedSettingIndex: modelSettingSelectionIndex, settingsValues: settingsValues }));
        case 5:
            return (React.createElement(Wallets, { activePane: walletPane, bestSelectedIndex: walletBestSelectionIndex, worstSelectedIndex: walletWorstSelectionIndex, trackedSelectedIndex: walletTrackedSelectionIndex, droppedSelectedIndex: walletDroppedSelectionIndex, detailOpen: walletDetailOpen, onWalletMetaChange: onWalletMetaChange }));
        case 6:
            return React.createElement(Settings, { editor: settingsEditor });
    }
}
function AppContent({ page, isRefreshing, settingsEditor, feedScrollOffset, onFeedScrollOffsetChange, signalsScrollOffset, onSignalsScrollOffsetChange, signalsHorizontalOffset, onSignalsHorizontalOffsetChange, perfCurrentScrollOffset, perfPastScrollOffset, perfActivePane, perfSelectedBox, perfDailyDetailOpen, perfDailyDetailScrollOffset, modelSelectionIndex, modelDetailOpen, modelSettingSelectionIndex, walletPane, walletBestSelectionIndex, walletWorstSelectionIndex, walletTrackedSelectionIndex, walletDroppedSelectionIndex, walletDetailOpen, onWalletMetaChange, onPerfCurrentScrollOffsetChange, onPerfPastScrollOffsetChange, onPerfDailyDetailScrollOffsetChange }) {
    const terminal = useTerminalSize();
    const botState = useBotState();
    const mode = botState.mode === 'live' ? '[LIVE]' : '[SHADOW]';
    const modeColor = botState.mode === 'live' ? theme.green : theme.dim;
    const now = Date.now() / 1000;
    const heartbeatWindow = Math.max((botState.poll_interval || 1) * 3, 3);
    const activityWindow = Math.max(heartbeatWindow, 30);
    const startedAt = botState.started_at ?? 0;
    const lastPollAt = botState.last_poll_at ?? 0;
    const lastActivityAt = botState.last_activity_at ?? 0;
    const currentLoopStartedAt = botState.last_loop_started_at ?? 0;
    const loopInProgress = botState.loop_in_progress ?? false;
    const retrainInProgress = botState.retrain_in_progress ?? false;
    const retrainStartedAt = botState.retrain_started_at ?? 0;
    const lastRetrainFinishedAt = botState.last_retrain_finished_at ?? 0;
    const retrainElapsedText = formatCurrentPollElapsedSeconds(now, retrainStartedAt);
    const retrainStatusText = formatRetrainStatus(botState.last_retrain_status);
    const pollIsFresh = lastPollAt > 0 && (now - lastPollAt) <= heartbeatWindow;
    const activityIsFresh = lastActivityAt > 0 && (now - lastActivityAt) <= activityWindow;
    const startupDetail = String(botState.startup_detail || '').trim();
    const startupInProgress = startedAt > 0 && activityIsFresh && lastPollAt <= 0;
    const backendDotColor = pollIsFresh
        ? theme.green
        : startedAt > 0 && activityIsFresh && (loopInProgress || lastPollAt <= 0)
            ? theme.yellow
            : theme.red;
    const backendStatusText = startupInProgress && startupDetail
        ? startupDetail
        : describeBackendStatus({
            startedAt,
            lastPollAt,
            activityIsFresh,
            pollIsFresh,
            loopInProgress
        });
    const backendStatusTag = formatHeaderStatusTag(backendStatusText);
    const navLabels = terminal.compact
        ? { 1: 'F', 2: 'S', 3: 'P', 4: 'M', 5: 'W', 6: 'C' }
        : terminal.narrow
            ? { 1: 'Track', 2: 'Sig', 3: 'Perf', 4: 'Mod', 5: 'Wall', 6: 'Cfg' }
            : { 1: 'Tracker', 2: 'Signals', 3: 'Perf', 4: 'Models', 5: 'Wallets', 6: 'Config' };
    const footerCompact = terminal.compact;
    const startupElapsedText = formatCurrentPollElapsedSeconds(now, startedAt);
    const currentPollElapsedText = formatCurrentPollElapsedSeconds(now, currentLoopStartedAt);
    const lastPollText = loopInProgress
        ? `polling...${currentPollElapsedText ? ` ${currentPollElapsedText}` : ''} | last poll: ${secondsAgo(botState.last_poll_at)}`
        : `last poll: ${secondsAgo(botState.last_poll_at)}`;
    const recentRetrainText = !retrainInProgress && retrainStatusText && lastRetrainFinishedAt > 0 && (now - lastRetrainFinishedAt) <= 60
        ? `${retrainStatusText}: ${secondsAgo(lastRetrainFinishedAt)}`
        : null;
    const footerStatusText = isRefreshing
        ? 'refreshing...'
        : retrainInProgress
            ? `training...${retrainElapsedText ? ` ${retrainElapsedText}` : ''} | ${lastPollText}`
            : startupInProgress
                ? `starting up...${startupElapsedText ? ` ${startupElapsedText}` : ''}`
            : recentRetrainText
                ? `${recentRetrainText} | ${lastPollText}`
                : lastPollText;
    const footerStatusColor = isRefreshing ? theme.accent : retrainInProgress ? theme.yellow : startupInProgress ? theme.yellow : theme.dim;
    const footerControls = page === 1
        ? terminal.compact
            ? '↑↓ scroll  ↑↑ latest  r refresh  q exit'
            : '↑/↓: scroll  ↑↑: latest  r: refresh  q: exit'
        : page === 2
            ? terminal.compact
                ? '↑↓ scroll  ←→ pan  ↑↑ latest  r refresh  q exit'
                : '↑/↓: scroll  ←/→: pan  ↑↑: latest  r: refresh  q: exit'
            : page === 3
                ? perfDailyDetailOpen
                    ? terminal.compact
                        ? '↑↓ list  esc close  r refresh  q exit'
                        : '↑/↓: list  esc: close  r: refresh  q: exit'
                    : terminal.compact
                        ? '←→ boxes  ↑↓ scroll  enter detail  r refresh  q exit'
                        : '←/→: cycle boxes  ↑/↓: scroll list  enter: daily detail  r: refresh  q: exit'
                : page === 4
                    ? modelDetailOpen
                        ? terminal.compact
                            ? '↑↓ settings  enter edit  esc close  r refresh  q exit'
                            : '↑/↓: settings  enter: edit in config  esc: close  r: refresh  q: exit'
                        : terminal.compact
                            ? '↑↓/←→ select  enter help  r refresh  q exit'
                            : '↑/↓/←/→: select  enter: help  r: refresh  q: exit'
                    : page === 5
                        ? terminal.compact
                            ? '←→ pane  ↑↓ select  enter detail  d drop  a reactivate  esc close  r refresh  q exit'
                            : '←/→: pane  ↑/↓: select  enter: detail  d: drop tracked  a: reactivate dropped  esc: close  r: refresh  q: exit'
                        : page === 6
                            ? settingsEditor.dangerConfirm
                                ? terminal.compact
                                    ? '↑↓ choose  enter confirm  esc cancel  r refresh  q exit'
                                    : '↑/↓: choose  enter: confirm  esc: cancel  r: refresh  q: exit'
                                : settingsEditor.isEditing
                                    ? terminal.compact
                                        ? '↑↓ presets  enter save  esc cancel  r refresh  q exit'
                                        : '↑/↓: cycle presets  enter: save  esc: cancel  r: refresh  q: exit'
                                    : terminal.compact
                                        ? '↑↓ select  enter open  r refresh  q exit'
                                        : '↑/↓: select  enter: edit/open  r: refresh  q: exit'
                            : terminal.compact
                                ? 'r refresh  q exit'
                                : 'r: refresh  q: exit';
    return (React.createElement(Box, { flexDirection: "column", borderStyle: "round", borderColor: theme.accent, width: terminal.width, height: terminal.height },
        React.createElement(Box, { borderStyle: "round", borderColor: theme.border, paddingX: 1 },
            React.createElement(Text, { color: backendDotColor }, "\u25CF"),
            React.createElement(Text, null, " "),
            React.createElement(Text, { color: theme.white, bold: true }, "KELLY-WATCHER"),
            React.createElement(Text, null, "  "),
            Object.entries(PAGES).map(([key, value]) => {
                const isSelected = Number(key) === page;
                const label = `${key}:${navLabels[Number(key)] || value.label}`;
                return (React.createElement(React.Fragment, { key: key },
                    React.createElement(Text, { color: isSelected ? theme.white : theme.dim, bold: isSelected }, isSelected ? `[${label}]` : label),
                    React.createElement(Text, null, "  ")));
            }),
            React.createElement(Spacer, null),
            React.createElement(Text, { color: backendDotColor, bold: true }, backendStatusTag),
            React.createElement(Text, { color: theme.dim }, " "),
            React.createElement(Text, { color: modeColor, bold: true }, mode)),
        React.createElement(Box, { padding: 1, flexGrow: 1 }, renderPage(page, settingsEditor, feedScrollOffset, onFeedScrollOffsetChange, signalsScrollOffset, onSignalsScrollOffsetChange, signalsHorizontalOffset, onSignalsHorizontalOffsetChange, perfCurrentScrollOffset, perfPastScrollOffset, perfActivePane, perfSelectedBox, perfDailyDetailOpen, perfDailyDetailScrollOffset, onPerfCurrentScrollOffsetChange, onPerfPastScrollOffsetChange, onPerfDailyDetailScrollOffsetChange, modelSelectionIndex, modelDetailOpen, modelSettingSelectionIndex, settingsEditor.values, walletPane, walletBestSelectionIndex, walletWorstSelectionIndex, walletTrackedSelectionIndex, walletDroppedSelectionIndex, walletDetailOpen, onWalletMetaChange)),
        React.createElement(Box, { borderStyle: "round", borderColor: theme.border, paddingX: 1 }, footerCompact ? (React.createElement(React.Fragment, null,
            React.createElement(Text, { color: theme.dim }, footerControls),
            React.createElement(Spacer, null),
            React.createElement(Text, { color: footerStatusColor }, footerStatusText))) : (React.createElement(React.Fragment, null,
            React.createElement(Text, { color: theme.dim }, footerControls),
            React.createElement(Spacer, null),
            React.createElement(Text, { color: footerStatusColor }, footerStatusText))))));
}
function App() {
    const [terminalBackgroundColor] = useState(() => globalThis.__KELLY_WATCHER_TERMINAL_BG__);
    const [page, setPage] = useState(1);
    const [refreshToken, setRefreshToken] = useState(0);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [feedScrollOffset, setFeedScrollOffset] = useState(0);
    const [signalsScrollOffset, setSignalsScrollOffset] = useState(0);
    const [signalsHorizontalOffset, setSignalsHorizontalOffset] = useState(0);
    const [perfCurrentScrollOffset, setPerfCurrentScrollOffset] = useState(0);
    const [perfPastScrollOffset, setPerfPastScrollOffset] = useState(0);
    const [perfActivePane, setPerfActivePane] = useState('current');
    const [perfSelectedBox, setPerfSelectedBox] = useState('current');
    const [perfDailyDetailOpen, setPerfDailyDetailOpen] = useState(false);
    const [perfDailyDetailScrollOffset, setPerfDailyDetailScrollOffset] = useState(0);
    const [modelSelectionIndex, setModelSelectionIndex] = useState(0);
    const [modelDetailOpen, setModelDetailOpen] = useState(false);
    const [modelSettingSelectionIndex, setModelSettingSelectionIndex] = useState(0);
    const [walletPane, setWalletPane] = useState('tracked');
    const [walletBestSelectionIndex, setWalletBestSelectionIndex] = useState(0);
    const [walletWorstSelectionIndex, setWalletWorstSelectionIndex] = useState(0);
    const [walletTrackedSelectionIndex, setWalletTrackedSelectionIndex] = useState(0);
    const [walletDroppedSelectionIndex, setWalletDroppedSelectionIndex] = useState(0);
    const [walletDetailOpen, setWalletDetailOpen] = useState(false);
    const [walletMeta, setWalletMeta] = useState({
        bestCount: 0,
        worstCount: 0,
        trackedCount: 0,
        droppedCount: 0,
        bestWalletAddresses: [],
        worstWalletAddresses: [],
        trackedWalletAddresses: [],
        droppedWalletAddresses: []
    });
    const lastUpArrowRef = useRef({ page: null, pane: null, at: 0 });
    const pendingTopJumpRef = useRef(null);
    const upArrowHoldActiveRef = useRef(false);
    const [settingsEditor, setSettingsEditor] = useState(() => ({
        values: readEditableConfigValues(),
        selectedIndex: 0,
        isEditing: false,
        draft: '',
        replaceDraftOnInput: false,
        statusMessage: 'Use up/down to select a setting. Enter edits config or opens the selected danger action.',
        statusTone: 'info',
        focusArea: 'config',
        dangerSelectedIndex: 0,
        dangerConfirm: null
    }));
    const selectedField = editableConfigFields[settingsEditor.selectedIndex];
    const selectedDangerAction = dangerActions[settingsEditor.dangerSelectedIndex];
    const selectedModelPanel = MODEL_PANEL_DEFS[Math.max(0, Math.min(modelSelectionIndex, MODEL_PANEL_DEFS.length - 1))];
    const selectedModelSettingKeys = selectedModelPanel?.settingKeys || [];
    const walletPaneCount = (pane) => {
        if (pane === 'best')
            return walletMeta.bestCount;
        if (pane === 'worst')
            return walletMeta.worstCount;
        if (pane === 'dropped')
            return walletMeta.droppedCount;
        return walletMeta.trackedCount;
    };
    const activeWalletCount = walletPaneCount(walletPane);
    const selectedBestWalletAddress = walletMeta.bestWalletAddresses[Math.max(0, Math.min(walletBestSelectionIndex, Math.max(walletMeta.bestCount - 1, 0)))] || '';
    const selectedWorstWalletAddress = walletMeta.worstWalletAddresses[Math.max(0, Math.min(walletWorstSelectionIndex, Math.max(walletMeta.worstCount - 1, 0)))] || '';
    const selectedDroppedWalletAddress = walletMeta.droppedWalletAddresses[Math.max(0, Math.min(walletDroppedSelectionIndex, Math.max(walletMeta.droppedCount - 1, 0)))] || '';
    const selectedTrackedWalletAddress = walletMeta.trackedWalletAddresses[Math.max(0, Math.min(walletTrackedSelectionIndex, Math.max(walletMeta.trackedCount - 1, 0)))] || '';
    const selectedWalletAddress = walletPane === 'best'
        ? selectedBestWalletAddress
        : walletPane === 'worst'
            ? selectedWorstWalletAddress
            : walletPane === 'dropped'
                ? selectedDroppedWalletAddress
                : selectedTrackedWalletAddress;
    const getAvailableWalletPanes = () => {
        const order = ['best', 'worst', 'tracked', 'dropped'];
        const available = order.filter((pane) => walletPaneCount(pane) > 0);
        return available.length ? available : ['tracked'];
    };
    const moveWalletPane = (direction) => {
        const available = getAvailableWalletPanes();
        const currentIndex = Math.max(0, available.indexOf(walletPane));
        const delta = direction === 'left' ? -1 : 1;
        setWalletPane(available[(currentIndex + delta + available.length) % available.length]);
    };
    const moveModelSelection = (direction) => {
        const panelCount = MODEL_PANEL_DEFS.length;
        const width = process.stdout.columns || 120;
        const columns = stackPanels(width) ? 1 : 2;
        setModelSelectionIndex((current) => {
            if (panelCount <= 1) {
                return 0;
            }
            if (columns === 1) {
                if (direction === 'up' || direction === 'left') {
                    return (current - 1 + panelCount) % panelCount;
                }
                return (current + 1) % panelCount;
            }
            const rowCount = Math.ceil(panelCount / columns);
            const row = Math.floor(current / columns);
            const column = current % columns;
            if (direction === 'left') {
                const target = row * columns + ((column - 1 + columns) % columns);
                return target >= panelCount ? current : target;
            }
            if (direction === 'right') {
                const target = row * columns + ((column + 1) % columns);
                return target >= panelCount ? current : target;
            }
            const nextRow = direction === 'up' ? (row - 1 + rowCount) % rowCount : (row + 1) % rowCount;
            const target = nextRow * columns + column;
            return target >= panelCount ? panelCount - 1 : target;
        });
        setModelSettingSelectionIndex(0);
    };
    const movePerfSelection = (direction) => {
        const order = ['summary', 'daily', 'current', 'past'];
        setPerfSelectedBox((current) => {
            const index = order.indexOf(current);
            const currentIndex = index >= 0 ? index : 0;
            const nextIndex = direction === 'right'
                ? (currentIndex + 1) % order.length
                : (currentIndex - 1 + order.length) % order.length;
            const next = order[nextIndex];
            if (next === 'current' || next === 'past') {
                setPerfActivePane(next);
            }
            return next;
        });
    };
    const saveConfigValue = (rawValue) => {
        const validation = validateEditableConfigValue(selectedField, rawValue);
        if (!validation.ok) {
            setSettingsEditor((current) => ({
                ...current,
                statusMessage: validation.error,
                statusTone: 'error'
            }));
            return;
        }
        try {
            writeEditableConfigValue(selectedField.key, validation.value);
            const values = readEditableConfigValues();
            setSettingsEditor((current) => ({
                ...current,
                values,
                focusArea: 'config',
                isEditing: false,
                draft: '',
                replaceDraftOnInput: false,
                dangerConfirm: null,
                statusMessage: selectedField.liveApplies
                    ? `${selectedField.label} saved. The bot will pick it up on the next poll loop.`
                    : `${selectedField.label} saved to .env. Restart the bot to apply it.`,
                statusTone: 'success'
            }));
        }
        catch (error) {
            const message = error instanceof Error ? error.message : 'Unknown write error';
            setSettingsEditor((current) => ({
                ...current,
                statusMessage: `Failed to save ${selectedField.label}: ${message}`,
                statusTone: 'error'
            }));
        }
    };
    const beginConfigEdit = () => {
        const currentValue = settingsEditor.values[selectedField.key] || selectedField.defaultValue;
        if (selectedField.kind === 'bool') {
            const nextValue = currentValue.toLowerCase() === 'true' ? 'false' : 'true';
            saveConfigValue(nextValue);
            return;
        }
        setSettingsEditor((current) => ({
            ...current,
            focusArea: 'config',
            isEditing: true,
            draft: current.values[selectedField.key] || selectedField.defaultValue,
            replaceDraftOnInput: true,
            statusMessage: isPresetDurationField(selectedField)
                ? `Editing ${selectedField.label}. Use up/down to cycle presets, Enter to save, or Esc to cancel.`
                : `Editing ${selectedField.label}. Press Enter to save or Esc to cancel.`,
            statusTone: 'info'
        }));
    };
    const openConfigField = (fieldKey) => {
        const fieldIndex = editableConfigFields.findIndex((field) => field.key === fieldKey);
        if (fieldIndex < 0) {
            return;
        }
        const values = readEditableConfigValues();
        const field = editableConfigFields[fieldIndex];
        const currentValue = values[field.key] || field.defaultValue;
        setModelDetailOpen(false);
        setPage(6);
        setSettingsEditor((current) => ({
            ...current,
            values,
            focusArea: 'config',
            selectedIndex: fieldIndex,
            isEditing: field.kind !== 'bool',
            draft: field.kind === 'bool' ? '' : currentValue,
            replaceDraftOnInput: field.kind !== 'bool',
            dangerConfirm: null,
            statusMessage: field.kind === 'bool'
                ? field.description
                : isPresetDurationField(field)
                    ? `Editing ${field.label}. Use up/down to cycle presets, Enter to save, or Esc to cancel.`
                    : `Editing ${field.label}. Press Enter to save or Esc to cancel.`,
            statusTone: 'info'
        }));
    };
    const moveSettingsSelection = (direction) => {
        setSettingsEditor((current) => {
            if (current.focusArea === 'config') {
                if (direction === 'up') {
                    if (current.selectedIndex > 0) {
                        const nextIndex = current.selectedIndex - 1;
                        return {
                            ...current,
                            selectedIndex: nextIndex,
                            statusMessage: editableConfigFields[nextIndex]?.description || current.statusMessage,
                            statusTone: 'info'
                        };
                    }
                    const nextDangerIndex = Math.max(dangerActions.length - 1, 0);
                    return {
                        ...current,
                        focusArea: 'danger',
                        dangerSelectedIndex: nextDangerIndex,
                        statusMessage: dangerActions[nextDangerIndex]?.description || current.statusMessage,
                        statusTone: 'info'
                    };
                }
                if (current.selectedIndex < editableConfigFields.length - 1) {
                    const nextIndex = current.selectedIndex + 1;
                    return {
                        ...current,
                        selectedIndex: nextIndex,
                        statusMessage: editableConfigFields[nextIndex]?.description || current.statusMessage,
                        statusTone: 'info'
                    };
                }
                return {
                    ...current,
                    focusArea: 'danger',
                    dangerSelectedIndex: 0,
                    statusMessage: dangerActions[0]?.description || current.statusMessage,
                    statusTone: 'info'
                };
            }
            if (direction === 'up') {
                if (current.dangerSelectedIndex > 0) {
                    const nextIndex = current.dangerSelectedIndex - 1;
                    return {
                        ...current,
                        dangerSelectedIndex: nextIndex,
                        statusMessage: dangerActions[nextIndex]?.description || current.statusMessage,
                        statusTone: 'info'
                    };
                }
                const nextConfigIndex = Math.max(editableConfigFields.length - 1, 0);
                return {
                    ...current,
                    focusArea: 'config',
                    selectedIndex: nextConfigIndex,
                    statusMessage: editableConfigFields[nextConfigIndex]?.description || current.statusMessage,
                    statusTone: 'info'
                };
            }
            if (current.dangerSelectedIndex < dangerActions.length - 1) {
                const nextIndex = current.dangerSelectedIndex + 1;
                return {
                    ...current,
                    dangerSelectedIndex: nextIndex,
                    statusMessage: dangerActions[nextIndex]?.description || current.statusMessage,
                    statusTone: 'info'
                };
            }
            return {
                ...current,
                focusArea: 'config',
                selectedIndex: 0,
                statusMessage: editableConfigFields[0]?.description || current.statusMessage,
                statusTone: 'info'
            };
        });
    };
    const cycleSelectedConfigPreset = (direction) => {
        const nextValue = cycleDurationPreset(selectedField, settingsEditor.draft || settingsEditor.values[selectedField.key] || selectedField.defaultValue, direction);
        if (!nextValue) {
            return;
        }
        setSettingsEditor((current) => ({
            ...current,
            draft: nextValue,
            replaceDraftOnInput: false,
            statusMessage: `Editing ${selectedField.label}. Use up/down to cycle presets, Enter to save, or Esc to cancel.`,
            statusTone: 'info'
        }));
    };
    const openDangerAction = () => {
        if (!selectedDangerAction) {
            return;
        }
        if (selectedDangerAction.id === 'live_trading') {
            const envValues = readEnvValues();
            const currentValue = String(envValues.USE_REAL_MONEY || '').trim().toLowerCase() === 'true';
            setSettingsEditor((current) => ({
                ...current,
                focusArea: 'danger',
                dangerConfirm: {
                    actionId: 'live_trading',
                    title: currentValue ? 'Disable Live Trading?' : 'Enable Live Trading?',
                    message: currentValue
                        ? 'This only updates config. The running bot will stay in its current mode until you restart it.'
                        : 'This arms real-money mode in config. The running bot will stay unchanged until you restart it.',
                    options: currentValue
                        ? [
                            { id: 'confirm_disable', label: 'Disable live trading', description: 'Save USE_REAL_MONEY=false.' },
                            { id: 'cancel', label: 'Cancel', description: 'Leave config unchanged.' }
                        ]
                        : [
                            { id: 'confirm_enable', label: 'Enable live trading', description: 'Save USE_REAL_MONEY=true.' },
                            { id: 'cancel', label: 'Cancel', description: 'Leave config unchanged.' }
                        ],
                    selectedIndex: 0
                },
                statusMessage: 'Use up/down to choose. Enter confirms. Esc cancels.',
                statusTone: 'info'
            }));
            return;
        }
        setSettingsEditor((current) => ({
            ...current,
            focusArea: 'danger',
            dangerConfirm: {
                actionId: 'restart_shadow',
                title: 'Restart Shadow Account?',
                message: 'This clears tracker history, events, bot state, and SQLite data, then restarts shadow mode from the configured bankroll.',
                options: [
                    { id: 'keep_wallets', label: 'Keep current wallets', description: 'Reset data but preserve WATCHED_WALLETS.' },
                    { id: 'clear_wallets', label: 'Clear current wallets', description: 'Reset data and blank WATCHED_WALLETS.' },
                    { id: 'cancel', label: 'Cancel', description: 'Leave everything unchanged.' }
                ],
                selectedIndex: 0
            },
            statusMessage: 'Use up/down to choose. Enter confirms. Esc cancels.',
            statusTone: 'info'
        }));
    };
    const executeDangerAction = () => {
        const confirm = settingsEditor.dangerConfirm;
        if (!confirm) {
            return;
        }
        const selectedOption = confirm.options[confirm.selectedIndex];
        if (!selectedOption || selectedOption.id === 'cancel') {
            setSettingsEditor((current) => ({
                ...current,
                dangerConfirm: null,
                statusMessage: `${confirm.title} canceled.`,
                statusTone: 'info'
            }));
            return;
        }
        const result = confirm.actionId === 'live_trading'
            ? setLiveTradingEnabled(selectedOption.id === 'confirm_enable')
            : restartShadowAccount(selectedOption.id === 'keep_wallets');
        setSettingsEditor((current) => ({
            ...current,
            values: readEditableConfigValues(),
            focusArea: 'danger',
            dangerConfirm: null,
            statusMessage: result.message,
            statusTone: result.ok ? 'success' : 'error'
        }));
        if (result.ok) {
            setIsRefreshing(true);
            setRefreshToken((current) => current + 1);
        }
    };
    useEffect(() => {
        if (refreshToken === 0 || !isRefreshing) {
            return;
        }
        // Keep the footer indicator visible long enough to confirm the refresh keypress.
        const timer = setTimeout(() => {
            setIsRefreshing(false);
        }, 400);
        return () => clearTimeout(timer);
    }, [refreshToken, isRefreshing]);
    useEffect(() => {
        return () => {
            if (pendingTopJumpRef.current !== null) {
                clearTimeout(pendingTopJumpRef.current);
            }
        };
    }, []);
    useEffect(() => {
        if (page !== 5 && walletDetailOpen) {
            setWalletDetailOpen(false);
        }
    }, [page, walletDetailOpen]);
    useEffect(() => {
        if (page !== 4 && modelDetailOpen) {
            setModelDetailOpen(false);
        }
    }, [page, modelDetailOpen]);
    useEffect(() => {
        if (page !== 3 && perfDailyDetailOpen) {
            setPerfDailyDetailOpen(false);
        }
    }, [page, perfDailyDetailOpen]);
    useEffect(() => {
        setWalletBestSelectionIndex((current) => Math.min(current, Math.max(walletMeta.bestCount - 1, 0)));
        setWalletWorstSelectionIndex((current) => Math.min(current, Math.max(walletMeta.worstCount - 1, 0)));
        setWalletTrackedSelectionIndex((current) => Math.min(current, Math.max(walletMeta.trackedCount - 1, 0)));
        setWalletDroppedSelectionIndex((current) => Math.min(current, Math.max(walletMeta.droppedCount - 1, 0)));
        const available = getAvailableWalletPanes();
        if (walletPaneCount(walletPane) <= 0) {
            setWalletPane(available[0]);
            if (walletDetailOpen) {
                setWalletDetailOpen(false);
            }
            return;
        }
        if (available.length <= 0 && walletDetailOpen) {
            setWalletDetailOpen(false);
        }
    }, [
        walletDetailOpen,
        walletMeta.bestCount,
        walletMeta.droppedCount,
        walletMeta.trackedCount,
        walletMeta.worstCount,
        walletPane
    ]);
    useEffect(() => {
        if (walletMeta.bestCount <= 0 &&
            walletMeta.worstCount <= 0 &&
            walletMeta.trackedCount <= 0 &&
            walletMeta.droppedCount <= 0 &&
            walletDetailOpen) {
            if (walletDetailOpen) {
                setWalletDetailOpen(false);
            }
        }
    }, [
        walletDetailOpen,
        walletMeta.bestCount,
        walletMeta.droppedCount,
        walletMeta.trackedCount,
        walletMeta.worstCount
    ]);
    useEffect(() => {
        setModelSelectionIndex((current) => Math.min(current, MODEL_PANEL_DEFS.length - 1));
    }, []);
    useEffect(() => {
        if (selectedModelSettingKeys.length <= 0) {
            setModelSettingSelectionIndex(0);
            return;
        }
        setModelSettingSelectionIndex((current) => Math.min(current, selectedModelSettingKeys.length - 1));
    }, [selectedModelSettingKeys.length]);
    const clearPendingTopJump = () => {
        if (pendingTopJumpRef.current !== null) {
            clearTimeout(pendingTopJumpRef.current);
            pendingTopJumpRef.current = null;
        }
    };
    const resetUpArrowState = () => {
        clearPendingTopJump();
        lastUpArrowRef.current = { page: null, pane: null, at: 0 };
        upArrowHoldActiveRef.current = false;
    };
    const scrollActivePaneUp = () => {
        if (page === 1) {
            setFeedScrollOffset((current) => Math.max(0, current - 1));
        }
        else if (page === 2) {
            setSignalsScrollOffset((current) => Math.max(0, current - 1));
        }
        else if (perfActivePane === 'current') {
            setPerfCurrentScrollOffset((current) => Math.max(0, current - 1));
        }
        else {
            setPerfPastScrollOffset((current) => Math.max(0, current - 1));
        }
    };
    const jumpPaneToTop = (targetPage, targetPane) => {
        if (targetPage === 1) {
            setFeedScrollOffset(0);
        }
        else if (targetPage === 2) {
            setSignalsScrollOffset(0);
        }
        else if (targetPane === 'current') {
            setPerfCurrentScrollOffset(0);
        }
        else {
            setPerfPastScrollOffset(0);
        }
    };
    const scrollSelectedPerformancePane = (delta) => {
        if (perfSelectedBox === 'current') {
            if (delta < 0) {
                setPerfCurrentScrollOffset((current) => Math.max(0, current - 1));
            }
            else {
                setPerfCurrentScrollOffset((current) => current + 1);
            }
            return;
        }
        if (perfSelectedBox === 'past') {
            if (delta < 0) {
                setPerfPastScrollOffset((current) => Math.max(0, current - 1));
            }
            else {
                setPerfPastScrollOffset((current) => current + 1);
            }
        }
    };
    useInput((input, key) => {
        const normalized = input.toLowerCase();
        if (!key.upArrow) {
            resetUpArrowState();
        }
        if (page === 6) {
            if (settingsEditor.dangerConfirm) {
                if (key.escape) {
                    setSettingsEditor((current) => ({
                        ...current,
                        dangerConfirm: null,
                        statusMessage: `${current.dangerConfirm?.title || 'Danger action'} canceled.`,
                        statusTone: 'info'
                    }));
                    return;
                }
                if (key.return) {
                    executeDangerAction();
                    return;
                }
                if (key.upArrow || normalized === 'k') {
                    setSettingsEditor((current) => {
                        if (!current.dangerConfirm) {
                            return current;
                        }
                        const optionCount = current.dangerConfirm.options.length;
                        const nextIndex = current.dangerConfirm.selectedIndex <= 0 ? optionCount - 1 : current.dangerConfirm.selectedIndex - 1;
                        return {
                            ...current,
                            dangerConfirm: {
                                ...current.dangerConfirm,
                                selectedIndex: nextIndex
                            },
                            statusMessage: current.dangerConfirm.options[nextIndex]?.description || current.statusMessage,
                            statusTone: 'info'
                        };
                    });
                    return;
                }
                if (key.downArrow || normalized === 'j') {
                    setSettingsEditor((current) => {
                        if (!current.dangerConfirm) {
                            return current;
                        }
                        const optionCount = current.dangerConfirm.options.length;
                        const nextIndex = current.dangerConfirm.selectedIndex >= optionCount - 1 ? 0 : current.dangerConfirm.selectedIndex + 1;
                        return {
                            ...current,
                            dangerConfirm: {
                                ...current.dangerConfirm,
                                selectedIndex: nextIndex
                            },
                            statusMessage: current.dangerConfirm.options[nextIndex]?.description || current.statusMessage,
                            statusTone: 'info'
                        };
                    });
                    return;
                }
                return;
            }
            if (settingsEditor.isEditing) {
                if (key.escape) {
                    setSettingsEditor((current) => ({
                        ...current,
                        isEditing: false,
                        draft: '',
                        replaceDraftOnInput: false,
                        statusMessage: `Canceled editing ${selectedField.label}.`,
                        statusTone: 'info'
                    }));
                    return;
                }
                if (key.return) {
                    saveConfigValue(settingsEditor.draft);
                    return;
                }
                if (isPresetDurationField(selectedField) && (key.upArrow || normalized === 'k')) {
                    cycleSelectedConfigPreset('previous');
                    return;
                }
                if (isPresetDurationField(selectedField) && (key.downArrow || normalized === 'j')) {
                    cycleSelectedConfigPreset('next');
                    return;
                }
                if (key.backspace || key.delete) {
                    setSettingsEditor((current) => ({
                        ...current,
                        draft: current.replaceDraftOnInput ? '' : current.draft.slice(0, -1),
                        replaceDraftOnInput: false
                    }));
                    return;
                }
                const accepts = selectedField.kind === 'int'
                    ? /^[0-9]$/
                    : selectedField.kind === 'choice'
                        ? /^[a-z0-9_-]$/i
                        : selectedField.kind === 'duration'
                            ? /^[0-9a-z.]$/i
                            : /^[0-9.]$/;
                if (accepts.test(input)) {
                    setSettingsEditor((current) => ({
                        ...current,
                        draft: current.replaceDraftOnInput
                            ? input.toLowerCase()
                            : `${current.draft}${input.toLowerCase()}`,
                        replaceDraftOnInput: false
                    }));
                    return;
                }
                return;
            }
            if (key.upArrow || normalized === 'k') {
                moveSettingsSelection('up');
                return;
            }
            if (key.downArrow || normalized === 'j') {
                moveSettingsSelection('down');
                return;
            }
            if (normalized === 'e' || key.return) {
                if (settingsEditor.focusArea === 'danger') {
                    openDangerAction();
                }
                else {
                    beginConfigEdit();
                }
                return;
            }
        }
        if (page === 4) {
            if (modelDetailOpen) {
                if (key.escape) {
                    setModelDetailOpen(false);
                    return;
                }
                if ((key.upArrow || normalized === 'k') && selectedModelSettingKeys.length > 0) {
                    setModelSettingSelectionIndex((current) => current <= 0 ? selectedModelSettingKeys.length - 1 : current - 1);
                    return;
                }
                if ((key.downArrow || normalized === 'j') && selectedModelSettingKeys.length > 0) {
                    setModelSettingSelectionIndex((current) => current >= selectedModelSettingKeys.length - 1 ? 0 : current + 1);
                    return;
                }
                if ((key.return || normalized === 'e') && selectedModelSettingKeys.length > 0) {
                    openConfigField(selectedModelSettingKeys[modelSettingSelectionIndex] || selectedModelSettingKeys[0]);
                    return;
                }
                return;
            }
            if (key.upArrow || normalized === 'k') {
                moveModelSelection('up');
                return;
            }
            if (key.downArrow || normalized === 'j') {
                moveModelSelection('down');
                return;
            }
            if (key.leftArrow || normalized === 'h') {
                moveModelSelection('left');
                return;
            }
            if (key.rightArrow || normalized === 'l') {
                moveModelSelection('right');
                return;
            }
            if (key.return) {
                setModelSettingSelectionIndex(0);
                setModelDetailOpen(true);
                return;
            }
        }
        if (page === 5) {
            if (key.escape && walletDetailOpen) {
                setWalletDetailOpen(false);
                return;
            }
            if (key.leftArrow || normalized === 'h') {
                moveWalletPane('left');
                return;
            }
            if (key.rightArrow || normalized === 'l') {
                moveWalletPane('right');
                return;
            }
            if ((key.upArrow || normalized === 'k') && activeWalletCount > 0) {
                if (walletPane === 'best') {
                    setWalletBestSelectionIndex((current) => (current <= 0 ? activeWalletCount - 1 : current - 1));
                }
                else if (walletPane === 'worst') {
                    setWalletWorstSelectionIndex((current) => (current <= 0 ? activeWalletCount - 1 : current - 1));
                }
                else if (walletPane === 'dropped') {
                    setWalletDroppedSelectionIndex((current) => (current <= 0 ? activeWalletCount - 1 : current - 1));
                }
                else {
                    setWalletTrackedSelectionIndex((current) => (current <= 0 ? activeWalletCount - 1 : current - 1));
                }
                return;
            }
            if ((key.downArrow || normalized === 'j') && activeWalletCount > 0) {
                if (walletPane === 'best') {
                    setWalletBestSelectionIndex((current) => (current >= activeWalletCount - 1 ? 0 : current + 1));
                }
                else if (walletPane === 'worst') {
                    setWalletWorstSelectionIndex((current) => (current >= activeWalletCount - 1 ? 0 : current + 1));
                }
                else if (walletPane === 'dropped') {
                    setWalletDroppedSelectionIndex((current) => (current >= activeWalletCount - 1 ? 0 : current + 1));
                }
                else {
                    setWalletTrackedSelectionIndex((current) => (current >= activeWalletCount - 1 ? 0 : current + 1));
                }
                return;
            }
            if (normalized === 'f' && selectedWalletAddress) {
                const trackedIndex = walletMeta.trackedWalletAddresses.findIndex((address) => address === selectedWalletAddress);
                if (trackedIndex >= 0) {
                    setWalletPane('tracked');
                    setWalletTrackedSelectionIndex(trackedIndex);
                    setWalletDetailOpen(false);
                    return;
                }
                const droppedIndex = walletMeta.droppedWalletAddresses.findIndex((address) => address === selectedWalletAddress);
                if (droppedIndex >= 0) {
                    setWalletPane('dropped');
                    setWalletDroppedSelectionIndex(droppedIndex);
                    setWalletDetailOpen(false);
                    return;
                }
            }
            if (normalized === 'a' && walletPane === 'dropped' && selectedDroppedWalletAddress) {
                if (reactivateDroppedWallet(selectedDroppedWalletAddress)) {
                    setWalletDetailOpen(false);
                    setRefreshToken((current) => current + 1);
                }
                return;
            }
            if (normalized === 'd' && walletPane === 'tracked' && selectedTrackedWalletAddress) {
                if (dropTrackedWallet(selectedTrackedWalletAddress)) {
                    setWalletDetailOpen(false);
                    setRefreshToken((current) => current + 1);
                }
                return;
            }
            if (key.return && activeWalletCount > 0) {
                setWalletDetailOpen(true);
                return;
            }
        }
        if (page === 3) {
            if (perfDailyDetailOpen) {
                if (key.escape) {
                    setPerfDailyDetailOpen(false);
                    return;
                }
                if (key.upArrow || normalized === 'k') {
                    setPerfDailyDetailScrollOffset((current) => Math.max(0, current - 1));
                    return;
                }
                if (key.downArrow || normalized === 'j') {
                    setPerfDailyDetailScrollOffset((current) => current + 1);
                    return;
                }
                return;
            }
            if (key.upArrow) {
                scrollSelectedPerformancePane(-1);
                return;
            }
            if (key.downArrow) {
                scrollSelectedPerformancePane(1);
                return;
            }
            if (key.leftArrow || normalized === 'h') {
                movePerfSelection('left');
                return;
            }
            if (key.rightArrow || normalized === 'l') {
                movePerfSelection('right');
                return;
            }
            if (key.return && perfSelectedBox === 'daily') {
                setPerfDailyDetailScrollOffset(0);
                setPerfDailyDetailOpen(true);
                return;
            }
        }
        if (page === 1 || page === 2) {
            if (key.upArrow) {
                const now = Date.now();
                const pane = null;
                const gap = now - lastUpArrowRef.current.at;
                if (upArrowHoldActiveRef.current) {
                    if (lastUpArrowRef.current.page === page &&
                        lastUpArrowRef.current.pane === pane &&
                        gap <= DOUBLE_UP_CONFIRM_MS) {
                        lastUpArrowRef.current = { page, pane, at: now };
                        scrollActivePaneUp();
                        return;
                    }
                    upArrowHoldActiveRef.current = false;
                }
                const isDoubleUp = lastUpArrowRef.current.page === page &&
                    lastUpArrowRef.current.pane === pane &&
                    gap <= DOUBLE_UP_JUMP_MS;
                if (isDoubleUp) {
                    if (pendingTopJumpRef.current !== null) {
                        clearPendingTopJump();
                        upArrowHoldActiveRef.current = true;
                        lastUpArrowRef.current = { page, pane, at: now };
                        scrollActivePaneUp();
                        return;
                    }
                    lastUpArrowRef.current = { page, pane, at: now };
                    pendingTopJumpRef.current = setTimeout(() => {
                        pendingTopJumpRef.current = null;
                        jumpPaneToTop(page, pane);
                        lastUpArrowRef.current = { page: null, pane: null, at: 0 };
                        upArrowHoldActiveRef.current = false;
                    }, DOUBLE_UP_CONFIRM_MS);
                    return;
                }
                clearPendingTopJump();
                upArrowHoldActiveRef.current = false;
                lastUpArrowRef.current = { page, pane, at: now };
                scrollActivePaneUp();
                return;
            }
            if (key.downArrow) {
                if (page === 1) {
                    setFeedScrollOffset((current) => current + 1);
                }
                else if (page === 2) {
                    setSignalsScrollOffset((current) => current + 1);
                }
                else if (perfActivePane === 'current') {
                    setPerfCurrentScrollOffset((current) => current + 1);
                }
                else {
                    setPerfPastScrollOffset((current) => current + 1);
                }
                return;
            }
            if (page === 2 && key.leftArrow) {
                startTransition(() => {
                    setSignalsHorizontalOffset((current) => Math.max(0, current - HORIZONTAL_SCROLL_STEP));
                });
                return;
            }
            if (page === 2 && key.rightArrow) {
                startTransition(() => {
                    setSignalsHorizontalOffset((current) => current + HORIZONTAL_SCROLL_STEP);
                });
                return;
            }
        }
        if (normalized === 'q')
            process.exit(0);
        if (normalized === 'r') {
            setIsRefreshing(true);
            setRefreshToken((current) => current + 1);
            setSettingsEditor((current) => ({
                ...current,
                values: readEditableConfigValues()
            }));
            return;
        }
        const parsed = Number.parseInt(input, 10);
        if (parsed >= 1 && parsed <= 6) {
            if (parsed === 6) {
                setSettingsEditor((current) => ({
                    ...current,
                    values: readEditableConfigValues(),
                    focusArea: 'config',
                    isEditing: false,
                    draft: '',
                    replaceDraftOnInput: false,
                    dangerConfirm: null
                }));
            }
            setPage(parsed);
        }
    });
    return (React.createElement(TerminalSizeProvider, { backgroundColor: terminalBackgroundColor },
        React.createElement(ManualRefreshProvider, { refreshToken: refreshToken },
            React.createElement(AppContent, { page: page, isRefreshing: isRefreshing, settingsEditor: settingsEditor, feedScrollOffset: feedScrollOffset, onFeedScrollOffsetChange: setFeedScrollOffset, signalsScrollOffset: signalsScrollOffset, onSignalsScrollOffsetChange: setSignalsScrollOffset, signalsHorizontalOffset: signalsHorizontalOffset, onSignalsHorizontalOffsetChange: setSignalsHorizontalOffset, perfCurrentScrollOffset: perfCurrentScrollOffset, perfPastScrollOffset: perfPastScrollOffset, perfActivePane: perfActivePane, perfSelectedBox: perfSelectedBox, perfDailyDetailOpen: perfDailyDetailOpen, perfDailyDetailScrollOffset: perfDailyDetailScrollOffset, modelSelectionIndex: modelSelectionIndex, modelDetailOpen: modelDetailOpen, modelSettingSelectionIndex: modelSettingSelectionIndex, walletPane: walletPane, walletBestSelectionIndex: walletBestSelectionIndex, walletWorstSelectionIndex: walletWorstSelectionIndex, walletTrackedSelectionIndex: walletTrackedSelectionIndex, walletDroppedSelectionIndex: walletDroppedSelectionIndex, walletDetailOpen: walletDetailOpen, onWalletMetaChange: setWalletMeta, onPerfCurrentScrollOffsetChange: setPerfCurrentScrollOffset, onPerfPastScrollOffsetChange: setPerfPastScrollOffset, onPerfDailyDetailScrollOffsetChange: setPerfDailyDetailScrollOffset }))));
}
function clearTerminal() {
    if (!process.stdout.isTTY) {
        return;
    }
    // Clear the visible screen, clear scrollback, and move the cursor home
    // before Ink draws the dashboard.
    process.stdout.write('\x1b[2J\x1b[3J\x1b[H');
}
clearTerminal();
async function bootstrap() {
    globalThis.__KELLY_WATCHER_TERMINAL_BG__ = await detectTerminalBackgroundColor();
    render(React.createElement(App, null));
}
void bootstrap();
