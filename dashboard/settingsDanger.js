import { postApiJson } from './api.js';
import { readEnvValues, writeEditableConfigValue } from './configEditor.js';
export const dangerActions = [
    {
        id: 'live_trading',
        label: 'Live Trading',
        description: 'Toggle USE_REAL_MONEY in config. This does not switch the running bot immediately. Restart the bot after changing it.',
        value: (envValues) => (isLiveTradingEnabled(envValues) ? 'ON' : 'OFF')
    },
    {
        id: 'restart_shadow',
        label: 'Restart Shadow',
        description: 'Do a fresh shadow reset by clearing tracker history, signals, open positions, and runtime state while preserving config settings, learned priors, and training history. Confirmation lets you keep active wallets, keep all wallets, or clear all wallets.',
        value: (envValues) => `${watchedWalletCount(envValues)} wlts`
    }
];
export function isLiveTradingEnabled(envValues = readEnvValues()) {
    return String(envValues.USE_REAL_MONEY || 'false').trim().toLowerCase() === 'true';
}
export function watchedWalletCount(envValues = readEnvValues()) {
    return String(envValues.WATCHED_WALLETS || '')
        .split(',')
        .map((wallet) => wallet.trim())
        .filter(Boolean).length;
}
export async function setLiveTradingEnabled(enabled) {
    try {
        await writeEditableConfigValue('USE_REAL_MONEY', enabled ? 'true' : 'false');
        return {
            ok: true,
            message: `Live Trading saved as ${enabled ? 'ON' : 'OFF'}. Restart the bot to apply it safely.`
        };
    }
    catch (error) {
        return {
            ok: false,
            message: `Failed to update Live Trading: ${error instanceof Error ? error.message : 'unknown error'}`
        };
    }
}
export async function restartShadowAccount(walletMode) {
    try {
        return await postApiJson('/api/shadow/restart', { walletMode });
    }
    catch (error) {
        return {
            ok: false,
            message: `Shadow restart failed: ${error instanceof Error ? error.message : 'unknown error'}`
        };
    }
}
