import React, {useEffect, useMemo, useState} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {ModalOverlay} from '../components/ModalOverlay.js'
import {StatRow} from '../components/StatRow.js'
import {editableConfigFields, formatEditableConfigValue, type EditableConfigField, type EditableConfigValues} from '../configEditor.js'
import {fit, fitRight, formatDollar, formatNumber, formatPct, formatShortDateTime, secondsAgo, timeUntil} from '../format.js'
import {stackPanels} from '../responsive.js'
import {useTerminalSize} from '../terminal.js'
import {centeredGradientColor, negativeHeatColor, positiveDollarColor, probabilityColor, selectionBackgroundColor, theme} from '../theme.js'
import {useBotState} from '../useBotState.js'
import {useQuery} from '../useDb.js'

interface ModelRow {
  trained_at: number
  n_samples: number
  brier_score: number
  log_loss: number
  feature_cols: string
  deployed: number
}

interface RetrainRunRow {
  finished_at: number
  sample_count: number
  brier_score: number | null
  log_loss: number | null
  status: string | null
  deployed: number
  message: string | null
}

interface SharedHoldoutComparison {
  challenger_log_loss: number
  challenger_brier_score: number
  incumbent_log_loss: number
  incumbent_brier_score: number
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

interface RecentSignalModeRow {
  mode: string
  taken: number
}

interface CalibrationSummaryRow {
  resolved: number | null
  avg_confidence: number | null
  actual_win_rate: number | null
  avg_gap: number | null
}

interface ConfusionMatrixRow {
  true_positive: number | null
  false_positive: number | null
  true_negative: number | null
  false_negative: number | null
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

interface TrainingProgressRow {
  last_deployed_trained_at: number | null
  total_labeled: number | null
  new_labeled: number | null
}

interface ExitAuditSummaryRow {
  audits_7d: number | null
  exits_7d: number | null
  holds_7d: number | null
  hard_exits_7d: number | null
  avg_estimated_return_pct: number | null
  avg_exit_return_pct: number | null
  avg_hold_return_pct: number | null
  avg_spread_pct: number | null
  avg_depth_multiple: number | null
  last_audited_at: number | null
}

interface ExitAuditRecentRow {
  audited_at: number
  decision: string | null
  estimated_return_pct: number | null
  reason: string | null
}

interface ExitAttributionRow {
  resolved_exits_30d: number | null
  exit_helped_count: number | null
  exit_hurt_count: number | null
  total_exit_alpha_usd: number | null
  dollars_saved_usd: number | null
  dollars_given_up_usd: number | null
  avg_exit_delta_usd: number | null
}

interface ReplayRunRow {
  id: number
  finished_at: number
  label: string | null
  policy_version: string | null
  total_pnl_usd: number | null
  max_drawdown_pct: number | null
  accepted_count: number | null
  resolved_count: number | null
  win_rate: number | null
}

interface ReplaySegmentRow {
  segment_value: string | null
  accepted_count: number | null
  resolved_count: number | null
  total_pnl_usd: number | null
  win_rate: number | null
}

interface ReplaySearchSummaryRow {
  id: number
  finished_at: number
  label_prefix: string | null
  candidate_count: number | null
  feasible_count: number | null
  rejected_count: number | null
  current_candidate_score: number | null
  current_candidate_feasible: number | null
  current_candidate_total_pnl_usd: number | null
  current_candidate_max_drawdown_pct: number | null
  current_candidate_constraint_failures_json: string | null
  current_candidate_result_json: string | null
  best_vs_current_pnl_usd: number | null
  best_vs_current_score: number | null
  best_feasible_score: number | null
  candidate_index: number | null
  score: number | null
  total_pnl_usd: number | null
  max_drawdown_pct: number | null
  positive_window_count: number | null
  negative_window_count: number | null
  worst_window_pnl_usd: number | null
  drawdown_penalty: number | null
  window_stddev_penalty: number | null
  worst_window_penalty: number | null
  pause_guard_penalty: number | null
  resolved_share_penalty: number | null
  worst_window_resolved_share_penalty: number | null
  mode_resolved_share_penalty: number | null
  mode_worst_window_resolved_share_penalty: number | null
  mode_loss_penalty: number | null
  mode_inactivity_penalty: number | null
  wallet_concentration_penalty: number | null
  market_concentration_penalty: number | null
  entry_price_band_concentration_penalty: number | null
  time_to_close_band_concentration_penalty: number | null
  constraints_json: string | null
  base_policy_json: string | null
  overrides_json: string | null
  policy_json: string | null
  config_json: string | null
  result_json: string | null
}

export type ModelPanelId =
  | 'prediction_quality'
  | 'tracker_health'
  | 'replay_lab'
  | 'confidence_modes'
  | 'exit_guard'
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
      'This box separates scorer config, the scorer loaded in the runtime, and the model artifact on disk.',
      'Lower loss numbers grade the model artifact, not the bankroll curve.'
    ],
    rows: [
      {label: 'Scorer gates', text: 'Which scorer paths are enabled in config. Disabled paths cannot be loaded for new decisions.'},
      {label: 'Loaded scorer', text: 'Which scorer the running bot has loaded right now for new decisions.'},
      {label: 'Model artifact', text: 'The latest deployed model artifact on disk, even if live trading is currently falling back.'},
      {label: 'Contract', text: 'Artifact contract versus runtime contract. A mismatch means the runtime will reject the model.'},
      {label: 'Fallback', text: 'Why the runtime is using heuristics instead of the model, if it is degraded.'},
      {label: 'Trained', text: 'When the latest deployed model artifact was built.'},
      {label: 'Model age', text: 'How long that deployed model artifact has been sitting without a retrain.'},
      {label: 'Samples', text: 'How many resolved trades were available to train the deployed model artifact.'},
      {label: 'Features', text: 'How many inputs the deployed model artifact is using.'},
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
    id: 'replay_lab',
    title: 'Replay Lab',
    summary: [
      'This box reads the latest offline replay run instead of the live bankroll.',
      'Use it to see which wallets, price bands, and holding periods helped or hurt under the latest tested policy.'
    ],
    rows: [
      {label: 'Last replay', text: 'When the latest completed replay finished.'},
      {label: 'Policy', text: 'Replay label and shortened policy version hash for the latest run.'},
      {label: 'Replay P&L', text: 'Total replay profit versus the replay starting bankroll.'},
      {label: 'Max DD', text: 'Largest peak-to-trough drawdown in the replay bankroll curve.'},
      {label: 'Accept / win', text: 'Accepted replay trades and realized win rate on the resolved subset.'},
      {label: 'Search run', text: 'How recently the latest persisted replay search finished.'},
      {label: 'Search fea/rej', text: 'Feasible versus rejected candidate count from the latest replay search run.'},
      {label: 'Best search', text: 'Score and candidate index for the latest best feasible replay-search result.'},
      {label: 'Score weights', text: 'Active replay-search score weights on the latest search run, including drawdown, instability, worst-window loss, pause-guard, global coverage, worst-window coverage, scorer coverage, scorer worst-window coverage, scorer-loss, scorer-inactivity, and concentration terms.'},
      {label: 'Best score', text: 'Best feasible score decomposition: replay P&L minus drawdown, instability, worst-window loss, pause-guard, global coverage, worst-window coverage, scorer coverage, scorer worst-window coverage, scorer-loss, scorer-inactivity, and concentration penalties.'},
      {label: 'Search robust', text: 'Best feasible search candidate P&L and drawdown.'},
      {label: 'Search windows', text: 'Positive versus negative windows and the worst window P&L for the latest best feasible search candidate.'},
      {label: 'Cfg drift', text: 'How many editable config keys currently differ from the best feasible replay-search recommendation.'},
      {label: 'Suggest cfg', text: 'Compact summary of the recommended config values from the latest best feasible replay-search candidate.'},
      {label: 'Apply scope', text: 'How many recommended config changes apply live on the next loop versus requiring a restart, plus any replay-only leftovers.'},
      {label: 'Deploy gap', text: 'Recommendation pieces not currently present in the persisted editable-config payload for the latest best feasible candidate. Older search rows may need a rerun after config-surface changes.'},
      {label: 'Seg gates', text: 'Entry-price-band, holding-horizon, and scorer-path gates on the latest best feasible replay-search candidate.'},
      {label: 'Wallet conc', text: 'Best and current replay-search dependence on a single wallet, shown as top accepted-share and top absolute-P&L share plus any active caps.'},
      {label: 'Market conc', text: 'Best and current replay-search dependence on a single market, shown as top accepted-share and top absolute-P&L share plus any active caps.'},
      {label: 'Entry conc', text: 'Best and current replay-search dependence on a single entry-price band, shown as top accepted-share, top absolute-P&L share, and any active cap or score penalty.'},
      {label: 'Horizon conc', text: 'Best and current replay-search dependence on a single time-to-close band, shown as top accepted-share, top absolute-P&L share, and any active cap or score penalty.'},
      {label: 'Pause guard', text: 'Replay-search dependence on daily-loss or live-drawdown guard rejects, shown for best and current candidates plus any active cap or ranking penalty.'},
      {label: 'Search modes', text: 'Accepted trade mix, resolved coverage, and replay P&L by scorer on the latest best feasible replay-search candidate.'},
      {label: 'Cur evidence', text: 'Resolved evidence and replay P&L by scorer on the current/base replay-search candidate.'},
      {label: 'Mode guard', text: 'Per-scorer accepted-count, positive-window count, inactive-window count, resolved-count, resolved-share, win-rate, total P&L, worst-window P&L, worst-window resolved-share, and accepted-share guardrails from the latest replay search, if any.'},
      {label: 'Mode pen', text: 'Soft scorer-path ranking weights from the latest replay search, for scorer coverage, scorer worst-window coverage, scorer-loss, and scorer-inactivity pressure.'},
      {label: 'Best headroom', text: 'Closest active replay-search guard margins for the latest best feasible candidate, across global, heuristic, and model constraints.'},
      {label: 'Cur headroom', text: 'Closest active replay-search guard margins for the current/base candidate, across global, heuristic, and model constraints.'},
      {label: 'Mode drift', text: 'Best feasible scorer mix minus the current/base scorer mix, shown in accepted-share percentage points.'},
      {label: 'Cur mode risk', text: 'Current/base scorer-path breaches against the latest replay-search mode guardrails, or clear if none.'},
      {label: 'Cur fails', text: 'Exact replay-search feasibility failures for the current/base candidate, including non-scorer global failures.'},
      {label: 'Cur feasible', text: 'Whether the current/base config clears the replay-search feasibility gates, plus its replay P&L and drawdown.'},
      {label: 'Cur score', text: 'Current/base score decomposition: replay P&L minus drawdown, instability, worst-window loss, pause-guard, global coverage, worst-window coverage, scorer coverage, scorer worst-window coverage, scorer-loss, scorer-inactivity, and concentration penalties.'},
      {label: 'Score drift', text: 'Best feasible minus current/base score decomposition, split into replay P&L and each score penalty term, including coverage, scorer-path, and concentration penalties.'},
      {label: 'Cur regret', text: 'Best feasible minus current/base config, shown as replay P&L gap and score gap.'},
      {label: 'Best wallet', text: 'Wallet with the strongest replay P&L on the latest run, subject to the minimum resolved sample filter.'},
      {label: 'Worst wallet', text: 'Wallet with the weakest replay P&L on the latest run, subject to the minimum resolved sample filter.'},
      {label: 'Best band', text: 'Entry-price band with the strongest replay P&L on the latest run.'},
      {label: 'Worst band', text: 'Entry-price band with the weakest replay P&L on the latest run.'},
      {label: 'Best horizon', text: 'Time-to-close bucket with the strongest replay P&L on the latest run.'},
      {label: 'Worst horizon', text: 'Time-to-close bucket with the weakest replay P&L on the latest run.'}
    ],
    settingKeys: [
      'MIN_CONFIDENCE',
      'HEURISTIC_MIN_ENTRY_PRICE',
      'HEURISTIC_MAX_ENTRY_PRICE',
      'MODEL_EDGE_MID_THRESHOLD',
      'MODEL_EDGE_HIGH_THRESHOLD',
      'MAX_BET_FRACTION',
      'MAX_TOTAL_OPEN_EXPOSURE_FRACTION',
      'MAX_MARKET_EXPOSURE_FRACTION',
      'MAX_TRADER_EXPOSURE_FRACTION'
    ]
  },
  {
    id: 'confidence_modes',
    title: 'Confidence + Modes',
    summary: [
      'This box answers two questions: how calibrated are accepted bets, and which decision path is carrying the load?',
      'The left side reads model bias. The right side shows which scorer is active, primary, or idle.'
    ],
    rows: [
      {label: 'TP / FP / TN / FN', text: 'Outcome split behind the confidence gate: accepted wins, accepted losses, helpful skips, and missed winners.'},
      {label: 'Resolved', text: 'Accepted tracker bets that already settled and can be graded.'},
      {label: 'Predicted / Actual', text: 'Average model confidence versus the realized win rate on those graded bets.'},
      {label: 'Read / Bias', text: 'Plain-English bias read plus the point gap between prediction and reality.'},
      {label: 'Avg miss', text: 'Average miss per graded bet versus the actual 0/1 outcome. Lower is better.'},
      {label: 'Main band / hit', text: 'The most common confidence range and how often it actually won.'},
      {label: 'Recent path', text: 'Which scorer has driven the most recent accepted trades. This is historical activity, not the runtime load state.'},
      {label: 'Primary path', text: 'Which decision path has produced the most accepted trades so far.'},
      {label: 'Role', text: 'Whether a path is primary, secondary, or currently idle.'},
      {label: 'Signals / taken', text: 'How many candidate signals flowed through that path, and how many became bets.'},
      {label: 'Use / win', text: 'Acceptance rate and settled win rate for that path.'},
      {label: 'Avg edge', text: 'Average confidence edge over price for accepted bets in that path.'},
      {label: 'P&L', text: 'Cumulative tracker profit from that path.'}
    ],
    settingKeys: ['MIN_CONFIDENCE', 'MAX_MARKET_HORIZON']
  },
  {
    id: 'exit_guard',
    title: 'Exit Guard',
    summary: [
      'This box measures what the stop-loss replacement is actually doing with bad quotes.',
      'It shows whether the guard is exiting, holding because the quote is unreliable, or hard-exiting on severe breaches.'
    ],
    rows: [
      {label: 'Mode', text: 'Whether these exit audits are coming from shadow or live mode.'},
      {label: 'Audits 7d', text: 'How many stop-loss breaches were evaluated in the last 7 days.'},
      {label: 'Exit / hold', text: 'How many breaches became immediate exits versus guarded holds.'},
      {label: 'Hard exits', text: 'Breaches so severe that the guard exited immediately despite quote-quality checks.'},
      {label: 'Avg breach', text: 'Average executable return at the moment the guard evaluated the position.'},
      {label: 'Exit breach', text: 'Average executable return for the subset the guard actually exited.'},
      {label: 'Hold breach', text: 'Average executable return for the subset the guard held.'},
      {label: 'Avg spread', text: 'Average quoted spread on audited positions. Lower is better.'},
      {label: 'Avg depth', text: 'Average visible bid depth relative to the position notional. Higher is better.'},
      {label: 'Last audit', text: 'How recently the exit guard evaluated a stop-loss breach.'},
      {label: 'Resolved exits', text: 'Exited trades from the last 30 days that have already fully resolved and can be compared to a no-exit baseline.'},
      {label: 'Exit alpha', text: 'Dollar delta between realized exit P&L and the hold-to-resolution baseline. Positive means exits helped.'},
      {label: 'Saved / gave up', text: 'Gross dollars preserved by good exits versus gross dollars forfeited by bad exits.'},
      {label: 'Helped / hurt', text: 'How many resolved exits beat the hold baseline versus underperformed it.'},
      {label: 'Avg delta', text: 'Average per-exit dollar delta versus hold-to-resolution on the resolved sample.'}
    ],
    settingKeys: ['STOP_LOSS_ENABLED', 'STOP_LOSS_MAX_LOSS_PCT', 'STOP_LOSS_MIN_HOLD']
  },
  {
    id: 'how_it_works',
    title: 'How It Works',
    summary: [
      'This box shows the moving parts behind the heuristic score in plainer language.',
      'The base score comes from trader quality and market quality, then history can nudge it up or down.'
    ],
    rows: [
      {label: 'Trader input', text: 'Average trader quality score from behavior and results.'},
      {label: 'Market input', text: 'Average market quality score from spread, depth, time, and momentum.'},
      {label: 'Base mix', text: 'Weighted blend before history shifts it. Formula: trader^0.60 * market^0.40.'},
      {label: 'History prior', text: 'Average historical win prior for similar resolved trades.'},
      {label: 'History weight', text: 'How much the prior was allowed to move the base score.'},
      {label: 'Final estimate', text: 'Estimated final heuristic score after the prior adjustment.'},
      {label: 'History nudge', text: 'How many percentage points history moved the base score.'},
      {label: 'Evidence', text: 'Average number of prior examples backing that adjustment.'}
    ],
    settingKeys: ['MIN_CONFIDENCE', 'MAX_MARKET_HORIZON', 'MAX_BET_FRACTION']
  },
  {
    id: 'training_cycle',
    title: 'Training Cycle',
    summary: [
      'This box covers how often the model gets rebuilt and what has to happen before a new one goes live.',
      'These are full retrains on resolved trades, not online fine-tunes.',
      'History below includes deployed, no-deploy, skipped, and failed retrain attempts.'
    ],
    rows: [
      {label: 'Update style', text: 'The system rebuilds the model from resolved trades each cycle.'},
      {label: 'Base cadence', text: 'Regular scheduled retrain frequency.'},
      {label: 'Run time', text: 'Local hour when the scheduled retrain is attempted.'},
      {label: 'Next scheduled', text: 'Next planned scheduled retrain window from the current cadence and local hour.'},
      {label: 'Scheduled in', text: 'Countdown until that scheduled retrain window.'},
      {label: 'Early check', text: 'How often the bot checks whether it should retrain sooner.'},
      {label: 'Early trigger', text: 'Minimum new labels needed to fire an unscheduled retrain.'},
      {
        label: 'Trigger progress',
        text: 'Progress toward the next retrain trigger. With a deployed model this counts new eligible labels since that model went live; before the first model it falls back to total labeled samples versus the minimum sample gate.'
            },
            {label: 'Manual run', text: 'Press t while this panel is selected to queue an in-process retrain through the running bot.'},
            {label: 'Shared gate', text: 'Latest apples-to-apples challenger versus incumbent comparison on the same final holdout. This is the actual deployment guardrail.'}
        ],
        settingKeys: ['RETRAIN_BASE_CADENCE', 'RETRAIN_HOUR_LOCAL', 'RETRAIN_EARLY_CHECK_INTERVAL', 'RETRAIN_MIN_NEW_LABELS', 'RETRAIN_MIN_SAMPLES']
  }
]

export const MODEL_PANEL_COLUMN_LAYOUT: number[][] = [
  [0, 1, 2],
  [3, 4],
  [5, 6]
]

interface ModelsProps {
  selectedPanelIndex: number
  detailOpen: boolean
  selectedSettingIndex: number
  settingsValues: EditableConfigValues
}

interface CompactStatItem {
  label: string
  value: string
  color?: string
}

interface ModePreviewCard {
  title: string
  titleColor?: string
  rows: CompactStatItem[]
}

type ConfusionHeatKind = 'good' | 'bad'

interface ConfusionCellProps {
  label: string
  value: number
  width: number
  kind: ConfusionHeatKind
  scale: number
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

const LOW_CONF_SKIP_WHERE = `
skipped=1
AND outcome IS NOT NULL
AND counterfactual_return IS NOT NULL
AND LOWER(COALESCE(skip_reason, '')) LIKE '%below the%'
AND LOWER(COALESCE(skip_reason, '')) LIKE '%minimum%'
AND (
  LOWER(COALESCE(skip_reason, '')) LIKE '%confidence%'
  OR LOWER(COALESCE(skip_reason, '')) LIKE '%heuristic score%'
)
`

const TRAINABLE_SKIPPED_REASON_WHERE = `
(
  LOWER(COALESCE(skip_reason, '')) LIKE 'signal confidence was %below the % minimum'
  OR LOWER(COALESCE(skip_reason, '')) LIKE 'confidence was %below the % minimum needed to place a trade'
  OR LOWER(COALESCE(skip_reason, '')) LIKE 'heuristic score was %below the % minimum needed to place a trade'
  OR LOWER(COALESCE(skip_reason, '')) LIKE 'model edge was %below the % threshold'
  OR LOWER(COALESCE(skip_reason, '')) = 'trade did not pass the signal checks'
  OR LOWER(COALESCE(skip_reason, '')) = 'kelly sizing found no positive edge at this price, so the trade was skipped'
)
`

const RESOLVED_TRAINABLE_SKIPPED_BUY_WHERE = `
skipped=1
AND COALESCE(source_action, 'buy')='buy'
AND counterfactual_return IS NOT NULL
AND ${TRAINABLE_SKIPPED_REASON_WHERE}
`

const RESOLVED_TRAINING_SAMPLE_WHERE = `
(
  ${RESOLVED_EXECUTED_ENTRY_WHERE}
)
OR
(
  ${RESOLVED_TRAINABLE_SKIPPED_BUY_WHERE}
)
`

const MODEL_SQL = `
SELECT trained_at, n_samples, brier_score, log_loss, feature_cols, deployed
FROM model_history
ORDER BY trained_at DESC
LIMIT 12
`

const RETRAIN_RUN_SQL = `
SELECT
  finished_at,
  sample_count,
  brier_score,
  log_loss,
  status,
  deployed,
  message
FROM retrain_runs
ORDER BY finished_at DESC, id DESC
LIMIT 48
`

const REPLAY_SEGMENT_MIN_RESOLVED = 3

const REPLAY_LATEST_RUN_SQL = `
SELECT
  id,
  finished_at,
  label,
  policy_version,
  total_pnl_usd,
  max_drawdown_pct,
  accepted_count,
  resolved_count,
  win_rate
FROM replay_runs
WHERE status='completed'
ORDER BY finished_at DESC, id DESC
LIMIT 1
`

const REPLAY_SEGMENT_BEST_SQL = `
WITH latest_run AS (
  SELECT id
  FROM replay_runs
  WHERE status='completed'
  ORDER BY finished_at DESC, id DESC
  LIMIT 1
)
SELECT
  segment_value,
  accepted_count,
  resolved_count,
  total_pnl_usd,
  win_rate
FROM segment_metrics
WHERE replay_run_id=(SELECT id FROM latest_run)
  AND segment_kind=?
  AND accepted_count > 0
  AND resolved_count >= ?
ORDER BY total_pnl_usd DESC, resolved_count DESC, accepted_count DESC, segment_value ASC
LIMIT 1
`

const REPLAY_SEGMENT_WORST_SQL = `
WITH latest_run AS (
  SELECT id
  FROM replay_runs
  WHERE status='completed'
  ORDER BY finished_at DESC, id DESC
  LIMIT 1
)
SELECT
  segment_value,
  accepted_count,
  resolved_count,
  total_pnl_usd,
  win_rate
FROM segment_metrics
WHERE replay_run_id=(SELECT id FROM latest_run)
  AND segment_kind=?
  AND accepted_count > 0
  AND resolved_count >= ?
ORDER BY total_pnl_usd ASC, resolved_count DESC, accepted_count DESC, segment_value ASC
LIMIT 1
`

const REPLAY_SEARCH_SUMMARY_SQL = `
WITH latest_search AS (
  SELECT
    id,
    finished_at,
    label_prefix,
    candidate_count,
    feasible_count,
    rejected_count,
    constraints_json,
    base_policy_json,
    current_candidate_score,
    current_candidate_feasible,
    current_candidate_total_pnl_usd,
    current_candidate_max_drawdown_pct,
    current_candidate_constraint_failures_json,
    current_candidate_result_json,
    best_vs_current_pnl_usd,
    best_vs_current_score,
    best_feasible_score,
    drawdown_penalty,
    window_stddev_penalty,
    worst_window_penalty,
    pause_guard_penalty,
    resolved_share_penalty,
    worst_window_resolved_share_penalty,
    mode_resolved_share_penalty,
    mode_worst_window_resolved_share_penalty,
    mode_loss_penalty,
    mode_inactivity_penalty,
    wallet_concentration_penalty,
    market_concentration_penalty,
    entry_price_band_concentration_penalty,
    time_to_close_band_concentration_penalty
  FROM replay_search_runs
  ORDER BY finished_at DESC, id DESC
  LIMIT 1
),
best_candidate AS (
  SELECT
    replay_search_run_id,
    candidate_index,
    score,
    total_pnl_usd,
    max_drawdown_pct,
    positive_window_count,
    negative_window_count,
    worst_window_pnl_usd,
    result_json,
    overrides_json,
    policy_json,
    config_json
  FROM replay_search_candidates
  WHERE replay_search_run_id=(SELECT id FROM latest_search)
    AND feasible=1
    AND candidate_index=(SELECT best_feasible_candidate_index FROM latest_search)
)
SELECT
  latest_search.id,
  latest_search.finished_at,
  latest_search.label_prefix,
  latest_search.candidate_count,
  latest_search.feasible_count,
  latest_search.rejected_count,
  latest_search.constraints_json,
  latest_search.base_policy_json,
  latest_search.current_candidate_score,
  latest_search.current_candidate_feasible,
  latest_search.current_candidate_total_pnl_usd,
  latest_search.current_candidate_max_drawdown_pct,
  latest_search.current_candidate_constraint_failures_json,
  latest_search.current_candidate_result_json,
  latest_search.best_vs_current_pnl_usd,
  latest_search.best_vs_current_score,
  latest_search.best_feasible_score,
  latest_search.drawdown_penalty,
  latest_search.window_stddev_penalty,
  latest_search.worst_window_penalty,
  latest_search.pause_guard_penalty,
  latest_search.resolved_share_penalty,
  latest_search.worst_window_resolved_share_penalty,
  latest_search.mode_resolved_share_penalty,
  latest_search.mode_worst_window_resolved_share_penalty,
  latest_search.mode_loss_penalty,
  latest_search.mode_inactivity_penalty,
  latest_search.wallet_concentration_penalty,
  latest_search.market_concentration_penalty,
  latest_search.entry_price_band_concentration_penalty,
  latest_search.time_to_close_band_concentration_penalty,
  best_candidate.candidate_index,
  best_candidate.score,
  best_candidate.total_pnl_usd,
  best_candidate.max_drawdown_pct,
  best_candidate.positive_window_count,
  best_candidate.negative_window_count,
  best_candidate.worst_window_pnl_usd,
  best_candidate.result_json,
  best_candidate.overrides_json,
  best_candidate.policy_json,
  best_candidate.config_json
FROM latest_search
LEFT JOIN best_candidate ON best_candidate.replay_search_run_id=latest_search.id
`

const SHARED_HOLDOUT_MESSAGE_RE = /shared holdout ll\/brier:\s*([-+]?[0-9]*\.?[0-9]+)\s*\/\s*([-+]?[0-9]*\.?[0-9]+)[\s\S]*?incumbent ll\/brier:\s*([-+]?[0-9]*\.?[0-9]+)\s*\/\s*([-+]?[0-9]*\.?[0-9]+)/i

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

const RECENT_SIGNAL_MODE_SQL = `
WITH recent_window AS (
  SELECT COALESCE(MAX(observed_at), 0) - 172800 AS cutoff_ts
  FROM trade_log
  WHERE real_money=0
    AND COALESCE(source_action, 'buy')='buy'
)
SELECT
  COALESCE(NULLIF(signal_mode, ''), 'heuristic') AS mode,
  COUNT(*) AS taken
FROM trade_log
WHERE COALESCE(source_action, 'buy')='buy'
  AND real_money=0
  AND actual_entry_size_usd IS NOT NULL
  AND observed_at >= (SELECT cutoff_ts FROM recent_window)
GROUP BY COALESCE(NULLIF(signal_mode, ''), 'heuristic')
ORDER BY taken DESC, mode ASC
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

const CONFUSION_SQL = `
SELECT
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} AND COALESCE(actual_pnl_usd, shadow_pnl_usd) > 0 THEN 1 ELSE 0 END) AS true_positive,
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} AND COALESCE(actual_pnl_usd, shadow_pnl_usd) <= 0 THEN 1 ELSE 0 END) AS false_positive,
  SUM(CASE WHEN ${LOW_CONF_SKIP_WHERE} AND counterfactual_return <= 0 THEN 1 ELSE 0 END) AS true_negative,
  SUM(CASE WHEN ${LOW_CONF_SKIP_WHERE} AND counterfactual_return > 0 THEN 1 ELSE 0 END) AS false_negative
FROM trade_log
WHERE COALESCE(source_action, 'buy')='buy'
  AND real_money=0
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
  SUM(CASE WHEN finished_at >= strftime('%s', 'now', '-7 days') THEN 1 ELSE 0 END) AS runs_7d,
  SUM(CASE WHEN finished_at >= strftime('%s', 'now', '-30 days') THEN 1 ELSE 0 END) AS runs_30d
FROM retrain_runs
`

const TRAINING_PROGRESS_SQL = `
WITH latest_model AS (
  SELECT trained_at
  FROM model_history
  WHERE deployed=1
  ORDER BY trained_at DESC
  LIMIT 1
)
SELECT
  (SELECT trained_at FROM latest_model) AS last_deployed_trained_at,
  COUNT(*) AS total_labeled,
  COALESCE(
    SUM(
      CASE
        WHEN COALESCE(label_applied_at, resolved_at, placed_at) > COALESCE((SELECT trained_at FROM latest_model), 0)
        THEN 1
        ELSE 0
      END
    ),
    0
  ) AS new_labeled
FROM trade_log
WHERE ${RESOLVED_TRAINING_SAMPLE_WHERE}
`

const EXIT_AUDIT_SUMMARY_SQL = `
SELECT
  COUNT(*) AS audits_7d,
  SUM(CASE WHEN decision='exit' THEN 1 ELSE 0 END) AS exits_7d,
  SUM(CASE WHEN decision='hold' THEN 1 ELSE 0 END) AS holds_7d,
  SUM(CASE WHEN LOWER(COALESCE(reason, '')) LIKE 'hard exit%' THEN 1 ELSE 0 END) AS hard_exits_7d,
  AVG(estimated_return_pct) AS avg_estimated_return_pct,
  AVG(CASE WHEN decision='exit' THEN estimated_return_pct END) AS avg_exit_return_pct,
  AVG(CASE WHEN decision='hold' THEN estimated_return_pct END) AS avg_hold_return_pct,
  AVG(CAST(json_extract(metadata_json, '$.spread_pct') AS REAL)) AS avg_spread_pct,
  AVG(CAST(json_extract(metadata_json, '$.depth_multiple') AS REAL)) AS avg_depth_multiple,
  MAX(audited_at) AS last_audited_at
FROM exit_audits
WHERE real_money=?
  AND audited_at >= strftime('%s', 'now', '-7 days')
`

const EXIT_AUDIT_RECENT_SQL = `
SELECT
  audited_at,
  decision,
  estimated_return_pct,
  reason
FROM exit_audits
WHERE real_money=?
ORDER BY audited_at DESC, id DESC
LIMIT 8
`

const EXIT_ATTRIBUTION_SQL = `
WITH exited AS (
  SELECT
    COALESCE(shadow_pnl_usd, actual_pnl_usd) AS realized_pnl_usd,
    (
      CASE WHEN outcome=1 THEN COALESCE(actual_entry_shares, 0) ELSE 0 END
      - COALESCE(actual_entry_size_usd, 0)
      - CASE
          WHEN outcome=1 AND COALESCE(actual_entry_shares, 0) > 1e-9 THEN ?
          ELSE 0
        END
    ) AS hold_pnl_usd
  FROM trade_log
  WHERE real_money=?
    AND COALESCE(source_action, 'buy')='buy'
    AND exited_at IS NOT NULL
    AND resolved_at IS NOT NULL
    AND actual_entry_size_usd IS NOT NULL
    AND actual_entry_shares IS NOT NULL
    AND outcome IS NOT NULL
    AND resolved_at >= strftime('%s', 'now', '-30 days')
),
deltas AS (
  SELECT
    realized_pnl_usd,
    hold_pnl_usd,
    realized_pnl_usd - hold_pnl_usd AS exit_delta_usd
  FROM exited
)
SELECT
  COUNT(*) AS resolved_exits_30d,
  SUM(CASE WHEN exit_delta_usd > 0 THEN 1 ELSE 0 END) AS exit_helped_count,
  SUM(CASE WHEN exit_delta_usd < 0 THEN 1 ELSE 0 END) AS exit_hurt_count,
  SUM(exit_delta_usd) AS total_exit_alpha_usd,
  SUM(CASE WHEN exit_delta_usd > 0 THEN exit_delta_usd ELSE 0 END) AS dollars_saved_usd,
  SUM(CASE WHEN exit_delta_usd < 0 THEN -exit_delta_usd ELSE 0 END) AS dollars_given_up_usd,
  AVG(exit_delta_usd) AS avg_exit_delta_usd
FROM deltas
`

function formatCount(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '0'
  return Math.round(value).toLocaleString()
}

function centerLine(text: string, width: number): string {
  const safeWidth = Math.max(1, width)
  const clipped = text.length > safeWidth ? text.slice(0, safeWidth) : text
  const remaining = Math.max(0, safeWidth - clipped.length)
  const left = Math.floor(remaining / 2)
  const right = remaining - left
  return `${' '.repeat(left)}${clipped}${' '.repeat(right)}`
}

function confusionHeatColor(value: number, scale: number, kind: ConfusionHeatKind): string {
  const safeValue = Math.max(0, value)
  const safeScale = Math.max(1, scale)
  return kind === 'good'
    ? positiveDollarColor(safeValue, safeScale)
    : negativeHeatColor(safeValue, safeScale)
}

function ConfusionMatrixCell({label, value, width, kind, scale}: ConfusionCellProps) {
  const fillColor = confusionHeatColor(value, scale, kind)
  const innerWidth = Math.max(1, width - 2)
  const centeredValue = centerLine(`${label} ${formatCount(value)}`, innerWidth)

  return (
    <InkBox width={width} height={6} borderStyle="round" borderColor={fillColor} flexDirection="column">
      <Text color={theme.modalBackground} backgroundColor={fillColor}>
        {' '.repeat(innerWidth)}
      </Text>
      <Text color={theme.modalBackground} backgroundColor={fillColor} bold>
        {' '.repeat(innerWidth)}
      </Text>
      <Text color={theme.modalBackground} backgroundColor={fillColor} bold>
        {centeredValue}
      </Text>
      <Text color={theme.modalBackground} backgroundColor={fillColor}>{' '.repeat(innerWidth)}</Text>
    </InkBox>
  )
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
  const worst = okay + (okay - good)
  const normalized = Math.max(0, Math.min(1, (worst - value) / Math.max(0.0001, worst - good)))
  return probabilityColor(normalized)
}

function sharpeColor(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return theme.dim
  return centeredGradientColor(value, 1)
}

function signedMetricColor(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return theme.dim
  return centeredGradientColor(value, 0.2)
}

function biasColor(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return theme.dim
  return centeredGradientColor(-value, 0.08)
}

function dollarColor(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return theme.dim
  return centeredGradientColor(value, 250)
}

function depthMultipleColor(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return theme.dim
  return probabilityColor(Math.max(0, Math.min(1, value / 1.25)))
}

function exitDecisionLabel(decision: string | null | undefined, reason: string | null | undefined): string {
  const normalized = String(decision || '').trim().toLowerCase()
  const normalizedReason = String(reason || '').trim().toLowerCase()
  if (normalizedReason.startsWith('hard exit')) return 'Hard exit'
  if (normalized === 'exit') return 'Exit'
  if (normalized === 'hold') return 'Hold'
  return normalized ? normalized : '-'
}

function exitDecisionColor(decision: string | null | undefined, reason: string | null | undefined): string {
  const label = exitDecisionLabel(decision, reason).toLowerCase()
  if (label === 'hard exit') return theme.red
  if (label === 'exit') return theme.yellow
  if (label === 'hold') return theme.blue
  return theme.dim
}

function bucketLabel(bucket: number): string {
  const lower = bucket * 10
  const upper = bucket === 9 ? 100 : bucket * 10 + 9
  return `${lower}-${upper}%`
}

function formatPointDelta(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '-'
  const abs = Math.abs(value * 100).toFixed(digits)
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  return `${sign}${abs}pt`
}

function calibrationRead(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-'
  if (Math.abs(value) <= 0.03) return 'In line'
  return value > 0 ? 'Overcalling' : 'Undercalling'
}

function modeRoleLabel(mode: string, taken: number, primaryMode: string | null, activeScorerLabel: string): string {
  const normalizedMode = mode.trim().toLowerCase()
  if (taken <= 0) {
    return normalizedMode === 'veto' ? 'Guardrail idle' : 'Idle'
  }
  if (normalizedMode === primaryMode) {
    return normalizedMode === activeScorerLabel.trim().toLowerCase() ? 'Primary recent path' : 'Primary path'
  }
  return 'Secondary path'
}

function retrainRunStateLabel(status: string | null | undefined, deployed: number | null | undefined): string {
  if (deployed) return 'deployed'
  const normalized = (status || '').trim().toLowerCase()
  if (!normalized) return '-'
  if (normalized === 'completed_not_deployed') return 'no deploy'
  if (normalized === 'skipped_not_enough_samples') return 'skip samples'
  if (normalized === 'skipped_insufficient_class_diversity') return 'skip diversity'
  if (normalized === 'skipped_insufficient_samples_for_holdout_search') return 'skip holdout'
  if (normalized === 'skipped_candidate_search_produced_no_valid_model') return 'skip search'
  if (normalized.startsWith('skipped_')) return 'skip'
  if (normalized === 'failed') return 'failed'
  if (normalized === 'already_running') return 'busy'
  return normalized.replace(/_/g, ' ')
}

function retrainRunStateCompactLabel(status: string | null | undefined, deployed: number | null | undefined): string {
  if (deployed) return 'deploy'
  const normalized = (status || '').trim().toLowerCase()
  if (!normalized) return '-'
  if (normalized === 'completed_not_deployed') return 'no dep'
  if (normalized === 'skipped_not_enough_samples') return 'skip smp'
  if (normalized === 'skipped_insufficient_class_diversity') return 'skip div'
  if (normalized === 'skipped_insufficient_samples_for_holdout_search') return 'skip hld'
  if (normalized === 'skipped_candidate_search_produced_no_valid_model') return 'skip src'
  if (normalized.startsWith('skipped_')) return 'skip'
  if (normalized === 'failed') return 'failed'
  if (normalized === 'already_running') return 'busy'
  return normalized.replace(/_/g, ' ')
}

function retrainRunStateColor(status: string | null | undefined, deployed: number | null | undefined): string {
  if (deployed) return theme.green
  const normalized = (status || '').trim().toLowerCase()
  if (!normalized) return theme.dim
  if (normalized === 'deployed') return theme.green
  if (normalized === 'completed_not_deployed') return theme.yellow
  if (normalized.startsWith('skipped_')) return theme.dim
  if (normalized === 'failed') return theme.red
  if (normalized === 'already_running') return theme.yellow
  return theme.white
}

function sharedHoldoutComparison(row: RetrainRunRow | null | undefined): SharedHoldoutComparison | null {
  const message = String(row?.message || '').trim()
  if (!message) return null
  const match = message.match(SHARED_HOLDOUT_MESSAGE_RE)
  if (!match) return null
  const challengerLogLoss = Number.parseFloat(match[1] || '')
  const challengerBrierScore = Number.parseFloat(match[2] || '')
  const incumbentLogLoss = Number.parseFloat(match[3] || '')
  const incumbentBrierScore = Number.parseFloat(match[4] || '')
  if (
    Number.isNaN(challengerLogLoss)
    || Number.isNaN(challengerBrierScore)
    || Number.isNaN(incumbentLogLoss)
    || Number.isNaN(incumbentBrierScore)
  ) {
    return null
  }
  return {
    challenger_log_loss: challengerLogLoss,
    challenger_brier_score: challengerBrierScore,
    incumbent_log_loss: incumbentLogLoss,
    incumbent_brier_score: incumbentBrierScore
  }
}

function sharedHoldoutGateRead(row: RetrainRunRow | null | undefined): string {
  const comparison = sharedHoldoutComparison(row)
  if (!comparison) return '-'
  const outcomes = [
    comparison.challenger_log_loss < comparison.incumbent_log_loss
      ? 'LL better'
      : comparison.challenger_log_loss > comparison.incumbent_log_loss
        ? 'LL worse'
        : 'LL tied',
    comparison.challenger_brier_score < comparison.incumbent_brier_score
      ? 'Brier better'
      : comparison.challenger_brier_score > comparison.incumbent_brier_score
        ? 'Brier worse'
        : 'Brier tied'
  ]
  return outcomes.join(', ')
}

function sharedHoldoutGateReadCompact(row: RetrainRunRow | null | undefined): string {
  return sharedHoldoutGateRead(row).replace(', ', ' / ')
}

function sharedHoldoutGateReadColor(row: RetrainRunRow | null | undefined): string {
  const comparison = sharedHoldoutComparison(row)
  if (!comparison) return theme.dim
  const llDelta = comparison.challenger_log_loss - comparison.incumbent_log_loss
  const brierDelta = comparison.challenger_brier_score - comparison.incumbent_brier_score
  if (llDelta < 0 && brierDelta < 0) return theme.green
  if (llDelta > 0 && brierDelta > 0) return theme.red
  if (llDelta === 0 && brierDelta === 0) return theme.dim
  return theme.yellow
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

function parseNonNegativeInt(value: string | null | undefined, fallback: number): number {
  const parsed = Number.parseInt(value || '', 10)
  if (!Number.isFinite(parsed)) return fallback
  return Math.max(parsed, 0)
}

function progressStatColor(current: number, target: number): string {
  if (current <= 0) return theme.dim
  if (target <= 0 || current >= target) return theme.green
  return theme.yellow
}

function manualRetrainLabel(startedAt: number, lastActivityAt: number, pollInterval: number, retrainInProgress: boolean, nowTs: number): CompactStatItem {
  if (retrainInProgress) {
    return {label: 'Manual run', value: 'Running...', color: theme.yellow}
  }

  const heartbeatWindow = Math.max(pollInterval * 3, 30)
  const online = startedAt > 0 && lastActivityAt > 0 && (nowTs - lastActivityAt) <= heartbeatWindow
  if (!online) {
    return {label: 'Manual run', value: 'Bot offline', color: theme.dim}
  }

  return {label: 'Manual run', value: 'Press t', color: theme.accent}
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
  if (normalized === 'xgboost') return 'XGBoost'
  if (normalized === 'ml') return 'XGBoost'
  if (normalized === 'hist_gradient_boosting') return 'XGBoost'
  if (normalized === 'heuristic') return 'Heuristic'
  if (normalized === 'disabled') return 'No scorer'
  if (normalized === 'shadow') return 'Tracker'
  if (normalized === 'live') return 'Live'
  return mode || 'Unknown'
}

function compactWalletLabel(value: string | null | undefined): string {
  const normalized = String(value || '').trim().toLowerCase()
  if (!normalized) return '-'
  if (!normalized.startsWith('0x') || normalized.length <= 12) return normalized
  return `${normalized.slice(0, 6)}..${normalized.slice(-4)}`
}

function replaySegmentLabel(kind: 'trader_address' | 'entry_price_band' | 'time_to_close_band', value: string | null | undefined): string {
  const normalized = String(value || '').trim()
  if (!normalized) return '-'
  if (kind === 'trader_address') return compactWalletLabel(normalized)
  return normalized
}

function replaySegmentValue(
  kind: 'trader_address' | 'entry_price_band' | 'time_to_close_band',
  row: ReplaySegmentRow | null | undefined
): string {
  if (!row?.segment_value) return '-'
  const label = replaySegmentLabel(kind, row.segment_value)
  const pnl = formatDollar(row.total_pnl_usd)
  return `${label} ${pnl}`.trim()
}

function replaySearchOverrideSummary(raw: string | null | undefined): string {
  if (!raw) return '-'
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return '-'
    const payload = parsed as Record<string, unknown>
    const parts: string[] = []
    if (payload.min_confidence != null) parts.push(`conf ${payload.min_confidence}`)
    if (payload.heuristic_min_entry_price != null && payload.heuristic_max_entry_price != null) {
      parts.push(`band ${payload.heuristic_min_entry_price}-${payload.heuristic_max_entry_price}`)
    }
    if (payload.max_bet_fraction != null) parts.push(`bet ${payload.max_bet_fraction}`)
    if (payload.model_edge_mid_threshold != null || payload.model_edge_high_threshold != null) {
      parts.push(`edge ${payload.model_edge_mid_threshold ?? '-'} / ${payload.model_edge_high_threshold ?? '-'}`)
    }
    if (parts.length) return parts.join(', ')
    const fallbackKeys = Object.keys(payload).slice(0, 3)
    return fallbackKeys.length ? fallbackKeys.map((key) => `${key}=${String(payload[key])}`).join(', ') : '-'
  } catch {
    return '-'
  }
}

function replaySearchSegmentGateSummary(raw: string | null | undefined): string {
  if (!raw) return 'all'
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return 'all'
    const payload = parsed as Record<string, unknown>
    const entryBands = Array.isArray(payload.allowed_entry_price_bands)
      ? payload.allowed_entry_price_bands.map((value) => String(value || '').trim()).filter(Boolean)
      : []
    const horizonBands = Array.isArray(payload.allowed_time_to_close_bands)
      ? payload.allowed_time_to_close_bands.map((value) => String(value || '').trim()).filter(Boolean)
      : []
    const heuristicBands = Array.isArray(payload.heuristic_allowed_entry_price_bands)
      ? payload.heuristic_allowed_entry_price_bands.map((value) => String(value || '').trim()).filter(Boolean)
      : []
    const modelBands = Array.isArray(payload.xgboost_allowed_entry_price_bands)
      ? payload.xgboost_allowed_entry_price_bands.map((value) => String(value || '').trim()).filter(Boolean)
      : []
    const heuristicMinHorizonRaw = payload.heuristic_min_time_to_close_seconds
    const modelMinHorizonRaw = payload.model_min_time_to_close_seconds
    const allowHeuristic = payload.allow_heuristic
    const allowXgboost = payload.allow_xgboost
    const heuristicMinHorizon = replayFormatDurationSeconds(
      typeof heuristicMinHorizonRaw === 'number' ? heuristicMinHorizonRaw : Number(heuristicMinHorizonRaw)
    )
    const modelMinHorizon = replayFormatDurationSeconds(
      typeof modelMinHorizonRaw === 'number' ? modelMinHorizonRaw : Number(modelMinHorizonRaw)
    )
    const parts: string[] = []
    if (entryBands.length) parts.push(`band ${entryBands.join('|')}`)
    if (horizonBands.length) parts.push(`hzn ${horizonBands.join('|')}`)
    if (heuristicBands.length) parts.push(`heur band ${heuristicBands.join('|')}`)
    if (heuristicMinHorizon && heuristicMinHorizon !== '0s') parts.push(`heur >=${heuristicMinHorizon}`)
    if (modelBands.length) parts.push(`model band ${modelBands.join('|')}`)
    if (modelMinHorizon && modelMinHorizon !== '0s') parts.push(`model >=${modelMinHorizon}`)
    if (allowHeuristic === false) parts.push('heur off')
    if (allowXgboost === false) parts.push('xgb off')
    return parts.length ? parts.join(', ') : 'all'
  } catch {
    return 'all'
  }
}

function replaySearchDeployGapSummary(
  rawPolicy: string | null | undefined,
  rawConfig: string | null | undefined
): string {
  if (!rawPolicy) return '-'
  try {
    const parsedPolicy = JSON.parse(rawPolicy)
    if (!parsedPolicy || typeof parsedPolicy !== 'object' || Array.isArray(parsedPolicy)) return '-'
    const payload = parsedPolicy as Record<string, unknown>
    const parsedConfig = rawConfig ? JSON.parse(rawConfig) : null
    const configPayload = parsedConfig && typeof parsedConfig === 'object' && !Array.isArray(parsedConfig)
      ? parsedConfig as Record<string, unknown>
      : {}
    const gaps: string[] = []
    if (payload.allow_heuristic === false && !Object.prototype.hasOwnProperty.call(configPayload, 'ALLOW_HEURISTIC')) {
      gaps.push('rerun: heur cfg')
    }
    if (payload.allow_xgboost === false && !Object.prototype.hasOwnProperty.call(configPayload, 'ALLOW_XGBOOST')) {
      gaps.push('rerun: xgb cfg')
    }
    return gaps.length ? gaps.join(' | ') : 'none'
  } catch {
    return '-'
  }
}

function replaySearchModeMixSummary(
  raw: string | null | undefined,
  policyRaw: string | null | undefined
): string {
  if (!raw) return '-'
  try {
    const parsed = JSON.parse(raw)
    const enabled = replaySearchEnabledModes(policyRaw)
    const rawSummary = parsed?.signal_mode_summary
    if (!rawSummary || typeof rawSummary !== 'object' || Array.isArray(rawSummary)) return '-'
    const parsedEntries = Object.entries(rawSummary as Record<string, unknown>)
      .map(([mode, value]) => {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return null
        const payload = value as Record<string, unknown>
        return {
          mode,
          acceptedCount: Number(payload.accepted_count || 0),
          resolvedCount: Number(payload.resolved_count || 0),
          totalPnlUsd: Number(payload.total_pnl_usd || 0)
        }
      })
      .filter((entry): entry is {mode: string; acceptedCount: number; resolvedCount: number; totalPnlUsd: number} => Boolean(entry))
      .filter((entry) => {
        if (entry.mode === 'heuristic') return enabled.heuristic
        if (entry.mode === 'xgboost') return enabled.xgboost
        return true
      })
    const entryByMode = new Map(parsedEntries.map((entry) => [entry.mode, entry]))
    const entries: Array<{mode: string; acceptedCount: number; resolvedCount: number; totalPnlUsd: number}> = []
    if (enabled.heuristic) {
      entries.push(entryByMode.get('heuristic') ?? {
        mode: 'heuristic',
        acceptedCount: 0,
        resolvedCount: 0,
        totalPnlUsd: 0
      })
    }
    if (enabled.xgboost) {
      entries.push(entryByMode.get('xgboost') ?? {
        mode: 'xgboost',
        acceptedCount: 0,
        resolvedCount: 0,
        totalPnlUsd: 0
      })
    }
    parsedEntries
      .filter((entry) => entry.mode !== 'heuristic' && entry.mode !== 'xgboost')
      .sort((left, right) => left.mode.localeCompare(right.mode))
      .forEach((entry) => {
        if (entry.acceptedCount > 0) entries.push(entry)
      })
    if (!entries.length) {
      const parts: string[] = []
      if (!enabled.heuristic) parts.push('Heuristic off')
      if (!enabled.xgboost) parts.push('XGBoost off')
      return parts.length ? parts.join(' | ') : '-'
    }
    const totalAccepted = entries.reduce((sum, entry) => sum + entry.acceptedCount, 0)
    const parts = entries
      .map((entry) => {
        const share = totalAccepted > 0 ? `${Math.round((entry.acceptedCount / totalAccepted) * 100)}%` : '0%'
        const resolvedShare = entry.acceptedCount > 0 ? formatPct(entry.resolvedCount / entry.acceptedCount, 0) : '0%'
        return `${modeLabel(entry.mode)} ${formatCount(entry.acceptedCount)} ${share} cov ${resolvedShare} ${formatDollar(entry.totalPnlUsd)}`
      })
    if (!enabled.heuristic) parts.push('Heuristic off')
    if (!enabled.xgboost) parts.push('XGBoost off')
    return parts.join(' | ')
  } catch {
    return '-'
  }
}

function replaySearchModeShares(
  raw: string | null | undefined,
  policyRaw: string | null | undefined
): Map<string, number> {
  const shares = new Map<string, number>()
  if (!raw) return shares
  try {
    const parsed = JSON.parse(raw)
    const enabled = replaySearchEnabledModes(policyRaw)
    const rawSummary = parsed?.signal_mode_summary
    if (!rawSummary || typeof rawSummary !== 'object' || Array.isArray(rawSummary)) return shares
    const entries = Object.entries(rawSummary as Record<string, unknown>)
      .map(([mode, value]) => {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return null
        const payload = value as Record<string, unknown>
        return {
          mode: String(mode || '').trim(),
          acceptedCount: Number(payload.accepted_count || 0)
        }
      })
      .filter((entry): entry is {mode: string; acceptedCount: number} => Boolean(entry))
      .filter((entry) => {
        if (entry.mode === 'heuristic') return enabled.heuristic
        if (entry.mode === 'xgboost') return enabled.xgboost
        return true
      })
      .filter((entry) => entry.acceptedCount > 0)
    const totalAccepted = entries.reduce((sum, entry) => sum + entry.acceptedCount, 0)
    if (totalAccepted <= 0) return shares
    for (const entry of entries) {
      shares.set(entry.mode, entry.acceptedCount / totalAccepted)
    }
  } catch {
    return shares
  }
  return shares
}

function replaySearchModeDriftSummary(
  bestRaw: string | null | undefined,
  currentRaw: string | null | undefined,
  bestPolicyRaw: string | null | undefined,
  currentPolicyRaw: string | null | undefined
): string {
  const bestShares = replaySearchModeShares(bestRaw, bestPolicyRaw)
  const currentShares = replaySearchModeShares(currentRaw, currentPolicyRaw)
  const bestEnabled = replaySearchEnabledModes(bestPolicyRaw)
  const currentEnabled = replaySearchEnabledModes(currentPolicyRaw)
  if (!bestShares.size || !currentShares.size) return '-'
  const parts: string[] = []
  for (const mode of ['heuristic', 'xgboost']) {
    const bestModeEnabled = mode === 'heuristic' ? bestEnabled.heuristic : bestEnabled.xgboost
    const currentModeEnabled = mode === 'heuristic' ? currentEnabled.heuristic : currentEnabled.xgboost
    if (!bestModeEnabled && !currentModeEnabled) continue
    if (bestModeEnabled !== currentModeEnabled) {
      parts.push(`${modeLabel(mode)} ${bestModeEnabled ? 'on' : 'off'} vs ${currentModeEnabled ? 'on' : 'off'}`)
      continue
    }
    if (!bestShares.has(mode) && !currentShares.has(mode)) continue
    const driftPctPoints = ((bestShares.get(mode) || 0) - (currentShares.get(mode) || 0)) * 100
    const rounded = Math.round(driftPctPoints)
    const sign = rounded > 0 ? '+' : ''
    parts.push(`${modeLabel(mode)} ${sign}${rounded}pt`)
  }
  return parts.length ? parts.join(' | ') : '-'
}

function replaySearchCurrentModeEvidenceSummary(
  raw: string | null | undefined,
  policyRaw: string | null | undefined
): string {
  if (!raw) return '-'
  try {
    const parsed = JSON.parse(raw)
    const enabled = replaySearchEnabledModes(policyRaw)
    const rawSummary = parsed?.signal_mode_summary
    if (!rawSummary || typeof rawSummary !== 'object' || Array.isArray(rawSummary)) return '-'
    const parsedEntries = Object.entries(rawSummary as Record<string, unknown>)
      .map(([mode, value]) => {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return null
        const payload = value as Record<string, unknown>
        return {
          mode,
          acceptedCount: Number(payload.accepted_count || 0),
          resolvedCount: Number(payload.resolved_count || 0),
          totalPnlUsd: Number(payload.total_pnl_usd || 0),
          winRate: payload.win_rate == null ? null : Number(payload.win_rate)
        }
      })
      .filter((entry): entry is {mode: string; acceptedCount: number; resolvedCount: number; totalPnlUsd: number; winRate: number | null} => Boolean(entry))
      .filter((entry) => {
        if (entry.mode === 'heuristic') return enabled.heuristic
        if (entry.mode === 'xgboost') return enabled.xgboost
        return true
      })
    const entryByMode = new Map(parsedEntries.map((entry) => [entry.mode, entry]))
    const entries: Array<{mode: string; acceptedCount: number; resolvedCount: number; totalPnlUsd: number; winRate: number | null}> = []
    if (enabled.heuristic) {
      entries.push(entryByMode.get('heuristic') ?? {
        mode: 'heuristic',
        acceptedCount: 0,
        resolvedCount: 0,
        totalPnlUsd: 0,
        winRate: null
      })
    }
    if (enabled.xgboost) {
      entries.push(entryByMode.get('xgboost') ?? {
        mode: 'xgboost',
        acceptedCount: 0,
        resolvedCount: 0,
        totalPnlUsd: 0,
        winRate: null
      })
    }
    parsedEntries
      .filter((entry) => entry.mode !== 'heuristic' && entry.mode !== 'xgboost')
      .sort((left, right) => left.mode.localeCompare(right.mode))
      .forEach((entry) => {
        if (entry.acceptedCount > 0) entries.push(entry)
      })
    if (!entries.length) {
      const parts: string[] = []
      if (!enabled.heuristic) parts.push('Heuristic off')
      if (!enabled.xgboost) parts.push('XGBoost off')
      return parts.length ? parts.join(' | ') : '-'
    }
    const parts = entries
      .map((entry) => {
        const coverage = entry.acceptedCount > 0 ? formatPct(entry.resolvedCount / entry.acceptedCount, 0) : '0%'
        const rate = entry.winRate == null ? '-' : formatPct(entry.winRate, 0)
        return `${modeLabel(entry.mode)} ${formatCount(entry.resolvedCount)}r/${formatCount(entry.acceptedCount)}a ${coverage} ${rate} ${formatDollar(entry.totalPnlUsd)}`
      })
    if (!enabled.heuristic) parts.push('Heuristic off')
    if (!enabled.xgboost) parts.push('XGBoost off')
    return parts.join(' | ')
  } catch {
    return '-'
  }
}

function replaySearchEnabledModes(policyRaw: string | null | undefined): {heuristic: boolean; xgboost: boolean} {
  if (!policyRaw) return {heuristic: true, xgboost: true}
  try {
    const parsed = JSON.parse(policyRaw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {heuristic: true, xgboost: true}
    }
    const payload = parsed as Record<string, unknown>
    return {
      heuristic: payload.allow_heuristic == null ? true : Boolean(payload.allow_heuristic),
      xgboost: payload.allow_xgboost == null ? true : Boolean(payload.allow_xgboost)
    }
  } catch {
    return {heuristic: true, xgboost: true}
  }
}

function replaySearchModeFloorSummary(
  raw: string | null | undefined,
  policyRaw: string | null | undefined
): string {
  if (!raw) return 'none'
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return 'none'
    const payload = parsed as Record<string, unknown>
    const parts: string[] = []
    const enabled = replaySearchEnabledModes(policyRaw)
    const minHeuristicAccepted = Number(payload.min_heuristic_accepted_count || 0)
    const minXgboostAccepted = Number(payload.min_xgboost_accepted_count || 0)
    const minHeuristicResolved = Number(payload.min_heuristic_resolved_count || 0)
    const minXgboostResolved = Number(payload.min_xgboost_resolved_count || 0)
    const minHeuristicResolvedShare = Number(payload.min_heuristic_resolved_share || 0)
    const minXgboostResolvedShare = Number(payload.min_xgboost_resolved_share || 0)
    const minHeuristicWinRate = Number(payload.min_heuristic_win_rate || 0)
    const minXgboostWinRate = Number(payload.min_xgboost_win_rate || 0)
    const minHeuristicPnlUsd = Number(payload.min_heuristic_pnl_usd || 0)
    const minXgboostPnlUsd = Number(payload.min_xgboost_pnl_usd || 0)
    const minHeuristicWorstWindowPnlUsd = Number(payload.min_heuristic_worst_window_pnl_usd ?? -1_000_000_000)
    const minXgboostWorstWindowPnlUsd = Number(payload.min_xgboost_worst_window_pnl_usd ?? -1_000_000_000)
    const minHeuristicWorstWindowResolvedShare = Number(payload.min_heuristic_worst_window_resolved_share || 0)
    const minXgboostWorstWindowResolvedShare = Number(payload.min_xgboost_worst_window_resolved_share || 0)
    const minHeuristicPositiveWindows = Number(payload.min_heuristic_positive_windows || 0)
    const minXgboostPositiveWindows = Number(payload.min_xgboost_positive_windows || 0)
    const maxHeuristicInactiveWindows = Number(payload.max_heuristic_inactive_windows ?? -1)
    const maxXgboostInactiveWindows = Number(payload.max_xgboost_inactive_windows ?? -1)
    const maxHeuristicAcceptedShare = Number(payload.max_heuristic_accepted_share || 0)
    const minXgboostAcceptedShare = Number(payload.min_xgboost_accepted_share || 0)
    if (!enabled.heuristic) {
      parts.push('heur off')
    } else {
      if (minHeuristicAccepted > 0) parts.push(`heur >=${formatCount(minHeuristicAccepted)}`)
      if (minHeuristicResolved > 0) parts.push(`heur r>=${formatCount(minHeuristicResolved)}`)
      if (minHeuristicResolvedShare > 0) parts.push(`heur cov>=${formatPct(minHeuristicResolvedShare, 0)}`)
      if (minHeuristicWinRate > 0) parts.push(`heur wr>=${formatPct(minHeuristicWinRate, 0)}`)
      if (minHeuristicPnlUsd !== 0) parts.push(`heur pnl>=${formatDollar(minHeuristicPnlUsd)}`)
      if (minHeuristicWorstWindowPnlUsd > -999_999_999) parts.push(`heur worst>=${formatDollar(minHeuristicWorstWindowPnlUsd)}`)
      if (minHeuristicWorstWindowResolvedShare > 0) parts.push(`heur worst cov>=${formatPct(minHeuristicWorstWindowResolvedShare, 0)}`)
      if (minHeuristicPositiveWindows > 0) parts.push(`heur pos>=${formatCount(minHeuristicPositiveWindows)}`)
      if (maxHeuristicInactiveWindows >= 0) parts.push(`heur idle<=${formatCount(maxHeuristicInactiveWindows)}`)
      if (maxHeuristicAcceptedShare > 0) parts.push(`heur mix<=${formatPct(maxHeuristicAcceptedShare, 0)}`)
    }
    if (!enabled.xgboost) {
      parts.push('model off')
    } else {
      if (minXgboostAccepted > 0) parts.push(`model >=${formatCount(minXgboostAccepted)}`)
      if (minXgboostResolved > 0) parts.push(`model r>=${formatCount(minXgboostResolved)}`)
      if (minXgboostResolvedShare > 0) parts.push(`model cov>=${formatPct(minXgboostResolvedShare, 0)}`)
      if (minXgboostWinRate > 0) parts.push(`model wr>=${formatPct(minXgboostWinRate, 0)}`)
      if (minXgboostPnlUsd !== 0) parts.push(`model pnl>=${formatDollar(minXgboostPnlUsd)}`)
      if (minXgboostWorstWindowPnlUsd > -999_999_999) parts.push(`model worst>=${formatDollar(minXgboostWorstWindowPnlUsd)}`)
      if (minXgboostWorstWindowResolvedShare > 0) parts.push(`model worst cov>=${formatPct(minXgboostWorstWindowResolvedShare, 0)}`)
      if (minXgboostPositiveWindows > 0) parts.push(`model pos>=${formatCount(minXgboostPositiveWindows)}`)
      if (maxXgboostInactiveWindows >= 0) parts.push(`model idle<=${formatCount(maxXgboostInactiveWindows)}`)
      if (minXgboostAcceptedShare > 0) parts.push(`model mix>=${formatPct(minXgboostAcceptedShare, 0)}`)
    }
    return parts.length ? parts.join(', ') : 'none'
  } catch {
    return 'none'
  }
}

interface ReplaySearchPauseGuardSummary {
  summary: string
  hasActiveGuard: boolean
  currentShare: number | null
  bestShare: number | null
  overLimit: boolean
}

function replaySearchScoreWeightSummary(row: ReplaySearchSummaryRow | undefined): string {
  if (!row) return '-'
  const parts: string[] = []
  const pushIfActive = (label: string, value: number | null | undefined) => {
    const numeric = Number(value || 0)
    if (Math.abs(numeric) > 1e-9) parts.push(`${label} ${formatNumber(numeric, 2)}x`)
  }
  pushIfActive('dd', row.drawdown_penalty)
  pushIfActive('std', row.window_stddev_penalty)
  pushIfActive('worst', row.worst_window_penalty)
  pushIfActive('pause', row.pause_guard_penalty)
  pushIfActive('cov', row.resolved_share_penalty)
  pushIfActive('w-cov', row.worst_window_resolved_share_penalty)
  pushIfActive('m-cov', row.mode_resolved_share_penalty)
  pushIfActive('mw-cov', row.mode_worst_window_resolved_share_penalty)
  pushIfActive('mode', row.mode_loss_penalty)
  pushIfActive('idle', row.mode_inactivity_penalty)
  pushIfActive('wallet', row.wallet_concentration_penalty)
  pushIfActive('market', row.market_concentration_penalty)
  pushIfActive('band', row.entry_price_band_concentration_penalty)
  pushIfActive('hzn', row.time_to_close_band_concentration_penalty)
  return parts.length ? parts.join(' | ') : 'none'
}

function replaySearchScoreBreakdownSummary(raw: string | null | undefined): string {
  if (!raw) return '-'
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return '-'
    const rawBreakdown = (parsed as Record<string, unknown>).score_breakdown
    if (!rawBreakdown || typeof rawBreakdown !== 'object' || Array.isArray(rawBreakdown)) return '-'
    const breakdown = rawBreakdown as Record<string, unknown>
    const scoreUsd = Number(breakdown.score_usd || 0)
    const pnlUsd = Number(breakdown.pnl_usd || 0)
    const drawdownPenaltyUsd = Number(breakdown.drawdown_penalty_usd || 0)
    const windowStddevPenaltyUsd = Number(breakdown.window_stddev_penalty_usd || 0)
    const worstWindowPenaltyUsd = Number(breakdown.worst_window_penalty_usd || 0)
    const pauseGuardPenaltyUsd = Number(breakdown.pause_guard_penalty_usd || 0)
    const resolvedSharePenaltyUsd = Number(breakdown.resolved_share_penalty_usd || 0)
    const worstWindowResolvedSharePenaltyUsd = Number(breakdown.worst_window_resolved_share_penalty_usd || 0)
    const modeResolvedSharePenaltyUsd = Number(breakdown.mode_resolved_share_penalty_usd || 0)
    const modeWorstWindowResolvedSharePenaltyUsd = Number(breakdown.mode_worst_window_resolved_share_penalty_usd || 0)
    const modeLossPenaltyUsd = Number(breakdown.mode_loss_penalty_usd || 0)
    const modeInactivityPenaltyUsd = Number(breakdown.mode_inactivity_penalty_usd || 0)
    const walletConcentrationPenaltyUsd = Number(breakdown.wallet_concentration_penalty_usd || 0)
    const marketConcentrationPenaltyUsd = Number(breakdown.market_concentration_penalty_usd || 0)
    const entryPriceBandConcentrationPenaltyUsd = Number(breakdown.entry_price_band_concentration_penalty_usd || 0)
    const timeToCloseBandConcentrationPenaltyUsd = Number(breakdown.time_to_close_band_concentration_penalty_usd || 0)
    const parts = [
      `${formatNumber(scoreUsd, 2)} = ${formatDollar(pnlUsd)}`,
      `dd ${formatDollar(-drawdownPenaltyUsd)}`
    ]
    if (Math.abs(windowStddevPenaltyUsd) > 1e-9) parts.push(`std ${formatDollar(-windowStddevPenaltyUsd)}`)
    if (Math.abs(worstWindowPenaltyUsd) > 1e-9) parts.push(`worst ${formatDollar(-worstWindowPenaltyUsd)}`)
    if (Math.abs(pauseGuardPenaltyUsd) > 1e-9) parts.push(`pause ${formatDollar(-pauseGuardPenaltyUsd)}`)
    if (Math.abs(resolvedSharePenaltyUsd) > 1e-9) parts.push(`cov ${formatDollar(-resolvedSharePenaltyUsd)}`)
    if (Math.abs(worstWindowResolvedSharePenaltyUsd) > 1e-9) parts.push(`w-cov ${formatDollar(-worstWindowResolvedSharePenaltyUsd)}`)
    if (Math.abs(modeResolvedSharePenaltyUsd) > 1e-9) parts.push(`m-cov ${formatDollar(-modeResolvedSharePenaltyUsd)}`)
    if (Math.abs(modeWorstWindowResolvedSharePenaltyUsd) > 1e-9) parts.push(`mw-cov ${formatDollar(-modeWorstWindowResolvedSharePenaltyUsd)}`)
    if (Math.abs(modeLossPenaltyUsd) > 1e-9) parts.push(`mode ${formatDollar(-modeLossPenaltyUsd)}`)
    if (Math.abs(modeInactivityPenaltyUsd) > 1e-9) parts.push(`idle ${formatDollar(-modeInactivityPenaltyUsd)}`)
    if (Math.abs(walletConcentrationPenaltyUsd) > 1e-9) parts.push(`wallet ${formatDollar(-walletConcentrationPenaltyUsd)}`)
    if (Math.abs(marketConcentrationPenaltyUsd) > 1e-9) parts.push(`market ${formatDollar(-marketConcentrationPenaltyUsd)}`)
    if (Math.abs(entryPriceBandConcentrationPenaltyUsd) > 1e-9) parts.push(`band ${formatDollar(-entryPriceBandConcentrationPenaltyUsd)}`)
    if (Math.abs(timeToCloseBandConcentrationPenaltyUsd) > 1e-9) parts.push(`hzn ${formatDollar(-timeToCloseBandConcentrationPenaltyUsd)}`)
    return parts.join(' | ')
  } catch {
    return '-'
  }
}

function replaySearchScoreDriftSummary(
  bestRaw: string | null | undefined,
  currentRaw: string | null | undefined
): string {
  if (!bestRaw || !currentRaw) return '-'
  const parse = (raw: string): Record<string, number> | null => {
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
      const rawBreakdown = (parsed as Record<string, unknown>).score_breakdown
      if (!rawBreakdown || typeof rawBreakdown !== 'object' || Array.isArray(rawBreakdown)) return null
      const breakdown = rawBreakdown as Record<string, unknown>
      return {
        score_usd: Number(breakdown.score_usd || 0),
        pnl_usd: Number(breakdown.pnl_usd || 0),
        drawdown_penalty_usd: Number(breakdown.drawdown_penalty_usd || 0),
        window_stddev_penalty_usd: Number(breakdown.window_stddev_penalty_usd || 0),
        worst_window_penalty_usd: Number(breakdown.worst_window_penalty_usd || 0),
        pause_guard_penalty_usd: Number(breakdown.pause_guard_penalty_usd || 0),
        resolved_share_penalty_usd: Number(breakdown.resolved_share_penalty_usd || 0),
        worst_window_resolved_share_penalty_usd: Number(breakdown.worst_window_resolved_share_penalty_usd || 0),
        mode_resolved_share_penalty_usd: Number(breakdown.mode_resolved_share_penalty_usd || 0),
        mode_worst_window_resolved_share_penalty_usd: Number(breakdown.mode_worst_window_resolved_share_penalty_usd || 0),
        mode_loss_penalty_usd: Number(breakdown.mode_loss_penalty_usd || 0),
        mode_inactivity_penalty_usd: Number(breakdown.mode_inactivity_penalty_usd || 0),
        wallet_concentration_penalty_usd: Number(breakdown.wallet_concentration_penalty_usd || 0),
        market_concentration_penalty_usd: Number(breakdown.market_concentration_penalty_usd || 0),
        entry_price_band_concentration_penalty_usd: Number(breakdown.entry_price_band_concentration_penalty_usd || 0),
        time_to_close_band_concentration_penalty_usd: Number(breakdown.time_to_close_band_concentration_penalty_usd || 0)
      }
    } catch {
      return null
    }
  }

  const best = parse(bestRaw)
  const current = parse(currentRaw)
  if (!best || !current) return '-'

  const scoreDelta = best.score_usd - current.score_usd
  const pnlDelta = best.pnl_usd - current.pnl_usd
  const drawdownDelta = current.drawdown_penalty_usd - best.drawdown_penalty_usd
  const stddevDelta = current.window_stddev_penalty_usd - best.window_stddev_penalty_usd
  const worstDelta = current.worst_window_penalty_usd - best.worst_window_penalty_usd
  const pauseDelta = current.pause_guard_penalty_usd - best.pause_guard_penalty_usd
  const coverageDelta = current.resolved_share_penalty_usd - best.resolved_share_penalty_usd
  const worstCoverageDelta = current.worst_window_resolved_share_penalty_usd - best.worst_window_resolved_share_penalty_usd
  const modeCoverageDelta = current.mode_resolved_share_penalty_usd - best.mode_resolved_share_penalty_usd
  const modeWorstCoverageDelta = current.mode_worst_window_resolved_share_penalty_usd - best.mode_worst_window_resolved_share_penalty_usd
  const modeDelta = current.mode_loss_penalty_usd - best.mode_loss_penalty_usd
  const inactivityDelta = current.mode_inactivity_penalty_usd - best.mode_inactivity_penalty_usd
  const walletDelta = current.wallet_concentration_penalty_usd - best.wallet_concentration_penalty_usd
  const marketDelta = current.market_concentration_penalty_usd - best.market_concentration_penalty_usd
  const entryBandDelta = current.entry_price_band_concentration_penalty_usd - best.entry_price_band_concentration_penalty_usd
  const horizonDelta = current.time_to_close_band_concentration_penalty_usd - best.time_to_close_band_concentration_penalty_usd
  const parts = [
    `${formatDollar(scoreDelta)} = pnl ${formatDollar(pnlDelta)}`,
    `dd ${formatDollar(drawdownDelta)}`
  ]
  if (Math.abs(stddevDelta) > 1e-9) parts.push(`std ${formatDollar(stddevDelta)}`)
  if (Math.abs(worstDelta) > 1e-9) parts.push(`worst ${formatDollar(worstDelta)}`)
  if (Math.abs(pauseDelta) > 1e-9) parts.push(`pause ${formatDollar(pauseDelta)}`)
  if (Math.abs(coverageDelta) > 1e-9) parts.push(`cov ${formatDollar(coverageDelta)}`)
  if (Math.abs(worstCoverageDelta) > 1e-9) parts.push(`w-cov ${formatDollar(worstCoverageDelta)}`)
  if (Math.abs(modeCoverageDelta) > 1e-9) parts.push(`m-cov ${formatDollar(modeCoverageDelta)}`)
  if (Math.abs(modeWorstCoverageDelta) > 1e-9) parts.push(`mw-cov ${formatDollar(modeWorstCoverageDelta)}`)
  if (Math.abs(modeDelta) > 1e-9) parts.push(`mode ${formatDollar(modeDelta)}`)
  if (Math.abs(inactivityDelta) > 1e-9) parts.push(`idle ${formatDollar(inactivityDelta)}`)
  if (Math.abs(walletDelta) > 1e-9) parts.push(`wallet ${formatDollar(walletDelta)}`)
  if (Math.abs(marketDelta) > 1e-9) parts.push(`market ${formatDollar(marketDelta)}`)
  if (Math.abs(entryBandDelta) > 1e-9) parts.push(`band ${formatDollar(entryBandDelta)}`)
  if (Math.abs(horizonDelta) > 1e-9) parts.push(`hzn ${formatDollar(horizonDelta)}`)
  return parts.join(' | ')
}

interface ReplaySearchTraderConcentrationSummary {
  summary: string
  hasActiveGuard: boolean
  overLimit: boolean
}

function replaySearchTraderConcentrationSummary(
  bestRaw: string | null | undefined,
  currentRaw: string | null | undefined,
  constraintsRaw: string | null | undefined,
  penalty: number | null | undefined
): ReplaySearchTraderConcentrationSummary {
  const parse = (raw: string | null | undefined): {acceptedShare: number | null; absPnlShare: number | null} => {
    if (!raw) return {acceptedShare: null, absPnlShare: null}
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {acceptedShare: null, absPnlShare: null}
      const concentration = (parsed as Record<string, unknown>).trader_concentration
      if (!concentration || typeof concentration !== 'object' || Array.isArray(concentration)) {
        return {acceptedShare: null, absPnlShare: null}
      }
      const payload = concentration as Record<string, unknown>
      return {
        acceptedShare: Number(payload.top_accepted_share || 0),
        absPnlShare: Number(payload.top_abs_pnl_share || 0)
      }
    } catch {
      return {acceptedShare: null, absPnlShare: null}
    }
  }

  const best = parse(bestRaw)
  const current = parse(currentRaw)
  const limits = (() => {
    if (!constraintsRaw) return {accepted: 0, pnl: 0}
    try {
      const parsed = JSON.parse(constraintsRaw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {accepted: 0, pnl: 0}
      return {
        accepted: Number((parsed as Record<string, unknown>).max_top_trader_accepted_share || 0),
        pnl: Number((parsed as Record<string, unknown>).max_top_trader_abs_pnl_share || 0)
      }
    } catch {
      return {accepted: 0, pnl: 0}
    }
  })()
  const hasActiveGuard = limits.accepted > 0 || limits.pnl > 0
  if (best.acceptedShare == null && current.acceptedShare == null && !hasActiveGuard) {
    return {summary: '-', hasActiveGuard: false, overLimit: false}
  }
  const parts: string[] = []
  if (best.acceptedShare != null || best.absPnlShare != null) {
    parts.push(`best n ${formatPct(best.acceptedShare, 0)} pnl ${formatPct(best.absPnlShare, 0)}`)
  }
  if (current.acceptedShare != null || current.absPnlShare != null) {
    parts.push(`cur n ${formatPct(current.acceptedShare, 0)} pnl ${formatPct(current.absPnlShare, 0)}`)
  }
  if (limits.accepted > 0 || limits.pnl > 0) {
    parts.push(`max n ${formatPct(limits.accepted, 0)} pnl ${formatPct(limits.pnl, 0)}`)
  }
  const resolvedPenalty = Math.max(Number(penalty || 0), 0)
  if (resolvedPenalty > 0) parts.push(`pen ${resolvedPenalty.toFixed(2)}x`)
  const overLimit =
    (limits.accepted > 0 && ((best.acceptedShare ?? 0) > limits.accepted || (current.acceptedShare ?? 0) > limits.accepted))
    || (limits.pnl > 0 && ((best.absPnlShare ?? 0) > limits.pnl || (current.absPnlShare ?? 0) > limits.pnl))
  return {
    summary: parts.join(' | ') || '-',
    hasActiveGuard,
    overLimit
  }
}

interface ReplaySearchMarketConcentrationSummary {
  summary: string
  hasActiveGuard: boolean
  overLimit: boolean
}

function replaySearchMarketConcentrationSummary(
  bestRaw: string | null | undefined,
  currentRaw: string | null | undefined,
  constraintsRaw: string | null | undefined,
  penalty: number | null | undefined
): ReplaySearchMarketConcentrationSummary {
  const parse = (raw: string | null | undefined): {acceptedShare: number | null; absPnlShare: number | null} => {
    if (!raw) return {acceptedShare: null, absPnlShare: null}
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {acceptedShare: null, absPnlShare: null}
      const concentration = (parsed as Record<string, unknown>).market_concentration
      if (!concentration || typeof concentration !== 'object' || Array.isArray(concentration)) {
        return {acceptedShare: null, absPnlShare: null}
      }
      const payload = concentration as Record<string, unknown>
      return {
        acceptedShare: Number(payload.top_accepted_share || 0),
        absPnlShare: Number(payload.top_abs_pnl_share || 0)
      }
    } catch {
      return {acceptedShare: null, absPnlShare: null}
    }
  }

  const best = parse(bestRaw)
  const current = parse(currentRaw)
  const limits = (() => {
    if (!constraintsRaw) return {accepted: 0, pnl: 0}
    try {
      const parsed = JSON.parse(constraintsRaw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {accepted: 0, pnl: 0}
      return {
        accepted: Number((parsed as Record<string, unknown>).max_top_market_accepted_share || 0),
        pnl: Number((parsed as Record<string, unknown>).max_top_market_abs_pnl_share || 0)
      }
    } catch {
      return {accepted: 0, pnl: 0}
    }
  })()
  const hasActiveGuard = limits.accepted > 0 || limits.pnl > 0
  if (best.acceptedShare == null && current.acceptedShare == null && !hasActiveGuard) {
    return {summary: '-', hasActiveGuard: false, overLimit: false}
  }
  const parts: string[] = []
  if (best.acceptedShare != null || best.absPnlShare != null) {
    parts.push(`best n ${formatPct(best.acceptedShare, 0)} pnl ${formatPct(best.absPnlShare, 0)}`)
  }
  if (current.acceptedShare != null || current.absPnlShare != null) {
    parts.push(`cur n ${formatPct(current.acceptedShare, 0)} pnl ${formatPct(current.absPnlShare, 0)}`)
  }
  if (limits.accepted > 0 || limits.pnl > 0) {
    parts.push(`max n ${formatPct(limits.accepted, 0)} pnl ${formatPct(limits.pnl, 0)}`)
  }
  const resolvedPenalty = Math.max(Number(penalty || 0), 0)
  if (resolvedPenalty > 0) parts.push(`pen ${resolvedPenalty.toFixed(2)}x`)
  const overLimit =
    (limits.accepted > 0 && ((best.acceptedShare ?? 0) > limits.accepted || (current.acceptedShare ?? 0) > limits.accepted))
    || (limits.pnl > 0 && ((best.absPnlShare ?? 0) > limits.pnl || (current.absPnlShare ?? 0) > limits.pnl))
  return {
    summary: parts.join(' | ') || '-',
    hasActiveGuard,
    overLimit
  }
}

function replaySearchEntryPriceBandConcentrationSummary(
  bestRaw: string | null | undefined,
  currentRaw: string | null | undefined,
  constraintsRaw: string | null | undefined,
  penalty: number | null | undefined
): ReplaySearchTraderConcentrationSummary {
  const parse = (raw: string | null | undefined): {acceptedShare: number | null; absPnlShare: number | null} => {
    if (!raw) return {acceptedShare: null, absPnlShare: null}
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {acceptedShare: null, absPnlShare: null}
      const concentration = (parsed as Record<string, unknown>).entry_price_band_concentration
      if (!concentration || typeof concentration !== 'object' || Array.isArray(concentration)) {
        return {acceptedShare: null, absPnlShare: null}
      }
      const payload = concentration as Record<string, unknown>
      return {
        acceptedShare: Number(payload.top_accepted_share || 0),
        absPnlShare: Number(payload.top_abs_pnl_share || 0)
      }
    } catch {
      return {acceptedShare: null, absPnlShare: null}
    }
  }

  const best = parse(bestRaw)
  const current = parse(currentRaw)
  const limits = (() => {
    if (!constraintsRaw) return {accepted: 0, pnl: 0}
    try {
      const parsed = JSON.parse(constraintsRaw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {accepted: 0, pnl: 0}
      return {
        accepted: Number((parsed as Record<string, unknown>).max_top_entry_price_band_accepted_share || 0),
        pnl: Number((parsed as Record<string, unknown>).max_top_entry_price_band_abs_pnl_share || 0)
      }
    } catch {
      return {accepted: 0, pnl: 0}
    }
  })()
  const hasActiveGuard = limits.accepted > 0 || limits.pnl > 0
  if (best.acceptedShare == null && current.acceptedShare == null && !hasActiveGuard) {
    return {summary: '-', hasActiveGuard: false, overLimit: false}
  }
  const parts: string[] = []
  if (best.acceptedShare != null || best.absPnlShare != null) {
    parts.push(`best n ${formatPct(best.acceptedShare, 0)} pnl ${formatPct(best.absPnlShare, 0)}`)
  }
  if (current.acceptedShare != null || current.absPnlShare != null) {
    parts.push(`cur n ${formatPct(current.acceptedShare, 0)} pnl ${formatPct(current.absPnlShare, 0)}`)
  }
  if (limits.accepted > 0 || limits.pnl > 0) {
    parts.push(`max n ${formatPct(limits.accepted, 0)} pnl ${formatPct(limits.pnl, 0)}`)
  }
  const resolvedPenalty = Math.max(Number(penalty || 0), 0)
  if (resolvedPenalty > 0) parts.push(`pen ${resolvedPenalty.toFixed(2)}x`)
  const overLimit =
    (limits.accepted > 0 && ((best.acceptedShare ?? 0) > limits.accepted || (current.acceptedShare ?? 0) > limits.accepted))
    || (limits.pnl > 0 && ((best.absPnlShare ?? 0) > limits.pnl || (current.absPnlShare ?? 0) > limits.pnl))
  return {
    summary: parts.join(' | ') || '-',
    hasActiveGuard,
    overLimit
  }
}

function replaySearchTimeToCloseBandConcentrationSummary(
  bestRaw: string | null | undefined,
  currentRaw: string | null | undefined,
  constraintsRaw: string | null | undefined,
  penalty: number | null | undefined
): ReplaySearchTraderConcentrationSummary {
  const parse = (raw: string | null | undefined): {acceptedShare: number | null; absPnlShare: number | null} => {
    if (!raw) return {acceptedShare: null, absPnlShare: null}
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {acceptedShare: null, absPnlShare: null}
      const concentration = (parsed as Record<string, unknown>).time_to_close_band_concentration
      if (!concentration || typeof concentration !== 'object' || Array.isArray(concentration)) {
        return {acceptedShare: null, absPnlShare: null}
      }
      const payload = concentration as Record<string, unknown>
      return {
        acceptedShare: Number(payload.top_accepted_share || 0),
        absPnlShare: Number(payload.top_abs_pnl_share || 0)
      }
    } catch {
      return {acceptedShare: null, absPnlShare: null}
    }
  }

  const best = parse(bestRaw)
  const current = parse(currentRaw)
  const limits = (() => {
    if (!constraintsRaw) return {accepted: 0, pnl: 0}
    try {
      const parsed = JSON.parse(constraintsRaw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {accepted: 0, pnl: 0}
      return {
        accepted: Number((parsed as Record<string, unknown>).max_top_time_to_close_band_accepted_share || 0),
        pnl: Number((parsed as Record<string, unknown>).max_top_time_to_close_band_abs_pnl_share || 0)
      }
    } catch {
      return {accepted: 0, pnl: 0}
    }
  })()
  const hasActiveGuard = limits.accepted > 0 || limits.pnl > 0
  if (best.acceptedShare == null && current.acceptedShare == null && !hasActiveGuard) {
    return {summary: '-', hasActiveGuard: false, overLimit: false}
  }
  const parts: string[] = []
  if (best.acceptedShare != null || best.absPnlShare != null) {
    parts.push(`best n ${formatPct(best.acceptedShare, 0)} pnl ${formatPct(best.absPnlShare, 0)}`)
  }
  if (current.acceptedShare != null || current.absPnlShare != null) {
    parts.push(`cur n ${formatPct(current.acceptedShare, 0)} pnl ${formatPct(current.absPnlShare, 0)}`)
  }
  if (limits.accepted > 0 || limits.pnl > 0) {
    parts.push(`max n ${formatPct(limits.accepted, 0)} pnl ${formatPct(limits.pnl, 0)}`)
  }
  const resolvedPenalty = Math.max(Number(penalty || 0), 0)
  if (resolvedPenalty > 0) parts.push(`pen ${resolvedPenalty.toFixed(2)}x`)
  const overLimit =
    (limits.accepted > 0 && ((best.acceptedShare ?? 0) > limits.accepted || (current.acceptedShare ?? 0) > limits.accepted))
    || (limits.pnl > 0 && ((best.absPnlShare ?? 0) > limits.pnl || (current.absPnlShare ?? 0) > limits.pnl))
  return {
    summary: parts.join(' | ') || '-',
    hasActiveGuard,
    overLimit
  }
}

function replaySearchPauseGuardSummary(
  bestRaw: string | null | undefined,
  currentRaw: string | null | undefined,
  constraintsRaw: string | null | undefined,
  pauseGuardPenalty: number | null | undefined
): ReplaySearchPauseGuardSummary {
  const parseShare = (raw: string | null | undefined): number | null => {
    if (!raw) return null
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
      const payload = parsed as Record<string, unknown>
      const tradeCount = Number(payload.trade_count || 0)
      if (tradeCount <= 0) return 0
      const rawRejectSummary = payload.reject_reason_summary
      if (!rawRejectSummary || typeof rawRejectSummary !== 'object' || Array.isArray(rawRejectSummary)) return 0
      const rejectSummary = rawRejectSummary as Record<string, unknown>
      const pauseCount = Number(rejectSummary.daily_loss_guard || 0) + Number(rejectSummary.live_drawdown_guard || 0)
      return pauseCount / tradeCount
    } catch {
      return null
    }
  }

  const bestShare = parseShare(bestRaw)
  const currentShare = parseShare(currentRaw)
  const maxShare = (() => {
    if (!constraintsRaw) return 0
    try {
      const parsed = JSON.parse(constraintsRaw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return 0
      return Number((parsed as Record<string, unknown>).max_pause_guard_reject_share || 0)
    } catch {
      return 0
    }
  })()
  const resolvedPauseGuardPenalty = Math.max(Number(pauseGuardPenalty || 0), 0)
  const formatPenaltyCost = (raw: string | null | undefined, share: number | null): string | null => {
    if (resolvedPauseGuardPenalty <= 0 || share == null || !raw) return null
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
      const initialBankrollUsd = Number((parsed as Record<string, unknown>).initial_bankroll_usd || 0)
      if (initialBankrollUsd <= 0) return null
      return formatDollar(-(initialBankrollUsd * resolvedPauseGuardPenalty * share))
    } catch {
      return null
    }
  }

  if (bestShare == null && currentShare == null && maxShare <= 0 && resolvedPauseGuardPenalty <= 0) {
    return {summary: '-', hasActiveGuard: false, currentShare: null, bestShare: null, overLimit: false}
  }

  const parts: string[] = []
  if (bestShare != null) {
    const penaltyCost = formatPenaltyCost(bestRaw, bestShare)
    parts.push(`best ${formatPct(bestShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`)
  }
  if (currentShare != null) {
    const penaltyCost = formatPenaltyCost(currentRaw, currentShare)
    parts.push(`cur ${formatPct(currentShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`)
  }
  if (maxShare > 0) parts.push(`max ${formatPct(maxShare, 0)}`)
  if (resolvedPauseGuardPenalty > 0) parts.push(`pen ${resolvedPauseGuardPenalty.toFixed(2)}x`)
  return {
    summary: parts.length ? parts.join(' | ') : '-',
    hasActiveGuard: maxShare > 0,
    currentShare,
    bestShare,
    overLimit: (bestShare != null && bestShare > maxShare && maxShare > 0) || (currentShare != null && currentShare > maxShare && maxShare > 0)
  }
}

function replaySearchModePenaltySummary(row: ReplaySearchSummaryRow | undefined): string {
  if (!row) return '-'
  const parts: string[] = []
  const modeResolvedSharePenalty = Math.max(Number(row.mode_resolved_share_penalty || 0), 0)
  const modeWorstWindowResolvedSharePenalty = Math.max(Number(row.mode_worst_window_resolved_share_penalty || 0), 0)
  const modeLossPenalty = Math.max(Number(row.mode_loss_penalty || 0), 0)
  const modeInactivityPenalty = Math.max(Number(row.mode_inactivity_penalty || 0), 0)
  if (modeResolvedSharePenalty > 0) parts.push(`cov ${modeResolvedSharePenalty.toFixed(2)}x`)
  if (modeWorstWindowResolvedSharePenalty > 0) parts.push(`w-cov ${modeWorstWindowResolvedSharePenalty.toFixed(2)}x`)
  if (modeLossPenalty > 0) parts.push(`loss ${modeLossPenalty.toFixed(2)}x`)
  if (modeInactivityPenalty > 0) parts.push(`idle ${modeInactivityPenalty.toFixed(2)}x`)
  return parts.length ? parts.join(' | ') : 'none'
}

interface ReplaySearchModeRiskSummary {
  summary: string
  breachCount: number
  hasActiveGuard: boolean
}

function replaySearchCurrentModeRiskSummary(
  currentRaw: string | null | undefined,
  constraintsRaw: string | null | undefined,
  policyRaw: string | null | undefined
): ReplaySearchModeRiskSummary {
  if (!currentRaw || !constraintsRaw) return {summary: '-', breachCount: 0, hasActiveGuard: false}
  try {
    const currentParsed = JSON.parse(currentRaw)
    const constraintsParsed = JSON.parse(constraintsRaw)
    const enabled = replaySearchEnabledModes(policyRaw)
    const rawSummary = currentParsed?.signal_mode_summary
    if (!rawSummary || typeof rawSummary !== 'object' || Array.isArray(rawSummary)) {
      return {summary: '-', breachCount: 0, hasActiveGuard: false}
    }
    const constraints = constraintsParsed && typeof constraintsParsed === 'object' && !Array.isArray(constraintsParsed)
      ? constraintsParsed as Record<string, unknown>
      : {}
    const summary = rawSummary as Record<string, unknown>
    const totalAccepted = Object.values(summary).reduce((sum: number, rawValue: unknown) => {
      if (!rawValue || typeof rawValue !== 'object' || Array.isArray(rawValue)) return sum
      return sum + Number((rawValue as Record<string, unknown>).accepted_count || 0)
    }, 0)

    const breaches: string[] = []
    let hasActiveGuard = false
    const sentinelWorstWindow = -999_999_999
    for (const [mode, prefix, shareKey, shareDirection] of [
      ['heuristic', 'heur', 'max_heuristic_accepted_share', 'max'],
      ['xgboost', 'model', 'min_xgboost_accepted_share', 'min']
    ] as const) {
      if ((mode === 'heuristic' && !enabled.heuristic) || (mode === 'xgboost' && !enabled.xgboost)) continue
      const rawValue = summary[mode]
      const payload = rawValue && typeof rawValue === 'object' && !Array.isArray(rawValue)
        ? rawValue as Record<string, unknown>
        : {}
      const acceptedCount = Number(payload.accepted_count || 0)
      const resolvedCount = Number(payload.resolved_count || 0)
      const acceptedShare = totalAccepted > 0 ? acceptedCount / totalAccepted : 0
      const resolvedShare = acceptedCount > 0 ? resolvedCount / acceptedCount : 0
      const rawWinRate = payload.win_rate
      const winRate = rawWinRate == null ? null : Number(rawWinRate)
      const totalPnlUsd = Number(payload.total_pnl_usd || 0)
      const rawWorstWindowPnlUsd = payload.worst_window_pnl_usd
      const worstWindowPnlUsd = rawWorstWindowPnlUsd == null ? totalPnlUsd : Number(rawWorstWindowPnlUsd)
      const worstWindowResolvedShare = Number(
        payload.worst_active_window_resolved_share
        ?? payload.worst_window_resolved_share
        ?? resolvedShare
      )
      const inactiveWindowCount = Number(payload.inactive_window_count || 0)

      const minAccepted = Number(constraints[`min_${mode}_accepted_count`] || 0)
      const minResolved = Number(constraints[`min_${mode}_resolved_count`] || 0)
      const minResolvedShare = Number(constraints[`min_${mode}_resolved_share`] || 0)
      const minWinRate = Number(constraints[`min_${mode}_win_rate`] || 0)
      const minPnlUsd = Number(constraints[`min_${mode}_pnl_usd`] || 0)
      const minWorstWindowPnlUsd = Number(constraints[`min_${mode}_worst_window_pnl_usd`] ?? sentinelWorstWindow)
      const minWorstWindowResolvedShare = Number(constraints[`min_${mode}_worst_window_resolved_share`] || 0)
      const maxInactiveWindows = Number(constraints[`max_${mode}_inactive_windows`] ?? -1)
      const shareLimit = Number(constraints[shareKey] || 0)

      if (minAccepted > 0) {
        hasActiveGuard = true
        if (acceptedCount < minAccepted) breaches.push(`${prefix} n ${formatCount(acceptedCount)}<${formatCount(minAccepted)}`)
      }
      if (minResolved > 0) {
        hasActiveGuard = true
        if (resolvedCount < minResolved) breaches.push(`${prefix} r ${formatCount(resolvedCount)}<${formatCount(minResolved)}`)
      }
      if (minResolvedShare > 0) {
        hasActiveGuard = true
        if (resolvedShare < minResolvedShare) breaches.push(`${prefix} cov ${formatPct(resolvedShare, 0)}<${formatPct(minResolvedShare, 0)}`)
      }
      if (minWinRate > 0) {
        hasActiveGuard = true
        if (winRate == null || winRate < minWinRate) breaches.push(`${prefix} wr ${formatPct(winRate, 0)}<${formatPct(minWinRate, 0)}`)
      }
      if (minPnlUsd !== 0) {
        hasActiveGuard = true
        if (totalPnlUsd < minPnlUsd) breaches.push(`${prefix} pnl ${formatDollar(totalPnlUsd)}<${formatDollar(minPnlUsd)}`)
      }
      if (minWorstWindowPnlUsd > sentinelWorstWindow) {
        hasActiveGuard = true
        if (worstWindowPnlUsd < minWorstWindowPnlUsd) breaches.push(`${prefix} worst ${formatDollar(worstWindowPnlUsd)}<${formatDollar(minWorstWindowPnlUsd)}`)
      }
      if (minWorstWindowResolvedShare > 0) {
        hasActiveGuard = true
        if (worstWindowResolvedShare < minWorstWindowResolvedShare) breaches.push(`${prefix} worst cov ${formatPct(worstWindowResolvedShare, 0)}<${formatPct(minWorstWindowResolvedShare, 0)}`)
      }
      if (maxInactiveWindows >= 0) {
        hasActiveGuard = true
        if (inactiveWindowCount > maxInactiveWindows) breaches.push(`${prefix} idle ${formatCount(inactiveWindowCount)}>${formatCount(maxInactiveWindows)}`)
      }
      if (shareLimit > 0) {
        hasActiveGuard = true
        if (shareDirection === 'max' && acceptedShare > shareLimit) {
          breaches.push(`${prefix} mix ${formatPct(acceptedShare, 0)}>${formatPct(shareLimit, 0)}`)
        }
        if (shareDirection === 'min' && acceptedShare < shareLimit) {
          breaches.push(`${prefix} mix ${formatPct(acceptedShare, 0)}<${formatPct(shareLimit, 0)}`)
        }
      }
    }
    if (!hasActiveGuard) return {summary: 'none', breachCount: 0, hasActiveGuard: false}
    if (!breaches.length) return {summary: 'clear', breachCount: 0, hasActiveGuard: true}
    return {
      summary: breaches.length > 4
        ? `${breaches.slice(0, 4).join(' | ')} | +${formatCount(breaches.length - 4)} more`
        : breaches.join(' | '),
      breachCount: breaches.length,
      hasActiveGuard: true
    }
  } catch {
    return {summary: '-', breachCount: 0, hasActiveGuard: false}
  }
}

function replaySearchFailureSummary(raw: string | null | undefined, feasible: number | null | undefined): string {
  if (Number(feasible || 0) > 0) return '-'
  if (!raw) return 'unknown'
  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return 'unknown'
    const failures = parsed
      .map((value) => String(value || '').trim())
      .filter(Boolean)
    if (!failures.length) return 'unknown'
    const labels = failures.map((failure) => {
      switch (failure) {
        case 'accepted_count':
          return 'accepted'
        case 'resolved_count':
          return 'resolved'
        case 'resolved_share':
          return 'coverage'
        case 'win_rate':
          return 'win rate'
        case 'total_pnl_usd':
          return 'pnl'
        case 'max_drawdown_pct':
          return 'drawdown'
        case 'worst_window_pnl_usd':
          return 'worst pnl'
        case 'worst_window_resolved_share':
          return 'worst cov'
        case 'worst_window_drawdown_pct':
          return 'worst dd'
        case 'positive_window_count':
          return 'positive windows'
        case 'pause_guard_reject_share':
          return 'pause share'
        case 'top_trader_accepted_share':
          return 'wallet n share'
        case 'top_trader_abs_pnl_share':
          return 'wallet pnl share'
        case 'top_market_accepted_share':
          return 'market n share'
        case 'top_market_abs_pnl_share':
          return 'market pnl share'
        case 'top_entry_price_band_accepted_share':
          return 'entry n share'
        case 'top_entry_price_band_abs_pnl_share':
          return 'entry pnl share'
        case 'top_time_to_close_band_accepted_share':
          return 'horizon n share'
        case 'top_time_to_close_band_abs_pnl_share':
          return 'horizon pnl share'
        case 'heuristic_inactive_window_count':
          return 'heur idle'
        case 'xgboost_inactive_window_count':
          return 'model idle'
        default:
          return failure.replaceAll('_', ' ')
      }
    })
    return labels.length > 4
      ? `${labels.slice(0, 4).join(' | ')} | +${formatCount(labels.length - 4)} more`
      : labels.join(' | ')
  } catch {
    return 'unknown'
  }
}

interface ReplaySearchHeadroomSummary {
  summary: string
  hasActiveGuard: boolean
  closestMarginRatio: number | null
  hasFailure: boolean
}

interface ReplaySearchHeadroomCandidate {
  group: 'global' | 'heuristic' | 'xgboost'
  label: string
  margin: number
  normalizedMargin: number
}

function replayHeadroomPctPoints(value: number): string {
  const points = value * 100
  if (!Number.isFinite(points)) return '-'
  const absPoints = Math.abs(points)
  const rounded = absPoints >= 10 ? Math.round(points) : Math.round(points * 10) / 10
  const sign = rounded > 0 ? '+' : ''
  return `${sign}${rounded}pt`
}

function replayHeadroomCount(value: number): string {
  if (!Number.isFinite(value)) return '-'
  const rounded = Math.round(value)
  const sign = rounded > 0 ? '+' : ''
  return `${sign}${rounded}`
}

function replaySearchHeadroomSummary(
  resultRaw: string | null | undefined,
  constraintsRaw: string | null | undefined,
  policyRaw: string | null | undefined
): ReplaySearchHeadroomSummary {
  if (!resultRaw || !constraintsRaw) return {summary: '-', hasActiveGuard: false, closestMarginRatio: null, hasFailure: false}
  try {
    const resultParsed = JSON.parse(resultRaw)
    const constraintsParsed = JSON.parse(constraintsRaw)
    const enabled = replaySearchEnabledModes(policyRaw)
    const constraints = constraintsParsed && typeof constraintsParsed === 'object' && !Array.isArray(constraintsParsed)
      ? constraintsParsed as Record<string, unknown>
      : {}
    if (!resultParsed || typeof resultParsed !== 'object' || Array.isArray(resultParsed)) {
      return {summary: '-', hasActiveGuard: false, closestMarginRatio: null, hasFailure: false}
    }

    const rawSignalModeSummary = resultParsed.signal_mode_summary
    const signalModeSummary = rawSignalModeSummary && typeof rawSignalModeSummary === 'object' && !Array.isArray(rawSignalModeSummary)
      ? rawSignalModeSummary as Record<string, unknown>
      : {}
    const acceptedTotal = Object.values(signalModeSummary).reduce((sum: number, rawValue: unknown) => {
      if (!rawValue || typeof rawValue !== 'object' || Array.isArray(rawValue)) return sum
      return sum + Number((rawValue as Record<string, unknown>).accepted_count || 0)
    }, 0)
    const headrooms: ReplaySearchHeadroomCandidate[] = []

    const pushHeadroom = (
      group: ReplaySearchHeadroomCandidate['group'],
      label: string,
      actual: number,
      threshold: number,
      formatter: (margin: number) => string,
      direction: 'min' | 'max'
    ) => {
      if (!Number.isFinite(actual) || !Number.isFinite(threshold)) return
      const margin = direction === 'min' ? actual - threshold : threshold - actual
      const denominator = Math.max(Math.abs(threshold), direction === 'max' ? 0.01 : 1)
      headrooms.push({
        group,
        label: `${label} ${formatter(margin)}`,
        margin,
        normalizedMargin: margin / denominator
      })
    }

    const globalAccepted = Number(resultParsed.accepted_count || 0)
    const globalResolved = Number(resultParsed.resolved_count || 0)
    const globalResolvedShare = globalAccepted > 0 ? globalResolved / globalAccepted : 0
    const globalWinRate = resultParsed.win_rate == null ? null : Number(resultParsed.win_rate)
    const globalTotalPnl = Number(resultParsed.total_pnl_usd || 0)
    const globalMaxDrawdown = Number(resultParsed.max_drawdown_pct || 0)
    const globalPositiveWindows = Number(resultParsed.positive_window_count || 0)
    const globalWorstWindowPnl = Number(resultParsed.worst_window_pnl_usd || 0)
    const globalWorstWindowResolvedShare = Number(
      resultParsed.worst_active_window_resolved_share
      ?? resultParsed.worst_window_resolved_share
      ?? globalResolvedShare
    )
    const globalWorstWindowDrawdown = Number(resultParsed.worst_window_drawdown_pct || 0)
    const rejectReasonSummary = resultParsed.reject_reason_summary && typeof resultParsed.reject_reason_summary === 'object' && !Array.isArray(resultParsed.reject_reason_summary)
      ? resultParsed.reject_reason_summary as Record<string, unknown>
      : {}
    const traderConcentration = resultParsed.trader_concentration && typeof resultParsed.trader_concentration === 'object' && !Array.isArray(resultParsed.trader_concentration)
      ? resultParsed.trader_concentration as Record<string, unknown>
      : {}
    const marketConcentration = resultParsed.market_concentration && typeof resultParsed.market_concentration === 'object' && !Array.isArray(resultParsed.market_concentration)
      ? resultParsed.market_concentration as Record<string, unknown>
      : {}
    const entryPriceBandConcentration = resultParsed.entry_price_band_concentration && typeof resultParsed.entry_price_band_concentration === 'object' && !Array.isArray(resultParsed.entry_price_band_concentration)
      ? resultParsed.entry_price_band_concentration as Record<string, unknown>
      : {}
    const timeToCloseBandConcentration = resultParsed.time_to_close_band_concentration && typeof resultParsed.time_to_close_band_concentration === 'object' && !Array.isArray(resultParsed.time_to_close_band_concentration)
      ? resultParsed.time_to_close_band_concentration as Record<string, unknown>
      : {}
    const pauseGuardRejectShare = globalAccepted + Number(resultParsed.rejected_count || 0) > 0
      ? (
        Number(rejectReasonSummary.daily_loss_guard || 0)
        + Number(rejectReasonSummary.live_drawdown_guard || 0)
      ) / Math.max(Number(resultParsed.trade_count || 0), 1)
      : 0
    const minAccepted = Number(constraints.min_accepted_count || 0)
    const minResolved = Number(constraints.min_resolved_count || 0)
    const minResolvedShare = Number(constraints.min_resolved_share || 0)
    const minWinRate = Number(constraints.min_win_rate || 0)
    const minTotalPnlUsd = Number(constraints.min_total_pnl_usd ?? -1_000_000_000)
    const maxDrawdownPct = Number(constraints.max_drawdown_pct || 0)
    const maxPauseGuardRejectShare = Number(constraints.max_pause_guard_reject_share || 0)
    const maxTopTraderAcceptedShare = Number(constraints.max_top_trader_accepted_share || 0)
    const maxTopTraderAbsPnlShare = Number(constraints.max_top_trader_abs_pnl_share || 0)
    const maxTopMarketAcceptedShare = Number(constraints.max_top_market_accepted_share || 0)
    const maxTopMarketAbsPnlShare = Number(constraints.max_top_market_abs_pnl_share || 0)
    const maxTopEntryPriceBandAcceptedShare = Number(constraints.max_top_entry_price_band_accepted_share || 0)
    const maxTopEntryPriceBandAbsPnlShare = Number(constraints.max_top_entry_price_band_abs_pnl_share || 0)
    const maxTopTimeToCloseBandAcceptedShare = Number(constraints.max_top_time_to_close_band_accepted_share || 0)
    const maxTopTimeToCloseBandAbsPnlShare = Number(constraints.max_top_time_to_close_band_abs_pnl_share || 0)
    const minPositiveWindows = Number(constraints.min_positive_windows || 0)
    const minWorstWindowPnlUsd = Number(constraints.min_worst_window_pnl_usd ?? -1_000_000_000)
    const minWorstWindowResolvedShare = Number(constraints.min_worst_window_resolved_share || 0)
    const maxWorstWindowDrawdownPct = Number(constraints.max_worst_window_drawdown_pct || 0)
    const topTraderAcceptedShare = Number(traderConcentration.top_accepted_share || 0)
    const topTraderAbsPnlShare = Number(traderConcentration.top_abs_pnl_share || 0)
    const topMarketAcceptedShare = Number(marketConcentration.top_accepted_share || 0)
    const topMarketAbsPnlShare = Number(marketConcentration.top_abs_pnl_share || 0)
    const topEntryPriceBandAcceptedShare = Number(entryPriceBandConcentration.top_accepted_share || 0)
    const topEntryPriceBandAbsPnlShare = Number(entryPriceBandConcentration.top_abs_pnl_share || 0)
    const topTimeToCloseBandAcceptedShare = Number(timeToCloseBandConcentration.top_accepted_share || 0)
    const topTimeToCloseBandAbsPnlShare = Number(timeToCloseBandConcentration.top_abs_pnl_share || 0)

    if (minAccepted > 0) pushHeadroom('global', 'acc', globalAccepted, minAccepted, replayHeadroomCount, 'min')
    if (minResolved > 0) pushHeadroom('global', 'res', globalResolved, minResolved, replayHeadroomCount, 'min')
    if (minResolvedShare > 0) pushHeadroom('global', 'cov', globalResolvedShare, minResolvedShare, replayHeadroomPctPoints, 'min')
    if (minWinRate > 0 && globalWinRate != null) pushHeadroom('global', 'win', globalWinRate, minWinRate, replayHeadroomPctPoints, 'min')
    if (minTotalPnlUsd > -999_999_999) pushHeadroom('global', 'pnl', globalTotalPnl, minTotalPnlUsd, formatDollar, 'min')
    if (maxDrawdownPct > 0) pushHeadroom('global', 'dd', globalMaxDrawdown, maxDrawdownPct, replayHeadroomPctPoints, 'max')
    if (maxPauseGuardRejectShare > 0) pushHeadroom('global', 'pause', pauseGuardRejectShare, maxPauseGuardRejectShare, replayHeadroomPctPoints, 'max')
    if (maxTopTraderAcceptedShare > 0) pushHeadroom('global', 'wallet n', topTraderAcceptedShare, maxTopTraderAcceptedShare, replayHeadroomPctPoints, 'max')
    if (maxTopTraderAbsPnlShare > 0) pushHeadroom('global', 'wallet pnl', topTraderAbsPnlShare, maxTopTraderAbsPnlShare, replayHeadroomPctPoints, 'max')
    if (maxTopMarketAcceptedShare > 0) pushHeadroom('global', 'market n', topMarketAcceptedShare, maxTopMarketAcceptedShare, replayHeadroomPctPoints, 'max')
    if (maxTopMarketAbsPnlShare > 0) pushHeadroom('global', 'market pnl', topMarketAbsPnlShare, maxTopMarketAbsPnlShare, replayHeadroomPctPoints, 'max')
    if (maxTopEntryPriceBandAcceptedShare > 0) pushHeadroom('global', 'entry n', topEntryPriceBandAcceptedShare, maxTopEntryPriceBandAcceptedShare, replayHeadroomPctPoints, 'max')
    if (maxTopEntryPriceBandAbsPnlShare > 0) pushHeadroom('global', 'entry pnl', topEntryPriceBandAbsPnlShare, maxTopEntryPriceBandAbsPnlShare, replayHeadroomPctPoints, 'max')
    if (maxTopTimeToCloseBandAcceptedShare > 0) pushHeadroom('global', 'horizon n', topTimeToCloseBandAcceptedShare, maxTopTimeToCloseBandAcceptedShare, replayHeadroomPctPoints, 'max')
    if (maxTopTimeToCloseBandAbsPnlShare > 0) pushHeadroom('global', 'horizon pnl', topTimeToCloseBandAbsPnlShare, maxTopTimeToCloseBandAbsPnlShare, replayHeadroomPctPoints, 'max')
    if (minPositiveWindows > 0) pushHeadroom('global', 'pos', globalPositiveWindows, minPositiveWindows, replayHeadroomCount, 'min')
    if (minWorstWindowPnlUsd > -999_999_999) pushHeadroom('global', 'worst', globalWorstWindowPnl, minWorstWindowPnlUsd, formatDollar, 'min')
    if (minWorstWindowResolvedShare > 0) pushHeadroom('global', 'worst cov', globalWorstWindowResolvedShare, minWorstWindowResolvedShare, replayHeadroomPctPoints, 'min')
    if (maxWorstWindowDrawdownPct > 0) {
      pushHeadroom('global', 'worst dd', globalWorstWindowDrawdown, maxWorstWindowDrawdownPct, replayHeadroomPctPoints, 'max')
    }

    for (const [mode, prefix] of [['heuristic', 'heur'], ['xgboost', 'model']] as const) {
      if ((mode === 'heuristic' && !enabled.heuristic) || (mode === 'xgboost' && !enabled.xgboost)) continue
      const rawMode = signalModeSummary[mode]
      const payload = rawMode && typeof rawMode === 'object' && !Array.isArray(rawMode)
        ? rawMode as Record<string, unknown>
        : {}
      const acceptedCount = Number(payload.accepted_count || 0)
      const resolvedCount = Number(payload.resolved_count || 0)
      const winRate = payload.win_rate == null ? null : Number(payload.win_rate)
      const totalPnlUsd = Number(payload.total_pnl_usd || 0)
      const positiveWindowCount = Number(payload.positive_window_count || 0)
      const worstWindowPnlUsd = Number(payload.worst_window_pnl_usd ?? totalPnlUsd)
      const resolvedShare = acceptedCount > 0 ? resolvedCount / acceptedCount : 0
      const worstWindowResolvedShare = Number(
        payload.worst_active_window_resolved_share
        ?? payload.worst_window_resolved_share
        ?? resolvedShare
      )
      const inactiveWindowCount = Number(payload.inactive_window_count || 0)
      const acceptedShare = acceptedTotal > 0 ? acceptedCount / acceptedTotal : 0

      const minModeAccepted = Number(constraints[`min_${mode}_accepted_count`] || 0)
      const minModeResolved = Number(constraints[`min_${mode}_resolved_count`] || 0)
      const minModeResolvedShare = Number(constraints[`min_${mode}_resolved_share`] || 0)
      const minModeWinRate = Number(constraints[`min_${mode}_win_rate`] || 0)
      const minModePnlUsd = Number(constraints[`min_${mode}_pnl_usd`] || 0)
      const minModeWorstWindowPnlUsd = Number(constraints[`min_${mode}_worst_window_pnl_usd`] ?? -1_000_000_000)
      const minModeWorstWindowResolvedShare = Number(constraints[`min_${mode}_worst_window_resolved_share`] || 0)
      const minModePositiveWindows = Number(constraints[`min_${mode}_positive_windows`] || 0)
      const maxModeInactiveWindows = Number(constraints[`max_${mode}_inactive_windows`] ?? -1)

      if (minModeAccepted > 0) pushHeadroom(mode, `${prefix} n`, acceptedCount, minModeAccepted, replayHeadroomCount, 'min')
      if (minModeResolved > 0) pushHeadroom(mode, `${prefix} r`, resolvedCount, minModeResolved, replayHeadroomCount, 'min')
      if (minModeResolvedShare > 0) pushHeadroom(mode, `${prefix} cov`, resolvedShare, minModeResolvedShare, replayHeadroomPctPoints, 'min')
      if (minModeWinRate > 0 && winRate != null) pushHeadroom(mode, `${prefix} wr`, winRate, minModeWinRate, replayHeadroomPctPoints, 'min')
      if (minModePnlUsd !== 0) pushHeadroom(mode, `${prefix} pnl`, totalPnlUsd, minModePnlUsd, formatDollar, 'min')
      if (minModeWorstWindowPnlUsd > -999_999_999) {
        pushHeadroom(mode, `${prefix} worst`, worstWindowPnlUsd, minModeWorstWindowPnlUsd, formatDollar, 'min')
      }
      if (minModeWorstWindowResolvedShare > 0) {
        pushHeadroom(mode, `${prefix} worst cov`, worstWindowResolvedShare, minModeWorstWindowResolvedShare, replayHeadroomPctPoints, 'min')
      }
      if (minModePositiveWindows > 0) {
        pushHeadroom(mode, `${prefix} pos`, positiveWindowCount, minModePositiveWindows, replayHeadroomCount, 'min')
      }
      if (maxModeInactiveWindows >= 0) {
        pushHeadroom(mode, `${prefix} idle`, inactiveWindowCount, maxModeInactiveWindows, replayHeadroomCount, 'max')
      }
      if (mode === 'heuristic') {
        const maxShare = Number(constraints.max_heuristic_accepted_share || 0)
        if (maxShare > 0) pushHeadroom(mode, `${prefix} mix`, acceptedShare, maxShare, replayHeadroomPctPoints, 'max')
      } else {
        const minShare = Number(constraints.min_xgboost_accepted_share || 0)
        if (minShare > 0) pushHeadroom(mode, `${prefix} mix`, acceptedShare, minShare, replayHeadroomPctPoints, 'min')
      }
    }

    if (!headrooms.length) return {summary: 'none', hasActiveGuard: false, closestMarginRatio: null, hasFailure: false}

    const bestByGroup = new Map<ReplaySearchHeadroomCandidate['group'], ReplaySearchHeadroomCandidate>()
    for (const headroom of headrooms) {
      const current = bestByGroup.get(headroom.group)
      if (!current || headroom.normalizedMargin < current.normalizedMargin) {
        bestByGroup.set(headroom.group, headroom)
      }
    }

    const ordered = ['global', 'heuristic', 'xgboost']
      .map((group) => bestByGroup.get(group as ReplaySearchHeadroomCandidate['group']))
      .filter((value): value is ReplaySearchHeadroomCandidate => value != null)

    const closestMarginRatio = ordered.reduce<number | null>(
      (current, headroom) => current == null ? headroom.normalizedMargin : Math.min(current, headroom.normalizedMargin),
      null
    )
    return {
      summary: ordered.map((headroom) => headroom.label).join(' | '),
      hasActiveGuard: true,
      closestMarginRatio,
      hasFailure: ordered.some((headroom) => headroom.margin < 0)
    }
  } catch {
    return {summary: '-', hasActiveGuard: false, closestMarginRatio: null, hasFailure: false}
  }
}

interface ReplaySearchConfigSuggestion {
  diffCount: number
  summary: string
  aligned: boolean
}

interface ReplaySearchConfigDelta {
  changed: Array<{field: EditableConfigField; recommendedRaw: string}>
  replayOnlyKeys: string[]
}

interface ReplaySearchApplyScopeSummary {
  liveCount: number
  restartCount: number
  replayOnlyCount: number
  summary: string
  aligned: boolean
}

function replayConfigRawValue(value: unknown): string | null {
  if (value == null) return null
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : ''
  return String(value).trim()
}

function replayDurationSeconds(raw: string): number | null {
  const value = raw.trim().toLowerCase()
  if (!value) return null
  if (value === 'unlimited' || value === 'infinite' || value === 'inf' || value === 'none') return Number.POSITIVE_INFINITY
  const numeric = Number(value)
  if (Number.isFinite(numeric)) return numeric
  const match = value.match(/^([0-9]+(?:\.[0-9]+)?)([smhdw])$/)
  if (!match) return null
  const amount = Number(match[1])
  if (!Number.isFinite(amount)) return null
  const unitSeconds: Record<string, number> = {s: 1, m: 60, h: 3600, d: 86400, w: 604800}
  return amount * unitSeconds[match[2]]
}

function replayFormatDurationSeconds(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return ''
  if (seconds === 0) return '0s'
  const rounded = Math.round(seconds)
  const units: Array<[number, string]> = [
    [604800, 'w'],
    [86400, 'd'],
    [3600, 'h'],
    [60, 'm']
  ]
  for (const [unitSeconds, suffix] of units) {
    if (rounded % unitSeconds === 0) {
      return `${rounded / unitSeconds}${suffix}`
    }
  }
  return `${rounded}s`
}

function replayConfigValuesEqual(field: EditableConfigField, currentRaw: string, recommendedRaw: string): boolean {
  if (field.kind === 'bool') {
    return currentRaw.trim().toLowerCase() === recommendedRaw.trim().toLowerCase()
  }
  if (field.kind === 'duration') {
    const currentSeconds = replayDurationSeconds(currentRaw)
    const recommendedSeconds = replayDurationSeconds(recommendedRaw)
    if (currentSeconds != null && recommendedSeconds != null) {
      return Math.abs(currentSeconds - recommendedSeconds) < 1e-9
    }
  }
  if (field.kind === 'float' || field.kind === 'int') {
    const currentNumeric = Number(currentRaw)
    const recommendedNumeric = Number(recommendedRaw)
    if (Number.isFinite(currentNumeric) && Number.isFinite(recommendedNumeric)) {
      return Math.abs(currentNumeric - recommendedNumeric) < 1e-9
    }
  }
  return currentRaw.trim().toLowerCase() === recommendedRaw.trim().toLowerCase()
}

function replaySearchConfigDelta(
  rawConfigJson: string | null | undefined,
  settingsValues: EditableConfigValues,
  configFieldByKey: Map<string, EditableConfigField>
): ReplaySearchConfigDelta | null {
  if (!rawConfigJson) return null
  try {
    const parsed = JSON.parse(rawConfigJson)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    const entries = Object.entries(parsed as Record<string, unknown>)
    if (!entries.length) return null

    const changed: Array<{field: EditableConfigField; recommendedRaw: string}> = []
    const replayOnlyKeys: string[] = []
    for (const [key, value] of entries) {
      const recommendedRaw = replayConfigRawValue(value)
      if (recommendedRaw == null) continue
      const field = configFieldByKey.get(key)
      if (!field) {
        replayOnlyKeys.push(key)
        continue
      }
      const currentRaw = Object.prototype.hasOwnProperty.call(settingsValues, key)
        ? settingsValues[key]
        : field.defaultValue
      if (replayConfigValuesEqual(field, currentRaw, recommendedRaw)) continue
      changed.push({field, recommendedRaw})
    }

    return {changed, replayOnlyKeys}
  } catch {
    return null
  }
}

function replaySearchConfigSuggestion(
  rawConfigJson: string | null | undefined,
  settingsValues: EditableConfigValues,
  configFieldByKey: Map<string, EditableConfigField>
): ReplaySearchConfigSuggestion | null {
  const delta = replaySearchConfigDelta(rawConfigJson, settingsValues, configFieldByKey)
  if (!delta) return null
  const changed = delta.changed

  if (!changed.length) {
    return {diffCount: 0, summary: 'Already aligned', aligned: true}
  }

  const preview = changed
    .slice(0, 2)
    .map(({field, recommendedRaw}) => `${field.label}=${formatEditableConfigValue(field, recommendedRaw)}`)
    .join(', ')
  const suffix = changed.length > 2 ? ` +${changed.length - 2}` : ''
  return {
    diffCount: changed.length,
    summary: `${preview}${suffix}`,
    aligned: false,
  }
}

function replaySearchApplyScope(
  rawConfigJson: string | null | undefined,
  settingsValues: EditableConfigValues,
  configFieldByKey: Map<string, EditableConfigField>
): ReplaySearchApplyScopeSummary | null {
  const delta = replaySearchConfigDelta(rawConfigJson, settingsValues, configFieldByKey)
  if (!delta) return null
  const liveCount = delta.changed.filter(({field}) => field.liveApplies).length
  const restartCount = delta.changed.filter(({field}) => !field.liveApplies).length
  const replayOnlyCount = delta.replayOnlyKeys.length
  const parts: string[] = []
  if (liveCount > 0) parts.push(`live ${formatCount(liveCount)}`)
  if (restartCount > 0) parts.push(`restart ${formatCount(restartCount)}`)
  if (replayOnlyCount > 0) parts.push(`replay-only ${formatCount(replayOnlyCount)}`)
  return {
    liveCount,
    restartCount,
    replayOnlyCount,
    summary: parts.length ? parts.join(' | ') : 'none',
    aligned: parts.length === 0
  }
}

function trainingCycleDisplayLabel(label: string): string {
  if (label === 'Next scheduled') return 'Next run'
  if (label === 'Scheduled in') return 'Starts in'
  if (label === 'Trigger progress') return 'Progress'
  return label
}

function splitIntoColumns<T>(items: T[], columnCount: number): T[][] {
  if (columnCount <= 1 || items.length <= 1) {
    return [items]
  }

  const perColumn = Math.ceil(items.length / columnCount)
  const columns: T[][] = []
  for (let index = 0; index < items.length; index += perColumn) {
    columns.push(items.slice(index, index + perColumn))
  }
  return columns
}

interface DenseModelsRowProps {
  label: string
  value: string
  width: number
  color?: string
  selected?: boolean
  backgroundColor?: string
  labelWidth?: number
  minValueWidth?: number
  valueAlign?: 'left' | 'right'
}

function denseModelsLabelWidth(width: number): number {
  const safeWidth = Math.max(18, width)
  return Math.max(8, Math.min(16, Math.floor(safeWidth * 0.42)))
}

function DenseModelsRow({
  label,
  value,
  width,
  color = theme.white,
  selected = false,
  backgroundColor,
  labelWidth,
  minValueWidth = 6,
  valueAlign = 'right'
}: DenseModelsRowProps) {
  const safeWidth = Math.max(18, width)
  const maxLabelWidth = Math.max(6, safeWidth - Math.max(6, minValueWidth) - 1)
  const preferredLabelWidth = Math.max(labelWidth ?? denseModelsLabelWidth(safeWidth), Math.min(label.length, maxLabelWidth))
  const boundedLabelWidth = Math.max(
    6,
    Math.min(
      maxLabelWidth,
      preferredLabelWidth
    )
  )
  const valueWidth = Math.max(Math.max(6, minValueWidth), safeWidth - boundedLabelWidth - 1)
  const rowBackground = selected ? backgroundColor : undefined

  return (
    <InkBox width={safeWidth}>
      <Text color={selected ? theme.accent : theme.dim} backgroundColor={rowBackground} bold={selected}>
        {fit(label, boundedLabelWidth)}
      </Text>
      <Text backgroundColor={rowBackground}> </Text>
      <Text color={color} backgroundColor={rowBackground} bold={selected}>
        {valueAlign === 'left' ? fit(value, valueWidth) : fitRight(value, valueWidth)}
      </Text>
    </InkBox>
  )
}

function recentRunsColumnWidths(width: number) {
  const safeWidth = Math.max(24, width)
  const resultWidth = safeWidth >= 34 ? 8 : 6
  const logLossWidth = safeWidth >= 34 ? 8 : 7
  const brierWidth = safeWidth >= 32 ? 7 : 6
  const timestampWidth = Math.max(5, safeWidth - logLossWidth - brierWidth - resultWidth - 3)
  return {safeWidth, timestampWidth, logLossWidth, brierWidth, resultWidth}
}

function RecentRunsHeaderRow({width}: {width: number}) {
  const {safeWidth, timestampWidth, logLossWidth, brierWidth, resultWidth} = recentRunsColumnWidths(width)
  return (
    <InkBox width={safeWidth}>
      <Text color={theme.accent} bold>{fit('Timestamp', timestampWidth)}</Text>
      <Text> </Text>
      <Text color={theme.accent} bold>{fitRight('Log Loss', logLossWidth)}</Text>
      <Text> </Text>
      <Text color={theme.accent} bold>{fitRight('Brier', brierWidth)}</Text>
      <Text> </Text>
      <Text color={theme.accent} bold>{fitRight('Result', resultWidth)}</Text>
    </InkBox>
  )
}

function RecentRunsDataRow({
  width,
  timestamp,
  timestampColor,
  logLoss,
  logLossColor,
  brier,
  brierColor,
  result,
  resultColor
}: {
  width: number
  timestamp: string
  timestampColor: string
  logLoss: string
  logLossColor: string
  brier: string
  brierColor: string
  result: string
  resultColor: string
}) {
  const {safeWidth, timestampWidth, logLossWidth, brierWidth, resultWidth} = recentRunsColumnWidths(width)
  return (
    <InkBox width={safeWidth}>
      <Text color={timestampColor}>{fit(timestamp, timestampWidth)}</Text>
      <Text> </Text>
      <Text color={logLossColor}>{fitRight(logLoss, logLossWidth)}</Text>
      <Text> </Text>
      <Text color={brierColor}>{fitRight(brier, brierWidth)}</Text>
      <Text> </Text>
      <Text color={resultColor}>{fitRight(result, resultWidth)}</Text>
    </InkBox>
  )
}

function recentExitColumnWidths(width: number) {
  const safeWidth = Math.max(28, width)
  const actionWidth = 9
  const returnWidth = 8
  const timestampWidth = 14
  const reasonWidth = Math.max(8, safeWidth - actionWidth - returnWidth - timestampWidth - 3)
  return {safeWidth, timestampWidth, actionWidth, returnWidth, reasonWidth}
}

function RecentExitHeaderRow({width}: {width: number}) {
  const {safeWidth, timestampWidth, actionWidth, returnWidth, reasonWidth} = recentExitColumnWidths(width)
  return (
    <InkBox width={safeWidth}>
      <Text color={theme.accent} bold>{fit('Timestamp', timestampWidth)}</Text>
      <Text> </Text>
      <Text color={theme.accent} bold>{fit('Action', actionWidth)}</Text>
      <Text> </Text>
      <Text color={theme.accent} bold>{fitRight('Return', returnWidth)}</Text>
      <Text> </Text>
      <Text color={theme.accent} bold>{fit('Reason', reasonWidth)}</Text>
    </InkBox>
  )
}

function RecentExitDataRow({
  width,
  timestamp,
  timestampColor,
  action,
  actionColor,
  estimatedReturn,
  estimatedReturnColor,
  reason,
  reasonColor
}: {
  width: number
  timestamp: string
  timestampColor: string
  action: string
  actionColor: string
  estimatedReturn: string
  estimatedReturnColor: string
  reason: string
  reasonColor: string
}) {
  const {safeWidth, timestampWidth, actionWidth, returnWidth, reasonWidth} = recentExitColumnWidths(width)
  return (
    <InkBox width={safeWidth}>
      <Text color={timestampColor}>{fit(timestamp, timestampWidth)}</Text>
      <Text> </Text>
      <Text color={actionColor}>{fit(action, actionWidth)}</Text>
      <Text> </Text>
      <Text color={estimatedReturnColor}>{fitRight(estimatedReturn, returnWidth)}</Text>
      <Text> </Text>
      <Text color={reasonColor}>{fit(reason, reasonWidth)}</Text>
    </InkBox>
  )
}

function ModelsSectionTitle({
  title,
  width,
  selected,
  backgroundColor
}: {
  title: string
  width: number
  selected: boolean
  backgroundColor?: string
}) {
  const prefix = selected ? '> ' : ''

  return (
    <Text
      color={selected ? theme.accent : theme.white}
      backgroundColor={selected ? backgroundColor : undefined}
      bold
    >
      {fit(`${prefix}${title}`, Math.max(1, width))}
    </Text>
  )
}

function ModelsSubsectionTitle({title, width, color = theme.accent}: {title: string; width: number; color?: string}) {
  const safeWidth = Math.max(1, width)
  return (
    <Text color={color} bold>
      {fit(title.trimStart(), safeWidth)}
    </Text>
  )
}

function ModelsSpacer() {
  return <Text> </Text>
}

export function Models({selectedPanelIndex, detailOpen, selectedSettingIndex, settingsValues}: ModelsProps) {
  const terminal = useTerminalSize()
  const botState = useBotState()
  const configFieldByKey = useMemo(
    () => new Map(editableConfigFields.map((field) => [field.key, field])),
    []
  )
  const settlementFixedCostUsd = useMemo(() => {
    const field = configFieldByKey.get('SETTLEMENT_FIXED_COST_USD')
    const rawValue = settingsValues['SETTLEMENT_FIXED_COST_USD'] || field?.defaultValue || '0'
    const parsed = Number.parseFloat(String(rawValue || '').trim())
    return Number.isFinite(parsed) ? Math.max(parsed, 0) : 0
  }, [configFieldByKey, settingsValues])
  const modalBackground = terminal.backgroundColor || theme.modalBackground
  const selectedRowBackground = selectionBackgroundColor(modalBackground)
  const nowTs = useNow()
  const stacked = stackPanels(terminal.width)
  const models = useQuery<ModelRow>(MODEL_SQL)
  const retrainRuns = useQuery<RetrainRunRow>(RETRAIN_RUN_SQL)
  const replayLatestRuns = useQuery<ReplayRunRow>(REPLAY_LATEST_RUN_SQL)
  const replaySearchSummaryRows = useQuery<ReplaySearchSummaryRow>(REPLAY_SEARCH_SUMMARY_SQL)
  const replayBestWalletRows = useQuery<ReplaySegmentRow>(REPLAY_SEGMENT_BEST_SQL, ['trader_address', REPLAY_SEGMENT_MIN_RESOLVED])
  const replayWorstWalletRows = useQuery<ReplaySegmentRow>(REPLAY_SEGMENT_WORST_SQL, ['trader_address', REPLAY_SEGMENT_MIN_RESOLVED])
  const replayBestBandRows = useQuery<ReplaySegmentRow>(REPLAY_SEGMENT_BEST_SQL, ['entry_price_band', REPLAY_SEGMENT_MIN_RESOLVED])
  const replayWorstBandRows = useQuery<ReplaySegmentRow>(REPLAY_SEGMENT_WORST_SQL, ['entry_price_band', REPLAY_SEGMENT_MIN_RESOLVED])
  const replayBestHorizonRows = useQuery<ReplaySegmentRow>(REPLAY_SEGMENT_BEST_SQL, ['time_to_close_band', REPLAY_SEGMENT_MIN_RESOLVED])
  const replayWorstHorizonRows = useQuery<ReplaySegmentRow>(REPLAY_SEGMENT_WORST_SQL, ['time_to_close_band', REPLAY_SEGMENT_MIN_RESOLVED])
  const trackerRows = useQuery<TrackerRow>(TRACKER_SQL)
  const perfRows = useQuery<PerfRow>(PERF_SQL)
  const signalModes = useQuery<SignalModeRow>(SIGNAL_MODE_SQL)
  const recentSignalModes = useQuery<RecentSignalModeRow>(RECENT_SIGNAL_MODE_SQL)
  const calibrationSummaryRows = useQuery<CalibrationSummaryRow>(CALIBRATION_SUMMARY_SQL)
  const confusionRows = useQuery<ConfusionMatrixRow>(CONFUSION_SQL)
  const calibrationRows = useQuery<CalibrationRow>(CALIBRATION_SQL)
  const flowRows = useQuery<FlowRow>(FLOW_SQL)
  const trainingSummaryRows = useQuery<TrainingSummaryRow>(TRAINING_SUMMARY_SQL)
  const trainingProgressRows = useQuery<TrainingProgressRow>(TRAINING_PROGRESS_SQL)
  const exitAuditModeRealMoney = botState.mode === 'live' ? 1 : 0
  const exitAuditSummaryRows = useQuery<ExitAuditSummaryRow>(EXIT_AUDIT_SUMMARY_SQL, [exitAuditModeRealMoney])
  const recentExitAuditRows = useQuery<ExitAuditRecentRow>(EXIT_AUDIT_RECENT_SQL, [exitAuditModeRealMoney])
  const exitAttributionRows = useQuery<ExitAttributionRow>(EXIT_ATTRIBUTION_SQL, [settlementFixedCostUsd, exitAuditModeRealMoney])

  const latest = models[0]
  const latestReplay = replayLatestRuns[0]
  const latestReplaySearch = replaySearchSummaryRows[0]
  const replayBestWallet = replayBestWalletRows[0]
  const replayWorstWallet = replayWorstWalletRows[0]
  const replayBestBand = replayBestBandRows[0]
  const replayWorstBand = replayWorstBandRows[0]
  const replayBestHorizon = replayBestHorizonRows[0]
  const replayWorstHorizon = replayWorstHorizonRows[0]
  const tracker = trackerRows[0]
  const calibration = calibrationSummaryRows[0]
  const confusion = confusionRows[0]
  const flow = flowRows[0]
  const trainingSummary = trainingSummaryRows[0]
  const trainingProgress = trainingProgressRows[0]
  const exitAuditSummary = exitAuditSummaryRows[0]
  const exitAttribution = exitAttributionRows[0]
  const latestSharedHoldoutRun = retrainRuns.find((row) => sharedHoldoutComparison(row) != null)
  const latestSharedHoldout = sharedHoldoutComparison(latestSharedHoldoutRun)
  const trackerSnapshot = perfRows.find((row) => row.mode === 'shadow') ?? perfRows[0]

  const featureCount = useMemo(() => parseFeatureCount(latest?.feature_cols), [latest?.feature_cols])
  const useRate = ratio(tracker?.taken, tracker?.signals)
  const trackerWinRate = ratio(tracker?.wins, tracker?.resolved)
  const retrainGaps = useMemo(
    () =>
      retrainRuns
        .slice(0, 12)
        .map((row, index, rows) => {
          const next = rows[index + 1]
          return next ? row.finished_at - next.finished_at : null
        })
        .filter((gap): gap is number => gap != null && gap > 0),
    [retrainRuns]
  )
  const calibrationLimit = terminal.compact ? 3 : terminal.height < 42 ? 4 : 5
  const historyLimit = terminal.compact ? 4 : terminal.height < 42 ? 5 : terminal.height < 50 ? 7 : 10
  const exitAuditHistoryLimit = terminal.compact ? 3 : terminal.height < 42 ? 4 : 6
  const twoColumnPanelContentWidth = stacked
    ? Math.max(46, terminal.width - 12)
    : Math.max(34, Math.floor((terminal.width - 18) / 2))
  const secondaryRowGap = 1
  const secondaryThreeAcross = !stacked && terminal.width >= 150
  const secondaryRowWidth = Math.max(96, terminal.width - 8)
  const confusionBoxWidth = secondaryThreeAcross
    ? Math.max(18, Math.floor(secondaryRowWidth * 0.17))
    : Math.max(13, Math.min(23, terminal.width - 12))
  const combinedSectionGap = 3
  const combinedMetricsBoxWidth = secondaryThreeAcross
    ? Math.max(60, secondaryRowWidth - confusionBoxWidth - secondaryRowGap)
    : undefined
  const combinedMetricsContentWidth = secondaryThreeAcross
    ? Math.max(56, Number(combinedMetricsBoxWidth) - 4)
    : twoColumnPanelContentWidth
  const combinedPanelsWide = secondaryThreeAcross && combinedMetricsContentWidth >= 68
  const combinedSectionWideBudget = combinedPanelsWide
    ? Math.max(48, combinedMetricsContentWidth - combinedSectionGap)
    : combinedMetricsContentWidth
  const confidenceSectionContentWidth = combinedPanelsWide
    ? Math.max(24, Math.floor(combinedSectionWideBudget / 2))
    : combinedMetricsContentWidth
  const signalModesSectionContentWidth = combinedPanelsWide
    ? Math.max(24, combinedSectionWideBudget - confidenceSectionContentWidth)
    : combinedMetricsContentWidth
  const retrainPanelContentWidth = twoColumnPanelContentWidth
  const confusionPanelContentWidth = Math.max(9, confusionBoxWidth - 4)
  const confusionCellWidth = Math.max(4, Math.floor((confusionPanelContentWidth - 1) / 2))
  const calibrationWidths = useMemo(() => {
    const rangeWidth = 8
    const nWidth = 5
    const gapCount = 4
    const metricWidth = Math.max(7, Math.floor((confidenceSectionContentWidth - rangeWidth - nWidth - gapCount) / 3))
    const used = rangeWidth + nWidth + gapCount + metricWidth * 3
    return {
      rangeWidth: rangeWidth + Math.max(0, confidenceSectionContentWidth - used),
      metricWidth,
      nWidth
    }
  }, [confidenceSectionContentWidth])
  const signalModeWidths = useMemo(() => {
    const useWidth = 7
    const winWidth = 7
    const edgeWidth = 7
    const pnlWidth = Math.max(12, Math.min(14, Math.floor(signalModesSectionContentWidth * 0.22)))
    const gapCount = 4
    return {
      modeWidth: Math.max(10, signalModesSectionContentWidth - useWidth - winWidth - edgeWidth - pnlWidth - gapCount),
      useWidth,
      winWidth,
      edgeWidth,
      pnlWidth
    }
  }, [signalModesSectionContentWidth])
  const retrainWidths = useMemo(() => {
    const sampleWidth = 8
    const brierWidth = 7
    const lossWidth = 7
    const gapCount = 4
    const minTimeWidth = 13
    let stateWidth = Math.max(8, Math.min(12, Math.floor(retrainPanelContentWidth * 0.2)))
    let timeWidth = retrainPanelContentWidth - gapCount - sampleWidth - brierWidth - lossWidth - stateWidth

    if (timeWidth < minTimeWidth) {
      const reclaimed = Math.min(minTimeWidth - timeWidth, Math.max(0, stateWidth - 8))
      stateWidth -= reclaimed
      timeWidth += reclaimed
    }

    if (timeWidth < minTimeWidth) {
      timeWidth = minTimeWidth
      stateWidth = Math.max(8, retrainPanelContentWidth - gapCount - sampleWidth - brierWidth - lossWidth - timeWidth)
    }

    return {
      timeWidth,
      sampleWidth,
      brierWidth,
      lossWidth,
      stateWidth
    }
  }, [retrainPanelContentWidth])
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
  const earlyTriggerThreshold = parseNonNegativeInt(rawConfigValue('RETRAIN_MIN_NEW_LABELS'), 100)
  const minSamplesThreshold = parseNonNegativeInt(rawConfigValue('RETRAIN_MIN_SAMPLES'), 200)
  const hasDeployedModel = Number(trainingProgress?.last_deployed_trained_at || 0) > 0
  const triggerProgressCurrent = hasDeployedModel
    ? Math.max(0, Number(trainingProgress?.new_labeled || 0))
    : Math.max(0, Number(trainingProgress?.total_labeled || 0))
  const triggerProgressTarget = hasDeployedModel ? earlyTriggerThreshold : minSamplesThreshold
  const triggerProgressValue = `${formatCount(triggerProgressCurrent)} / ${formatCount(triggerProgressTarget)}${hasDeployedModel ? ' new' : ' total'}`
  const manualRunItem = manualRetrainLabel(
    Number(botState.started_at || 0),
    Number(botState.last_activity_at || 0),
    Number(botState.poll_interval || 1),
    Boolean(botState.retrain_in_progress),
    nowTs
  )
  const nextScheduledRetrainTs = useMemo(
    () => getNextScheduledRetrainTs(normalizeCadence(baseCadenceRaw), clampHour(retrainHourRaw), nowTs),
    [baseCadenceRaw, retrainHourRaw, nowTs]
  )
  const trackerHealthStats = useMemo<CompactStatItem[]>(
    () => [
      {label: 'Signals logged', value: formatCount(tracker?.signals)},
      {label: 'Bets taken', value: formatCount(tracker?.taken)},
      {
        label: 'Use rate',
        value: formatPct(useRate, 1),
        color: useRate != null ? probabilityColor(Math.max(0.5, useRate)) : theme.dim
      },
      {label: 'Resolved bets', value: formatCount(tracker?.resolved)},
      {
        label: 'Win rate',
        value: formatPct(trackerWinRate, 1),
        color: trackerWinRate != null ? probabilityColor(trackerWinRate) : theme.dim
      },
      {
        label: 'Avg confidence',
        value: formatPct(tracker?.avg_confidence, 1),
        color: tracker?.avg_confidence != null ? probabilityColor(tracker.avg_confidence) : theme.dim
      },
      {
        label: 'Avg edge',
        value: formatPct(tracker?.avg_edge, 1),
        color: signedMetricColor(tracker?.avg_edge)
      },
      {
        label: 'Tracker P&L',
        value: formatDollar(tracker?.total_pnl),
        color: dollarColor(tracker?.total_pnl)
      },
      {
        label: 'Sharpe ratio',
        value: formatNumber(trackerSnapshot?.sharpe, 2),
        color: sharpeColor(trackerSnapshot?.sharpe)
      },
      {
        label: 'Snapshot age',
        value: trackerSnapshot ? secondsAgo(trackerSnapshot.snapshot_at) : '-'
      }
    ],
    [tracker, trackerSnapshot, trackerWinRate, useRate]
  )
  const trackerHealthColumns = useMemo(
    () => splitIntoColumns(trackerHealthStats, 2),
    [trackerHealthStats]
  )
  const replaySearchSuggestedConfig = useMemo(
    () => replaySearchConfigSuggestion(latestReplaySearch?.config_json, settingsValues, configFieldByKey),
    [configFieldByKey, latestReplaySearch?.config_json, settingsValues]
  )
  const replaySearchApplyScopeSummary = useMemo(
    () => replaySearchApplyScope(latestReplaySearch?.config_json, settingsValues, configFieldByKey),
    [configFieldByKey, latestReplaySearch?.config_json, settingsValues]
  )
  const replaySearchDeployGap = useMemo(
    () => replaySearchDeployGapSummary(latestReplaySearch?.policy_json, latestReplaySearch?.config_json),
    [latestReplaySearch?.config_json, latestReplaySearch?.policy_json]
  )
  const replaySearchCurrentModeRisk = useMemo(
    () => replaySearchCurrentModeRiskSummary(
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.base_policy_json
    ),
    [latestReplaySearch?.base_policy_json, latestReplaySearch?.constraints_json, latestReplaySearch?.current_candidate_result_json]
  )
  const replaySearchBestHeadroom = useMemo(
    () => replaySearchHeadroomSummary(
      latestReplaySearch?.result_json,
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.policy_json
    ),
    [latestReplaySearch?.constraints_json, latestReplaySearch?.policy_json, latestReplaySearch?.result_json]
  )
  const replaySearchCurrentHeadroom = useMemo(
    () => replaySearchHeadroomSummary(
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.base_policy_json
    ),
    [latestReplaySearch?.base_policy_json, latestReplaySearch?.constraints_json, latestReplaySearch?.current_candidate_result_json]
  )
  const replaySearchPauseGuard = useMemo(
    () => replaySearchPauseGuardSummary(
      latestReplaySearch?.result_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.pause_guard_penalty
    ),
    [
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.pause_guard_penalty,
      latestReplaySearch?.result_json
    ]
  )
  const replaySearchTraderConcentration = useMemo(
    () => replaySearchTraderConcentrationSummary(
      latestReplaySearch?.result_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.wallet_concentration_penalty
    ),
    [
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.result_json,
      latestReplaySearch?.wallet_concentration_penalty
    ]
  )
  const replaySearchMarketConcentration = useMemo(
    () => replaySearchMarketConcentrationSummary(
      latestReplaySearch?.result_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.market_concentration_penalty
    ),
    [
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.result_json,
      latestReplaySearch?.market_concentration_penalty
    ]
  )
  const replaySearchEntryPriceBandConcentration = useMemo(
    () => replaySearchEntryPriceBandConcentrationSummary(
      latestReplaySearch?.result_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.entry_price_band_concentration_penalty
    ),
    [
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.result_json,
      latestReplaySearch?.entry_price_band_concentration_penalty
    ]
  )
  const replaySearchTimeToCloseBandConcentration = useMemo(
    () => replaySearchTimeToCloseBandConcentrationSummary(
      latestReplaySearch?.result_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.time_to_close_band_concentration_penalty
    ),
    [
      latestReplaySearch?.constraints_json,
      latestReplaySearch?.current_candidate_result_json,
      latestReplaySearch?.result_json,
      latestReplaySearch?.time_to_close_band_concentration_penalty
    ]
  )
  const replayLabStats = useMemo<CompactStatItem[]>(
    () => [
      {
        label: 'Last replay',
        value: latestReplay?.finished_at ? secondsAgo(latestReplay.finished_at) : '-'
      },
      {
        label: 'Policy',
        value: latestReplay
          ? `${String(latestReplay.label || '').trim() || 'latest'} ${String(latestReplay.policy_version || '').slice(0, 8)}`.trim()
          : '-',
        color: latestReplay ? theme.white : theme.dim
      },
      {
        label: 'Replay P&L',
        value: formatDollar(latestReplay?.total_pnl_usd),
        color: dollarColor(latestReplay?.total_pnl_usd)
      },
      {
        label: 'Max DD',
        value: formatPct(latestReplay?.max_drawdown_pct, 1),
        color: lowerIsBetterColor(latestReplay?.max_drawdown_pct, 0.05, 0.12)
      },
      {
        label: 'Accept / win',
        value: latestReplay
          ? `${formatCount(latestReplay.accepted_count)} / ${formatPct(latestReplay.win_rate, 1)}`
          : '-',
        color: latestReplay?.win_rate != null ? probabilityColor(latestReplay.win_rate) : theme.dim
      },
      {
        label: 'Search run',
        value: latestReplaySearch?.finished_at ? secondsAgo(latestReplaySearch.finished_at) : '-'
      },
      {
        label: 'Search fea/rej',
        value: latestReplaySearch
          ? `${formatCount(latestReplaySearch.feasible_count)} / ${formatCount(latestReplaySearch.rejected_count)}`
          : '-',
        color: latestReplaySearch
          ? Number(latestReplaySearch.feasible_count || 0) > 0
            ? theme.green
            : Number(latestReplaySearch.candidate_count || 0) > 0
              ? theme.red
              : theme.dim
          : theme.dim
      },
      {
        label: 'Best search',
        value: latestReplaySearch
          ? latestReplaySearch.candidate_index != null
            ? `#${formatCount(latestReplaySearch.candidate_index)} @ ${formatNumber(latestReplaySearch.score ?? latestReplaySearch.best_feasible_score, 2)}`
            : latestReplaySearch.best_feasible_score != null
              ? `score ${formatNumber(latestReplaySearch.best_feasible_score, 2)}`
              : '-'
          : '-',
        color: latestReplaySearch ? theme.white : theme.dim
      },
      {
        label: 'Best score',
        value: replaySearchScoreBreakdownSummary(latestReplaySearch?.result_json),
        color: latestReplaySearch?.result_json ? theme.white : theme.dim
      },
      {
        label: 'Score weights',
        value: replaySearchScoreWeightSummary(latestReplaySearch),
        color: latestReplaySearch ? theme.white : theme.dim
      },
      {
        label: 'Search robust',
        value: latestReplaySearch
          ? `${formatDollar(latestReplaySearch.total_pnl_usd)} / ${formatPct(latestReplaySearch.max_drawdown_pct, 1)}`
          : '-',
        color: dollarColor(latestReplaySearch?.total_pnl_usd)
      },
      {
        label: 'Search windows',
        value: latestReplaySearch
          ? `${formatCount(latestReplaySearch.positive_window_count)}+ / ${formatCount(latestReplaySearch.negative_window_count)}- | ${formatDollar(latestReplaySearch.worst_window_pnl_usd)}`
          : '-',
        color: dollarColor(latestReplaySearch?.worst_window_pnl_usd)
      },
      {
        label: 'Cfg drift',
        value: replaySearchSuggestedConfig
          ? replaySearchSuggestedConfig.aligned
            ? 'Already aligned'
            : `${formatCount(replaySearchSuggestedConfig.diffCount)} keys differ`
          : '-',
        color: replaySearchSuggestedConfig
          ? replaySearchSuggestedConfig.aligned
            ? theme.green
            : theme.yellow
          : theme.dim
      },
      {
        label: 'Suggest cfg',
        value: replaySearchSuggestedConfig?.summary || '-',
        color: replaySearchSuggestedConfig
          ? replaySearchSuggestedConfig.aligned
            ? theme.green
            : theme.white
          : theme.dim
      },
      {
        label: 'Apply scope',
        value: replaySearchApplyScopeSummary?.summary || '-',
        color: !replaySearchApplyScopeSummary
          ? theme.dim
          : replaySearchApplyScopeSummary.aligned
            ? theme.green
            : replaySearchApplyScopeSummary.replayOnlyCount > 0 || replaySearchApplyScopeSummary.restartCount > 0
              ? theme.yellow
              : theme.white
      },
      {
        label: 'Deploy gap',
        value: replaySearchDeployGap,
        color: !latestReplaySearch
          ? theme.dim
          : replaySearchDeployGap === 'none'
            ? theme.green
            : theme.yellow
      },
      {
        label: 'Seg gates',
        value: replaySearchSegmentGateSummary(latestReplaySearch?.policy_json),
        color: latestReplaySearch?.policy_json ? theme.white : theme.dim
      },
      {
        label: 'Wallet conc',
        value: replaySearchTraderConcentration.summary,
        color: !latestReplaySearch
          ? theme.dim
          : replaySearchTraderConcentration.overLimit
            ? theme.red
            : replaySearchTraderConcentration.hasActiveGuard
              ? theme.green
              : theme.white
      },
      {
        label: 'Market conc',
        value: replaySearchMarketConcentration.summary,
        color: !latestReplaySearch
          ? theme.dim
          : replaySearchMarketConcentration.overLimit
            ? theme.red
            : replaySearchMarketConcentration.hasActiveGuard
              ? theme.green
              : theme.white
      },
      {
        label: 'Entry conc',
        value: replaySearchEntryPriceBandConcentration.summary,
        color: !latestReplaySearch
          ? theme.dim
          : replaySearchEntryPriceBandConcentration.overLimit
            ? theme.red
            : replaySearchEntryPriceBandConcentration.hasActiveGuard
              ? theme.green
              : theme.white
      },
      {
        label: 'Horizon conc',
        value: replaySearchTimeToCloseBandConcentration.summary,
        color: !latestReplaySearch
          ? theme.dim
          : replaySearchTimeToCloseBandConcentration.overLimit
            ? theme.red
            : replaySearchTimeToCloseBandConcentration.hasActiveGuard
              ? theme.green
              : theme.white
      },
      {
        label: 'Pause guard',
        value: replaySearchPauseGuard.summary,
        color: !latestReplaySearch
          ? theme.dim
          : replaySearchPauseGuard.overLimit
            ? theme.red
            : replaySearchPauseGuard.hasActiveGuard
              ? theme.green
              : theme.white
      },
      {
        label: 'Search modes',
        value: replaySearchModeMixSummary(latestReplaySearch?.result_json, latestReplaySearch?.policy_json),
        color: latestReplaySearch?.result_json ? theme.white : theme.dim
      },
      {
        label: 'Cur evidence',
        value: replaySearchCurrentModeEvidenceSummary(
          latestReplaySearch?.current_candidate_result_json,
          latestReplaySearch?.base_policy_json
        ),
        color: latestReplaySearch?.current_candidate_result_json ? theme.white : theme.dim
      },
      {
        label: 'Mode guard',
        value: replaySearchModeFloorSummary(latestReplaySearch?.constraints_json, latestReplaySearch?.policy_json),
        color: latestReplaySearch?.constraints_json ? theme.white : theme.dim
      },
      {
        label: 'Mode pen',
        value: replaySearchModePenaltySummary(latestReplaySearch),
        color: latestReplaySearch ? theme.white : theme.dim
      },
      {
        label: 'Best headroom',
        value: replaySearchBestHeadroom.summary,
        color: !latestReplaySearch
          ? theme.dim
          : !replaySearchBestHeadroom.hasActiveGuard
            ? theme.dim
            : replaySearchBestHeadroom.hasFailure
              ? theme.red
              : replaySearchBestHeadroom.closestMarginRatio != null && replaySearchBestHeadroom.closestMarginRatio < 0.15
                ? theme.yellow
                : theme.green
      },
      {
        label: 'Cur headroom',
        value: replaySearchCurrentHeadroom.summary,
        color: !latestReplaySearch
          ? theme.dim
          : !replaySearchCurrentHeadroom.hasActiveGuard
            ? theme.dim
            : replaySearchCurrentHeadroom.hasFailure
              ? theme.red
              : replaySearchCurrentHeadroom.closestMarginRatio != null && replaySearchCurrentHeadroom.closestMarginRatio < 0.15
                ? theme.yellow
                : theme.green
      },
      {
        label: 'Mode drift',
        value: replaySearchModeDriftSummary(
          latestReplaySearch?.result_json,
          latestReplaySearch?.current_candidate_result_json,
          latestReplaySearch?.policy_json,
          latestReplaySearch?.base_policy_json
        ),
        color: latestReplaySearch?.current_candidate_result_json ? theme.white : theme.dim
      },
      {
        label: 'Cur mode risk',
        value: replaySearchCurrentModeRisk.summary,
        color: !latestReplaySearch
          ? theme.dim
          : !replaySearchCurrentModeRisk.hasActiveGuard
            ? theme.dim
            : replaySearchCurrentModeRisk.breachCount === 0
              ? theme.green
              : replaySearchCurrentModeRisk.breachCount >= 3
                ? theme.red
                : theme.yellow
      },
      {
        label: 'Cur feasible',
        value: latestReplaySearch
          ? `${Number(latestReplaySearch.current_candidate_feasible || 0) > 0 ? 'yes' : 'no'} | ${formatDollar(latestReplaySearch.current_candidate_total_pnl_usd)} / ${formatPct(latestReplaySearch.current_candidate_max_drawdown_pct, 1)}`
          : '-',
        color: latestReplaySearch
          ? Number(latestReplaySearch.current_candidate_feasible || 0) > 0
            ? dollarColor(latestReplaySearch.current_candidate_total_pnl_usd)
            : theme.yellow
          : theme.dim
      },
      {
        label: 'Cur fails',
        value: replaySearchFailureSummary(
          latestReplaySearch?.current_candidate_constraint_failures_json,
          latestReplaySearch?.current_candidate_feasible
        ),
        color: !latestReplaySearch
          ? theme.dim
          : Number(latestReplaySearch.current_candidate_feasible || 0) > 0
            ? theme.dim
            : theme.yellow
      },
      {
        label: 'Cur score',
        value: replaySearchScoreBreakdownSummary(latestReplaySearch?.current_candidate_result_json),
        color: latestReplaySearch?.current_candidate_result_json ? theme.white : theme.dim
      },
      {
        label: 'Score drift',
        value: replaySearchScoreDriftSummary(
          latestReplaySearch?.result_json,
          latestReplaySearch?.current_candidate_result_json
        ),
        color: latestReplaySearch?.current_candidate_result_json ? theme.white : theme.dim
      },
      {
        label: 'Cur regret',
        value: latestReplaySearch
          ? `${formatDollar(latestReplaySearch.best_vs_current_pnl_usd)} / ${formatNumber(latestReplaySearch.best_vs_current_score, 2)}`
          : '-',
        color: dollarColor(latestReplaySearch?.best_vs_current_pnl_usd)
      },
      {
        label: 'Best wallet',
        value: replaySegmentValue('trader_address', replayBestWallet),
        color: dollarColor(replayBestWallet?.total_pnl_usd)
      },
      {
        label: 'Worst wallet',
        value: replaySegmentValue('trader_address', replayWorstWallet),
        color: dollarColor(replayWorstWallet?.total_pnl_usd)
      },
      {
        label: 'Best band',
        value: replaySegmentValue('entry_price_band', replayBestBand),
        color: dollarColor(replayBestBand?.total_pnl_usd)
      },
      {
        label: 'Worst band',
        value: replaySegmentValue('entry_price_band', replayWorstBand),
        color: dollarColor(replayWorstBand?.total_pnl_usd)
      },
      {
        label: 'Best horizon',
        value: replaySegmentValue('time_to_close_band', replayBestHorizon),
        color: dollarColor(replayBestHorizon?.total_pnl_usd)
      },
      {
        label: 'Worst horizon',
        value: replaySegmentValue('time_to_close_band', replayWorstHorizon),
        color: dollarColor(replayWorstHorizon?.total_pnl_usd)
      }
    ],
    [
      latestReplay,
      latestReplaySearch,
      replaySearchDeployGap,
      replaySearchBestHeadroom,
      replaySearchCurrentModeRisk,
      replaySearchTraderConcentration,
      replaySearchMarketConcentration,
      replaySearchEntryPriceBandConcentration,
      replaySearchTimeToCloseBandConcentration,
      replaySearchSuggestedConfig,
      replayBestBand,
      replayBestHorizon,
      replayBestWallet,
      replayWorstBand,
      replayWorstHorizon,
      replayWorstWallet
    ]
  )
  const trainingCycleStats = useMemo<CompactStatItem[]>(
    () => [
      {label: 'Update style', value: 'Full retrain'},
      {label: 'Base cadence', value: baseCadenceValue},
      {label: 'Run time', value: retrainHourValue},
      {label: 'Next scheduled', value: formatShortDateTime(nextScheduledRetrainTs)},
      {label: 'Scheduled in', value: timeUntil(nextScheduledRetrainTs)},
      {label: 'Early check', value: earlyCheckValue},
      {label: 'Early trigger', value: earlyTriggerValue},
      {
        label: 'Trigger progress',
        value: triggerProgressValue,
        color: progressStatColor(triggerProgressCurrent, triggerProgressTarget)
      },
      manualRunItem,
      {label: 'Total runs', value: formatCount(trainingSummary?.total_runs)}
    ],
    [
      baseCadenceValue,
      earlyCheckValue,
      earlyTriggerValue,
      manualRunItem,
      nextScheduledRetrainTs,
      retrainHourValue,
      triggerProgressCurrent,
      triggerProgressTarget,
      triggerProgressValue,
      trainingSummary?.total_runs
    ]
  )
  const trainingCycleColumns = useMemo(
    () => splitIntoColumns(trainingCycleStats, 2),
    [trainingCycleStats]
  )
  const trainingCycleDisplayStats = useMemo<CompactStatItem[]>(
    () =>
      trainingCycleStats.map((item) => ({
        ...item,
        label: trainingCycleDisplayLabel(item.label)
      })),
    [trainingCycleStats]
  )
  const exitGuardStats = useMemo<CompactStatItem[]>(
    () => [
      {
        label: 'Mode',
        value: botState.mode === 'live' ? 'Live' : 'Shadow',
        color: botState.mode === 'live' ? theme.red : theme.blue
      },
      {label: 'Audits 7d', value: formatCount(exitAuditSummary?.audits_7d)},
      {
        label: 'Exit / hold',
        value: `${formatCount(exitAuditSummary?.exits_7d)} / ${formatCount(exitAuditSummary?.holds_7d)}`,
        color: theme.white
      },
      {
        label: 'Hard exits',
        value: formatCount(exitAuditSummary?.hard_exits_7d),
        color: Number(exitAuditSummary?.hard_exits_7d || 0) > 0 ? theme.red : theme.dim
      },
      {
        label: 'Avg breach',
        value: formatPct(exitAuditSummary?.avg_estimated_return_pct, 1),
        color: signedMetricColor(exitAuditSummary?.avg_estimated_return_pct)
      },
      {
        label: 'Exit breach',
        value: formatPct(exitAuditSummary?.avg_exit_return_pct, 1),
        color: signedMetricColor(exitAuditSummary?.avg_exit_return_pct)
      },
      {
        label: 'Hold breach',
        value: formatPct(exitAuditSummary?.avg_hold_return_pct, 1),
        color: signedMetricColor(exitAuditSummary?.avg_hold_return_pct)
      },
      {
        label: 'Avg spread',
        value: formatPct(exitAuditSummary?.avg_spread_pct, 1),
        color: lowerIsBetterColor(exitAuditSummary?.avg_spread_pct, 0.03, 0.06)
      },
      {
        label: 'Avg depth',
        value: formatNumber(exitAuditSummary?.avg_depth_multiple, 2),
        color: depthMultipleColor(exitAuditSummary?.avg_depth_multiple)
      },
      {
        label: 'Last audit',
        value: exitAuditSummary?.last_audited_at ? secondsAgo(exitAuditSummary.last_audited_at) : '-'
      },
      {
        label: 'Resolved exits',
        value: formatCount(exitAttribution?.resolved_exits_30d)
      },
      {
        label: 'Exit alpha',
        value: formatDollar(exitAttribution?.total_exit_alpha_usd),
        color: dollarColor(exitAttribution?.total_exit_alpha_usd)
      },
      {
        label: 'Saved / gave up',
        value: `${formatDollar(exitAttribution?.dollars_saved_usd)} / ${formatDollar(exitAttribution?.dollars_given_up_usd)}`,
        color: theme.white
      },
      {
        label: 'Helped / hurt',
        value: `${formatCount(exitAttribution?.exit_helped_count)} / ${formatCount(exitAttribution?.exit_hurt_count)}`,
        color: theme.white
      },
      {
        label: 'Avg delta',
        value: formatDollar(exitAttribution?.avg_exit_delta_usd),
        color: dollarColor(exitAttribution?.avg_exit_delta_usd)
      }
    ],
    [botState.mode, exitAttribution, exitAuditSummary]
  )
  const confusionCells = useMemo(
    () => [
      {label: 'TP', value: Math.max(0, Number(confusion?.true_positive || 0)), kind: 'good' as const},
      {label: 'FP', value: Math.max(0, Number(confusion?.false_positive || 0)), kind: 'bad' as const},
      {label: 'TN', value: Math.max(0, Number(confusion?.true_negative || 0)), kind: 'good' as const},
      {label: 'FN', value: Math.max(0, Number(confusion?.false_negative || 0)), kind: 'bad' as const}
    ],
    [confusion]
  )
  const confusionScale = useMemo(
    () => Math.max(1, ...confusionCells.map((cell) => cell.value)),
    [confusionCells]
  )
  const calibrationBias = useMemo(
    () => (
      calibration?.avg_confidence != null && calibration?.actual_win_rate != null
        ? calibration.avg_confidence - calibration.actual_win_rate
        : null
    ),
    [calibration?.actual_win_rate, calibration?.avg_confidence]
  )
  const mainCalibrationBucket = useMemo(
    () => calibrationRows.reduce<CalibrationRow | null>((best, row) => (best == null || row.n > best.n ? row : best), null),
    [calibrationRows]
  )
  const confidenceCheckStats = useMemo<CompactStatItem[]>(
    () => [
      {label: 'Resolved', value: formatCount(calibration?.resolved)},
      {
        label: 'Predicted',
        value: formatPct(calibration?.avg_confidence, 1),
        color: calibration?.avg_confidence != null ? probabilityColor(calibration.avg_confidence) : theme.dim
      },
      {
        label: 'Actual',
        value: formatPct(calibration?.actual_win_rate, 1),
        color: calibration?.actual_win_rate != null ? probabilityColor(calibration.actual_win_rate) : theme.dim
      },
      {
        label: 'Read',
        value: calibrationRead(calibrationBias),
        color: biasColor(calibrationBias)
      },
      {
        label: 'Bias',
        value: formatPointDelta(calibrationBias, 1),
        color: biasColor(calibrationBias)
      },
      {
        label: 'Avg miss',
        value: formatPct(calibration?.avg_gap, 1),
        color: lowerIsBetterColor(calibration?.avg_gap, 0.12, 0.2)
      },
      {
        label: 'Main band',
        value: mainCalibrationBucket ? bucketLabel(mainCalibrationBucket.bucket) : '-',
        color: theme.white
      },
      {
        label: 'Band hit',
        value: mainCalibrationBucket ? formatPct(mainCalibrationBucket.actual_win_rate, 1) : '-',
        color: mainCalibrationBucket?.actual_win_rate != null ? probabilityColor(mainCalibrationBucket.actual_win_rate) : theme.dim
      },
      {
        label: 'Band size',
        value: mainCalibrationBucket ? formatCount(mainCalibrationBucket.n) : '-',
        color: theme.white
      }
    ],
    [
      calibration?.actual_win_rate,
      calibration?.avg_confidence,
      calibration?.avg_gap,
      calibration?.resolved,
      calibrationBias,
      mainCalibrationBucket
    ]
  )
  const confidenceCheckColumns = useMemo(
    () => splitIntoColumns(confidenceCheckStats, 2),
    [confidenceCheckStats]
  )
  const loadedScorerLabel = botState.loaded_scorer ? modeLabel(botState.loaded_scorer) : 'Heuristic'
  const loadedScorerColor = loadedScorerLabel === 'XGBoost'
    ? theme.green
    : loadedScorerLabel === 'No scorer'
      ? theme.red
      : theme.yellow
  const scorerConfigLabel = useMemo(() => {
    const heuristicEnabled = botState.heuristic_enabled
    const xgboostEnabled = botState.xgboost_enabled
    if (heuristicEnabled == null || xgboostEnabled == null) return '-'
    const heuristicLabel = heuristicEnabled ? 'on' : 'off'
    const xgboostLabel = xgboostEnabled ? 'on' : 'off'
    return `heur ${heuristicLabel} | xgb ${xgboostLabel}`
  }, [botState.heuristic_enabled, botState.xgboost_enabled])
  const scorerConfigColor = useMemo(() => {
    const heuristicEnabled = botState.heuristic_enabled
    const xgboostEnabled = botState.xgboost_enabled
    if (heuristicEnabled == null || xgboostEnabled == null) return theme.dim
    if (heuristicEnabled === false && xgboostEnabled === false) return theme.red
    if (heuristicEnabled === true && xgboostEnabled === true) return theme.green
    return theme.yellow
  }, [botState.heuristic_enabled, botState.xgboost_enabled, theme.dim, theme.green, theme.red, theme.yellow])
  const deployedModelLabel = botState.model_artifact_exists
    ? modeLabel(botState.model_artifact_backend || 'unknown')
    : '-'
  const deployedModelColor = !botState.model_artifact_exists
    ? theme.dim
    : botState.model_runtime_compatible
      ? theme.green
      : theme.yellow
  const contractLabel = (
    botState.model_artifact_contract != null && botState.runtime_contract != null
      ? `${botState.model_artifact_contract} / ${botState.runtime_contract}`
      : '-'
  )
  const contractColor = !botState.model_artifact_exists
    ? theme.dim
    : botState.model_runtime_compatible
      ? theme.green
      : theme.red
  const fallbackLabel = useMemo(() => {
    const reason = String(botState.model_fallback_reason || '').trim().toLowerCase()
    if (!reason) return '-'
    if (reason === 'missing_artifact') return 'No artifact'
    if (reason === 'contract_mismatch') return 'Contract mismatch'
    if (reason === 'label_mode_mismatch') return 'Label mismatch'
    if (reason === 'legacy_artifact_type') return 'Legacy artifact'
    if (reason === 'load_failed') return 'Load failed'
    return reason.replace(/_/g, ' ')
  }, [botState.model_fallback_reason])
  const fallbackColor = fallbackLabel === '-'
    ? theme.dim
    : fallbackLabel === 'No artifact'
      ? theme.yellow
      : theme.red
  const recentActiveMode = useMemo(
    () =>
      recentSignalModes.reduce<string | null>(
        (best, row) => {
          if (best == null) return row.mode
          const currentBest = recentSignalModes.find((candidate) => candidate.mode === best)
          return (currentBest?.taken || 0) >= row.taken ? best : row.mode
        },
        null
      ),
    [recentSignalModes]
  )
  const activeScorerLabel = recentActiveMode ? modeLabel(recentActiveMode) : loadedScorerLabel
  const activeScorerColor = activeScorerLabel === 'XGBoost' ? theme.green : theme.yellow
  const primaryMode = useMemo(
    () =>
      signalModes.reduce<string | null>(
        (best, row) => {
          if (best == null) return row.mode
          const currentBest = signalModes.find((candidate) => candidate.mode === best)
          return (currentBest?.taken || 0) >= row.taken ? best : row.mode
        },
        null
      ),
    [signalModes]
  )
  const signalModeCards = useMemo<ModePreviewCard[]>(
    () =>
      signalModes
        .filter((row) => row.mode.trim().toLowerCase() !== 'veto')
        .map((row) => {
        const modeWinRate = ratio(row.wins, row.resolved)
        const modeUseRate = ratio(row.taken, row.signals)
        return {
          title: modeLabel(row.mode),
          titleColor: row.mode.trim().toLowerCase() === primaryMode ? theme.accent : theme.white,
          rows: [
            {
              label: 'Role',
              value: modeRoleLabel(row.mode, row.taken, primaryMode, activeScorerLabel),
              color: row.taken > 0 ? theme.accent : theme.dim
            },
            {
              label: 'Signals / taken',
              value: `${formatCount(row.signals)} / ${formatCount(row.taken)}`,
              color: theme.white
            },
            {
              label: 'Use / win',
              value: `${formatPct(modeUseRate, 1)} / ${formatPct(modeWinRate, 1)}`,
              color: modeWinRate != null ? probabilityColor(modeWinRate) : theme.dim
            },
            {
              label: 'Avg edge',
              value: formatPct(row.avg_edge, 1),
              color: signedMetricColor(row.avg_edge)
            },
            {
              label: 'P&L',
              value: formatDollar(row.total_pnl),
              color: dollarColor(row.total_pnl)
            }
          ]
        }
        }),
    [activeScorerLabel, primaryMode, signalModes]
  )
  const signalModeCardColumns = useMemo(
    () => splitIntoColumns(signalModeCards, combinedPanelsWide && signalModeCards.length > 1 ? 2 : 1),
    [combinedPanelsWide, signalModeCards]
  )
  const recentExitAudits = useMemo(
    () => recentExitAuditRows.slice(0, exitAuditHistoryLimit),
    [exitAuditHistoryLimit, recentExitAuditRows]
  )
  const baseHeuristicScore = useMemo(
    () => (
      flow?.trader_score != null && flow?.market_score != null
        ? (Math.max(flow.trader_score, 0) ** 0.6) * (Math.max(flow.market_score, 0) ** 0.4)
        : null
    ),
    [flow?.market_score, flow?.trader_score]
  )
  const finalHeuristicEstimate = useMemo(
    () => (
      baseHeuristicScore != null && flow?.belief_prior != null && flow?.belief_blend != null
        ? ((1 - flow.belief_blend) * baseHeuristicScore) + (flow.belief_blend * flow.belief_prior)
        : null
    ),
    [baseHeuristicScore, flow?.belief_blend, flow?.belief_prior]
  )
  const priorPull = useMemo(
    () => (
      finalHeuristicEstimate != null && baseHeuristicScore != null
        ? finalHeuristicEstimate - baseHeuristicScore
        : null
    ),
    [baseHeuristicScore, finalHeuristicEstimate]
  )
  const scoringMixStats = useMemo<CompactStatItem[]>(
    () => [
      {
        label: 'Trader input',
        value: formatPct(flow?.trader_score, 1),
        color: flow?.trader_score != null ? probabilityColor(flow.trader_score) : theme.dim
      },
      {
        label: 'Market input',
        value: formatPct(flow?.market_score, 1),
        color: flow?.market_score != null ? probabilityColor(flow.market_score) : theme.dim
      },
      {
        label: 'Base mix',
        value: formatPct(baseHeuristicScore, 1),
        color: baseHeuristicScore != null ? probabilityColor(baseHeuristicScore) : theme.dim
      },
      {
        label: 'Final estimate',
        value: formatPct(finalHeuristicEstimate, 1),
        color: finalHeuristicEstimate != null ? probabilityColor(finalHeuristicEstimate) : theme.dim
      },
      {
        label: 'History prior',
        value: formatPct(flow?.belief_prior, 1),
        color: flow?.belief_prior != null ? probabilityColor(flow.belief_prior) : theme.dim
      },
      {
        label: 'History weight',
        value: formatPct(flow?.belief_blend, 1),
        color: flow?.belief_blend != null ? probabilityColor(flow.belief_blend) : theme.dim
      },
      {
        label: 'History nudge',
        value: formatPointDelta(priorPull, 1),
        color: theme.blue
      },
      {
        label: 'Evidence',
        value: formatNumber(flow?.belief_evidence, 0),
        color: theme.white
      }
    ],
    [
      baseHeuristicScore,
      finalHeuristicEstimate,
      flow?.belief_blend,
      flow?.belief_evidence,
      flow?.belief_prior,
      flow?.market_score,
      flow?.trader_score,
      priorPull
    ]
  )
  const scoringMixColumns = useMemo(
    () => splitIntoColumns(scoringMixStats, 2),
    [scoringMixStats]
  )
  const modelsColumnGap = terminal.compact ? 1 : 2
  const modelsContentWidth = Math.max(50, terminal.width - 10)
  const modelsUsableWidth = Math.max(18, modelsContentWidth - modelsColumnGap * 2)
  const leftModelsColumnWidth = Math.max(18, Math.floor(modelsUsableWidth * 0.26))
  const middleModelsColumnWidth = Math.max(18, Math.floor(modelsUsableWidth * 0.27))
  const modelsColumnWidths: [number, number, number] = [
    leftModelsColumnWidth,
    middleModelsColumnWidth,
    Math.max(18, modelsUsableWidth - leftModelsColumnWidth - middleModelsColumnWidth)
  ]
  const predictionQualityStats = useMemo<CompactStatItem[]>(
    () => [
      {
        label: 'Scorer gates',
        value: scorerConfigLabel,
        color: scorerConfigColor
      },
      {
        label: 'Loaded scorer',
        value: loadedScorerLabel,
        color: loadedScorerColor
      },
      {
        label: 'Model artifact',
        value: deployedModelLabel,
        color: deployedModelColor
      },
      {label: 'Contract', value: contractLabel, color: contractColor},
      {label: 'Fallback', value: fallbackLabel, color: fallbackColor},
      {label: 'Trained', value: latest ? formatShortDateTime(latest.trained_at) : '-'},
      {label: 'Model age', value: latest ? secondsAgo(latest.trained_at) : '-'},
      {label: 'Samples', value: formatCount(latest?.n_samples)},
      {label: 'Features', value: featureCount != null ? formatCount(featureCount) : '-'},
      {
        label: 'Brier score',
        value: formatNumber(latest?.brier_score, 4),
        color: lowerIsBetterColor(latest?.brier_score, 0.18, 0.25)
      },
      {
        label: 'Log loss',
        value: formatNumber(latest?.log_loss, 4),
        color: lowerIsBetterColor(latest?.log_loss, 0.55, 0.69)
      }
    ],
    [
      contractColor,
      contractLabel,
      deployedModelColor,
      deployedModelLabel,
      fallbackColor,
      fallbackLabel,
      featureCount,
      latest,
      scorerConfigColor,
      scorerConfigLabel,
      loadedScorerColor,
      loadedScorerLabel
    ]
  )
  const recentRetrainRuns = useMemo(
    () => retrainRuns.slice(0, historyLimit),
    [historyLimit, retrainRuns]
  )
  const howItWorksScoreRows = useMemo(
    () => scoringMixStats.slice(0, 4),
    [scoringMixStats]
  )
  const howItWorksHistoryRows = useMemo(
    () => scoringMixStats.slice(4),
    [scoringMixStats]
  )

  const renderPageBody = () => (
    <InkBox width="100%">
      <InkBox width={modelsColumnWidths[0]} flexDirection="column">
        <ModelsSectionTitle
          title="Prediction Quality"
          width={modelsColumnWidths[0]}
          selected={clampedSelectedPanelIndex === 0}
          backgroundColor={selectedRowBackground}
        />
        {predictionQualityStats.map((item) => (
          <DenseModelsRow
            key={item.label}
            label={item.label}
            value={item.value}
            color={item.color ?? theme.white}
            width={modelsColumnWidths[0]}
            labelWidth={14}
          />
        ))}
        <ModelsSpacer />
        <ModelsSectionTitle
          title="Tracker Health"
          width={modelsColumnWidths[0]}
          selected={clampedSelectedPanelIndex === 1}
          backgroundColor={selectedRowBackground}
        />
        {trackerHealthStats.map((item) => (
          <DenseModelsRow
            key={item.label}
            label={item.label}
            value={item.value}
            color={item.color ?? theme.white}
            width={modelsColumnWidths[0]}
            labelWidth={14}
          />
        ))}
        <ModelsSpacer />
        <ModelsSectionTitle
          title="Replay Lab"
          width={modelsColumnWidths[0]}
          selected={clampedSelectedPanelIndex === 2}
          backgroundColor={selectedRowBackground}
        />
        {replayLabStats.map((item) => (
          <DenseModelsRow
            key={item.label}
            label={item.label}
            value={item.value}
            color={item.color ?? theme.white}
            width={modelsColumnWidths[0]}
            labelWidth={14}
          />
        ))}
      </InkBox>

      <InkBox width={modelsColumnGap} />

      <InkBox width={modelsColumnWidths[1]} flexDirection="column">
        <ModelsSectionTitle
          title="Confidence + Modes"
          width={modelsColumnWidths[1]}
          selected={clampedSelectedPanelIndex === 3}
          backgroundColor={selectedRowBackground}
        />
        {confusionCells.map((cell) => (
          <DenseModelsRow
            key={cell.label}
            label={cell.label}
            value={formatCount(cell.value)}
            color={confusionHeatColor(cell.value, confusionScale, cell.kind)}
            width={modelsColumnWidths[1]}
            labelWidth={12}
          />
        ))}
        <ModelsSpacer />
        <ModelsSubsectionTitle title="Calibration" width={modelsColumnWidths[1]} />
        {confidenceCheckStats.map((item) => (
          <DenseModelsRow
            key={item.label}
            label={item.label}
            value={item.value}
            color={item.color ?? theme.white}
            width={modelsColumnWidths[1]}
            labelWidth={12}
          />
        ))}
        <ModelsSpacer />
        <ModelsSubsectionTitle title="Decision Paths" width={modelsColumnWidths[1]} />
        <DenseModelsRow
          label="Recent path"
          value={activeScorerLabel}
          color={activeScorerColor}
          width={modelsColumnWidths[1]}
          labelWidth={12}
        />
        <DenseModelsRow
          label="Primary path"
          value={primaryMode ? modeLabel(primaryMode) : '-'}
          color={primaryMode ? theme.accent : theme.dim}
          width={modelsColumnWidths[1]}
          labelWidth={12}
        />
        {signalModeCards.length ? (
          signalModeCards.map((card, index) => (
            <React.Fragment key={card.title}>
              <ModelsSpacer />
              <ModelsSubsectionTitle
                title={card.title}
                width={modelsColumnWidths[1]}
                color={card.titleColor ?? theme.white}
              />
              {card.rows.map((item) => (
                <DenseModelsRow
                  key={`${card.title}-${item.label}`}
                  label={item.label}
                  value={item.value}
                  color={item.color ?? theme.white}
                  width={modelsColumnWidths[1]}
                  labelWidth={12}
                />
              ))}
            </React.Fragment>
          ))
        ) : (
          <Text color={theme.dim}>{fit('No tracker signals yet.', modelsColumnWidths[1])}</Text>
        )}
        <ModelsSpacer />
        <ModelsSectionTitle
          title="Exit Guard"
          width={modelsColumnWidths[1]}
          selected={clampedSelectedPanelIndex === 4}
          backgroundColor={selectedRowBackground}
        />
        {exitGuardStats.map((item) => (
          <DenseModelsRow
            key={item.label}
            label={item.label}
            value={item.value}
            color={item.color ?? theme.white}
            width={modelsColumnWidths[1]}
            labelWidth={12}
          />
        ))}
        <ModelsSpacer />
        <RecentExitHeaderRow width={modelsColumnWidths[1]} />
        {recentExitAudits.length ? (
          recentExitAudits.map((row, index) => (
            <RecentExitDataRow
              key={`${row.audited_at}-${row.decision || 'decision'}-${index}`}
              width={modelsColumnWidths[1]}
              timestamp={formatShortDateTime(row.audited_at)}
              timestampColor={theme.dim}
              action={exitDecisionLabel(row.decision, row.reason)}
              actionColor={exitDecisionColor(row.decision, row.reason)}
              estimatedReturn={formatPct(row.estimated_return_pct, 1)}
              estimatedReturnColor={signedMetricColor(row.estimated_return_pct)}
              reason={String(row.reason || '').trim() || '-'}
              reasonColor={theme.dim}
            />
          ))
        ) : (
          <Text color={theme.dim}>{fit('No exit audits logged yet.', modelsColumnWidths[1])}</Text>
        )}
      </InkBox>

      <InkBox width={modelsColumnGap} />

      <InkBox width={modelsColumnWidths[2]} flexDirection="column">
        <ModelsSectionTitle
          title="How It Works"
          width={modelsColumnWidths[2]}
          selected={clampedSelectedPanelIndex === 5}
          backgroundColor={selectedRowBackground}
        />
        {howItWorksScoreRows.map((item) => (
          <DenseModelsRow
            key={item.label}
            label={item.label}
            value={item.value}
            color={item.color ?? theme.white}
            width={modelsColumnWidths[2]}
            labelWidth={14}
          />
        ))}
        <ModelsSpacer />
        <ModelsSpacer />
        <ModelsSubsectionTitle title="History Nudge" width={modelsColumnWidths[2]} />
        {howItWorksHistoryRows.map((item) => (
          <DenseModelsRow
            key={item.label}
            label={item.label}
            value={item.value}
            color={item.color ?? theme.white}
            width={modelsColumnWidths[2]}
            labelWidth={14}
          />
        ))}
        <ModelsSpacer />
        <ModelsSectionTitle
          title="Training Cycle"
          width={modelsColumnWidths[2]}
          selected={clampedSelectedPanelIndex === 6}
          backgroundColor={selectedRowBackground}
        />
        {trainingCycleDisplayStats.map((item) => (
          <DenseModelsRow
            key={item.label}
            label={item.label}
            value={item.value}
            color={item.color ?? theme.white}
            selected={clampedSelectedPanelIndex === 6 && item.label === 'Manual run'}
            backgroundColor={selectedRowBackground}
            width={modelsColumnWidths[2]}
            labelWidth={11}
            minValueWidth={16}
          />
        ))}

        {latestSharedHoldoutRun && latestSharedHoldout ? (
          <>
            <DenseModelsRow
              label="Holdout gate"
              value={sharedHoldoutGateReadCompact(latestSharedHoldoutRun)}
              color={sharedHoldoutGateReadColor(latestSharedHoldoutRun)}
              width={modelsColumnWidths[2]}
              labelWidth={11}
              minValueWidth={16}
            />
            <DenseModelsRow
              label="Gate run"
              value={formatShortDateTime(latestSharedHoldoutRun.finished_at)}
              width={modelsColumnWidths[2]}
              labelWidth={11}
              minValueWidth={16}
            />
            <DenseModelsRow
              label="LL c / i"
              value={`${formatNumber(latestSharedHoldout.challenger_log_loss, 4)} / ${formatNumber(latestSharedHoldout.incumbent_log_loss, 4)}`}
              color={sharedHoldoutGateReadColor(latestSharedHoldoutRun)}
              width={modelsColumnWidths[2]}
              labelWidth={11}
              minValueWidth={16}
            />
            <DenseModelsRow
              label="Brier c / i"
              value={`${formatNumber(latestSharedHoldout.challenger_brier_score, 4)} / ${formatNumber(latestSharedHoldout.incumbent_brier_score, 4)}`}
              color={sharedHoldoutGateReadColor(latestSharedHoldoutRun)}
              width={modelsColumnWidths[2]}
              labelWidth={11}
              minValueWidth={16}
            />
          </>
        ) : null}
        <ModelsSpacer />
        <RecentRunsHeaderRow width={modelsColumnWidths[2]} />
        {recentRetrainRuns.length ? (
          recentRetrainRuns.map((row, index) => (
            <RecentRunsDataRow
              key={`${row.finished_at}-${row.status || 'run'}-${index}`}
              width={modelsColumnWidths[2]}
              timestamp={formatShortDateTime(row.finished_at)}
              timestampColor={theme.dim}
              logLoss={formatNumber(row.log_loss, 3)}
              logLossColor={lowerIsBetterColor(row.log_loss, 0.55, 0.69)}
              brier={formatNumber(row.brier_score, 3)}
              brierColor={lowerIsBetterColor(row.brier_score, 0.18, 0.25)}
              result={retrainRunStateCompactLabel(row.status, row.deployed)}
              resultColor={retrainRunStateColor(row.status, row.deployed)}
            />
          ))
        ) : (
          <Text color={theme.dim}>{fit('No retrain attempts logged yet.', modelsColumnWidths[2])}</Text>
        )}
      </InkBox>
    </InkBox>
  )

  return (
    <InkBox flexDirection="column" width="100%">
      {renderPageBody()}
      {detailOpen ? (
        <ModalOverlay backgroundColor={terminal.backgroundColor}>
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
              <Text key={line} color={theme.white} backgroundColor={modalBackground}>
                {` ${fit(line, helpContentWidth)} `}
              </Text>
            ))}

            <Text backgroundColor={modalBackground}>{helpSpacerLine}</Text>

            <InkBox flexDirection="column">
              <Text color={theme.accent} backgroundColor={modalBackground} bold>
                {` ${fit('Label Guide', helpContentWidth)} `}
              </Text>
              {selectedPanel.rows.map((row) => (
                <Text key={`${selectedPanel.id}-${row.label}`} color={theme.white} backgroundColor={modalBackground}>
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
                    const rowBackground = selected ? selectedRowBackground : modalBackground
                    return (
                      <InkBox key={`${selectedPanel.id}-${field.key}`} width="100%">
                        <Text color={selected ? theme.accent : theme.dim} backgroundColor={rowBackground} bold={selected}>
                          {` ${fit(label, helpSettingLabelWidth)}`}
                        </Text>
                        <Text backgroundColor={rowBackground}> </Text>
                        <Text color={theme.white} backgroundColor={rowBackground} bold={selected}>
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
        </ModalOverlay>
      ) : null}
    </InkBox>
  )
}
