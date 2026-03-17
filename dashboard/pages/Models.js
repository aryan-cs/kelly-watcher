import React from 'react';
import { Box as InkBox, Text } from 'ink';
import { Box } from '../components/Box.js';
import { StatRow } from '../components/StatRow.js';
import { formatNumber } from '../format.js';
import { stackPanels } from '../responsive.js';
import { useTerminalSize } from '../terminal.js';
import { theme } from '../theme.js';
import { useQuery } from '../useDb.js';
const EXECUTED_ENTRY_WHERE = `
skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
`;
const MODEL_SQL = `
SELECT trained_at, n_samples, brier_score, log_loss, deployed
FROM model_history
ORDER BY trained_at DESC
LIMIT 5
`;
const FEATURE_SQL = `
SELECT
  AVG(f_trader_win_rate) AS trader_win_rate,
  AVG(f_conviction_ratio) AS conviction_ratio,
  AVG(f_consistency) AS consistency,
  AVG(f_days_to_res) AS days_to_res,
  AVG(f_spread_pct) AS spread_pct,
  AVG(f_momentum_1h) AS momentum_1h
FROM trade_log
WHERE ${EXECUTED_ENTRY_WHERE}
`;
export function Models() {
    const terminal = useTerminalSize();
    const stacked = stackPanels(terminal.width);
    const models = useQuery(MODEL_SQL);
    const featureRows = useQuery(FEATURE_SQL);
    const latest = models[0];
    const features = featureRows[0];
    const heuristicRole = latest?.deployed ? 'Fallback path' : 'Active path';
    return (React.createElement(InkBox, { flexDirection: "column", width: "100%" },
        React.createElement(InkBox, { flexDirection: stacked ? 'column' : 'row' },
            React.createElement(Box, { title: "Model Status", width: stacked ? '100%' : '50%' },
                React.createElement(StatRow, { label: "Active model", value: latest ? (latest.deployed ? 'XGBoost' : 'History only') : 'Heuristic only', color: latest?.deployed ? theme.green : theme.dim }),
                React.createElement(StatRow, { label: "Samples", value: latest ? String(latest.n_samples) : '0' }),
                React.createElement(StatRow, { label: "Brier", value: formatNumber(latest?.brier_score) }),
                React.createElement(StatRow, { label: "Log loss", value: formatNumber(latest?.log_loss) })),
            !stacked ? React.createElement(InkBox, { width: 1 }) : React.createElement(InkBox, { height: 1 }),
            React.createElement(Box, { title: "Average Logged Features", width: stacked ? '100%' : '50%' },
                React.createElement(StatRow, { label: "Trader win rate", value: formatNumber(features?.trader_win_rate) }),
                React.createElement(StatRow, { label: "Conviction ratio", value: formatNumber(features?.conviction_ratio) }),
                React.createElement(StatRow, { label: "Consistency", value: formatNumber(features?.consistency) }),
                React.createElement(StatRow, { label: "Days to resolution", value: formatNumber(features?.days_to_res) }),
                React.createElement(StatRow, { label: "Spread %", value: formatNumber(features?.spread_pct) }),
                React.createElement(StatRow, { label: "Momentum 1h", value: formatNumber(features?.momentum_1h) }))),
        React.createElement(InkBox, { marginTop: 1 },
            React.createElement(Box, { title: "Heuristic Scorer" },
                React.createElement(Text, { color: theme.white },
                    heuristicRole,
                    ": geometric blend of trader and market scores."),
                React.createElement(Text, { color: theme.dim }, "Trader score: 60% of the heuristic confidence."),
                React.createElement(Text, { color: theme.dim }, "Uses win rate, consistency, account age, conviction, and diversity."),
                React.createElement(Text, { color: theme.dim }, "Market score: 40% of the heuristic confidence."),
                React.createElement(Text, { color: theme.dim }, "Uses spread, depth, time to resolution, momentum, volume, OI concentration, and resolution distance."),
                React.createElement(Text, { color: theme.dim }, "Belief priors then nudge the score using resolved-history buckets, with a capped blend."),
                React.createElement(Text, { color: theme.dim }, "A signal passes only if adjusted confidence clears the configured minimum threshold."),
                React.createElement(Text, { color: theme.dim }, "Hard vetoes still reject impossible cases like broken books, no visible depth, or too little time left to execute."))),
        React.createElement(InkBox, { marginTop: 1 },
            React.createElement(Box, { title: "Retrain History" }, models.length ? (models.map((row) => (React.createElement(InkBox, { key: row.trained_at, justifyContent: "space-between" },
                React.createElement(Text, null, new Date(row.trained_at * 1000).toLocaleString()),
                React.createElement(Text, null,
                    "n=",
                    row.n_samples),
                React.createElement(Text, null,
                    "Brier ",
                    formatNumber(row.brier_score)),
                React.createElement(Text, null,
                    "LL ",
                    formatNumber(row.log_loss)),
                React.createElement(Text, { color: row.deployed ? theme.green : theme.dim }, row.deployed ? 'deployed' : 'old'))))) : (React.createElement(Text, { color: theme.dim }, "No trained models yet. The heuristic scorer is active."))))));
}
