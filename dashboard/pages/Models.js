import React, { useEffect, useMemo, useState } from 'react';
import { Box as InkBox, Text } from 'ink';
import { ModalOverlay } from '../components/ModalOverlay.js';
import { editableConfigFields, formatEditableConfigValue } from '../configEditor.js';
import { fit, fitRight, formatDollar, formatNumber, formatPct, formatShortDateTime, secondsAgo, timeUntil } from '../format.js';
import { stackPanels } from '../responsive.js';
import { useTerminalSize } from '../terminal.js';
import { centeredGradientColor, negativeHeatColor, positiveDollarColor, probabilityColor, selectionBackgroundColor, theme } from '../theme.js';
import { useBotState } from '../useBotState.js';
import { useQuery } from '../useDb.js';
function isDefined(value) {
    return value !== undefined;
}
export const MODEL_PANEL_DEFS = [
    {
        id: 'prediction_quality',
    title: 'Prediction Quality',
    summary: [
        'This box separates scorer config, the scorer loaded in the runtime, and the model artifact on disk.',
        'Lower loss numbers grade the model artifact, not the bankroll curve.'
    ],
    rows: [
        { label: 'Scorer gates', text: 'Which scorer paths are enabled in config. Disabled paths cannot be loaded for new decisions.' },
        { label: 'Loaded scorer', text: 'Which scorer the running bot has loaded right now for new decisions.' },
        { label: 'Model artifact', text: 'The latest deployed model artifact on disk, even if live trading is currently falling back.' },
            { label: 'Contract', text: 'Artifact contract versus runtime contract. A mismatch means the runtime will reject the model.' },
            { label: 'Fallback', text: 'Why the runtime is using heuristics instead of the model, if it is degraded.' },
            { label: 'Trained', text: 'When the latest deployed model artifact was built.' },
            { label: 'Model age', text: 'How long that deployed model artifact has been sitting without a retrain.' },
            { label: 'Samples', text: 'How many resolved trades were available to train the deployed model artifact.' },
            { label: 'Features', text: 'How many inputs the deployed model artifact is using.' },
            { label: 'Brier score', text: 'Average probability error. Lower is better.' },
            { label: 'Log loss', text: 'Penalizes confident wrong calls much harder. Lower is better.' }
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
            { label: 'Signals logged', text: 'Candidate trades the bot observed before filtering.' },
            { label: 'Bets taken', text: 'Signals that passed checks and became tracker bets.' },
            { label: 'Use rate', text: 'Accepted bets divided by total signals. Lower means stricter filtering.' },
            { label: 'Win rate', text: 'Resolved tracker bets that finished as wins.' },
            { label: 'Avg confidence', text: 'Average predicted win probability on accepted bets.' },
            { label: 'Avg edge', text: 'Confidence minus price. Positive means the bot saw value.' },
            { label: 'Tracker P&L', text: 'Cumulative paper profit from accepted bets.' },
            { label: 'Sharpe ratio', text: 'Return compared to volatility. Higher is smoother.' }
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
            { label: 'Last replay', text: 'When the latest completed replay finished.' },
            { label: 'Policy', text: 'Replay label and shortened policy version hash for the latest run.' },
            { label: 'Replay P&L', text: 'Total replay profit versus the replay starting bankroll.' },
            { label: 'Max DD', text: 'Largest peak-to-trough drawdown in the replay bankroll curve.' },
            { label: 'Accept / win', text: 'Accepted replay trades and realized win rate on the resolved subset.' },
            { label: 'Search run', text: 'How recently the latest persisted replay search finished.' },
            { label: 'Search fea/rej', text: 'Feasible versus rejected candidate count from the latest replay search run.' },
            { label: 'Best search', text: 'Score and candidate index for the latest best feasible replay-search result.' },
            { label: 'Score weights', text: 'Active replay-search score weights on the latest search run, including drawdown, instability, worst-window loss, peak open-exposure, window-end carry exposure, carry-window frequency, carry-restart continuity, accepting-window count/share, accepting-window drought streaks and repeated drought episodes, accepting-window concentration, scorer accepting-window count/share and droughts, count and deployed-dollar accepting-window depth, daily/live guard frequency and restart continuity, count-weighted and deployed-dollar coverage, global and worst-window count-weighted and deployed-dollar coverage, scorer-path count-weighted and deployed-dollar coverage and depth, scorer accepting-window mix, and count/share plus deployed-dollar concentration terms.' },
            { label: 'Best score', text: 'Best feasible score decomposition: replay P&L minus drawdown, instability, worst-window loss, peak open-exposure, window-end carry exposure, carry-window frequency, carry-restart continuity, accepting-window count/share, accepting-window droughts, accepting-window concentration, scorer accepting-window count/share, count and deployed-dollar accepting-window depth, daily/live guard frequency and restart continuity, count-weighted and deployed-dollar coverage, worst-window count-weighted and deployed-dollar coverage, scorer-path count-weighted and deployed-dollar coverage and depth, scorer-loss, scorer-inactivity, and concentration penalties.' },
            { label: 'Search robust', text: 'Best feasible search candidate P&L and drawdown.' },
            { label: 'Search windows', text: 'Positive versus negative windows, active versus idle participation, fresh-entry accepting-window count/share, maximum and repeated stitched active-window accepting droughts, top and overall stitched accepting-window trade-share and deployed-dollar concentration, carry-window frequency, carry-restart continuity risk, sparsest accepting-window trade depth and deployed dollars, peak and average window-end carry exposure, and the worst window P&L for the latest best feasible search candidate.' },
            { label: 'Cfg drift', text: 'How many editable config keys currently differ from the best feasible replay-search recommendation.' },
            { label: 'Suggest cfg', text: 'Compact summary of the recommended config values from the latest best feasible replay-search candidate.' },
            { label: 'Apply scope', text: 'How many recommended config changes apply live on the next loop versus requiring a restart, plus any replay-only leftovers.' },
            { label: 'Deploy gap', text: 'Recommendation pieces not currently present in the persisted editable-config payload for the latest best feasible candidate. Older search rows may need a rerun after config-surface changes.' },
            { label: 'Seg gates', text: 'Entry-price-band, holding-horizon, and scorer-path gates on the latest best feasible replay-search candidate.' },
            { label: 'Wallet conc', text: 'Best and current replay-search dependence on wallets, shown as distinct wallet count, top accepted-share, top deployed-dollar share, top absolute-P&L share, and any active floor, cap, or score penalty.' },
            { label: 'Market conc', text: 'Best and current replay-search dependence on markets, shown as distinct market count, top accepted-share, top deployed-dollar share, top absolute-P&L share, and any active floor, cap, or score penalty.' },
            { label: 'Entry conc', text: 'Best and current replay-search dependence on entry-price bands, shown as distinct band count, top accepted-share, top deployed-dollar share, top absolute-P&L share, and any active floor, cap, or score penalty.' },
            { label: 'Horizon conc', text: 'Best and current replay-search dependence on time-to-close bands, shown as distinct band count, top accepted-share, top deployed-dollar share, top absolute-P&L share, and any active floor, cap, or score penalty.' },
            { label: 'Pause guard', text: 'Replay-search dependence on daily-loss/live-drawdown rejects, active windows that still end with daily/live guard state tripped, and later stitched participation windows that resume after those guard-tripped windows, including across inactive gaps.' },
            { label: 'Search modes', text: 'Accepted trade mix and deployed-dollar mix, scorer accepting-window count/share, accepting-window drought streaks and episodes, stitched accepting-window concentration, plus count-weighted and deployed-dollar resolved coverage and replay P&L by scorer on the latest best feasible replay-search candidate.' },
            { label: 'Cur evidence', text: 'Current/base scorer accepted trade mix and deployed-dollar mix, scorer accepting-window count/share, accepting-window drought streaks and episodes, and per-scorer stitched accepting-window concentration, plus count-weighted and deployed-dollar resolved evidence and replay P&L.' },
            { label: 'Mode guard', text: 'Per-scorer accepted-count, positive-window count, inactive-window count, accepting-window count/share and concentration, resolved-count, count-weighted and deployed-dollar resolved-share, win-rate, total P&L, worst-window P&L, worst-window count-weighted coverage, worst-window deployed-dollar coverage, and aggregate plus stitched accepting-window count-share and deployed-dollar-share guardrails from the latest replay search, if any.' },
            { label: 'Mode pen', text: 'Soft scorer-path ranking weights from the latest replay search, for scorer coverage, scorer deployed-dollar coverage, scorer worst-window count-weighted coverage, scorer worst-window deployed-dollar coverage, scorer accepting-window count/share, droughts, drought episodes, scorer accepting-window count depth, scorer accepting-window deployed-dollar depth, scorer accepting-window mix, scorer-loss, and scorer-inactivity pressure.' },
            { label: 'Best headroom', text: 'Closest active replay-search guard margins for the latest best feasible candidate, across global, heuristic, and model constraints.' },
            { label: 'Cur headroom', text: 'Closest active replay-search guard margins for the current/base candidate, across global, heuristic, and model constraints.' },
            { label: 'Mode drift', text: 'Best feasible scorer mix minus the current/base scorer mix, shown in accepted-share and deployed-dollar-share percentage points.' },
            { label: 'Cur mode risk', text: 'Current/base scorer-path breaches against the latest replay-search mode guardrails, or clear if none.' },
            { label: 'Cur fails', text: 'Exact replay-search feasibility failures for the current/base candidate, including non-scorer global failures.' },
            { label: 'Cur feasible', text: 'Whether the current/base config clears the replay-search feasibility gates, plus its replay P&L and drawdown.' },
            { label: 'Cur score', text: 'Current/base score decomposition: replay P&L minus drawdown, instability, worst-window loss, peak open-exposure, window-end carry exposure, carry-window frequency, carry-restart continuity, accepting-window count/share, accepting-window droughts, accepting-window concentration, scorer accepting-window count/share and droughts, daily/live guard frequency and restart continuity, count-weighted and deployed-dollar coverage, worst-window count-weighted and deployed-dollar coverage, global window inactivity, scorer-path count-weighted and deployed-dollar coverage and depth, scorer-loss, scorer-inactivity, and concentration penalties.' },
            { label: 'Score drift', text: 'Best feasible minus current/base score decomposition, split into replay P&L and each score penalty term, including carry-window frequency, carry-restart continuity, accepting-window count/share, accepting-window droughts, accepting-window concentration, scorer accepting-window count/share and droughts, daily/live guard frequency and restart continuity, count-weighted and deployed-dollar coverage, worst-window count-weighted and deployed-dollar coverage, inactivity, scorer-path count-weighted and deployed-dollar coverage/depth, and concentration penalties.' },
            { label: 'Cur regret', text: 'Best feasible minus current/base config, shown as replay P&L gap and score gap.' },
            { label: 'Best wallet', text: 'Wallet with the strongest replay P&L on the latest run, subject to the minimum resolved sample filter.' },
            { label: 'Worst wallet', text: 'Wallet with the weakest replay P&L on the latest run, subject to the minimum resolved sample filter.' },
            { label: 'Best band', text: 'Entry-price band with the strongest replay P&L on the latest run.' },
            { label: 'Worst band', text: 'Entry-price band with the weakest replay P&L on the latest run.' },
            { label: 'Best horizon', text: 'Time-to-close bucket with the strongest replay P&L on the latest run.' },
            { label: 'Worst horizon', text: 'Time-to-close bucket with the weakest replay P&L on the latest run.' }
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
            { label: 'TP / FP / TN / FN', text: 'Outcome split behind the confidence gate: accepted wins, accepted losses, helpful skips, and missed winners.' },
            { label: 'Resolved', text: 'Accepted tracker bets that already settled and can be graded.' },
            { label: 'Predicted / Actual', text: 'Average model confidence versus the realized win rate on those graded bets.' },
            { label: 'Read / Bias', text: 'Plain-English bias read plus the point gap between prediction and reality.' },
            { label: 'Avg miss', text: 'Average miss per graded bet versus the actual 0/1 outcome. Lower is better.' },
            { label: 'Main band / hit', text: 'The most common confidence range and how often it actually won.' },
            { label: 'Recent path', text: 'Which scorer has driven the most recent accepted trades. This is historical activity, not the runtime load state.' },
            { label: 'Primary path', text: 'Which decision path has produced the most accepted trades so far.' },
            { label: 'Role', text: 'Whether a path is primary, secondary, or currently idle.' },
            { label: 'Signals / taken', text: 'How many candidate signals flowed through that path, and how many became bets.' },
            { label: 'Use / win', text: 'Acceptance rate and settled win rate for that path.' },
            { label: 'Avg edge', text: 'Average confidence edge over price for accepted bets in that path.' },
            { label: 'P&L', text: 'Cumulative tracker profit from that path.' }
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
            { label: 'Mode', text: 'Whether these exit audits are coming from shadow or live mode.' },
            { label: 'Audits 7d', text: 'How many stop-loss breaches were evaluated in the last 7 days.' },
            { label: 'Exit / hold', text: 'How many breaches became immediate exits versus guarded holds.' },
            { label: 'Hard exits', text: 'Breaches so severe that the guard exited immediately despite quote-quality checks.' },
            { label: 'Avg breach', text: 'Average executable return at the moment the guard evaluated the position.' },
            { label: 'Exit breach', text: 'Average executable return for the subset the guard actually exited.' },
            { label: 'Hold breach', text: 'Average executable return for the subset the guard held.' },
            { label: 'Avg spread', text: 'Average quoted spread on audited positions. Lower is better.' },
            { label: 'Avg depth', text: 'Average visible bid depth relative to the position notional. Higher is better.' },
            { label: 'Last audit', text: 'How recently the exit guard evaluated a stop-loss breach.' },
            { label: 'Resolved exits', text: 'Exited trades from the last 30 days that have already fully resolved and can be compared to a no-exit baseline.' },
            { label: 'Exit alpha', text: 'Dollar delta between realized exit P&L and the hold-to-resolution baseline. Positive means exits helped.' },
            { label: 'Saved / gave up', text: 'Gross dollars preserved by good exits versus gross dollars forfeited by bad exits.' },
            { label: 'Helped / hurt', text: 'How many resolved exits beat the hold baseline versus underperformed it.' },
            { label: 'Avg delta', text: 'Average per-exit dollar delta versus hold-to-resolution on the resolved sample.' }
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
            { label: 'Trader input', text: 'Average trader quality score from behavior and results.' },
            { label: 'Market input', text: 'Average market quality score from spread, depth, time, and momentum.' },
            { label: 'Base mix', text: 'Weighted blend before history shifts it. Formula: trader^0.60 * market^0.40.' },
            { label: 'History prior', text: 'Average historical win prior for similar resolved trades.' },
            { label: 'History weight', text: 'How much the prior was allowed to move the base score.' },
            { label: 'Final estimate', text: 'Estimated final heuristic score after the prior adjustment.' },
            { label: 'History nudge', text: 'How many percentage points history moved the base score.' },
            { label: 'Evidence', text: 'Average number of prior examples backing that adjustment.' }
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
            { label: 'Update style', text: 'The system rebuilds the model from resolved trades each cycle.' },
            { label: 'Base cadence', text: 'Regular scheduled retrain frequency.' },
            { label: 'Run time', text: 'Local hour when the scheduled retrain is attempted.' },
            { label: 'Next scheduled', text: 'Next planned scheduled retrain window from the current cadence and local hour.' },
            { label: 'Scheduled in', text: 'Countdown until that scheduled retrain window.' },
            { label: 'Early check', text: 'How often the bot checks whether it should retrain sooner.' },
            { label: 'Early trigger', text: 'Minimum new labels needed to fire an unscheduled retrain.' },
            {
                label: 'Trigger progress',
                text: 'Progress toward the next retrain trigger. With a deployed model this counts new eligible labels since that model went live; before the first model it falls back to total labeled samples versus the minimum sample gate.'
            },
            { label: 'Manual run', text: 'Press t while this panel is selected to queue an in-process retrain through the running bot.' },
            { label: 'Shared gate', text: 'Latest apples-to-apples challenger versus incumbent comparison on the same final holdout. This is the actual deployment guardrail.' }
        ],
        settingKeys: ['RETRAIN_BASE_CADENCE', 'RETRAIN_HOUR_LOCAL', 'RETRAIN_EARLY_CHECK_INTERVAL', 'RETRAIN_MIN_NEW_LABELS', 'RETRAIN_MIN_SAMPLES']
    }
];
export const MODEL_PANEL_COLUMN_LAYOUT = [
    [0, 1, 2],
    [3, 4],
    [5, 6]
];
const EXECUTED_ENTRY_WHERE = `
skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
`;
const RESOLVED_EXECUTED_ENTRY_WHERE = `
${EXECUTED_ENTRY_WHERE}
AND COALESCE(actual_pnl_usd, shadow_pnl_usd) IS NOT NULL
`;
const PROFITABLE_TRADE_SQL = `CASE WHEN COALESCE(actual_pnl_usd, shadow_pnl_usd) > 0 THEN 1 ELSE 0 END`;
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
`;
const TRAINABLE_SKIPPED_REASON_WHERE = `
(
  LOWER(COALESCE(skip_reason, '')) LIKE 'signal confidence was %below the % minimum'
  OR LOWER(COALESCE(skip_reason, '')) LIKE 'confidence was %below the % minimum needed to place a trade'
  OR LOWER(COALESCE(skip_reason, '')) LIKE 'heuristic score was %below the % minimum needed to place a trade'
  OR LOWER(COALESCE(skip_reason, '')) LIKE 'model edge was %below the % threshold'
  OR LOWER(COALESCE(skip_reason, '')) = 'trade did not pass the signal checks'
  OR LOWER(COALESCE(skip_reason, '')) = 'kelly sizing found no positive edge at this price, so the trade was skipped'
)
`;
const RESOLVED_TRAINABLE_SKIPPED_BUY_WHERE = `
skipped=1
AND COALESCE(source_action, 'buy')='buy'
AND counterfactual_return IS NOT NULL
AND ${TRAINABLE_SKIPPED_REASON_WHERE}
`;
const RESOLVED_TRAINING_SAMPLE_WHERE = `
(
  ${RESOLVED_EXECUTED_ENTRY_WHERE}
)
OR
(
  ${RESOLVED_TRAINABLE_SKIPPED_BUY_WHERE}
)
`;
const MODEL_SQL = `
SELECT trained_at, n_samples, brier_score, log_loss, feature_cols, deployed
FROM model_history
ORDER BY trained_at DESC
LIMIT 12
`;
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
`;
const REPLAY_SEGMENT_MIN_RESOLVED = 3;
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
`;
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
`;
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
`;
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
    daily_guard_window_penalty,
    live_guard_window_penalty,
    daily_guard_restart_window_penalty,
    live_guard_restart_window_penalty,
    open_exposure_penalty,
    window_end_open_exposure_penalty,
    avg_window_end_open_exposure_penalty,
    carry_window_penalty,
    carry_restart_window_penalty,
    resolved_share_penalty,
    resolved_size_share_penalty,
    worst_window_resolved_share_penalty,
    worst_window_resolved_size_share_penalty,
    mode_resolved_share_penalty,
    mode_resolved_size_share_penalty,
    mode_worst_window_resolved_share_penalty,
    mode_worst_window_resolved_size_share_penalty,
    mode_active_window_accepted_share_penalty,
    mode_active_window_accepted_size_share_penalty,
    worst_active_window_accepted_penalty,
    worst_active_window_accepted_size_penalty,
    mode_worst_active_window_accepted_penalty,
    mode_worst_active_window_accepted_size_penalty,
    mode_loss_penalty,
    mode_inactivity_penalty,
    mode_accepted_window_count_penalty,
    mode_accepted_window_share_penalty,
    mode_non_accepting_active_window_streak_penalty,
    mode_non_accepting_active_window_episode_penalty,
    mode_accepting_window_accepted_share_penalty,
    mode_accepting_window_accepted_size_share_penalty,
    mode_top_two_accepting_window_accepted_share_penalty,
    mode_top_two_accepting_window_accepted_size_share_penalty,
    mode_accepting_window_accepted_concentration_index_penalty,
    mode_accepting_window_accepted_size_concentration_index_penalty,
    window_inactivity_penalty,
    accepted_window_count_penalty,
    accepted_window_share_penalty,
    non_accepting_active_window_streak_penalty,
    non_accepting_active_window_episode_penalty,
    accepting_window_accepted_share_penalty,
    accepting_window_accepted_size_share_penalty,
    top_two_accepting_window_accepted_share_penalty,
    top_two_accepting_window_accepted_size_share_penalty,
    accepting_window_accepted_concentration_index_penalty,
    accepting_window_accepted_size_concentration_index_penalty,
    wallet_count_penalty,
    market_count_penalty,
    entry_price_band_count_penalty,
    time_to_close_band_count_penalty,
    wallet_concentration_penalty,
    market_concentration_penalty,
    entry_price_band_concentration_penalty,
    time_to_close_band_concentration_penalty,
    wallet_size_concentration_penalty,
    market_size_concentration_penalty,
    entry_price_band_size_concentration_penalty,
    time_to_close_band_size_concentration_penalty
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
  latest_search.daily_guard_window_penalty,
  latest_search.live_guard_window_penalty,
  latest_search.daily_guard_restart_window_penalty,
  latest_search.live_guard_restart_window_penalty,
  latest_search.open_exposure_penalty,
  latest_search.window_end_open_exposure_penalty,
  latest_search.avg_window_end_open_exposure_penalty,
  latest_search.carry_window_penalty,
  latest_search.carry_restart_window_penalty,
  latest_search.resolved_share_penalty,
  latest_search.resolved_size_share_penalty,
  latest_search.worst_window_resolved_share_penalty,
  latest_search.worst_window_resolved_size_share_penalty,
  latest_search.mode_resolved_share_penalty,
  latest_search.mode_resolved_size_share_penalty,
  latest_search.mode_worst_window_resolved_share_penalty,
  latest_search.mode_worst_window_resolved_size_share_penalty,
  latest_search.mode_active_window_accepted_share_penalty,
  latest_search.mode_active_window_accepted_size_share_penalty,
  latest_search.worst_active_window_accepted_penalty,
  latest_search.worst_active_window_accepted_size_penalty,
  latest_search.mode_worst_active_window_accepted_penalty,
  latest_search.mode_worst_active_window_accepted_size_penalty,
  latest_search.mode_loss_penalty,
  latest_search.mode_inactivity_penalty,
  latest_search.mode_accepted_window_count_penalty,
  latest_search.mode_accepted_window_share_penalty,
  latest_search.mode_non_accepting_active_window_streak_penalty,
  latest_search.mode_non_accepting_active_window_episode_penalty,
  latest_search.mode_accepting_window_accepted_share_penalty,
  latest_search.mode_accepting_window_accepted_size_share_penalty,
  latest_search.mode_top_two_accepting_window_accepted_share_penalty,
  latest_search.mode_top_two_accepting_window_accepted_size_share_penalty,
  latest_search.mode_accepting_window_accepted_concentration_index_penalty,
  latest_search.mode_accepting_window_accepted_size_concentration_index_penalty,
  latest_search.window_inactivity_penalty,
  latest_search.accepted_window_count_penalty,
  latest_search.accepted_window_share_penalty,
  latest_search.non_accepting_active_window_streak_penalty,
  latest_search.non_accepting_active_window_episode_penalty,
  latest_search.accepting_window_accepted_share_penalty,
  latest_search.accepting_window_accepted_size_share_penalty,
  latest_search.top_two_accepting_window_accepted_share_penalty,
  latest_search.top_two_accepting_window_accepted_size_share_penalty,
  latest_search.accepting_window_accepted_concentration_index_penalty,
  latest_search.accepting_window_accepted_size_concentration_index_penalty,
  latest_search.wallet_count_penalty,
  latest_search.market_count_penalty,
  latest_search.entry_price_band_count_penalty,
  latest_search.time_to_close_band_count_penalty,
  latest_search.wallet_concentration_penalty,
  latest_search.market_concentration_penalty,
  latest_search.entry_price_band_concentration_penalty,
  latest_search.time_to_close_band_concentration_penalty,
  latest_search.wallet_size_concentration_penalty,
  latest_search.market_size_concentration_penalty,
  latest_search.entry_price_band_size_concentration_penalty,
  latest_search.time_to_close_band_size_concentration_penalty,
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
`;
const SHARED_HOLDOUT_MESSAGE_RE = /shared holdout ll\/brier:\s*([-+]?[0-9]*\.?[0-9]+)\s*\/\s*([-+]?[0-9]*\.?[0-9]+)[\s\S]*?incumbent ll\/brier:\s*([-+]?[0-9]*\.?[0-9]+)\s*\/\s*([-+]?[0-9]*\.?[0-9]+)/i;
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
`;
const PERF_SQL = `
SELECT snapshot_at, mode, n_signals, n_acted, n_resolved, win_rate, total_pnl_usd, avg_confidence, sharpe
FROM perf_snapshots
WHERE id IN (
  SELECT MAX(id)
  FROM perf_snapshots
  GROUP BY mode
)
ORDER BY CASE WHEN mode='shadow' THEN 0 ELSE 1 END, snapshot_at DESC
`;
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
`;
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
`;
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
`;
const CONFUSION_SQL = `
SELECT
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} AND COALESCE(actual_pnl_usd, shadow_pnl_usd) > 0 THEN 1 ELSE 0 END) AS true_positive,
  SUM(CASE WHEN ${RESOLVED_EXECUTED_ENTRY_WHERE} AND COALESCE(actual_pnl_usd, shadow_pnl_usd) <= 0 THEN 1 ELSE 0 END) AS false_positive,
  SUM(CASE WHEN ${LOW_CONF_SKIP_WHERE} AND counterfactual_return <= 0 THEN 1 ELSE 0 END) AS true_negative,
  SUM(CASE WHEN ${LOW_CONF_SKIP_WHERE} AND counterfactual_return > 0 THEN 1 ELSE 0 END) AS false_negative
FROM trade_log
WHERE COALESCE(source_action, 'buy')='buy'
  AND real_money=0
`;
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
`;
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
`;
const TRAINING_SUMMARY_SQL = `
SELECT
  COUNT(*) AS total_runs,
  SUM(CASE WHEN finished_at >= strftime('%s', 'now', '-7 days') THEN 1 ELSE 0 END) AS runs_7d,
  SUM(CASE WHEN finished_at >= strftime('%s', 'now', '-30 days') THEN 1 ELSE 0 END) AS runs_30d
FROM retrain_runs
`;
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
`;
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
`;
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
`;
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
`;
function formatCount(value) {
    if (value == null || Number.isNaN(value))
        return '0';
    return Math.round(value).toLocaleString();
}
function centerLine(text, width) {
    const safeWidth = Math.max(1, width);
    const clipped = text.length > safeWidth ? text.slice(0, safeWidth) : text;
    const remaining = Math.max(0, safeWidth - clipped.length);
    const left = Math.floor(remaining / 2);
    const right = remaining - left;
    return `${' '.repeat(left)}${clipped}${' '.repeat(right)}`;
}
function confusionHeatColor(value, scale, kind) {
    const safeValue = Math.max(0, value);
    const safeScale = Math.max(1, scale);
    return kind === 'good'
        ? positiveDollarColor(safeValue, safeScale)
        : negativeHeatColor(safeValue, safeScale);
}
function ConfusionMatrixCell({ label, value, width, kind, scale }) {
    const fillColor = confusionHeatColor(value, scale, kind);
    const innerWidth = Math.max(1, width - 2);
    const centeredValue = centerLine(`${label} ${formatCount(value)}`, innerWidth);
    return (React.createElement(InkBox, { width: width, height: 6, borderStyle: "round", borderColor: fillColor, flexDirection: "column" },
        React.createElement(Text, { color: theme.modalBackground, backgroundColor: fillColor }, ' '.repeat(innerWidth)),
        React.createElement(Text, { color: theme.modalBackground, backgroundColor: fillColor, bold: true }, ' '.repeat(innerWidth)),
        React.createElement(Text, { color: theme.modalBackground, backgroundColor: fillColor, bold: true }, centeredValue),
        React.createElement(Text, { color: theme.modalBackground, backgroundColor: fillColor }, ' '.repeat(innerWidth))));
}
function ratio(numerator, denominator) {
    if (numerator == null || denominator == null || denominator <= 0)
        return null;
    return numerator / denominator;
}
function parseFeatureCount(raw) {
    if (!raw)
        return null;
    try {
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed.length : null;
    }
    catch {
        return null;
    }
}
function lowerIsBetterColor(value, good, okay) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    const worst = okay + (okay - good);
    const normalized = Math.max(0, Math.min(1, (worst - value) / Math.max(0.0001, worst - good)));
    return probabilityColor(normalized);
}
function sharpeColor(value) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    return centeredGradientColor(value, 1);
}
function signedMetricColor(value) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    return centeredGradientColor(value, 0.2);
}
function biasColor(value) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    return centeredGradientColor(-value, 0.08);
}
function dollarColor(value) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    return centeredGradientColor(value, 250);
}
function depthMultipleColor(value) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    return probabilityColor(Math.max(0, Math.min(1, value / 1.25)));
}
function exitDecisionLabel(decision, reason) {
    const normalized = String(decision || '').trim().toLowerCase();
    const normalizedReason = String(reason || '').trim().toLowerCase();
    if (normalizedReason.startsWith('hard exit'))
        return 'Hard exit';
    if (normalized === 'exit')
        return 'Exit';
    if (normalized === 'hold')
        return 'Hold';
    return normalized ? normalized : '-';
}
function exitDecisionColor(decision, reason) {
    const label = exitDecisionLabel(decision, reason).toLowerCase();
    if (label === 'hard exit')
        return theme.red;
    if (label === 'exit')
        return theme.yellow;
    if (label === 'hold')
        return theme.blue;
    return theme.dim;
}
function bucketLabel(bucket) {
    const lower = bucket * 10;
    const upper = bucket === 9 ? 100 : bucket * 10 + 9;
    return `${lower}-${upper}%`;
}
function formatPointDelta(value, digits = 1) {
    if (value == null || Number.isNaN(value))
        return '-';
    const abs = Math.abs(value * 100).toFixed(digits);
    const sign = value > 0 ? '+' : value < 0 ? '-' : '';
    return `${sign}${abs}pt`;
}
function calibrationRead(value) {
    if (value == null || Number.isNaN(value))
        return '-';
    if (Math.abs(value) <= 0.03)
        return 'In line';
    return value > 0 ? 'Overcalling' : 'Undercalling';
}
function modeRoleLabel(mode, taken, primaryMode, activeScorerLabel) {
    const normalizedMode = mode.trim().toLowerCase();
    if (taken <= 0) {
        return normalizedMode === 'veto' ? 'Guardrail idle' : 'Idle';
    }
    if (normalizedMode === primaryMode) {
        return normalizedMode === activeScorerLabel.trim().toLowerCase() ? 'Primary recent path' : 'Primary path';
    }
    return 'Secondary path';
}
function retrainRunStateLabel(status, deployed) {
    if (deployed)
        return 'deployed';
    const normalized = (status || '').trim().toLowerCase();
    if (!normalized)
        return '-';
    if (normalized === 'completed_not_deployed')
        return 'no deploy';
    if (normalized === 'skipped_not_enough_samples')
        return 'skip samples';
    if (normalized === 'skipped_insufficient_class_diversity')
        return 'skip diversity';
    if (normalized === 'skipped_insufficient_samples_for_holdout_search')
        return 'skip holdout';
    if (normalized === 'skipped_candidate_search_produced_no_valid_model')
        return 'skip search';
    if (normalized.startsWith('skipped_'))
        return 'skip';
    if (normalized === 'failed')
        return 'failed';
    if (normalized === 'already_running')
        return 'busy';
    return normalized.replace(/_/g, ' ');
}
function retrainRunStateCompactLabel(status, deployed) {
    if (deployed)
        return 'deploy';
    const normalized = (status || '').trim().toLowerCase();
    if (!normalized)
        return '-';
    if (normalized === 'completed_not_deployed')
        return 'no dep';
    if (normalized === 'skipped_not_enough_samples')
        return 'skip smp';
    if (normalized === 'skipped_insufficient_class_diversity')
        return 'skip div';
    if (normalized === 'skipped_insufficient_samples_for_holdout_search')
        return 'skip hld';
    if (normalized === 'skipped_candidate_search_produced_no_valid_model')
        return 'skip src';
    if (normalized.startsWith('skipped_'))
        return 'skip';
    if (normalized === 'failed')
        return 'failed';
    if (normalized === 'already_running')
        return 'busy';
    return normalized.replace(/_/g, ' ');
}
function retrainRunStateColor(status, deployed) {
    if (deployed)
        return theme.green;
    const normalized = (status || '').trim().toLowerCase();
    if (!normalized)
        return theme.dim;
    if (normalized === 'deployed')
        return theme.green;
    if (normalized === 'completed_not_deployed')
        return theme.yellow;
    if (normalized.startsWith('skipped_'))
        return theme.dim;
    if (normalized === 'failed')
        return theme.red;
    if (normalized === 'already_running')
        return theme.yellow;
    return theme.white;
}
function sharedHoldoutComparison(row) {
    const message = String(row?.message || '').trim();
    if (!message)
        return null;
    const match = message.match(SHARED_HOLDOUT_MESSAGE_RE);
    if (!match)
        return null;
    const challengerLogLoss = Number.parseFloat(match[1] || '');
    const challengerBrierScore = Number.parseFloat(match[2] || '');
    const incumbentLogLoss = Number.parseFloat(match[3] || '');
    const incumbentBrierScore = Number.parseFloat(match[4] || '');
    if (Number.isNaN(challengerLogLoss)
        || Number.isNaN(challengerBrierScore)
        || Number.isNaN(incumbentLogLoss)
        || Number.isNaN(incumbentBrierScore)) {
        return null;
    }
    return {
        challenger_log_loss: challengerLogLoss,
        challenger_brier_score: challengerBrierScore,
        incumbent_log_loss: incumbentLogLoss,
        incumbent_brier_score: incumbentBrierScore
    };
}
function sharedHoldoutGateRead(row) {
    const comparison = sharedHoldoutComparison(row);
    if (!comparison)
        return '-';
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
    ];
    return outcomes.join(', ');
}
function sharedHoldoutGateReadCompact(row) {
    return sharedHoldoutGateRead(row).replace(', ', ' / ');
}
function sharedHoldoutGateReadColor(row) {
    const comparison = sharedHoldoutComparison(row);
    if (!comparison)
        return theme.dim;
    const llDelta = comparison.challenger_log_loss - comparison.incumbent_log_loss;
    const brierDelta = comparison.challenger_brier_score - comparison.incumbent_brier_score;
    if (llDelta < 0 && brierDelta < 0)
        return theme.green;
    if (llDelta > 0 && brierDelta > 0)
        return theme.red;
    if (llDelta === 0 && brierDelta === 0)
        return theme.dim;
    return theme.yellow;
}
function formatInterval(seconds) {
    if (seconds == null || Number.isNaN(seconds) || seconds <= 0)
        return '-';
    if (seconds < 3600)
        return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
    }
    if (seconds < 86400 * 14) {
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
    }
    const weeks = seconds / (86400 * 7);
    return `${weeks.toFixed(1).replace(/\.0$/, '')}w`;
}
function normalizeCadence(value) {
    return value?.trim().toLowerCase() === 'weekly' ? 'weekly' : 'daily';
}
function clampHour(value) {
    const parsed = Number.parseInt(value || '', 10);
    if (!Number.isFinite(parsed))
        return 3;
    return Math.min(Math.max(parsed, 0), 23);
}
function parseNonNegativeInt(value, fallback) {
    const parsed = Number.parseInt(value || '', 10);
    if (!Number.isFinite(parsed))
        return fallback;
    return Math.max(parsed, 0);
}
function progressStatColor(current, target) {
    if (current <= 0)
        return theme.dim;
    if (target <= 0 || current >= target)
        return theme.green;
    return theme.yellow;
}
function manualRetrainLabel(startedAt, lastActivityAt, pollInterval, retrainInProgress, nowTs) {
    if (retrainInProgress) {
        return { label: 'Manual run', value: 'Running...', color: theme.yellow };
    }
    const heartbeatWindow = Math.max(pollInterval * 3, 30);
    const online = startedAt > 0 && lastActivityAt > 0 && (nowTs - lastActivityAt) <= heartbeatWindow;
    if (!online) {
        return { label: 'Manual run', value: 'Bot offline', color: theme.dim };
    }
    return { label: 'Manual run', value: 'Press t', color: theme.accent };
}
function getNextScheduledRetrainTs(cadence, hour, nowTs) {
    const now = new Date(nowTs * 1000);
    const next = new Date(nowTs * 1000);
    next.setHours(hour, 0, 0, 0);
    if (cadence === 'weekly') {
        const daysUntilMonday = (1 - now.getDay() + 7) % 7;
        next.setDate(now.getDate() + daysUntilMonday);
        if (next.getTime() / 1000 <= nowTs) {
            next.setDate(next.getDate() + 7);
        }
        return Math.floor(next.getTime() / 1000);
    }
    if (next.getTime() / 1000 <= nowTs) {
        next.setDate(next.getDate() + 1);
    }
    return Math.floor(next.getTime() / 1000);
}
function useNow(intervalMs = 30000) {
    const [nowTs, setNowTs] = useState(() => Math.floor(Date.now() / 1000));
    useEffect(() => {
        const id = setInterval(() => setNowTs(Math.floor(Date.now() / 1000)), intervalMs);
        return () => clearInterval(id);
    }, [intervalMs]);
    return nowTs;
}
function modeLabel(mode) {
    const normalized = mode.trim().toLowerCase();
    if (normalized === 'model')
        return 'XGBoost';
    if (normalized === 'xgboost')
        return 'XGBoost';
    if (normalized === 'ml')
        return 'XGBoost';
    if (normalized === 'hist_gradient_boosting')
        return 'XGBoost';
    if (normalized === 'heuristic')
        return 'Heuristic';
    if (normalized === 'disabled')
        return 'No scorer';
    if (normalized === 'shadow')
        return 'Tracker';
    if (normalized === 'live')
        return 'Live';
    return mode || 'Unknown';
}
function compactWalletLabel(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (!normalized)
        return '-';
    if (!normalized.startsWith('0x') || normalized.length <= 12)
        return normalized;
    return `${normalized.slice(0, 6)}..${normalized.slice(-4)}`;
}
function replaySegmentLabel(kind, value) {
    const normalized = String(value || '').trim();
    if (!normalized)
        return '-';
    if (kind === 'trader_address')
        return compactWalletLabel(normalized);
    return normalized;
}
function replaySegmentValue(kind, row) {
    if (!row?.segment_value)
        return '-';
    const label = replaySegmentLabel(kind, row.segment_value);
    const pnl = formatDollar(row.total_pnl_usd);
    return `${label} ${pnl}`.trim();
}
function replaySearchOverrideSummary(raw) {
    if (!raw)
        return '-';
    try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
            return '-';
        const payload = parsed;
        const parts = [];
        if (payload.min_confidence != null)
            parts.push(`conf ${payload.min_confidence}`);
        if (payload.heuristic_min_entry_price != null && payload.heuristic_max_entry_price != null) {
            parts.push(`band ${payload.heuristic_min_entry_price}-${payload.heuristic_max_entry_price}`);
        }
        if (payload.max_bet_fraction != null)
            parts.push(`bet ${payload.max_bet_fraction}`);
        if (payload.model_edge_mid_threshold != null || payload.model_edge_high_threshold != null) {
            parts.push(`edge ${payload.model_edge_mid_threshold ?? '-'} / ${payload.model_edge_high_threshold ?? '-'}`);
        }
        if (parts.length)
            return parts.join(', ');
        const fallbackKeys = Object.keys(payload).slice(0, 3);
        return fallbackKeys.length ? fallbackKeys.map((key) => `${key}=${String(payload[key])}`).join(', ') : '-';
    }
    catch {
        return '-';
    }
}
function replaySearchSegmentGateSummary(raw) {
    if (!raw)
        return 'all';
    try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
            return 'all';
        const payload = parsed;
        const entryBands = Array.isArray(payload.allowed_entry_price_bands)
            ? payload.allowed_entry_price_bands.map((value) => String(value || '').trim()).filter(Boolean)
            : [];
        const horizonBands = Array.isArray(payload.allowed_time_to_close_bands)
            ? payload.allowed_time_to_close_bands.map((value) => String(value || '').trim()).filter(Boolean)
            : [];
        const heuristicBands = Array.isArray(payload.heuristic_allowed_entry_price_bands)
            ? payload.heuristic_allowed_entry_price_bands.map((value) => String(value || '').trim()).filter(Boolean)
            : [];
        const modelBands = Array.isArray(payload.xgboost_allowed_entry_price_bands)
            ? payload.xgboost_allowed_entry_price_bands.map((value) => String(value || '').trim()).filter(Boolean)
            : [];
        const heuristicMinHorizonRaw = payload.heuristic_min_time_to_close_seconds;
        const modelMinHorizonRaw = payload.model_min_time_to_close_seconds;
        const allowHeuristic = payload.allow_heuristic;
        const allowXgboost = payload.allow_xgboost;
        const heuristicMinHorizon = replayFormatDurationSeconds(typeof heuristicMinHorizonRaw === 'number' ? heuristicMinHorizonRaw : Number(heuristicMinHorizonRaw));
        const modelMinHorizon = replayFormatDurationSeconds(typeof modelMinHorizonRaw === 'number' ? modelMinHorizonRaw : Number(modelMinHorizonRaw));
        const parts = [];
        if (entryBands.length)
            parts.push(`band ${entryBands.join('|')}`);
        if (horizonBands.length)
            parts.push(`hzn ${horizonBands.join('|')}`);
        if (heuristicBands.length)
            parts.push(`heur band ${heuristicBands.join('|')}`);
        if (heuristicMinHorizon && heuristicMinHorizon !== '0s')
            parts.push(`heur >=${heuristicMinHorizon}`);
        if (modelBands.length)
            parts.push(`model band ${modelBands.join('|')}`);
        if (modelMinHorizon && modelMinHorizon !== '0s')
            parts.push(`model >=${modelMinHorizon}`);
        if (allowHeuristic === false)
            parts.push('heur off');
        if (allowXgboost === false)
            parts.push('xgb off');
        return parts.length ? parts.join(', ') : 'all';
    }
    catch {
        return 'all';
    }
}
function replaySearchDeployGapSummary(rawPolicy, rawConfig) {
    if (!rawPolicy)
        return '-';
    try {
        const parsedPolicy = JSON.parse(rawPolicy);
        if (!parsedPolicy || typeof parsedPolicy !== 'object' || Array.isArray(parsedPolicy))
            return '-';
        const payload = parsedPolicy;
        const parsedConfig = rawConfig ? JSON.parse(rawConfig) : null;
        const configPayload = parsedConfig && typeof parsedConfig === 'object' && !Array.isArray(parsedConfig)
            ? parsedConfig
            : {};
        const gaps = [];
        if (payload.allow_heuristic === false && !Object.prototype.hasOwnProperty.call(configPayload, 'ALLOW_HEURISTIC'))
            gaps.push('rerun: heur cfg');
        if (payload.allow_xgboost === false && !Object.prototype.hasOwnProperty.call(configPayload, 'ALLOW_XGBOOST'))
            gaps.push('rerun: xgb cfg');
        return gaps.length ? gaps.join(' | ') : 'none';
    }
    catch {
        return '-';
    }
}
function replaySearchModeMixSummary(raw, policyRaw) {
    if (!raw)
        return '-';
    try {
        const parsed = JSON.parse(raw);
        const windowCount = Number((parsed === null || parsed === void 0 ? void 0 : parsed.window_count) || 0);
        const enabled = replaySearchEnabledModes(policyRaw);
        const rawSummary = parsed?.signal_mode_summary;
        if (!rawSummary || typeof rawSummary !== 'object' || Array.isArray(rawSummary))
            return '-';
        const parsedEntries = Object.entries(rawSummary)
            .map(([mode, value]) => {
            if (!value || typeof value !== 'object' || Array.isArray(value))
                return null;
            const payload = value;
            return {
                mode,
                acceptedCount: Number(payload.accepted_count || 0),
                resolvedCount: Number(payload.resolved_count || 0),
                acceptedSizeUsd: Number(payload.accepted_size_usd || 0),
                resolvedSizeUsd: Number(payload.resolved_size_usd || 0),
                totalPnlUsd: Number(payload.total_pnl_usd || 0),
                activeWindowCount: replaySearchModeActiveWindowCountFromPayload(payload, windowCount),
                acceptedWindowCount: replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount),
                acceptedWindowShare: replaySearchModeAcceptedWindowShareFromPayload(payload, windowCount),
                maxNonAcceptingActiveWindowStreak: replaySearchModeMaxNonAcceptingActiveWindowStreakFromPayload(payload, windowCount),
                nonAcceptingActiveWindowEpisodeCount: replaySearchModeNonAcceptingActiveWindowEpisodeCountFromPayload(payload, windowCount),
                maxAcceptingWindowAcceptedShare: replaySearchModeMaxAcceptingWindowAcceptedShareFromPayload(payload, windowCount),
                maxAcceptingWindowAcceptedSizeShare: replaySearchModeMaxAcceptingWindowAcceptedSizeShareFromPayload(payload, windowCount),
                topTwoAcceptingWindowAcceptedShare: replaySearchModeTopTwoAcceptingWindowAcceptedShareFromPayload(payload, windowCount),
                topTwoAcceptingWindowAcceptedSizeShare: replaySearchModeTopTwoAcceptingWindowAcceptedSizeShareFromPayload(payload, windowCount),
                acceptingWindowAcceptedConcentrationIndex: replaySearchAcceptingWindowAcceptedConcentrationIndexFromPayload(payload),
                acceptingWindowAcceptedSizeConcentrationIndex: replaySearchAcceptingWindowAcceptedSizeConcentrationIndexFromPayload(payload)
            };
        })
            .filter((entry) => Boolean(entry))
            .filter((entry) => {
            if (entry.mode === 'heuristic')
                return enabled.heuristic;
            if (entry.mode === 'xgboost')
                return enabled.xgboost;
            return true;
        });
        const entryByMode = new Map(parsedEntries.map((entry) => [entry.mode, entry]));
        const entries = [];
        if (enabled.heuristic) {
        entries.push(entryByMode.get('heuristic') ?? {
            mode: 'heuristic',
            acceptedCount: 0,
            resolvedCount: 0,
            acceptedSizeUsd: 0,
            resolvedSizeUsd: 0,
                totalPnlUsd: 0,
                activeWindowCount: 0,
                acceptedWindowCount: 0,
                acceptedWindowShare: 0,
                maxNonAcceptingActiveWindowStreak: 0,
                nonAcceptingActiveWindowEpisodeCount: 0,
                maxAcceptingWindowAcceptedShare: 0,
                maxAcceptingWindowAcceptedSizeShare: 0,
                topTwoAcceptingWindowAcceptedShare: 0,
                topTwoAcceptingWindowAcceptedSizeShare: 0,
                acceptingWindowAcceptedConcentrationIndex: 0,
                acceptingWindowAcceptedSizeConcentrationIndex: 0
            });
        }
        if (enabled.xgboost) {
        entries.push(entryByMode.get('xgboost') ?? {
            mode: 'xgboost',
            acceptedCount: 0,
            resolvedCount: 0,
            acceptedSizeUsd: 0,
            resolvedSizeUsd: 0,
                totalPnlUsd: 0,
                activeWindowCount: 0,
                acceptedWindowCount: 0,
                acceptedWindowShare: 0,
                maxNonAcceptingActiveWindowStreak: 0,
                nonAcceptingActiveWindowEpisodeCount: 0,
                maxAcceptingWindowAcceptedShare: 0,
                maxAcceptingWindowAcceptedSizeShare: 0,
                topTwoAcceptingWindowAcceptedShare: 0,
                topTwoAcceptingWindowAcceptedSizeShare: 0,
                acceptingWindowAcceptedConcentrationIndex: 0,
                acceptingWindowAcceptedSizeConcentrationIndex: 0
            });
        }
        parsedEntries
            .filter((entry) => entry.mode !== 'heuristic' && entry.mode !== 'xgboost')
            .sort((left, right) => left.mode.localeCompare(right.mode))
            .forEach((entry) => {
            if (entry.acceptedCount > 0)
                entries.push(entry);
        });
        if (!entries.length) {
            const parts = [];
            if (!enabled.heuristic)
                parts.push('Heuristic off');
            if (!enabled.xgboost)
                parts.push('XGBoost off');
            return parts.length ? parts.join(' | ') : '-';
        }
        const totalAccepted = entries.reduce((sum, entry) => sum + entry.acceptedCount, 0);
        const totalAcceptedSizeUsd = entries.reduce((sum, entry) => sum + entry.acceptedSizeUsd, 0);
        const parts = entries
            .map((entry) => {
            const share = totalAccepted > 0 ? `${Math.round((entry.acceptedCount / totalAccepted) * 100)}%` : '0%';
            const sizeShare = totalAcceptedSizeUsd > 0 ? `${Math.round((entry.acceptedSizeUsd / totalAcceptedSizeUsd) * 100)}%` : '0%';
            const resolvedShare = entry.acceptedCount > 0 ? formatPct(entry.resolvedCount / entry.acceptedCount, 0) : '0%';
            const resolvedSizeShare = entry.acceptedSizeUsd > 0 ? formatPct(entry.resolvedSizeUsd / entry.acceptedSizeUsd, 0) : '0%';
            return `${modeLabel(entry.mode)} ${formatCount(entry.acceptedCount)} ${share} sz-mix ${sizeShare} acc-win ${formatCount(entry.acceptedWindowCount)}/${formatCount(entry.activeWindowCount)} acc-freq ${formatPct(entry.acceptedWindowShare, 0)} acc-gap ${formatCount(entry.maxNonAcceptingActiveWindowStreak)} acc-runs ${formatCount(entry.nonAcceptingActiveWindowEpisodeCount)} top-acc ${formatPct(entry.maxAcceptingWindowAcceptedShare, 0)} top-acc$ ${formatPct(entry.maxAcceptingWindowAcceptedSizeShare, 0)} top2-acc ${formatPct(entry.topTwoAcceptingWindowAcceptedShare, 0)} top2-acc$ ${formatPct(entry.topTwoAcceptingWindowAcceptedSizeShare, 0)} acc-ci ${formatPct(entry.acceptingWindowAcceptedConcentrationIndex, 0)} acc-ci$ ${formatPct(entry.acceptingWindowAcceptedSizeConcentrationIndex, 0)} cov ${resolvedShare} sz-cov ${resolvedSizeShare} ${formatDollar(entry.totalPnlUsd)}`;
        })
        if (!enabled.heuristic)
            parts.push('Heuristic off');
        if (!enabled.xgboost)
            parts.push('XGBoost off');
        return parts.join(' | ');
    }
    catch {
        return '-';
    }
}
function replaySearchModeShares(raw, policyRaw) {
    const countShares = new Map();
    const sizeShares = new Map();
    if (!raw)
        return { countShares, sizeShares };
    try {
        const parsed = JSON.parse(raw);
        const enabled = replaySearchEnabledModes(policyRaw);
        const rawSummary = parsed?.signal_mode_summary;
        if (!rawSummary || typeof rawSummary !== 'object' || Array.isArray(rawSummary))
            return { countShares, sizeShares };
        const entries = Object.entries(rawSummary)
            .map(([mode, value]) => {
            if (!value || typeof value !== 'object' || Array.isArray(value))
                return null;
            const payload = value;
            return {
                mode: String(mode || '').trim(),
                acceptedCount: Number(payload.accepted_count || 0),
                acceptedSizeUsd: Number(payload.accepted_size_usd || 0)
            };
        })
            .filter((entry) => Boolean(entry))
            .filter((entry) => {
            if (entry.mode === 'heuristic')
                return enabled.heuristic;
            if (entry.mode === 'xgboost')
                return enabled.xgboost;
            return true;
        })
            .filter((entry) => entry.acceptedCount > 0 || entry.acceptedSizeUsd > 0);
        const totalAccepted = entries.reduce((sum, entry) => sum + entry.acceptedCount, 0);
        const totalAcceptedSizeUsd = entries.reduce((sum, entry) => sum + entry.acceptedSizeUsd, 0);
        for (const entry of entries) {
            if (totalAccepted > 0 && entry.acceptedCount > 0)
                countShares.set(entry.mode, entry.acceptedCount / totalAccepted);
            if (totalAcceptedSizeUsd > 0 && entry.acceptedSizeUsd > 0)
                sizeShares.set(entry.mode, entry.acceptedSizeUsd / totalAcceptedSizeUsd);
        }
    }
    catch {
        return { countShares, sizeShares };
    }
    return { countShares, sizeShares };
}
function replaySearchModeDriftSummary(bestRaw, currentRaw, bestPolicyRaw, currentPolicyRaw) {
    const bestShares = replaySearchModeShares(bestRaw, bestPolicyRaw);
    const currentShares = replaySearchModeShares(currentRaw, currentPolicyRaw);
    const bestEnabled = replaySearchEnabledModes(bestPolicyRaw);
    const currentEnabled = replaySearchEnabledModes(currentPolicyRaw);
    if ((!bestShares.countShares.size && !bestShares.sizeShares.size) || (!currentShares.countShares.size && !currentShares.sizeShares.size))
        return '-';
    const parts = [];
    for (const mode of ['heuristic', 'xgboost']) {
        const bestModeEnabled = mode === 'heuristic' ? bestEnabled.heuristic : bestEnabled.xgboost;
        const currentModeEnabled = mode === 'heuristic' ? currentEnabled.heuristic : currentEnabled.xgboost;
        if (!bestModeEnabled && !currentModeEnabled)
            continue;
        if (bestModeEnabled !== currentModeEnabled) {
            parts.push(`${modeLabel(mode)} ${bestModeEnabled ? 'on' : 'off'} vs ${currentModeEnabled ? 'on' : 'off'}`);
            continue;
        }
        const countDriftPctPoints = ((bestShares.countShares.get(mode) || 0) - (currentShares.countShares.get(mode) || 0)) * 100;
        const sizeDriftPctPoints = ((bestShares.sizeShares.get(mode) || 0) - (currentShares.sizeShares.get(mode) || 0)) * 100;
        if (!bestShares.countShares.has(mode)
            && !currentShares.countShares.has(mode)
            && !bestShares.sizeShares.has(mode)
            && !currentShares.sizeShares.has(mode))
            continue;
        const countRounded = Math.round(countDriftPctPoints);
        const sizeRounded = Math.round(sizeDriftPctPoints);
        const countSign = countRounded > 0 ? '+' : '';
        const sizeSign = sizeRounded > 0 ? '+' : '';
        parts.push(`${modeLabel(mode)} ${countSign}${countRounded}pt sz ${sizeSign}${sizeRounded}pt`);
    }
    return parts.length ? parts.join(' | ') : '-';
}
function replaySearchCurrentModeEvidenceSummary(raw, policyRaw) {
    if (!raw)
        return '-';
    try {
        const parsed = JSON.parse(raw);
        const windowCount = Number((parsed === null || parsed === void 0 ? void 0 : parsed.window_count) || 0);
        const enabled = replaySearchEnabledModes(policyRaw);
        const rawSummary = parsed?.signal_mode_summary;
        if (!rawSummary || typeof rawSummary !== 'object' || Array.isArray(rawSummary))
            return '-';
        const parsedEntries = Object.entries(rawSummary)
            .map(([mode, value]) => {
            if (!value || typeof value !== 'object' || Array.isArray(value))
                return null;
            const payload = value;
            return {
                mode,
                acceptedCount: Number(payload.accepted_count || 0),
                resolvedCount: Number(payload.resolved_count || 0),
                acceptedSizeUsd: Number(payload.accepted_size_usd || 0),
                resolvedSizeUsd: Number(payload.resolved_size_usd || 0),
                totalPnlUsd: Number(payload.total_pnl_usd || 0),
                winRate: payload.win_rate == null ? null : Number(payload.win_rate),
                activeWindowCount: replaySearchModeActiveWindowCountFromPayload(payload, windowCount),
                acceptedWindowCount: replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount),
                acceptedWindowShare: replaySearchModeAcceptedWindowShareFromPayload(payload, windowCount),
                maxNonAcceptingActiveWindowStreak: replaySearchModeMaxNonAcceptingActiveWindowStreakFromPayload(payload, windowCount),
                nonAcceptingActiveWindowEpisodeCount: replaySearchModeNonAcceptingActiveWindowEpisodeCountFromPayload(payload, windowCount),
                maxAcceptingWindowAcceptedShare: replaySearchModeMaxAcceptingWindowAcceptedShareFromPayload(payload, windowCount),
                maxAcceptingWindowAcceptedSizeShare: replaySearchModeMaxAcceptingWindowAcceptedSizeShareFromPayload(payload, windowCount),
                topTwoAcceptingWindowAcceptedShare: replaySearchModeTopTwoAcceptingWindowAcceptedShareFromPayload(payload, windowCount),
                topTwoAcceptingWindowAcceptedSizeShare: replaySearchModeTopTwoAcceptingWindowAcceptedSizeShareFromPayload(payload, windowCount),
                acceptingWindowAcceptedConcentrationIndex: replaySearchAcceptingWindowAcceptedConcentrationIndexFromPayload(payload),
                acceptingWindowAcceptedSizeConcentrationIndex: replaySearchAcceptingWindowAcceptedSizeConcentrationIndexFromPayload(payload)
            };
        })
            .filter((entry) => Boolean(entry))
            .filter((entry) => {
            if (entry.mode === 'heuristic')
                return enabled.heuristic;
            if (entry.mode === 'xgboost')
                return enabled.xgboost;
            return true;
        });
        const entryByMode = new Map(parsedEntries.map((entry) => [entry.mode, entry]));
        const entries = [];
        if (enabled.heuristic) {
        entries.push(entryByMode.get('heuristic') ?? {
            mode: 'heuristic',
            acceptedCount: 0,
            resolvedCount: 0,
            acceptedSizeUsd: 0,
            resolvedSizeUsd: 0,
                totalPnlUsd: 0,
                winRate: null,
                activeWindowCount: 0,
                acceptedWindowCount: 0,
                acceptedWindowShare: 0,
                maxNonAcceptingActiveWindowStreak: 0,
                nonAcceptingActiveWindowEpisodeCount: 0,
                maxAcceptingWindowAcceptedShare: 0,
                maxAcceptingWindowAcceptedSizeShare: 0,
                topTwoAcceptingWindowAcceptedShare: 0,
                topTwoAcceptingWindowAcceptedSizeShare: 0,
                acceptingWindowAcceptedConcentrationIndex: 0,
                acceptingWindowAcceptedSizeConcentrationIndex: 0
            });
        }
        if (enabled.xgboost) {
        entries.push(entryByMode.get('xgboost') ?? {
            mode: 'xgboost',
            acceptedCount: 0,
            resolvedCount: 0,
            acceptedSizeUsd: 0,
            resolvedSizeUsd: 0,
                totalPnlUsd: 0,
                winRate: null,
                activeWindowCount: 0,
                acceptedWindowCount: 0,
                acceptedWindowShare: 0,
                maxNonAcceptingActiveWindowStreak: 0,
                nonAcceptingActiveWindowEpisodeCount: 0,
                maxAcceptingWindowAcceptedShare: 0,
                maxAcceptingWindowAcceptedSizeShare: 0,
                topTwoAcceptingWindowAcceptedShare: 0,
                topTwoAcceptingWindowAcceptedSizeShare: 0,
                acceptingWindowAcceptedConcentrationIndex: 0,
                acceptingWindowAcceptedSizeConcentrationIndex: 0
            });
        }
        parsedEntries
            .filter((entry) => entry.mode !== 'heuristic' && entry.mode !== 'xgboost')
            .sort((left, right) => left.mode.localeCompare(right.mode))
            .forEach((entry) => {
            if (entry.acceptedCount > 0)
                entries.push(entry);
        });
        if (!entries.length) {
            const parts = [];
            if (!enabled.heuristic)
                parts.push('Heuristic off');
            if (!enabled.xgboost)
                parts.push('XGBoost off');
            return parts.length ? parts.join(' | ') : '-';
        }
        const totalAccepted = entries.reduce((sum, entry) => sum + entry.acceptedCount, 0);
        const totalAcceptedSizeUsd = entries.reduce((sum, entry) => sum + entry.acceptedSizeUsd, 0);
        const parts = entries
            .map((entry) => {
            const share = totalAccepted > 0 ? formatPct(entry.acceptedCount / totalAccepted, 0) : '0%';
            const sizeShare = totalAcceptedSizeUsd > 0 ? formatPct(entry.acceptedSizeUsd / totalAcceptedSizeUsd, 0) : '0%';
            const coverage = entry.acceptedCount > 0 ? formatPct(entry.resolvedCount / entry.acceptedCount, 0) : '0%';
            const sizeCoverage = entry.acceptedSizeUsd > 0 ? formatPct(entry.resolvedSizeUsd / entry.acceptedSizeUsd, 0) : '0%';
            const rate = entry.winRate == null ? '-' : formatPct(entry.winRate, 0);
            return `${modeLabel(entry.mode)} ${formatCount(entry.resolvedCount)}r/${formatCount(entry.acceptedCount)}a mix ${share} sz-mix ${sizeShare} acc-win ${formatCount(entry.acceptedWindowCount)}/${formatCount(entry.activeWindowCount)} acc-freq ${formatPct(entry.acceptedWindowShare, 0)} acc-gap ${formatCount(entry.maxNonAcceptingActiveWindowStreak)} acc-runs ${formatCount(entry.nonAcceptingActiveWindowEpisodeCount)} top-acc ${formatPct(entry.maxAcceptingWindowAcceptedShare, 0)} top-acc$ ${formatPct(entry.maxAcceptingWindowAcceptedSizeShare, 0)} top2-acc ${formatPct(entry.topTwoAcceptingWindowAcceptedShare, 0)} top2-acc$ ${formatPct(entry.topTwoAcceptingWindowAcceptedSizeShare, 0)} acc-ci ${formatPct(entry.acceptingWindowAcceptedConcentrationIndex, 0)} acc-ci$ ${formatPct(entry.acceptingWindowAcceptedSizeConcentrationIndex, 0)} ${coverage} sz-cov ${sizeCoverage} ${rate} ${formatDollar(entry.totalPnlUsd)}`;
        })
        if (!enabled.heuristic)
            parts.push('Heuristic off');
        if (!enabled.xgboost)
            parts.push('XGBoost off');
        return parts.join(' | ');
    }
    catch {
        return '-';
    }
}
function replaySearchEnabledModes(policyRaw) {
    if (!policyRaw)
        return { heuristic: true, xgboost: true };
    try {
        const parsed = JSON.parse(policyRaw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            return { heuristic: true, xgboost: true };
        }
        const payload = parsed;
        return {
            heuristic: payload.allow_heuristic == null ? true : Boolean(payload.allow_heuristic),
            xgboost: payload.allow_xgboost == null ? true : Boolean(payload.allow_xgboost)
        };
    }
    catch {
        return { heuristic: true, xgboost: true };
    }
}
function replaySearchModeFloorSummary(raw, policyRaw) {
    if (!raw)
        return 'none';
    try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
            return 'none';
        const payload = parsed;
        const parts = [];
        const enabled = replaySearchEnabledModes(policyRaw);
        const mixModesEnabled = enabled.heuristic && enabled.xgboost;
        const minHeuristicAccepted = Number(payload.min_heuristic_accepted_count || 0);
        const minXgboostAccepted = Number(payload.min_xgboost_accepted_count || 0);
        const minHeuristicResolved = Number(payload.min_heuristic_resolved_count || 0);
        const minXgboostResolved = Number(payload.min_xgboost_resolved_count || 0);
        const minHeuristicResolvedShare = Number(payload.min_heuristic_resolved_share || 0);
        const minXgboostResolvedShare = Number(payload.min_xgboost_resolved_share || 0);
        const minHeuristicResolvedSizeShare = Number(payload.min_heuristic_resolved_size_share || 0);
        const minXgboostResolvedSizeShare = Number(payload.min_xgboost_resolved_size_share || 0);
        const minHeuristicWinRate = Number(payload.min_heuristic_win_rate || 0);
        const minXgboostWinRate = Number(payload.min_xgboost_win_rate || 0);
        const minHeuristicPnlUsd = Number(payload.min_heuristic_pnl_usd || 0);
        const minXgboostPnlUsd = Number(payload.min_xgboost_pnl_usd || 0);
    const minHeuristicWorstWindowPnlUsd = Number(payload.min_heuristic_worst_window_pnl_usd ?? -1000000000);
    const minXgboostWorstWindowPnlUsd = Number(payload.min_xgboost_worst_window_pnl_usd ?? -1000000000);
    const minHeuristicWorstWindowResolvedShare = Number(payload.min_heuristic_worst_window_resolved_share || 0);
    const minXgboostWorstWindowResolvedShare = Number(payload.min_xgboost_worst_window_resolved_share || 0);
    const minHeuristicWorstWindowResolvedSizeShare = Number(payload.min_heuristic_worst_window_resolved_size_share || 0);
    const minXgboostWorstWindowResolvedSizeShare = Number(payload.min_xgboost_worst_window_resolved_size_share || 0);
    const minHeuristicPositiveWindows = Number(payload.min_heuristic_positive_windows || 0);
    const minXgboostPositiveWindows = Number(payload.min_xgboost_positive_windows || 0);
    const minHeuristicWorstActiveWindowAcceptedCount = Number(payload.min_heuristic_worst_active_window_accepted_count || 0);
    const minHeuristicWorstActiveWindowAcceptedSizeUsd = Number(payload.min_heuristic_worst_active_window_accepted_size_usd || 0);
    const minXgboostWorstActiveWindowAcceptedCount = Number(payload.min_xgboost_worst_active_window_accepted_count || 0);
    const minXgboostWorstActiveWindowAcceptedSizeUsd = Number(payload.min_xgboost_worst_active_window_accepted_size_usd || 0);
    const maxHeuristicInactiveWindows = Number(payload.max_heuristic_inactive_windows ?? -1);
        const maxXgboostInactiveWindows = Number(payload.max_xgboost_inactive_windows ?? -1);
        const maxHeuristicAcceptedShare = Number(payload.max_heuristic_accepted_share || 0);
        const maxHeuristicAcceptedSizeShare = Number(payload.max_heuristic_accepted_size_share || 0);
        const maxHeuristicActiveWindowAcceptedShare = Number(payload.max_heuristic_active_window_accepted_share || 0);
        const maxHeuristicActiveWindowAcceptedSizeShare = Number(payload.max_heuristic_active_window_accepted_size_share || 0);
        const minHeuristicAcceptedWindows = Number(payload.min_heuristic_accepted_windows || 0);
        const minHeuristicAcceptedWindowShare = Number(payload.min_heuristic_accepted_window_share || 0);
        const maxHeuristicNonAcceptingActiveWindowStreak = Number(payload.max_heuristic_non_accepting_active_window_streak ?? -1);
        const maxHeuristicNonAcceptingActiveWindowEpisodes = Number(payload.max_heuristic_non_accepting_active_window_episodes ?? -1);
        const maxHeuristicAcceptingWindowAcceptedShare = Number(payload.max_heuristic_accepting_window_accepted_share || 0);
        const maxHeuristicAcceptingWindowAcceptedSizeShare = Number(payload.max_heuristic_accepting_window_accepted_size_share || 0);
        const maxHeuristicTopTwoAcceptingWindowAcceptedShare = Number(payload.max_heuristic_top_two_accepting_window_accepted_share || 0);
        const maxHeuristicTopTwoAcceptingWindowAcceptedSizeShare = Number(payload.max_heuristic_top_two_accepting_window_accepted_size_share || 0);
        const maxHeuristicAcceptingWindowAcceptedConcentrationIndex = Number(payload.max_heuristic_accepting_window_accepted_concentration_index || 0);
        const maxHeuristicAcceptingWindowAcceptedSizeConcentrationIndex = Number(payload.max_heuristic_accepting_window_accepted_size_concentration_index || 0);
        const minXgboostAcceptedShare = Number(payload.min_xgboost_accepted_share || 0);
        const minXgboostAcceptedSizeShare = Number(payload.min_xgboost_accepted_size_share || 0);
        const minXgboostActiveWindowAcceptedShare = Number(payload.min_xgboost_active_window_accepted_share || 0);
        const minXgboostActiveWindowAcceptedSizeShare = Number(payload.min_xgboost_active_window_accepted_size_share || 0);
        const minXgboostAcceptedWindows = Number(payload.min_xgboost_accepted_windows || 0);
        const minXgboostAcceptedWindowShare = Number(payload.min_xgboost_accepted_window_share || 0);
        const maxXgboostNonAcceptingActiveWindowStreak = Number(payload.max_xgboost_non_accepting_active_window_streak ?? -1);
        const maxXgboostNonAcceptingActiveWindowEpisodes = Number(payload.max_xgboost_non_accepting_active_window_episodes ?? -1);
        const maxXgboostAcceptingWindowAcceptedShare = Number(payload.max_xgboost_accepting_window_accepted_share || 0);
        const maxXgboostAcceptingWindowAcceptedSizeShare = Number(payload.max_xgboost_accepting_window_accepted_size_share || 0);
        const maxXgboostTopTwoAcceptingWindowAcceptedShare = Number(payload.max_xgboost_top_two_accepting_window_accepted_share || 0);
        const maxXgboostTopTwoAcceptingWindowAcceptedSizeShare = Number(payload.max_xgboost_top_two_accepting_window_accepted_size_share || 0);
        const maxXgboostAcceptingWindowAcceptedConcentrationIndex = Number(payload.max_xgboost_accepting_window_accepted_concentration_index || 0);
        const maxXgboostAcceptingWindowAcceptedSizeConcentrationIndex = Number(payload.max_xgboost_accepting_window_accepted_size_concentration_index || 0);
        if (!enabled.heuristic) {
            parts.push('heur off');
        }
        else {
            if (minHeuristicAccepted > 0)
                parts.push(`heur >=${formatCount(minHeuristicAccepted)}`);
            if (minHeuristicResolved > 0)
                parts.push(`heur r>=${formatCount(minHeuristicResolved)}`);
            if (minHeuristicResolvedShare > 0)
                parts.push(`heur cov>=${formatPct(minHeuristicResolvedShare, 0)}`);
            if (minHeuristicResolvedSizeShare > 0)
                parts.push(`heur sz-cov>=${formatPct(minHeuristicResolvedSizeShare, 0)}`);
            if (minHeuristicWinRate > 0)
                parts.push(`heur wr>=${formatPct(minHeuristicWinRate, 0)}`);
            if (minHeuristicPnlUsd !== 0)
            parts.push(`heur pnl>=${formatDollar(minHeuristicPnlUsd)}`);
        if (minHeuristicWorstWindowPnlUsd > -999999999)
            parts.push(`heur worst>=${formatDollar(minHeuristicWorstWindowPnlUsd)}`);
        if (minHeuristicWorstWindowResolvedShare > 0)
            parts.push(`heur worst cov>=${formatPct(minHeuristicWorstWindowResolvedShare, 0)}`);
        if (minHeuristicWorstWindowResolvedSizeShare > 0)
            parts.push(`heur worst sz-cov>=${formatPct(minHeuristicWorstWindowResolvedSizeShare, 0)}`);
        if (minHeuristicPositiveWindows > 0)
            parts.push(`heur pos>=${formatCount(minHeuristicPositiveWindows)}`);
        if (minHeuristicWorstActiveWindowAcceptedCount > 0)
            parts.push(`heur worst acc>=${formatCount(minHeuristicWorstActiveWindowAcceptedCount)}`);
            if (minHeuristicWorstActiveWindowAcceptedSizeUsd > 0)
                parts.push(`heur worst acc$>=${formatDollar(minHeuristicWorstActiveWindowAcceptedSizeUsd)}`);
            if (maxHeuristicInactiveWindows >= 0)
                parts.push(`heur idle<=${formatCount(maxHeuristicInactiveWindows)}`);
            if (minHeuristicAcceptedWindows > 0)
                parts.push(`heur acc-win>=${formatCount(minHeuristicAcceptedWindows)}`);
            if (minHeuristicAcceptedWindowShare > 0)
                parts.push(`heur acc-freq>=${formatPct(minHeuristicAcceptedWindowShare, 0)}`);
            if (maxHeuristicNonAcceptingActiveWindowStreak >= 0)
                parts.push(`heur acc-gap<=${formatCount(maxHeuristicNonAcceptingActiveWindowStreak)}`);
            if (maxHeuristicNonAcceptingActiveWindowEpisodes >= 0)
                parts.push(`heur acc-runs<=${formatCount(maxHeuristicNonAcceptingActiveWindowEpisodes)}`);
            if (maxHeuristicAcceptingWindowAcceptedShare > 0)
                parts.push(`heur top-acc<=${formatPct(maxHeuristicAcceptingWindowAcceptedShare, 0)}`);
            if (maxHeuristicAcceptingWindowAcceptedSizeShare > 0)
                parts.push(`heur top-acc$<=${formatPct(maxHeuristicAcceptingWindowAcceptedSizeShare, 0)}`);
            if (maxHeuristicTopTwoAcceptingWindowAcceptedShare > 0)
                parts.push(`heur top2-acc<=${formatPct(maxHeuristicTopTwoAcceptingWindowAcceptedShare, 0)}`);
            if (maxHeuristicTopTwoAcceptingWindowAcceptedSizeShare > 0)
                parts.push(`heur top2-acc$<=${formatPct(maxHeuristicTopTwoAcceptingWindowAcceptedSizeShare, 0)}`);
            if (maxHeuristicAcceptingWindowAcceptedConcentrationIndex > 0)
                parts.push(`heur acc-ci<=${formatPct(maxHeuristicAcceptingWindowAcceptedConcentrationIndex, 0)}`);
            if (maxHeuristicAcceptingWindowAcceptedSizeConcentrationIndex > 0)
                parts.push(`heur acc-ci$<=${formatPct(maxHeuristicAcceptingWindowAcceptedSizeConcentrationIndex, 0)}`);
            if (mixModesEnabled && maxHeuristicAcceptedShare > 0)
                parts.push(`heur mix<=${formatPct(maxHeuristicAcceptedShare, 0)}`);
            if (mixModesEnabled && maxHeuristicAcceptedSizeShare > 0)
                parts.push(`heur mix$<=${formatPct(maxHeuristicAcceptedSizeShare, 0)}`);
            if (mixModesEnabled && maxHeuristicActiveWindowAcceptedShare > 0)
                parts.push(`heur acc-mix<=${formatPct(maxHeuristicActiveWindowAcceptedShare, 0)}`);
            if (mixModesEnabled && maxHeuristicActiveWindowAcceptedSizeShare > 0)
                parts.push(`heur acc-mix$<=${formatPct(maxHeuristicActiveWindowAcceptedSizeShare, 0)}`);
        }
        if (!enabled.xgboost) {
            parts.push('model off');
        }
        else {
            if (minXgboostAccepted > 0)
                parts.push(`model >=${formatCount(minXgboostAccepted)}`);
            if (minXgboostResolved > 0)
                parts.push(`model r>=${formatCount(minXgboostResolved)}`);
            if (minXgboostResolvedShare > 0)
                parts.push(`model cov>=${formatPct(minXgboostResolvedShare, 0)}`);
            if (minXgboostResolvedSizeShare > 0)
                parts.push(`model sz-cov>=${formatPct(minXgboostResolvedSizeShare, 0)}`);
            if (minXgboostWinRate > 0)
                parts.push(`model wr>=${formatPct(minXgboostWinRate, 0)}`);
            if (minXgboostPnlUsd !== 0)
            parts.push(`model pnl>=${formatDollar(minXgboostPnlUsd)}`);
        if (minXgboostWorstWindowPnlUsd > -999999999)
            parts.push(`model worst>=${formatDollar(minXgboostWorstWindowPnlUsd)}`);
        if (minXgboostWorstWindowResolvedShare > 0)
            parts.push(`model worst cov>=${formatPct(minXgboostWorstWindowResolvedShare, 0)}`);
        if (minXgboostWorstWindowResolvedSizeShare > 0)
            parts.push(`model worst sz-cov>=${formatPct(minXgboostWorstWindowResolvedSizeShare, 0)}`);
        if (minXgboostPositiveWindows > 0)
            parts.push(`model pos>=${formatCount(minXgboostPositiveWindows)}`);
        if (minXgboostWorstActiveWindowAcceptedCount > 0)
            parts.push(`model worst acc>=${formatCount(minXgboostWorstActiveWindowAcceptedCount)}`);
            if (minXgboostWorstActiveWindowAcceptedSizeUsd > 0)
                parts.push(`model worst acc$>=${formatDollar(minXgboostWorstActiveWindowAcceptedSizeUsd)}`);
            if (maxXgboostInactiveWindows >= 0)
                parts.push(`model idle<=${formatCount(maxXgboostInactiveWindows)}`);
            if (minXgboostAcceptedWindows > 0)
                parts.push(`model acc-win>=${formatCount(minXgboostAcceptedWindows)}`);
            if (minXgboostAcceptedWindowShare > 0)
                parts.push(`model acc-freq>=${formatPct(minXgboostAcceptedWindowShare, 0)}`);
            if (maxXgboostNonAcceptingActiveWindowStreak >= 0)
                parts.push(`model acc-gap<=${formatCount(maxXgboostNonAcceptingActiveWindowStreak)}`);
            if (maxXgboostNonAcceptingActiveWindowEpisodes >= 0)
                parts.push(`model acc-runs<=${formatCount(maxXgboostNonAcceptingActiveWindowEpisodes)}`);
            if (maxXgboostAcceptingWindowAcceptedShare > 0)
                parts.push(`model top-acc<=${formatPct(maxXgboostAcceptingWindowAcceptedShare, 0)}`);
            if (maxXgboostAcceptingWindowAcceptedSizeShare > 0)
                parts.push(`model top-acc$<=${formatPct(maxXgboostAcceptingWindowAcceptedSizeShare, 0)}`);
            if (maxXgboostTopTwoAcceptingWindowAcceptedShare > 0)
                parts.push(`model top2-acc<=${formatPct(maxXgboostTopTwoAcceptingWindowAcceptedShare, 0)}`);
            if (maxXgboostTopTwoAcceptingWindowAcceptedSizeShare > 0)
                parts.push(`model top2-acc$<=${formatPct(maxXgboostTopTwoAcceptingWindowAcceptedSizeShare, 0)}`);
            if (maxXgboostAcceptingWindowAcceptedConcentrationIndex > 0)
                parts.push(`model acc-ci<=${formatPct(maxXgboostAcceptingWindowAcceptedConcentrationIndex, 0)}`);
            if (maxXgboostAcceptingWindowAcceptedSizeConcentrationIndex > 0)
                parts.push(`model acc-ci$<=${formatPct(maxXgboostAcceptingWindowAcceptedSizeConcentrationIndex, 0)}`);
            if (mixModesEnabled && minXgboostAcceptedShare > 0)
                parts.push(`model mix>=${formatPct(minXgboostAcceptedShare, 0)}`);
            if (mixModesEnabled && minXgboostAcceptedSizeShare > 0)
                parts.push(`model mix$>=${formatPct(minXgboostAcceptedSizeShare, 0)}`);
            if (mixModesEnabled && minXgboostActiveWindowAcceptedShare > 0)
                parts.push(`model acc-mix>=${formatPct(minXgboostActiveWindowAcceptedShare, 0)}`);
            if (mixModesEnabled && minXgboostActiveWindowAcceptedSizeShare > 0)
                parts.push(`model acc-mix$>=${formatPct(minXgboostActiveWindowAcceptedSizeShare, 0)}`);
        }
        return parts.length ? parts.join(', ') : 'none';
    }
    catch {
        return 'none';
    }
}
function replaySearchPauseGuardSummary(bestRaw, currentRaw, constraintsRaw, pauseGuardPenalty, dailyGuardWindowPenalty, liveGuardWindowPenalty, dailyGuardRestartWindowPenalty, liveGuardRestartWindowPenalty) {
    const parseState = (raw) => {
        if (!raw)
            return null;
        try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return null;
            const payload = parsed;
            const tradeCount = Number(payload.trade_count || 0);
            const rejectShare = tradeCount > 0
                ? (() => {
                    const rawRejectSummary = payload.reject_reason_summary;
                    if (!rawRejectSummary || typeof rawRejectSummary !== 'object' || Array.isArray(rawRejectSummary))
                        return 0;
                    const rejectSummary = rawRejectSummary;
                    const pauseCount = Number(rejectSummary.daily_loss_guard || 0) + Number(rejectSummary.live_drawdown_guard || 0);
                    return pauseCount / tradeCount;
                })()
                : 0;
            return {
                rejectShare,
                dailyGuardWindowShare: replaySearchDailyGuardWindowShareFromPayload(payload),
                liveGuardWindowShare: replaySearchLiveGuardWindowShareFromPayload(payload),
                dailyGuardRestartWindowShare: replaySearchDailyGuardRestartWindowShareFromPayload(payload),
                liveGuardRestartWindowShare: replaySearchLiveGuardRestartWindowShareFromPayload(payload),
                initialBankrollUsd: Number(payload.initial_bankroll_usd || 0)
            };
        }
        catch {
            return null;
        }
    };
    const bestState = parseState(bestRaw);
    const currentState = parseState(currentRaw);
    const bestShare = (bestState === null || bestState === void 0 ? void 0 : bestState.rejectShare) ?? null;
    const currentShare = (currentState === null || currentState === void 0 ? void 0 : currentState.rejectShare) ?? null;
    const bestDailyGuardWindowShare = (bestState === null || bestState === void 0 ? void 0 : bestState.dailyGuardWindowShare) ?? null;
    const currentDailyGuardWindowShare = (currentState === null || currentState === void 0 ? void 0 : currentState.dailyGuardWindowShare) ?? null;
    const bestLiveGuardWindowShare = (bestState === null || bestState === void 0 ? void 0 : bestState.liveGuardWindowShare) ?? null;
    const currentLiveGuardWindowShare = (currentState === null || currentState === void 0 ? void 0 : currentState.liveGuardWindowShare) ?? null;
    const bestDailyGuardRestartWindowShare = (bestState === null || bestState === void 0 ? void 0 : bestState.dailyGuardRestartWindowShare) ?? null;
    const currentDailyGuardRestartWindowShare = (currentState === null || currentState === void 0 ? void 0 : currentState.dailyGuardRestartWindowShare) ?? null;
    const bestLiveGuardRestartWindowShare = (bestState === null || bestState === void 0 ? void 0 : bestState.liveGuardRestartWindowShare) ?? null;
    const currentLiveGuardRestartWindowShare = (currentState === null || currentState === void 0 ? void 0 : currentState.liveGuardRestartWindowShare) ?? null;
    const { maxShare, maxDailyGuardWindowShare, maxLiveGuardWindowShare, maxDailyGuardRestartWindowShare, maxLiveGuardRestartWindowShare } = (() => {
        if (!constraintsRaw)
            return {
                maxShare: 0,
                maxDailyGuardWindowShare: 0,
                maxLiveGuardWindowShare: 0,
                maxDailyGuardRestartWindowShare: 0,
                maxLiveGuardRestartWindowShare: 0
            };
        try {
            const parsed = JSON.parse(constraintsRaw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return {
                    maxShare: 0,
                    maxDailyGuardWindowShare: 0,
                    maxLiveGuardWindowShare: 0,
                    maxDailyGuardRestartWindowShare: 0,
                    maxLiveGuardRestartWindowShare: 0
                };
            return {
                maxShare: Number(parsed.max_pause_guard_reject_share || 0),
                maxDailyGuardWindowShare: Number(parsed.max_daily_guard_window_share || 0),
                maxLiveGuardWindowShare: Number(parsed.max_live_guard_window_share || 0),
                maxDailyGuardRestartWindowShare: Number(parsed.max_daily_guard_restart_window_share || 0),
                maxLiveGuardRestartWindowShare: Number(parsed.max_live_guard_restart_window_share || 0)
            };
        }
        catch {
            return {
                maxShare: 0,
                maxDailyGuardWindowShare: 0,
                maxLiveGuardWindowShare: 0,
                maxDailyGuardRestartWindowShare: 0,
                maxLiveGuardRestartWindowShare: 0
            };
        }
    })();
    const resolvedPauseGuardPenalty = Math.max(Number(pauseGuardPenalty || 0), 0);
    const resolvedDailyGuardWindowPenalty = Math.max(Number(dailyGuardWindowPenalty || 0), 0);
    const resolvedLiveGuardWindowPenalty = Math.max(Number(liveGuardWindowPenalty || 0), 0);
    const resolvedDailyGuardRestartWindowPenalty = Math.max(Number(dailyGuardRestartWindowPenalty || 0), 0);
    const resolvedLiveGuardRestartWindowPenalty = Math.max(Number(liveGuardRestartWindowPenalty || 0), 0);
    const formatPenaltyCost = (initialBankrollUsd, penalty, share) => {
        if (penalty <= 0 || share == null || initialBankrollUsd == null || initialBankrollUsd <= 0)
            return null;
        return formatDollar(-(initialBankrollUsd * penalty * share));
    };
    if (bestShare == null
        && currentShare == null
        && bestDailyGuardWindowShare == null
        && currentDailyGuardWindowShare == null
        && bestLiveGuardWindowShare == null
        && currentLiveGuardWindowShare == null
        && bestDailyGuardRestartWindowShare == null
        && currentDailyGuardRestartWindowShare == null
        && bestLiveGuardRestartWindowShare == null
        && currentLiveGuardRestartWindowShare == null
        && maxShare <= 0
        && maxDailyGuardWindowShare <= 0
        && maxLiveGuardWindowShare <= 0
        && maxDailyGuardRestartWindowShare <= 0
        && maxLiveGuardRestartWindowShare <= 0
        && resolvedPauseGuardPenalty <= 0
        && resolvedDailyGuardWindowPenalty <= 0
        && resolvedLiveGuardWindowPenalty <= 0
        && resolvedDailyGuardRestartWindowPenalty <= 0
        && resolvedLiveGuardRestartWindowPenalty <= 0) {
        return { summary: '-', hasActiveGuard: false, currentShare: null, bestShare: null, overLimit: false };
    }
    const parts = [];
    if (bestShare != null || bestDailyGuardWindowShare != null || bestLiveGuardWindowShare != null || bestDailyGuardRestartWindowShare != null || bestLiveGuardRestartWindowShare != null) {
        const bestParts = [];
        if (bestShare != null) {
            const penaltyCost = formatPenaltyCost((bestState === null || bestState === void 0 ? void 0 : bestState.initialBankrollUsd) ?? null, resolvedPauseGuardPenalty, bestShare);
            bestParts.push(`rej ${formatPct(bestShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        if (bestDailyGuardWindowShare != null) {
            const penaltyCost = formatPenaltyCost((bestState === null || bestState === void 0 ? void 0 : bestState.initialBankrollUsd) ?? null, resolvedDailyGuardWindowPenalty, bestDailyGuardWindowShare);
            bestParts.push(`d-freq ${formatPct(bestDailyGuardWindowShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        if (bestLiveGuardWindowShare != null) {
            const penaltyCost = formatPenaltyCost((bestState === null || bestState === void 0 ? void 0 : bestState.initialBankrollUsd) ?? null, resolvedLiveGuardWindowPenalty, bestLiveGuardWindowShare);
            bestParts.push(`p-freq ${formatPct(bestLiveGuardWindowShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        if (bestDailyGuardRestartWindowShare != null) {
            const penaltyCost = formatPenaltyCost((bestState === null || bestState === void 0 ? void 0 : bestState.initialBankrollUsd) ?? null, resolvedDailyGuardRestartWindowPenalty, bestDailyGuardRestartWindowShare);
            bestParts.push(`d-rst ${formatPct(bestDailyGuardRestartWindowShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        if (bestLiveGuardRestartWindowShare != null) {
            const penaltyCost = formatPenaltyCost((bestState === null || bestState === void 0 ? void 0 : bestState.initialBankrollUsd) ?? null, resolvedLiveGuardRestartWindowPenalty, bestLiveGuardRestartWindowShare);
            bestParts.push(`p-rst ${formatPct(bestLiveGuardRestartWindowShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        parts.push(`best ${bestParts.join(' ')}`);
    }
    if (currentShare != null || currentDailyGuardWindowShare != null || currentLiveGuardWindowShare != null || currentDailyGuardRestartWindowShare != null || currentLiveGuardRestartWindowShare != null) {
        const currentParts = [];
        if (currentShare != null) {
            const penaltyCost = formatPenaltyCost((currentState === null || currentState === void 0 ? void 0 : currentState.initialBankrollUsd) ?? null, resolvedPauseGuardPenalty, currentShare);
            currentParts.push(`rej ${formatPct(currentShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        if (currentDailyGuardWindowShare != null) {
            const penaltyCost = formatPenaltyCost((currentState === null || currentState === void 0 ? void 0 : currentState.initialBankrollUsd) ?? null, resolvedDailyGuardWindowPenalty, currentDailyGuardWindowShare);
            currentParts.push(`d-freq ${formatPct(currentDailyGuardWindowShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        if (currentLiveGuardWindowShare != null) {
            const penaltyCost = formatPenaltyCost((currentState === null || currentState === void 0 ? void 0 : currentState.initialBankrollUsd) ?? null, resolvedLiveGuardWindowPenalty, currentLiveGuardWindowShare);
            currentParts.push(`p-freq ${formatPct(currentLiveGuardWindowShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        if (currentDailyGuardRestartWindowShare != null) {
            const penaltyCost = formatPenaltyCost((currentState === null || currentState === void 0 ? void 0 : currentState.initialBankrollUsd) ?? null, resolvedDailyGuardRestartWindowPenalty, currentDailyGuardRestartWindowShare);
            currentParts.push(`d-rst ${formatPct(currentDailyGuardRestartWindowShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        if (currentLiveGuardRestartWindowShare != null) {
            const penaltyCost = formatPenaltyCost((currentState === null || currentState === void 0 ? void 0 : currentState.initialBankrollUsd) ?? null, resolvedLiveGuardRestartWindowPenalty, currentLiveGuardRestartWindowShare);
            currentParts.push(`p-rst ${formatPct(currentLiveGuardRestartWindowShare, 0)}${penaltyCost ? ` (${penaltyCost})` : ''}`);
        }
        parts.push(`cur ${currentParts.join(' ')}`);
    }
    if (maxShare > 0 || maxDailyGuardWindowShare > 0 || maxLiveGuardWindowShare > 0 || maxDailyGuardRestartWindowShare > 0 || maxLiveGuardRestartWindowShare > 0) {
        const limitParts = [];
        if (maxShare > 0)
            limitParts.push(`max rej ${formatPct(maxShare, 0)}`);
        if (maxDailyGuardWindowShare > 0)
            limitParts.push(`max d-freq ${formatPct(maxDailyGuardWindowShare, 0)}`);
        if (maxLiveGuardWindowShare > 0)
            limitParts.push(`max p-freq ${formatPct(maxLiveGuardWindowShare, 0)}`);
        if (maxDailyGuardRestartWindowShare > 0)
            limitParts.push(`max d-rst ${formatPct(maxDailyGuardRestartWindowShare, 0)}`);
        if (maxLiveGuardRestartWindowShare > 0)
            limitParts.push(`max p-rst ${formatPct(maxLiveGuardRestartWindowShare, 0)}`);
        parts.push(limitParts.join(' '));
    }
    if (resolvedPauseGuardPenalty > 0 || resolvedDailyGuardWindowPenalty > 0 || resolvedLiveGuardWindowPenalty > 0 || resolvedDailyGuardRestartWindowPenalty > 0 || resolvedLiveGuardRestartWindowPenalty > 0) {
        const penaltyParts = [];
        if (resolvedPauseGuardPenalty > 0)
            penaltyParts.push(`rej pen ${resolvedPauseGuardPenalty.toFixed(2)}x`);
        if (resolvedDailyGuardWindowPenalty > 0)
            penaltyParts.push(`d-freq pen ${resolvedDailyGuardWindowPenalty.toFixed(2)}x`);
        if (resolvedLiveGuardWindowPenalty > 0)
            penaltyParts.push(`p-freq pen ${resolvedLiveGuardWindowPenalty.toFixed(2)}x`);
        if (resolvedDailyGuardRestartWindowPenalty > 0)
            penaltyParts.push(`d-rst pen ${resolvedDailyGuardRestartWindowPenalty.toFixed(2)}x`);
        if (resolvedLiveGuardRestartWindowPenalty > 0)
            penaltyParts.push(`p-rst pen ${resolvedLiveGuardRestartWindowPenalty.toFixed(2)}x`);
        parts.push(penaltyParts.join(' '));
    }
    return {
        summary: parts.length ? parts.join(' | ') : '-',
        hasActiveGuard: maxShare > 0
            || maxDailyGuardWindowShare > 0
            || maxLiveGuardWindowShare > 0
            || maxDailyGuardRestartWindowShare > 0
            || maxLiveGuardRestartWindowShare > 0,
        currentShare,
        bestShare,
        overLimit: (bestShare != null && bestShare > maxShare && maxShare > 0)
            || (currentShare != null && currentShare > maxShare && maxShare > 0)
            || (bestDailyGuardWindowShare != null && bestDailyGuardWindowShare > maxDailyGuardWindowShare && maxDailyGuardWindowShare > 0)
            || (currentDailyGuardWindowShare != null && currentDailyGuardWindowShare > maxDailyGuardWindowShare && maxDailyGuardWindowShare > 0)
            || (bestLiveGuardWindowShare != null && bestLiveGuardWindowShare > maxLiveGuardWindowShare && maxLiveGuardWindowShare > 0)
            || (currentLiveGuardWindowShare != null && currentLiveGuardWindowShare > maxLiveGuardWindowShare && maxLiveGuardWindowShare > 0)
            || (bestDailyGuardRestartWindowShare != null && bestDailyGuardRestartWindowShare > maxDailyGuardRestartWindowShare && maxDailyGuardRestartWindowShare > 0)
            || (currentDailyGuardRestartWindowShare != null && currentDailyGuardRestartWindowShare > maxDailyGuardRestartWindowShare && maxDailyGuardRestartWindowShare > 0)
            || (bestLiveGuardRestartWindowShare != null && bestLiveGuardRestartWindowShare > maxLiveGuardRestartWindowShare && maxLiveGuardRestartWindowShare > 0)
            || (currentLiveGuardRestartWindowShare != null && currentLiveGuardRestartWindowShare > maxLiveGuardRestartWindowShare && maxLiveGuardRestartWindowShare > 0)
    };
}
function replaySearchScoreBreakdownSummary(raw) {
    if (!raw)
        return '-';
    try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
            return '-';
        const rawBreakdown = parsed.score_breakdown;
        if (!rawBreakdown || typeof rawBreakdown !== 'object' || Array.isArray(rawBreakdown))
            return '-';
        const breakdown = rawBreakdown;
        const scoreUsd = Number(breakdown.score_usd || 0);
        const pnlUsd = Number(breakdown.pnl_usd || 0);
        const drawdownPenaltyUsd = Number(breakdown.drawdown_penalty_usd || 0);
        const windowStddevPenaltyUsd = Number(breakdown.window_stddev_penalty_usd || 0);
        const worstWindowPenaltyUsd = Number(breakdown.worst_window_penalty_usd || 0);
        const pauseGuardPenaltyUsd = Number(breakdown.pause_guard_penalty_usd || 0);
        const dailyGuardWindowPenaltyUsd = Number(breakdown.daily_guard_window_penalty_usd || 0);
        const liveGuardWindowPenaltyUsd = Number(breakdown.live_guard_window_penalty_usd || 0);
        const dailyGuardRestartWindowPenaltyUsd = Number(breakdown.daily_guard_restart_window_penalty_usd || 0);
        const liveGuardRestartWindowPenaltyUsd = Number(breakdown.live_guard_restart_window_penalty_usd || 0);
        const openExposurePenaltyUsd = Number(breakdown.open_exposure_penalty_usd || 0);
        const windowEndOpenExposurePenaltyUsd = Number(breakdown.window_end_open_exposure_penalty_usd || 0);
        const avgWindowEndOpenExposurePenaltyUsd = Number(breakdown.avg_window_end_open_exposure_penalty_usd || 0);
        const carryWindowPenaltyUsd = Number(breakdown.carry_window_penalty_usd || 0);
        const carryRestartWindowPenaltyUsd = Number(breakdown.carry_restart_window_penalty_usd || 0);
        const resolvedSharePenaltyUsd = Number(breakdown.resolved_share_penalty_usd || 0);
        const resolvedSizeSharePenaltyUsd = Number(breakdown.resolved_size_share_penalty_usd || 0);
        const windowInactivityPenaltyUsd = Number(breakdown.window_inactivity_penalty_usd || 0);
        const acceptedWindowCountPenaltyUsd = Number(breakdown.accepted_window_count_penalty_usd || 0);
        const acceptedWindowSharePenaltyUsd = Number(breakdown.accepted_window_share_penalty_usd || 0);
        const nonAcceptingActiveWindowStreakPenaltyUsd = Number(breakdown.non_accepting_active_window_streak_penalty_usd || 0);
        const nonAcceptingActiveWindowEpisodePenaltyUsd = Number(breakdown.non_accepting_active_window_episode_penalty_usd || 0);
        const acceptingWindowAcceptedSharePenaltyUsd = Number(breakdown.accepting_window_accepted_share_penalty_usd || 0);
        const acceptingWindowAcceptedSizeSharePenaltyUsd = Number(breakdown.accepting_window_accepted_size_share_penalty_usd || 0);
        const topTwoAcceptingWindowAcceptedSharePenaltyUsd = Number(breakdown.top_two_accepting_window_accepted_share_penalty_usd || 0);
        const topTwoAcceptingWindowAcceptedSizeSharePenaltyUsd = Number(breakdown.top_two_accepting_window_accepted_size_share_penalty_usd || 0);
        const acceptingWindowAcceptedConcentrationIndexPenaltyUsd = Number(breakdown.accepting_window_accepted_concentration_index_penalty_usd || 0);
        const acceptingWindowAcceptedSizeConcentrationIndexPenaltyUsd = Number(breakdown.accepting_window_accepted_size_concentration_index_penalty_usd || 0);
        const worstWindowResolvedSharePenaltyUsd = Number(breakdown.worst_window_resolved_share_penalty_usd || 0);
        const worstWindowResolvedSizeSharePenaltyUsd = Number(breakdown.worst_window_resolved_size_share_penalty_usd || 0);
        const worstActiveWindowAcceptedPenaltyUsd = Number(breakdown.worst_active_window_accepted_penalty_usd || 0);
        const worstActiveWindowAcceptedSizePenaltyUsd = Number(breakdown.worst_active_window_accepted_size_penalty_usd || 0);
        const modeResolvedSharePenaltyUsd = Number(breakdown.mode_resolved_share_penalty_usd || 0);
        const modeResolvedSizeSharePenaltyUsd = Number(breakdown.mode_resolved_size_share_penalty_usd || 0);
        const modeWorstWindowResolvedSharePenaltyUsd = Number(breakdown.mode_worst_window_resolved_share_penalty_usd || 0);
        const modeWorstWindowResolvedSizeSharePenaltyUsd = Number(breakdown.mode_worst_window_resolved_size_share_penalty_usd || 0);
        const modeActiveWindowAcceptedSharePenaltyUsd = Number(breakdown.mode_active_window_accepted_share_penalty_usd || 0);
        const modeActiveWindowAcceptedSizeSharePenaltyUsd = Number(breakdown.mode_active_window_accepted_size_share_penalty_usd || 0);
        const modeWorstActiveWindowAcceptedPenaltyUsd = Number(breakdown.mode_worst_active_window_accepted_penalty_usd || 0);
        const modeWorstActiveWindowAcceptedSizePenaltyUsd = Number(breakdown.mode_worst_active_window_accepted_size_penalty_usd || 0);
        const modeLossPenaltyUsd = Number(breakdown.mode_loss_penalty_usd || 0);
        const modeInactivityPenaltyUsd = Number(breakdown.mode_inactivity_penalty_usd || 0);
        const modeAcceptedWindowCountPenaltyUsd = Number(breakdown.mode_accepted_window_count_penalty_usd || 0);
        const modeAcceptedWindowSharePenaltyUsd = Number(breakdown.mode_accepted_window_share_penalty_usd || 0);
        const modeNonAcceptingActiveWindowStreakPenaltyUsd = Number(breakdown.mode_non_accepting_active_window_streak_penalty_usd || 0);
        const modeNonAcceptingActiveWindowEpisodePenaltyUsd = Number(breakdown.mode_non_accepting_active_window_episode_penalty_usd || 0);
        const modeAcceptingWindowAcceptedSharePenaltyUsd = Number(breakdown.mode_accepting_window_accepted_share_penalty_usd || 0);
        const modeAcceptingWindowAcceptedSizeSharePenaltyUsd = Number(breakdown.mode_accepting_window_accepted_size_share_penalty_usd || 0);
        const modeTopTwoAcceptingWindowAcceptedSharePenaltyUsd = Number(breakdown.mode_top_two_accepting_window_accepted_share_penalty_usd || 0);
        const modeTopTwoAcceptingWindowAcceptedSizeSharePenaltyUsd = Number(breakdown.mode_top_two_accepting_window_accepted_size_share_penalty_usd || 0);
        const modeAcceptingWindowAcceptedConcentrationIndexPenaltyUsd = Number(breakdown.mode_accepting_window_accepted_concentration_index_penalty_usd || 0);
        const modeAcceptingWindowAcceptedSizeConcentrationIndexPenaltyUsd = Number(breakdown.mode_accepting_window_accepted_size_concentration_index_penalty_usd || 0);
        const walletCountPenaltyUsd = Number(breakdown.wallet_count_penalty_usd || 0);
        const marketCountPenaltyUsd = Number(breakdown.market_count_penalty_usd || 0);
        const entryPriceBandCountPenaltyUsd = Number(breakdown.entry_price_band_count_penalty_usd || 0);
        const timeToCloseBandCountPenaltyUsd = Number(breakdown.time_to_close_band_count_penalty_usd || 0);
        const walletConcentrationPenaltyUsd = Number(breakdown.wallet_concentration_penalty_usd || 0);
        const marketConcentrationPenaltyUsd = Number(breakdown.market_concentration_penalty_usd || 0);
        const entryPriceBandConcentrationPenaltyUsd = Number(breakdown.entry_price_band_concentration_penalty_usd || 0);
        const timeToCloseBandConcentrationPenaltyUsd = Number(breakdown.time_to_close_band_concentration_penalty_usd || 0);
        const walletSizeConcentrationPenaltyUsd = Number(breakdown.wallet_size_concentration_penalty_usd || 0);
        const marketSizeConcentrationPenaltyUsd = Number(breakdown.market_size_concentration_penalty_usd || 0);
        const entryPriceBandSizeConcentrationPenaltyUsd = Number(breakdown.entry_price_band_size_concentration_penalty_usd || 0);
        const timeToCloseBandSizeConcentrationPenaltyUsd = Number(breakdown.time_to_close_band_size_concentration_penalty_usd || 0);
        const parts = [
            `${formatNumber(scoreUsd, 2)} = ${formatDollar(pnlUsd)}`,
            `dd ${formatDollar(-drawdownPenaltyUsd)}`
        ];
        if (Math.abs(windowStddevPenaltyUsd) > 1e-9)
            parts.push(`std ${formatDollar(-windowStddevPenaltyUsd)}`);
        if (Math.abs(worstWindowPenaltyUsd) > 1e-9)
            parts.push(`worst ${formatDollar(-worstWindowPenaltyUsd)}`);
        if (Math.abs(pauseGuardPenaltyUsd) > 1e-9)
            parts.push(`pause ${formatDollar(-pauseGuardPenaltyUsd)}`);
        if (Math.abs(dailyGuardWindowPenaltyUsd) > 1e-9)
            parts.push(`d-freq ${formatDollar(-dailyGuardWindowPenaltyUsd)}`);
        if (Math.abs(liveGuardWindowPenaltyUsd) > 1e-9)
            parts.push(`p-freq ${formatDollar(-liveGuardWindowPenaltyUsd)}`);
        if (Math.abs(dailyGuardRestartWindowPenaltyUsd) > 1e-9)
            parts.push(`d-rst ${formatDollar(-dailyGuardRestartWindowPenaltyUsd)}`);
        if (Math.abs(liveGuardRestartWindowPenaltyUsd) > 1e-9)
            parts.push(`p-rst ${formatDollar(-liveGuardRestartWindowPenaltyUsd)}`);
        if (Math.abs(openExposurePenaltyUsd) > 1e-9)
            parts.push(`exp ${formatDollar(-openExposurePenaltyUsd)}`);
        if (Math.abs(windowEndOpenExposurePenaltyUsd) > 1e-9)
            parts.push(`carry ${formatDollar(-windowEndOpenExposurePenaltyUsd)}`);
        if (Math.abs(avgWindowEndOpenExposurePenaltyUsd) > 1e-9)
            parts.push(`carry-avg ${formatDollar(-avgWindowEndOpenExposurePenaltyUsd)}`);
        if (Math.abs(carryWindowPenaltyUsd) > 1e-9)
            parts.push(`c-freq ${formatDollar(-carryWindowPenaltyUsd)}`);
        if (Math.abs(carryRestartWindowPenaltyUsd) > 1e-9)
            parts.push(`c-rst ${formatDollar(-carryRestartWindowPenaltyUsd)}`);
        if (Math.abs(resolvedSharePenaltyUsd) > 1e-9)
            parts.push(`cov ${formatDollar(-resolvedSharePenaltyUsd)}`);
        if (Math.abs(resolvedSizeSharePenaltyUsd) > 1e-9)
            parts.push(`sz-cov ${formatDollar(-resolvedSizeSharePenaltyUsd)}`);
        if (Math.abs(windowInactivityPenaltyUsd) > 1e-9)
            parts.push(`w-idle ${formatDollar(-windowInactivityPenaltyUsd)}`);
        if (Math.abs(acceptedWindowCountPenaltyUsd) > 1e-9)
            parts.push(`acc-win ${formatDollar(-acceptedWindowCountPenaltyUsd)}`);
        if (Math.abs(acceptedWindowSharePenaltyUsd) > 1e-9)
            parts.push(`acc-freq ${formatDollar(-acceptedWindowSharePenaltyUsd)}`);
        if (Math.abs(nonAcceptingActiveWindowStreakPenaltyUsd) > 1e-9)
            parts.push(`acc-gap ${formatDollar(-nonAcceptingActiveWindowStreakPenaltyUsd)}`);
        if (Math.abs(nonAcceptingActiveWindowEpisodePenaltyUsd) > 1e-9)
            parts.push(`acc-runs ${formatDollar(-nonAcceptingActiveWindowEpisodePenaltyUsd)}`);
        if (Math.abs(acceptingWindowAcceptedSharePenaltyUsd) > 1e-9)
            parts.push(`top-acc ${formatDollar(-acceptingWindowAcceptedSharePenaltyUsd)}`);
        if (Math.abs(acceptingWindowAcceptedSizeSharePenaltyUsd) > 1e-9)
            parts.push(`top-acc$ ${formatDollar(-acceptingWindowAcceptedSizeSharePenaltyUsd)}`);
        if (Math.abs(topTwoAcceptingWindowAcceptedSharePenaltyUsd) > 1e-9)
            parts.push(`top2-acc ${formatDollar(-topTwoAcceptingWindowAcceptedSharePenaltyUsd)}`);
        if (Math.abs(topTwoAcceptingWindowAcceptedSizeSharePenaltyUsd) > 1e-9)
            parts.push(`top2-acc$ ${formatDollar(-topTwoAcceptingWindowAcceptedSizeSharePenaltyUsd)}`);
        if (Math.abs(acceptingWindowAcceptedConcentrationIndexPenaltyUsd) > 1e-9)
            parts.push(`acc-ci ${formatDollar(-acceptingWindowAcceptedConcentrationIndexPenaltyUsd)}`);
        if (Math.abs(acceptingWindowAcceptedSizeConcentrationIndexPenaltyUsd) > 1e-9)
            parts.push(`acc-ci$ ${formatDollar(-acceptingWindowAcceptedSizeConcentrationIndexPenaltyUsd)}`);
        if (Math.abs(worstWindowResolvedSharePenaltyUsd) > 1e-9)
            parts.push(`w-cov ${formatDollar(-worstWindowResolvedSharePenaltyUsd)}`);
        if (Math.abs(worstWindowResolvedSizeSharePenaltyUsd) > 1e-9)
            parts.push(`w-sz-cov ${formatDollar(-worstWindowResolvedSizeSharePenaltyUsd)}`);
        if (Math.abs(worstActiveWindowAcceptedPenaltyUsd) > 1e-9)
            parts.push(`w-acc ${formatDollar(-worstActiveWindowAcceptedPenaltyUsd)}`);
        if (Math.abs(worstActiveWindowAcceptedSizePenaltyUsd) > 1e-9)
            parts.push(`w-acc$ ${formatDollar(-worstActiveWindowAcceptedSizePenaltyUsd)}`);
        if (Math.abs(modeResolvedSharePenaltyUsd) > 1e-9)
            parts.push(`m-cov ${formatDollar(-modeResolvedSharePenaltyUsd)}`);
        if (Math.abs(modeResolvedSizeSharePenaltyUsd) > 1e-9)
            parts.push(`m-sz-cov ${formatDollar(-modeResolvedSizeSharePenaltyUsd)}`);
        if (Math.abs(modeWorstWindowResolvedSharePenaltyUsd) > 1e-9)
            parts.push(`mw-cov ${formatDollar(-modeWorstWindowResolvedSharePenaltyUsd)}`);
        if (Math.abs(modeWorstWindowResolvedSizeSharePenaltyUsd) > 1e-9)
            parts.push(`mw-sz-cov ${formatDollar(-modeWorstWindowResolvedSizeSharePenaltyUsd)}`);
        if (Math.abs(modeActiveWindowAcceptedSharePenaltyUsd) > 1e-9)
            parts.push(`m-acc-mix ${formatDollar(-modeActiveWindowAcceptedSharePenaltyUsd)}`);
        if (Math.abs(modeActiveWindowAcceptedSizeSharePenaltyUsd) > 1e-9)
            parts.push(`m-acc-mix$ ${formatDollar(-modeActiveWindowAcceptedSizeSharePenaltyUsd)}`);
        if (Math.abs(modeWorstActiveWindowAcceptedPenaltyUsd) > 1e-9)
            parts.push(`mw-acc ${formatDollar(-modeWorstActiveWindowAcceptedPenaltyUsd)}`);
        if (Math.abs(modeWorstActiveWindowAcceptedSizePenaltyUsd) > 1e-9)
            parts.push(`mw-acc$ ${formatDollar(-modeWorstActiveWindowAcceptedSizePenaltyUsd)}`);
        if (Math.abs(modeLossPenaltyUsd) > 1e-9)
            parts.push(`mode ${formatDollar(-modeLossPenaltyUsd)}`);
        if (Math.abs(modeInactivityPenaltyUsd) > 1e-9)
            parts.push(`idle ${formatDollar(-modeInactivityPenaltyUsd)}`);
        if (Math.abs(modeAcceptedWindowCountPenaltyUsd) > 1e-9)
            parts.push(`m-acc-win ${formatDollar(-modeAcceptedWindowCountPenaltyUsd)}`);
        if (Math.abs(modeAcceptedWindowSharePenaltyUsd) > 1e-9)
            parts.push(`m-acc-freq ${formatDollar(-modeAcceptedWindowSharePenaltyUsd)}`);
        if (Math.abs(modeNonAcceptingActiveWindowStreakPenaltyUsd) > 1e-9)
            parts.push(`m-acc-gap ${formatDollar(-modeNonAcceptingActiveWindowStreakPenaltyUsd)}`);
        if (Math.abs(modeNonAcceptingActiveWindowEpisodePenaltyUsd) > 1e-9)
            parts.push(`m-acc-runs ${formatDollar(-modeNonAcceptingActiveWindowEpisodePenaltyUsd)}`);
        if (Math.abs(modeAcceptingWindowAcceptedSharePenaltyUsd) > 1e-9)
            parts.push(`m-top-acc ${formatDollar(-modeAcceptingWindowAcceptedSharePenaltyUsd)}`);
        if (Math.abs(modeAcceptingWindowAcceptedSizeSharePenaltyUsd) > 1e-9)
            parts.push(`m-top-acc$ ${formatDollar(-modeAcceptingWindowAcceptedSizeSharePenaltyUsd)}`);
        if (Math.abs(modeTopTwoAcceptingWindowAcceptedSharePenaltyUsd) > 1e-9)
            parts.push(`m-top2-acc ${formatDollar(-modeTopTwoAcceptingWindowAcceptedSharePenaltyUsd)}`);
        if (Math.abs(modeTopTwoAcceptingWindowAcceptedSizeSharePenaltyUsd) > 1e-9)
            parts.push(`m-top2-acc$ ${formatDollar(-modeTopTwoAcceptingWindowAcceptedSizeSharePenaltyUsd)}`);
        if (Math.abs(modeAcceptingWindowAcceptedConcentrationIndexPenaltyUsd) > 1e-9)
            parts.push(`m-acc-ci ${formatDollar(-modeAcceptingWindowAcceptedConcentrationIndexPenaltyUsd)}`);
        if (Math.abs(modeAcceptingWindowAcceptedSizeConcentrationIndexPenaltyUsd) > 1e-9)
            parts.push(`m-acc-ci$ ${formatDollar(-modeAcceptingWindowAcceptedSizeConcentrationIndexPenaltyUsd)}`);
        if (Math.abs(walletCountPenaltyUsd) > 1e-9)
            parts.push(`wallet# ${formatDollar(-walletCountPenaltyUsd)}`);
        if (Math.abs(marketCountPenaltyUsd) > 1e-9)
            parts.push(`market# ${formatDollar(-marketCountPenaltyUsd)}`);
        if (Math.abs(entryPriceBandCountPenaltyUsd) > 1e-9)
            parts.push(`band# ${formatDollar(-entryPriceBandCountPenaltyUsd)}`);
        if (Math.abs(timeToCloseBandCountPenaltyUsd) > 1e-9)
            parts.push(`hzn# ${formatDollar(-timeToCloseBandCountPenaltyUsd)}`);
        if (Math.abs(walletConcentrationPenaltyUsd) > 1e-9)
            parts.push(`wallet ${formatDollar(-walletConcentrationPenaltyUsd)}`);
        if (Math.abs(marketConcentrationPenaltyUsd) > 1e-9)
            parts.push(`market ${formatDollar(-marketConcentrationPenaltyUsd)}`);
        if (Math.abs(entryPriceBandConcentrationPenaltyUsd) > 1e-9)
            parts.push(`band ${formatDollar(-entryPriceBandConcentrationPenaltyUsd)}`);
        if (Math.abs(timeToCloseBandConcentrationPenaltyUsd) > 1e-9)
            parts.push(`hzn ${formatDollar(-timeToCloseBandConcentrationPenaltyUsd)}`);
        if (Math.abs(walletSizeConcentrationPenaltyUsd) > 1e-9)
            parts.push(`wallet$ ${formatDollar(-walletSizeConcentrationPenaltyUsd)}`);
        if (Math.abs(marketSizeConcentrationPenaltyUsd) > 1e-9)
            parts.push(`market$ ${formatDollar(-marketSizeConcentrationPenaltyUsd)}`);
        if (Math.abs(entryPriceBandSizeConcentrationPenaltyUsd) > 1e-9)
            parts.push(`band$ ${formatDollar(-entryPriceBandSizeConcentrationPenaltyUsd)}`);
        if (Math.abs(timeToCloseBandSizeConcentrationPenaltyUsd) > 1e-9)
            parts.push(`hzn$ ${formatDollar(-timeToCloseBandSizeConcentrationPenaltyUsd)}`);
        return parts.join(' | ');
    }
    catch {
        return '-';
    }
}
function replaySearchScoreWeightSummary(row) {
  if (!row)
    return '-';
    const parts = [];
    const pushIfActive = (label, value) => {
        const numeric = Number(value || 0);
        if (Math.abs(numeric) > 1e-9)
            parts.push(`${label} ${formatNumber(numeric, 2)}x`);
    };
  pushIfActive('dd', row.drawdown_penalty);
  pushIfActive('std', row.window_stddev_penalty);
  pushIfActive('worst', row.worst_window_penalty);
  pushIfActive('pause', row.pause_guard_penalty);
    pushIfActive('d-freq', row.daily_guard_window_penalty);
    pushIfActive('p-freq', row.live_guard_window_penalty);
    pushIfActive('d-rst', row.daily_guard_restart_window_penalty);
    pushIfActive('p-rst', row.live_guard_restart_window_penalty);
    pushIfActive('exp', row.open_exposure_penalty);
    pushIfActive('carry', row.window_end_open_exposure_penalty);
    pushIfActive('carry-avg', row.avg_window_end_open_exposure_penalty);
    pushIfActive('c-freq', row.carry_window_penalty);
    pushIfActive('c-rst', row.carry_restart_window_penalty);
    pushIfActive('cov', row.resolved_share_penalty);
    pushIfActive('sz-cov', row.resolved_size_share_penalty);
    pushIfActive('w-idle', row.window_inactivity_penalty);
    pushIfActive('acc-win', row.accepted_window_count_penalty);
    pushIfActive('acc-freq', row.accepted_window_share_penalty);
    pushIfActive('acc-gap', row.non_accepting_active_window_streak_penalty);
    pushIfActive('acc-runs', row.non_accepting_active_window_episode_penalty);
    pushIfActive('top-acc', row.accepting_window_accepted_share_penalty);
    pushIfActive('top-acc$', row.accepting_window_accepted_size_share_penalty);
    pushIfActive('top2-acc', row.top_two_accepting_window_accepted_share_penalty);
    pushIfActive('top2-acc$', row.top_two_accepting_window_accepted_size_share_penalty);
    pushIfActive('acc-ci', row.accepting_window_accepted_concentration_index_penalty);
    pushIfActive('acc-ci$', row.accepting_window_accepted_size_concentration_index_penalty);
    pushIfActive('w-cov', row.worst_window_resolved_share_penalty);
    pushIfActive('w-sz-cov', row.worst_window_resolved_size_share_penalty);
    pushIfActive('w-acc', row.worst_active_window_accepted_penalty);
    pushIfActive('w-acc$', row.worst_active_window_accepted_size_penalty);
    pushIfActive('m-cov', row.mode_resolved_share_penalty);
    pushIfActive('m-sz-cov', row.mode_resolved_size_share_penalty);
    pushIfActive('mw-cov', row.mode_worst_window_resolved_share_penalty);
    pushIfActive('mw-sz-cov', row.mode_worst_window_resolved_size_share_penalty);
    pushIfActive('m-acc-mix', row.mode_active_window_accepted_share_penalty);
    pushIfActive('m-acc-mix$', row.mode_active_window_accepted_size_share_penalty);
    pushIfActive('mw-acc', row.mode_worst_active_window_accepted_penalty);
    pushIfActive('mw-acc$', row.mode_worst_active_window_accepted_size_penalty);
    pushIfActive('mode', row.mode_loss_penalty);
    pushIfActive('idle', row.mode_inactivity_penalty);
    pushIfActive('m-acc-win', row.mode_accepted_window_count_penalty);
    pushIfActive('m-acc-freq', row.mode_accepted_window_share_penalty);
    pushIfActive('m-acc-gap', row.mode_non_accepting_active_window_streak_penalty);
    pushIfActive('m-acc-runs', row.mode_non_accepting_active_window_episode_penalty);
    pushIfActive('m-top-acc', row.mode_accepting_window_accepted_share_penalty);
    pushIfActive('m-top-acc$', row.mode_accepting_window_accepted_size_share_penalty);
    pushIfActive('m-top2-acc', row.mode_top_two_accepting_window_accepted_share_penalty);
    pushIfActive('m-top2-acc$', row.mode_top_two_accepting_window_accepted_size_share_penalty);
    pushIfActive('m-acc-ci', row.mode_accepting_window_accepted_concentration_index_penalty);
    pushIfActive('m-acc-ci$', row.mode_accepting_window_accepted_size_concentration_index_penalty);
    pushIfActive('wallet#', row.wallet_count_penalty);
    pushIfActive('market#', row.market_count_penalty);
    pushIfActive('band#', row.entry_price_band_count_penalty);
    pushIfActive('hzn#', row.time_to_close_band_count_penalty);
    pushIfActive('wallet', row.wallet_concentration_penalty);
    pushIfActive('market', row.market_concentration_penalty);
    pushIfActive('band', row.entry_price_band_concentration_penalty);
    pushIfActive('hzn', row.time_to_close_band_concentration_penalty);
    pushIfActive('wallet$', row.wallet_size_concentration_penalty);
    pushIfActive('market$', row.market_size_concentration_penalty);
    pushIfActive('band$', row.entry_price_band_size_concentration_penalty);
    pushIfActive('hzn$', row.time_to_close_band_size_concentration_penalty);
    return parts.length ? parts.join(' | ') : 'none';
}

function replaySearchHasParticipation(payload) {
  if (Number(payload.accepted_count || 0) > 0)
    return true;
  if (Number(payload.accepted_size_usd || 0) > 0)
    return true;
  if (Number(payload.resolved_count || 0) > 0)
    return true;
  if (Number(payload.resolved_size_usd || 0) > 0)
    return true;
  if (Math.abs(Number(payload.total_pnl_usd || 0)) > 1e-9)
    return true;
  if (Number(payload.peak_open_exposure_usd || 0) > 0)
    return true;
  if (Number(payload.window_end_open_exposure_usd || 0) > 0)
    return true;
  if (Number(payload.window_end_live_guard_triggered || 0) > 0)
    return true;
  if (Number(payload.window_end_daily_guard_triggered || 0) > 0)
    return true;
  return false;
}
function replaySearchActiveWindowCountFromPayload(payload) {
  const explicit = Number(payload.active_window_count || 0);
  if (explicit > 0)
    return explicit;
  const windowCount = Number(payload.window_count || 0);
  if (windowCount <= 1)
    return replaySearchHasParticipation(payload) ? 1 : 0;
  return Math.max(windowCount - Number(payload.inactive_window_count || 0), 0);
}
function replaySearchAcceptedWindowCountFromPayload(payload) {
  const explicit = Number(payload.accepted_window_count || 0);
  if (explicit > 0)
    return explicit;
  const acceptedCount = Number(payload.accepted_count || 0);
  const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
  const windowCount = Number(payload.window_count || 0);
  if (windowCount <= 1)
    return acceptedCount > 0 || acceptedSizeUsd > 0 ? 1 : 0;
  if (acceptedCount > 0 || acceptedSizeUsd > 0)
    return 1;
  return 0;
}
function replaySearchAcceptedWindowShareFromPayload(payload) {
  const acceptedWindowCount = replaySearchAcceptedWindowCountFromPayload(payload);
  const activeWindowCount = replaySearchActiveWindowCountFromPayload(payload);
  if (activeWindowCount > 0)
    return acceptedWindowCount / activeWindowCount;
  return acceptedWindowCount > 0 ? 1 : 0;
}
function replaySearchWorstWindowPnlFromPayload(payload) {
  if (payload.worst_window_pnl_usd != null)
    return Number(payload.worst_window_pnl_usd || 0);
  const totalPnlUsd = Number(payload.total_pnl_usd || 0);
  const windowCount = Number(payload.window_count || 0);
  if (windowCount <= 1)
    return totalPnlUsd;
  return Math.min(totalPnlUsd, 0);
}
function replaySearchWorstActiveWindowResolvedShareFromPayload(payload) {
  if (payload.worst_active_window_resolved_share != null)
    return Number(payload.worst_active_window_resolved_share || 0);
  if (payload.worst_window_resolved_share != null)
    return Number(payload.worst_window_resolved_share || 0);
  const acceptedCount = Number(payload.accepted_count || 0);
  if (acceptedCount <= 0)
    return 1;
  const resolvedCount = Number(payload.resolved_count || 0);
  const windowCount = Number(payload.window_count || 0);
  const exactShare = resolvedCount / acceptedCount;
  if (windowCount <= 1)
    return exactShare;
  return 0;
}
function replaySearchWorstActiveWindowResolvedSizeShareFromPayload(payload) {
  if (payload.worst_active_window_resolved_size_share != null)
    return Number(payload.worst_active_window_resolved_size_share || 0);
  if (payload.worst_window_resolved_size_share != null)
    return Number(payload.worst_window_resolved_size_share || 0);
  const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
  if (acceptedSizeUsd <= 0)
    return 1;
  const resolvedSizeUsd = Number(payload.resolved_size_usd || 0);
  const windowCount = Number(payload.window_count || 0);
  const exactShare = resolvedSizeUsd / acceptedSizeUsd;
  if (windowCount <= 1)
    return exactShare;
  return 0;
}
function replaySearchWorstWindowPnlFromSummaryRow(row) {
  if (!row)
    return 0;
  if (row.result_json) {
    try {
      const parsed = JSON.parse(row.result_json);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
        return replaySearchWorstWindowPnlFromPayload(parsed);
    } catch {
    }
  }
  return Number(row.worst_window_pnl_usd || 0);
}
function replaySearchMaxNonAcceptingActiveWindowStreakFromPayload(payload) {
  if (payload.max_non_accepting_active_window_streak != null)
    return Math.max(Number(payload.max_non_accepting_active_window_streak || 0), 0);
  const activeWindowCount = replaySearchActiveWindowCountFromPayload(payload);
  const acceptedWindowCount = replaySearchAcceptedWindowCountFromPayload(payload);
  if (activeWindowCount <= 0)
    return 0;
  if (acceptedWindowCount <= 0)
    return activeWindowCount;
  return Math.max(activeWindowCount - acceptedWindowCount, 0);
}
function replaySearchNonAcceptingActiveWindowEpisodeCountFromPayload(payload) {
  if (payload.non_accepting_active_window_episode_count != null)
    return Math.max(Number(payload.non_accepting_active_window_episode_count || 0), 0);
  return replaySearchMaxNonAcceptingActiveWindowStreakFromPayload(payload) > 0 ? 1 : 0;
}
function replaySearchMaxAcceptingWindowAcceptedShareFromPayload(payload) {
  if (payload.max_accepting_window_accepted_share != null)
    return Number(payload.max_accepting_window_accepted_share || 0);
  const acceptedWindowCount = replaySearchAcceptedWindowCountFromPayload(payload);
  const acceptedCount = Number(payload.accepted_count || 0);
  return acceptedWindowCount <= 1 && acceptedCount > 0 ? 1 : 0;
}
function replaySearchTopTwoAcceptingWindowAcceptedShareFromPayload(payload) {
  if (payload.top_two_accepting_window_accepted_share != null)
    return Number(payload.top_two_accepting_window_accepted_share || 0);
  const acceptedWindowCount = replaySearchAcceptedWindowCountFromPayload(payload);
  const acceptedCount = Number(payload.accepted_count || 0);
  if (acceptedWindowCount <= 2 && acceptedCount > 0)
    return 1;
  return replaySearchMaxAcceptingWindowAcceptedShareFromPayload(payload);
}
function replaySearchMaxAcceptingWindowAcceptedSizeShareFromPayload(payload) {
  if (payload.max_accepting_window_accepted_size_share != null)
    return Number(payload.max_accepting_window_accepted_size_share || 0);
  const acceptedWindowCount = replaySearchAcceptedWindowCountFromPayload(payload);
  const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
  return acceptedWindowCount <= 1 && acceptedSizeUsd > 0 ? 1 : 0;
}
function replaySearchTopTwoAcceptingWindowAcceptedSizeShareFromPayload(payload) {
  if (payload.top_two_accepting_window_accepted_size_share != null)
    return Number(payload.top_two_accepting_window_accepted_size_share || 0);
  const acceptedWindowCount = replaySearchAcceptedWindowCountFromPayload(payload);
  const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
  if (acceptedWindowCount <= 2 && acceptedSizeUsd > 0)
    return 1;
  return replaySearchMaxAcceptingWindowAcceptedSizeShareFromPayload(payload);
}
function replaySearchAcceptingWindowAcceptedConcentrationIndexFromPayload(payload) {
  if (payload.accepting_window_accepted_concentration_index != null)
    return Number(payload.accepting_window_accepted_concentration_index || 0);
  const acceptedWindowCount = replaySearchAcceptedWindowCountFromPayload(payload);
  const acceptedCount = Number(payload.accepted_count || 0);
  return acceptedWindowCount <= 1 && acceptedCount > 0 ? 1 : 0;
}
function replaySearchAcceptingWindowAcceptedSizeConcentrationIndexFromPayload(payload) {
  if (payload.accepting_window_accepted_size_concentration_index != null)
    return Number(payload.accepting_window_accepted_size_concentration_index || 0);
  const acceptedWindowCount = replaySearchAcceptedWindowCountFromPayload(payload);
  const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
  return acceptedWindowCount <= 1 && acceptedSizeUsd > 0 ? 1 : 0;
}
function replaySearchModeHasParticipation(payload) {
  if (Number(payload.accepted_count || 0) > 0)
    return true;
  if (Number(payload.accepted_size_usd || 0) > 0)
    return true;
  if (Number(payload.resolved_count || 0) > 0)
    return true;
  if (Number(payload.resolved_size_usd || 0) > 0)
    return true;
  return Math.abs(Number(payload.total_pnl_usd || 0)) > 1e-9;
}
function replaySearchModeActiveWindowCountFromPayload(payload, windowCount) {
  if (windowCount <= 1)
    return replaySearchModeHasParticipation(payload) ? 1 : 0;
  return Math.max(windowCount - Number(payload.inactive_window_count || 0), 0);
}
function replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount) {
  const explicit = Number(payload.accepted_window_count || 0);
  if (explicit > 0)
    return explicit;
  const acceptedCount = Number(payload.accepted_count || 0);
  const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
  if (windowCount <= 1)
    return acceptedCount > 0 || acceptedSizeUsd > 0 ? 1 : 0;
  if (acceptedCount > 0 || acceptedSizeUsd > 0)
    return 1;
  return 0;
}
function replaySearchModeAcceptedWindowShareFromPayload(payload, windowCount) {
  const acceptedWindowCount = replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount);
  const activeWindowCount = replaySearchModeActiveWindowCountFromPayload(payload, windowCount);
  if (activeWindowCount > 0)
    return acceptedWindowCount / activeWindowCount;
  return acceptedWindowCount > 0 ? 1 : 0;
}
function replaySearchModeMaxNonAcceptingActiveWindowStreakFromPayload(payload, windowCount) {
  if (payload.max_non_accepting_active_window_streak != null)
    return Math.max(Number(payload.max_non_accepting_active_window_streak || 0), 0);
  const activeWindowCount = replaySearchModeActiveWindowCountFromPayload(payload, windowCount);
  const acceptedWindowCount = replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount);
  if (activeWindowCount <= 0)
    return 0;
  if (acceptedWindowCount <= 0)
    return activeWindowCount;
  return Math.max(activeWindowCount - acceptedWindowCount, 0);
}
function replaySearchModeNonAcceptingActiveWindowEpisodeCountFromPayload(payload, windowCount) {
  if (payload.non_accepting_active_window_episode_count != null)
    return Math.max(Number(payload.non_accepting_active_window_episode_count || 0), 0);
  return replaySearchModeMaxNonAcceptingActiveWindowStreakFromPayload(payload, windowCount) > 0 ? 1 : 0;
}
function replaySearchModeMaxAcceptingWindowAcceptedShareFromPayload(payload, windowCount) {
  if (payload.max_accepting_window_accepted_share != null)
    return Number(payload.max_accepting_window_accepted_share || 0);
  const acceptedWindowCount = replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount);
  const acceptedCount = Number(payload.accepted_count || 0);
  return acceptedWindowCount <= 1 && acceptedCount > 0 ? 1 : 0;
}
function replaySearchModeTopTwoAcceptingWindowAcceptedShareFromPayload(payload, windowCount) {
  if (payload.top_two_accepting_window_accepted_share != null)
    return Number(payload.top_two_accepting_window_accepted_share || 0);
  const acceptedWindowCount = replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount);
  const acceptedCount = Number(payload.accepted_count || 0);
  if (acceptedWindowCount <= 2 && acceptedCount > 0)
    return 1;
  return replaySearchModeMaxAcceptingWindowAcceptedShareFromPayload(payload, windowCount);
}
function replaySearchModeMaxAcceptingWindowAcceptedSizeShareFromPayload(payload, windowCount) {
  if (payload.max_accepting_window_accepted_size_share != null)
    return Number(payload.max_accepting_window_accepted_size_share || 0);
  const acceptedWindowCount = replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount);
  const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
  return acceptedWindowCount <= 1 && acceptedSizeUsd > 0 ? 1 : 0;
}
function replaySearchModeTopTwoAcceptingWindowAcceptedSizeShareFromPayload(payload, windowCount) {
  if (payload.top_two_accepting_window_accepted_size_share != null)
    return Number(payload.top_two_accepting_window_accepted_size_share || 0);
  const acceptedWindowCount = replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount);
  const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
  if (acceptedWindowCount <= 2 && acceptedSizeUsd > 0)
    return 1;
  return replaySearchModeMaxAcceptingWindowAcceptedSizeShareFromPayload(payload, windowCount);
}

function replaySearchCarryWindowShareFromPayload(payload) {
  if (payload.carry_window_share != null)
    return Number(payload.carry_window_share || 0);
  const activeWindowCount = replaySearchActiveWindowCountFromPayload(payload);
  if (activeWindowCount <= 0)
    return 0;
  return Number(payload.carry_window_count || 0) / activeWindowCount;
}
function replaySearchCarryRestartWindowShareFromPayload(payload) {
    if (payload.carry_restart_window_share != null)
        return Number(payload.carry_restart_window_share || 0);
  const opportunityCount = Number(payload.carry_restart_window_opportunity_count || 0);
  if (opportunityCount <= 0)
    return 0;
    return Number(payload.carry_restart_window_count || 0) / opportunityCount;
}
function replaySearchDailyGuardWindowShareFromPayload(payload) {
    if (payload.daily_guard_window_share != null)
        return Number(payload.daily_guard_window_share || 0);
    const activeWindowCount = replaySearchActiveWindowCountFromPayload(payload);
    if (activeWindowCount <= 0)
        return 0;
    return Number(payload.daily_guard_window_count || 0) / activeWindowCount;
}
function replaySearchDailyGuardRestartWindowShareFromPayload(payload) {
    if (payload.daily_guard_restart_window_share != null)
        return Number(payload.daily_guard_restart_window_share || 0);
    const opportunityCount = Number(payload.daily_guard_restart_window_opportunity_count || 0);
    if (opportunityCount <= 0)
        return 0;
    return Number(payload.daily_guard_restart_window_count || 0) / opportunityCount;
}
function replaySearchLiveGuardRestartWindowShareFromPayload(payload) {
    if (payload.live_guard_restart_window_share != null)
        return Number(payload.live_guard_restart_window_share || 0);
    const opportunityCount = Number(payload.live_guard_restart_window_opportunity_count || 0);
    if (opportunityCount <= 0)
        return 0;
    return Number(payload.live_guard_restart_window_count || 0) / opportunityCount;
}

function replaySearchAvgWindowEndOpenExposureShareFromPayload(payload) {
  if (payload.avg_window_end_open_exposure_share != null)
    return Number(payload.avg_window_end_open_exposure_share || 0);
  return Number(payload.max_window_end_open_exposure_share ?? payload.window_end_open_exposure_share ?? 0);
}

function replaySearchLiveGuardWindowShareFromPayload(payload) {
  if (payload.live_guard_window_share != null)
    return Number(payload.live_guard_window_share || 0);
  const activeWindowCount = replaySearchActiveWindowCountFromPayload(payload);
  if (activeWindowCount <= 0)
    return 0;
  return Number(payload.live_guard_window_count || 0) / activeWindowCount;
}
function replaySearchScoreDriftSummary(bestRaw, currentRaw) {
    if (!bestRaw || !currentRaw)
        return '-';
    const parse = (raw) => {
        try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return null;
            const rawBreakdown = parsed.score_breakdown;
            if (!rawBreakdown || typeof rawBreakdown !== 'object' || Array.isArray(rawBreakdown))
                return null;
            const breakdown = rawBreakdown;
            return {
                score_usd: Number(breakdown.score_usd || 0),
                pnl_usd: Number(breakdown.pnl_usd || 0),
                drawdown_penalty_usd: Number(breakdown.drawdown_penalty_usd || 0),
                window_stddev_penalty_usd: Number(breakdown.window_stddev_penalty_usd || 0),
                worst_window_penalty_usd: Number(breakdown.worst_window_penalty_usd || 0),
                pause_guard_penalty_usd: Number(breakdown.pause_guard_penalty_usd || 0),
                daily_guard_window_penalty_usd: Number(breakdown.daily_guard_window_penalty_usd || 0),
                live_guard_window_penalty_usd: Number(breakdown.live_guard_window_penalty_usd || 0),
                daily_guard_restart_window_penalty_usd: Number(breakdown.daily_guard_restart_window_penalty_usd || 0),
                live_guard_restart_window_penalty_usd: Number(breakdown.live_guard_restart_window_penalty_usd || 0),
                open_exposure_penalty_usd: Number(breakdown.open_exposure_penalty_usd || 0),
                window_end_open_exposure_penalty_usd: Number(breakdown.window_end_open_exposure_penalty_usd || 0),
                avg_window_end_open_exposure_penalty_usd: Number(breakdown.avg_window_end_open_exposure_penalty_usd || 0),
                carry_window_penalty_usd: Number(breakdown.carry_window_penalty_usd || 0),
                carry_restart_window_penalty_usd: Number(breakdown.carry_restart_window_penalty_usd || 0),
                resolved_share_penalty_usd: Number(breakdown.resolved_share_penalty_usd || 0),
                resolved_size_share_penalty_usd: Number(breakdown.resolved_size_share_penalty_usd || 0),
                window_inactivity_penalty_usd: Number(breakdown.window_inactivity_penalty_usd || 0),
                accepted_window_count_penalty_usd: Number(breakdown.accepted_window_count_penalty_usd || 0),
                accepted_window_share_penalty_usd: Number(breakdown.accepted_window_share_penalty_usd || 0),
                non_accepting_active_window_streak_penalty_usd: Number(breakdown.non_accepting_active_window_streak_penalty_usd || 0),
                non_accepting_active_window_episode_penalty_usd: Number(breakdown.non_accepting_active_window_episode_penalty_usd || 0),
                accepting_window_accepted_share_penalty_usd: Number(breakdown.accepting_window_accepted_share_penalty_usd || 0),
                accepting_window_accepted_size_share_penalty_usd: Number(breakdown.accepting_window_accepted_size_share_penalty_usd || 0),
                top_two_accepting_window_accepted_share_penalty_usd: Number(breakdown.top_two_accepting_window_accepted_share_penalty_usd || 0),
                top_two_accepting_window_accepted_size_share_penalty_usd: Number(breakdown.top_two_accepting_window_accepted_size_share_penalty_usd || 0),
                accepting_window_accepted_concentration_index_penalty_usd: Number(breakdown.accepting_window_accepted_concentration_index_penalty_usd || 0),
                accepting_window_accepted_size_concentration_index_penalty_usd: Number(breakdown.accepting_window_accepted_size_concentration_index_penalty_usd || 0),
                worst_window_resolved_share_penalty_usd: Number(breakdown.worst_window_resolved_share_penalty_usd || 0),
                worst_window_resolved_size_share_penalty_usd: Number(breakdown.worst_window_resolved_size_share_penalty_usd || 0),
                worst_active_window_accepted_penalty_usd: Number(breakdown.worst_active_window_accepted_penalty_usd || 0),
                worst_active_window_accepted_size_penalty_usd: Number(breakdown.worst_active_window_accepted_size_penalty_usd || 0),
                mode_resolved_share_penalty_usd: Number(breakdown.mode_resolved_share_penalty_usd || 0),
                mode_resolved_size_share_penalty_usd: Number(breakdown.mode_resolved_size_share_penalty_usd || 0),
                mode_worst_window_resolved_share_penalty_usd: Number(breakdown.mode_worst_window_resolved_share_penalty_usd || 0),
                mode_worst_window_resolved_size_share_penalty_usd: Number(breakdown.mode_worst_window_resolved_size_share_penalty_usd || 0),
                mode_active_window_accepted_share_penalty_usd: Number(breakdown.mode_active_window_accepted_share_penalty_usd || 0),
                mode_active_window_accepted_size_share_penalty_usd: Number(breakdown.mode_active_window_accepted_size_share_penalty_usd || 0),
                mode_worst_active_window_accepted_penalty_usd: Number(breakdown.mode_worst_active_window_accepted_penalty_usd || 0),
                mode_worst_active_window_accepted_size_penalty_usd: Number(breakdown.mode_worst_active_window_accepted_size_penalty_usd || 0),
                mode_loss_penalty_usd: Number(breakdown.mode_loss_penalty_usd || 0),
                mode_inactivity_penalty_usd: Number(breakdown.mode_inactivity_penalty_usd || 0),
                mode_accepted_window_count_penalty_usd: Number(breakdown.mode_accepted_window_count_penalty_usd || 0),
                mode_accepted_window_share_penalty_usd: Number(breakdown.mode_accepted_window_share_penalty_usd || 0),
                mode_non_accepting_active_window_streak_penalty_usd: Number(breakdown.mode_non_accepting_active_window_streak_penalty_usd || 0),
                mode_non_accepting_active_window_episode_penalty_usd: Number(breakdown.mode_non_accepting_active_window_episode_penalty_usd || 0),
                mode_accepting_window_accepted_share_penalty_usd: Number(breakdown.mode_accepting_window_accepted_share_penalty_usd || 0),
                mode_accepting_window_accepted_size_share_penalty_usd: Number(breakdown.mode_accepting_window_accepted_size_share_penalty_usd || 0),
                mode_top_two_accepting_window_accepted_share_penalty_usd: Number(breakdown.mode_top_two_accepting_window_accepted_share_penalty_usd || 0),
                mode_top_two_accepting_window_accepted_size_share_penalty_usd: Number(breakdown.mode_top_two_accepting_window_accepted_size_share_penalty_usd || 0),
                mode_accepting_window_accepted_concentration_index_penalty_usd: Number(breakdown.mode_accepting_window_accepted_concentration_index_penalty_usd || 0),
                mode_accepting_window_accepted_size_concentration_index_penalty_usd: Number(breakdown.mode_accepting_window_accepted_size_concentration_index_penalty_usd || 0),
                wallet_count_penalty_usd: Number(breakdown.wallet_count_penalty_usd || 0),
                market_count_penalty_usd: Number(breakdown.market_count_penalty_usd || 0),
                entry_price_band_count_penalty_usd: Number(breakdown.entry_price_band_count_penalty_usd || 0),
                time_to_close_band_count_penalty_usd: Number(breakdown.time_to_close_band_count_penalty_usd || 0),
                wallet_concentration_penalty_usd: Number(breakdown.wallet_concentration_penalty_usd || 0),
                market_concentration_penalty_usd: Number(breakdown.market_concentration_penalty_usd || 0),
                entry_price_band_concentration_penalty_usd: Number(breakdown.entry_price_band_concentration_penalty_usd || 0),
                time_to_close_band_concentration_penalty_usd: Number(breakdown.time_to_close_band_concentration_penalty_usd || 0),
                wallet_size_concentration_penalty_usd: Number(breakdown.wallet_size_concentration_penalty_usd || 0),
                market_size_concentration_penalty_usd: Number(breakdown.market_size_concentration_penalty_usd || 0),
                entry_price_band_size_concentration_penalty_usd: Number(breakdown.entry_price_band_size_concentration_penalty_usd || 0),
                time_to_close_band_size_concentration_penalty_usd: Number(breakdown.time_to_close_band_size_concentration_penalty_usd || 0)
            };
        }
        catch {
            return null;
        }
    };
    const best = parse(bestRaw);
    const current = parse(currentRaw);
    if (!best || !current)
        return '-';
    const scoreDelta = best.score_usd - current.score_usd;
    const pnlDelta = best.pnl_usd - current.pnl_usd;
    const drawdownDelta = current.drawdown_penalty_usd - best.drawdown_penalty_usd;
    const stddevDelta = current.window_stddev_penalty_usd - best.window_stddev_penalty_usd;
    const worstDelta = current.worst_window_penalty_usd - best.worst_window_penalty_usd;
    const pauseDelta = current.pause_guard_penalty_usd - best.pause_guard_penalty_usd;
    const dailyGuardWindowDelta = current.daily_guard_window_penalty_usd - best.daily_guard_window_penalty_usd;
    const liveGuardWindowDelta = current.live_guard_window_penalty_usd - best.live_guard_window_penalty_usd;
    const dailyGuardRestartWindowDelta = current.daily_guard_restart_window_penalty_usd - best.daily_guard_restart_window_penalty_usd;
    const liveGuardRestartWindowDelta = current.live_guard_restart_window_penalty_usd - best.live_guard_restart_window_penalty_usd;
    const openExposureDelta = current.open_exposure_penalty_usd - best.open_exposure_penalty_usd;
    const carryDelta = current.window_end_open_exposure_penalty_usd - best.window_end_open_exposure_penalty_usd;
    const avgCarryDelta = current.avg_window_end_open_exposure_penalty_usd - best.avg_window_end_open_exposure_penalty_usd;
    const carryWindowDelta = current.carry_window_penalty_usd - best.carry_window_penalty_usd;
    const carryRestartWindowDelta = current.carry_restart_window_penalty_usd - best.carry_restart_window_penalty_usd;
    const coverageDelta = current.resolved_share_penalty_usd - best.resolved_share_penalty_usd;
    const sizeCoverageDelta = current.resolved_size_share_penalty_usd - best.resolved_size_share_penalty_usd;
    const windowInactivityDelta = current.window_inactivity_penalty_usd - best.window_inactivity_penalty_usd;
    const acceptedWindowCountDelta = current.accepted_window_count_penalty_usd - best.accepted_window_count_penalty_usd;
    const acceptedWindowShareDelta = current.accepted_window_share_penalty_usd - best.accepted_window_share_penalty_usd;
    const nonAcceptingActiveWindowStreakDelta = current.non_accepting_active_window_streak_penalty_usd - best.non_accepting_active_window_streak_penalty_usd;
    const nonAcceptingActiveWindowEpisodeDelta = current.non_accepting_active_window_episode_penalty_usd - best.non_accepting_active_window_episode_penalty_usd;
    const acceptingWindowAcceptedShareDelta = current.accepting_window_accepted_share_penalty_usd - best.accepting_window_accepted_share_penalty_usd;
    const acceptingWindowAcceptedSizeShareDelta = current.accepting_window_accepted_size_share_penalty_usd - best.accepting_window_accepted_size_share_penalty_usd;
    const topTwoAcceptingWindowAcceptedShareDelta = current.top_two_accepting_window_accepted_share_penalty_usd - best.top_two_accepting_window_accepted_share_penalty_usd;
    const topTwoAcceptingWindowAcceptedSizeShareDelta = current.top_two_accepting_window_accepted_size_share_penalty_usd - best.top_two_accepting_window_accepted_size_share_penalty_usd;
    const acceptingWindowAcceptedConcentrationIndexDelta = current.accepting_window_accepted_concentration_index_penalty_usd - best.accepting_window_accepted_concentration_index_penalty_usd;
    const acceptingWindowAcceptedSizeConcentrationIndexDelta = current.accepting_window_accepted_size_concentration_index_penalty_usd - best.accepting_window_accepted_size_concentration_index_penalty_usd;
    const worstCoverageDelta = current.worst_window_resolved_share_penalty_usd - best.worst_window_resolved_share_penalty_usd;
    const worstSizeCoverageDelta = current.worst_window_resolved_size_share_penalty_usd - best.worst_window_resolved_size_share_penalty_usd;
    const worstActiveDepthDelta = current.worst_active_window_accepted_penalty_usd - best.worst_active_window_accepted_penalty_usd;
    const worstActiveSizeDepthDelta = current.worst_active_window_accepted_size_penalty_usd - best.worst_active_window_accepted_size_penalty_usd;
    const modeCoverageDelta = current.mode_resolved_share_penalty_usd - best.mode_resolved_share_penalty_usd;
    const modeSizeCoverageDelta = current.mode_resolved_size_share_penalty_usd - best.mode_resolved_size_share_penalty_usd;
    const modeWorstCoverageDelta = current.mode_worst_window_resolved_share_penalty_usd - best.mode_worst_window_resolved_share_penalty_usd;
    const modeWorstSizeCoverageDelta = current.mode_worst_window_resolved_size_share_penalty_usd - best.mode_worst_window_resolved_size_share_penalty_usd;
    const modeActiveWindowMixDelta = current.mode_active_window_accepted_share_penalty_usd - best.mode_active_window_accepted_share_penalty_usd;
    const modeActiveWindowSizeMixDelta = current.mode_active_window_accepted_size_share_penalty_usd - best.mode_active_window_accepted_size_share_penalty_usd;
    const modeWorstActiveDepthDelta = current.mode_worst_active_window_accepted_penalty_usd - best.mode_worst_active_window_accepted_penalty_usd;
    const modeWorstActiveSizeDepthDelta = current.mode_worst_active_window_accepted_size_penalty_usd - best.mode_worst_active_window_accepted_size_penalty_usd;
    const modeDelta = current.mode_loss_penalty_usd - best.mode_loss_penalty_usd;
    const inactivityDelta = current.mode_inactivity_penalty_usd - best.mode_inactivity_penalty_usd;
    const modeAcceptedWindowCountDelta = current.mode_accepted_window_count_penalty_usd - best.mode_accepted_window_count_penalty_usd;
    const modeAcceptedWindowShareDelta = current.mode_accepted_window_share_penalty_usd - best.mode_accepted_window_share_penalty_usd;
    const modeNonAcceptingActiveWindowStreakDelta = current.mode_non_accepting_active_window_streak_penalty_usd - best.mode_non_accepting_active_window_streak_penalty_usd;
    const modeNonAcceptingActiveWindowEpisodeDelta = current.mode_non_accepting_active_window_episode_penalty_usd - best.mode_non_accepting_active_window_episode_penalty_usd;
    const modeAcceptingWindowAcceptedShareDelta = current.mode_accepting_window_accepted_share_penalty_usd - best.mode_accepting_window_accepted_share_penalty_usd;
    const modeAcceptingWindowAcceptedSizeShareDelta = current.mode_accepting_window_accepted_size_share_penalty_usd - best.mode_accepting_window_accepted_size_share_penalty_usd;
    const modeTopTwoAcceptingWindowAcceptedShareDelta = current.mode_top_two_accepting_window_accepted_share_penalty_usd - best.mode_top_two_accepting_window_accepted_share_penalty_usd;
    const modeTopTwoAcceptingWindowAcceptedSizeShareDelta = current.mode_top_two_accepting_window_accepted_size_share_penalty_usd - best.mode_top_two_accepting_window_accepted_size_share_penalty_usd;
    const modeAcceptingWindowAcceptedConcentrationIndexDelta = current.mode_accepting_window_accepted_concentration_index_penalty_usd - best.mode_accepting_window_accepted_concentration_index_penalty_usd;
    const modeAcceptingWindowAcceptedSizeConcentrationIndexDelta = current.mode_accepting_window_accepted_size_concentration_index_penalty_usd - best.mode_accepting_window_accepted_size_concentration_index_penalty_usd;
    const walletCountDelta = current.wallet_count_penalty_usd - best.wallet_count_penalty_usd;
    const marketCountDelta = current.market_count_penalty_usd - best.market_count_penalty_usd;
    const entryBandCountDelta = current.entry_price_band_count_penalty_usd - best.entry_price_band_count_penalty_usd;
    const horizonCountDelta = current.time_to_close_band_count_penalty_usd - best.time_to_close_band_count_penalty_usd;
    const walletDelta = current.wallet_concentration_penalty_usd - best.wallet_concentration_penalty_usd;
    const marketDelta = current.market_concentration_penalty_usd - best.market_concentration_penalty_usd;
    const entryBandDelta = current.entry_price_band_concentration_penalty_usd - best.entry_price_band_concentration_penalty_usd;
    const horizonDelta = current.time_to_close_band_concentration_penalty_usd - best.time_to_close_band_concentration_penalty_usd;
    const walletSizeDelta = current.wallet_size_concentration_penalty_usd - best.wallet_size_concentration_penalty_usd;
    const marketSizeDelta = current.market_size_concentration_penalty_usd - best.market_size_concentration_penalty_usd;
    const entryBandSizeDelta = current.entry_price_band_size_concentration_penalty_usd - best.entry_price_band_size_concentration_penalty_usd;
    const horizonSizeDelta = current.time_to_close_band_size_concentration_penalty_usd - best.time_to_close_band_size_concentration_penalty_usd;
    const parts = [
        `${formatNumber(scoreDelta, 2)} = pnl ${formatDollar(pnlDelta)}`,
        `dd ${formatDollar(drawdownDelta)}`
    ];
    if (Math.abs(stddevDelta) > 1e-9)
        parts.push(`std ${formatDollar(stddevDelta)}`);
    if (Math.abs(worstDelta) > 1e-9)
        parts.push(`worst ${formatDollar(worstDelta)}`);
    if (Math.abs(pauseDelta) > 1e-9)
        parts.push(`pause ${formatDollar(pauseDelta)}`);
    if (Math.abs(dailyGuardWindowDelta) > 1e-9)
        parts.push(`d-freq ${formatDollar(dailyGuardWindowDelta)}`);
    if (Math.abs(liveGuardWindowDelta) > 1e-9)
        parts.push(`p-freq ${formatDollar(liveGuardWindowDelta)}`);
    if (Math.abs(dailyGuardRestartWindowDelta) > 1e-9)
        parts.push(`d-rst ${formatDollar(dailyGuardRestartWindowDelta)}`);
    if (Math.abs(liveGuardRestartWindowDelta) > 1e-9)
        parts.push(`p-rst ${formatDollar(liveGuardRestartWindowDelta)}`);
    if (Math.abs(openExposureDelta) > 1e-9)
        parts.push(`exp ${formatDollar(openExposureDelta)}`);
    if (Math.abs(carryDelta) > 1e-9)
        parts.push(`carry ${formatDollar(carryDelta)}`);
    if (Math.abs(avgCarryDelta) > 1e-9)
        parts.push(`carry-avg ${formatDollar(avgCarryDelta)}`);
    if (Math.abs(carryWindowDelta) > 1e-9)
        parts.push(`c-freq ${formatDollar(carryWindowDelta)}`);
    if (Math.abs(carryRestartWindowDelta) > 1e-9)
        parts.push(`c-rst ${formatDollar(carryRestartWindowDelta)}`);
    if (Math.abs(coverageDelta) > 1e-9)
        parts.push(`cov ${formatDollar(coverageDelta)}`);
    if (Math.abs(sizeCoverageDelta) > 1e-9)
        parts.push(`sz-cov ${formatDollar(sizeCoverageDelta)}`);
    if (Math.abs(windowInactivityDelta) > 1e-9)
        parts.push(`w-idle ${formatDollar(windowInactivityDelta)}`);
    if (Math.abs(acceptedWindowCountDelta) > 1e-9)
        parts.push(`acc-win ${formatDollar(acceptedWindowCountDelta)}`);
    if (Math.abs(acceptedWindowShareDelta) > 1e-9)
        parts.push(`acc-freq ${formatDollar(acceptedWindowShareDelta)}`);
    if (Math.abs(nonAcceptingActiveWindowStreakDelta) > 1e-9)
        parts.push(`acc-gap ${formatDollar(nonAcceptingActiveWindowStreakDelta)}`);
    if (Math.abs(nonAcceptingActiveWindowEpisodeDelta) > 1e-9)
        parts.push(`acc-runs ${formatDollar(nonAcceptingActiveWindowEpisodeDelta)}`);
    if (Math.abs(acceptingWindowAcceptedShareDelta) > 1e-9)
        parts.push(`top-acc ${formatDollar(acceptingWindowAcceptedShareDelta)}`);
    if (Math.abs(acceptingWindowAcceptedSizeShareDelta) > 1e-9)
        parts.push(`top-acc$ ${formatDollar(acceptingWindowAcceptedSizeShareDelta)}`);
    if (Math.abs(topTwoAcceptingWindowAcceptedShareDelta) > 1e-9)
        parts.push(`top2-acc ${formatDollar(topTwoAcceptingWindowAcceptedShareDelta)}`);
    if (Math.abs(topTwoAcceptingWindowAcceptedSizeShareDelta) > 1e-9)
        parts.push(`top2-acc$ ${formatDollar(topTwoAcceptingWindowAcceptedSizeShareDelta)}`);
    if (Math.abs(acceptingWindowAcceptedConcentrationIndexDelta) > 1e-9)
        parts.push(`acc-ci ${formatDollar(acceptingWindowAcceptedConcentrationIndexDelta)}`);
    if (Math.abs(acceptingWindowAcceptedSizeConcentrationIndexDelta) > 1e-9)
        parts.push(`acc-ci$ ${formatDollar(acceptingWindowAcceptedSizeConcentrationIndexDelta)}`);
    if (Math.abs(worstCoverageDelta) > 1e-9)
        parts.push(`w-cov ${formatDollar(worstCoverageDelta)}`);
    if (Math.abs(worstSizeCoverageDelta) > 1e-9)
        parts.push(`w-sz-cov ${formatDollar(worstSizeCoverageDelta)}`);
    if (Math.abs(worstActiveDepthDelta) > 1e-9)
        parts.push(`w-acc ${formatDollar(worstActiveDepthDelta)}`);
    if (Math.abs(worstActiveSizeDepthDelta) > 1e-9)
        parts.push(`w-acc$ ${formatDollar(worstActiveSizeDepthDelta)}`);
    if (Math.abs(modeCoverageDelta) > 1e-9)
        parts.push(`m-cov ${formatDollar(modeCoverageDelta)}`);
    if (Math.abs(modeSizeCoverageDelta) > 1e-9)
        parts.push(`m-sz-cov ${formatDollar(modeSizeCoverageDelta)}`);
    if (Math.abs(modeWorstCoverageDelta) > 1e-9)
        parts.push(`mw-cov ${formatDollar(modeWorstCoverageDelta)}`);
    if (Math.abs(modeWorstSizeCoverageDelta) > 1e-9)
        parts.push(`mw-sz-cov ${formatDollar(modeWorstSizeCoverageDelta)}`);
    if (Math.abs(modeActiveWindowMixDelta) > 1e-9)
        parts.push(`m-acc-mix ${formatDollar(modeActiveWindowMixDelta)}`);
    if (Math.abs(modeActiveWindowSizeMixDelta) > 1e-9)
        parts.push(`m-acc-mix$ ${formatDollar(modeActiveWindowSizeMixDelta)}`);
    if (Math.abs(modeWorstActiveDepthDelta) > 1e-9)
        parts.push(`mw-acc ${formatDollar(modeWorstActiveDepthDelta)}`);
    if (Math.abs(modeWorstActiveSizeDepthDelta) > 1e-9)
        parts.push(`mw-acc$ ${formatDollar(modeWorstActiveSizeDepthDelta)}`);
    if (Math.abs(modeDelta) > 1e-9)
        parts.push(`mode ${formatDollar(modeDelta)}`);
    if (Math.abs(inactivityDelta) > 1e-9)
        parts.push(`idle ${formatDollar(inactivityDelta)}`);
    if (Math.abs(modeAcceptedWindowCountDelta) > 1e-9)
        parts.push(`m-acc-win ${formatDollar(modeAcceptedWindowCountDelta)}`);
    if (Math.abs(modeAcceptedWindowShareDelta) > 1e-9)
        parts.push(`m-acc-freq ${formatDollar(modeAcceptedWindowShareDelta)}`);
    if (Math.abs(modeNonAcceptingActiveWindowStreakDelta) > 1e-9)
        parts.push(`m-acc-gap ${formatDollar(modeNonAcceptingActiveWindowStreakDelta)}`);
    if (Math.abs(modeNonAcceptingActiveWindowEpisodeDelta) > 1e-9)
        parts.push(`m-acc-runs ${formatDollar(modeNonAcceptingActiveWindowEpisodeDelta)}`);
    if (Math.abs(modeAcceptingWindowAcceptedShareDelta) > 1e-9)
        parts.push(`m-top-acc ${formatDollar(modeAcceptingWindowAcceptedShareDelta)}`);
    if (Math.abs(modeAcceptingWindowAcceptedSizeShareDelta) > 1e-9)
        parts.push(`m-top-acc$ ${formatDollar(modeAcceptingWindowAcceptedSizeShareDelta)}`);
    if (Math.abs(modeTopTwoAcceptingWindowAcceptedShareDelta) > 1e-9)
        parts.push(`m-top2-acc ${formatDollar(modeTopTwoAcceptingWindowAcceptedShareDelta)}`);
    if (Math.abs(modeTopTwoAcceptingWindowAcceptedSizeShareDelta) > 1e-9)
        parts.push(`m-top2-acc$ ${formatDollar(modeTopTwoAcceptingWindowAcceptedSizeShareDelta)}`);
    if (Math.abs(modeAcceptingWindowAcceptedConcentrationIndexDelta) > 1e-9)
        parts.push(`m-acc-ci ${formatDollar(modeAcceptingWindowAcceptedConcentrationIndexDelta)}`);
    if (Math.abs(modeAcceptingWindowAcceptedSizeConcentrationIndexDelta) > 1e-9)
        parts.push(`m-acc-ci$ ${formatDollar(modeAcceptingWindowAcceptedSizeConcentrationIndexDelta)}`);
    if (Math.abs(walletCountDelta) > 1e-9)
        parts.push(`wallet# ${formatDollar(walletCountDelta)}`);
    if (Math.abs(marketCountDelta) > 1e-9)
        parts.push(`market# ${formatDollar(marketCountDelta)}`);
    if (Math.abs(entryBandCountDelta) > 1e-9)
        parts.push(`band# ${formatDollar(entryBandCountDelta)}`);
    if (Math.abs(horizonCountDelta) > 1e-9)
        parts.push(`hzn# ${formatDollar(horizonCountDelta)}`);
    if (Math.abs(walletDelta) > 1e-9)
        parts.push(`wallet ${formatDollar(walletDelta)}`);
    if (Math.abs(marketDelta) > 1e-9)
        parts.push(`market ${formatDollar(marketDelta)}`);
    if (Math.abs(entryBandDelta) > 1e-9)
        parts.push(`band ${formatDollar(entryBandDelta)}`);
    if (Math.abs(horizonDelta) > 1e-9)
        parts.push(`hzn ${formatDollar(horizonDelta)}`);
    if (Math.abs(walletSizeDelta) > 1e-9)
        parts.push(`wallet$ ${formatDollar(walletSizeDelta)}`);
    if (Math.abs(marketSizeDelta) > 1e-9)
        parts.push(`market$ ${formatDollar(marketSizeDelta)}`);
    if (Math.abs(entryBandSizeDelta) > 1e-9)
        parts.push(`band$ ${formatDollar(entryBandSizeDelta)}`);
    if (Math.abs(horizonSizeDelta) > 1e-9)
        parts.push(`hzn$ ${formatDollar(horizonSizeDelta)}`);
    return parts.join(' | ');
}
function replaySearchTraderConcentrationSummary(bestRaw, currentRaw, constraintsRaw, sharePenalty, countPenalty, sizePenalty) {
    const parse = (raw) => {
        if (!raw)
            return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
        try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
            const concentration = parsed.trader_concentration;
            if (!concentration || typeof concentration !== 'object' || Array.isArray(concentration)) {
                return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
            }
            const payload = concentration;
            return {
                count: Number(payload.trader_count || 0),
                peakCount: Number(payload.peak_trader_count || payload.trader_count || 0),
                acceptedShare: Number(payload.top_accepted_share || 0),
                absPnlShare: Number(payload.top_abs_pnl_share || 0),
                sizeShare: Number(payload.top_size_share || 0)
            };
        }
        catch {
            return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
        }
    };
    const best = parse(bestRaw);
    const current = parse(currentRaw);
    const limits = (() => {
        if (!constraintsRaw)
            return { count: 0, accepted: 0, pnl: 0, size: 0 };
        try {
            const parsed = JSON.parse(constraintsRaw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return { count: 0, accepted: 0, pnl: 0, size: 0 };
            return {
                count: Number(parsed.min_trader_count || 0),
                accepted: Number(parsed.max_top_trader_accepted_share || 0),
                pnl: Number(parsed.max_top_trader_abs_pnl_share || 0),
                size: Number(parsed.max_top_trader_size_share || 0)
            };
        }
        catch {
            return { count: 0, accepted: 0, pnl: 0, size: 0 };
        }
    })();
    const hasActiveGuard = limits.count > 0 || limits.accepted > 0 || limits.pnl > 0 || limits.size > 0;
    if (best.acceptedShare == null && current.acceptedShare == null && best.count == null && current.count == null && !hasActiveGuard) {
        return { summary: '-', hasActiveGuard: false, overLimit: false };
    }
    const parts = [];
    if (best.count != null || best.acceptedShare != null || best.absPnlShare != null || best.sizeShare != null) {
        const countText = best.count != null
            ? `worst cnt ${formatCount(best.count)}${best.peakCount != null && best.peakCount > best.count ? ` peak ${formatCount(best.peakCount)}` : ''}`
            : null;
        const mixText = best.acceptedShare != null || best.absPnlShare != null || best.sizeShare != null ? `n ${formatPct(best.acceptedShare, 0)} sz ${formatPct(best.sizeShare, 0)} pnl ${formatPct(best.absPnlShare, 0)}` : null;
        parts.push(`best ${[countText, mixText].filter(Boolean).join(' ')}`);
    }
    if (current.count != null || current.acceptedShare != null || current.absPnlShare != null || current.sizeShare != null) {
        const countText = current.count != null
            ? `worst cnt ${formatCount(current.count)}${current.peakCount != null && current.peakCount > current.count ? ` peak ${formatCount(current.peakCount)}` : ''}`
            : null;
        const mixText = current.acceptedShare != null || current.absPnlShare != null || current.sizeShare != null ? `n ${formatPct(current.acceptedShare, 0)} sz ${formatPct(current.sizeShare, 0)} pnl ${formatPct(current.absPnlShare, 0)}` : null;
        parts.push(`cur ${[countText, mixText].filter(Boolean).join(' ')}`);
    }
    if (limits.count > 0 || limits.accepted > 0 || limits.pnl > 0 || limits.size > 0) {
        const countText = limits.count > 0 ? `min cnt ${formatCount(limits.count)}` : null;
        const mixText = limits.accepted > 0 || limits.pnl > 0 || limits.size > 0 ? `max n ${formatPct(limits.accepted, 0)} sz ${formatPct(limits.size, 0)} pnl ${formatPct(limits.pnl, 0)}` : null;
        parts.push([countText, mixText].filter(Boolean).join(' '));
    }
    const resolvedSharePenalty = Math.max(Number(sharePenalty || 0), 0);
    const resolvedCountPenalty = Math.max(Number(countPenalty || 0), 0);
    const resolvedSizePenalty = Math.max(Number(sizePenalty || 0), 0);
    if (resolvedSharePenalty > 0)
        parts.push(`share pen ${resolvedSharePenalty.toFixed(2)}x`);
    if (resolvedCountPenalty > 0)
        parts.push(`cnt pen ${resolvedCountPenalty.toFixed(2)}x`);
    if (resolvedSizePenalty > 0)
        parts.push(`size pen ${resolvedSizePenalty.toFixed(2)}x`);
    const overLimit = (limits.count > 0 && ((best.count ?? 0) < limits.count || (current.count ?? 0) < limits.count))
        || (limits.accepted > 0 && ((best.acceptedShare ?? 0) > limits.accepted || (current.acceptedShare ?? 0) > limits.accepted))
        || (limits.pnl > 0 && ((best.absPnlShare ?? 0) > limits.pnl || (current.absPnlShare ?? 0) > limits.pnl))
        || (limits.size > 0 && ((best.sizeShare ?? 0) > limits.size || (current.sizeShare ?? 0) > limits.size));
    return {
        summary: parts.join(' | ') || '-',
        hasActiveGuard,
        overLimit
    };
}
function replaySearchMarketConcentrationSummary(bestRaw, currentRaw, constraintsRaw, sharePenalty, countPenalty, sizePenalty) {
    const parse = (raw) => {
        if (!raw)
            return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
        try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
            const concentration = parsed.market_concentration;
            if (!concentration || typeof concentration !== 'object' || Array.isArray(concentration)) {
                return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
            }
            const payload = concentration;
            return {
                count: Number(payload.market_count || 0),
                peakCount: Number(payload.peak_market_count || payload.market_count || 0),
                acceptedShare: Number(payload.top_accepted_share || 0),
                absPnlShare: Number(payload.top_abs_pnl_share || 0),
                sizeShare: Number(payload.top_size_share || 0)
            };
        }
        catch {
            return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
        }
    };
    const best = parse(bestRaw);
    const current = parse(currentRaw);
    const limits = (() => {
        if (!constraintsRaw)
            return { count: 0, accepted: 0, pnl: 0, size: 0 };
        try {
            const parsed = JSON.parse(constraintsRaw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return { count: 0, accepted: 0, pnl: 0, size: 0 };
            return {
                count: Number(parsed.min_market_count || 0),
                accepted: Number(parsed.max_top_market_accepted_share || 0),
                pnl: Number(parsed.max_top_market_abs_pnl_share || 0),
                size: Number(parsed.max_top_market_size_share || 0)
            };
        }
        catch {
            return { count: 0, accepted: 0, pnl: 0, size: 0 };
        }
    })();
    const hasActiveGuard = limits.count > 0 || limits.accepted > 0 || limits.pnl > 0 || limits.size > 0;
    if (best.acceptedShare == null && current.acceptedShare == null && best.count == null && current.count == null && !hasActiveGuard) {
        return { summary: '-', hasActiveGuard: false, overLimit: false };
    }
    const parts = [];
    if (best.count != null || best.acceptedShare != null || best.absPnlShare != null || best.sizeShare != null) {
        const countText = best.count != null
            ? `worst cnt ${formatCount(best.count)}${best.peakCount != null && best.peakCount > best.count ? ` peak ${formatCount(best.peakCount)}` : ''}`
            : null;
        const mixText = best.acceptedShare != null || best.absPnlShare != null || best.sizeShare != null ? `n ${formatPct(best.acceptedShare, 0)} sz ${formatPct(best.sizeShare, 0)} pnl ${formatPct(best.absPnlShare, 0)}` : null;
        parts.push(`best ${[countText, mixText].filter(Boolean).join(' ')}`);
    }
    if (current.count != null || current.acceptedShare != null || current.absPnlShare != null || current.sizeShare != null) {
        const countText = current.count != null
            ? `worst cnt ${formatCount(current.count)}${current.peakCount != null && current.peakCount > current.count ? ` peak ${formatCount(current.peakCount)}` : ''}`
            : null;
        const mixText = current.acceptedShare != null || current.absPnlShare != null || current.sizeShare != null ? `n ${formatPct(current.acceptedShare, 0)} sz ${formatPct(current.sizeShare, 0)} pnl ${formatPct(current.absPnlShare, 0)}` : null;
        parts.push(`cur ${[countText, mixText].filter(Boolean).join(' ')}`);
    }
    if (limits.count > 0 || limits.accepted > 0 || limits.pnl > 0 || limits.size > 0) {
        const countText = limits.count > 0 ? `min cnt ${formatCount(limits.count)}` : null;
        const mixText = limits.accepted > 0 || limits.pnl > 0 || limits.size > 0 ? `max n ${formatPct(limits.accepted, 0)} sz ${formatPct(limits.size, 0)} pnl ${formatPct(limits.pnl, 0)}` : null;
        parts.push([countText, mixText].filter(Boolean).join(' '));
    }
    const resolvedSharePenalty = Math.max(Number(sharePenalty || 0), 0);
    const resolvedCountPenalty = Math.max(Number(countPenalty || 0), 0);
    const resolvedSizePenalty = Math.max(Number(sizePenalty || 0), 0);
    if (resolvedSharePenalty > 0)
        parts.push(`share pen ${resolvedSharePenalty.toFixed(2)}x`);
    if (resolvedCountPenalty > 0)
        parts.push(`cnt pen ${resolvedCountPenalty.toFixed(2)}x`);
    if (resolvedSizePenalty > 0)
        parts.push(`size pen ${resolvedSizePenalty.toFixed(2)}x`);
    const overLimit = (limits.count > 0 && ((best.count ?? 0) < limits.count || (current.count ?? 0) < limits.count))
        || (limits.accepted > 0 && ((best.acceptedShare ?? 0) > limits.accepted || (current.acceptedShare ?? 0) > limits.accepted))
        || (limits.pnl > 0 && ((best.absPnlShare ?? 0) > limits.pnl || (current.absPnlShare ?? 0) > limits.pnl))
        || (limits.size > 0 && ((best.sizeShare ?? 0) > limits.size || (current.sizeShare ?? 0) > limits.size));
    return {
        summary: parts.join(' | ') || '-',
        hasActiveGuard,
        overLimit
    };
}
function replaySearchEntryPriceBandConcentrationSummary(bestRaw, currentRaw, constraintsRaw, sharePenalty, countPenalty, sizePenalty) {
    const parse = (raw) => {
        if (!raw)
            return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
        try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
            const concentration = parsed.entry_price_band_concentration;
            if (!concentration || typeof concentration !== 'object' || Array.isArray(concentration)) {
                return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
            }
            const payload = concentration;
            return {
                count: Number(payload.entry_price_band_count || 0),
                peakCount: Number(payload.peak_entry_price_band_count || payload.entry_price_band_count || 0),
                acceptedShare: Number(payload.top_accepted_share || 0),
                absPnlShare: Number(payload.top_abs_pnl_share || 0),
                sizeShare: Number(payload.top_size_share || 0)
            };
        }
        catch {
            return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
        }
    };
    const best = parse(bestRaw);
    const current = parse(currentRaw);
    const limits = (() => {
        if (!constraintsRaw)
            return { count: 0, accepted: 0, pnl: 0, size: 0 };
        try {
            const parsed = JSON.parse(constraintsRaw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return { count: 0, accepted: 0, pnl: 0, size: 0 };
            return {
                count: Number(parsed.min_entry_price_band_count || 0),
                accepted: Number(parsed.max_top_entry_price_band_accepted_share || 0),
                pnl: Number(parsed.max_top_entry_price_band_abs_pnl_share || 0),
                size: Number(parsed.max_top_entry_price_band_size_share || 0)
            };
        }
        catch {
            return { count: 0, accepted: 0, pnl: 0, size: 0 };
        }
    })();
    const hasActiveGuard = limits.count > 0 || limits.accepted > 0 || limits.pnl > 0 || limits.size > 0;
    if (best.acceptedShare == null && current.acceptedShare == null && best.count == null && current.count == null && !hasActiveGuard) {
        return { summary: '-', hasActiveGuard: false, overLimit: false };
    }
    const parts = [];
    if (best.count != null || best.acceptedShare != null || best.absPnlShare != null || best.sizeShare != null) {
        const countText = best.count != null
            ? `worst cnt ${formatCount(best.count)}${best.peakCount != null && best.peakCount > best.count ? ` peak ${formatCount(best.peakCount)}` : ''}`
            : null;
        const mixText = best.acceptedShare != null || best.absPnlShare != null || best.sizeShare != null ? `n ${formatPct(best.acceptedShare, 0)} sz ${formatPct(best.sizeShare, 0)} pnl ${formatPct(best.absPnlShare, 0)}` : null;
        parts.push(`best ${[countText, mixText].filter(Boolean).join(' ')}`);
    }
    if (current.count != null || current.acceptedShare != null || current.absPnlShare != null || current.sizeShare != null) {
        const countText = current.count != null
            ? `worst cnt ${formatCount(current.count)}${current.peakCount != null && current.peakCount > current.count ? ` peak ${formatCount(current.peakCount)}` : ''}`
            : null;
        const mixText = current.acceptedShare != null || current.absPnlShare != null || current.sizeShare != null ? `n ${formatPct(current.acceptedShare, 0)} sz ${formatPct(current.sizeShare, 0)} pnl ${formatPct(current.absPnlShare, 0)}` : null;
        parts.push(`cur ${[countText, mixText].filter(Boolean).join(' ')}`);
    }
    if (limits.count > 0 || limits.accepted > 0 || limits.pnl > 0 || limits.size > 0) {
        const countText = limits.count > 0 ? `min cnt ${formatCount(limits.count)}` : null;
        const mixText = limits.accepted > 0 || limits.pnl > 0 || limits.size > 0 ? `max n ${formatPct(limits.accepted, 0)} sz ${formatPct(limits.size, 0)} pnl ${formatPct(limits.pnl, 0)}` : null;
        parts.push([countText, mixText].filter(Boolean).join(' '));
    }
    const resolvedSharePenalty = Math.max(Number(sharePenalty || 0), 0);
    const resolvedCountPenalty = Math.max(Number(countPenalty || 0), 0);
    const resolvedSizePenalty = Math.max(Number(sizePenalty || 0), 0);
    if (resolvedSharePenalty > 0)
        parts.push(`share pen ${resolvedSharePenalty.toFixed(2)}x`);
    if (resolvedCountPenalty > 0)
        parts.push(`cnt pen ${resolvedCountPenalty.toFixed(2)}x`);
    if (resolvedSizePenalty > 0)
        parts.push(`size pen ${resolvedSizePenalty.toFixed(2)}x`);
    const overLimit = (limits.count > 0 && ((best.count ?? 0) < limits.count || (current.count ?? 0) < limits.count))
        || (limits.accepted > 0 && ((best.acceptedShare ?? 0) > limits.accepted || (current.acceptedShare ?? 0) > limits.accepted))
        || (limits.pnl > 0 && ((best.absPnlShare ?? 0) > limits.pnl || (current.absPnlShare ?? 0) > limits.pnl))
        || (limits.size > 0 && ((best.sizeShare ?? 0) > limits.size || (current.sizeShare ?? 0) > limits.size));
    return {
        summary: parts.join(' | ') || '-',
        hasActiveGuard,
        overLimit
    };
}
function replaySearchTimeToCloseBandConcentrationSummary(bestRaw, currentRaw, constraintsRaw, sharePenalty, countPenalty, sizePenalty) {
    const parse = (raw) => {
        if (!raw)
            return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
        try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
            const concentration = parsed.time_to_close_band_concentration;
            if (!concentration || typeof concentration !== 'object' || Array.isArray(concentration)) {
                return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
            }
            const payload = concentration;
            return {
                count: Number(payload.time_to_close_band_count || 0),
                peakCount: Number(payload.peak_time_to_close_band_count || payload.time_to_close_band_count || 0),
                acceptedShare: Number(payload.top_accepted_share || 0),
                absPnlShare: Number(payload.top_abs_pnl_share || 0),
                sizeShare: Number(payload.top_size_share || 0)
            };
        }
        catch {
            return { count: null, peakCount: null, acceptedShare: null, absPnlShare: null, sizeShare: null };
        }
    };
    const best = parse(bestRaw);
    const current = parse(currentRaw);
    const limits = (() => {
        if (!constraintsRaw)
            return { count: 0, accepted: 0, pnl: 0, size: 0 };
        try {
            const parsed = JSON.parse(constraintsRaw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
                return { count: 0, accepted: 0, pnl: 0, size: 0 };
            return {
                count: Number(parsed.min_time_to_close_band_count || 0),
                accepted: Number(parsed.max_top_time_to_close_band_accepted_share || 0),
                pnl: Number(parsed.max_top_time_to_close_band_abs_pnl_share || 0),
                size: Number(parsed.max_top_time_to_close_band_size_share || 0)
            };
        }
        catch {
            return { count: 0, accepted: 0, pnl: 0, size: 0 };
        }
    })();
    const hasActiveGuard = limits.count > 0 || limits.accepted > 0 || limits.pnl > 0 || limits.size > 0;
    if (best.acceptedShare == null && current.acceptedShare == null && best.count == null && current.count == null && !hasActiveGuard) {
        return { summary: '-', hasActiveGuard: false, overLimit: false };
    }
    const parts = [];
    if (best.count != null || best.acceptedShare != null || best.absPnlShare != null || best.sizeShare != null) {
        const countText = best.count != null
            ? `worst cnt ${formatCount(best.count)}${best.peakCount != null && best.peakCount > best.count ? ` peak ${formatCount(best.peakCount)}` : ''}`
            : null;
        const mixText = best.acceptedShare != null || best.absPnlShare != null || best.sizeShare != null ? `n ${formatPct(best.acceptedShare, 0)} sz ${formatPct(best.sizeShare, 0)} pnl ${formatPct(best.absPnlShare, 0)}` : null;
        parts.push(`best ${[countText, mixText].filter(Boolean).join(' ')}`);
    }
    if (current.count != null || current.acceptedShare != null || current.absPnlShare != null || current.sizeShare != null) {
        const countText = current.count != null
            ? `worst cnt ${formatCount(current.count)}${current.peakCount != null && current.peakCount > current.count ? ` peak ${formatCount(current.peakCount)}` : ''}`
            : null;
        const mixText = current.acceptedShare != null || current.absPnlShare != null || current.sizeShare != null ? `n ${formatPct(current.acceptedShare, 0)} sz ${formatPct(current.sizeShare, 0)} pnl ${formatPct(current.absPnlShare, 0)}` : null;
        parts.push(`cur ${[countText, mixText].filter(Boolean).join(' ')}`);
    }
    if (limits.count > 0 || limits.accepted > 0 || limits.pnl > 0 || limits.size > 0) {
        const countText = limits.count > 0 ? `min cnt ${formatCount(limits.count)}` : null;
        const mixText = limits.accepted > 0 || limits.pnl > 0 || limits.size > 0 ? `max n ${formatPct(limits.accepted, 0)} sz ${formatPct(limits.size, 0)} pnl ${formatPct(limits.pnl, 0)}` : null;
        parts.push([countText, mixText].filter(Boolean).join(' '));
    }
    const resolvedSharePenalty = Math.max(Number(sharePenalty || 0), 0);
    const resolvedCountPenalty = Math.max(Number(countPenalty || 0), 0);
    const resolvedSizePenalty = Math.max(Number(sizePenalty || 0), 0);
    if (resolvedSharePenalty > 0)
        parts.push(`share pen ${resolvedSharePenalty.toFixed(2)}x`);
    if (resolvedCountPenalty > 0)
        parts.push(`cnt pen ${resolvedCountPenalty.toFixed(2)}x`);
    if (resolvedSizePenalty > 0)
        parts.push(`size pen ${resolvedSizePenalty.toFixed(2)}x`);
    const overLimit = (limits.count > 0 && ((best.count ?? 0) < limits.count || (current.count ?? 0) < limits.count))
        || (limits.accepted > 0 && ((best.acceptedShare ?? 0) > limits.accepted || (current.acceptedShare ?? 0) > limits.accepted))
        || (limits.pnl > 0 && ((best.absPnlShare ?? 0) > limits.pnl || (current.absPnlShare ?? 0) > limits.pnl))
        || (limits.size > 0 && ((best.sizeShare ?? 0) > limits.size || (current.sizeShare ?? 0) > limits.size));
    return {
        summary: parts.join(' | ') || '-',
        hasActiveGuard,
        overLimit
    };
}
function replaySearchCurrentModeRiskSummary(currentRaw, constraintsRaw, policyRaw) {
    if (!currentRaw || !constraintsRaw)
        return { summary: '-', breachCount: 0, hasActiveGuard: false };
    try {
        const currentParsed = JSON.parse(currentRaw);
        const windowCount = Number((currentParsed === null || currentParsed === void 0 ? void 0 : currentParsed.window_count) || 0);
        const constraintsParsed = JSON.parse(constraintsRaw);
        const enabled = replaySearchEnabledModes(policyRaw);
        const rawSummary = currentParsed?.signal_mode_summary;
        if (!rawSummary || typeof rawSummary !== 'object' || Array.isArray(rawSummary)) {
            return { summary: '-', breachCount: 0, hasActiveGuard: false };
        }
        const constraints = constraintsParsed && typeof constraintsParsed === 'object' && !Array.isArray(constraintsParsed)
            ? constraintsParsed
            : {};
        const summary = rawSummary;
        const mixModesEnabled = enabled.heuristic && enabled.xgboost;
        const totalAccepted = Object.values(summary).reduce((sum, rawValue) => {
            if (!rawValue || typeof rawValue !== 'object' || Array.isArray(rawValue))
                return sum;
            return sum + Number(rawValue.accepted_count || 0);
        }, 0);
        const totalAcceptedSizeUsd = Object.values(summary).reduce((sum, rawMode) => {
            if (!rawMode || typeof rawMode !== 'object' || Array.isArray(rawMode))
                return sum;
            return sum + Number(rawMode.accepted_size_usd || 0);
        }, 0);
        const breaches = [];
        let hasActiveGuard = false;
        const sentinelWorstWindow = -999999999;
        for (const [mode, prefix, shareKey, shareDirection] of [
            ['heuristic', 'heur', 'max_heuristic_accepted_share', 'max'],
            ['xgboost', 'model', 'min_xgboost_accepted_share', 'min']
        ]) {
            if ((mode === 'heuristic' && !enabled.heuristic) || (mode === 'xgboost' && !enabled.xgboost))
                continue;
            const rawValue = summary[mode];
            const payload = rawValue && typeof rawValue === 'object' && !Array.isArray(rawValue)
                ? rawValue
                : {};
            const acceptedCount = Number(payload.accepted_count || 0);
            const resolvedCount = Number(payload.resolved_count || 0);
            const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
            const resolvedSizeUsd = Number(payload.resolved_size_usd || 0);
            const acceptedShare = totalAccepted > 0 ? acceptedCount / totalAccepted : 0;
            const acceptedSizeShare = totalAcceptedSizeUsd > 0 ? acceptedSizeUsd / totalAcceptedSizeUsd : 0;
            const resolvedShare = acceptedCount > 0 ? resolvedCount / acceptedCount : 0;
            const resolvedSizeShare = acceptedSizeUsd > 0 ? resolvedSizeUsd / acceptedSizeUsd : 0;
            const rawWinRate = payload.win_rate;
            const winRate = rawWinRate == null ? null : Number(rawWinRate);
            const totalPnlUsd = Number(payload.total_pnl_usd || 0);
            const worstWindowPnlUsd = replaySearchWorstWindowPnlFromPayload(payload);
      const worstWindowResolvedShare = replaySearchWorstActiveWindowResolvedShareFromPayload(payload);
            const worstActiveWindowAcceptedCount = payload.worst_accepting_window_accepted_count == null && payload.worst_active_window_accepted_count == null
                ? null
                : Number(payload.worst_accepting_window_accepted_count ?? payload.worst_active_window_accepted_count);
            const worstActiveWindowAcceptedSizeUsd = Number(payload.worst_accepting_window_accepted_size_usd ?? payload.worst_active_window_accepted_size_usd ?? 0);
            const inactiveWindowCount = Number(payload.inactive_window_count || 0);
            const activeWindowCount = replaySearchModeActiveWindowCountFromPayload(payload, windowCount);
      const acceptedWindowCount = replaySearchModeAcceptedWindowCountFromPayload(payload, windowCount);
      const acceptedWindowShare = replaySearchModeAcceptedWindowShareFromPayload(payload, windowCount);
      const maxNonAcceptingActiveWindowStreakValue = replaySearchModeMaxNonAcceptingActiveWindowStreakFromPayload(payload, windowCount);
      const nonAcceptingActiveWindowEpisodeCount = replaySearchModeNonAcceptingActiveWindowEpisodeCountFromPayload(payload, windowCount);
      const maxAcceptingWindowAcceptedShare = replaySearchModeMaxAcceptingWindowAcceptedShareFromPayload(payload, windowCount);
      const maxAcceptingWindowAcceptedSizeShare = replaySearchModeMaxAcceptingWindowAcceptedSizeShareFromPayload(payload, windowCount);
      const topTwoAcceptingWindowAcceptedShare = replaySearchModeTopTwoAcceptingWindowAcceptedShareFromPayload(payload, windowCount);
      const topTwoAcceptingWindowAcceptedSizeShare = replaySearchModeTopTwoAcceptingWindowAcceptedSizeShareFromPayload(payload, windowCount);
      const acceptingWindowAcceptedConcentrationIndex = replaySearchAcceptingWindowAcceptedConcentrationIndexFromPayload(payload);
      const acceptingWindowAcceptedSizeConcentrationIndex = replaySearchAcceptingWindowAcceptedSizeConcentrationIndexFromPayload(payload);
            const minAccepted = Number(constraints[`min_${mode}_accepted_count`] || 0);
            const minResolved = Number(constraints[`min_${mode}_resolved_count`] || 0);
            const minResolvedShare = Number(constraints[`min_${mode}_resolved_share`] || 0);
            const minResolvedSizeShare = Number(constraints[`min_${mode}_resolved_size_share`] || 0);
            const minWinRate = Number(constraints[`min_${mode}_win_rate`] || 0);
            const minPnlUsd = Number(constraints[`min_${mode}_pnl_usd`] || 0);
            const minWorstWindowPnlUsd = Number(constraints[`min_${mode}_worst_window_pnl_usd`] ?? sentinelWorstWindow);
            const minWorstWindowResolvedShare = Number(constraints[`min_${mode}_worst_window_resolved_share`] || 0);
            const minWorstWindowResolvedSizeShare = Number(constraints[`min_${mode}_worst_window_resolved_size_share`] || 0);
            const minWorstActiveWindowAcceptedCount = Number(constraints[`min_${mode}_worst_active_window_accepted_count`] || 0);
            const minWorstActiveWindowAcceptedSizeUsd = Number(constraints[`min_${mode}_worst_active_window_accepted_size_usd`] || 0);
            const maxInactiveWindows = Number(constraints[`max_${mode}_inactive_windows`] ?? -1);
      const minAcceptedWindows = Number(constraints[`min_${mode}_accepted_windows`] || 0);
      const minAcceptedWindowShare = Number(constraints[`min_${mode}_accepted_window_share`] || 0);
      const maxNonAcceptingActiveWindowStreak = Number(constraints[`max_${mode}_non_accepting_active_window_streak`] ?? -1);
      const maxNonAcceptingActiveWindowEpisodes = Number(constraints[`max_${mode}_non_accepting_active_window_episodes`] ?? -1);
      const maxAcceptingWindowShareLimit = Number(constraints[`max_${mode}_accepting_window_accepted_share`] || 0);
      const maxAcceptingWindowSizeShareLimit = Number(constraints[`max_${mode}_accepting_window_accepted_size_share`] || 0);
      const maxTopTwoAcceptingWindowShareLimit = Number(constraints[`max_${mode}_top_two_accepting_window_accepted_share`] || 0);
      const maxTopTwoAcceptingWindowSizeShareLimit = Number(constraints[`max_${mode}_top_two_accepting_window_accepted_size_share`] || 0);
      const maxAcceptingWindowConcentrationIndexLimit = Number(constraints[`max_${mode}_accepting_window_accepted_concentration_index`] || 0);
      const maxAcceptingWindowSizeConcentrationIndexLimit = Number(constraints[`max_${mode}_accepting_window_accepted_size_concentration_index`] || 0);
            const shareLimit = Number(constraints[shareKey] || 0);
            const sizeShareLimit = Number(constraints[mode === 'heuristic' ? 'max_heuristic_accepted_size_share' : 'min_xgboost_accepted_size_share'] || 0);
            const activeWindowShareLimit = Number(constraints[mode === 'heuristic' ? 'max_heuristic_active_window_accepted_share' : 'min_xgboost_active_window_accepted_share'] || 0);
            const activeWindowSizeShareLimit = Number(constraints[mode === 'heuristic' ? 'max_heuristic_active_window_accepted_size_share' : 'min_xgboost_active_window_accepted_size_share'] || 0);
            const activeWindowShare = Number(payload[mode === 'heuristic' ? 'max_active_window_accepted_share' : 'min_active_window_accepted_share'] ?? acceptedShare);
            const activeWindowSizeShare = Number(payload[mode === 'heuristic' ? 'max_active_window_accepted_size_share' : 'min_active_window_accepted_size_share'] ?? acceptedSizeShare);
      const worstWindowResolvedSizeShare = replaySearchWorstActiveWindowResolvedSizeShareFromPayload(payload);
            if (minAccepted > 0) {
                hasActiveGuard = true;
                if (acceptedCount < minAccepted)
                    breaches.push(`${prefix} n ${formatCount(acceptedCount)}<${formatCount(minAccepted)}`);
            }
            if (minResolved > 0) {
                hasActiveGuard = true;
                if (resolvedCount < minResolved)
                    breaches.push(`${prefix} r ${formatCount(resolvedCount)}<${formatCount(minResolved)}`);
            }
            if (minResolvedShare > 0) {
                hasActiveGuard = true;
                if (resolvedShare < minResolvedShare)
                    breaches.push(`${prefix} cov ${formatPct(resolvedShare, 0)}<${formatPct(minResolvedShare, 0)}`);
            }
            if (minResolvedSizeShare > 0) {
                hasActiveGuard = true;
                if (resolvedSizeShare < minResolvedSizeShare)
                    breaches.push(`${prefix} sz-cov ${formatPct(resolvedSizeShare, 0)}<${formatPct(minResolvedSizeShare, 0)}`);
            }
            if (minWinRate > 0) {
                hasActiveGuard = true;
                if (winRate == null || winRate < minWinRate)
                    breaches.push(`${prefix} wr ${formatPct(winRate, 0)}<${formatPct(minWinRate, 0)}`);
            }
            if (minPnlUsd !== 0) {
                hasActiveGuard = true;
                if (totalPnlUsd < minPnlUsd)
                    breaches.push(`${prefix} pnl ${formatDollar(totalPnlUsd)}<${formatDollar(minPnlUsd)}`);
            }
            if (minWorstWindowPnlUsd > sentinelWorstWindow) {
                hasActiveGuard = true;
                if (worstWindowPnlUsd < minWorstWindowPnlUsd)
                    breaches.push(`${prefix} worst ${formatDollar(worstWindowPnlUsd)}<${formatDollar(minWorstWindowPnlUsd)}`);
            }
            if (minWorstWindowResolvedShare > 0) {
                hasActiveGuard = true;
                if (worstWindowResolvedShare < minWorstWindowResolvedShare)
                    breaches.push(`${prefix} worst cov ${formatPct(worstWindowResolvedShare, 0)}<${formatPct(minWorstWindowResolvedShare, 0)}`);
            }
            if (minWorstWindowResolvedSizeShare > 0) {
                hasActiveGuard = true;
                if (worstWindowResolvedSizeShare < minWorstWindowResolvedSizeShare)
                    breaches.push(`${prefix} worst sz-cov ${formatPct(worstWindowResolvedSizeShare, 0)}<${formatPct(minWorstWindowResolvedSizeShare, 0)}`);
            }
            if (minWorstActiveWindowAcceptedCount > 0 && acceptedCount > 0) {
                hasActiveGuard = true;
                if (worstActiveWindowAcceptedCount == null || worstActiveWindowAcceptedCount < minWorstActiveWindowAcceptedCount)
                    breaches.push(`${prefix} worst acc ${formatCount(worstActiveWindowAcceptedCount)}<${formatCount(minWorstActiveWindowAcceptedCount)}`);
            }
            if (minWorstActiveWindowAcceptedSizeUsd > 0 && acceptedSizeUsd > 0) {
                hasActiveGuard = true;
                if (worstActiveWindowAcceptedSizeUsd < minWorstActiveWindowAcceptedSizeUsd)
                    breaches.push(`${prefix} worst acc$ ${formatDollar(worstActiveWindowAcceptedSizeUsd)}<${formatDollar(minWorstActiveWindowAcceptedSizeUsd)}`);
            }
            if (maxInactiveWindows >= 0) {
                hasActiveGuard = true;
                if (inactiveWindowCount > maxInactiveWindows)
                    breaches.push(`${prefix} idle ${formatCount(inactiveWindowCount)}>${formatCount(maxInactiveWindows)}`);
            }
            if (minAcceptedWindows > 0) {
                hasActiveGuard = true;
                if (acceptedWindowCount < minAcceptedWindows)
                    breaches.push(`${prefix} acc-win ${formatCount(acceptedWindowCount)}<${formatCount(minAcceptedWindows)}`);
            }
            if (minAcceptedWindowShare > 0 && activeWindowCount > 0) {
                hasActiveGuard = true;
                if (acceptedWindowShare < minAcceptedWindowShare) {
                    breaches.push(`${prefix} acc-freq ${formatPct(acceptedWindowShare, 0)}<${formatPct(minAcceptedWindowShare, 0)}`);
                }
            }
            if (maxNonAcceptingActiveWindowStreak >= 0) {
                hasActiveGuard = true;
                if (maxNonAcceptingActiveWindowStreakValue > maxNonAcceptingActiveWindowStreak) {
                    breaches.push(`${prefix} acc-gap ${formatCount(maxNonAcceptingActiveWindowStreakValue)}>${formatCount(maxNonAcceptingActiveWindowStreak)}`);
                }
            }
            if (maxNonAcceptingActiveWindowEpisodes >= 0) {
                hasActiveGuard = true;
                if (nonAcceptingActiveWindowEpisodeCount > maxNonAcceptingActiveWindowEpisodes) {
                    breaches.push(`${prefix} acc-runs ${formatCount(nonAcceptingActiveWindowEpisodeCount)}>${formatCount(maxNonAcceptingActiveWindowEpisodes)}`);
                }
            }
            if (maxAcceptingWindowShareLimit > 0) {
                hasActiveGuard = true;
                if (maxAcceptingWindowAcceptedShare > maxAcceptingWindowShareLimit) {
                    breaches.push(`${prefix} top-acc ${formatPct(maxAcceptingWindowAcceptedShare, 0)}>${formatPct(maxAcceptingWindowShareLimit, 0)}`);
                }
            }
            if (maxAcceptingWindowSizeShareLimit > 0) {
                hasActiveGuard = true;
                if (maxAcceptingWindowAcceptedSizeShare > maxAcceptingWindowSizeShareLimit) {
                    breaches.push(`${prefix} top-acc$ ${formatPct(maxAcceptingWindowAcceptedSizeShare, 0)}>${formatPct(maxAcceptingWindowSizeShareLimit, 0)}`);
                }
            }
            if (maxTopTwoAcceptingWindowShareLimit > 0) {
                hasActiveGuard = true;
                if (topTwoAcceptingWindowAcceptedShare > maxTopTwoAcceptingWindowShareLimit) {
                    breaches.push(`${prefix} top2-acc ${formatPct(topTwoAcceptingWindowAcceptedShare, 0)}>${formatPct(maxTopTwoAcceptingWindowShareLimit, 0)}`);
                }
            }
            if (maxTopTwoAcceptingWindowSizeShareLimit > 0) {
                hasActiveGuard = true;
            if (topTwoAcceptingWindowAcceptedSizeShare > maxTopTwoAcceptingWindowSizeShareLimit) {
                breaches.push(`${prefix} top2-acc$ ${formatPct(topTwoAcceptingWindowAcceptedSizeShare, 0)}>${formatPct(maxTopTwoAcceptingWindowSizeShareLimit, 0)}`);
            }
        }
        if (maxAcceptingWindowConcentrationIndexLimit > 0) {
            hasActiveGuard = true;
            if (acceptingWindowAcceptedConcentrationIndex > maxAcceptingWindowConcentrationIndexLimit) {
                breaches.push(`${prefix} acc-ci ${formatPct(acceptingWindowAcceptedConcentrationIndex, 0)}>${formatPct(maxAcceptingWindowConcentrationIndexLimit, 0)}`);
            }
        }
        if (maxAcceptingWindowSizeConcentrationIndexLimit > 0) {
            hasActiveGuard = true;
            if (acceptingWindowAcceptedSizeConcentrationIndex > maxAcceptingWindowSizeConcentrationIndexLimit) {
                breaches.push(`${prefix} acc-ci$ ${formatPct(acceptingWindowAcceptedSizeConcentrationIndex, 0)}>${formatPct(maxAcceptingWindowSizeConcentrationIndexLimit, 0)}`);
            }
        }
            if (mixModesEnabled && shareLimit > 0) {
                hasActiveGuard = true;
                if (shareDirection === 'max' && acceptedShare > shareLimit) {
                    breaches.push(`${prefix} mix ${formatPct(acceptedShare, 0)}>${formatPct(shareLimit, 0)}`);
                }
                if (shareDirection === 'min' && acceptedShare < shareLimit) {
                    breaches.push(`${prefix} mix ${formatPct(acceptedShare, 0)}<${formatPct(shareLimit, 0)}`);
                }
            }
            if (mixModesEnabled && sizeShareLimit > 0) {
                hasActiveGuard = true;
                if (shareDirection === 'max' && acceptedSizeShare > sizeShareLimit) {
                    breaches.push(`${prefix} mix$ ${formatPct(acceptedSizeShare, 0)}>${formatPct(sizeShareLimit, 0)}`);
                }
                if (shareDirection === 'min' && acceptedSizeShare < sizeShareLimit) {
                    breaches.push(`${prefix} mix$ ${formatPct(acceptedSizeShare, 0)}<${formatPct(sizeShareLimit, 0)}`);
                }
            }
            if (mixModesEnabled && activeWindowShareLimit > 0) {
                hasActiveGuard = true;
                if (shareDirection === 'max' && activeWindowShare > activeWindowShareLimit) {
                    breaches.push(`${prefix} acc-mix ${formatPct(activeWindowShare, 0)}>${formatPct(activeWindowShareLimit, 0)}`);
                }
                if (shareDirection === 'min' && activeWindowShare < activeWindowShareLimit) {
                    breaches.push(`${prefix} acc-mix ${formatPct(activeWindowShare, 0)}<${formatPct(activeWindowShareLimit, 0)}`);
                }
            }
            if (mixModesEnabled && activeWindowSizeShareLimit > 0) {
                hasActiveGuard = true;
                if (shareDirection === 'max' && activeWindowSizeShare > activeWindowSizeShareLimit) {
                    breaches.push(`${prefix} acc-mix$ ${formatPct(activeWindowSizeShare, 0)}>${formatPct(activeWindowSizeShareLimit, 0)}`);
                }
                if (shareDirection === 'min' && activeWindowSizeShare < activeWindowSizeShareLimit) {
                    breaches.push(`${prefix} acc-mix$ ${formatPct(activeWindowSizeShare, 0)}<${formatPct(activeWindowSizeShareLimit, 0)}`);
                }
            }
        }
        if (!hasActiveGuard)
            return { summary: 'none', breachCount: 0, hasActiveGuard: false };
        if (!breaches.length)
            return { summary: 'clear', breachCount: 0, hasActiveGuard: true };
        return {
            summary: breaches.length > 4
                ? `${breaches.slice(0, 4).join(' | ')} | +${formatCount(breaches.length - 4)} more`
                : breaches.join(' | '),
            breachCount: breaches.length,
            hasActiveGuard: true
        };
    }
    catch {
        return { summary: '-', breachCount: 0, hasActiveGuard: false };
    }
}
function replaySearchModePenaltySummary(row) {
    if (!row)
        return '-';
    const parts = [];
    const modeResolvedSharePenalty = Math.max(Number(row.mode_resolved_share_penalty || 0), 0);
    const modeResolvedSizeSharePenalty = Math.max(Number(row.mode_resolved_size_share_penalty || 0), 0);
    const modeWorstWindowResolvedSharePenalty = Math.max(Number(row.mode_worst_window_resolved_share_penalty || 0), 0);
    const modeWorstWindowResolvedSizeSharePenalty = Math.max(Number(row.mode_worst_window_resolved_size_share_penalty || 0), 0);
    const modeActiveWindowAcceptedSharePenalty = Math.max(Number(row.mode_active_window_accepted_share_penalty || 0), 0);
    const modeActiveWindowAcceptedSizeSharePenalty = Math.max(Number(row.mode_active_window_accepted_size_share_penalty || 0), 0);
    const modeWorstActiveWindowAcceptedPenalty = Math.max(Number(row.mode_worst_active_window_accepted_penalty || 0), 0);
    const modeWorstActiveWindowAcceptedSizePenalty = Math.max(Number(row.mode_worst_active_window_accepted_size_penalty || 0), 0);
    const modeLossPenalty = Math.max(Number(row.mode_loss_penalty || 0), 0);
    const modeInactivityPenalty = Math.max(Number(row.mode_inactivity_penalty || 0), 0);
    const modeAcceptedWindowCountPenalty = Math.max(Number(row.mode_accepted_window_count_penalty || 0), 0);
    const modeAcceptedWindowSharePenalty = Math.max(Number(row.mode_accepted_window_share_penalty || 0), 0);
    const modeNonAcceptingActiveWindowStreakPenalty = Math.max(Number(row.mode_non_accepting_active_window_streak_penalty || 0), 0);
    const modeNonAcceptingActiveWindowEpisodePenalty = Math.max(Number(row.mode_non_accepting_active_window_episode_penalty || 0), 0);
    const modeAcceptingWindowAcceptedSharePenalty = Math.max(Number(row.mode_accepting_window_accepted_share_penalty || 0), 0);
    const modeAcceptingWindowAcceptedSizeSharePenalty = Math.max(Number(row.mode_accepting_window_accepted_size_share_penalty || 0), 0);
    const modeTopTwoAcceptingWindowAcceptedSharePenalty = Math.max(Number(row.mode_top_two_accepting_window_accepted_share_penalty || 0), 0);
    const modeTopTwoAcceptingWindowAcceptedSizeSharePenalty = Math.max(Number(row.mode_top_two_accepting_window_accepted_size_share_penalty || 0), 0);
    const modeAcceptingWindowAcceptedConcentrationIndexPenalty = Math.max(Number(row.mode_accepting_window_accepted_concentration_index_penalty || 0), 0);
    const modeAcceptingWindowAcceptedSizeConcentrationIndexPenalty = Math.max(Number(row.mode_accepting_window_accepted_size_concentration_index_penalty || 0), 0);
    if (modeResolvedSharePenalty > 0)
        parts.push(`cov ${modeResolvedSharePenalty.toFixed(2)}x`);
    if (modeResolvedSizeSharePenalty > 0)
        parts.push(`sz-cov ${modeResolvedSizeSharePenalty.toFixed(2)}x`);
    if (modeWorstWindowResolvedSharePenalty > 0)
        parts.push(`w-cov ${modeWorstWindowResolvedSharePenalty.toFixed(2)}x`);
    if (modeWorstWindowResolvedSizeSharePenalty > 0)
        parts.push(`w-sz-cov ${modeWorstWindowResolvedSizeSharePenalty.toFixed(2)}x`);
    if (modeActiveWindowAcceptedSharePenalty > 0)
        parts.push(`acc-mix ${modeActiveWindowAcceptedSharePenalty.toFixed(2)}x`);
    if (modeActiveWindowAcceptedSizeSharePenalty > 0)
        parts.push(`acc-mix$ ${modeActiveWindowAcceptedSizeSharePenalty.toFixed(2)}x`);
    if (modeWorstActiveWindowAcceptedPenalty > 0)
        parts.push(`w-acc ${modeWorstActiveWindowAcceptedPenalty.toFixed(2)}x`);
    if (modeWorstActiveWindowAcceptedSizePenalty > 0)
        parts.push(`w-acc$ ${modeWorstActiveWindowAcceptedSizePenalty.toFixed(2)}x`);
    if (modeLossPenalty > 0)
        parts.push(`loss ${modeLossPenalty.toFixed(2)}x`);
    if (modeInactivityPenalty > 0)
        parts.push(`idle ${modeInactivityPenalty.toFixed(2)}x`);
    if (modeAcceptedWindowCountPenalty > 0)
        parts.push(`acc-win ${modeAcceptedWindowCountPenalty.toFixed(2)}x`);
    if (modeAcceptedWindowSharePenalty > 0)
        parts.push(`acc-freq ${modeAcceptedWindowSharePenalty.toFixed(2)}x`);
    if (modeNonAcceptingActiveWindowStreakPenalty > 0)
        parts.push(`acc-gap ${modeNonAcceptingActiveWindowStreakPenalty.toFixed(2)}x`);
    if (modeNonAcceptingActiveWindowEpisodePenalty > 0)
        parts.push(`acc-runs ${modeNonAcceptingActiveWindowEpisodePenalty.toFixed(2)}x`);
    if (modeAcceptingWindowAcceptedSharePenalty > 0)
        parts.push(`top-acc ${modeAcceptingWindowAcceptedSharePenalty.toFixed(2)}x`);
    if (modeAcceptingWindowAcceptedSizeSharePenalty > 0)
        parts.push(`top-acc$ ${modeAcceptingWindowAcceptedSizeSharePenalty.toFixed(2)}x`);
    if (modeTopTwoAcceptingWindowAcceptedSharePenalty > 0)
        parts.push(`top2-acc ${modeTopTwoAcceptingWindowAcceptedSharePenalty.toFixed(2)}x`);
    if (modeTopTwoAcceptingWindowAcceptedSizeSharePenalty > 0)
        parts.push(`top2-acc$ ${modeTopTwoAcceptingWindowAcceptedSizeSharePenalty.toFixed(2)}x`);
    if (modeAcceptingWindowAcceptedConcentrationIndexPenalty > 0)
        parts.push(`acc-ci ${modeAcceptingWindowAcceptedConcentrationIndexPenalty.toFixed(2)}x`);
    if (modeAcceptingWindowAcceptedSizeConcentrationIndexPenalty > 0)
        parts.push(`acc-ci$ ${modeAcceptingWindowAcceptedSizeConcentrationIndexPenalty.toFixed(2)}x`);
    return parts.length ? parts.join(' | ') : 'none';
}
function replaySearchFailureSummary(raw, feasible) {
    if (Number(feasible || 0) > 0)
        return '-';
    if (!raw)
        return 'unknown';
    try {
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed))
            return 'unknown';
        const failures = parsed
            .map((value) => String(value || '').trim())
            .filter(Boolean);
        if (!failures.length)
            return 'unknown';
        const labels = failures.map((failure) => {
            switch (failure) {
                case 'accepted_count':
                    return 'accepted';
                case 'resolved_count':
                    return 'resolved';
                case 'resolved_share':
                    return 'coverage';
                case 'resolved_size_share':
                    return 'size cov';
                case 'win_rate':
                    return 'win rate';
                case 'total_pnl_usd':
                    return 'pnl';
                case 'max_drawdown_pct':
                    return 'drawdown';
                case 'worst_window_pnl_usd':
                    return 'worst pnl';
                case 'worst_window_resolved_share':
                    return 'worst cov';
                case 'worst_window_resolved_size_share':
                    return 'worst size cov';
                case 'worst_window_drawdown_pct':
                    return 'worst dd';
                case 'positive_window_count':
                    return 'positive windows';
                case 'active_window_count':
                    return 'active windows';
                case 'inactive_window_count':
                    return 'inactive windows';
                case 'accepted_window_count':
                    return 'acc-win';
                case 'accepted_window_share':
                    return 'acc-freq';
                case 'max_non_accepting_active_window_streak':
                    return 'acc-gap';
                case 'non_accepting_active_window_episode_count':
                    return 'acc-runs';
                case 'max_accepting_window_accepted_share':
                    return 'top-acc';
                case 'max_accepting_window_accepted_size_share':
                    return 'top-acc$';
                case 'top_two_accepting_window_accepted_share':
                    return 'top2-acc';
                case 'top_two_accepting_window_accepted_size_share':
                    return 'top2-acc$';
                case 'accepting_window_accepted_concentration_index':
                    return 'acc-ci';
                case 'accepting_window_accepted_size_concentration_index':
                    return 'acc-ci$';
                case 'worst_active_window_accepted_count':
                    return 'worst acc n';
                case 'worst_active_window_accepted_size_usd':
                    return 'worst acc$';
                case 'pause_guard_reject_share':
                    return 'pause share';
                case 'daily_guard_window_share':
                    return 'd-freq';
                case 'live_guard_window_share':
                    return 'p-freq';
                case 'daily_guard_restart_window_share':
                    return 'd-rst';
                case 'live_guard_restart_window_share':
                    return 'p-rst';
                case 'max_open_exposure_share':
                    return 'exposure';
                case 'max_window_end_open_exposure_share':
                    return 'carry';
                case 'avg_window_end_open_exposure_share':
                    return 'carry avg';
                case 'carry_window_share':
                    return 'carry-freq';
                case 'carry_restart_window_share':
                    return 'carry-rst';
                case 'trader_count':
                    return 'wallet worst count';
                case 'market_count':
                    return 'market worst count';
                case 'entry_price_band_count':
                    return 'entry worst count';
                case 'time_to_close_band_count':
                    return 'horizon worst count';
                case 'top_trader_accepted_share':
                    return 'wallet n share';
                case 'top_trader_abs_pnl_share':
                    return 'wallet pnl share';
                case 'top_trader_size_share':
                    return 'wallet size share';
                case 'top_market_accepted_share':
                    return 'market n share';
                case 'top_market_abs_pnl_share':
                    return 'market pnl share';
                case 'top_market_size_share':
                    return 'market size share';
                case 'top_entry_price_band_accepted_share':
                    return 'entry n share';
                case 'top_entry_price_band_abs_pnl_share':
                    return 'entry pnl share';
                case 'top_entry_price_band_size_share':
                    return 'entry size share';
                case 'top_time_to_close_band_accepted_share':
                    return 'horizon n share';
                case 'top_time_to_close_band_abs_pnl_share':
                    return 'horizon pnl share';
                case 'top_time_to_close_band_size_share':
                    return 'horizon size share';
                case 'heuristic_inactive_window_count':
                    return 'heur idle';
                case 'heuristic_accepted_window_count':
                    return 'heur acc-win';
                case 'heuristic_accepted_window_share':
                    return 'heur acc-freq';
                case 'heuristic_max_non_accepting_active_window_streak':
                    return 'heur acc-gap';
                case 'heuristic_non_accepting_active_window_episode_count':
                    return 'heur acc-runs';
                case 'heuristic_max_accepting_window_accepted_share':
                    return 'heur top-acc';
                case 'heuristic_max_accepting_window_accepted_size_share':
                    return 'heur top-acc$';
                case 'heuristic_top_two_accepting_window_accepted_share':
                    return 'heur top2-acc';
                case 'heuristic_top_two_accepting_window_accepted_size_share':
                    return 'heur top2-acc$';
                case 'heuristic_accepting_window_accepted_concentration_index':
                    return 'heur acc-ci';
                case 'heuristic_accepting_window_accepted_size_concentration_index':
                    return 'heur acc-ci$';
                case 'heuristic_resolved_size_share':
                    return 'heur size cov';
                case 'heuristic_worst_window_resolved_size_share':
                    return 'heur worst size cov';
                case 'heuristic_worst_active_window_accepted_count':
                    return 'heur worst acc';
                case 'heuristic_worst_active_window_accepted_size_usd':
                    return 'heur worst acc$';
                case 'heuristic_accepted_size_share':
                    return 'heur mix$';
                case 'heuristic_accepted_share':
                    return 'heur mix';
                case 'heuristic_active_window_accepted_share':
                    return 'heur acc-mix';
                case 'heuristic_active_window_accepted_size_share':
                    return 'heur acc-mix$';
                case 'xgboost_inactive_window_count':
                    return 'model idle';
                case 'xgboost_accepted_window_count':
                    return 'model acc-win';
                case 'xgboost_accepted_window_share':
                    return 'model acc-freq';
                case 'xgboost_max_non_accepting_active_window_streak':
                    return 'model acc-gap';
                case 'xgboost_non_accepting_active_window_episode_count':
                    return 'model acc-runs';
                case 'xgboost_max_accepting_window_accepted_share':
                    return 'model top-acc';
                case 'xgboost_max_accepting_window_accepted_size_share':
                    return 'model top-acc$';
                case 'xgboost_top_two_accepting_window_accepted_share':
                    return 'model top2-acc';
                case 'xgboost_top_two_accepting_window_accepted_size_share':
                    return 'model top2-acc$';
                case 'xgboost_accepting_window_accepted_concentration_index':
                    return 'model acc-ci';
                case 'xgboost_accepting_window_accepted_size_concentration_index':
                    return 'model acc-ci$';
                case 'xgboost_resolved_size_share':
                    return 'model size cov';
                case 'xgboost_worst_window_resolved_size_share':
                    return 'model worst size cov';
                case 'xgboost_worst_active_window_accepted_count':
                    return 'model worst acc';
                case 'xgboost_worst_active_window_accepted_size_usd':
                    return 'model worst acc$';
                case 'xgboost_accepted_size_share':
                    return 'model mix$';
                case 'xgboost_accepted_share':
                    return 'model mix';
                case 'xgboost_active_window_accepted_share':
                    return 'model acc-mix';
                case 'xgboost_active_window_accepted_size_share':
                    return 'model acc-mix$';
                default:
                    return failure.replaceAll('_', ' ');
            }
        });
        return labels.length > 4
            ? `${labels.slice(0, 4).join(' | ')} | +${formatCount(labels.length - 4)} more`
            : labels.join(' | ');
    }
    catch {
        return 'unknown';
    }
}
function replayHeadroomPctPoints(value) {
    const points = value * 100;
    if (!Number.isFinite(points))
        return '-';
    const absPoints = Math.abs(points);
    const rounded = absPoints >= 10 ? Math.round(points) : Math.round(points * 10) / 10;
    const sign = rounded > 0 ? '+' : '';
    return `${sign}${rounded}pt`;
}
function replayHeadroomCount(value) {
    if (!Number.isFinite(value))
        return '-';
    const rounded = Math.round(value);
    const sign = rounded > 0 ? '+' : '';
    return `${sign}${rounded}`;
}
function replaySearchHeadroomSummary(resultRaw, constraintsRaw, policyRaw) {
    if (!resultRaw || !constraintsRaw)
        return { summary: '-', hasActiveGuard: false, closestMarginRatio: null, hasFailure: false };
    try {
        const resultParsed = JSON.parse(resultRaw);
        const constraintsParsed = JSON.parse(constraintsRaw);
        const enabled = replaySearchEnabledModes(policyRaw);
        const constraints = constraintsParsed && typeof constraintsParsed === 'object' && !Array.isArray(constraintsParsed)
            ? constraintsParsed
            : {};
        if (!resultParsed || typeof resultParsed !== 'object' || Array.isArray(resultParsed)) {
            return { summary: '-', hasActiveGuard: false, closestMarginRatio: null, hasFailure: false };
        }
        const rawSignalModeSummary = resultParsed.signal_mode_summary;
        const signalModeSummary = rawSignalModeSummary && typeof rawSignalModeSummary === 'object' && !Array.isArray(rawSignalModeSummary)
            ? rawSignalModeSummary
            : {};
        const acceptedTotal = Object.values(signalModeSummary).reduce((sum, rawValue) => {
            if (!rawValue || typeof rawValue !== 'object' || Array.isArray(rawValue))
                return sum;
            return sum + Number(rawValue.accepted_count || 0);
        }, 0);
        const acceptedSizeTotal = Object.values(signalModeSummary).reduce((sum, rawValue) => {
            if (!rawValue || typeof rawValue !== 'object' || Array.isArray(rawValue))
                return sum;
            return sum + Number(rawValue.accepted_size_usd || 0);
        }, 0);
        const mixModesEnabled = enabled.heuristic && enabled.xgboost;
        const headrooms = [];
        const pushHeadroom = (group, label, actual, threshold, formatter, direction) => {
            if (!Number.isFinite(actual) || !Number.isFinite(threshold))
                return;
            const margin = direction === 'min' ? actual - threshold : threshold - actual;
            const denominator = Math.max(Math.abs(threshold), direction === 'max' ? 0.01 : 1);
            headrooms.push({
                group,
                label: `${label} ${formatter(margin)}`,
                margin,
                normalizedMargin: margin / denominator
            });
        };
        const globalAccepted = Number(resultParsed.accepted_count || 0);
        const globalResolved = Number(resultParsed.resolved_count || 0);
        const globalResolvedShare = globalAccepted > 0 ? globalResolved / globalAccepted : 0;
        const globalAcceptedSizeUsd = Number(resultParsed.accepted_size_usd || 0);
        const globalResolvedSizeUsd = Number(resultParsed.resolved_size_usd || 0);
        const globalResolvedSizeShare = globalAcceptedSizeUsd > 0 ? globalResolvedSizeUsd / globalAcceptedSizeUsd : 0;
        const globalWinRate = resultParsed.win_rate == null ? null : Number(resultParsed.win_rate);
        const globalTotalPnl = Number(resultParsed.total_pnl_usd || 0);
        const globalMaxDrawdown = Number(resultParsed.max_drawdown_pct || 0);
        const globalPositiveWindows = Number(resultParsed.positive_window_count || 0);
        const globalActiveWindows = Number(resultParsed.active_window_count || 0);
        const globalInactiveWindows = Number(resultParsed.inactive_window_count || 0);
        const globalAcceptedWindows = replaySearchAcceptedWindowCountFromPayload(resultParsed);
        const globalAcceptedWindowShare = replaySearchAcceptedWindowShareFromPayload(resultParsed);
        const globalMaxNonAcceptingActiveWindowStreak = replaySearchMaxNonAcceptingActiveWindowStreakFromPayload(resultParsed);
        const globalNonAcceptingActiveWindowEpisodes = replaySearchNonAcceptingActiveWindowEpisodeCountFromPayload(resultParsed);
    const globalMaxAcceptingWindowAcceptedShare = replaySearchMaxAcceptingWindowAcceptedShareFromPayload(resultParsed);
    const globalMaxAcceptingWindowAcceptedSizeShare = replaySearchMaxAcceptingWindowAcceptedSizeShareFromPayload(resultParsed);
    const globalTopTwoAcceptingWindowAcceptedShare = replaySearchTopTwoAcceptingWindowAcceptedShareFromPayload(resultParsed);
    const globalTopTwoAcceptingWindowAcceptedSizeShare = replaySearchTopTwoAcceptingWindowAcceptedSizeShareFromPayload(resultParsed);
        const globalWorstActiveWindowAcceptedCount = Number(resultParsed.worst_accepting_window_accepted_count ?? resultParsed.worst_active_window_accepted_count ?? 0);
        const globalWorstActiveWindowAcceptedSizeUsd = Number(resultParsed.worst_accepting_window_accepted_size_usd ?? resultParsed.worst_active_window_accepted_size_usd ?? 0);
        const globalWorstWindowPnl = replaySearchWorstWindowPnlFromPayload(resultParsed);
        const globalWorstWindowResolvedShare = replaySearchWorstActiveWindowResolvedShareFromPayload(resultParsed);
        const globalWorstWindowResolvedSizeShare = replaySearchWorstActiveWindowResolvedSizeShareFromPayload(resultParsed);
        const globalWorstWindowDrawdown = Number(resultParsed.worst_window_drawdown_pct || 0);
        const globalOpenExposureShare = Number(resultParsed.max_open_exposure_share || 0);
        const globalWindowEndOpenExposureShare = Number(resultParsed.max_window_end_open_exposure_share ?? resultParsed.window_end_open_exposure_share ?? 0);
        const globalAvgWindowEndOpenExposureShare = replaySearchAvgWindowEndOpenExposureShareFromPayload(resultParsed);
        const globalCarryWindowShare = replaySearchCarryWindowShareFromPayload(resultParsed);
        const globalCarryRestartWindowShare = replaySearchCarryRestartWindowShareFromPayload(resultParsed);
        const globalDailyGuardWindowShare = replaySearchDailyGuardWindowShareFromPayload(resultParsed);
        const globalLiveGuardWindowShare = replaySearchLiveGuardWindowShareFromPayload(resultParsed);
        const globalDailyGuardRestartWindowShare = replaySearchDailyGuardRestartWindowShareFromPayload(resultParsed);
        const globalLiveGuardRestartWindowShare = replaySearchLiveGuardRestartWindowShareFromPayload(resultParsed);
        const rejectReasonSummary = resultParsed.reject_reason_summary && typeof resultParsed.reject_reason_summary === 'object' && !Array.isArray(resultParsed.reject_reason_summary)
            ? resultParsed.reject_reason_summary
            : {};
        const traderConcentration = resultParsed.trader_concentration && typeof resultParsed.trader_concentration === 'object' && !Array.isArray(resultParsed.trader_concentration)
            ? resultParsed.trader_concentration
            : {};
        const marketConcentration = resultParsed.market_concentration && typeof resultParsed.market_concentration === 'object' && !Array.isArray(resultParsed.market_concentration)
            ? resultParsed.market_concentration
            : {};
        const entryPriceBandConcentration = resultParsed.entry_price_band_concentration && typeof resultParsed.entry_price_band_concentration === 'object' && !Array.isArray(resultParsed.entry_price_band_concentration)
            ? resultParsed.entry_price_band_concentration
            : {};
        const timeToCloseBandConcentration = resultParsed.time_to_close_band_concentration && typeof resultParsed.time_to_close_band_concentration === 'object' && !Array.isArray(resultParsed.time_to_close_band_concentration)
            ? resultParsed.time_to_close_band_concentration
            : {};
        const pauseGuardRejectShare = globalAccepted + Number(resultParsed.rejected_count || 0) > 0
            ? (Number(rejectReasonSummary.daily_loss_guard || 0) + Number(rejectReasonSummary.live_drawdown_guard || 0)) / Math.max(Number(resultParsed.trade_count || 0), 1)
            : 0;
        const minAccepted = Number(constraints.min_accepted_count || 0);
        const minResolved = Number(constraints.min_resolved_count || 0);
        const minResolvedShare = Number(constraints.min_resolved_share || 0);
        const minResolvedSizeShare = Number(constraints.min_resolved_size_share || 0);
        const minWinRate = Number(constraints.min_win_rate || 0);
        const minTotalPnlUsd = Number(constraints.min_total_pnl_usd ?? -1000000000);
        const maxDrawdownPct = Number(constraints.max_drawdown_pct || 0);
        const maxPauseGuardRejectShare = Number(constraints.max_pause_guard_reject_share || 0);
        const maxDailyGuardWindowShare = Number(constraints.max_daily_guard_window_share || 0);
        const maxLiveGuardWindowShare = Number(constraints.max_live_guard_window_share || 0);
        const maxDailyGuardRestartWindowShare = Number(constraints.max_daily_guard_restart_window_share || 0);
        const maxLiveGuardRestartWindowShare = Number(constraints.max_live_guard_restart_window_share || 0);
        const maxOpenExposureShare = Number(constraints.max_open_exposure_share || 0);
        const maxWindowEndOpenExposureShare = Number(constraints.max_window_end_open_exposure_share || 0);
        const maxAvgWindowEndOpenExposureShare = Number(constraints.max_avg_window_end_open_exposure_share || 0);
        const maxCarryWindowShare = Number(constraints.max_carry_window_share || 0);
        const maxCarryRestartWindowShare = Number(constraints.max_carry_restart_window_share || 0);
        const minTraderCount = Number(constraints.min_trader_count || 0);
        const minMarketCount = Number(constraints.min_market_count || 0);
        const minEntryPriceBandCount = Number(constraints.min_entry_price_band_count || 0);
        const minTimeToCloseBandCount = Number(constraints.min_time_to_close_band_count || 0);
        const maxTopTraderAcceptedShare = Number(constraints.max_top_trader_accepted_share || 0);
        const maxTopTraderAbsPnlShare = Number(constraints.max_top_trader_abs_pnl_share || 0);
        const maxTopTraderSizeShare = Number(constraints.max_top_trader_size_share || 0);
        const maxTopMarketAcceptedShare = Number(constraints.max_top_market_accepted_share || 0);
        const maxTopMarketAbsPnlShare = Number(constraints.max_top_market_abs_pnl_share || 0);
        const maxTopMarketSizeShare = Number(constraints.max_top_market_size_share || 0);
        const maxTopEntryPriceBandAcceptedShare = Number(constraints.max_top_entry_price_band_accepted_share || 0);
        const maxTopEntryPriceBandAbsPnlShare = Number(constraints.max_top_entry_price_band_abs_pnl_share || 0);
        const maxTopEntryPriceBandSizeShare = Number(constraints.max_top_entry_price_band_size_share || 0);
        const maxTopTimeToCloseBandAcceptedShare = Number(constraints.max_top_time_to_close_band_accepted_share || 0);
        const maxTopTimeToCloseBandAbsPnlShare = Number(constraints.max_top_time_to_close_band_abs_pnl_share || 0);
        const maxTopTimeToCloseBandSizeShare = Number(constraints.max_top_time_to_close_band_size_share || 0);
        const minPositiveWindows = Number(constraints.min_positive_windows || 0);
        const minActiveWindows = Number(constraints.min_active_windows || 0);
        const maxInactiveWindows = Number(constraints.max_inactive_windows ?? -1);
        const minAcceptedWindows = Number(constraints.min_accepted_windows || 0);
        const minAcceptedWindowShare = Number(constraints.min_accepted_window_share || 0);
        const maxNonAcceptingActiveWindowStreak = Number(constraints.max_non_accepting_active_window_streak ?? -1);
        const maxNonAcceptingActiveWindowEpisodes = Number(constraints.max_non_accepting_active_window_episodes ?? -1);
        const maxAcceptingWindowAcceptedShare = Number(constraints.max_accepting_window_accepted_share || 0);
        const maxAcceptingWindowAcceptedSizeShare = Number(constraints.max_accepting_window_accepted_size_share || 0);
        const maxTopTwoAcceptingWindowAcceptedShare = Number(constraints.max_top_two_accepting_window_accepted_share || 0);
        const maxTopTwoAcceptingWindowAcceptedSizeShare = Number(constraints.max_top_two_accepting_window_accepted_size_share || 0);
        const maxAcceptingWindowAcceptedConcentrationIndex = Number(constraints.max_accepting_window_accepted_concentration_index || 0);
        const maxAcceptingWindowAcceptedSizeConcentrationIndex = Number(constraints.max_accepting_window_accepted_size_concentration_index || 0);
        const minWorstActiveWindowAcceptedCount = Number(constraints.min_worst_active_window_accepted_count || 0);
        const minWorstActiveWindowAcceptedSizeUsd = Number(constraints.min_worst_active_window_accepted_size_usd || 0);
        const minWorstWindowPnlUsd = Number(constraints.min_worst_window_pnl_usd ?? -1000000000);
        const minWorstWindowResolvedShare = Number(constraints.min_worst_window_resolved_share || 0);
        const minWorstWindowResolvedSizeShare = Number(constraints.min_worst_window_resolved_size_share || 0);
        const maxWorstWindowDrawdownPct = Number(constraints.max_worst_window_drawdown_pct || 0);
        const topTraderAcceptedShare = Number(traderConcentration.top_accepted_share || 0);
        const topTraderAbsPnlShare = Number(traderConcentration.top_abs_pnl_share || 0);
        const topTraderSizeShare = Number(traderConcentration.top_size_share || 0);
        const traderCount = Number(traderConcentration.trader_count || 0);
        const topMarketAcceptedShare = Number(marketConcentration.top_accepted_share || 0);
        const topMarketAbsPnlShare = Number(marketConcentration.top_abs_pnl_share || 0);
        const topMarketSizeShare = Number(marketConcentration.top_size_share || 0);
        const marketCount = Number(marketConcentration.market_count || 0);
        const topEntryPriceBandAcceptedShare = Number(entryPriceBandConcentration.top_accepted_share || 0);
        const topEntryPriceBandAbsPnlShare = Number(entryPriceBandConcentration.top_abs_pnl_share || 0);
        const topEntryPriceBandSizeShare = Number(entryPriceBandConcentration.top_size_share || 0);
        const entryPriceBandCount = Number(entryPriceBandConcentration.entry_price_band_count || 0);
        const topTimeToCloseBandAcceptedShare = Number(timeToCloseBandConcentration.top_accepted_share || 0);
        const topTimeToCloseBandAbsPnlShare = Number(timeToCloseBandConcentration.top_abs_pnl_share || 0);
        const topTimeToCloseBandSizeShare = Number(timeToCloseBandConcentration.top_size_share || 0);
        const timeToCloseBandCount = Number(timeToCloseBandConcentration.time_to_close_band_count || 0);
        if (minAccepted > 0)
            pushHeadroom('global', 'acc', globalAccepted, minAccepted, replayHeadroomCount, 'min');
        if (minResolved > 0)
            pushHeadroom('global', 'res', globalResolved, minResolved, replayHeadroomCount, 'min');
        if (minResolvedShare > 0)
            pushHeadroom('global', 'cov', globalResolvedShare, minResolvedShare, replayHeadroomPctPoints, 'min');
        if (minResolvedSizeShare > 0)
            pushHeadroom('global', 'sz-cov', globalResolvedSizeShare, minResolvedSizeShare, replayHeadroomPctPoints, 'min');
        if (minWinRate > 0 && globalWinRate != null)
            pushHeadroom('global', 'win', globalWinRate, minWinRate, replayHeadroomPctPoints, 'min');
        if (minTotalPnlUsd > -999999999)
            pushHeadroom('global', 'pnl', globalTotalPnl, minTotalPnlUsd, formatDollar, 'min');
        if (maxDrawdownPct > 0)
            pushHeadroom('global', 'dd', globalMaxDrawdown, maxDrawdownPct, replayHeadroomPctPoints, 'max');
        if (maxPauseGuardRejectShare > 0)
            pushHeadroom('global', 'pause', pauseGuardRejectShare, maxPauseGuardRejectShare, replayHeadroomPctPoints, 'max');
        if (maxDailyGuardWindowShare > 0)
            pushHeadroom('global', 'd-freq', globalDailyGuardWindowShare, maxDailyGuardWindowShare, replayHeadroomPctPoints, 'max');
        if (maxLiveGuardWindowShare > 0)
            pushHeadroom('global', 'p-freq', globalLiveGuardWindowShare, maxLiveGuardWindowShare, replayHeadroomPctPoints, 'max');
        if (maxDailyGuardRestartWindowShare > 0)
            pushHeadroom('global', 'd-rst', globalDailyGuardRestartWindowShare, maxDailyGuardRestartWindowShare, replayHeadroomPctPoints, 'max');
        if (maxLiveGuardRestartWindowShare > 0)
            pushHeadroom('global', 'p-rst', globalLiveGuardRestartWindowShare, maxLiveGuardRestartWindowShare, replayHeadroomPctPoints, 'max');
        if (maxOpenExposureShare > 0)
            pushHeadroom('global', 'exp', globalOpenExposureShare, maxOpenExposureShare, replayHeadroomPctPoints, 'max');
        if (maxWindowEndOpenExposureShare > 0)
            pushHeadroom('global', 'carry', globalWindowEndOpenExposureShare, maxWindowEndOpenExposureShare, replayHeadroomPctPoints, 'max');
        if (maxAvgWindowEndOpenExposureShare > 0)
            pushHeadroom('global', 'carry avg', globalAvgWindowEndOpenExposureShare, maxAvgWindowEndOpenExposureShare, replayHeadroomPctPoints, 'max');
        if (maxCarryWindowShare > 0)
            pushHeadroom('global', 'carry-freq', globalCarryWindowShare, maxCarryWindowShare, replayHeadroomPctPoints, 'max');
        if (maxCarryRestartWindowShare > 0)
            pushHeadroom('global', 'carry-rst', globalCarryRestartWindowShare, maxCarryRestartWindowShare, replayHeadroomPctPoints, 'max');
        if (minTraderCount > 0)
            pushHeadroom('global', 'wallet worst cnt', traderCount, minTraderCount, replayHeadroomCount, 'min');
        if (minMarketCount > 0)
            pushHeadroom('global', 'market worst cnt', marketCount, minMarketCount, replayHeadroomCount, 'min');
        if (minEntryPriceBandCount > 0)
            pushHeadroom('global', 'entry worst cnt', entryPriceBandCount, minEntryPriceBandCount, replayHeadroomCount, 'min');
        if (minTimeToCloseBandCount > 0)
            pushHeadroom('global', 'horizon worst cnt', timeToCloseBandCount, minTimeToCloseBandCount, replayHeadroomCount, 'min');
        if (maxTopTraderAcceptedShare > 0)
            pushHeadroom('global', 'wallet n', topTraderAcceptedShare, maxTopTraderAcceptedShare, replayHeadroomPctPoints, 'max');
        if (maxTopTraderAbsPnlShare > 0)
            pushHeadroom('global', 'wallet pnl', topTraderAbsPnlShare, maxTopTraderAbsPnlShare, replayHeadroomPctPoints, 'max');
        if (maxTopTraderSizeShare > 0)
            pushHeadroom('global', 'wallet sz', topTraderSizeShare, maxTopTraderSizeShare, replayHeadroomPctPoints, 'max');
        if (maxTopMarketAcceptedShare > 0)
            pushHeadroom('global', 'market n', topMarketAcceptedShare, maxTopMarketAcceptedShare, replayHeadroomPctPoints, 'max');
        if (maxTopMarketAbsPnlShare > 0)
            pushHeadroom('global', 'market pnl', topMarketAbsPnlShare, maxTopMarketAbsPnlShare, replayHeadroomPctPoints, 'max');
        if (maxTopMarketSizeShare > 0)
            pushHeadroom('global', 'market sz', topMarketSizeShare, maxTopMarketSizeShare, replayHeadroomPctPoints, 'max');
        if (maxTopEntryPriceBandAcceptedShare > 0)
            pushHeadroom('global', 'entry n', topEntryPriceBandAcceptedShare, maxTopEntryPriceBandAcceptedShare, replayHeadroomPctPoints, 'max');
        if (maxTopEntryPriceBandAbsPnlShare > 0)
            pushHeadroom('global', 'entry pnl', topEntryPriceBandAbsPnlShare, maxTopEntryPriceBandAbsPnlShare, replayHeadroomPctPoints, 'max');
        if (maxTopEntryPriceBandSizeShare > 0)
            pushHeadroom('global', 'entry sz', topEntryPriceBandSizeShare, maxTopEntryPriceBandSizeShare, replayHeadroomPctPoints, 'max');
        if (maxTopTimeToCloseBandAcceptedShare > 0)
            pushHeadroom('global', 'horizon n', topTimeToCloseBandAcceptedShare, maxTopTimeToCloseBandAcceptedShare, replayHeadroomPctPoints, 'max');
        if (maxTopTimeToCloseBandAbsPnlShare > 0)
            pushHeadroom('global', 'horizon pnl', topTimeToCloseBandAbsPnlShare, maxTopTimeToCloseBandAbsPnlShare, replayHeadroomPctPoints, 'max');
        if (maxTopTimeToCloseBandSizeShare > 0)
            pushHeadroom('global', 'horizon sz', topTimeToCloseBandSizeShare, maxTopTimeToCloseBandSizeShare, replayHeadroomPctPoints, 'max');
        if (minPositiveWindows > 0)
            pushHeadroom('global', 'pos', globalPositiveWindows, minPositiveWindows, replayHeadroomCount, 'min');
        if (minActiveWindows > 0)
            pushHeadroom('global', 'act', globalActiveWindows, minActiveWindows, replayHeadroomCount, 'min');
        if (maxInactiveWindows >= 0)
            pushHeadroom('global', 'idle', globalInactiveWindows, maxInactiveWindows, replayHeadroomCount, 'max');
        if (minAcceptedWindows > 0)
            pushHeadroom('global', 'acc-win', globalAcceptedWindows, minAcceptedWindows, replayHeadroomCount, 'min');
        if (minAcceptedWindowShare > 0)
            pushHeadroom('global', 'acc-freq', globalAcceptedWindowShare, minAcceptedWindowShare, replayHeadroomPctPoints, 'min');
        if (maxNonAcceptingActiveWindowStreak >= 0)
            pushHeadroom('global', 'acc-gap', globalMaxNonAcceptingActiveWindowStreak, maxNonAcceptingActiveWindowStreak, replayHeadroomCount, 'max');
        if (maxNonAcceptingActiveWindowEpisodes >= 0)
            pushHeadroom('global', 'acc-runs', globalNonAcceptingActiveWindowEpisodes, maxNonAcceptingActiveWindowEpisodes, replayHeadroomCount, 'max');
        if (maxAcceptingWindowAcceptedShare > 0)
            pushHeadroom('global', 'top-acc', globalMaxAcceptingWindowAcceptedShare, maxAcceptingWindowAcceptedShare, replayHeadroomPctPoints, 'max');
        if (maxAcceptingWindowAcceptedSizeShare > 0)
            pushHeadroom('global', 'top-acc$', globalMaxAcceptingWindowAcceptedSizeShare, maxAcceptingWindowAcceptedSizeShare, replayHeadroomPctPoints, 'max');
        if (maxTopTwoAcceptingWindowAcceptedShare > 0)
            pushHeadroom('global', 'top2-acc', globalTopTwoAcceptingWindowAcceptedShare, maxTopTwoAcceptingWindowAcceptedShare, replayHeadroomPctPoints, 'max');
        if (maxTopTwoAcceptingWindowAcceptedSizeShare > 0)
            pushHeadroom('global', 'top2-acc$', globalTopTwoAcceptingWindowAcceptedSizeShare, maxTopTwoAcceptingWindowAcceptedSizeShare, replayHeadroomPctPoints, 'max');
        if (maxAcceptingWindowAcceptedConcentrationIndex > 0)
            pushHeadroom('global', 'acc-ci', replaySearchAcceptingWindowAcceptedConcentrationIndexFromPayload(resultParsed), maxAcceptingWindowAcceptedConcentrationIndex, replayHeadroomPctPoints, 'max');
        if (maxAcceptingWindowAcceptedSizeConcentrationIndex > 0)
            pushHeadroom('global', 'acc-ci$', replaySearchAcceptingWindowAcceptedSizeConcentrationIndexFromPayload(resultParsed), maxAcceptingWindowAcceptedSizeConcentrationIndex, replayHeadroomPctPoints, 'max');
        if (minWorstActiveWindowAcceptedCount > 0)
            pushHeadroom('global', 'worst acc n', globalWorstActiveWindowAcceptedCount, minWorstActiveWindowAcceptedCount, replayHeadroomCount, 'min');
        if (minWorstActiveWindowAcceptedSizeUsd > 0)
            pushHeadroom('global', 'worst acc$', globalWorstActiveWindowAcceptedSizeUsd, minWorstActiveWindowAcceptedSizeUsd, formatDollar, 'min');
        if (minWorstWindowPnlUsd > -999999999)
            pushHeadroom('global', 'worst', globalWorstWindowPnl, minWorstWindowPnlUsd, formatDollar, 'min');
        if (minWorstWindowResolvedShare > 0)
            pushHeadroom('global', 'worst cov', globalWorstWindowResolvedShare, minWorstWindowResolvedShare, replayHeadroomPctPoints, 'min');
        if (minWorstWindowResolvedSizeShare > 0)
            pushHeadroom('global', 'worst sz-cov', globalWorstWindowResolvedSizeShare, minWorstWindowResolvedSizeShare, replayHeadroomPctPoints, 'min');
        if (maxWorstWindowDrawdownPct > 0)
            pushHeadroom('global', 'worst dd', globalWorstWindowDrawdown, maxWorstWindowDrawdownPct, replayHeadroomPctPoints, 'max');
        for (const [mode, prefix] of [['heuristic', 'heur'], ['xgboost', 'model']]) {
            if ((mode === 'heuristic' && !enabled.heuristic) || (mode === 'xgboost' && !enabled.xgboost))
                continue;
            const rawMode = signalModeSummary[mode];
            const payload = rawMode && typeof rawMode === 'object' && !Array.isArray(rawMode)
                ? rawMode
                : {};
            const acceptedCount = Number(payload.accepted_count || 0);
            const resolvedCount = Number(payload.resolved_count || 0);
            const acceptedSizeUsd = Number(payload.accepted_size_usd || 0);
            const resolvedSizeUsd = Number(payload.resolved_size_usd || 0);
            const winRate = payload.win_rate == null ? null : Number(payload.win_rate);
            const totalPnlUsd = Number(payload.total_pnl_usd || 0);
            const positiveWindowCount = Number(payload.positive_window_count || 0);
            const worstWindowPnlUsd = replaySearchWorstWindowPnlFromPayload(payload);
            const resolvedShare = acceptedCount > 0 ? resolvedCount / acceptedCount : 0;
            const resolvedSizeShare = acceptedSizeUsd > 0 ? resolvedSizeUsd / acceptedSizeUsd : 0;
            const worstWindowResolvedShare = replaySearchWorstActiveWindowResolvedShareFromPayload(payload);
            const worstActiveWindowAcceptedCount = payload.worst_accepting_window_accepted_count == null && payload.worst_active_window_accepted_count == null
                ? null
                : Number(payload.worst_accepting_window_accepted_count ?? payload.worst_active_window_accepted_count);
            const worstActiveWindowAcceptedSizeUsd = Number(payload.worst_accepting_window_accepted_size_usd ?? payload.worst_active_window_accepted_size_usd ?? 0);
      const inactiveWindowCount = Number(payload.inactive_window_count || 0);
      const activeWindowCount = replaySearchModeActiveWindowCountFromPayload(payload, Number(resultParsed.window_count || 0));
      const acceptedWindowCount = replaySearchModeAcceptedWindowCountFromPayload(payload, Number(resultParsed.window_count || 0));
      const acceptedWindowShare = replaySearchModeAcceptedWindowShareFromPayload(payload, Number(resultParsed.window_count || 0));
      const maxNonAcceptingActiveWindowStreak = replaySearchModeMaxNonAcceptingActiveWindowStreakFromPayload(payload, Number(resultParsed.window_count || 0));
      const nonAcceptingActiveWindowEpisodeCount = replaySearchModeNonAcceptingActiveWindowEpisodeCountFromPayload(payload, Number(resultParsed.window_count || 0));
      const maxAcceptingWindowAcceptedShare = replaySearchModeMaxAcceptingWindowAcceptedShareFromPayload(payload, Number(resultParsed.window_count || 0));
      const maxAcceptingWindowAcceptedSizeShare = replaySearchModeMaxAcceptingWindowAcceptedSizeShareFromPayload(payload, Number(resultParsed.window_count || 0));
      const topTwoAcceptingWindowAcceptedShare = replaySearchModeTopTwoAcceptingWindowAcceptedShareFromPayload(payload, Number(resultParsed.window_count || 0));
      const topTwoAcceptingWindowAcceptedSizeShare = replaySearchModeTopTwoAcceptingWindowAcceptedSizeShareFromPayload(payload, Number(resultParsed.window_count || 0));
      const acceptingWindowAcceptedConcentrationIndex = replaySearchAcceptingWindowAcceptedConcentrationIndexFromPayload(payload);
      const acceptingWindowAcceptedSizeConcentrationIndex = replaySearchAcceptingWindowAcceptedSizeConcentrationIndexFromPayload(payload);
      const acceptedShare = acceptedTotal > 0 ? acceptedCount / acceptedTotal : 0;
            const acceptedSizeShare = acceptedSizeTotal > 0 ? acceptedSizeUsd / acceptedSizeTotal : 0;
            const activeWindowShare = Number(payload[mode === 'heuristic' ? 'max_active_window_accepted_share' : 'min_active_window_accepted_share'] ?? acceptedShare);
            const activeWindowSizeShare = Number(payload[mode === 'heuristic' ? 'max_active_window_accepted_size_share' : 'min_active_window_accepted_size_share'] ?? acceptedSizeShare);
            const minModeAccepted = Number(constraints[`min_${mode}_accepted_count`] || 0);
            const minModeResolved = Number(constraints[`min_${mode}_resolved_count`] || 0);
            const minModeResolvedShare = Number(constraints[`min_${mode}_resolved_share`] || 0);
            const minModeResolvedSizeShare = Number(constraints[`min_${mode}_resolved_size_share`] || 0);
            const minModeWinRate = Number(constraints[`min_${mode}_win_rate`] || 0);
            const minModePnlUsd = Number(constraints[`min_${mode}_pnl_usd`] || 0);
            const minModeWorstWindowPnlUsd = Number(constraints[`min_${mode}_worst_window_pnl_usd`] ?? -1000000000);
            const minModeWorstWindowResolvedShare = Number(constraints[`min_${mode}_worst_window_resolved_share`] || 0);
            const minModeWorstWindowResolvedSizeShare = Number(constraints[`min_${mode}_worst_window_resolved_size_share`] || 0);
            const minModePositiveWindows = Number(constraints[`min_${mode}_positive_windows`] || 0);
            const minModeWorstActiveWindowAcceptedCount = Number(constraints[`min_${mode}_worst_active_window_accepted_count`] || 0);
            const minModeWorstActiveWindowAcceptedSizeUsd = Number(constraints[`min_${mode}_worst_active_window_accepted_size_usd`] || 0);
            const maxModeInactiveWindows = Number(constraints[`max_${mode}_inactive_windows`] ?? -1);
            const minModeAcceptedWindows = Number(constraints[`min_${mode}_accepted_windows`] || 0);
            const minModeAcceptedWindowShare = Number(constraints[`min_${mode}_accepted_window_share`] || 0);
            const maxModeNonAcceptingActiveWindowStreak = Number(constraints[`max_${mode}_non_accepting_active_window_streak`] ?? -1);
            const maxModeNonAcceptingActiveWindowEpisodes = Number(constraints[`max_${mode}_non_accepting_active_window_episodes`] ?? -1);
            const maxModeAcceptingWindowAcceptedShare = Number(constraints[`max_${mode}_accepting_window_accepted_share`] || 0);
            const maxModeAcceptingWindowAcceptedSizeShare = Number(constraints[`max_${mode}_accepting_window_accepted_size_share`] || 0);
            const maxModeTopTwoAcceptingWindowAcceptedShare = Number(constraints[`max_${mode}_top_two_accepting_window_accepted_share`] || 0);
            const maxModeTopTwoAcceptingWindowAcceptedSizeShare = Number(constraints[`max_${mode}_top_two_accepting_window_accepted_size_share`] || 0);
            const maxModeAcceptingWindowAcceptedConcentrationIndex = Number(constraints[`max_${mode}_accepting_window_accepted_concentration_index`] || 0);
            const maxModeAcceptingWindowAcceptedSizeConcentrationIndex = Number(constraints[`max_${mode}_accepting_window_accepted_size_concentration_index`] || 0);
            const worstWindowResolvedSizeShare = replaySearchWorstActiveWindowResolvedSizeShareFromPayload(payload);
            if (minModeAccepted > 0)
                pushHeadroom(mode, `${prefix} n`, acceptedCount, minModeAccepted, replayHeadroomCount, 'min');
            if (minModeResolved > 0)
                pushHeadroom(mode, `${prefix} r`, resolvedCount, minModeResolved, replayHeadroomCount, 'min');
            if (minModeResolvedShare > 0)
                pushHeadroom(mode, `${prefix} cov`, resolvedShare, minModeResolvedShare, replayHeadroomPctPoints, 'min');
            if (minModeResolvedSizeShare > 0)
                pushHeadroom(mode, `${prefix} sz-cov`, resolvedSizeShare, minModeResolvedSizeShare, replayHeadroomPctPoints, 'min');
            if (minModeWinRate > 0 && winRate != null)
                pushHeadroom(mode, `${prefix} wr`, winRate, minModeWinRate, replayHeadroomPctPoints, 'min');
            if (minModePnlUsd !== 0)
                pushHeadroom(mode, `${prefix} pnl`, totalPnlUsd, minModePnlUsd, formatDollar, 'min');
            if (minModeWorstWindowPnlUsd > -999999999)
                pushHeadroom(mode, `${prefix} worst`, worstWindowPnlUsd, minModeWorstWindowPnlUsd, formatDollar, 'min');
            if (minModeWorstWindowResolvedShare > 0)
                pushHeadroom(mode, `${prefix} worst cov`, worstWindowResolvedShare, minModeWorstWindowResolvedShare, replayHeadroomPctPoints, 'min');
            if (minModeWorstWindowResolvedSizeShare > 0)
                pushHeadroom(mode, `${prefix} worst sz-cov`, worstWindowResolvedSizeShare, minModeWorstWindowResolvedSizeShare, replayHeadroomPctPoints, 'min');
            if (minModePositiveWindows > 0)
                pushHeadroom(mode, `${prefix} pos`, positiveWindowCount, minModePositiveWindows, replayHeadroomCount, 'min');
            if (minModeWorstActiveWindowAcceptedCount > 0 && acceptedCount > 0 && worstActiveWindowAcceptedCount != null)
                pushHeadroom(mode, `${prefix} worst acc`, worstActiveWindowAcceptedCount, minModeWorstActiveWindowAcceptedCount, replayHeadroomCount, 'min');
            if (minModeWorstActiveWindowAcceptedSizeUsd > 0 && acceptedSizeUsd > 0)
                pushHeadroom(mode, `${prefix} worst acc$`, worstActiveWindowAcceptedSizeUsd, minModeWorstActiveWindowAcceptedSizeUsd, formatDollar, 'min');
            if (maxModeInactiveWindows >= 0)
                pushHeadroom(mode, `${prefix} idle`, inactiveWindowCount, maxModeInactiveWindows, replayHeadroomCount, 'max');
            if (minModeAcceptedWindows > 0)
                pushHeadroom(mode, `${prefix} acc-win`, acceptedWindowCount, minModeAcceptedWindows, replayHeadroomCount, 'min');
            if (minModeAcceptedWindowShare > 0 && activeWindowCount > 0)
                pushHeadroom(mode, `${prefix} acc-freq`, acceptedWindowShare, minModeAcceptedWindowShare, replayHeadroomPctPoints, 'min');
            if (maxModeNonAcceptingActiveWindowStreak >= 0)
                pushHeadroom(mode, `${prefix} acc-gap`, maxNonAcceptingActiveWindowStreak, maxModeNonAcceptingActiveWindowStreak, replayHeadroomCount, 'max');
            if (maxModeNonAcceptingActiveWindowEpisodes >= 0)
                pushHeadroom(mode, `${prefix} acc-runs`, nonAcceptingActiveWindowEpisodeCount, maxModeNonAcceptingActiveWindowEpisodes, replayHeadroomCount, 'max');
            if (maxModeAcceptingWindowAcceptedShare > 0)
                pushHeadroom(mode, `${prefix} top-acc`, maxAcceptingWindowAcceptedShare, maxModeAcceptingWindowAcceptedShare, replayHeadroomPctPoints, 'max');
            if (maxModeAcceptingWindowAcceptedSizeShare > 0)
                pushHeadroom(mode, `${prefix} top-acc$`, maxAcceptingWindowAcceptedSizeShare, maxModeAcceptingWindowAcceptedSizeShare, replayHeadroomPctPoints, 'max');
            if (maxModeTopTwoAcceptingWindowAcceptedShare > 0)
                pushHeadroom(mode, `${prefix} top2-acc`, topTwoAcceptingWindowAcceptedShare, maxModeTopTwoAcceptingWindowAcceptedShare, replayHeadroomPctPoints, 'max');
            if (maxModeTopTwoAcceptingWindowAcceptedSizeShare > 0)
                pushHeadroom(mode, `${prefix} top2-acc$`, topTwoAcceptingWindowAcceptedSizeShare, maxModeTopTwoAcceptingWindowAcceptedSizeShare, replayHeadroomPctPoints, 'max');
            if (maxModeAcceptingWindowAcceptedConcentrationIndex > 0)
                pushHeadroom(mode, `${prefix} acc-ci`, acceptingWindowAcceptedConcentrationIndex, maxModeAcceptingWindowAcceptedConcentrationIndex, replayHeadroomPctPoints, 'max');
            if (maxModeAcceptingWindowAcceptedSizeConcentrationIndex > 0)
                pushHeadroom(mode, `${prefix} acc-ci$`, acceptingWindowAcceptedSizeConcentrationIndex, maxModeAcceptingWindowAcceptedSizeConcentrationIndex, replayHeadroomPctPoints, 'max');
            if (mode === 'heuristic') {
                const maxShare = Number(constraints.max_heuristic_accepted_share || 0);
                if (mixModesEnabled && maxShare > 0)
                    pushHeadroom(mode, `${prefix} mix`, acceptedShare, maxShare, replayHeadroomPctPoints, 'max');
                const maxSizeShare = Number(constraints.max_heuristic_accepted_size_share || 0);
                if (mixModesEnabled && maxSizeShare > 0)
                    pushHeadroom(mode, `${prefix} mix$`, acceptedSizeShare, maxSizeShare, replayHeadroomPctPoints, 'max');
                const maxActiveWindowShare = Number(constraints.max_heuristic_active_window_accepted_share || 0);
                if (mixModesEnabled && maxActiveWindowShare > 0)
                    pushHeadroom(mode, `${prefix} acc-mix`, activeWindowShare, maxActiveWindowShare, replayHeadroomPctPoints, 'max');
                const maxActiveWindowSizeShare = Number(constraints.max_heuristic_active_window_accepted_size_share || 0);
                if (mixModesEnabled && maxActiveWindowSizeShare > 0)
                    pushHeadroom(mode, `${prefix} acc-mix$`, activeWindowSizeShare, maxActiveWindowSizeShare, replayHeadroomPctPoints, 'max');
            }
            else {
                const minShare = Number(constraints.min_xgboost_accepted_share || 0);
                if (mixModesEnabled && minShare > 0)
                    pushHeadroom(mode, `${prefix} mix`, acceptedShare, minShare, replayHeadroomPctPoints, 'min');
                const minSizeShare = Number(constraints.min_xgboost_accepted_size_share || 0);
                if (mixModesEnabled && minSizeShare > 0)
                    pushHeadroom(mode, `${prefix} mix$`, acceptedSizeShare, minSizeShare, replayHeadroomPctPoints, 'min');
                const minActiveWindowShare = Number(constraints.min_xgboost_active_window_accepted_share || 0);
                if (mixModesEnabled && minActiveWindowShare > 0)
                    pushHeadroom(mode, `${prefix} acc-mix`, activeWindowShare, minActiveWindowShare, replayHeadroomPctPoints, 'min');
                const minActiveWindowSizeShare = Number(constraints.min_xgboost_active_window_accepted_size_share || 0);
                if (mixModesEnabled && minActiveWindowSizeShare > 0)
                    pushHeadroom(mode, `${prefix} acc-mix$`, activeWindowSizeShare, minActiveWindowSizeShare, replayHeadroomPctPoints, 'min');
            }
        }
        if (!headrooms.length)
            return { summary: 'none', hasActiveGuard: false, closestMarginRatio: null, hasFailure: false };
        const bestByGroup = new Map();
        for (const headroom of headrooms) {
            const current = bestByGroup.get(headroom.group);
            if (!current || headroom.normalizedMargin < current.normalizedMargin) {
                bestByGroup.set(headroom.group, headroom);
            }
        }
        const ordered = ['global', 'heuristic', 'xgboost']
            .map((group) => bestByGroup.get(group))
            .filter((value) => value != null);
        const closestMarginRatio = ordered.reduce((current, headroom) => current == null ? headroom.normalizedMargin : Math.min(current, headroom.normalizedMargin), null);
        return {
            summary: ordered.map((headroom) => headroom.label).join(' | '),
            hasActiveGuard: true,
            closestMarginRatio,
            hasFailure: ordered.some((headroom) => headroom.margin < 0)
        };
    }
    catch {
        return { summary: '-', hasActiveGuard: false, closestMarginRatio: null, hasFailure: false };
    }
}
function replaySearchWindowSummary(latestSearch) {
    if (!latestSearch)
        return '-';
    const positive = formatCount(latestSearch.positive_window_count);
    const negative = formatCount(latestSearch.negative_window_count);
    const worst = formatDollar(replaySearchWorstWindowPnlFromSummaryRow(latestSearch));
    if (!latestSearch.result_json)
        return `${positive}+ / ${negative}- | ${worst}`;
    try {
        const parsed = JSON.parse(latestSearch.result_json);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
            return `${positive}+ / ${negative}- | ${worst}`;
        const windowCount = Number(parsed.window_count || 0);
        const activeWindowCount = Number(parsed.active_window_count || 0);
        const acceptedWindowCount = replaySearchAcceptedWindowCountFromPayload(parsed);
        const acceptedWindowShare = replaySearchAcceptedWindowShareFromPayload(parsed);
        const maxNonAcceptingActiveWindowStreak = replaySearchMaxNonAcceptingActiveWindowStreakFromPayload(parsed);
        const nonAcceptingActiveWindowEpisodeCount = replaySearchNonAcceptingActiveWindowEpisodeCountFromPayload(parsed);
        const inactiveWindowCount = Number(parsed.inactive_window_count || 0);
        const maxAcceptingWindowAcceptedShare = replaySearchMaxAcceptingWindowAcceptedShareFromPayload(parsed);
        const maxAcceptingWindowAcceptedSizeShare = replaySearchMaxAcceptingWindowAcceptedSizeShareFromPayload(parsed);
        const worstActiveWindowAcceptedCount = Number(parsed.worst_accepting_window_accepted_count ?? parsed.worst_active_window_accepted_count ?? 0);
        const worstActiveWindowAcceptedSizeUsd = Number(parsed.worst_accepting_window_accepted_size_usd ?? parsed.worst_active_window_accepted_size_usd ?? 0);
        const carryWindowCount = Number(parsed.carry_window_count || 0);
        const carryRestartWindowCount = Number(parsed.carry_restart_window_count || 0);
        const carryRestartWindowOpportunityCount = Number(parsed.carry_restart_window_opportunity_count || 0);
        const carryShare = Number(parsed.max_window_end_open_exposure_share ?? parsed.window_end_open_exposure_share ?? 0);
        const avgCarryShare = replaySearchAvgWindowEndOpenExposureShareFromPayload(parsed);
        const carryUsd = Number(parsed.max_window_end_open_exposure_usd ?? parsed.window_end_open_exposure_usd ?? 0);
        const topTwoAcceptingWindowAcceptedShare = replaySearchTopTwoAcceptingWindowAcceptedShareFromPayload(parsed);
        const topTwoAcceptingWindowAcceptedSizeShare = replaySearchTopTwoAcceptingWindowAcceptedSizeShareFromPayload(parsed);
        const acceptingWindowAcceptedConcentrationIndex = replaySearchAcceptingWindowAcceptedConcentrationIndexFromPayload(parsed);
        const acceptingWindowAcceptedSizeConcentrationIndex = replaySearchAcceptingWindowAcceptedSizeConcentrationIndexFromPayload(parsed);
        const carrySuffix = carryShare > 0 || carryUsd > 0
            ? ` | carry ${formatPct(carryShare, 0)} | carry avg ${formatPct(avgCarryShare, 0)} | carry$ ${formatDollar(carryUsd)}`
            : '';
        const carryFreqSuffix = activeWindowCount > 0
            ? ` | carry-freq ${formatCount(carryWindowCount)}/${formatCount(activeWindowCount)}`
            : (carryWindowCount > 0 ? ' | carry-freq yes' : '');
        const carryRestartSuffix = carryRestartWindowOpportunityCount > 0
            ? ` | carry-rst ${formatCount(carryRestartWindowCount)}/${formatCount(carryRestartWindowOpportunityCount)}`
            : (carryRestartWindowCount > 0 ? ' | carry-rst yes' : '');
        const topTwoAcceptSuffix = topTwoAcceptingWindowAcceptedShare > 0
            ? ` | top2-acc ${formatPct(topTwoAcceptingWindowAcceptedShare, 0)}`
            : '';
        const topTwoAcceptSizeSuffix = topTwoAcceptingWindowAcceptedSizeShare > 0
            ? ` | top2-acc$ ${formatPct(topTwoAcceptingWindowAcceptedSizeShare, 0)}`
            : '';
        const topAcceptSuffix = maxAcceptingWindowAcceptedShare > 0
            ? ` | top-acc ${formatPct(maxAcceptingWindowAcceptedShare, 0)}`
            : '';
        const topAcceptSizeSuffix = maxAcceptingWindowAcceptedSizeShare > 0
            ? ` | top-acc$ ${formatPct(maxAcceptingWindowAcceptedSizeShare, 0)}`
            : '';
        const concentrationSuffix = acceptingWindowAcceptedConcentrationIndex > 0
            ? ` | acc-ci ${formatPct(acceptingWindowAcceptedConcentrationIndex, 0)}`
            : '';
        const concentrationSizeSuffix = acceptingWindowAcceptedSizeConcentrationIndex > 0
            ? ` | acc-ci$ ${formatPct(acceptingWindowAcceptedSizeConcentrationIndex, 0)}`
            : '';
        if (windowCount <= 1)
            return `${positive}+ / ${negative}-${topAcceptSuffix}${topAcceptSizeSuffix}${topTwoAcceptSuffix}${topTwoAcceptSizeSuffix}${concentrationSuffix}${concentrationSizeSuffix}${carrySuffix}${carryFreqSuffix}${carryRestartSuffix} | ${worst}`;
        return `${positive}+ / ${negative}- | act ${formatCount(activeWindowCount)}/${formatCount(windowCount)} | accept ${formatCount(acceptedWindowCount)}/${formatCount(windowCount)} | acc-freq ${formatPct(acceptedWindowShare, 0)} | acc-gap ${formatCount(maxNonAcceptingActiveWindowStreak)} | acc-runs ${formatCount(nonAcceptingActiveWindowEpisodeCount)} | top-acc ${formatPct(maxAcceptingWindowAcceptedShare, 0)} | top-acc$ ${formatPct(maxAcceptingWindowAcceptedSizeShare, 0)}${topTwoAcceptSuffix}${topTwoAcceptSizeSuffix}${concentrationSuffix}${concentrationSizeSuffix} | idle ${formatCount(inactiveWindowCount)}${carryFreqSuffix}${carryRestartSuffix} | worst acc ${formatCount(worstActiveWindowAcceptedCount)} | worst acc$ ${formatDollar(worstActiveWindowAcceptedSizeUsd)}${carrySuffix} | ${worst}`;
    }
    catch {
        return `${positive}+ / ${negative}- | ${worst}`;
    }
}
function replayConfigRawValue(value) {
    if (value == null)
        return null;
    if (typeof value === 'boolean')
        return value ? 'true' : 'false';
    if (typeof value === 'number')
        return Number.isFinite(value) ? String(value) : '';
    return String(value).trim();
}
function replayDurationSeconds(raw) {
    const value = raw.trim().toLowerCase();
    if (!value)
        return null;
    if (value === 'unlimited' || value === 'infinite' || value === 'inf' || value === 'none')
        return Number.POSITIVE_INFINITY;
    const numeric = Number(value);
    if (Number.isFinite(numeric))
        return numeric;
    const match = value.match(/^([0-9]+(?:\.[0-9]+)?)([smhdw])$/);
    if (!match)
        return null;
    const amount = Number(match[1]);
    if (!Number.isFinite(amount))
        return null;
    const unitSeconds = { s: 1, m: 60, h: 3600, d: 86400, w: 604800 };
    return amount * unitSeconds[match[2]];
}
function replayFormatDurationSeconds(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0)
        return '';
    if (seconds === 0)
        return '0s';
    const rounded = Math.round(seconds);
    const units = [
        [604800, 'w'],
        [86400, 'd'],
        [3600, 'h'],
        [60, 'm']
    ];
    for (const [unitSeconds, suffix] of units) {
        if (rounded % unitSeconds === 0) {
            return `${rounded / unitSeconds}${suffix}`;
        }
    }
    return `${rounded}s`;
}
function replayConfigValuesEqual(field, currentRaw, recommendedRaw) {
    if (field.kind === 'bool') {
        return currentRaw.trim().toLowerCase() === recommendedRaw.trim().toLowerCase();
    }
    if (field.kind === 'duration') {
        const currentSeconds = replayDurationSeconds(currentRaw);
        const recommendedSeconds = replayDurationSeconds(recommendedRaw);
        if (currentSeconds != null && recommendedSeconds != null) {
            return Math.abs(currentSeconds - recommendedSeconds) < 1e-9;
        }
    }
    if (field.kind === 'float' || field.kind === 'int') {
        const currentNumeric = Number(currentRaw);
        const recommendedNumeric = Number(recommendedRaw);
        if (Number.isFinite(currentNumeric) && Number.isFinite(recommendedNumeric)) {
            return Math.abs(currentNumeric - recommendedNumeric) < 1e-9;
        }
    }
    return currentRaw.trim().toLowerCase() === recommendedRaw.trim().toLowerCase();
}
function replaySearchConfigDelta(rawConfigJson, settingsValues, configFieldByKey) {
    if (!rawConfigJson)
        return null;
    try {
        const parsed = JSON.parse(rawConfigJson);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
            return null;
        const entries = Object.entries(parsed);
        if (!entries.length)
            return null;
        const changed = [];
        const replayOnlyKeys = [];
        for (const [key, value] of entries) {
            const recommendedRaw = replayConfigRawValue(value);
            if (recommendedRaw == null)
                continue;
            const field = configFieldByKey.get(key);
            if (!field) {
                replayOnlyKeys.push(key);
                continue;
            }
            const currentRaw = Object.prototype.hasOwnProperty.call(settingsValues, key)
                ? settingsValues[key]
                : field.defaultValue;
            if (replayConfigValuesEqual(field, currentRaw, recommendedRaw))
                continue;
            changed.push({ field, recommendedRaw });
        }
        return { changed, replayOnlyKeys };
    }
    catch {
        return null;
    }
}
function replaySearchConfigSuggestion(rawConfigJson, settingsValues, configFieldByKey) {
    const delta = replaySearchConfigDelta(rawConfigJson, settingsValues, configFieldByKey);
    if (!delta)
        return null;
    const changed = delta.changed;
    if (!changed.length) {
        return { diffCount: 0, summary: 'Already aligned', aligned: true };
    }
    const preview = changed
            .slice(0, 2)
            .map(({ field, recommendedRaw }) => `${field.label}=${formatEditableConfigValue(field, recommendedRaw)}`)
            .join(', ');
    const suffix = changed.length > 2 ? ` +${changed.length - 2}` : '';
    return {
        diffCount: changed.length,
        summary: `${preview}${suffix}`,
        aligned: false,
    };
}
function replaySearchApplyScope(rawConfigJson, settingsValues, configFieldByKey) {
    const delta = replaySearchConfigDelta(rawConfigJson, settingsValues, configFieldByKey);
    if (!delta)
        return null;
    const liveCount = delta.changed.filter(({ field }) => field.liveApplies).length;
    const restartCount = delta.changed.filter(({ field }) => !field.liveApplies).length;
    const replayOnlyCount = delta.replayOnlyKeys.length;
    const parts = [];
    if (liveCount > 0)
        parts.push(`live ${formatCount(liveCount)}`);
    if (restartCount > 0)
        parts.push(`restart ${formatCount(restartCount)}`);
    if (replayOnlyCount > 0)
        parts.push(`replay-only ${formatCount(replayOnlyCount)}`);
    return {
        liveCount,
        restartCount,
        replayOnlyCount,
        summary: parts.length ? parts.join(' | ') : 'none',
        aligned: parts.length === 0
    };
}
function trainingCycleDisplayLabel(label) {
    if (label === 'Next scheduled')
        return 'Next run';
    if (label === 'Scheduled in')
        return 'Starts in';
    if (label === 'Trigger progress')
        return 'Progress';
    return label;
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
function denseModelsLabelWidth(width) {
    const safeWidth = Math.max(18, width);
    return Math.max(8, Math.min(16, Math.floor(safeWidth * 0.42)));
}
function DenseModelsRow({ label, value, width, color = theme.white, selected = false, backgroundColor, labelWidth, minValueWidth = 6, valueAlign = 'right' }) {
    const safeWidth = Math.max(18, width);
    const maxLabelWidth = Math.max(6, safeWidth - Math.max(6, minValueWidth) - 1);
    const preferredLabelWidth = Math.max(labelWidth ?? denseModelsLabelWidth(safeWidth), Math.min(label.length, maxLabelWidth));
    const boundedLabelWidth = Math.max(6, Math.min(maxLabelWidth, preferredLabelWidth));
    const valueWidth = Math.max(Math.max(6, minValueWidth), safeWidth - boundedLabelWidth - 1);
    const rowBackground = selected ? backgroundColor : undefined;
    return (React.createElement(InkBox, { width: safeWidth },
        React.createElement(Text, { color: selected ? theme.accent : theme.dim, backgroundColor: rowBackground, bold: selected }, fit(label, boundedLabelWidth)),
        React.createElement(Text, { backgroundColor: rowBackground }, " "),
        React.createElement(Text, { color: color, backgroundColor: rowBackground, bold: selected }, valueAlign === 'left' ? fit(value, valueWidth) : fitRight(value, valueWidth))));
}
function recentRunsColumnWidths(width) {
    const safeWidth = Math.max(24, width);
    const resultWidth = safeWidth >= 34 ? 8 : 6;
    const logLossWidth = safeWidth >= 34 ? 8 : 7;
    const brierWidth = safeWidth >= 32 ? 7 : 6;
    const timestampWidth = Math.max(5, safeWidth - logLossWidth - brierWidth - resultWidth - 3);
    return { safeWidth, timestampWidth, logLossWidth, brierWidth, resultWidth };
}
function RecentRunsHeaderRow({ width }) {
    const { safeWidth, timestampWidth, logLossWidth, brierWidth, resultWidth } = recentRunsColumnWidths(width);
    return (React.createElement(InkBox, { width: safeWidth },
        React.createElement(Text, { color: theme.accent, bold: true }, fit('Timestamp', timestampWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: theme.accent, bold: true }, fitRight('Log Loss', logLossWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: theme.accent, bold: true }, fitRight('Brier', brierWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: theme.accent, bold: true }, fitRight('Result', resultWidth))));
}
function RecentRunsDataRow({ width, timestamp, timestampColor, logLoss, logLossColor, brier, brierColor, result, resultColor }) {
    const { safeWidth, timestampWidth, logLossWidth, brierWidth, resultWidth } = recentRunsColumnWidths(width);
    return (React.createElement(InkBox, { width: safeWidth },
        React.createElement(Text, { color: timestampColor }, fit(timestamp, timestampWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: logLossColor }, fitRight(logLoss, logLossWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: brierColor }, fitRight(brier, brierWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: resultColor }, fitRight(result, resultWidth))));
}
function recentExitColumnWidths(width) {
    const safeWidth = Math.max(28, width);
    const actionWidth = 9;
    const returnWidth = 8;
    const timestampWidth = 14;
    const reasonWidth = Math.max(8, safeWidth - actionWidth - returnWidth - timestampWidth - 3);
    return { safeWidth, timestampWidth, actionWidth, returnWidth, reasonWidth };
}
function RecentExitHeaderRow({ width }) {
    const { safeWidth, timestampWidth, actionWidth, returnWidth, reasonWidth } = recentExitColumnWidths(width);
    return (React.createElement(InkBox, { width: safeWidth },
        React.createElement(Text, { color: theme.accent, bold: true }, fit('Timestamp', timestampWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: theme.accent, bold: true }, fit('Action', actionWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: theme.accent, bold: true }, fitRight('Return', returnWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: theme.accent, bold: true }, fit('Reason', reasonWidth))));
}
function RecentExitDataRow({ width, timestamp, timestampColor, action, actionColor, estimatedReturn, estimatedReturnColor, reason, reasonColor }) {
    const { safeWidth, timestampWidth, actionWidth, returnWidth, reasonWidth } = recentExitColumnWidths(width);
    return (React.createElement(InkBox, { width: safeWidth },
        React.createElement(Text, { color: timestampColor }, fit(timestamp, timestampWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: actionColor }, fit(action, actionWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: estimatedReturnColor }, fitRight(estimatedReturn, returnWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: reasonColor }, fit(reason, reasonWidth))));
}
function ModelsSectionTitle({ title, width, selected, backgroundColor }) {
    const prefix = selected ? '> ' : '';
    return (React.createElement(Text, { color: selected ? theme.accent : theme.white, backgroundColor: selected ? backgroundColor : undefined, bold: true }, fit(`${prefix}${title}`, Math.max(1, width))));
}
function ModelsSubsectionTitle({ title, width, color = theme.accent }) {
    const safeWidth = Math.max(1, width);
    return (React.createElement(Text, { color: color, bold: true }, fit(title.trimStart(), safeWidth)));
}
function ModelsSpacer() {
    return React.createElement(Text, null, " ");
}
export function Models({ selectedPanelIndex, detailOpen, selectedSettingIndex, settingsValues }) {
    const terminal = useTerminalSize();
    const botState = useBotState();
    const configFieldByKey = useMemo(() => new Map(editableConfigFields.map((field) => [field.key, field])), []);
    const settlementFixedCostUsd = useMemo(() => {
        const field = configFieldByKey.get('SETTLEMENT_FIXED_COST_USD');
        const rawValue = settingsValues['SETTLEMENT_FIXED_COST_USD'] || field?.defaultValue || '0';
        const parsed = Number.parseFloat(String(rawValue || '').trim());
        return Number.isFinite(parsed) ? Math.max(parsed, 0) : 0;
    }, [configFieldByKey, settingsValues]);
    const modalBackground = terminal.backgroundColor || theme.modalBackground;
    const selectedRowBackground = selectionBackgroundColor(modalBackground);
    const nowTs = useNow();
    const stacked = stackPanels(terminal.width);
    const models = useQuery(MODEL_SQL);
    const retrainRuns = useQuery(RETRAIN_RUN_SQL);
    const replayLatestRuns = useQuery(REPLAY_LATEST_RUN_SQL);
    const replaySearchSummaryRows = useQuery(REPLAY_SEARCH_SUMMARY_SQL);
    const replayBestWalletRows = useQuery(REPLAY_SEGMENT_BEST_SQL, ['trader_address', REPLAY_SEGMENT_MIN_RESOLVED]);
    const replayWorstWalletRows = useQuery(REPLAY_SEGMENT_WORST_SQL, ['trader_address', REPLAY_SEGMENT_MIN_RESOLVED]);
    const replayBestBandRows = useQuery(REPLAY_SEGMENT_BEST_SQL, ['entry_price_band', REPLAY_SEGMENT_MIN_RESOLVED]);
    const replayWorstBandRows = useQuery(REPLAY_SEGMENT_WORST_SQL, ['entry_price_band', REPLAY_SEGMENT_MIN_RESOLVED]);
    const replayBestHorizonRows = useQuery(REPLAY_SEGMENT_BEST_SQL, ['time_to_close_band', REPLAY_SEGMENT_MIN_RESOLVED]);
    const replayWorstHorizonRows = useQuery(REPLAY_SEGMENT_WORST_SQL, ['time_to_close_band', REPLAY_SEGMENT_MIN_RESOLVED]);
    const trackerRows = useQuery(TRACKER_SQL);
    const perfRows = useQuery(PERF_SQL);
    const signalModes = useQuery(SIGNAL_MODE_SQL);
    const recentSignalModes = useQuery(RECENT_SIGNAL_MODE_SQL);
    const calibrationSummaryRows = useQuery(CALIBRATION_SUMMARY_SQL);
    const confusionRows = useQuery(CONFUSION_SQL);
    const calibrationRows = useQuery(CALIBRATION_SQL);
    const flowRows = useQuery(FLOW_SQL);
    const trainingSummaryRows = useQuery(TRAINING_SUMMARY_SQL);
    const trainingProgressRows = useQuery(TRAINING_PROGRESS_SQL);
    const exitAuditModeRealMoney = botState.mode === 'live' ? 1 : 0;
    const exitAuditSummaryRows = useQuery(EXIT_AUDIT_SUMMARY_SQL, [exitAuditModeRealMoney]);
    const recentExitAuditRows = useQuery(EXIT_AUDIT_RECENT_SQL, [exitAuditModeRealMoney]);
    const exitAttributionRows = useQuery(EXIT_ATTRIBUTION_SQL, [settlementFixedCostUsd, exitAuditModeRealMoney]);
    const latest = models[0];
    const latestReplay = replayLatestRuns[0];
    const latestReplaySearch = replaySearchSummaryRows[0];
    const replayBestWallet = replayBestWalletRows[0];
    const replayWorstWallet = replayWorstWalletRows[0];
    const replayBestBand = replayBestBandRows[0];
    const replayWorstBand = replayWorstBandRows[0];
    const replayBestHorizon = replayBestHorizonRows[0];
    const replayWorstHorizon = replayWorstHorizonRows[0];
    const tracker = trackerRows[0];
    const calibration = calibrationSummaryRows[0];
    const confusion = confusionRows[0];
    const flow = flowRows[0];
    const trainingSummary = trainingSummaryRows[0];
    const trainingProgress = trainingProgressRows[0];
    const exitAuditSummary = exitAuditSummaryRows[0];
    const exitAttribution = exitAttributionRows[0];
    const latestSharedHoldoutRun = retrainRuns.find((row) => sharedHoldoutComparison(row) != null);
    const latestSharedHoldout = sharedHoldoutComparison(latestSharedHoldoutRun);
    const trackerSnapshot = perfRows.find((row) => row.mode === 'shadow') ?? perfRows[0];
    const featureCount = useMemo(() => parseFeatureCount(latest?.feature_cols), [latest?.feature_cols]);
    const useRate = ratio(tracker?.taken, tracker?.signals);
    const trackerWinRate = ratio(tracker?.wins, tracker?.resolved);
    const retrainGaps = useMemo(() => retrainRuns
        .slice(0, 12)
        .map((row, index, rows) => {
        const next = rows[index + 1];
        return next ? row.finished_at - next.finished_at : null;
    })
        .filter((gap) => gap != null && gap > 0), [retrainRuns]);
    const calibrationLimit = terminal.compact ? 3 : terminal.height < 42 ? 4 : 5;
    const historyLimit = terminal.compact ? 4 : terminal.height < 42 ? 5 : terminal.height < 50 ? 7 : 10;
    const exitAuditHistoryLimit = terminal.compact ? 3 : terminal.height < 42 ? 4 : 6;
    const twoColumnPanelContentWidth = stacked
        ? Math.max(46, terminal.width - 12)
        : Math.max(34, Math.floor((terminal.width - 18) / 2));
    const secondaryRowGap = 1;
    const secondaryThreeAcross = !stacked && terminal.width >= 150;
    const secondaryRowWidth = Math.max(96, terminal.width - 8);
    const confusionBoxWidth = secondaryThreeAcross
        ? Math.max(18, Math.floor(secondaryRowWidth * 0.17))
        : Math.max(13, Math.min(23, terminal.width - 12));
    const combinedSectionGap = 3;
    const combinedMetricsBoxWidth = secondaryThreeAcross
        ? Math.max(60, secondaryRowWidth - confusionBoxWidth - secondaryRowGap)
        : undefined;
    const combinedMetricsContentWidth = secondaryThreeAcross
        ? Math.max(56, Number(combinedMetricsBoxWidth) - 4)
        : twoColumnPanelContentWidth;
    const combinedPanelsWide = secondaryThreeAcross && combinedMetricsContentWidth >= 68;
    const combinedSectionWideBudget = combinedPanelsWide
        ? Math.max(48, combinedMetricsContentWidth - combinedSectionGap)
        : combinedMetricsContentWidth;
    const confidenceSectionContentWidth = combinedPanelsWide
        ? Math.max(24, Math.floor(combinedSectionWideBudget / 2))
        : combinedMetricsContentWidth;
    const signalModesSectionContentWidth = combinedPanelsWide
        ? Math.max(24, combinedSectionWideBudget - confidenceSectionContentWidth)
        : combinedMetricsContentWidth;
    const retrainPanelContentWidth = twoColumnPanelContentWidth;
    const confusionPanelContentWidth = Math.max(9, confusionBoxWidth - 4);
    const confusionCellWidth = Math.max(4, Math.floor((confusionPanelContentWidth - 1) / 2));
    const calibrationWidths = useMemo(() => {
        const rangeWidth = 8;
        const nWidth = 5;
        const gapCount = 4;
        const metricWidth = Math.max(7, Math.floor((confidenceSectionContentWidth - rangeWidth - nWidth - gapCount) / 3));
        const used = rangeWidth + nWidth + gapCount + metricWidth * 3;
        return {
            rangeWidth: rangeWidth + Math.max(0, confidenceSectionContentWidth - used),
            metricWidth,
            nWidth
        };
    }, [confidenceSectionContentWidth]);
    const signalModeWidths = useMemo(() => {
        const useWidth = 7;
        const winWidth = 7;
        const edgeWidth = 7;
        const pnlWidth = Math.max(12, Math.min(14, Math.floor(signalModesSectionContentWidth * 0.22)));
        const gapCount = 4;
        return {
            modeWidth: Math.max(10, signalModesSectionContentWidth - useWidth - winWidth - edgeWidth - pnlWidth - gapCount),
            useWidth,
            winWidth,
            edgeWidth,
            pnlWidth
        };
    }, [signalModesSectionContentWidth]);
    const retrainWidths = useMemo(() => {
        const sampleWidth = 8;
        const brierWidth = 7;
        const lossWidth = 7;
        const gapCount = 4;
        const minTimeWidth = 13;
        let stateWidth = Math.max(8, Math.min(12, Math.floor(retrainPanelContentWidth * 0.2)));
        let timeWidth = retrainPanelContentWidth - gapCount - sampleWidth - brierWidth - lossWidth - stateWidth;
        if (timeWidth < minTimeWidth) {
            const reclaimed = Math.min(minTimeWidth - timeWidth, Math.max(0, stateWidth - 8));
            stateWidth -= reclaimed;
            timeWidth += reclaimed;
        }
        if (timeWidth < minTimeWidth) {
            timeWidth = minTimeWidth;
            stateWidth = Math.max(8, retrainPanelContentWidth - gapCount - sampleWidth - brierWidth - lossWidth - timeWidth);
        }
        return {
            timeWidth,
            sampleWidth,
            brierWidth,
            lossWidth,
            stateWidth
        };
    }, [retrainPanelContentWidth]);
    const clampedSelectedPanelIndex = Math.max(0, Math.min(selectedPanelIndex, MODEL_PANEL_DEFS.length - 1));
    const selectedPanel = MODEL_PANEL_DEFS[clampedSelectedPanelIndex];
    const relatedSettings = useMemo(() => selectedPanel.settingKeys.map((key) => configFieldByKey.get(key)).filter(isDefined), [configFieldByKey, selectedPanel]);
    const clampedSelectedSettingIndex = relatedSettings.length > 0
        ? Math.max(0, Math.min(selectedSettingIndex, relatedSettings.length - 1))
        : 0;
    const helpModalWidth = Math.max(70, Math.min(terminal.width - 8, terminal.wide ? 118 : 94));
    const helpContentWidth = Math.max(52, helpModalWidth - 4);
    const helpSettingLabelWidth = Math.max(18, Math.min(28, Math.floor(helpContentWidth * 0.48)));
    const helpSettingValueWidth = Math.max(14, helpContentWidth - helpSettingLabelWidth - 1);
    const helpIndexLabel = `${clampedSelectedPanelIndex + 1}/${MODEL_PANEL_DEFS.length}`;
    const helpTitleWidth = Math.max(1, helpContentWidth - helpIndexLabel.length - 1);
    const helpSpacerLine = ' '.repeat(helpModalWidth - 2);
    const formatConfigValue = (key) => {
        const field = configFieldByKey.get(key);
        if (!field)
            return '-';
        return formatEditableConfigValue(field, settingsValues[key] || field.defaultValue);
    };
    const rawConfigValue = (key) => settingsValues[key] || configFieldByKey.get(key)?.defaultValue || '';
    const baseCadenceRaw = rawConfigValue('RETRAIN_BASE_CADENCE');
    const retrainHourRaw = rawConfigValue('RETRAIN_HOUR_LOCAL');
    const baseCadenceValue = formatConfigValue('RETRAIN_BASE_CADENCE');
    const retrainHourValue = formatConfigValue('RETRAIN_HOUR_LOCAL');
    const earlyCheckValue = formatConfigValue('RETRAIN_EARLY_CHECK_INTERVAL');
    const earlyTriggerValue = formatConfigValue('RETRAIN_MIN_NEW_LABELS');
    const earlyTriggerThreshold = parseNonNegativeInt(rawConfigValue('RETRAIN_MIN_NEW_LABELS'), 100);
    const minSamplesThreshold = parseNonNegativeInt(rawConfigValue('RETRAIN_MIN_SAMPLES'), 200);
    const hasDeployedModel = Number(trainingProgress?.last_deployed_trained_at || 0) > 0;
    const triggerProgressCurrent = hasDeployedModel
        ? Math.max(0, Number(trainingProgress?.new_labeled || 0))
        : Math.max(0, Number(trainingProgress?.total_labeled || 0));
    const triggerProgressTarget = hasDeployedModel ? earlyTriggerThreshold : minSamplesThreshold;
    const triggerProgressValue = `${formatCount(triggerProgressCurrent)} / ${formatCount(triggerProgressTarget)}${hasDeployedModel ? ' new' : ' total'}`;
    const manualRunItem = manualRetrainLabel(Number(botState.started_at || 0), Number(botState.last_activity_at || 0), Number(botState.poll_interval || 1), Boolean(botState.retrain_in_progress), nowTs);
    const nextScheduledRetrainTs = useMemo(() => getNextScheduledRetrainTs(normalizeCadence(baseCadenceRaw), clampHour(retrainHourRaw), nowTs), [baseCadenceRaw, retrainHourRaw, nowTs]);
    const trackerHealthStats = useMemo(() => [
        { label: 'Signals logged', value: formatCount(tracker?.signals) },
        { label: 'Bets taken', value: formatCount(tracker?.taken) },
        {
            label: 'Use rate',
            value: formatPct(useRate, 1),
            color: useRate != null ? probabilityColor(Math.max(0.5, useRate)) : theme.dim
        },
        { label: 'Resolved bets', value: formatCount(tracker?.resolved) },
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
    ], [tracker, trackerSnapshot, trackerWinRate, useRate]);
    const trackerHealthColumns = useMemo(() => splitIntoColumns(trackerHealthStats, 2), [trackerHealthStats]);
    const replaySearchSuggestedConfig = useMemo(() => replaySearchConfigSuggestion(latestReplaySearch?.config_json, settingsValues, configFieldByKey), [configFieldByKey, latestReplaySearch?.config_json, settingsValues]);
    const replaySearchApplyScopeSummary = useMemo(() => replaySearchApplyScope(latestReplaySearch?.config_json, settingsValues, configFieldByKey), [configFieldByKey, latestReplaySearch?.config_json, settingsValues]);
    const replaySearchDeployGap = useMemo(() => replaySearchDeployGapSummary(latestReplaySearch?.policy_json, latestReplaySearch?.config_json), [latestReplaySearch?.config_json, latestReplaySearch?.policy_json]);
    const replaySearchCurrentModeRisk = useMemo(() => replaySearchCurrentModeRiskSummary(latestReplaySearch?.current_candidate_result_json, latestReplaySearch?.constraints_json, latestReplaySearch?.base_policy_json), [latestReplaySearch?.base_policy_json, latestReplaySearch?.constraints_json, latestReplaySearch?.current_candidate_result_json]);
    const replaySearchBestHeadroom = useMemo(() => replaySearchHeadroomSummary(latestReplaySearch?.result_json, latestReplaySearch?.constraints_json, latestReplaySearch?.policy_json), [latestReplaySearch?.constraints_json, latestReplaySearch?.policy_json, latestReplaySearch?.result_json]);
    const replaySearchCurrentHeadroom = useMemo(() => replaySearchHeadroomSummary(latestReplaySearch?.current_candidate_result_json, latestReplaySearch?.constraints_json, latestReplaySearch?.base_policy_json), [latestReplaySearch?.base_policy_json, latestReplaySearch?.constraints_json, latestReplaySearch?.current_candidate_result_json]);
    const replaySearchPauseGuard = useMemo(() => replaySearchPauseGuardSummary(latestReplaySearch?.result_json, latestReplaySearch?.current_candidate_result_json, latestReplaySearch?.constraints_json, latestReplaySearch?.pause_guard_penalty, latestReplaySearch?.daily_guard_window_penalty, latestReplaySearch?.live_guard_window_penalty, latestReplaySearch?.daily_guard_restart_window_penalty, latestReplaySearch?.live_guard_restart_window_penalty), [
        latestReplaySearch?.constraints_json,
        latestReplaySearch?.current_candidate_result_json,
        latestReplaySearch?.daily_guard_restart_window_penalty,
        latestReplaySearch?.daily_guard_window_penalty,
        latestReplaySearch?.live_guard_window_penalty,
        latestReplaySearch?.live_guard_restart_window_penalty,
        latestReplaySearch?.pause_guard_penalty,
        latestReplaySearch?.result_json
    ]);
    const replaySearchTraderConcentration = useMemo(() => replaySearchTraderConcentrationSummary(latestReplaySearch?.result_json, latestReplaySearch?.current_candidate_result_json, latestReplaySearch?.constraints_json, latestReplaySearch?.wallet_concentration_penalty, latestReplaySearch?.wallet_count_penalty, latestReplaySearch?.wallet_size_concentration_penalty), [
        latestReplaySearch?.constraints_json,
        latestReplaySearch?.current_candidate_result_json,
        latestReplaySearch?.result_json,
        latestReplaySearch?.wallet_concentration_penalty,
        latestReplaySearch?.wallet_count_penalty,
        latestReplaySearch?.wallet_size_concentration_penalty
    ]);
    const replaySearchMarketConcentration = useMemo(() => replaySearchMarketConcentrationSummary(latestReplaySearch?.result_json, latestReplaySearch?.current_candidate_result_json, latestReplaySearch?.constraints_json, latestReplaySearch?.market_concentration_penalty, latestReplaySearch?.market_count_penalty, latestReplaySearch?.market_size_concentration_penalty), [
        latestReplaySearch?.constraints_json,
        latestReplaySearch?.current_candidate_result_json,
        latestReplaySearch?.result_json,
        latestReplaySearch?.market_concentration_penalty,
        latestReplaySearch?.market_count_penalty,
        latestReplaySearch?.market_size_concentration_penalty
    ]);
    const replaySearchEntryPriceBandConcentration = useMemo(() => replaySearchEntryPriceBandConcentrationSummary(latestReplaySearch?.result_json, latestReplaySearch?.current_candidate_result_json, latestReplaySearch?.constraints_json, latestReplaySearch?.entry_price_band_concentration_penalty, latestReplaySearch?.entry_price_band_count_penalty, latestReplaySearch?.entry_price_band_size_concentration_penalty), [
        latestReplaySearch?.constraints_json,
        latestReplaySearch?.current_candidate_result_json,
        latestReplaySearch?.result_json,
        latestReplaySearch?.entry_price_band_concentration_penalty,
        latestReplaySearch?.entry_price_band_count_penalty,
        latestReplaySearch?.entry_price_band_size_concentration_penalty
    ]);
    const replaySearchTimeToCloseBandConcentration = useMemo(() => replaySearchTimeToCloseBandConcentrationSummary(latestReplaySearch?.result_json, latestReplaySearch?.current_candidate_result_json, latestReplaySearch?.constraints_json, latestReplaySearch?.time_to_close_band_concentration_penalty, latestReplaySearch?.time_to_close_band_count_penalty, latestReplaySearch?.time_to_close_band_size_concentration_penalty), [
        latestReplaySearch?.constraints_json,
        latestReplaySearch?.current_candidate_result_json,
        latestReplaySearch?.result_json,
        latestReplaySearch?.time_to_close_band_concentration_penalty,
        latestReplaySearch?.time_to_close_band_count_penalty,
        latestReplaySearch?.time_to_close_band_size_concentration_penalty
    ]);
    const replayLabStats = useMemo(() => [
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
            label: 'Score weights',
            value: replaySearchScoreWeightSummary(latestReplaySearch),
            color: latestReplaySearch ? theme.white : theme.dim
        },
        {
            label: 'Best score',
            value: replaySearchScoreBreakdownSummary(latestReplaySearch?.result_json),
            color: latestReplaySearch?.result_json ? theme.white : theme.dim
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
            value: replaySearchWindowSummary(latestReplaySearch),
            color: dollarColor(replaySearchWorstWindowPnlFromSummaryRow(latestReplaySearch))
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
            value: replaySearchCurrentModeEvidenceSummary(latestReplaySearch?.current_candidate_result_json, latestReplaySearch?.base_policy_json),
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
            value: replaySearchModeDriftSummary(latestReplaySearch?.result_json, latestReplaySearch?.current_candidate_result_json, latestReplaySearch?.policy_json, latestReplaySearch?.base_policy_json),
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
            value: replaySearchFailureSummary(latestReplaySearch?.current_candidate_constraint_failures_json, latestReplaySearch?.current_candidate_feasible),
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
            value: replaySearchScoreDriftSummary(latestReplaySearch?.result_json, latestReplaySearch?.current_candidate_result_json),
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
    ], [
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
    ]);
    const trainingCycleStats = useMemo(() => [
        { label: 'Update style', value: 'Full retrain' },
        { label: 'Base cadence', value: baseCadenceValue },
        { label: 'Run time', value: retrainHourValue },
        { label: 'Next scheduled', value: formatShortDateTime(nextScheduledRetrainTs) },
        { label: 'Scheduled in', value: timeUntil(nextScheduledRetrainTs) },
        { label: 'Early check', value: earlyCheckValue },
        { label: 'Early trigger', value: earlyTriggerValue },
        {
            label: 'Trigger progress',
            value: triggerProgressValue,
            color: progressStatColor(triggerProgressCurrent, triggerProgressTarget)
        },
        manualRunItem,
        { label: 'Total runs', value: formatCount(trainingSummary?.total_runs) }
    ], [
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
    ]);
    const trainingCycleColumns = useMemo(() => splitIntoColumns(trainingCycleStats, 2), [trainingCycleStats]);
    const trainingCycleDisplayStats = useMemo(() => trainingCycleStats.map((item) => ({
        ...item,
        label: trainingCycleDisplayLabel(item.label)
    })), [trainingCycleStats]);
    const exitGuardStats = useMemo(() => [
        {
            label: 'Mode',
            value: botState.mode === 'live' ? 'Live' : 'Shadow',
            color: botState.mode === 'live' ? theme.red : theme.blue
        },
        { label: 'Audits 7d', value: formatCount(exitAuditSummary?.audits_7d) },
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
    ], [botState.mode, exitAttribution, exitAuditSummary]);
    const confusionCells = useMemo(() => [
        { label: 'TP', value: Math.max(0, Number(confusion?.true_positive || 0)), kind: 'good' },
        { label: 'FP', value: Math.max(0, Number(confusion?.false_positive || 0)), kind: 'bad' },
        { label: 'TN', value: Math.max(0, Number(confusion?.true_negative || 0)), kind: 'good' },
        { label: 'FN', value: Math.max(0, Number(confusion?.false_negative || 0)), kind: 'bad' }
    ], [confusion]);
    const confusionScale = useMemo(() => Math.max(1, ...confusionCells.map((cell) => cell.value)), [confusionCells]);
    const calibrationBias = useMemo(() => (calibration?.avg_confidence != null && calibration?.actual_win_rate != null
        ? calibration.avg_confidence - calibration.actual_win_rate
        : null), [calibration?.actual_win_rate, calibration?.avg_confidence]);
    const mainCalibrationBucket = useMemo(() => calibrationRows.reduce((best, row) => (best == null || row.n > best.n ? row : best), null), [calibrationRows]);
    const confidenceCheckStats = useMemo(() => [
        { label: 'Resolved', value: formatCount(calibration?.resolved) },
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
    ], [
        calibration?.actual_win_rate,
        calibration?.avg_confidence,
        calibration?.avg_gap,
        calibration?.resolved,
        calibrationBias,
        mainCalibrationBucket
    ]);
    const confidenceCheckColumns = useMemo(() => splitIntoColumns(confidenceCheckStats, 2), [confidenceCheckStats]);
    const loadedScorerLabel = botState.loaded_scorer ? modeLabel(botState.loaded_scorer) : 'Heuristic';
    const loadedScorerColor = loadedScorerLabel === 'XGBoost'
        ? theme.green
        : loadedScorerLabel === 'No scorer'
            ? theme.red
            : theme.yellow;
    const scorerConfigLabel = useMemo(() => {
        const heuristicEnabled = botState.heuristic_enabled;
        const xgboostEnabled = botState.xgboost_enabled;
        if (heuristicEnabled == null || xgboostEnabled == null)
            return '-';
        const heuristicLabel = heuristicEnabled ? 'on' : 'off';
        const xgboostLabel = xgboostEnabled ? 'on' : 'off';
        return `heur ${heuristicLabel} | xgb ${xgboostLabel}`;
    }, [botState.heuristic_enabled, botState.xgboost_enabled]);
    const scorerConfigColor = useMemo(() => {
        const heuristicEnabled = botState.heuristic_enabled;
        const xgboostEnabled = botState.xgboost_enabled;
        if (heuristicEnabled == null || xgboostEnabled == null)
            return theme.dim;
        if (heuristicEnabled === false && xgboostEnabled === false)
            return theme.red;
        if (heuristicEnabled === true && xgboostEnabled === true)
            return theme.green;
        return theme.yellow;
    }, [botState.heuristic_enabled, botState.xgboost_enabled, theme.dim, theme.green, theme.red, theme.yellow]);
    const deployedModelLabel = botState.model_artifact_exists
        ? modeLabel(botState.model_artifact_backend || 'unknown')
        : '-';
    const deployedModelColor = !botState.model_artifact_exists
        ? theme.dim
        : botState.model_runtime_compatible
            ? theme.green
            : theme.yellow;
    const contractLabel = (botState.model_artifact_contract != null && botState.runtime_contract != null
        ? `${botState.model_artifact_contract} / ${botState.runtime_contract}`
        : '-');
    const contractColor = !botState.model_artifact_exists
        ? theme.dim
        : botState.model_runtime_compatible
            ? theme.green
            : theme.red;
    const fallbackLabel = useMemo(() => {
        const reason = String(botState.model_fallback_reason || '').trim().toLowerCase();
        if (!reason)
            return '-';
        if (reason === 'missing_artifact')
            return 'No artifact';
        if (reason === 'contract_mismatch')
            return 'Contract mismatch';
        if (reason === 'label_mode_mismatch')
            return 'Label mismatch';
        if (reason === 'legacy_artifact_type')
            return 'Legacy artifact';
        if (reason === 'load_failed')
            return 'Load failed';
        return reason.replace(/_/g, ' ');
    }, [botState.model_fallback_reason]);
    const fallbackColor = fallbackLabel === '-'
        ? theme.dim
        : fallbackLabel === 'No artifact'
            ? theme.yellow
            : theme.red;
    const recentActiveMode = useMemo(() => recentSignalModes.reduce((best, row) => {
        if (best == null)
            return row.mode;
        const currentBest = recentSignalModes.find((candidate) => candidate.mode === best);
        return (currentBest?.taken || 0) >= row.taken ? best : row.mode;
    }, null), [recentSignalModes]);
    const activeScorerLabel = recentActiveMode ? modeLabel(recentActiveMode) : loadedScorerLabel;
    const activeScorerColor = activeScorerLabel === 'XGBoost' ? theme.green : theme.yellow;
    const primaryMode = useMemo(() => signalModes.reduce((best, row) => {
        if (best == null)
            return row.mode;
        const currentBest = signalModes.find((candidate) => candidate.mode === best);
        return (currentBest?.taken || 0) >= row.taken ? best : row.mode;
    }, null), [signalModes]);
    const signalModeCards = useMemo(() => signalModes
        .filter((row) => row.mode.trim().toLowerCase() !== 'veto')
        .map((row) => {
        const modeWinRate = ratio(row.wins, row.resolved);
        const modeUseRate = ratio(row.taken, row.signals);
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
        };
    }), [activeScorerLabel, primaryMode, signalModes]);
    const signalModeCardColumns = useMemo(() => splitIntoColumns(signalModeCards, combinedPanelsWide && signalModeCards.length > 1 ? 2 : 1), [combinedPanelsWide, signalModeCards]);
    const recentExitAudits = useMemo(() => recentExitAuditRows.slice(0, exitAuditHistoryLimit), [exitAuditHistoryLimit, recentExitAuditRows]);
    const baseHeuristicScore = useMemo(() => (flow?.trader_score != null && flow?.market_score != null
        ? (Math.max(flow.trader_score, 0) ** 0.6) * (Math.max(flow.market_score, 0) ** 0.4)
        : null), [flow?.market_score, flow?.trader_score]);
    const finalHeuristicEstimate = useMemo(() => (baseHeuristicScore != null && flow?.belief_prior != null && flow?.belief_blend != null
        ? ((1 - flow.belief_blend) * baseHeuristicScore) + (flow.belief_blend * flow.belief_prior)
        : null), [baseHeuristicScore, flow?.belief_blend, flow?.belief_prior]);
    const priorPull = useMemo(() => (finalHeuristicEstimate != null && baseHeuristicScore != null
        ? finalHeuristicEstimate - baseHeuristicScore
        : null), [baseHeuristicScore, finalHeuristicEstimate]);
    const scoringMixStats = useMemo(() => [
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
    ], [
        baseHeuristicScore,
        finalHeuristicEstimate,
        flow?.belief_blend,
        flow?.belief_evidence,
        flow?.belief_prior,
        flow?.market_score,
        flow?.trader_score,
        priorPull
    ]);
    const scoringMixColumns = useMemo(() => splitIntoColumns(scoringMixStats, 2), [scoringMixStats]);
    const modelsColumnGap = terminal.compact ? 1 : 2;
    const modelsContentWidth = Math.max(50, terminal.width - 10);
    const modelsUsableWidth = Math.max(18, modelsContentWidth - modelsColumnGap * 2);
    const leftModelsColumnWidth = Math.max(18, Math.floor(modelsUsableWidth * 0.26));
    const middleModelsColumnWidth = Math.max(18, Math.floor(modelsUsableWidth * 0.27));
    const modelsColumnWidths = [
        leftModelsColumnWidth,
        middleModelsColumnWidth,
        Math.max(18, modelsUsableWidth - leftModelsColumnWidth - middleModelsColumnWidth)
    ];
    const predictionQualityStats = useMemo(() => [
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
        { label: 'Contract', value: contractLabel, color: contractColor },
        { label: 'Fallback', value: fallbackLabel, color: fallbackColor },
        { label: 'Trained', value: latest ? formatShortDateTime(latest.trained_at) : '-' },
        { label: 'Model age', value: latest ? secondsAgo(latest.trained_at) : '-' },
        { label: 'Samples', value: formatCount(latest?.n_samples) },
        { label: 'Features', value: featureCount != null ? formatCount(featureCount) : '-' },
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
    ], [
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
    ]);
    const recentRetrainRuns = useMemo(() => retrainRuns.slice(0, historyLimit), [historyLimit, retrainRuns]);
    const howItWorksScoreRows = useMemo(() => scoringMixStats.slice(0, 4), [scoringMixStats]);
    const howItWorksHistoryRows = useMemo(() => scoringMixStats.slice(4), [scoringMixStats]);
    const renderPageBody = () => (React.createElement(InkBox, { width: "100%" },
        React.createElement(InkBox, { width: modelsColumnWidths[0], flexDirection: "column" },
            React.createElement(ModelsSectionTitle, { title: "Prediction Quality", width: modelsColumnWidths[0], selected: clampedSelectedPanelIndex === 0, backgroundColor: selectedRowBackground }),
            predictionQualityStats.map((item) => (React.createElement(DenseModelsRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white, width: modelsColumnWidths[0], labelWidth: 14 }))),
            React.createElement(ModelsSpacer, null),
            React.createElement(ModelsSectionTitle, { title: "Tracker Health", width: modelsColumnWidths[0], selected: clampedSelectedPanelIndex === 1, backgroundColor: selectedRowBackground }),
            trackerHealthStats.map((item) => (React.createElement(DenseModelsRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white, width: modelsColumnWidths[0], labelWidth: 14 }))),
            React.createElement(ModelsSpacer, null),
            React.createElement(ModelsSectionTitle, { title: "Replay Lab", width: modelsColumnWidths[0], selected: clampedSelectedPanelIndex === 2, backgroundColor: selectedRowBackground }),
            replayLabStats.map((item) => (React.createElement(DenseModelsRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white, width: modelsColumnWidths[0], labelWidth: 14 })))),
        React.createElement(InkBox, { width: modelsColumnGap }),
        React.createElement(InkBox, { width: modelsColumnWidths[1], flexDirection: "column" },
            React.createElement(ModelsSectionTitle, { title: "Confidence + Modes", width: modelsColumnWidths[1], selected: clampedSelectedPanelIndex === 3, backgroundColor: selectedRowBackground }),
            confusionCells.map((cell) => (React.createElement(DenseModelsRow, { key: cell.label, label: cell.label, value: formatCount(cell.value), color: confusionHeatColor(cell.value, confusionScale, cell.kind), width: modelsColumnWidths[1], labelWidth: 12 }))),
            React.createElement(ModelsSpacer, null),
            React.createElement(ModelsSubsectionTitle, { title: "Calibration", width: modelsColumnWidths[1] }),
            confidenceCheckStats.map((item) => (React.createElement(DenseModelsRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white, width: modelsColumnWidths[1], labelWidth: 12 }))),
            React.createElement(ModelsSpacer, null),
            React.createElement(ModelsSubsectionTitle, { title: "Decision Paths", width: modelsColumnWidths[1] }),
            React.createElement(DenseModelsRow, { label: "Recent path", value: activeScorerLabel, color: activeScorerColor, width: modelsColumnWidths[1], labelWidth: 12 }),
            React.createElement(DenseModelsRow, { label: "Primary path", value: primaryMode ? modeLabel(primaryMode) : '-', color: primaryMode ? theme.accent : theme.dim, width: modelsColumnWidths[1], labelWidth: 12 }),
            signalModeCards.length ? (signalModeCards.map((card, index) => (React.createElement(React.Fragment, { key: card.title },
                React.createElement(ModelsSpacer, null),
                React.createElement(ModelsSubsectionTitle, { title: card.title, width: modelsColumnWidths[1], color: card.titleColor ?? theme.white }),
                card.rows.map((item) => (React.createElement(DenseModelsRow, { key: `${card.title}-${item.label}`, label: item.label, value: item.value, color: item.color ?? theme.white, width: modelsColumnWidths[1], labelWidth: 12 }))))))) : (React.createElement(Text, { color: theme.dim }, fit('No tracker signals yet.', modelsColumnWidths[1]))),
            React.createElement(ModelsSpacer, null),
            React.createElement(ModelsSectionTitle, { title: "Exit Guard", width: modelsColumnWidths[1], selected: clampedSelectedPanelIndex === 4, backgroundColor: selectedRowBackground }),
            exitGuardStats.map((item) => (React.createElement(DenseModelsRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white, width: modelsColumnWidths[1], labelWidth: 12 }))),
            React.createElement(ModelsSpacer, null),
            React.createElement(RecentExitHeaderRow, { width: modelsColumnWidths[1] }),
            recentExitAudits.length ? (recentExitAudits.map((row, index) => (React.createElement(RecentExitDataRow, { key: `${row.audited_at}-${row.decision || 'decision'}-${index}`, width: modelsColumnWidths[1], timestamp: formatShortDateTime(row.audited_at), timestampColor: theme.dim, action: exitDecisionLabel(row.decision, row.reason), actionColor: exitDecisionColor(row.decision, row.reason), estimatedReturn: formatPct(row.estimated_return_pct, 1), estimatedReturnColor: signedMetricColor(row.estimated_return_pct), reason: String(row.reason || '').trim() || '-', reasonColor: theme.dim })))) : (React.createElement(Text, { color: theme.dim }, fit('No exit audits logged yet.', modelsColumnWidths[1])))),
        React.createElement(InkBox, { width: modelsColumnGap }),
        React.createElement(InkBox, { width: modelsColumnWidths[2], flexDirection: "column" },
            React.createElement(ModelsSectionTitle, { title: "How It Works", width: modelsColumnWidths[2], selected: clampedSelectedPanelIndex === 5, backgroundColor: selectedRowBackground }),
            howItWorksScoreRows.map((item) => (React.createElement(DenseModelsRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white, width: modelsColumnWidths[2], labelWidth: 14 }))),
            React.createElement(ModelsSpacer, null),
            React.createElement(ModelsSpacer, null),
            React.createElement(ModelsSubsectionTitle, { title: "History Nudge", width: modelsColumnWidths[2] }),
            howItWorksHistoryRows.map((item) => (React.createElement(DenseModelsRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white, width: modelsColumnWidths[2], labelWidth: 14 }))),
            React.createElement(ModelsSpacer, null),
            React.createElement(ModelsSectionTitle, { title: "Training Cycle", width: modelsColumnWidths[2], selected: clampedSelectedPanelIndex === 6, backgroundColor: selectedRowBackground }),
            trainingCycleDisplayStats.map((item) => (React.createElement(DenseModelsRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white, selected: clampedSelectedPanelIndex === 6 && item.label === 'Manual run', backgroundColor: selectedRowBackground, width: modelsColumnWidths[2], labelWidth: 11, minValueWidth: 16 }))),
            latestSharedHoldoutRun && latestSharedHoldout ? (React.createElement(React.Fragment, null,
                React.createElement(DenseModelsRow, { label: "Holdout gate", value: sharedHoldoutGateReadCompact(latestSharedHoldoutRun), color: sharedHoldoutGateReadColor(latestSharedHoldoutRun), width: modelsColumnWidths[2], labelWidth: 11, minValueWidth: 16 }),
                React.createElement(DenseModelsRow, { label: "Gate run", value: formatShortDateTime(latestSharedHoldoutRun.finished_at), width: modelsColumnWidths[2], labelWidth: 11, minValueWidth: 16 }),
                React.createElement(DenseModelsRow, { label: "LL c / i", value: `${formatNumber(latestSharedHoldout.challenger_log_loss, 4)} / ${formatNumber(latestSharedHoldout.incumbent_log_loss, 4)}`, color: sharedHoldoutGateReadColor(latestSharedHoldoutRun), width: modelsColumnWidths[2], labelWidth: 11, minValueWidth: 16 }),
                React.createElement(DenseModelsRow, { label: "Brier c / i", value: `${formatNumber(latestSharedHoldout.challenger_brier_score, 4)} / ${formatNumber(latestSharedHoldout.incumbent_brier_score, 4)}`, color: sharedHoldoutGateReadColor(latestSharedHoldoutRun), width: modelsColumnWidths[2], labelWidth: 11, minValueWidth: 16 }))) : null,
            React.createElement(ModelsSpacer, null),
            React.createElement(RecentRunsHeaderRow, { width: modelsColumnWidths[2] }),
            recentRetrainRuns.length ? (recentRetrainRuns.map((row, index) => (React.createElement(RecentRunsDataRow, { key: `${row.finished_at}-${row.status || 'run'}-${index}`, width: modelsColumnWidths[2], timestamp: formatShortDateTime(row.finished_at), timestampColor: theme.dim, logLoss: formatNumber(row.log_loss, 3), logLossColor: lowerIsBetterColor(row.log_loss, 0.55, 0.69), brier: formatNumber(row.brier_score, 3), brierColor: lowerIsBetterColor(row.brier_score, 0.18, 0.25), result: retrainRunStateCompactLabel(row.status, row.deployed), resultColor: retrainRunStateColor(row.status, row.deployed) })))) : (React.createElement(Text, { color: theme.dim }, fit('No retrain attempts logged yet.', modelsColumnWidths[2]))))));
    return (React.createElement(InkBox, { flexDirection: "column", width: "100%" },
        renderPageBody(),
        detailOpen ? (React.createElement(ModalOverlay, { backgroundColor: terminal.backgroundColor },
            React.createElement(InkBox, { borderStyle: "round", borderColor: theme.accent, flexDirection: "column", width: helpModalWidth },
                React.createElement(InkBox, { width: "100%" },
                    React.createElement(Text, { color: theme.accent, backgroundColor: modalBackground, bold: true }, ` ${fit(selectedPanel.title, helpTitleWidth)}`),
                    React.createElement(Text, { backgroundColor: modalBackground }, " "),
                    React.createElement(Text, { color: theme.dim, backgroundColor: modalBackground }, `${fitRight(helpIndexLabel, helpIndexLabel.length)} `)),
                selectedPanel.summary.map((line) => (React.createElement(Text, { key: line, color: theme.white, backgroundColor: modalBackground }, ` ${fit(line, helpContentWidth)} `))),
                React.createElement(Text, { backgroundColor: modalBackground }, helpSpacerLine),
                React.createElement(InkBox, { flexDirection: "column" },
                    React.createElement(Text, { color: theme.accent, backgroundColor: modalBackground, bold: true }, ` ${fit('Label Guide', helpContentWidth)} `),
                    selectedPanel.rows.map((row) => (React.createElement(Text, { key: `${selectedPanel.id}-${row.label}`, color: theme.white, backgroundColor: modalBackground }, ` ${fit(`${row.label}: ${row.text}`, helpContentWidth)} `)))),
                React.createElement(Text, { backgroundColor: modalBackground }, helpSpacerLine),
                React.createElement(InkBox, { flexDirection: "column" },
                    React.createElement(Text, { color: theme.accent, backgroundColor: modalBackground, bold: true }, ` ${fit('Related Settings', helpContentWidth)} `),
                    relatedSettings.length ? (React.createElement(React.Fragment, null,
                        relatedSettings.map((field, index) => {
                            const selected = index === clampedSelectedSettingIndex;
                            const label = `${selected ? '> ' : '  '}${field.label}`;
                            const rowBackground = selected ? selectedRowBackground : modalBackground;
                            return (React.createElement(InkBox, { key: `${selectedPanel.id}-${field.key}`, width: "100%" },
                                React.createElement(Text, { color: selected ? theme.accent : theme.dim, backgroundColor: rowBackground, bold: selected }, ` ${fit(label, helpSettingLabelWidth)}`),
                                React.createElement(Text, { backgroundColor: rowBackground }, " "),
                                React.createElement(Text, { color: theme.white, backgroundColor: rowBackground, bold: selected }, `${fitRight(formatEditableConfigValue(field, settingsValues[field.key] || field.defaultValue), helpSettingValueWidth)} `)));
                        }),
                        React.createElement(Text, { color: theme.dim, backgroundColor: modalBackground }, ` ${fit('Up/down selects a setting. Enter opens it in Config. Esc closes.', helpContentWidth)} `))) : (React.createElement(Text, { color: theme.dim, backgroundColor: modalBackground }, ` ${fit('No direct settings are tied to this box yet. Esc closes.', helpContentWidth)} `)))))) : null));
}
