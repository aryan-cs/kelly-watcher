import {postApiJson} from './api.js'

export const editablePositionStatuses = ['open', 'waiting', 'win', 'lose', 'exit'] as const
export type PositionManualEditStatus = (typeof editablePositionStatuses)[number]

export interface TradeLogManualEditRow {
  trade_log_id: number
  entry_price: number | null
  shares: number | null
  size_usd: number | null
  status: string | null
  updated_at: number
}

export interface PositionManualEditRow {
  market_id: string
  token_id: string
  side: string
  real_money: number
  entry_price: number | null
  shares: number | null
  size_usd: number | null
  status: string | null
  updated_at: number
}

export interface SavePositionManualEditInput {
  sourceKind: 'trade_log' | 'position'
  sourceTradeLogId: number | null
  marketId: string
  tokenId: string
  side: string
  realMoney: number
  entryPrice: number
  shares: number
  sizeUsd: number
  status: PositionManualEditStatus
}

interface SavePositionManualEditResult {
  ok?: boolean
  message?: string
}

export async function savePositionManualEdit(input: SavePositionManualEditInput): Promise<void> {
  const response = await postApiJson<SavePositionManualEditResult>('/api/positions/manual-edit', input)
  if (response && response.ok === false) {
    throw new Error(String(response.message || 'Manual position edit failed.'))
  }
}
