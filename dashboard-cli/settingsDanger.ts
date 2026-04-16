import {postApiJson} from './api.js'
import {readEnvValues} from './configEditor.js'

export type DangerActionId = 'live_trading' | 'archive_trade_log' | 'restart_shadow' | 'recover_db'
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
    description: 'Enable or disable live mode through the guarded backend endpoint. Live mode stays blocked until DB integrity, shadow-history, and segment-shadow readiness are satisfied.',
    value: (envValues) => (isLiveTradingEnabled(envValues) ? 'ON' : 'OFF')
  },
  {
    id: 'restart_shadow',
    label: 'Restart Shadow',
    description: 'Do a full shadow account reset by deleting the entire save directory and all shadow history, logs, models, and runtime state. Config settings stay in place. Confirmation lets you keep active wallets, keep all wallets, or clear all wallets.',
    value: (envValues) => `${watchedWalletCount(envValues)} wlts`
  },
  {
    id: 'archive_trade_log',
    label: 'Archive Trade Log',
    description: 'Move eligible closed trade_log rows out of the hot database and into the cold archive DB. Startup and daily maintenance already do this automatically; this manual action runs one bounded batch now.',
    value: () => 'batch'
  },
  {
    id: 'recover_db',
    label: 'Recover DB',
    description: 'Restore the shadow SQLite database from the latest verified backup and restart shadow mode. A verified backup may be integrity-only, not evidence-ready. Use this only when the current ledger is corrupt or untrustworthy.',
    value: () => 'backup'
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
    return await postApiJson<DangerActionResult>('/api/live-mode', {enabled})
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

export async function archiveTradeLog(): Promise<DangerActionResult> {
  try {
    return await postApiJson<DangerActionResult>('/api/shadow/archive-trade-log', {})
  } catch (error) {
    return {
      ok: false,
      message: `Trade log archive failed: ${error instanceof Error ? error.message : 'unknown error'}`
    }
  }
}

export async function recoverShadowDatabase(): Promise<DangerActionResult> {
  try {
    return await postApiJson<DangerActionResult>('/api/shadow/recover-db', {})
  } catch (error) {
    return {
      ok: false,
      message: `DB recovery failed: ${error instanceof Error ? error.message : 'unknown error'}`
    }
  }
}
