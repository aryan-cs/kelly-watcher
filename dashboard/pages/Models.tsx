import React, {useMemo} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {StatRow} from '../components/StatRow.js'
import {formatDollar, formatNumber, formatPct, formatShortDateTime, secondsAgo} from '../format.js'
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

const MODEL_SQL = `
SELECT trained_at, n_samples, brier_score, log_loss, feature_cols, deployed
FROM model_history
ORDER BY trained_at DESC
LIMIT 6
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

  const latest = models[0]
  const tracker = trackerRows[0]
  const calibration = calibrationSummaryRows[0]
  const flow = flowRows[0]
  const trackerSnapshot = perfRows.find((row) => row.mode === 'shadow') ?? perfRows[0]

  const featureCount = useMemo(() => parseFeatureCount(latest?.feature_cols), [latest?.feature_cols])
  const useRate = ratio(tracker?.taken, tracker?.signals)
  const trackerWinRate = ratio(tracker?.wins, tracker?.resolved)
  const calibrationLimit = terminal.compact ? 3 : terminal.height < 42 ? 4 : 5
  const historyLimit = terminal.compact ? 3 : 4

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
            calibrationRows.slice(0, calibrationLimit).map((row) => (
              <Text key={row.bucket}>
                <Text color={theme.white}>{bucketLabel(row.bucket)}</Text>
                <Text color={theme.dim}>: pred </Text>
                <Text color={row.avg_confidence != null ? probabilityColor(row.avg_confidence) : theme.dim}>
                  {formatPct(row.avg_confidence, 1)}
                </Text>
                <Text color={theme.dim}> act </Text>
                <Text color={row.actual_win_rate != null ? probabilityColor(row.actual_win_rate) : theme.dim}>
                  {formatPct(row.actual_win_rate, 1)}
                </Text>
                <Text color={theme.dim}> gap </Text>
                <Text color={lowerIsBetterColor(row.avg_gap, 0.12, 0.2)}>
                  {formatPct(row.avg_gap, 1)}
                </Text>
                <Text color={theme.dim}> n={row.n}</Text>
              </Text>
            ))
          ) : (
            <Text color={theme.dim}>Need a few resolved tracker bets to grade calibration.</Text>
          )}
        </Box>

        {!stacked ? <InkBox width={1} /> : <InkBox height={1} />}

        <Box title="Signal Modes" width={stacked ? '100%' : '50%'}>
          {signalModes.length ? (
            signalModes.map((row) => {
              const modeWinRate = ratio(row.wins, row.resolved)
              const modeUseRate = ratio(row.taken, row.signals)
              return (
                <Text key={row.mode}>
                  <Text color={theme.white}>{modeLabel(row.mode)}</Text>
                  <Text color={theme.dim}>: use </Text>
                  <Text color={modeUseRate != null ? probabilityColor(Math.max(0.5, modeUseRate)) : theme.dim}>
                    {formatPct(modeUseRate, 1)}
                  </Text>
                  <Text color={theme.dim}> win </Text>
                  <Text color={modeWinRate != null ? probabilityColor(modeWinRate) : theme.dim}>
                    {formatPct(modeWinRate, 1)}
                  </Text>
                  <Text color={theme.dim}> edge </Text>
                  <Text color={signedMetricColor(row.avg_edge)}>{formatPct(row.avg_edge, 1)}</Text>
                  <Text color={theme.dim}> pnl </Text>
                  <Text color={dollarColor(row.total_pnl)}>{formatDollar(row.total_pnl)}</Text>
                </Text>
              )
            })
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

        <Box title="Retrain History" width={stacked ? '100%' : '50%'}>
          {models.length ? (
            models.slice(0, historyLimit).map((row) => (
              <Text key={row.trained_at}>
                <Text color={theme.white}>{formatShortDateTime(row.trained_at)}</Text>
                <Text color={theme.dim}>  n=</Text>
                <Text>{formatCount(row.n_samples)}</Text>
                <Text color={theme.dim}>  Brier </Text>
                <Text color={lowerIsBetterColor(row.brier_score, 0.18, 0.25)}>{formatNumber(row.brier_score, 4)}</Text>
                <Text color={theme.dim}>  LL </Text>
                <Text color={lowerIsBetterColor(row.log_loss, 0.55, 0.69)}>{formatNumber(row.log_loss, 4)}</Text>
                <Text color={row.deployed ? theme.green : theme.dim}>
                  {row.deployed ? '  active' : '  archived'}
                </Text>
              </Text>
            ))
          ) : (
            <Text color={theme.dim}>No trained models yet. The heuristic path is active.</Text>
          )}
        </Box>
      </InkBox>
    </InkBox>
  )
}
