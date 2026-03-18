import React, {startTransition, useEffect, useRef, useState} from 'react'
import {Box, Spacer, Text, render, useInput} from 'ink'
import {
  cycleDurationPreset,
  editableConfigFields,
  isPresetDurationField,
  readEditableConfigValues,
  validateEditableConfigValue,
  writeEditableConfigValue
} from './configEditor.js'
import {MODEL_PANEL_DEFS, Models} from './pages/Models.js'
import {stackPanels} from './responsive.js'
import {theme} from './theme.js'
import {LiveFeed} from './pages/LiveFeed.js'
import {Signals} from './pages/Signals.js'
import {Performance, type PerfBox} from './pages/Performance.js'
import {Wallets} from './pages/Wallets.js'
import {Settings, type SettingsEditorState} from './pages/Settings.js'
import {secondsAgo} from './format.js'
import {ManualRefreshProvider} from './refresh.js'
import {detectTerminalBackgroundColor, TerminalSizeProvider, useTerminalSize} from './terminal.js'
import {useBotState} from './useBotState.js'
import {useQuery} from './useDb.js'

type Page = 1 | 2 | 3 | 4 | 5 | 6
type PerfPane = 'current' | 'past'
const DOUBLE_UP_JUMP_MS = 350
const DOUBLE_UP_CONFIRM_MS = 140
const HORIZONTAL_SCROLL_STEP = 8

const PAGES: Record<Page, {label: string}> = {
  1: {label: 'Tracker'},
  2: {label: 'Signals'},
  3: {label: 'Perf'},
  4: {label: 'Models'},
  5: {label: 'Wallets'},
  6: {label: 'Config'}
}

interface CountRow {
  n: number
}

function formatCurrentPollElapsedSeconds(nowSeconds: number, startedAtSeconds?: number): string | null {
  if (!startedAtSeconds || startedAtSeconds <= 0) {
    return null
  }
  return `${Math.max(0, Math.floor(nowSeconds - startedAtSeconds))}s`
}

interface AppContentProps {
  page: Page
  isRefreshing: boolean
  settingsEditor: SettingsEditorState
  feedScrollOffset: number
  signalsScrollOffset: number
  signalsHorizontalOffset: number
  perfCurrentScrollOffset: number
  perfPastScrollOffset: number
  perfActivePane: PerfPane
  perfSelectedBox: PerfBox
  perfDailyDetailOpen: boolean
  perfDailyDetailScrollOffset: number
  modelSelectionIndex: number
  modelDetailOpen: boolean
  modelSettingSelectionIndex: number
  walletSelectionIndex: number
  walletDetailOpen: boolean
  onWalletCountChange: (count: number) => void
}

function renderPage(
  page: Page,
  settingsEditor: SettingsEditorState,
  feedScrollOffset: number,
  signalsScrollOffset: number,
  signalsHorizontalOffset: number,
  perfCurrentScrollOffset: number,
  perfPastScrollOffset: number,
  perfActivePane: PerfPane,
  perfSelectedBox: PerfBox,
  perfDailyDetailOpen: boolean,
  perfDailyDetailScrollOffset: number,
  modelSelectionIndex: number,
  modelDetailOpen: boolean,
  modelSettingSelectionIndex: number,
  settingsValues: SettingsEditorState['values'],
  walletSelectionIndex: number,
  walletDetailOpen: boolean,
  onWalletCountChange: (count: number) => void
) {
  switch (page) {
    case 1:
      return <LiveFeed scrollOffset={feedScrollOffset} />
    case 2:
      return <Signals scrollOffset={signalsScrollOffset} horizontalOffset={signalsHorizontalOffset} />
    case 3:
      return (
        <Performance
          currentScrollOffset={perfCurrentScrollOffset}
          pastScrollOffset={perfPastScrollOffset}
          activePane={perfActivePane}
          selectedBox={perfSelectedBox}
          dailyDetailOpen={perfDailyDetailOpen}
          dailyDetailScrollOffset={perfDailyDetailScrollOffset}
        />
      )
    case 4:
      return (
        <Models
          selectedPanelIndex={modelSelectionIndex}
          detailOpen={modelDetailOpen}
          selectedSettingIndex={modelSettingSelectionIndex}
          settingsValues={settingsValues}
        />
      )
    case 5:
      return (
        <Wallets
          selectedIndex={walletSelectionIndex}
          detailOpen={walletDetailOpen}
          onWalletCountChange={onWalletCountChange}
        />
      )
    case 6:
      return <Settings editor={settingsEditor} />
  }
}

function AppContent({
  page,
  isRefreshing,
  settingsEditor,
  feedScrollOffset,
  signalsScrollOffset,
  signalsHorizontalOffset,
  perfCurrentScrollOffset,
  perfPastScrollOffset,
  perfActivePane,
  perfSelectedBox,
  perfDailyDetailOpen,
  perfDailyDetailScrollOffset,
  modelSelectionIndex,
  modelDetailOpen,
  modelSettingSelectionIndex,
  walletSelectionIndex,
  walletDetailOpen,
  onWalletCountChange
}: AppContentProps) {
  const terminal = useTerminalSize()
  const botState = useBotState()
  const counts = useQuery<CountRow>('SELECT COUNT(*) AS n FROM trade_log')
  const mode = botState.mode === 'live' ? '[LIVE]' : '[SHADOW]'
  const modeColor = botState.mode === 'live' ? theme.green : theme.dim
  const configuredPollInterval = settingsEditor.values.POLL_INTERVAL_SECONDS?.trim()
  const pollIntervalText =
    configuredPollInterval && configuredPollInterval.length > 0
      ? `${configuredPollInterval}s`
      : botState.poll_interval
        ? `${botState.poll_interval}s`
        : '-'
  const now = Date.now() / 1000
  const heartbeatWindow = Math.max((botState.poll_interval || 1) * 3, 3)
  const activityWindow = Math.max(heartbeatWindow, 30)
  const startedAt = botState.started_at ?? 0
  const lastPollAt = botState.last_poll_at ?? 0
  const lastActivityAt = botState.last_activity_at ?? 0
  const currentLoopStartedAt = botState.last_loop_started_at ?? 0
  const loopInProgress = botState.loop_in_progress ?? false
  const pollIsFresh = lastPollAt > 0 && (now - lastPollAt) <= heartbeatWindow
  const activityIsFresh = lastActivityAt > 0 && (now - lastActivityAt) <= activityWindow
  const backendDotColor = pollIsFresh
    ? theme.green
    : startedAt > 0 && activityIsFresh && (loopInProgress || lastPollAt <= 0)
      ? theme.yellow
      : theme.red
  const navLabels = terminal.compact
    ? {1: 'F', 2: 'S', 3: 'P', 4: 'M', 5: 'W', 6: 'C'}
    : terminal.narrow
      ? {1: 'Track', 2: 'Sig', 3: 'Perf', 4: 'Mod', 5: 'Wall', 6: 'Cfg'}
      : {1: 'Tracker', 2: 'Signals', 3: 'Perf', 4: 'Models', 5: 'Wallets', 6: 'Config'}
  const footerCompact = terminal.compact
  const currentPollElapsedText = formatCurrentPollElapsedSeconds(now, currentLoopStartedAt)
  const lastPollText = loopInProgress
    ? `polling...${currentPollElapsedText ? ` ${currentPollElapsedText}` : ''} | last poll: ${secondsAgo(botState.last_poll_at)}`
    : `last poll: ${secondsAgo(botState.last_poll_at)}`
  const footerControls =
    page === 1
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
            : '↑/↓: list  Esc: close  r: refresh  q: exit'
          : terminal.compact
            ? `sel:${perfSelectedBox}  arrows box  j/k scroll  enter detail  r refresh  q exit`
            : `selected: ${perfSelectedBox}  arrows: select box  j/k: scroll positions  Enter: daily detail  r: refresh  q: exit`
      : page === 4
        ? modelDetailOpen
          ? terminal.compact
            ? '↑↓ settings  enter edit  esc close  r refresh  q exit'
            : '↑/↓: settings  Enter: edit in config  Esc: close  r: refresh  q: exit'
          : terminal.compact
            ? '↑↓/←→ select  enter help  r refresh  q exit'
            : '↑/↓/←/→: select  Enter: help  r: refresh  q: exit'
      : page === 5
        ? terminal.compact
          ? '↑↓ select  enter detail  esc close  r refresh  q exit'
          : '↑/↓: select  Enter: detail  Esc: close  r: refresh  q: exit'
      : terminal.compact
        ? 'r refresh  q exit'
        : 'r: refresh  q: exit'

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.accent} width={terminal.width} height={terminal.height}>
      <Box borderStyle="round" borderColor={theme.border} paddingX={1}>
        <Text color={backendDotColor}>●</Text>
        <Text> </Text>
        <Text color={theme.white} bold>KELLY-WATCHER</Text>
        <Text>  </Text>
        {(Object.entries(PAGES) as Array<[string, {label: string}]>).map(([key, value]) => {
          const isSelected = Number(key) === page
          const label = `${key}:${navLabels[Number(key) as keyof typeof navLabels] || value.label}`
          return (
            <React.Fragment key={key}>
              <Text color={isSelected ? theme.white : theme.dim} bold={isSelected}>
                {isSelected ? `[${label}]` : label}
              </Text>
              <Text>  </Text>
            </React.Fragment>
          )
        })}
        <Spacer />
        <Text color={modeColor} bold>{mode}</Text>
      </Box>

      <Box padding={1} flexGrow={1}>
        {renderPage(
          page,
          settingsEditor,
          feedScrollOffset,
          signalsScrollOffset,
          signalsHorizontalOffset,
          perfCurrentScrollOffset,
          perfPastScrollOffset,
          perfActivePane,
          perfSelectedBox,
          perfDailyDetailOpen,
          perfDailyDetailScrollOffset,
          modelSelectionIndex,
          modelDetailOpen,
          modelSettingSelectionIndex,
          settingsEditor.values,
          walletSelectionIndex,
          walletDetailOpen,
          onWalletCountChange
        )}
      </Box>

      <Box borderStyle="round" borderColor={theme.border} paddingX={1}>
        {footerCompact ? (
          <>
            <Text color={theme.dim}>w:{botState.n_wallets || 0}  int:{pollIntervalText}  {footerControls}</Text>
            <Spacer />
            <Text color={isRefreshing ? theme.accent : theme.dim}>{isRefreshing ? 'refreshing...' : lastPollText}</Text>
          </>
        ) : (
          <>
            <Text color={theme.dim}>wallets: {botState.n_wallets || 0}  </Text>
            <Text color={theme.dim}>poll interval: {pollIntervalText}  </Text>
            <Text color={theme.dim}>db rows: {counts[0]?.n || 0}  </Text>
            <Text color={theme.dim}>{footerControls}</Text>
            <Spacer />
            <Text color={isRefreshing ? theme.accent : theme.dim}>{isRefreshing ? 'refreshing...' : lastPollText}</Text>
          </>
        )}
      </Box>
    </Box>
  )
}

function App() {
  const [terminalBackgroundColor] = useState<string | undefined>(() => globalThis.__KELLY_WATCHER_TERMINAL_BG__)
  const [page, setPage] = useState<Page>(1)
  const [refreshToken, setRefreshToken] = useState(0)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [feedScrollOffset, setFeedScrollOffset] = useState(0)
  const [signalsScrollOffset, setSignalsScrollOffset] = useState(0)
  const [signalsHorizontalOffset, setSignalsHorizontalOffset] = useState(0)
  const [perfCurrentScrollOffset, setPerfCurrentScrollOffset] = useState(0)
  const [perfPastScrollOffset, setPerfPastScrollOffset] = useState(0)
  const [perfActivePane, setPerfActivePane] = useState<PerfPane>('current')
  const [perfSelectedBox, setPerfSelectedBox] = useState<PerfBox>('current')
  const [perfDailyDetailOpen, setPerfDailyDetailOpen] = useState(false)
  const [perfDailyDetailScrollOffset, setPerfDailyDetailScrollOffset] = useState(0)
  const [modelSelectionIndex, setModelSelectionIndex] = useState(0)
  const [modelDetailOpen, setModelDetailOpen] = useState(false)
  const [modelSettingSelectionIndex, setModelSettingSelectionIndex] = useState(0)
  const [walletSelectionIndex, setWalletSelectionIndex] = useState(0)
  const [walletDetailOpen, setWalletDetailOpen] = useState(false)
  const [walletCount, setWalletCount] = useState(0)
  const lastUpArrowRef = useRef<{page: Page | null; pane: PerfPane | null; at: number}>({page: null, pane: null, at: 0})
  const pendingTopJumpRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const upArrowHoldActiveRef = useRef(false)
  const [settingsEditor, setSettingsEditor] = useState<SettingsEditorState>(() => ({
    values: readEditableConfigValues(),
    selectedIndex: 0,
    isEditing: false,
    draft: '',
    replaceDraftOnInput: false,
    statusMessage: 'Use j/k or arrows to select a setting, then press e to edit.',
    statusTone: 'info'
  }))

  const selectedField = editableConfigFields[settingsEditor.selectedIndex]
  const selectedModelPanel = MODEL_PANEL_DEFS[Math.max(0, Math.min(modelSelectionIndex, MODEL_PANEL_DEFS.length - 1))]
  const selectedModelSettingKeys = selectedModelPanel?.settingKeys || []

  const moveModelSelection = (direction: 'up' | 'down' | 'left' | 'right') => {
    const panelCount = MODEL_PANEL_DEFS.length
    const width = process.stdout.columns || 120
    const columns = stackPanels(width) ? 1 : 2

    setModelSelectionIndex((current) => {
      if (panelCount <= 1) {
        return 0
      }

      if (columns === 1) {
        if (direction === 'up' || direction === 'left') {
          return (current - 1 + panelCount) % panelCount
        }
        return (current + 1) % panelCount
      }

      const rowCount = Math.ceil(panelCount / columns)
      const row = Math.floor(current / columns)
      const column = current % columns

      if (direction === 'left') {
        const target = row * columns + ((column - 1 + columns) % columns)
        return target >= panelCount ? current : target
      }

      if (direction === 'right') {
        const target = row * columns + ((column + 1) % columns)
        return target >= panelCount ? current : target
      }

      const nextRow = direction === 'up' ? (row - 1 + rowCount) % rowCount : (row + 1) % rowCount
      const target = nextRow * columns + column
      return target >= panelCount ? panelCount - 1 : target
    })
    setModelSettingSelectionIndex(0)
  }

  const movePerfSelection = (direction: 'up' | 'down' | 'left' | 'right') => {
    const grid: PerfBox[][] = [
      ['summary', 'daily'],
      ['current', 'past']
    ]
    const locations: Record<PerfBox, [number, number]> = {
      summary: [0, 0],
      daily: [0, 1],
      current: [1, 0],
      past: [1, 1]
    }

    setPerfSelectedBox((current) => {
      const [row, column] = locations[current]
      const nextRow =
        direction === 'up'
          ? Math.max(0, row - 1)
          : direction === 'down'
            ? Math.min(grid.length - 1, row + 1)
            : row
      const nextColumn =
        direction === 'left'
          ? Math.max(0, column - 1)
          : direction === 'right'
            ? Math.min(grid[0].length - 1, column + 1)
            : column
      const next = grid[nextRow][nextColumn]
      if (next === 'current' || next === 'past') {
        setPerfActivePane(next)
      }
      return next
    })
  }

  const saveConfigValue = (rawValue: string) => {
    const validation = validateEditableConfigValue(selectedField, rawValue)
    if (!validation.ok) {
      setSettingsEditor((current) => ({
        ...current,
        statusMessage: validation.error,
        statusTone: 'error'
      }))
      return
    }

    try {
      writeEditableConfigValue(selectedField.key, validation.value)
      const values = readEditableConfigValues()
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
      }))
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown write error'
      setSettingsEditor((current) => ({
        ...current,
        statusMessage: `Failed to save ${selectedField.label}: ${message}`,
        statusTone: 'error'
      }))
    }
  }

  const beginConfigEdit = () => {
    const currentValue = settingsEditor.values[selectedField.key] || selectedField.defaultValue
    if (selectedField.kind === 'bool') {
      const nextValue = currentValue.toLowerCase() === 'true' ? 'false' : 'true'
      saveConfigValue(nextValue)
      return
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
      }))
  }

  const openConfigField = (fieldKey: string) => {
    const fieldIndex = editableConfigFields.findIndex((field) => field.key === fieldKey)
    if (fieldIndex < 0) {
      return
    }

    const values = readEditableConfigValues()
    const field = editableConfigFields[fieldIndex]
    const currentValue = values[field.key] || field.defaultValue

    setModelDetailOpen(false)
    setPage(6)
    setSettingsEditor((current) => ({
      ...current,
      values,
      selectedIndex: fieldIndex,
      isEditing: field.kind !== 'bool',
      draft: field.kind === 'bool' ? '' : currentValue,
      replaceDraftOnInput: field.kind !== 'bool',
      statusMessage: field.kind === 'bool'
        ? field.description
        : isPresetDurationField(field)
          ? `Editing ${field.label}. Use left/right to toggle presets, Enter to save, or Esc to cancel.`
          : `Editing ${field.label}. Press Enter to save or Esc to cancel.`,
      statusTone: 'info'
    }))
  }

  useEffect(() => {
    if (refreshToken === 0 || !isRefreshing) {
      return
    }

    // Keep the footer indicator visible long enough to confirm the refresh keypress.
    const timer = setTimeout(() => {
      setIsRefreshing(false)
    }, 400)

    return () => clearTimeout(timer)
  }, [refreshToken, isRefreshing])

  useEffect(() => {
    return () => {
      if (pendingTopJumpRef.current !== null) {
        clearTimeout(pendingTopJumpRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (page !== 5 && walletDetailOpen) {
      setWalletDetailOpen(false)
    }
  }, [page, walletDetailOpen])

  useEffect(() => {
    if (page !== 4 && modelDetailOpen) {
      setModelDetailOpen(false)
    }
  }, [page, modelDetailOpen])

  useEffect(() => {
    if (page !== 3 && perfDailyDetailOpen) {
      setPerfDailyDetailOpen(false)
    }
  }, [page, perfDailyDetailOpen])

  useEffect(() => {
    if (walletCount <= 0) {
      setWalletSelectionIndex(0)
      if (walletDetailOpen) {
        setWalletDetailOpen(false)
      }
      return
    }

    setWalletSelectionIndex((current) => Math.min(current, walletCount - 1))
  }, [walletCount, walletDetailOpen])

  useEffect(() => {
    setModelSelectionIndex((current) => Math.min(current, MODEL_PANEL_DEFS.length - 1))
  }, [])

  useEffect(() => {
    if (selectedModelSettingKeys.length <= 0) {
      setModelSettingSelectionIndex(0)
      return
    }
    setModelSettingSelectionIndex((current) => Math.min(current, selectedModelSettingKeys.length - 1))
  }, [selectedModelSettingKeys.length])

  const clearPendingTopJump = () => {
    if (pendingTopJumpRef.current !== null) {
      clearTimeout(pendingTopJumpRef.current)
      pendingTopJumpRef.current = null
    }
  }

  const resetUpArrowState = () => {
    clearPendingTopJump()
    lastUpArrowRef.current = {page: null, pane: null, at: 0}
    upArrowHoldActiveRef.current = false
  }

  const scrollActivePaneUp = () => {
    if (page === 1) {
      setFeedScrollOffset((current) => Math.max(0, current - 1))
    } else if (page === 2) {
      setSignalsScrollOffset((current) => Math.max(0, current - 1))
    } else if (perfActivePane === 'current') {
      setPerfCurrentScrollOffset((current) => Math.max(0, current - 1))
    } else {
      setPerfPastScrollOffset((current) => Math.max(0, current - 1))
    }
  }

  const jumpPaneToTop = (targetPage: Page, targetPane: PerfPane | null) => {
    if (targetPage === 1) {
      setFeedScrollOffset(0)
    } else if (targetPage === 2) {
      setSignalsScrollOffset(0)
    } else if (targetPane === 'current') {
      setPerfCurrentScrollOffset(0)
    } else {
      setPerfPastScrollOffset(0)
    }
  }

  const scrollSelectedPerformancePane = (delta: number) => {
    if (perfSelectedBox === 'current') {
      if (delta < 0) {
        setPerfCurrentScrollOffset((current) => Math.max(0, current - 1))
      } else {
        setPerfCurrentScrollOffset((current) => current + 1)
      }
      return
    }

    if (perfSelectedBox === 'past') {
      if (delta < 0) {
        setPerfPastScrollOffset((current) => Math.max(0, current - 1))
      } else {
        setPerfPastScrollOffset((current) => current + 1)
      }
    }
  }

  useInput((input, key) => {
    const normalized = input.toLowerCase()

    if (!key.upArrow) {
      resetUpArrowState()
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
          }))
          return
        }

        if (key.return) {
          saveConfigValue(settingsEditor.draft)
          return
        }

        if ((key.leftArrow || key.rightArrow) && isPresetDurationField(selectedField)) {
          const nextValue = cycleDurationPreset(
            selectedField,
            settingsEditor.draft || settingsEditor.values[selectedField.key] || selectedField.defaultValue,
            key.rightArrow ? 'right' : 'left'
          )
          if (nextValue) {
            setSettingsEditor((current) => ({
              ...current,
              draft: nextValue,
              replaceDraftOnInput: false,
              statusMessage: `Editing ${selectedField.label}. Use left/right to toggle presets, Enter to save, or Esc to cancel.`,
              statusTone: 'info'
            }))
          }
          return
        }

        if (key.backspace || key.delete) {
          setSettingsEditor((current) => ({
            ...current,
            draft: current.replaceDraftOnInput ? '' : current.draft.slice(0, -1),
            replaceDraftOnInput: false
          }))
          return
        }

        const accepts =
          selectedField.kind === 'int'
            ? /^[0-9]$/
            : selectedField.kind === 'choice'
              ? /^[a-z0-9_-]$/i
            : selectedField.kind === 'duration'
              ? /^[0-9a-z.]$/i
              : /^[0-9.]$/
        if (accepts.test(input)) {
          setSettingsEditor((current) => ({
            ...current,
            draft: current.replaceDraftOnInput
              ? input.toLowerCase()
              : `${current.draft}${input.toLowerCase()}`,
            replaceDraftOnInput: false
          }))
          return
        }

        return
      }

      if (key.upArrow || normalized === 'k') {
        setSettingsEditor((current) => ({
          ...current,
          selectedIndex: (current.selectedIndex - 1 + editableConfigFields.length) % editableConfigFields.length,
          statusMessage: editableConfigFields[(current.selectedIndex - 1 + editableConfigFields.length) % editableConfigFields.length].description,
          statusTone: 'info'
        }))
        return
      }

      if (key.downArrow || normalized === 'j') {
        setSettingsEditor((current) => ({
          ...current,
          selectedIndex: (current.selectedIndex + 1) % editableConfigFields.length,
          statusMessage: editableConfigFields[(current.selectedIndex + 1) % editableConfigFields.length].description,
          statusTone: 'info'
        }))
        return
      }

      if (normalized === 'e' || key.return) {
        beginConfigEdit()
        return
      }
    }

    if (page === 4) {
      if (modelDetailOpen) {
        if (key.escape) {
          setModelDetailOpen(false)
          return
        }

        if ((key.upArrow || normalized === 'k') && selectedModelSettingKeys.length > 0) {
          setModelSettingSelectionIndex((current) =>
            current <= 0 ? selectedModelSettingKeys.length - 1 : current - 1
          )
          return
        }

        if ((key.downArrow || normalized === 'j') && selectedModelSettingKeys.length > 0) {
          setModelSettingSelectionIndex((current) =>
            current >= selectedModelSettingKeys.length - 1 ? 0 : current + 1
          )
          return
        }

        if ((key.return || normalized === 'e') && selectedModelSettingKeys.length > 0) {
          openConfigField(selectedModelSettingKeys[modelSettingSelectionIndex] || selectedModelSettingKeys[0])
          return
        }

        return
      }

      if (key.upArrow || normalized === 'k') {
        moveModelSelection('up')
        return
      }

      if (key.downArrow || normalized === 'j') {
        moveModelSelection('down')
        return
      }

      if (key.leftArrow || normalized === 'h') {
        moveModelSelection('left')
        return
      }

      if (key.rightArrow || normalized === 'l') {
        moveModelSelection('right')
        return
      }

      if (key.return) {
        setModelSettingSelectionIndex(0)
        setModelDetailOpen(true)
        return
      }
    }

    if (page === 5) {
      if (key.escape && walletDetailOpen) {
        setWalletDetailOpen(false)
        return
      }

      if ((key.upArrow || normalized === 'k') && walletCount > 0) {
        setWalletSelectionIndex((current) => (current <= 0 ? walletCount - 1 : current - 1))
        return
      }

      if ((key.downArrow || normalized === 'j') && walletCount > 0) {
        setWalletSelectionIndex((current) => (current >= walletCount - 1 ? 0 : current + 1))
        return
      }

      if (key.return && walletCount > 0) {
        setWalletDetailOpen(true)
        return
      }
    }

    if (page === 3) {
      if (perfDailyDetailOpen) {
        if (key.escape) {
          setPerfDailyDetailOpen(false)
          return
        }

        if (key.upArrow || normalized === 'k') {
          setPerfDailyDetailScrollOffset((current) => Math.max(0, current - 1))
          return
        }

        if (key.downArrow || normalized === 'j') {
          setPerfDailyDetailScrollOffset((current) => current + 1)
          return
        }

        return
      }

      if (key.upArrow) {
        movePerfSelection('up')
        return
      }

      if (key.downArrow) {
        movePerfSelection('down')
        return
      }

      if (key.leftArrow || normalized === 'h') {
        movePerfSelection('left')
        return
      }

      if (key.rightArrow || normalized === 'l') {
        movePerfSelection('right')
        return
      }

      if (normalized === 'k') {
        scrollSelectedPerformancePane(-1)
        return
      }

      if (normalized === 'j') {
        scrollSelectedPerformancePane(1)
        return
      }

      if (key.return && perfSelectedBox === 'daily') {
        setPerfDailyDetailScrollOffset(0)
        setPerfDailyDetailOpen(true)
        return
      }
    }

    if (page === 1 || page === 2) {
      if (key.upArrow) {
        const now = Date.now()
        const pane = null
        const gap = now - lastUpArrowRef.current.at

        if (upArrowHoldActiveRef.current) {
          if (
            lastUpArrowRef.current.page === page &&
            lastUpArrowRef.current.pane === pane &&
            gap <= DOUBLE_UP_CONFIRM_MS
          ) {
            lastUpArrowRef.current = {page, pane, at: now}
            scrollActivePaneUp()
            return
          }

          upArrowHoldActiveRef.current = false
        }

        const isDoubleUp =
          lastUpArrowRef.current.page === page &&
          lastUpArrowRef.current.pane === pane &&
          gap <= DOUBLE_UP_JUMP_MS

        if (isDoubleUp) {
          if (pendingTopJumpRef.current !== null) {
            clearPendingTopJump()
            upArrowHoldActiveRef.current = true
            lastUpArrowRef.current = {page, pane, at: now}
            scrollActivePaneUp()
            return
          }

          lastUpArrowRef.current = {page, pane, at: now}
          pendingTopJumpRef.current = setTimeout(() => {
            pendingTopJumpRef.current = null
            jumpPaneToTop(page, pane)
            lastUpArrowRef.current = {page: null, pane: null, at: 0}
            upArrowHoldActiveRef.current = false
          }, DOUBLE_UP_CONFIRM_MS)
          return
        }

        clearPendingTopJump()
        upArrowHoldActiveRef.current = false
        lastUpArrowRef.current = {page, pane, at: now}
        scrollActivePaneUp()
        return
      }

      if (key.downArrow) {
        if (page === 1) {
          setFeedScrollOffset((current) => current + 1)
        } else if (page === 2) {
          setSignalsScrollOffset((current) => current + 1)
        } else if (perfActivePane === 'current') {
          setPerfCurrentScrollOffset((current) => current + 1)
        } else {
          setPerfPastScrollOffset((current) => current + 1)
        }
        return
      }

      if (page === 2 && key.leftArrow) {
        startTransition(() => {
          setSignalsHorizontalOffset((current) => Math.max(0, current - HORIZONTAL_SCROLL_STEP))
        })
        return
      }

      if (page === 2 && key.rightArrow) {
        startTransition(() => {
          setSignalsHorizontalOffset((current) => current + HORIZONTAL_SCROLL_STEP)
        })
        return
      }
    }

    if (normalized === 'q') process.exit(0)
    if (normalized === 'r') {
      setIsRefreshing(true)
      setRefreshToken((current) => current + 1)
      setSettingsEditor((current) => ({
        ...current,
        values: readEditableConfigValues()
      }))
      return
    }
    const parsed = Number.parseInt(input, 10)
    if (parsed >= 1 && parsed <= 6) {
      if (parsed === 6) {
        setSettingsEditor((current) => ({
          ...current,
          values: readEditableConfigValues(),
          isEditing: false,
          draft: '',
          replaceDraftOnInput: false
        }))
      }
      setPage(parsed as Page)
    }
  })

  return (
    <TerminalSizeProvider backgroundColor={terminalBackgroundColor}>
      <ManualRefreshProvider refreshToken={refreshToken}>
        <AppContent
          page={page}
          isRefreshing={isRefreshing}
          settingsEditor={settingsEditor}
          feedScrollOffset={feedScrollOffset}
          signalsScrollOffset={signalsScrollOffset}
          signalsHorizontalOffset={signalsHorizontalOffset}
          perfCurrentScrollOffset={perfCurrentScrollOffset}
          perfPastScrollOffset={perfPastScrollOffset}
          perfActivePane={perfActivePane}
          perfSelectedBox={perfSelectedBox}
          perfDailyDetailOpen={perfDailyDetailOpen}
          perfDailyDetailScrollOffset={perfDailyDetailScrollOffset}
          modelSelectionIndex={modelSelectionIndex}
          modelDetailOpen={modelDetailOpen}
          modelSettingSelectionIndex={modelSettingSelectionIndex}
          walletSelectionIndex={walletSelectionIndex}
          walletDetailOpen={walletDetailOpen}
          onWalletCountChange={setWalletCount}
        />
      </ManualRefreshProvider>
    </TerminalSizeProvider>
  )
}

function clearTerminal() {
  if (!process.stdout.isTTY) {
    return
  }

  // Clear the visible screen, clear scrollback, and move the cursor home
  // before Ink draws the dashboard.
  process.stdout.write('\x1b[2J\x1b[3J\x1b[H')
}

clearTerminal()

declare global {
  var __KELLY_WATCHER_TERMINAL_BG__: string | undefined
}

async function bootstrap() {
  globalThis.__KELLY_WATCHER_TERMINAL_BG__ = await detectTerminalBackgroundColor()
  render(<App />)
}

void bootstrap()
