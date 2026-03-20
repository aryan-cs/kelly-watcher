import { spawnSync } from 'child_process';
import { readEnvValues, writeEditableConfigValue } from './configEditor.js';
import { projectRoot } from './paths.js';
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
        description: 'Clear shadow tracker state, event history, and SQLite data, then restart the bot from the configured tracker bankroll. Confirmation lets you keep or clear WATCHED_WALLETS.',
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
function bankrollSummary(output) {
    const match = output.match(/Initial bankroll:\s*\$([0-9.]+)/i);
    return match ? `$${match[1]}` : 'the configured bankroll';
}
function combinedOutput(stdout, stderr) {
    return [stdout || '', stderr || '']
        .map((value) => value.trim())
        .filter(Boolean)
        .join('\n');
}
export function setLiveTradingEnabled(enabled) {
    try {
        writeEditableConfigValue('USE_REAL_MONEY', enabled ? 'true' : 'false');
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
export function restartShadowAccount(keepWallets) {
    const envValues = readEnvValues();
    if (isLiveTradingEnabled(envValues)) {
        return {
            ok: false,
            message: 'Restart Shadow is blocked while Live Trading is enabled in config. Turn Live Trading off first.'
        };
    }
    try {
        if (!keepWallets) {
            writeEditableConfigValue('WATCHED_WALLETS', '');
        }
        const result = spawnSync('/bin/bash', ['restart_shadow.sh'], {
            cwd: projectRoot,
            encoding: 'utf8'
        });
        const output = combinedOutput(result.stdout, result.stderr);
        if (result.status !== 0) {
            return {
                ok: false,
                message: output || `Shadow restart failed with exit code ${result.status ?? 1}.`
            };
        }
        const bankroll = bankrollSummary(output);
        return {
            ok: true,
            message: keepWallets
                ? `Shadow account restarted from ${bankroll} and kept the current watched wallets.`
                : `Shadow account restarted from ${bankroll} and cleared WATCHED_WALLETS.`
        };
    }
    catch (error) {
        return {
            ok: false,
            message: `Shadow restart failed: ${error instanceof Error ? error.message : 'unknown error'}`
        };
    }
}
