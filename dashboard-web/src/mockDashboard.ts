import type {
  BotState,
  ConfigSnapshot,
  DiscoveryCandidatesResponse,
  LiveEvent,
  ModelTrainingRun,
  ManagedWalletsResponse
} from './api'
import {editableConfigFields} from './configFields'

export interface DashboardPage {
  id: string
  label: string
}

export interface DashboardModel {
  mode: 'mock' | 'api'
  pages: DashboardPage[]
  trackerEvents: LiveEvent[]
  signalEvents: LiveEvent[]
  botState: BotState
  configSnapshot: ConfigSnapshot
  managedWallets: ManagedWalletsResponse
  discoveryCandidates: DiscoveryCandidatesResponse
}

const pages: DashboardPage[] = [
  {id: 'tracker', label: '[1] TRACKER'},
  {id: 'signals', label: '[2] SIGNALS'},
  {id: 'perf', label: '[3] PERFORMANCE'},
  {id: 'models', label: '[4] MODEL'},
  {id: 'wallets', label: '[5] WALLETS'},
  {id: 'config', label: '[6] CONFIG'}
]

const rawMode = String(import.meta.env.VITE_DASHBOARD_DATA_MODE || '').trim().toLowerCase()

export const dashboardDataMode: 'mock' | 'api' =
  rawMode === 'api' ? 'api' : import.meta.env.DEV ? 'mock' : 'api'

const nowSeconds = Math.floor(Date.now() / 1000)

const MOCK_EVENT_COUNT = 200
const mockQuestions = [
  'Will the Fed cut rates before September?',
  'Will the Senate pass the AI safety bill this quarter?',
  'Will spot ETH ETF volume exceed $1B on launch week?',
  'Will turnout exceed 64% in the general election?',
  'Will Brent crude close above $95 this month?',
  'Will core CPI print under 3.2% next release?',
  'Will the SEC approve another crypto ETF this quarter?',
  'Will the unemployment rate stay below 4.3% next month?',
  'Will gold close above $2,500 this quarter?',
  'Will OpenAI release a GPT-6 preview this year?'
]
const mockUsers = [
  'macro_maven',
  'policyflow',
  'etf_tape',
  'civicsbook',
  'energy_tape',
  'ratesdesk',
  'volwatch',
  'headlineedge',
  'macro_alpha',
  'eventbeta'
]
const mockReasons = [
  'passed all checks',
  'passed model edge threshold',
  'order in-flight',
  'duplicate trade_id',
  'market veto: expires in <900s',
  'model edge 0.012 < threshold 0.025',
  'position already open',
  'conf 0.54 < min 0.58'
]
const mockDecisions = ['ACCEPT', 'REJECT', 'PAUSE', 'SKIP', 'IGNORE'] as const

function buildMockMarketSlug(question: string, index: number): string {
  return question
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .concat(`-${index + 1}`)
}

function buildMockTrader(index: number): string {
  return `0x${(index + 1).toString(16).padStart(40, '0')}`
}

function buildMockIncomingEvent(index: number): LiveEvent {
  const question = mockQuestions[index % mockQuestions.length]
  const username = mockUsers[index % mockUsers.length]
  const side = index % 2 === 0 ? 'YES' : 'NO'
  const price = 0.18 + ((index * 37) % 64) / 100
  const roundedPrice = Number(Math.min(0.89, price).toFixed(3))
  const shares = Number((82 + (index % 14) * 11.75 + ((index * 3) % 9)).toFixed(2))
  const amountUsd = Number((roundedPrice * shares).toFixed(2))
  const slug = buildMockMarketSlug(question, index)

  return {
    type: 'incoming',
    trade_id: `trade-${1001 + index}`,
    market_id: `market-${slug}`,
    question,
    market_url: `https://polymarket.com/event/${slug}`,
    side,
    action: 'buy',
    price: roundedPrice,
    shares,
    amount_usd: amountUsd,
    size_usd: shares,
    username,
    trader: buildMockTrader(index),
    ts: nowSeconds - (index * 41 + 24)
  }
}

function buildMockSignalEvent(event: LiveEvent, index: number): LiveEvent {
  return {
    type: 'signal',
    trade_id: event.trade_id,
    market_id: event.market_id,
    question: event.question,
    market_url: event.market_url,
    side: event.side,
    action: index % 5 === 0 ? 'buy' : undefined,
    price: event.price,
    shares: event.shares,
    amount_usd: event.amount_usd,
    size_usd: event.amount_usd ?? event.size_usd,
    username: event.username,
    trader: event.trader,
    decision: mockDecisions[index % mockDecisions.length],
    confidence: Number((0.42 + ((index * 11) % 43) / 100).toFixed(3)),
    reason: mockReasons[index % mockReasons.length],
    ts: event.ts + 2
  }
}

const trackerEvents: LiveEvent[] = Array.from({length: MOCK_EVENT_COUNT}, (_, index) =>
  buildMockIncomingEvent(index)
)

const signalEvents: LiveEvent[] = trackerEvents.map((event, index) =>
  buildMockSignalEvent(event, index)
)

const trainingRuns: ModelTrainingRun[] = Array.from({length: 12}, (_, index) => {
  const startedAt = nowSeconds - (index + 1) * 21600
  const finishedAt = startedAt + 96 + (index % 4) * 18
  const logLoss = Number((0.918 - index * 0.027 + (index % 3) * 0.004).toFixed(3))
  const brier = Number((0.364 - index * 0.011 + (index % 2) * 0.003).toFixed(3))
  const deployed = index === 0 || index === 3 || index === 7
  const status = deployed ? 'deployed' : index % 5 === 0 ? 'rejected' : 'completed'

  return {
    run_id: `xgb-${String(index + 1).padStart(3, '0')}`,
    started_at: startedAt,
    finished_at: finishedAt,
    scorer: 'xgboost',
    backend: 'xgboost',
    log_loss: logLoss,
    brier,
    deployed,
    deployed_at: deployed ? finishedAt + 12 : undefined,
    status,
    note:
      status === 'deployed'
        ? 'promoted after holdout improvement'
        : status === 'rejected'
          ? 'challenger underperformed baseline'
          : 'completed and archived'
  }
})

const botState: BotState = {
  mode: 'shadow',
  configured_mode: 'shadow',
  mode_block_reason: '',
  session_id: 'mock-session',
  loop_in_progress: false,
  started_at: nowSeconds - 7200,
  last_loop_started_at: nowSeconds - 4,
  last_activity_at: nowSeconds - 2,
  n_wallets: 18,
  poll_interval: 5,
  last_poll_at: nowSeconds - 3,
  last_poll_duration_s: 0.84,
  bankroll_usd: 1250.5,
  last_event_count: trackerEvents.length,
  resolved_shadow_trade_count: 142,
  live_require_shadow_history_enabled: true,
  live_shadow_history_ready: false,
  live_shadow_history_total_ready: false,
  shadow_snapshot_state_known: true,
  shadow_snapshot_scope: 'post-promotion',
  shadow_snapshot_status: 'building',
  shadow_snapshot_resolved: 42,
  shadow_snapshot_routed_resolved: 31,
  shadow_snapshot_ready: false,
  shadow_snapshot_block_reason: 'need 18 more routed shadow trades before live gate clears',
  loaded_scorer: 'xgboost',
  loaded_model_backend: 'xgboost',
  model_runtime_compatible: true,
  model_prediction_mode: 'model',
  model_loaded_at: nowSeconds - 5400,
  retrain_in_progress: false,
  last_retrain_started_at: nowSeconds - 86400,
  last_retrain_finished_at: nowSeconds - 86340,
  last_retrain_status: 'success',
  last_retrain_message: 'deployed challenger after holdout improvement',
  last_replay_search_started_at: nowSeconds - 4200,
  last_replay_search_finished_at: nowSeconds - 4120,
  last_replay_search_status: 'success',
  last_replay_search_message: 'best feasible candidate improved score by 0.18',
  training_runs: trainingRuns,
  startup_recovery_only: false,
  startup_block_reason: '',
  db_integrity_known: true,
  db_integrity_ok: true,
  db_integrity_message: 'ok',
  shadow_restart_pending: false,
  shadow_restart_kind: '',
  shadow_restart_message: '',
  db_recovery_candidate_ready: true,
  db_recovery_candidate_mode: 'evidence_ready',
  db_recovery_candidate_message: 'latest verified backup is available',
  db_recovery_candidate_class_reason:
    'verified backup is recoverable and passes the current shadow evidence gate',
  trade_log_archive_enabled: true,
  trade_log_archive_state_known: true,
  trade_log_archive_status: 'idle',
  trade_log_archive_pending: false,
  trade_log_archive_request_message: '',
  trade_log_archive_active_row_count: 4821,
  trade_log_archive_archive_row_count: 9012,
  trade_log_archive_eligible_row_count: 212,
  trade_log_archive_cutoff_ts: nowSeconds - 14 * 86400,
  trade_log_archive_preserve_since_ts: nowSeconds - 3 * 86400,
  trade_log_archive_block_reason: '',
  storage_state_known: true,
  storage_trading_db_size_bytes: 9_120_000,
  storage_trade_log_archive_db_size_bytes: 17_440_000,
  storage_save_dir_size_bytes: 42_300_000,
  storage_log_dir_size_bytes: 8_400_000,
  startup_detail: 'polling cleanly in shadow mode',
  startup_failed: false,
  startup_validation_failed: false
}

const configSnapshot: ConfigSnapshot = {
  ok: true,
  message: 'mock config loaded',
  watched_wallets: [
    '0xmacro000000000000000000000000000001',
    '0xpolicy0000000000000000000000000002',
    '0xetf00000000000000000000000000000003',
    '0xcivics00000000000000000000000000004'
  ],
  live_wallets: [],
  live_wallet_count: 0,
  wallet_registry_source: 'sqlite',
  safe_values: {
    POLL_INTERVAL_SECONDS: '5',
    MIN_CONFIDENCE: '0.58',
    SHADOW_BANKROLL_USD: '1250',
    MAX_BET_FRACTION: '0.035',
    MAX_MARKET_EXPOSURE_FRACTION: '0.12',
    MODEL_PATH: 'save/models/current.json'
  },
  rows: editableConfigFields.map((field) => ({
    key: field.key,
    value:
      (
        {
          POLL_INTERVAL_SECONDS: '5',
          MIN_CONFIDENCE: '0.58',
          SHADOW_BANKROLL_USD: '1250',
          MAX_BET_FRACTION: '0.035',
          MAX_MARKET_EXPOSURE_FRACTION: '0.12',
          MODEL_PATH: 'save/models/current.json',
          LOG_LEVEL: 'INFO',
          RETRAIN_BASE_CADENCE: 'daily',
          RETRAIN_HOUR_LOCAL: '3',
          LIVE_MIN_SHADOW_RESOLVED: '100'
        } as Record<string, string>
      )[field.key] ?? field.defaultValue,
    source:
      field.key === 'MODEL_PATH' || field.key === 'LOG_LEVEL'
        ? 'env'
        : 'db'
  }))
}

const seedManagedWallets = [
    {
      wallet_address: '0xmacro000000000000000000000000000001',
      username: 'macro_maven',
      status: 'active',
      status_reason: 'tracked',
      trust_tier: 'trusted',
      trust_size_multiplier: 1.14,
      trust_note: 'strong local copied pnl',
      wallet_family: 'core',
      wallet_family_multiplier: 1,
      tracking_started_at: nowSeconds - 1_209_600,
      updated_at: nowSeconds - 3_600,
      post_promotion_evidence_ready: true,
      post_promotion_resolved_copied_count: 24,
      post_promotion_resolved_copied_win_rate: 0.67,
      post_promotion_uncopyable_skip_rate: 0.06,
      post_promotion_resolved_copied_total_pnl_usd: 182.4,
      discovery_score: 0.82
    },
    {
      wallet_address: '0xpolicy0000000000000000000000000002',
      username: 'policyflow',
      status: 'active',
      status_reason: 'tracked',
      trust_tier: 'probation',
      trust_size_multiplier: 0.82,
      trust_note: 'drag-prone recent fills',
      wallet_family: 'timing_sensitive',
      wallet_family_multiplier: 0.95,
      tracking_started_at: nowSeconds - 604_800,
      updated_at: nowSeconds - 7_200,
      post_promotion_evidence_ready: false,
      post_promotion_evidence_note: 'awaiting proof',
      post_promotion_resolved_copied_count: 8,
      post_promotion_resolved_copied_win_rate: 0.38,
      post_promotion_uncopyable_skip_rate: 0.18,
      post_promotion_resolved_copied_total_pnl_usd: -16.2,
      discovery_score: 0.64
    },
    {
      wallet_address: '0xetf00000000000000000000000000000003',
      username: 'etf_tape',
      status: 'active',
      status_reason: 'tracked',
      trust_tier: 'discovery',
      trust_size_multiplier: 0.65,
      trust_note: 'still in cold start',
      wallet_family: 'promotion_proof',
      wallet_family_multiplier: 0.9,
      tracking_started_at: nowSeconds - 302_400,
      updated_at: nowSeconds - 10_800,
      post_promotion_evidence_ready: false,
      post_promotion_evidence_note: 'not enough routed outcomes yet',
      post_promotion_resolved_copied_count: 3,
      post_promotion_resolved_copied_win_rate: 0.67,
      post_promotion_uncopyable_skip_rate: 0.11,
      post_promotion_resolved_copied_total_pnl_usd: 11.8,
      discovery_score: 0.57
    },
    {
      wallet_address: '0xcivics00000000000000000000000000004',
      username: 'civicsbook',
      status: 'disabled',
      status_reason: 'local drop',
      trust_tier: 'disabled',
      trust_size_multiplier: 0.5,
      trust_note: 'uncopyable skip rate too high',
      wallet_family: 'liquidity_sensitive',
      wallet_family_multiplier: 0.88,
      tracking_started_at: nowSeconds - 1_814_400,
      disabled_at: nowSeconds - 21_600,
      updated_at: nowSeconds - 18_000,
      post_promotion_evidence_ready: false,
      post_promotion_resolved_copied_count: 12,
      post_promotion_resolved_copied_win_rate: 0.33,
      post_promotion_uncopyable_skip_rate: 0.29,
      post_promotion_resolved_copied_total_pnl_usd: -48.6,
      discovery_score: 0.41
    },
    {
      wallet_address: '0xheadline000000000000000000000000005',
      username: 'headlineedge',
      status: 'active',
      status_reason: 'tracked',
      trust_tier: 'trusted',
      trust_size_multiplier: 1.06,
      trust_note: 'stable event timing',
      wallet_family: 'scalable',
      wallet_family_multiplier: 1.02,
      tracking_started_at: nowSeconds - 950_400,
      updated_at: nowSeconds - 5_400,
      post_promotion_evidence_ready: true,
      post_promotion_resolved_copied_count: 18,
      post_promotion_resolved_copied_win_rate: 0.61,
      post_promotion_uncopyable_skip_rate: 0.08,
      post_promotion_resolved_copied_total_pnl_usd: 97.3,
      discovery_score: 0.78
    },
    {
      wallet_address: '0xvolwatch00000000000000000000000006',
      username: 'volwatch',
      status: 'disabled',
      status_reason: 'family drag',
      disabled_reason: 'post-promotion copied pnl below floor',
      trust_tier: 'disabled',
      trust_size_multiplier: 0.44,
      trust_note: 'sustained negative copied pnl',
      wallet_family: 'thin_edge',
      wallet_family_multiplier: 0.84,
      tracking_started_at: nowSeconds - 1_555_200,
      disabled_at: nowSeconds - 64_800,
      updated_at: nowSeconds - 57_600,
      post_promotion_evidence_ready: false,
      post_promotion_evidence_note: 'watch disabled after review',
      post_promotion_resolved_copied_count: 19,
      post_promotion_resolved_copied_win_rate: 0.26,
      post_promotion_uncopyable_skip_rate: 0.31,
      post_promotion_resolved_copied_total_pnl_usd: -126.4,
      discovery_score: 0.33
    },
    {
      wallet_address: '0xratesdesk0000000000000000000000007',
      username: 'ratesdesk',
      status: 'active',
      status_reason: 'tracked',
      trust_tier: 'warm',
      trust_size_multiplier: 0.93,
      trust_note: 'steady local quality',
      wallet_family: 'core',
      wallet_family_multiplier: 1,
      tracking_started_at: nowSeconds - 777_600,
      updated_at: nowSeconds - 4_800,
      post_promotion_evidence_ready: true,
      post_promotion_resolved_copied_count: 14,
      post_promotion_resolved_copied_win_rate: 0.57,
      post_promotion_uncopyable_skip_rate: 0.09,
      post_promotion_resolved_copied_total_pnl_usd: 34.8,
      discovery_score: 0.69
    },
    {
      wallet_address: '0xeventbeta0000000000000000000000008',
      username: 'eventbeta',
      status: 'disabled',
      status_reason: 'local drop',
      disabled_reason: 'timing-sensitive fills stayed uncopyable',
      trust_tier: 'disabled',
      trust_size_multiplier: 0.38,
      trust_note: 'too many late entries',
      wallet_family: 'timing_sensitive',
      wallet_family_multiplier: 0.81,
      tracking_started_at: nowSeconds - 432_000,
      disabled_at: nowSeconds - 172_800,
      updated_at: nowSeconds - 165_600,
      post_promotion_evidence_ready: false,
      post_promotion_evidence_note: 'dropped after late-buy review',
      post_promotion_resolved_copied_count: 6,
      post_promotion_resolved_copied_win_rate: 0.17,
      post_promotion_uncopyable_skip_rate: 0.42,
      post_promotion_resolved_copied_total_pnl_usd: -73.9,
      discovery_score: 0.37
    },
    {
      wallet_address: '0xmacroalpha000000000000000000000009',
      username: 'macro_alpha',
      status: 'active',
      status_reason: 'tracked',
      trust_tier: 'probation',
      trust_size_multiplier: 0.74,
      trust_note: 'newly promoted',
      wallet_family: 'promotion_proof',
      wallet_family_multiplier: 0.9,
      tracking_started_at: nowSeconds - 259_200,
      updated_at: nowSeconds - 9_000,
      post_promotion_evidence_ready: false,
      post_promotion_evidence_note: 'probation until more routed outcomes land',
      post_promotion_resolved_copied_count: 5,
      post_promotion_resolved_copied_win_rate: 0.6,
      post_promotion_uncopyable_skip_rate: 0.13,
      post_promotion_resolved_copied_total_pnl_usd: 22.7,
      discovery_score: 0.71
    }
]

const TARGET_TRACKED_WALLET_COUNT = 150
const EXTRA_DROPPED_WALLET_COUNT = 24
const seedTrackedWalletCount = seedManagedWallets.filter((wallet) => wallet.status !== 'disabled').length
const generatedTrackedWalletCount = Math.max(0, TARGET_TRACKED_WALLET_COUNT - seedTrackedWalletCount)
const generatedWalletCount = generatedTrackedWalletCount + EXTRA_DROPPED_WALLET_COUNT

const extraManagedWallets = Array.from({length: generatedWalletCount}, (_, index) => {
  const active = index < generatedTrackedWalletCount
  const username = `wallet_${String(index + 10).padStart(2, '0')}`
  const trackedAgeDays = 2 + index
  const updatedAgeHours = 2 + (index % 9)
  const copiedCount = 4 + (index % 17)
  const copyWinRate = Math.max(0.12, Math.min(0.84, 0.24 + (index % 11) * 0.053))
  const skipRate = Math.max(0.03, Math.min(0.48, 0.05 + (index % 8) * 0.045))
  const copiedPnl = Number((((index % 2 === 0 ? 1 : -1) * (18 + index * 8.7))).toFixed(1))

  return {
    wallet_address: `0xwallet${String(index + 10).padStart(34, '0')}`,
    username,
    status: active ? 'active' : 'disabled',
    status_reason: active ? 'tracked' : 'local drop',
    disabled_reason: active ? '' : 'manual review drop for scroll-state testing',
    trust_tier: active ? (index % 3 === 0 ? 'trusted' : index % 3 === 1 ? 'warm' : 'probation') : 'disabled',
    trust_size_multiplier: Number((0.46 + (index % 9) * 0.09).toFixed(2)),
    trust_note: active ? 'mock tracked wallet for viewport testing' : 'mock dropped wallet for viewport testing',
    wallet_family: index % 4 === 0 ? 'core' : index % 4 === 1 ? 'promotion_proof' : index % 4 === 2 ? 'timing_sensitive' : 'scalable',
    wallet_family_multiplier: Number((0.84 + (index % 5) * 0.05).toFixed(2)),
    tracking_started_at: nowSeconds - trackedAgeDays * 86_400,
    updated_at: nowSeconds - updatedAgeHours * 3_600,
    disabled_at: active ? undefined : nowSeconds - (6 + index) * 3_600,
    post_promotion_evidence_ready: active ? index % 3 !== 1 : false,
    post_promotion_evidence_note: active ? (index % 3 === 1 ? 'awaiting more routed outcomes' : '') : 'disabled after mock review',
    post_promotion_resolved_copied_count: copiedCount,
    post_promotion_resolved_copied_win_rate: Number(copyWinRate.toFixed(3)),
    post_promotion_uncopyable_skip_rate: Number(skipRate.toFixed(3)),
    post_promotion_resolved_copied_total_pnl_usd: copiedPnl,
    discovery_score: Number((0.28 + (index % 10) * 0.061).toFixed(3))
  }
})

const managedWalletRows = [...seedManagedWallets, ...extraManagedWallets]
const trackedManagedWalletRows = managedWalletRows.filter((wallet) => wallet.status !== 'disabled')
const trackedManagedWalletAddresses = trackedManagedWalletRows
  .map((wallet) => String(wallet.wallet_address || '').trim())
  .filter(Boolean)

const managedWallets: ManagedWalletsResponse = {
  ok: true,
  source: 'sqlite',
  count: managedWalletRows.length,
  wallets: managedWalletRows
}

botState.n_wallets = trackedManagedWalletRows.length
configSnapshot.watched_wallets = trackedManagedWalletAddresses

const discoveryCandidates: DiscoveryCandidatesResponse = {
  ok: true,
  source: 'scanner',
  count: 3,
  ready_count: 2,
  review_count: 1,
  accepted_count: 2,
  candidates: [
    {
      wallet_address: '0xalpha000000000000000000000000000001',
      username: 'macro_alpha',
      follow_score: 0.88,
      accepted: true,
      style: 'momentum',
      leaderboard_rank: 3,
      recent_buys: 11,
      median_buy_lead_hours: 14.2,
      late_buy_ratio: 0.09,
      realized_pnl_usd: 242.8,
      updated_at: nowSeconds - 900
    },
    {
      wallet_address: '0xbeta0000000000000000000000000000002',
      username: 'eventbeta',
      follow_score: 0.72,
      accepted: true,
      style: 'news',
      leaderboard_rank: 9,
      recent_buys: 8,
      median_buy_lead_hours: 7.8,
      late_buy_ratio: 0.14,
      realized_pnl_usd: 91.6,
      updated_at: nowSeconds - 1400
    },
    {
      wallet_address: '0xgamma000000000000000000000000000003',
      username: 'thinbook',
      follow_score: 0.39,
      accepted: false,
      reject_reason: 'too many liquidity-related skips',
      style: 'scalp',
      leaderboard_rank: 28,
      recent_buys: 17,
      median_buy_lead_hours: 1.4,
      late_buy_ratio: 0.42,
      realized_pnl_usd: -33.1,
      updated_at: nowSeconds - 2200
    }
  ]
}

export const dashboardModel: DashboardModel = {
  mode: dashboardDataMode,
  pages,
  trackerEvents,
  signalEvents,
  botState,
  configSnapshot,
  managedWallets,
  discoveryCandidates
}
