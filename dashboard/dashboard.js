import React, { startTransition, useEffect, useRef, useState } from 'react';
import { Box, Spacer, Text, render, useInput } from 'ink';
import { cycleDurationPreset, editableConfigFields, isPresetDurationField, readEditableConfigValues, validateEditableConfigValue, writeEditableConfigValue } from './configEditor.js';
import { theme } from './theme.js';
import { LiveFeed } from './pages/LiveFeed.js';
import { Signals } from './pages/Signals.js';
import { Performance } from './pages/Performance.js';
import { Models } from './pages/Models.js';
import { Wallets } from './pages/Wallets.js';
import { Settings } from './pages/Settings.js';
import { secondsAgo } from './format.js';
import { ManualRefreshProvider } from './refresh.js';
import { TerminalSizeProvider, useTerminalSize } from './terminal.js';
import { useBotState } from './useBotState.js';
import { useQuery } from './useDb.js';
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
function renderPage(page, settingsEditor, feedScrollOffset, signalsScrollOffset, signalsHorizontalOffset, perfCurrentScrollOffset, perfPastScrollOffset, perfActivePane, walletSelectionIndex, walletDetailOpen, onWalletCountChange) {
    switch (page) {
        case 1:
            return React.createElement(LiveFeed, { scrollOffset: feedScrollOffset });
        case 2:
            return React.createElement(Signals, { scrollOffset: signalsScrollOffset, horizontalOffset: signalsHorizontalOffset });
        case 3:
            return (React.createElement(Performance, { currentScrollOffset: perfCurrentScrollOffset, pastScrollOffset: perfPastScrollOffset, activePane: perfActivePane }));
        case 4:
            return React.createElement(Models, null);
        case 5:
            return (React.createElement(Wallets, { selectedIndex: walletSelectionIndex, detailOpen: walletDetailOpen, onWalletCountChange: onWalletCountChange }));
        case 6:
            return React.createElement(Settings, { editor: settingsEditor });
    }
}
function AppContent({ page, isRefreshing, settingsEditor, feedScrollOffset, signalsScrollOffset, signalsHorizontalOffset, perfCurrentScrollOffset, perfPastScrollOffset, perfActivePane, walletSelectionIndex, walletDetailOpen, onWalletCountChange }) {
    const terminal = useTerminalSize();
    const botState = useBotState();
    const counts = useQuery('SELECT COUNT(*) AS n FROM trade_log');
    const mode = botState.mode === 'live' ? '[LIVE]' : '[SHADOW]';
    const modeColor = botState.mode === 'live' ? theme.green : theme.dim;
    const configuredPollInterval = settingsEditor.values.POLL_INTERVAL_SECONDS?.trim();
    const pollIntervalText = configuredPollInterval && configuredPollInterval.length > 0
        ? `${configuredPollInterval}s`
        : botState.poll_interval
            ? `${botState.poll_interval}s`
            : '-';
    const now = Date.now() / 1000;
    const heartbeatWindow = Math.max((botState.poll_interval || 1) * 3, 3);
    const activityWindow = Math.max(heartbeatWindow, 30);
    const startedAt = botState.started_at ?? 0;
    const lastPollAt = botState.last_poll_at ?? 0;
    const lastActivityAt = botState.last_activity_at ?? 0;
    const currentLoopStartedAt = botState.last_loop_started_at ?? 0;
    const loopInProgress = botState.loop_in_progress ?? false;
    const pollIsFresh = lastPollAt > 0 && (now - lastPollAt) <= heartbeatWindow;
    const activityIsFresh = lastActivityAt > 0 && (now - lastActivityAt) <= activityWindow;
    const backendDotColor = pollIsFresh
        ? theme.green
        : startedAt > 0 && activityIsFresh && (loopInProgress || lastPollAt <= 0)
            ? theme.yellow
            : theme.red;
    const navLabels = terminal.compact
        ? { 1: 'F', 2: 'S', 3: 'P', 4: 'M', 5: 'W', 6: 'C' }
        : terminal.narrow
            ? { 1: 'Track', 2: 'Sig', 3: 'Perf', 4: 'Mod', 5: 'Wall', 6: 'Cfg' }
            : { 1: 'Tracker', 2: 'Signals', 3: 'Perf', 4: 'Models', 5: 'Wallets', 6: 'Config' };
    const footerCompact = terminal.compact;
    const currentPollElapsedText = !currentLoopStartedAt || currentLoopStartedAt <= 0
        ? null
        : `${Math.max(0, Math.floor(now - currentLoopStartedAt))}s`;
    const lastPollText = loopInProgress
        ? `polling...${currentPollElapsedText ? ` ${currentPollElapsedText}` : ''} | last poll: ${secondsAgo(botState.last_poll_at)}`
        : `last poll: ${secondsAgo(botState.last_poll_at)}`;
    const footerControls = page === 1
        ? terminal.compact
            ? '↑↓ scroll  ↑↑ latest  r refresh  q exit'
            : '↑/↓: scroll  ↑↑: latest  r: refresh  q: exit'
        : page === 2
            ? terminal.compact
                ? '↑↓ scroll  ←→ pan  ↑↑ latest  r refresh  q exit'
                : '↑/↓: scroll  ←/→: pan  ↑↑: latest  r: refresh  q: exit'
            : page === 3
                ? terminal.compact
                    ? `pane:${perfActivePane === 'current' ? 'current' : 'past'}  ↑↓ scroll  ←→ pane  ↑↑ top  r refresh  q exit`
                    : `pane: ${perfActivePane === 'current' ? 'current' : 'past'}  ↑/↓: scroll  ←/→: pane  ↑↑: top  r: refresh  q: exit`
                : page === 5
                    ? terminal.compact
                        ? '↑↓ select  enter detail  esc close  r refresh  q exit'
                        : '↑/↓: select  Enter: detail  Esc: close  r: refresh  q: exit'
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
            React.createElement(Text, { color: modeColor, bold: true }, mode)),
        React.createElement(Box, { padding: 1, flexGrow: 1 }, renderPage(page, settingsEditor, feedScrollOffset, signalsScrollOffset, signalsHorizontalOffset, perfCurrentScrollOffset, perfPastScrollOffset, perfActivePane, walletSelectionIndex, walletDetailOpen, onWalletCountChange)),
        React.createElement(Box, { borderStyle: "round", borderColor: theme.border, paddingX: 1 }, footerCompact ? (React.createElement(React.Fragment, null,
            React.createElement(Text, { color: theme.dim },
                "w:",
                botState.n_wallets || 0,
                "  int:",
                pollIntervalText,
                "  ",
                footerControls),
            React.createElement(Spacer, null),
            React.createElement(Text, { color: isRefreshing ? theme.accent : theme.dim }, isRefreshing ? 'refreshing...' : lastPollText))) : (React.createElement(React.Fragment, null,
            React.createElement(Text, { color: theme.dim },
                "wallets: ",
                botState.n_wallets || 0,
                "  "),
            React.createElement(Text, { color: theme.dim },
                "poll interval: ",
                pollIntervalText,
                "  "),
            React.createElement(Text, { color: theme.dim },
                "db rows: ",
                counts[0]?.n || 0,
                "  "),
            React.createElement(Text, { color: theme.dim }, footerControls),
            React.createElement(Spacer, null),
            React.createElement(Text, { color: isRefreshing ? theme.accent : theme.dim }, isRefreshing ? 'refreshing...' : lastPollText))))));
}
function App() {
    const [page, setPage] = useState(1);
    const [refreshToken, setRefreshToken] = useState(0);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [feedScrollOffset, setFeedScrollOffset] = useState(0);
    const [signalsScrollOffset, setSignalsScrollOffset] = useState(0);
    const [signalsHorizontalOffset, setSignalsHorizontalOffset] = useState(0);
    const [perfCurrentScrollOffset, setPerfCurrentScrollOffset] = useState(0);
    const [perfPastScrollOffset, setPerfPastScrollOffset] = useState(0);
    const [perfActivePane, setPerfActivePane] = useState('current');
    const [walletSelectionIndex, setWalletSelectionIndex] = useState(0);
    const [walletDetailOpen, setWalletDetailOpen] = useState(false);
    const [walletCount, setWalletCount] = useState(0);
    const lastUpArrowRef = useRef({ page: null, pane: null, at: 0 });
    const pendingTopJumpRef = useRef(null);
    const upArrowHoldActiveRef = useRef(false);
    const [settingsEditor, setSettingsEditor] = useState(() => ({
        values: readEditableConfigValues(),
        selectedIndex: 0,
        isEditing: false,
        draft: '',
        replaceDraftOnInput: false,
        statusMessage: 'Use j/k or arrows to select a setting, then press e to edit.',
        statusTone: 'info'
    }));
    const selectedField = editableConfigFields[settingsEditor.selectedIndex];
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
                isEditing: false,
                draft: '',
                replaceDraftOnInput: false,
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
            isEditing: true,
            draft: current.values[selectedField.key] || selectedField.defaultValue,
            replaceDraftOnInput: true,
            statusMessage: isPresetDurationField(selectedField)
                ? `Editing ${selectedField.label}. Use left/right to toggle presets, Enter to save, or Esc to cancel.`
                : `Editing ${selectedField.label}. Press Enter to save or Esc to cancel.`,
            statusTone: 'info'
        }));
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
        if (walletCount <= 0) {
            setWalletSelectionIndex(0);
            if (walletDetailOpen) {
                setWalletDetailOpen(false);
            }
            return;
        }
        setWalletSelectionIndex((current) => Math.min(current, walletCount - 1));
    }, [walletCount, walletDetailOpen]);
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
    useInput((input, key) => {
        const normalized = input.toLowerCase();
        if (!key.upArrow) {
            resetUpArrowState();
        }
        if (page === 6) {
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
                if ((key.leftArrow || key.rightArrow) && isPresetDurationField(selectedField)) {
                    const nextValue = cycleDurationPreset(selectedField, settingsEditor.draft || settingsEditor.values[selectedField.key] || selectedField.defaultValue, key.rightArrow ? 'right' : 'left');
                    if (nextValue) {
                        setSettingsEditor((current) => ({
                            ...current,
                            draft: nextValue,
                            replaceDraftOnInput: false,
                            statusMessage: `Editing ${selectedField.label}. Use left/right to toggle presets, Enter to save, or Esc to cancel.`,
                            statusTone: 'info'
                        }));
                    }
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
                setSettingsEditor((current) => ({
                    ...current,
                    selectedIndex: (current.selectedIndex - 1 + editableConfigFields.length) % editableConfigFields.length,
                    statusMessage: editableConfigFields[(current.selectedIndex - 1 + editableConfigFields.length) % editableConfigFields.length].description,
                    statusTone: 'info'
                }));
                return;
            }
            if (key.downArrow || normalized === 'j') {
                setSettingsEditor((current) => ({
                    ...current,
                    selectedIndex: (current.selectedIndex + 1) % editableConfigFields.length,
                    statusMessage: editableConfigFields[(current.selectedIndex + 1) % editableConfigFields.length].description,
                    statusTone: 'info'
                }));
                return;
            }
            if (normalized === 'e' || key.return) {
                beginConfigEdit();
                return;
            }
        }
        if (page === 5) {
            if (key.escape && walletDetailOpen) {
                setWalletDetailOpen(false);
                return;
            }
            if ((key.upArrow || normalized === 'k') && walletCount > 0) {
                setWalletSelectionIndex((current) => (current <= 0 ? walletCount - 1 : current - 1));
                return;
            }
            if ((key.downArrow || normalized === 'j') && walletCount > 0) {
                setWalletSelectionIndex((current) => (current >= walletCount - 1 ? 0 : current + 1));
                return;
            }
            if (key.return && walletCount > 0) {
                setWalletDetailOpen(true);
                return;
            }
        }
        if (page === 1 || page === 2 || page === 3) {
            if (key.upArrow) {
                const now = Date.now();
                const pane = page === 3 ? perfActivePane : null;
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
            if (page === 3 && key.leftArrow) {
                setPerfActivePane('current');
                return;
            }
            if (page === 3 && key.rightArrow) {
                setPerfActivePane('past');
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
                    isEditing: false,
                    draft: '',
                    replaceDraftOnInput: false
                }));
            }
            setPage(parsed);
        }
    });
    return (React.createElement(TerminalSizeProvider, null,
        React.createElement(ManualRefreshProvider, { refreshToken: refreshToken },
            React.createElement(AppContent, { page: page, isRefreshing: isRefreshing, settingsEditor: settingsEditor, feedScrollOffset: feedScrollOffset, signalsScrollOffset: signalsScrollOffset, signalsHorizontalOffset: signalsHorizontalOffset, perfCurrentScrollOffset: perfCurrentScrollOffset, perfPastScrollOffset: perfPastScrollOffset, perfActivePane: perfActivePane, walletSelectionIndex: walletSelectionIndex, walletDetailOpen: walletDetailOpen, onWalletCountChange: setWalletCount }))));
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
render(React.createElement(App, null));
