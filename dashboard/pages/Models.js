import React, { useEffect, useMemo, useState } from 'react';
import { Box as InkBox, Text } from 'ink';
import { Box } from '../components/Box.js';
import { ModalOverlay } from '../components/ModalOverlay.js';
import { StatRow } from '../components/StatRow.js';
import { editableConfigFields, formatEditableConfigValue } from '../configEditor.js';
import { fit, fitRight, formatDollar, formatNumber, formatPct, formatShortDateTime, secondsAgo, timeUntil } from '../format.js';
import { stackPanels } from '../responsive.js';
import { useTerminalSize } from '../terminal.js';
import { negativeHeatColor, positiveDollarColor, probabilityColor, selectionBackgroundColor, theme } from '../theme.js';
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
            'This box grades the currently active scorer itself, not the bankroll curve.',
            'Lower loss numbers mean the probabilities are closer to what actually happened.'
        ],
        rows: [
            { label: 'Active path', text: 'Which scorer is currently making decisions: XGBoost or Heuristic.' },
            { label: 'Trained', text: 'When the latest deployed model was built.' },
            { label: 'Model age', text: 'How long the active deployed model has been running without a retrain.' },
            { label: 'Samples', text: 'How many resolved trades were available to train on.' },
            { label: 'Features', text: 'How many inputs the deployed model is using.' },
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
        id: 'confusion_matrix',
        title: 'Confusion Matrix',
        summary: [
            'This box is the outcome split behind the confidence gate.',
            'It shows where accepted bets were right or wrong, and where low-confidence skips helped or hurt.'
        ],
        rows: [
            { label: 'TP', text: 'Accepted bets that settled profitable.' },
            { label: 'FP', text: 'Accepted bets that settled unprofitable.' },
            { label: 'TN', text: 'Low-confidence skips that would have lost anyway.' },
            { label: 'FN', text: 'Low-confidence skips that would have won if taken.' },
            { label: 'Top row', text: 'Accepted bets. Left is good, right is bad.' },
            { label: 'Bottom row', text: 'Skipped bets. Left is good, right is bad.' },
            { label: 'Color scale', text: 'Bigger counts get stronger green or red heat.' }
        ],
        settingKeys: ['MIN_CONFIDENCE']
    },
    {
        id: 'confidence_modes',
        title: 'Confidence + Modes',
        summary: [
            'This box answers two questions: how calibrated are accepted bets, and which decision path is carrying the load?',
            'The left side reads model bias. The right side shows which scorer is active, primary, or idle.'
        ],
        rows: [
            { label: 'Resolved', text: 'Accepted tracker bets that already settled and can be graded.' },
            { label: 'Predicted / Actual', text: 'Average model confidence versus the realized win rate on those graded bets.' },
            { label: 'Read / Bias', text: 'Plain-English bias read plus the point gap between prediction and reality.' },
            { label: 'Avg miss', text: 'Average miss per graded bet versus the actual 0/1 outcome. Lower is better.' },
            { label: 'Main band / hit', text: 'The most common confidence range and how often it actually won.' },
            { label: 'Active scorer', text: 'Which scorer is currently driving accepted trades right now.' },
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
            { label: 'Last / Avg gap', text: 'Observed time between recent retrain attempts.' },
            { label: 'Runs 7d / 30d', text: 'How many retrain attempts landed recently, including failures and skips.' }
        ],
        settingKeys: ['RETRAIN_BASE_CADENCE', 'RETRAIN_HOUR_LOCAL', 'RETRAIN_EARLY_CHECK_INTERVAL', 'RETRAIN_MIN_NEW_LABELS', 'RETRAIN_MIN_SAMPLES']
    }
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
SELECT finished_at, sample_count, brier_score, log_loss, status, deployed
FROM retrain_runs
ORDER BY finished_at DESC, id DESC
LIMIT 48
`;
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
    if (value <= good)
        return theme.green;
    if (value <= okay)
        return theme.yellow;
    return theme.red;
}
function sharpeColor(value) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    if (value >= 1)
        return theme.green;
    if (value >= 0)
        return theme.yellow;
    return theme.red;
}
function signedMetricColor(value) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    if (value > 0)
        return theme.green;
    if (value < 0)
        return theme.red;
    return theme.white;
}
function biasColor(value) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    if (Math.abs(value) <= 0.03)
        return theme.yellow;
    return value > 0 ? theme.red : theme.blue;
}
function dollarColor(value) {
    if (value == null || Number.isNaN(value))
        return theme.dim;
    if (value < 0)
        return theme.red;
    return positiveDollarColor(value);
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
        return normalizedMode === activeScorerLabel.trim().toLowerCase() ? 'Primary live path' : 'Primary path';
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
    if (normalized.startsWith('skipped_'))
        return 'skipped';
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
    if (normalized === 'heuristic')
        return 'Heuristic';
    if (normalized === 'shadow')
        return 'Tracker';
    if (normalized === 'live')
        return 'Live';
    return mode || 'Unknown';
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
export function Models({ selectedPanelIndex, detailOpen, selectedSettingIndex, settingsValues }) {
    const terminal = useTerminalSize();
    const botState = useBotState();
    const modalBackground = terminal.backgroundColor || theme.modalBackground;
    const selectedRowBackground = selectionBackgroundColor(modalBackground);
    const nowTs = useNow();
    const stacked = stackPanels(terminal.width);
    const models = useQuery(MODEL_SQL);
    const retrainRuns = useQuery(RETRAIN_RUN_SQL);
    const trackerRows = useQuery(TRACKER_SQL);
    const perfRows = useQuery(PERF_SQL);
    const signalModes = useQuery(SIGNAL_MODE_SQL);
    const calibrationSummaryRows = useQuery(CALIBRATION_SUMMARY_SQL);
    const confusionRows = useQuery(CONFUSION_SQL);
    const calibrationRows = useQuery(CALIBRATION_SQL);
    const flowRows = useQuery(FLOW_SQL);
    const trainingSummaryRows = useQuery(TRAINING_SUMMARY_SQL);
    const trainingProgressRows = useQuery(TRAINING_PROGRESS_SQL);
    const latest = models[0];
    const tracker = trackerRows[0];
    const calibration = calibrationSummaryRows[0];
    const confusion = confusionRows[0];
    const flow = flowRows[0];
    const trainingSummary = trainingSummaryRows[0];
    const trainingProgress = trainingProgressRows[0];
    const trackerSnapshot = perfRows.find((row) => row.mode === 'shadow') ?? perfRows[0];
    const configFieldByKey = useMemo(() => new Map(editableConfigFields.map((field) => [field.key, field])), []);
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
    const lastRetrainGap = retrainGaps[0] ?? null;
    const averageRetrainGap = retrainGaps.length > 0
        ? retrainGaps.reduce((sum, gap) => sum + gap, 0) / retrainGaps.length
        : null;
    const calibrationLimit = terminal.compact ? 3 : terminal.height < 42 ? 4 : 5;
    const historyLimit = terminal.compact ? 4 : terminal.height < 42 ? 5 : terminal.height < 50 ? 7 : 10;
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
        { label: 'Total runs', value: formatCount(trainingSummary?.total_runs) },
        { label: 'Last gap', value: formatInterval(lastRetrainGap) },
        { label: 'Avg gap', value: formatInterval(averageRetrainGap) },
        { label: 'Runs 7d', value: formatCount(trainingSummary?.runs_7d) },
        { label: 'Runs 30d', value: formatCount(trainingSummary?.runs_30d) }
    ], [
        averageRetrainGap,
        baseCadenceValue,
        earlyCheckValue,
        earlyTriggerValue,
        lastRetrainGap,
        manualRunItem,
        nextScheduledRetrainTs,
        retrainHourValue,
        triggerProgressCurrent,
        triggerProgressTarget,
        triggerProgressValue,
        trainingSummary?.runs_7d,
        trainingSummary?.runs_30d,
        trainingSummary?.total_runs
    ]);
    const trainingCycleColumns = useMemo(() => splitIntoColumns(trainingCycleStats, 2), [trainingCycleStats]);
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
    const activeScorerLabel = latest?.deployed ? 'XGBoost' : 'Heuristic';
    const primaryMode = useMemo(() => signalModes.reduce((best, row) => {
        if (best == null)
            return row.mode;
        const currentBest = signalModes.find((candidate) => candidate.mode === best);
        return (currentBest?.taken || 0) >= row.taken ? best : row.mode;
    }, null), [signalModes]);
    const signalModeCards = useMemo(() => signalModes.map((row) => {
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
    const topRowBoxWidth = stacked ? '100%' : '50%';
    const confusionMatrixBox = (React.createElement(Box, { width: confusionBoxWidth, accent: clampedSelectedPanelIndex === 2 },
        React.createElement(InkBox, { width: "100%", flexDirection: "column" },
            React.createElement(InkBox, null,
                React.createElement(ConfusionMatrixCell, { label: confusionCells[0].label, value: confusionCells[0].value, width: confusionCellWidth, kind: confusionCells[0].kind, scale: confusionScale }),
                React.createElement(InkBox, { width: 1 }),
                React.createElement(ConfusionMatrixCell, { label: confusionCells[1].label, value: confusionCells[1].value, width: confusionCellWidth, kind: confusionCells[1].kind, scale: confusionScale })),
            React.createElement(InkBox, { height: 1 }),
            React.createElement(InkBox, null,
                React.createElement(ConfusionMatrixCell, { label: confusionCells[2].label, value: confusionCells[2].value, width: confusionCellWidth, kind: confusionCells[2].kind, scale: confusionScale }),
                React.createElement(InkBox, { width: 1 }),
                React.createElement(ConfusionMatrixCell, { label: confusionCells[3].label, value: confusionCells[3].value, width: confusionCellWidth, kind: confusionCells[3].kind, scale: confusionScale })))));
    const renderPageBody = () => (React.createElement(React.Fragment, null,
        React.createElement(InkBox, { flexDirection: stacked ? 'column' : 'row' },
            React.createElement(Box, { title: "Prediction Quality", width: topRowBoxWidth, accent: clampedSelectedPanelIndex === 0 },
                React.createElement(StatRow, { label: "Active path", value: latest?.deployed ? 'XGBoost' : 'Heuristic', color: latest?.deployed ? theme.green : theme.yellow }),
                React.createElement(StatRow, { label: "Trained", value: latest ? formatShortDateTime(latest.trained_at) : '-' }),
                React.createElement(StatRow, { label: "Model age", value: latest ? secondsAgo(latest.trained_at) : '-' }),
                React.createElement(StatRow, { label: "Samples", value: formatCount(latest?.n_samples) }),
                React.createElement(StatRow, { label: "Features", value: featureCount != null ? formatCount(featureCount) : '-' }),
                React.createElement(StatRow, { label: "Brier score", value: formatNumber(latest?.brier_score, 4), color: lowerIsBetterColor(latest?.brier_score, 0.18, 0.25) }),
                React.createElement(StatRow, { label: "Log loss", value: formatNumber(latest?.log_loss, 4), color: lowerIsBetterColor(latest?.log_loss, 0.55, 0.69) })),
            !stacked ? React.createElement(InkBox, { width: 1 }) : React.createElement(InkBox, { height: 1 }),
            React.createElement(Box, { title: "Tracker Health", width: topRowBoxWidth, accent: clampedSelectedPanelIndex === 1 },
                React.createElement(InkBox, { width: "100%" }, trackerHealthColumns.map((column, columnIndex) => (React.createElement(React.Fragment, { key: `tracker-health-column-${columnIndex}` },
                    React.createElement(InkBox, { flexDirection: "column", flexGrow: 1 }, column.map((item) => (React.createElement(StatRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white })))),
                    columnIndex < trackerHealthColumns.length - 1 ? React.createElement(InkBox, { width: 2 }) : null)))))),
        React.createElement(InkBox, { marginTop: 1, flexDirection: secondaryThreeAcross ? 'row' : 'column', width: "100%" },
            secondaryThreeAcross ? confusionMatrixBox : React.createElement(InkBox, { width: "100%", justifyContent: "center" }, confusionMatrixBox),
            secondaryThreeAcross ? React.createElement(InkBox, { width: secondaryRowGap }) : React.createElement(InkBox, { height: 1 }),
            React.createElement(InkBox, { width: secondaryThreeAcross ? 0 : '100%', flexGrow: 1 },
                React.createElement(Box, { title: "Confidence + Modes", width: "100%", accent: clampedSelectedPanelIndex === 3 },
                    React.createElement(InkBox, { width: "100%", flexDirection: combinedPanelsWide ? 'row' : 'column' },
                        React.createElement(InkBox, { flexDirection: "column", width: combinedPanelsWide ? confidenceSectionContentWidth : '100%', flexGrow: combinedPanelsWide ? 1 : 0 },
                            React.createElement(Text, { color: theme.accent, bold: true }, "Calibration Read"),
                            React.createElement(InkBox, { width: "100%" }, confidenceCheckColumns.map((column, columnIndex) => (React.createElement(React.Fragment, { key: `confidence-column-${columnIndex}` },
                                React.createElement(InkBox, { flexDirection: "column", flexGrow: 1 }, column.map((item) => (React.createElement(StatRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white })))),
                                columnIndex < confidenceCheckColumns.length - 1 ? React.createElement(InkBox, { width: 2 }) : null))))),
                        combinedPanelsWide ? React.createElement(InkBox, { width: combinedSectionGap }) : React.createElement(InkBox, { height: 1 }),
                        React.createElement(InkBox, { flexDirection: "column", width: combinedPanelsWide ? signalModesSectionContentWidth : '100%', flexGrow: combinedPanelsWide ? 1 : 0 },
                            React.createElement(Text, { color: theme.accent, bold: true }, "Decision Paths"),
                            React.createElement(StatRow, { label: "Active scorer", value: activeScorerLabel, color: latest?.deployed ? theme.green : theme.yellow }),
                            React.createElement(StatRow, { label: "Primary path", value: primaryMode ? modeLabel(primaryMode) : '-', color: primaryMode ? theme.accent : theme.dim }),
                            signalModeCards.length ? (React.createElement(InkBox, { width: "100%" }, signalModeCardColumns.map((column, columnIndex) => (React.createElement(React.Fragment, { key: `signal-mode-column-${columnIndex}` },
                                React.createElement(InkBox, { flexDirection: "column", flexGrow: 1 }, column.map((card, rowIndex) => {
                                    return (React.createElement(InkBox, { key: card.title, flexDirection: "column" },
                                        React.createElement(Text, { color: card.titleColor ?? theme.white, bold: true }, card.title),
                                        card.rows.map((item) => (React.createElement(StatRow, { key: `${card.title}-${item.label}`, label: item.label, value: item.value, color: item.color ?? theme.white }))),
                                        rowIndex < column.length - 1 ? React.createElement(InkBox, { height: 1 }) : null));
                                })),
                                columnIndex < signalModeCardColumns.length - 1 ? React.createElement(InkBox, { width: 2 }) : null))))) : (React.createElement(Text, { color: theme.dim }, "No tracker signals yet."))))))),
        React.createElement(InkBox, { marginTop: 1, flexDirection: stacked ? 'column' : 'row' },
            React.createElement(Box, { title: "How It Works", width: stacked ? '100%' : '50%', accent: clampedSelectedPanelIndex === 4 },
                React.createElement(InkBox, { width: "100%" },
                    React.createElement(InkBox, { flexDirection: "column", flexGrow: 1 },
                        React.createElement(Text, { color: theme.accent, bold: true }, "Score Build"),
                        scoringMixColumns[0]?.map((item) => (React.createElement(StatRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white })))),
                    React.createElement(InkBox, { width: 2 }),
                    React.createElement(InkBox, { flexDirection: "column", flexGrow: 1 },
                        React.createElement(Text, { color: theme.accent, bold: true }, "History Nudge"),
                        scoringMixColumns[1]?.map((item) => (React.createElement(StatRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white })))))),
            !stacked ? React.createElement(InkBox, { width: 1 }) : React.createElement(InkBox, { height: 1 }),
            React.createElement(Box, { title: "Training Cycle", width: stacked ? '100%' : '50%', accent: clampedSelectedPanelIndex === 5 },
                React.createElement(InkBox, { width: "100%" }, trainingCycleColumns.map((column, columnIndex) => (React.createElement(React.Fragment, { key: `training-cycle-column-${columnIndex}` },
                    React.createElement(InkBox, { flexDirection: "column", flexGrow: 1 }, column.map((item) => (React.createElement(StatRow, { key: item.label, label: item.label, value: item.value, color: item.color ?? theme.white })))),
                    columnIndex < trainingCycleColumns.length - 1 ? React.createElement(InkBox, { width: 2 }) : null)))),
                React.createElement(InkBox, { width: "100%", marginTop: 1 },
                    React.createElement(Text, { color: theme.dim }, fit('TIME', retrainWidths.timeWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('SAMPLES', retrainWidths.sampleWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('BRIER', retrainWidths.brierWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('LL', retrainWidths.lossWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: theme.dim }, fitRight('STATE', retrainWidths.stateWidth))),
                retrainRuns.length ? (retrainRuns.slice(0, historyLimit).map((row, index) => (React.createElement(InkBox, { key: `${row.finished_at}-${row.status || 'run'}-${index}`, width: "100%" },
                    React.createElement(Text, { color: theme.white }, fit(formatShortDateTime(row.finished_at), retrainWidths.timeWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: theme.white }, fitRight(formatCount(row.sample_count), retrainWidths.sampleWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: row.brier_score == null ? theme.dim : lowerIsBetterColor(row.brier_score, 0.18, 0.25) }, fitRight(formatNumber(row.brier_score, 4), retrainWidths.brierWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: row.log_loss == null ? theme.dim : lowerIsBetterColor(row.log_loss, 0.55, 0.69) }, fitRight(formatNumber(row.log_loss, 4), retrainWidths.lossWidth)),
                    React.createElement(Text, null, " "),
                    React.createElement(Text, { color: retrainRunStateColor(row.status, row.deployed) }, fitRight(retrainRunStateLabel(row.status, row.deployed), retrainWidths.stateWidth)))))) : (React.createElement(Text, { color: theme.dim }, "No retrain attempts logged yet."))))));
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
