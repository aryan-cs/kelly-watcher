import type {LiveEvent} from './api'

export interface DashboardPage {
  id: string
  label: string
}

export interface DashboardModel {
  mode: 'mock' | 'api'
  pages: DashboardPage[]
  trackerEvents: LiveEvent[]
  signalEvents: LiveEvent[]
}

const pages: DashboardPage[] = [
  {id: 'tracker', label: 'Tracker'},
  {id: 'signals', label: 'Signals'},
  {id: 'perf', label: 'Perf'},
  {id: 'models', label: 'Models'},
  {id: 'wallets', label: 'Wallets'},
  {id: 'config', label: 'Config'}
]

const rawMode = String(import.meta.env.VITE_DASHBOARD_DATA_MODE || '').trim().toLowerCase()

export const dashboardDataMode: 'mock' | 'api' =
  rawMode === 'api' ? 'api' : import.meta.env.DEV ? 'mock' : 'api'

const nowSeconds = Math.floor(Date.now() / 1000)

const trackerEvents: LiveEvent[] = [
  {
    type: 'incoming',
    trade_id: 'trade-1001',
    market_id: 'market-fed-cut-1',
    question: 'Will the Fed cut rates before September?',
    market_url: 'https://polymarket.com/event/fed-cut-before-september',
    side: 'YES',
    action: 'buy',
    price: 0.612,
    shares: 148.25,
    amount_usd: 90.74,
    size_usd: 148.25,
    username: 'macro_maven',
    trader: '0xmacro000000000000000000000000000001',
    ts: nowSeconds - 24
  },
  {
    type: 'incoming',
    trade_id: 'trade-1002',
    market_id: 'market-ai-bill-1',
    question: 'Will the Senate pass the AI safety bill this quarter?',
    market_url: 'https://polymarket.com/event/ai-safety-bill-q2',
    side: 'NO',
    action: 'buy',
    price: 0.438,
    shares: 205.4,
    amount_usd: 89.97,
    size_usd: 205.4,
    username: 'policyflow',
    trader: '0xpolicy0000000000000000000000000002',
    ts: nowSeconds - 67
  },
  {
    type: 'incoming',
    trade_id: 'trade-1003',
    market_id: 'market-eth-etf-1',
    question: 'Will spot ETH ETF volume exceed $1B on launch week?',
    market_url: 'https://polymarket.com/event/eth-etf-volume-launch-week',
    side: 'YES',
    action: 'buy',
    price: 0.731,
    shares: 126.9,
    amount_usd: 92.77,
    size_usd: 126.9,
    username: 'etf_tape',
    trader: '0xetf00000000000000000000000000000003',
    ts: nowSeconds - 103
  },
  {
    type: 'incoming',
    trade_id: 'trade-1004',
    market_id: 'market-election-1',
    question: 'Will turnout exceed 64% in the general election?',
    market_url: 'https://polymarket.com/event/election-turnout-over-64',
    side: 'NO',
    action: 'buy',
    price: 0.284,
    shares: 318.7,
    amount_usd: 90.51,
    size_usd: 318.7,
    username: 'civicsbook',
    trader: '0xcivics00000000000000000000000000004',
    ts: nowSeconds - 149
  },
  {
    type: 'incoming',
    trade_id: 'trade-1005',
    market_id: 'market-oil-1',
    question: 'Will Brent crude close above $95 this month?',
    market_url: 'https://polymarket.com/event/brent-above-95-this-month',
    side: 'YES',
    action: 'buy',
    price: 0.557,
    shares: 161.0,
    amount_usd: 89.68,
    size_usd: 161.0,
    username: 'energy_tape',
    trader: '0xenergy0000000000000000000000000005',
    ts: nowSeconds - 198
  },
  {
    type: 'incoming',
    trade_id: 'trade-1006',
    market_id: 'market-cpi-1',
    question: 'Will core CPI print under 3.2% next release?',
    market_url: 'https://polymarket.com/event/core-cpi-under-32',
    side: 'NO',
    action: 'buy',
    price: 0.649,
    shares: 138.6,
    amount_usd: 89.95,
    size_usd: 138.6,
    username: 'ratesdesk',
    trader: '0xrates000000000000000000000000000006',
    ts: nowSeconds - 244
  }
]

const signalEvents: LiveEvent[] = [
  {
    type: 'signal',
    trade_id: 'trade-1001',
    market_id: 'market-fed-cut-1',
    question: 'Will the Fed cut rates before September?',
    market_url: 'https://polymarket.com/event/fed-cut-before-september',
    side: 'YES',
    price: 0.612,
    shares: 148.25,
    amount_usd: 90.74,
    size_usd: 90.74,
    username: 'macro_maven',
    trader: '0xmacro000000000000000000000000000001',
    decision: 'ACCEPT',
    confidence: 0.684,
    reason: 'passed all checks',
    ts: nowSeconds - 22
  },
  {
    type: 'signal',
    trade_id: 'trade-1002',
    market_id: 'market-ai-bill-1',
    question: 'Will the Senate pass the AI safety bill this quarter?',
    market_url: 'https://polymarket.com/event/ai-safety-bill-q2',
    side: 'NO',
    price: 0.438,
    shares: 205.4,
    amount_usd: 89.97,
    size_usd: 89.97,
    username: 'policyflow',
    trader: '0xpolicy0000000000000000000000000002',
    decision: 'REJECT',
    confidence: 0.471,
    reason: 'model edge 0.012 < threshold 0.025',
    ts: nowSeconds - 65
  },
  {
    type: 'signal',
    trade_id: 'trade-1003',
    market_id: 'market-eth-etf-1',
    question: 'Will spot ETH ETF volume exceed $1B on launch week?',
    market_url: 'https://polymarket.com/event/eth-etf-volume-launch-week',
    side: 'YES',
    action: 'buy',
    price: 0.731,
    shares: 126.9,
    amount_usd: 92.77,
    size_usd: 92.77,
    username: 'etf_tape',
    trader: '0xetf00000000000000000000000000000003',
    decision: 'PAUSE',
    confidence: 0.593,
    reason: 'market veto: expires in <900s',
    ts: nowSeconds - 101
  },
  {
    type: 'signal',
    trade_id: 'trade-1004',
    market_id: 'market-election-1',
    question: 'Will turnout exceed 64% in the general election?',
    market_url: 'https://polymarket.com/event/election-turnout-over-64',
    side: 'NO',
    price: 0.284,
    shares: 318.7,
    amount_usd: 90.51,
    size_usd: 90.51,
    username: 'civicsbook',
    trader: '0xcivics00000000000000000000000000004',
    decision: 'ACCEPT',
    confidence: 0.741,
    reason: 'passed model edge threshold',
    ts: nowSeconds - 147
  },
  {
    type: 'signal',
    trade_id: 'trade-1005',
    market_id: 'market-oil-1',
    question: 'Will Brent crude close above $95 this month?',
    market_url: 'https://polymarket.com/event/brent-above-95-this-month',
    side: 'YES',
    price: 0.557,
    shares: 161.0,
    amount_usd: 89.68,
    size_usd: 89.68,
    username: 'energy_tape',
    trader: '0xenergy0000000000000000000000000005',
    decision: 'SKIP',
    confidence: 0.521,
    reason: 'order in-flight',
    ts: nowSeconds - 196
  },
  {
    type: 'signal',
    trade_id: 'trade-1006',
    market_id: 'market-cpi-1',
    question: 'Will core CPI print under 3.2% next release?',
    market_url: 'https://polymarket.com/event/core-cpi-under-32',
    side: 'NO',
    price: 0.649,
    shares: 138.6,
    amount_usd: 89.95,
    size_usd: 89.95,
    username: 'ratesdesk',
    trader: '0xrates000000000000000000000000000006',
    decision: 'IGNORE',
    confidence: 0.503,
    reason: 'duplicate trade_id',
    ts: nowSeconds - 242
  }
]

export const dashboardModel: DashboardModel = {
  mode: dashboardDataMode,
  pages,
  trackerEvents,
  signalEvents
}
