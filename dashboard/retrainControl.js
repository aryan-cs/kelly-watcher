import fs from 'fs';
import path from 'path';
import { botStatePath, retrainRequestPath } from './paths.js';
function readBotStateSnapshot() {
    try {
        return JSON.parse(fs.readFileSync(botStatePath, 'utf8'));
    }
    catch {
        return {};
    }
}
function requestIsRecent(filePath, maxAgeSeconds) {
    try {
        const ageSeconds = (Date.now() - fs.statSync(filePath).mtimeMs) / 1000;
        return ageSeconds <= maxAgeSeconds;
    }
    catch {
        return false;
    }
}
export function requestManualRetrain() {
    const botState = readBotStateSnapshot();
    const now = Math.floor(Date.now() / 1000);
    const startedAt = Number(botState.started_at || 0);
    const lastActivityAt = Number(botState.last_activity_at || 0);
    const heartbeatWindow = Math.max(Number(botState.poll_interval || 1) * 3, 30);
    if (startedAt <= 0 || lastActivityAt <= 0) {
        return {
            ok: false,
            message: 'Manual retrain is unavailable because bot state is missing. Start the bot first.'
        };
    }
    if ((now - lastActivityAt) > heartbeatWindow) {
        return {
            ok: false,
            message: 'Manual retrain is unavailable because the bot state looks stale. Restart or refresh the bot first.'
        };
    }
    if (botState.retrain_in_progress) {
        return {
            ok: false,
            message: 'A retrain is already running.'
        };
    }
    if (requestIsRecent(retrainRequestPath, 30)) {
        return {
            ok: true,
            message: 'Manual retrain already requested. Waiting for the bot to pick it up.'
        };
    }
    const payload = {
        action: 'manual_retrain',
        source: 'dashboard',
        request_id: `dashboard-${now}-${process.pid}`,
        requested_at: now
    };
    try {
        fs.mkdirSync(path.dirname(retrainRequestPath), { recursive: true });
        const tempPath = `${retrainRequestPath}.${process.pid}.tmp`;
        fs.writeFileSync(tempPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
        fs.renameSync(tempPath, retrainRequestPath);
        return {
            ok: true,
            message: 'Manual retrain requested. The running bot should pick it up within about a second.'
        };
    }
    catch (error) {
        return {
            ok: false,
            message: `Failed to request manual retrain: ${error instanceof Error ? error.message : 'unknown error'}`
        };
    }
}
