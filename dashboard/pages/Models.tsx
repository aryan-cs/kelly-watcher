import React, {useEffect, useMemo, useState} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {StatRow} from '../components/StatRow.js'
import {editableConfigFields, formatEditableConfigValue, type EditableConfigValues} from '../configEditor.js'
import {fit, fitRight, formatDollar, formatNumber, formatPct, formatShortDateTime, secondsAgo, timeUntil} from '../format.js'
import {stackPanels} from '../responsive.js'
import {useTerminalSize} from '../terminal.js'
import {positiveDollarColor, probabilityColor, theme} from '../theme.js'
import {useQuery} from '../useDb.js'

interface ModelRow {
  trained_at: number
  n_samples: number
  brier_score: number
  log_loss: number
  feature_cols: string
  deployed: number
}

interface TrackerRow {
  signals: number | null
  taken: number | null
  resolved: number | null
  wins: number | null
  avg_confidence: number | null
  avg_edge: number | null
  total_pnl: number | null
}

interface PerfRow {
  snapshot_at: number
  mode: string
  n_signals: number
  n_acted: number
  n_resolved: number
  win_rate: number | null
  total_pnl_usd: number | null
  avg_confidence: number | null
  sharpe: number | null
}

interface SignalModeRow {
  mode: string
  signals: number
  taken: number
  resolved: number
  wins: number
  avg_confidence: number | null
  avg_edge: number | null
  total_pnl: number | null
}

interface CalibrationSummaryRow {
  resolved: number | null
  avg_confidence: number | null
  actual_win_rate: number | null
  avg_gap: number | null
}

interface CalibrationRow {
  bucket: number
  n: number
  avg_confidence: number | null
  actual_win_rate: number | null
  avg_gap: number | null
}

interface FlowRow {
  trader_score: number | null
  market_score: number | null
  belief_prior: number | null
  belief_blend: number | null
  belief_evidence: number | null
}

interface TrainingSummaryRow {
  total_runs: number | null
  runs_7d: number | null
  runs_30d: number | null
}

export type ModelPanelId =
  | 'prediction_quality'
  | 'tracker_health'
  | 'confidence_check'
  | 'signal_modes'
  | 'how_it_works'
  | 'training_cycle'

interface ModelPanelHelpRow {
  label: string
  text: string
}

interface ModelPanelDefinition {
  id: ModelPanelId
  title: string
  summary: string[]
  rows: ModelPanelHelpRow[]
  settingKeys: string[]
}

function isDefined<T>(value: T | undefined): value is T {
  return value !== undefined
}

export const MODEL_PANEL_DEFS: ModelPanelDefinition[] = [
  {
    id: 'prediction_quality',
    title: 'Prediction Quality',
    summary: [
      'This box grades the currently active scorer itself, not the bankroll curve.',
      'Lower loss numbers mean the probabilities are closer to what actually happened.'
    ],
    rows: [
      {label: 'Active path', text: 'Which scorer is currently making decisions: XGBoost or the heuristic score.'},
      {label: 'Trained', text: 'When the latest deployed model was built.'},
      {label: 'Model age', text: 'How long the active deployed model has been running without a retrain.'},
      {label: 'Samples', text: 'How many resolved trades were available to train on.'},
      {label: 'Features', text: 'How many inputs the deployed model is using.'},
      {label: 'Brier score', text: 'Average probability error. Lower is better.'},
      {label: 'Log loss', text: 'Penalizes confident wrong calls much harder. Lower is better.'}
    ],
    settingKeys: []
  },
  {
    id: 'tracker_health',
    title: 'Tracker Health',
    summary: [
      'This box measures what happened after the bot actually accepted signals.',
      'It is the best quick read on how strict filters, edge, and sizing are working together.'
    ],
    rows: [
      {label: 'Signals logged', text: 'Candidate trades the bot observed before filtering.'},
      {label: 'Bets taken', text: 'Signals that passed checks and became tracker bets.'},
      {label: 'Use rate', text: 'Accepted bets divided by total signals. Lower means stricter filtering.'},
      {label: 'Win rate', text: 'Resolved tracker bets that finished as wins.'},
      {label: 'Avg confidence', text: 'Average predicted win probability on accepted bets.'},
      {label: 'Avg edge', text: 'Confidence minus price. Positive means the bot saw value.'},
      {label: 'Tracker P&L', text: 'Cumulative paper profit from accepted bets.'},
      {label: 'Sharpe ratio', text: 'Return compared to volatility. Higher is smoother.'}
    ],
    settingKeys: ['MIN_CONFIDENCE', 'MAX_BET_FRACTION', 'SHADOW_BANKROLL_USD']
  },
  {
    id: 'confidence_check',
    title: 'Confidence Check',
    summary: [
      'This box asks whether the probabilities are calibrated.',
      'If the bot says 70%, the actual win rate should land close to 70% over time.'
    ],
    rows: [
      {label: 'Resolved bets', text: 'Accepted tracker bets that already settled and can be graded.'},
      {label: 'Avg confidence', text: 'Mean predicted probability across resolved accepted bets.'},
      {label: 'Actual win', text: 'Observed win rate for those same bets.'},
      {label: 'Calib gap', text: 'Average absolute distance between predicted odds and actual outcomes. Lower is better.'},
      {label: 'Range', text: 'Confidence bucket for grouped calibration checks.'},
      {label: 'Pred / Act / Gap', text: 'Predicted win rate, actual win rate, and the difference for that bucket.'}
    ],
    settingKeys: ['MIN_CONFIDENCE']
  },
  {
    id: 'signal_modes',
    title: 'Signal Modes',
    summary: [
      'This box compares how each decision path is behaving.',
      'It helps you see whether XGBoost is actually earning its keep versus the heuristic fallback.'
    ],
    rows: [
      {label: 'Mode', text: 'The decision path used for that signal group.'},
      {label: 'Use', text: 'Acceptance rate for that path.'},
      {label: 'Win', text: 'Resolved win rate for accepted bets from that path.'},
      {label: 'Edge', text: 'Average confidence edge over price for that path.'},
      {label: 'P&L', text: 'Cumulative tracker profit from that path.'}
    ],
    settingKeys: ['MIN_CONFIDENCE', 'MAX_MARKET_HORIZON']
  },
  {
    id: 'how_it_works',
    title: 'How It Works',
    summary: [
      'This box shows historical averages of the heuristic inputs, not parts that should add to 100%.',
      'For one signal, base = trader^0.60 * market^0.40.',
      'Then final = (1 - blend) * base + blend * prior.'
    ],
    rows: [
      {label: 'Avg trader qual', text: 'Average trader quality score from history and behavior.'},
      {label: 'Avg market qual', text: 'Average market quality score from spread, depth, time, and momentum.'},
      {label: 'Avg prior win', text: 'Average resolved-history prior for similar trades.'},
      {label: 'Avg prior blend', text: 'Average fraction of the prior allowed to move the base score.'},
      {label: 'Avg evidence', text: 'Average number of prior examples supporting the adjustment.'},
      {label: 'Base formula', text: 'trader^0.60 * market^0.40 weighted geometric mean.'},
      {label: 'Final formula', text: '(1 - blend) * base + blend * prior.'}
    ],
    settingKeys: ['MIN_CONFIDENCE', 'MAX_MARKET_HORIZON', 'MAX_BET_FRACTION']
  },
  {
    id: 'training_cycle',
    title: 'Training Cycle',
    summary: [
      'This box covers how often the model gets rebuilt and what has to happen before a new one goes live.',
      'These are full retrains on resolved trades, not online fine-tunes.'
    ],
    rows: [
      {label: 'Update style', text: 'The system rebuilds the model from resolved trades each cycle.'},
      {label: 'Base cadence', text: 'Regular scheduled retrain frequency.'},
      {label: 'Run time', text: 'Local hour when the scheduled retrain is attempted.'},
      {label: 'Next scheduled', text: 'Next planned scheduled retrain window from the current cadence and local hour.'},
      {label: 'Scheduled in', text: 'Countdown until that scheduled retrain window.'},
      {label: 'Early check', text: 'How often the bot checks whether it should retrain sooner.'},
      {label: 'Early trigger', text: 'Minimum new labels needed to fire an unscheduled retrain.'},
      {label: 'Last / Avg gap', text: 'Observed time between recent successful training runs.'},
      {label: 'Runs 7d / 30d', text: 'How many training runs landed recently.'}
    ],
    settingKeys: ['RETRAIN_BASE_CADENCE', 'RETRAIN_HOUR_LOCAL', 'RETRAIN_EARLY_CHECK_INTERVAL', 'RETRAIN_MIN_NEW_LABELS']
  }
]

interface ModelsProps {
  selectedPanelIndex: number
  detailOpen: boolean
  selectedSettingIndex: number
  settingsValues: EditableConfigValues
}

const EXECUTED_ENTRY_WHERE = `
skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
`

const RESOLVED_EXECUTED_ENTRY_WHERE = `
${EXECUTED_ENTRY_WHERE}
AND COALESCE(actual_pnl_usd, shadow_pnl_usd) IS NOT NULL
`

const PROFITABLE_TRADE_SQL = `CASE WHEN COALESCE(actual_pnl_usd, shadow_pnl_usd) > 0 THEN 1 ELSE 0 END`

const MODEL_SQL = `
SELECT trained_at, n_samples, brier_score, log_loss, feature_cols, deployed
FROM model_history
ORDER BY trained_at DESC
LIMIT 12
`

const TRACKER_SQL = `
SELECT
  COUNT(*) AS signals,
  SUM(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN 1 ELSE 0 END) AS taken,
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} THEN 1 ELSE 0 END) AS resolved,
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} AND shadow_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
  AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN confidence END) AS avg_confidence,
  AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN confidence - actual_entry_price END) AS avg_edge,
  SUM(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN COALESCE(shadow_pnl_usd, 0) ELSE 0 END) AS total_pnl
FROM trade_log
WHERE COALESCE(source_action, 'buy')='buy'
  AND real_money=0
`

const PERF_SQL = `
SELECT snapshot_at, mode, n_signals, n_acted, n_resolved, win_rate, total_pnl_usd, avg_confidence, sharpe
FROM perf_snapshots
WHERE id IN (
  SELECT MAX(id)
  FROM perf_snapshots
  GROUP BY mode
)
ORDER BY CASE WHEN mode='shadow' THEN 0 ELSE 1 END, snapshot_at DESC
`

const SIGNAL_MODE_SQL = `
SELECT
  COALESCE(NULLIF(signal_mode, ''), 'heuristic') AS mode,
  COUNT(*) AS signals,
  SUM(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN 1 ELSE 0 END) AS taken,
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} THEN 1 ELSE 0 END) AS resolved,
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} AND shadow_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
  AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN confidence END) AS avg_confidence,
  AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN confidence - actual_entry_price END) AS avg_edge,
  SUM(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN COALESCE(shadow_pnl_usd, 0) ELSE 0 END) AS total_pnl
FROM trade_log
WHERE COALESCE(source_action, 'buy')='buy'
  AND real_money=0
GROUP BY COALESCE(NULLIF(signal_mode, ''), 'heuristic')
ORDER BY taken DESC, signals DESC
`

const CALIBRATION_SUMMARY_SQL = `
SELECT
  COUNT(*) AS resolved,
  AVG(confidence) AS avg_confidence,
  AVG(CAST(${PROFITABLE_TRADE_SQL} AS REAL)) AS actual_win_rate,
  AVG(ABS(confidence - CAST(${PROFITABLE_TRADE_SQL} AS REAL))) AS avg_gap
FROM trade_log
WHERE real_money=0
  AND ${RESOLVED_EXECUTED_ENTRY_WHERE}
  AND confidence IS NOT NULL
`

const CALIBRATION_SQL = `
WITH bucketed AS (
  SELECT
    CASE
      WHEN confidence >= 0.9 THEN 9
      WHEN confidence < 0.5 THEN 4
      ELSE CAST(confidence * 10 AS INTEGER)
    END AS bucket,
    confidence,
    CAST(${PROFITABLE_TRADE_SQL} AS REAL) AS outcome
  FROM trade_log
  WHERE real_money=0
    AND ${RESOLVED_EXECUTED_ENTRY_WHERE}
    AND confidence IS NOT NULL
)
SELECT
  bucket,
  COUNT(*) AS n,
  AVG(confidence) AS avg_confidence,
  AVG(outcome) AS actual_win_rate,
  AVG(ABS(confidence - outcome)) AS avg_gap
FROM bucketed
GROUP BY bucket
HAVING COUNT(*) >= 3
ORDER BY bucket ASC
`

const FLOW_SQL = `
SELECT
  AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN trader_score END) AS trader_score,
  AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN market_score END) AS market_score,
  AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN belief_prior END) AS belief_prior,
  AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN belief_blend END) AS belief_blend,
  AVG(CASE WHEN ${EXECUTED_ENTRY_WHERE} THEN belief_evidence END) AS belief_evidence
FROM trade_log
WHERE COALESCE(source_action, 'buy')='buy'
  AND real_money=0
`

const TRAINING_SUMMARY_SQL = `
SELECT
  COUNT(*) AS total_runs,
  SUM(CASE WHEN trained_at >= strftime('%s', 'now', '-7 days') THEN 1 ELSE 0 END) AS runs_7d,
  SUM(CASE WHEN trained_at >= strftime('%s', 'now', '-30 days') THEN 1 ELSE 0 END) AS runs_30d
FROM model_history
`

function formatCount(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '0'
  return Math.round(value).toLocaleString()
}

function ratio(numerator: number | null | undefined, denominator: number | null | undefined): number | null {
  if (numerator == null || denominator == null || denominator <= 0) return null
  return numerator / denominator
}

function parseFeatureCount(raw: string | null | undefined): number | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.length : null
  } catch {
    return null
  }
}

function lowerIsBetterColor(value: number | null | undefined, good: number, okay: number): string {
  if (value == null || Number.isNaN(value)) return theme.dim
  if (value <= good) return theme.green
  if (value <= okay) return theme.yellow
  return theme.red
}

function sharpeColor(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return theme.dim
  if (value >= 1) return theme.green
  if (value >= 0) return theme.yellow
  return theme.red
}

function signedMetricColor(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return theme.dim
  if (value > 0) return theme.green
  if (value < 0) return theme.red
  return theme.white
}

function dollarColor(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return theme.dim
  if (value < 0) return theme.red
  return positiveDollarColor(value)
}

function bucketLabel(bucket: number): string {
  const lower = bucket * 10
  const upper = bucket === 9 ? 100 : bucket * 10 + 9
  return `${lower}-${upper}%`
}

function formatInterval(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds) || seconds <= 0) return '-'
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`
  }
  if (seconds < 86400 * 14) {
    const days = Math.floor(seconds / 86400)
    const hours = Math.floor((seconds % 86400) / 3600)
    return hours > 0 ? `${days}d ${hours}h` : `${days}d`
  }
  const weeks = seconds / (86400 * 7)
  return `${weeks.toFixed(1).replace(/\.0$/, '')}w`
}

function normalizeCadence(value: string | null | undefined): 'daily' | 'weekly' {
  return value?.trim().toLowerCase() === 'weekly' ? 'weekly' : 'daily'
}

function clampHour(value: string | null | undefined): number {
  const parsed = Number.parseInt(value || '', 10)
  if (!Number.isFinite(parsed)) return 3
  return Math.min(Math.max(parsed, 0), 23)
}

function getNextScheduledRetrainTs(cadence: 'daily' | 'weekly', hour: number, nowTs: number): number {
  const now = new Date(nowTs * 1000)
  const next = new Date(nowTs * 1000)

  next.setHours(hour, 0, 0, 0)

  if (cadence === 'weekly') {
    const daysUntilMonday = (1 - now.getDay() + 7) % 7
    next.setDate(now.getDate() + daysUntilMonday)
    if (next.getTime() / 1000 <= nowTs) {
      next.setDate(next.getDate() + 7)
    }
    return Math.floor(next.getTime() / 1000)
  }

  if (next.getTime() / 1000 <= nowTs) {
    next.setDate(next.getDate() + 1)
  }

  return Math.floor(next.getTime() / 1000)
}

function useNow(intervalMs = 30000): number {
  const [nowTs, setNowTs] = useState(() => Math.floor(Date.now() / 1000))

  useEffect(() => {
    const id = setInterval(() => setNowTs(Math.floor(Date.now() / 1000)), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])

  return nowTs
}

function modeLabel(mode: string): string {
  const normalized = mode.trim().toLowerCase()
  if (normalized === 'model') return 'XGBoost'
  if (normalized === 'heuristic') return 'Heuristic score'
  if (normalized === 'shadow') return 'Tracker'
  if (normalized === 'live') return 'Live'
  return mode || 'Unknown'
}

export function Models({selectedPanelIndex, detailOpen, selectedSettingIndex, settingsValues}: ModelsProps) {
  const terminal = useTerminalSize()
  const modalBackground = terminal.backgroundColor || theme.modalBackground
  const nowTs = useNow()
  const stacked = stackPanels(terminal.width)
  const models = useQuery<ModelRow>(MODEL_SQL)
  const trackerRows = useQuery<TrackerRow>(TRACKER_SQL)
  const perfRows = useQuery<PerfRow>(PERF_SQL)
  const signalModes = useQuery<SignalModeRow>(SIGNAL_MODE_SQL)
  const calibrationSummaryRows = useQuery<CalibrationSummaryRow>(CALIBRATION_SUMMARY_SQL)
  const calibrationRows = useQuery<CalibrationRow>(CALIBRATION_SQL)
  const flowRows = useQuery<FlowRow>(FLOW_SQL)
  const trainingSummaryRows = useQuery<TrainingSummaryRow>(TRAINING_SUMMARY_SQL)

  const latest = models[0]
  const tracker = trackerRows[0]
  const calibration = calibrationSummaryRows[0]
  const flow = flowRows[0]
  const trainingSummary = trainingSummaryRows[0]
  const trackerSnapshot = perfRows.find((row) => row.mode === 'shadow') ?? perfRows[0]
  const configFieldByKey = useMemo(
    () => new Map(editableConfigFields.map((field) => [field.key, field])),
    []
  )

  const featureCount = useMemo(() => parseFeatureCount(latest?.feature_cols), [latest?.feature_cols])
  const useRate = ratio(tracker?.taken, tracker?.signals)
  const trackerWinRate = ratio(tracker?.wins, tracker?.resolved)
  const retrainGaps = useMemo(
    () =>
      models
        .slice(0, 12)
        .map((row, index, rows) => {
          const next = rows[index + 1]
          return next ? row.trained_at - next.trained_at : null
        })
        .filter((gap): gap is number => gap != null && gap > 0),
    [models]
  )
  const lastRetrainGap = retrainGaps[0] ?? null
  const averageRetrainGap =
    retrainGaps.length > 0
      ? retrainGaps.reduce((sum, gap) => sum + gap, 0) / retrainGaps.length
      : null
  const calibrationLimit = terminal.compact ? 3 : terminal.height < 42 ? 4 : 5
  const historyLimit = terminal.compact ? 3 : 4
  const panelContentWidth = stacked
    ? Math.max(46, terminal.width - 12)
    : Math.max(34, Math.floor((terminal.width - 18) / 2))
  const calibrationWidths = useMemo(() => {
    const rangeWidth = 8
    const nWidth = 5
    const gapCount = 4
    const metricWidth = Math.max(7, Math.floor((panelContentWidth - rangeWidth - nWidth - gapCount) / 3))
    const used = rangeWidth + nWidth + gapCount + metricWidth * 3
    return {
      rangeWidth: rangeWidth + Math.max(0, panelContentWidth - used),
      metricWidth,
      nWidth
    }
  }, [panelContentWidth])
  const signalModeWidths = useMemo(() => {
    const useWidth = 7
    const winWidth = 7
    const edgeWidth = 7
    const pnlWidth = Math.max(12, Math.min(14, Math.floor(panelContentWidth * 0.22)))
    const gapCount = 4
    return {
      modeWidth: Math.max(10, panelContentWidth - useWidth - winWidth - edgeWidth - pnlWidth - gapCount),
      useWidth,
      winWidth,
      edgeWidth,
      pnlWidth
    }
  }, [panelContentWidth])
  const retrainWidths = useMemo(() => {
    const sampleWidth = 8
    const brierWidth = 7
    const lossWidth = 7
    const gapCount = 4
    const minTimeWidth = 13
    let stateWidth = Math.max(8, Math.min(12, Math.floor(panelContentWidth * 0.2)))
    let timeWidth = panelContentWidth - gapCount - sampleWidth - brierWidth - lossWidth - stateWidth

    if (timeWidth < minTimeWidth) {
      const reclaimed = Math.min(minTimeWidth - timeWidth, Math.max(0, stateWidth - 8))
      stateWidth -= reclaimed
      timeWidth += reclaimed
    }

    if (timeWidth < minTimeWidth) {
      timeWidth = minTimeWidth
      stateWidth = Math.max(8, panelContentWidth - gapCount - sampleWidth - brierWidth - lossWidth - timeWidth)
    }

    return {
      timeWidth,
      sampleWidth,
      brierWidth,
      lossWidth,
      stateWidth
    }
  }, [panelContentWidth])
  const clampedSelectedPanelIndex = Math.max(0, Math.min(selectedPanelIndex, MODEL_PANEL_DEFS.length - 1))
  const selectedPanel = MODEL_PANEL_DEFS[clampedSelectedPanelIndex]
  const relatedSettings = useMemo(
    () => selectedPanel.settingKeys.map((key) => configFieldByKey.get(key)).filter(isDefined),
    [configFieldByKey, selectedPanel]
  )
  const clampedSelectedSettingIndex =
    relatedSettings.length > 0
      ? Math.max(0, Math.min(selectedSettingIndex, relatedSettings.length - 1))
      : 0
  const helpModalWidth = Math.max(70, Math.min(terminal.width - 8, terminal.wide ? 118 : 94))
  const helpContentWidth = Math.max(52, helpModalWidth - 4)
  const helpSettingLabelWidth = Math.max(18, Math.min(28, Math.floor(helpContentWidth * 0.48)))
  const helpSettingValueWidth = Math.max(14, helpContentWidth - helpSettingLabelWidth - 1)
  const helpIndexLabel = `${clampedSelectedPanelIndex + 1}/${MODEL_PANEL_DEFS.length}`
  const helpTitleWidth = Math.max(1, helpContentWidth - helpIndexLabel.length - 1)
  const helpSpacerLine = ' '.repeat(helpModalWidth - 2)
  const formatConfigValue = (key: string): string => {
    const field = configFieldByKey.get(key)
    if (!field) return '-'
    return formatEditableConfigValue(field, settingsValues[key] || field.defaultValue)
  }
  const rawConfigValue = (key: string): string => settingsValues[key] || configFieldByKey.get(key)?.defaultValue || ''
  const baseCadenceRaw = rawConfigValue('RETRAIN_BASE_CADENCE')
  const retrainHourRaw = rawConfigValue('RETRAIN_HOUR_LOCAL')
  const baseCadenceValue = formatConfigValue('RETRAIN_BASE_CADENCE')
  const retrainHourValue = formatConfigValue('RETRAIN_HOUR_LOCAL')
  const earlyCheckValue = formatConfigValue('RETRAIN_EARLY_CHECK_INTERVAL')
  const earlyTriggerValue = formatConfigValue('RETRAIN_MIN_NEW_LABELS')
  const nextScheduledRetrainTs = useMemo(
    () => getNextScheduledRetrainTs(normalizeCadence(baseCadenceRaw), clampHour(retrainHourRaw), nowTs),
    [baseCadenceRaw, retrainHourRaw, nowTs]
  )

  return (
    <InkBox flexDirection="column" width="100%">
      <InkBox flexDirection={stacked ? 'column' : 'row'}>
        <Box title="Prediction Quality" width={stacked ? '100%' : '50%'} accent={clampedSelectedPanelIndex === 0}>
          <StatRow
            label="Active path"
            value={latest?.deployed ? 'XGBoost' : 'Heuristic score'}
            color={latest?.deployed ? theme.green : theme.yellow}
          />
          <StatRow label="Trained" value={latest ? formatShortDateTime(latest.trained_at) : '-'} />
          <StatRow label="Model age" value={latest ? secondsAgo(latest.trained_at) : '-'} />
          <StatRow label="Samples" value={formatCount(latest?.n_samples)} />
          <StatRow label="Features" value={featureCount != null ? formatCount(featureCount) : '-'} />
          <StatRow
            label="Brier score"
            value={formatNumber(latest?.brier_score, 4)}
            color={lowerIsBetterColor(latest?.brier_score, 0.18, 0.25)}
          />
          <StatRow
            label="Log loss"
            value={formatNumber(latest?.log_loss, 4)}
            color={lowerIsBetterColor(latest?.log_loss, 0.55, 0.69)}
          />
          <Text color={theme.dim}>Brier measures average probability error.</Text>
          <Text color={theme.dim}>Log loss hits confident mistakes harder.</Text>
        </Box>

        {!stacked ? <InkBox width={1} /> : <InkBox height={1} />}

        <Box title="Tracker Health" width={stacked ? '100%' : '50%'} accent={clampedSelectedPanelIndex === 1}>
          <StatRow label="Signals logged" value={formatCount(tracker?.signals)} />
          <StatRow label="Bets taken" value={formatCount(tracker?.taken)} />
          <StatRow
            label="Use rate"
            value={formatPct(useRate, 1)}
            color={useRate != null ? probabilityColor(Math.max(0.5, useRate)) : theme.dim}
          />
          <StatRow label="Resolved bets" value={formatCount(tracker?.resolved)} />
          <StatRow
            label="Win rate"
            value={formatPct(trackerWinRate, 1)}
            color={trackerWinRate != null ? probabilityColor(trackerWinRate) : theme.dim}
          />
          <StatRow
            label="Avg confidence"
            value={formatPct(tracker?.avg_confidence, 1)}
            color={tracker?.avg_confidence != null ? probabilityColor(tracker.avg_confidence) : theme.dim}
          />
          <StatRow
            label="Avg edge"
            value={formatPct(tracker?.avg_edge, 1)}
            color={signedMetricColor(tracker?.avg_edge)}
          />
          <StatRow
            label="Tracker P&L"
            value={formatDollar(tracker?.total_pnl)}
            color={dollarColor(tracker?.total_pnl)}
          />
          <StatRow
            label="Sharpe ratio"
            value={formatNumber(trackerSnapshot?.sharpe, 2)}
            color={sharpeColor(trackerSnapshot?.sharpe)}
          />
          <StatRow label="Snapshot age" value={trackerSnapshot ? secondsAgo(trackerSnapshot.snapshot_at) : '-'} />
          <Text color={theme.dim}>Sharpe compares return to volatility.</Text>
          <Text color={theme.dim}>Above 1 is solid; below 0 is rough.</Text>
        </Box>
      </InkBox>

      <InkBox marginTop={1} flexDirection={stacked ? 'column' : 'row'}>
        <Box title="Confidence Check" width={stacked ? '100%' : '50%'} accent={clampedSelectedPanelIndex === 2}>
          <StatRow label="Resolved bets" value={formatCount(calibration?.resolved)} />
          <StatRow
            label="Avg confidence"
            value={formatPct(calibration?.avg_confidence, 1)}
            color={calibration?.avg_confidence != null ? probabilityColor(calibration.avg_confidence) : theme.dim}
          />
          <StatRow
            label="Actual win"
            value={formatPct(calibration?.actual_win_rate, 1)}
            color={calibration?.actual_win_rate != null ? probabilityColor(calibration.actual_win_rate) : theme.dim}
          />
          <StatRow
            label="Calib gap"
            value={formatPct(calibration?.avg_gap, 1)}
            color={lowerIsBetterColor(calibration?.avg_gap, 0.12, 0.2)}
          />
          {calibrationRows.length ? (
            <>
              <InkBox width="100%" marginTop={1}>
                <Text color={theme.dim}>{fit('RANGE', calibrationWidths.rangeWidth)}</Text>
                <Text> </Text>
                <Text color={theme.dim}>{fitRight('PRED', calibrationWidths.metricWidth)}</Text>
                <Text> </Text>
                <Text color={theme.dim}>{fitRight('ACT', calibrationWidths.metricWidth)}</Text>
                <Text> </Text>
                <Text color={theme.dim}>{fitRight('GAP', calibrationWidths.metricWidth)}</Text>
                <Text> </Text>
                <Text color={theme.dim}>{fitRight('N', calibrationWidths.nWidth)}</Text>
              </InkBox>
              {calibrationRows.slice(0, calibrationLimit).map((row) => (
                <InkBox key={row.bucket} width="100%">
                  <Text color={theme.white}>{fit(bucketLabel(row.bucket), calibrationWidths.rangeWidth)}</Text>
                  <Text> </Text>
                  <Text color={row.avg_confidence != null ? probabilityColor(row.avg_confidence) : theme.dim}>
                    {fitRight(formatPct(row.avg_confidence, 1), calibrationWidths.metricWidth)}
                  </Text>
                  <Text> </Text>
                  <Text color={row.actual_win_rate != null ? probabilityColor(row.actual_win_rate) : theme.dim}>
                    {fitRight(formatPct(row.actual_win_rate, 1), calibrationWidths.metricWidth)}
                  </Text>
                  <Text> </Text>
                  <Text color={lowerIsBetterColor(row.avg_gap, 0.12, 0.2)}>
                    {fitRight(formatPct(row.avg_gap, 1), calibrationWidths.metricWidth)}
                  </Text>
                  <Text> </Text>
                  <Text color={theme.dim}>{fitRight(String(row.n), calibrationWidths.nWidth)}</Text>
                </InkBox>
              ))}
            </>
          ) : (
            <Text color={theme.dim}>Need a few resolved tracker bets to grade calibration.</Text>
          )}
        </Box>

        {!stacked ? <InkBox width={1} /> : <InkBox height={1} />}

        <Box title="Signal Modes" width={stacked ? '100%' : '50%'} accent={clampedSelectedPanelIndex === 3}>
          {signalModes.length ? (
            <>
              <InkBox width="100%">
                <Text color={theme.dim}>{fit('MODE', signalModeWidths.modeWidth)}</Text>
                <Text> </Text>
                <Text color={theme.dim}>{fitRight('USE', signalModeWidths.useWidth)}</Text>
                <Text> </Text>
                <Text color={theme.dim}>{fitRight('WIN', signalModeWidths.winWidth)}</Text>
                <Text> </Text>
                <Text color={theme.dim}>{fitRight('EDGE', signalModeWidths.edgeWidth)}</Text>
                <Text> </Text>
                <Text color={theme.dim}>{fitRight('P&L', signalModeWidths.pnlWidth)}</Text>
              </InkBox>
              {signalModes.map((row) => {
                const modeWinRate = ratio(row.wins, row.resolved)
                const modeUseRate = ratio(row.taken, row.signals)
                return (
                  <InkBox key={row.mode} width="100%">
                    <Text color={theme.white}>{fit(modeLabel(row.mode), signalModeWidths.modeWidth)}</Text>
                    <Text> </Text>
                    <Text color={modeUseRate != null ? probabilityColor(Math.max(0.5, modeUseRate)) : theme.dim}>
                      {fitRight(formatPct(modeUseRate, 1), signalModeWidths.useWidth)}
                    </Text>
                    <Text> </Text>
                    <Text color={modeWinRate != null ? probabilityColor(modeWinRate) : theme.dim}>
                      {fitRight(formatPct(modeWinRate, 1), signalModeWidths.winWidth)}
                    </Text>
                    <Text> </Text>
                    <Text color={signedMetricColor(row.avg_edge)}>
                      {fitRight(formatPct(row.avg_edge, 1), signalModeWidths.edgeWidth)}
                    </Text>
                    <Text> </Text>
                    <Text color={dollarColor(row.total_pnl)}>
                      {fitRight(formatDollar(row.total_pnl), signalModeWidths.pnlWidth)}
                    </Text>
                  </InkBox>
                )
              })}
            </>
          ) : (
            <Text color={theme.dim}>No tracker signals yet.</Text>
          )}
          <Text color={theme.dim}>Use rate = share of signals that became bets.</Text>
        </Box>
      </InkBox>

      <InkBox marginTop={1} flexDirection={stacked ? 'column' : 'row'}>
        <Box title="How It Works" width={stacked ? '100%' : '50%'} accent={clampedSelectedPanelIndex === 4}>
          <StatRow
            label="Avg trader qual"
            value={formatPct(flow?.trader_score, 1)}
            color={flow?.trader_score != null ? probabilityColor(flow.trader_score) : theme.dim}
          />
          <StatRow
            label="Avg market qual"
            value={formatPct(flow?.market_score, 1)}
            color={flow?.market_score != null ? probabilityColor(flow.market_score) : theme.dim}
          />
          <StatRow
            label="Avg prior win"
            value={formatPct(flow?.belief_prior, 1)}
            color={flow?.belief_prior != null ? probabilityColor(flow.belief_prior) : theme.dim}
          />
          <StatRow
            label="Avg prior blend"
            value={formatPct(flow?.belief_blend, 1)}
            color={flow?.belief_blend != null ? probabilityColor(flow.belief_blend) : theme.dim}
          />
          <StatRow label="Avg evidence" value={formatNumber(flow?.belief_evidence, 0)} />
          <Text color={theme.dim}>Shown values are averages, not pieces that add to 100%.</Text>
          <Text color={theme.dim}>Base = trader^0.60 * market^0.40</Text>
          <Text color={theme.dim}>Final = (1 - blend) * base + blend * prior</Text>
          <Text color={theme.dim}>Signals still need edge, and bad books get vetoed.</Text>
        </Box>

        {!stacked ? <InkBox width={1} /> : <InkBox height={1} />}

        <Box title="Training Cycle" width={stacked ? '100%' : '50%'} accent={clampedSelectedPanelIndex === 5}>
          <StatRow label="Update style" value="Full retrain" />
          <StatRow label="Base cadence" value={baseCadenceValue} />
          <StatRow label="Run time" value={retrainHourValue} />
          <StatRow label="Next scheduled" value={formatShortDateTime(nextScheduledRetrainTs)} />
          <StatRow label="Scheduled in" value={timeUntil(nextScheduledRetrainTs)} />
          <StatRow label="Early check" value={earlyCheckValue} />
          <StatRow label="Early trigger" value={earlyTriggerValue} />
          <StatRow label="Total runs" value={formatCount(trainingSummary?.total_runs)} />
          <StatRow label="Last gap" value={formatInterval(lastRetrainGap)} />
          <StatRow label="Avg gap" value={formatInterval(averageRetrainGap)} />
          <StatRow label="Runs 7d" value={formatCount(trainingSummary?.runs_7d)} />
          <StatRow label="Runs 30d" value={formatCount(trainingSummary?.runs_30d)} />
          <Text color={theme.dim}>No online fine-tuning. Each update rebuilds the model from resolved trades.</Text>
          <Text color={theme.dim}>Scheduled next uses the configured cadence and local hour; early retrains can still happen sooner.</Text>
          <Text color={theme.dim}>A retrain only deploys if it beats baseline checks and validation P&L gates.</Text>
          <InkBox width="100%" marginTop={1}>
            <Text color={theme.dim}>{fit('TIME', retrainWidths.timeWidth)}</Text>
            <Text> </Text>
            <Text color={theme.dim}>{fitRight('SAMPLES', retrainWidths.sampleWidth)}</Text>
            <Text> </Text>
            <Text color={theme.dim}>{fitRight('BRIER', retrainWidths.brierWidth)}</Text>
            <Text> </Text>
            <Text color={theme.dim}>{fitRight('LL', retrainWidths.lossWidth)}</Text>
            <Text> </Text>
            <Text color={theme.dim}>{fitRight('STATE', retrainWidths.stateWidth)}</Text>
          </InkBox>
          {models.length ? (
            models.slice(0, historyLimit).map((row) => (
              <InkBox key={row.trained_at} width="100%">
                <Text color={theme.white}>{fit(formatShortDateTime(row.trained_at), retrainWidths.timeWidth)}</Text>
                <Text> </Text>
                <Text color={theme.white}>{fitRight(formatCount(row.n_samples), retrainWidths.sampleWidth)}</Text>
                <Text> </Text>
                <Text color={lowerIsBetterColor(row.brier_score, 0.18, 0.25)}>
                  {fitRight(formatNumber(row.brier_score, 4), retrainWidths.brierWidth)}
                </Text>
                <Text> </Text>
                <Text color={lowerIsBetterColor(row.log_loss, 0.55, 0.69)}>
                  {fitRight(formatNumber(row.log_loss, 4), retrainWidths.lossWidth)}
                </Text>
                <Text> </Text>
                <Text color={row.deployed ? theme.green : theme.dim}>
                  {fitRight(row.deployed ? 'active' : 'archived', retrainWidths.stateWidth)}
                </Text>
              </InkBox>
            ))
          ) : (
            <Text color={theme.dim}>No trained models yet. The heuristic path is active.</Text>
          )}
        </Box>
      </InkBox>

      {detailOpen ? (
        <InkBox position="absolute" width="100%" height="100%" justifyContent="center" alignItems="center">
          <InkBox borderStyle="round" borderColor={theme.accent} flexDirection="column" width={helpModalWidth}>
            <InkBox width="100%">
              <Text color={theme.accent} backgroundColor={modalBackground} bold>
                {` ${fit(selectedPanel.title, helpTitleWidth)}`}
              </Text>
              <Text backgroundColor={modalBackground}> </Text>
              <Text color={theme.dim} backgroundColor={modalBackground}>
                {`${fitRight(helpIndexLabel, helpIndexLabel.length)} `}
              </Text>
            </InkBox>

            {selectedPanel.summary.map((line) => (
              <Text key={line} color={theme.dim} backgroundColor={modalBackground}>
                {` ${fit(line, helpContentWidth)} `}
              </Text>
            ))}

            <Text backgroundColor={modalBackground}>{helpSpacerLine}</Text>

            <InkBox flexDirection="column">
              <Text color={theme.accent} backgroundColor={modalBackground} bold>
                {` ${fit('Label Guide', helpContentWidth)} `}
              </Text>
              {selectedPanel.rows.map((row) => (
                <Text key={`${selectedPanel.id}-${row.label}`} color={theme.dim} backgroundColor={modalBackground}>
                  {` ${fit(`${row.label}: ${row.text}`, helpContentWidth)} `}
                </Text>
              ))}
            </InkBox>

            <Text backgroundColor={modalBackground}>{helpSpacerLine}</Text>

            <InkBox flexDirection="column">
              <Text color={theme.accent} backgroundColor={modalBackground} bold>
                {` ${fit('Related Settings', helpContentWidth)} `}
              </Text>
              {relatedSettings.length ? (
                <>
                  {relatedSettings.map((field, index) => {
                    const selected = index === clampedSelectedSettingIndex
                    const label = `${selected ? '> ' : '  '}${field.label}`
                    return (
                      <InkBox key={`${selectedPanel.id}-${field.key}`} width="100%">
                        <Text color={selected ? theme.accent : theme.dim} backgroundColor={modalBackground} bold={selected}>
                          {` ${fit(label, helpSettingLabelWidth)}`}
                        </Text>
                        <Text backgroundColor={modalBackground}> </Text>
                        <Text color={theme.white} backgroundColor={modalBackground} bold={selected}>
                          {`${fitRight(formatEditableConfigValue(field, settingsValues[field.key] || field.defaultValue), helpSettingValueWidth)} `}
                        </Text>
                      </InkBox>
                    )
                  })}
                  <Text color={theme.dim} backgroundColor={modalBackground}>
                    {` ${fit('Up/down selects a setting. Enter opens it in Config. Esc closes.', helpContentWidth)} `}
                  </Text>
                </>
              ) : (
                <Text color={theme.dim} backgroundColor={modalBackground}>
                  {` ${fit('No direct settings are tied to this box yet. Esc closes.', helpContentWidth)} `}
                </Text>
              )}
            </InkBox>
          </InkBox>
        </InkBox>
      ) : null}
    </InkBox>
  )
}
