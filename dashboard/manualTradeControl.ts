import fs from 'fs'
import path from 'path'
import {botStatePath, manualTradeRequestPath} from './paths.js'

interface BotStateSnapshot {
  started_at?: number
  last_activity_at?: number
  poll_interval?: number
}

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

interface ManualTradeRequestPayload {
  action: ManualTradeAction
  source: 'dashboard'
  request_id: string
  requested_at: number
  market_id: string
  token_id: string
  side: string
  question?: string
  trader_address?: string
  amount_usd?: number
}

export interface ManualTradeRequestResult {
  ok: boolean
  message: string
}

function readBotStateSnapshot(): BotStateSnapshot {
  try {
    return JSON.parse(fs.readFileSync(botStatePath, 'utf8')) as BotStateSnapshot
  } catch {
    return {}
  }
}

function requestIsRecent(filePath: string, maxAgeSeconds: number): boolean {
  try {
    const ageSeconds = (Date.now() - fs.statSync(filePath).mtimeMs) / 1000
    return ageSeconds <= maxAgeSeconds
  } catch {
    return false
  }
}

function normalizeAction(rawAction: string): ManualTradeAction | null {
  const normalized = String(rawAction || '').trim().toLowerCase()
  if (normalized === 'buy_more') return 'buy_more'
  if (normalized === 'cash_out') return 'cash_out'
  return null
}

export function requestManualTrade(input: ManualTradeRequestInput): ManualTradeRequestResult {
  const action = normalizeAction(input.action)
  const marketId = String(input.marketId || '').trim()
  const tokenId = String(input.tokenId || '').trim()
  const side = String(input.side || '').trim().toLowerCase()
  const question = String(input.question || '').trim()
  const traderAddress = String(input.traderAddress || '').trim().toLowerCase()
  const amountUsd = input.amountUsd != null ? Number(input.amountUsd) : null
  const botState = readBotStateSnapshot()
  const now = Math.floor(Date.now() / 1000)
  const startedAt = Number(botState.started_at || 0)
  const lastActivityAt = Number(botState.last_activity_at || 0)
  const heartbeatWindow = Math.max(Number(botState.poll_interval || 1) * 3, 30)

  if (!action) {
    return {
      ok: false,
      message: 'Manual trade request is missing a supported action.'
    }
  }
  if (!marketId) {
    return {
      ok: false,
      message: 'Manual trade request is missing a market id.'
    }
  }
  if (!tokenId) {
    return {
      ok: false,
      message: 'Manual trade request is missing a token id.'
    }
  }
  if (!side) {
    return {
      ok: false,
      message: 'Manual trade request is missing a side.'
    }
  }
  if (action === 'buy_more' && (!Number.isFinite(amountUsd) || Number(amountUsd) <= 0)) {
    return {
      ok: false,
      message: 'Buy more requires a positive USD amount.'
    }
  }
  if (startedAt <= 0 || lastActivityAt <= 0) {
    return {
      ok: false,
      message: 'Manual trade actions are unavailable because bot state is missing. Start the bot first.'
    }
  }
  if ((now - lastActivityAt) > heartbeatWindow) {
    return {
      ok: false,
      message: 'Manual trade actions are unavailable because the bot state looks stale. Restart or refresh the bot first.'
    }
  }
  if (requestIsRecent(manualTradeRequestPath, 15)) {
    return {
      ok: true,
      message: 'A manual trade request is already pending. Waiting for the bot to pick it up.'
    }
  }

  const payload: ManualTradeRequestPayload = {
    action,
    source: 'dashboard',
    request_id: `dashboard-${action}-${now}-${process.pid}`,
    requested_at: now,
    market_id: marketId,
    token_id: tokenId,
    side,
    question: question || undefined,
    trader_address: traderAddress || undefined,
    amount_usd: action === 'buy_more' && amountUsd != null ? Number(amountUsd.toFixed(6)) : undefined
  }

  try {
    fs.mkdirSync(path.dirname(manualTradeRequestPath), {recursive: true})
    const tempPath = `${manualTradeRequestPath}.${process.pid}.tmp`
    fs.writeFileSync(tempPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
    fs.renameSync(tempPath, manualTradeRequestPath)
    return {
      ok: true,
      message:
        action === 'buy_more'
          ? `Manual buy request queued for $${Number(amountUsd || 0).toFixed(2)}.`
          : 'Manual cash-out request queued.'
    }
  } catch (error) {
    return {
      ok: false,
      message: `Failed to request manual trade: ${error instanceof Error ? error.message : 'unknown error'}`
    }
  }
}
