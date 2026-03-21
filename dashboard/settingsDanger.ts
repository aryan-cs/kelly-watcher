import {spawnSync} from 'child_process'
import {readEnvValues, writeEditableConfigValue} from './configEditor.js'
import {projectRoot} from './paths.js'

export type DangerActionId = 'live_trading' | 'restart_shadow'

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
    description: 'Clear shadow tracker state, event history, and SQLite data, then restart the bot from the configured shadow bankroll. Confirmation lets you keep or clear WATCHED_WALLETS.',
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

function bankrollSummary(output: string): string {
  const match = output.match(/Initial bankroll:\s*\$([0-9.]+)/i)
  return match ? `$${match[1]}` : 'the configured bankroll'
}

function combinedOutput(stdout: string | null | undefined, stderr: string | null | undefined): string {
  return [stdout || '', stderr || '']
    .map((value) => value.trim())
    .filter(Boolean)
    .join('\n')
}

export function setLiveTradingEnabled(enabled: boolean): {ok: boolean; message: string} {
  try {
    writeEditableConfigValue('USE_REAL_MONEY', enabled ? 'true' : 'false')
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

export function restartShadowAccount(keepWallets: boolean): {ok: boolean; message: string} {
  const envValues = readEnvValues()
  if (isLiveTradingEnabled(envValues)) {
    return {
      ok: false,
      message: 'Restart Shadow is blocked while Live Trading is enabled in config. Turn Live Trading off first.'
    }
  }

  const previousWallets = String(envValues.WATCHED_WALLETS || '')

  try {
    if (!keepWallets) {
      writeEditableConfigValue('WATCHED_WALLETS', '')
    }

    const result = spawnSync('uv', ['run', 'python', 'restart_shadow.py'], {
      cwd: projectRoot,
      encoding: 'utf8'
    })
    if (result.error) {
      throw result.error
    }
    const output = combinedOutput(result.stdout, result.stderr)

    if (result.status !== 0) {
      if (!keepWallets) {
        writeEditableConfigValue('WATCHED_WALLETS', previousWallets)
      }
      return {
        ok: false,
        message: output || `Shadow restart failed with exit code ${result.status ?? 1}.`
      }
    }

    const bankroll = bankrollSummary(output)
    return {
      ok: true,
      message: keepWallets
        ? `Shadow account restarted from ${bankroll} and kept the current watched wallets.`
        : `Shadow account restarted from ${bankroll} and cleared WATCHED_WALLETS.`
    }
  } catch (error) {
    if (!keepWallets) {
      try {
        writeEditableConfigValue('WATCHED_WALLETS', previousWallets)
      } catch {
        // Keep the original failure surface as the primary error.
      }
    }
    return {
      ok: false,
      message: `Shadow restart failed: ${error instanceof Error ? error.message : 'unknown error'}`
    }
  }
}
