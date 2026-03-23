import {postApiJson} from './api.js'
import {readEnvValues, writeEditableConfigValue} from './configEditor.js'

export type DangerActionId = 'live_trading' | 'restart_shadow'
export type RestartShadowWalletMode = 'keep_active' | 'keep_all' | 'clear_all'

export interface DangerActionDefinition {
  id: DangerActionId
  label: string
  description: string
  value: (envValues: Record<string, string>) => string
}

export interface DangerConfirmOption {
  id: string
  label: string
  description: string
}

export interface DangerConfirmState {
  actionId: DangerActionId
  title: string
  message: string
  options: DangerConfirmOption[]
  selectedIndex: number
}

interface DangerActionResult {
  ok: boolean
  message: string
}

export const dangerActions: DangerActionDefinition[] = [
  {
    id: 'live_trading',
    label: 'Live Trading',
    description: 'Toggle USE_REAL_MONEY in config. This does not switch the running bot immediately. Restart the bot after changing it.',
    value: (envValues) => (isLiveTradingEnabled(envValues) ? 'ON' : 'OFF')
  },
  {
    id: 'restart_shadow',
    label: 'Restart Shadow',
    description: 'Do a fresh shadow reset by wiping tracker history, training history, SQLite data, model artifacts, identity cache, events, and bot state before restart. Confirmation lets you keep active wallets, keep all wallets, or clear all wallets.',
    value: (envValues) => `${watchedWalletCount(envValues)} wlts`
  }
]

export function isLiveTradingEnabled(envValues: Record<string, string> = readEnvValues()): boolean {
  return String(envValues.USE_REAL_MONEY || 'false').trim().toLowerCase() === 'true'
}

export function watchedWalletCount(envValues: Record<string, string> = readEnvValues()): number {
  return String(envValues.WATCHED_WALLETS || '')
    .split(',')
    .map((wallet) => wallet.trim())
    .filter(Boolean).length
}

export async function setLiveTradingEnabled(enabled: boolean): Promise<DangerActionResult> {
  try {
    await writeEditableConfigValue('USE_REAL_MONEY', enabled ? 'true' : 'false')
    return {
      ok: true,
      message: `Live Trading saved as ${enabled ? 'ON' : 'OFF'}. Restart the bot to apply it safely.`
    }
  } catch (error) {
    return {
      ok: false,
      message: `Failed to update Live Trading: ${error instanceof Error ? error.message : 'unknown error'}`
    }
  }
}

export async function restartShadowAccount(walletMode: RestartShadowWalletMode): Promise<DangerActionResult> {
  try {
    return await postApiJson<DangerActionResult>('/api/shadow/restart', {walletMode})
  } catch (error) {
    return {
      ok: false,
      message: `Shadow restart failed: ${error instanceof Error ? error.message : 'unknown error'}`
    }
  }
}
