import React from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {StatRow} from '../components/StatRow.js'
import {formatNumber} from '../format.js'
import {stackPanels} from '../responsive.js'
import {useTerminalSize} from '../terminal.js'
import {theme} from '../theme.js'
import {useQuery} from '../useDb.js'

interface ModelRow {
  trained_at: number
  n_samples: number
  brier_score: number
  log_loss: number
  deployed: number
}

const MODEL_SQL = `
SELECT trained_at, n_samples, brier_score, log_loss, deployed
FROM model_history
ORDER BY trained_at DESC
LIMIT 5
`

const FEATURE_SQL = `
SELECT
  AVG(f_trader_win_rate) AS trader_win_rate,
  AVG(f_conviction_ratio) AS conviction_ratio,
  AVG(f_consistency) AS consistency,
  AVG(f_days_to_res) AS days_to_res,
  AVG(f_spread_pct) AS spread_pct,
  AVG(f_momentum_1h) AS momentum_1h
FROM trade_log
WHERE skipped=0
`

interface FeatureRow {
  trader_win_rate: number | null
  conviction_ratio: number | null
  consistency: number | null
  days_to_res: number | null
  spread_pct: number | null
  momentum_1h: number | null
}

export function Models() {
  const terminal = useTerminalSize()
  const stacked = stackPanels(terminal.width)
  const models = useQuery<ModelRow>(MODEL_SQL)
  const featureRows = useQuery<FeatureRow>(FEATURE_SQL)
  const latest = models[0]
  const features = featureRows[0]
  const heuristicRole = latest?.deployed ? 'Fallback path' : 'Active path'

  return (
    <InkBox flexDirection="column" width="100%">
      <InkBox flexDirection={stacked ? 'column' : 'row'}>
        <Box title="Model Status" width={stacked ? '100%' : '50%'}>
          <StatRow label="Active model" value={latest ? (latest.deployed ? 'XGBoost' : 'History only') : 'Heuristic only'} color={latest?.deployed ? theme.green : theme.dim} />
          <StatRow label="Samples" value={latest ? String(latest.n_samples) : '0'} />
          <StatRow label="Brier" value={formatNumber(latest?.brier_score)} />
          <StatRow label="Log loss" value={formatNumber(latest?.log_loss)} />
        </Box>
        {!stacked ? <InkBox width={1} /> : <InkBox height={1} />}
        <Box title="Average Logged Features" width={stacked ? '100%' : '50%'}>
          <StatRow label="Trader win rate" value={formatNumber(features?.trader_win_rate)} />
          <StatRow label="Conviction ratio" value={formatNumber(features?.conviction_ratio)} />
          <StatRow label="Consistency" value={formatNumber(features?.consistency)} />
          <StatRow label="Days to resolution" value={formatNumber(features?.days_to_res)} />
          <StatRow label="Spread %" value={formatNumber(features?.spread_pct)} />
          <StatRow label="Momentum 1h" value={formatNumber(features?.momentum_1h)} />
        </Box>
      </InkBox>

      <InkBox marginTop={1}>
        <Box title="Heuristic Scorer">
          <Text color={theme.white}>{heuristicRole}: geometric blend of trader and market scores.</Text>
          <Text color={theme.dim}>Trader score: 60% of the heuristic confidence.</Text>
          <Text color={theme.dim}>Uses win rate, consistency, account age, conviction, and diversity.</Text>
          <Text color={theme.dim}>Market score: 40% of the heuristic confidence.</Text>
          <Text color={theme.dim}>Uses spread, depth, time to resolution, momentum, volume, OI concentration, and resolution distance.</Text>
          <Text color={theme.dim}>Belief priors then nudge the score using resolved-history buckets, with a capped blend.</Text>
          <Text color={theme.dim}>A signal passes only if adjusted confidence clears the configured minimum threshold.</Text>
          <Text color={theme.dim}>Hard vetoes still reject impossible cases like broken books, no visible depth, or too little time left to execute.</Text>
        </Box>
      </InkBox>

      <InkBox marginTop={1}>
        <Box title="Retrain History">
          {models.length ? (
            models.map((row) => (
              <InkBox key={row.trained_at} justifyContent="space-between">
                <Text>{new Date(row.trained_at * 1000).toLocaleString()}</Text>
                <Text>n={row.n_samples}</Text>
                <Text>Brier {formatNumber(row.brier_score)}</Text>
                <Text>LL {formatNumber(row.log_loss)}</Text>
                <Text color={row.deployed ? theme.green : theme.dim}>{row.deployed ? 'deployed' : 'old'}</Text>
              </InkBox>
            ))
          ) : (
            <Text color={theme.dim}>No trained models yet. The heuristic scorer is active.</Text>
          )}
        </Box>
      </InkBox>
    </InkBox>
  )
}
