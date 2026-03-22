import {postApiJson} from './api.js'

export type ManualTradeAction = 'buy_more' | 'cash_out'

export interface ManualTradeRequestInput {
  action: ManualTradeAction
  marketId: string
  tokenId: string
  side: string
  question?: string
  traderAddress?: string | null
  amountUsd?: number | null
}

export interface ManualTradeRequestResult {
  ok: boolean
  message: string
}

export async function requestManualTrade(input: ManualTradeRequestInput): Promise<ManualTradeRequestResult> {
  return postApiJson<ManualTradeRequestResult>('/api/manual-trade', input)
}
