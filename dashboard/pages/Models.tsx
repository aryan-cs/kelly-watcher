import React, {useMemo} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {StatRow} from '../components/StatRow.js'
import {fit, fitRight, formatDollar, formatNumber, formatPct, formatShortDateTime, secondsAgo} from '../format.js'
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

const MODEL_SQL = `
SELECT trained_at, n_samples, brier_score, log_loss, feature_cols, deployed
FROM model_history
ORDER BY trained_at DESC
LIMIT 12
`

const TRACKER_SQL = `
SELECT
  COUNT(*) AS signals,
  SUM(CASE WHEN skipped=0 THEN 1 ELSE 0 END) AS taken,
  SUM(CASE WHEN skipped=0 AND outcome IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
  SUM(CASE WHEN skipped=0 AND outcome=1 THEN 1 ELSE 0 END) AS wins,
  AVG(CASE WHEN skipped=0 THEN confidence END) AS avg_confidence,
  AVG(CASE WHEN skipped=0 THEN confidence - price_at_signal END) AS avg_edge,
  SUM(CASE WHEN skipped=0 THEN COALESCE(shadow_pnl_usd, actual_pnl_usd) ELSE 0 END) AS total_pnl
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
  SUM(CASE WHEN skipped=0 THEN 1 ELSE 0 END) AS taken,
  SUM(CASE WHEN skipped=0 AND outcome IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
  SUM(CASE WHEN skipped=0 AND outcome=1 THEN 1 ELSE 0 END) AS wins,
  AVG(CASE WHEN skipped=0 THEN confidence END) AS avg_confidence,
  AVG(CASE WHEN skipped=0 THEN confidence - price_at_signal END) AS avg_edge,
  SUM(CASE WHEN skipped=0 THEN COALESCE(shadow_pnl_usd, actual_pnl_usd) ELSE 0 END) AS total_pnl
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
  AVG(CAST(outcome AS REAL)) AS actual_win_rate,
  AVG(ABS(confidence - CAST(outcome AS REAL))) AS avg_gap
FROM trade_log
WHERE COALESCE(source_action, 'buy')='buy'
  AND real_money=0
  AND skipped=0
  AND outcome IS NOT NULL
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
    CAST(outcome AS REAL) AS outcome
  FROM trade_log
  WHERE COALESCE(source_action, 'buy')='buy'
    AND real_money=0
    AND skipped=0
    AND outcome IS NOT NULL
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
  AVG(CASE WHEN skipped=0 THEN trader_score END) AS trader_score,
  AVG(CASE WHEN skipped=0 THEN market_score END) AS market_score,
  AVG(CASE WHEN skipped=0 THEN belief_prior END) AS belief_prior,
  AVG(CASE WHEN skipped=0 THEN belief_blend END) AS belief_blend,
  AVG(CASE WHEN skipped=0 THEN belief_evidence END) AS belief_evidence
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

function modeLabel(mode: string): string {
  const normalized = mode.trim().toLowerCase()
  if (normalized === 'model') return 'XGBoost'
  if (normalized === 'heuristic') return 'Heuristic score'
  if (normalized === 'shadow') return 'Tracker'
  if (normalized === 'live') return 'Live'
  return mode || 'Unknown'
}

export function Models() {
  const terminal = useTerminalSize()
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
    const timeWidth = 12
    const sampleWidth = 8
    const brierWidth = 7
    const lossWidth = 7
    const gapCount = 4
    return {
      timeWidth,
      sampleWidth,
      brierWidth,
      lossWidth,
      stateWidth: Math.max(8, panelContentWidth - timeWidth - sampleWidth - brierWidth - lossWidth - gapCount)
    }
  }, [panelContentWidth])

  return (
    <InkBox flexDirection="column" width="100%">
      <InkBox flexDirection={stacked ? 'column' : 'row'}>
        <Box title="Prediction Quality" width={stacked ? '100%' : '50%'}>
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

        <Box title="Tracker Health" width={stacked ? '100%' : '50%'}>
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
        <Box title="Confidence Check" width={stacked ? '100%' : '50%'}>
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

        <Box title="Signal Modes" width={stacked ? '100%' : '50%'}>
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
        <Box title="How It Works" width={stacked ? '100%' : '50%'}>
          <StatRow
            label="Trader score"
            value={formatPct(flow?.trader_score, 1)}
            color={flow?.trader_score != null ? probabilityColor(flow.trader_score) : theme.dim}
          />
          <StatRow
            label="Market score"
            value={formatPct(flow?.market_score, 1)}
            color={flow?.market_score != null ? probabilityColor(flow.market_score) : theme.dim}
          />
          <StatRow
            label="Prior win"
            value={formatPct(flow?.belief_prior, 1)}
            color={flow?.belief_prior != null ? probabilityColor(flow.belief_prior) : theme.dim}
          />
          <StatRow
            label="Prior blend"
            value={formatPct(flow?.belief_blend, 1)}
            color={flow?.belief_blend != null ? probabilityColor(flow.belief_blend) : theme.dim}
          />
          <StatRow label="Avg evidence" value={formatNumber(flow?.belief_evidence, 0)} />
          <Text color={theme.dim}>Trader history drives most of the base score.</Text>
          <Text color={theme.dim}>Market context adds spread, depth, time, and momentum.</Text>
          <Text color={theme.dim}>Belief priors nudge the score using similar past trades.</Text>
          <Text color={theme.dim}>Signals still need edge, and bad books get vetoed.</Text>
        </Box>

        {!stacked ? <InkBox width={1} /> : <InkBox height={1} />}

        <Box title="Training Cycle" width={stacked ? '100%' : '50%'}>
          <StatRow label="Update style" value="Full retrain" />
          <StatRow label="Base cadence" value="Weekly" />
          <StatRow label="Weekly slot" value="Mon 3:00 local" />
          <StatRow label="Early check" value="Every 24h" />
          <StatRow label="Early trigger" value="100 new labels" />
          <StatRow label="Total runs" value={formatCount(trainingSummary?.total_runs)} />
          <StatRow label="Last gap" value={formatInterval(lastRetrainGap)} />
          <StatRow label="Avg gap" value={formatInterval(averageRetrainGap)} />
          <StatRow label="Runs 7d" value={formatCount(trainingSummary?.runs_7d)} />
          <StatRow label="Runs 30d" value={formatCount(trainingSummary?.runs_30d)} />
          <Text color={theme.dim}>No online fine-tuning. Each update rebuilds the model from resolved trades.</Text>
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
            <Text color={theme.dim}>{fit('STATE', retrainWidths.stateWidth)}</Text>
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
                  {fit(row.deployed ? 'active' : 'archived', retrainWidths.stateWidth)}
                </Text>
              </InkBox>
            ))
          ) : (
            <Text color={theme.dim}>No trained models yet. The heuristic path is active.</Text>
          )}
        </Box>
      </InkBox>
    </InkBox>
  )
}
