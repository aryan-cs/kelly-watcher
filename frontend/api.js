import fs from 'fs';
import { localMode } from './envProfile.js';
import { envExamplePath, envReadPaths } from './paths.js';
export class ApiError extends Error {
    status;
    constructor(message, status = 500) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
    }
}
function sourceEnvPath() {
    return envReadPaths.find((candidate) => fs.existsSync(candidate)) || envExamplePath;
}
function stripMatchingQuotes(value) {
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        return value.slice(1, -1);
    }
    return value;
}
function readEnvFileValue(key) {
    for (const envPath of envReadPaths.length ? envReadPaths : [sourceEnvPath()]) {
        try {
            const lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
            for (const rawLine of lines) {
                const line = rawLine.trim();
                if (!line || line.startsWith('#') || !line.includes('=')) {
                    continue;
                }
                const [currentKey, ...rest] = line.split('=');
                if (currentKey.trim() !== key) {
                    continue;
                }
                return stripMatchingQuotes(rest.join('=').trim());
            }
        }
        catch {
            continue;
        }
    }
    return '';
}
function readRuntimeEnv(key, fallback = '') {
    const processValue = String(process.env[key] || '').trim();
    if (processValue) {
        return processValue;
    }
    const fileValue = readEnvFileValue(key).trim();
    return fileValue || fallback;
}
function localApiBaseUrl() {
    const port = readRuntimeEnv('DASHBOARD_API_PORT', '8765');
    return `http://127.0.0.1:${port}`;
}
const rawApiBaseUrl = localMode ? localApiBaseUrl() : readRuntimeEnv('KELLY_API_BASE_URL', 'http://127.0.0.1:8765');
export const apiBaseUrl = rawApiBaseUrl.replace(/\/+$/, '');
const apiToken = readRuntimeEnv('KELLY_API_TOKEN') || readRuntimeEnv('DASHBOARD_API_TOKEN');
function apiUrl(path) {
    if (/^https?:\/\//i.test(path)) {
        return path;
    }
    return `${apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`;
}
async function parseJsonResponse(response) {
    const text = await response.text();
    let payload = {};
    if (text) {
        try {
            payload = JSON.parse(text);
        }
        catch {
            if (!response.ok) {
                throw new ApiError(text, response.status);
            }
            throw new ApiError('Invalid JSON response from backend API.', response.status);
        }
    }
    if (!response.ok) {
        const message = typeof payload === 'object' && payload && 'message' in payload
            ? String(payload.message || '')
            : '';
        throw new ApiError(message || `Backend API request failed with status ${response.status}.`, response.status);
    }
    return payload;
}
export async function fetchApiJson(path, init = {}) {
    const headers = new Headers(init.headers || {});
    headers.set('Accept', 'application/json');
    if (apiToken) {
        headers.set('Authorization', `Bearer ${apiToken}`);
    }
    let response;
    try {
        response = await fetch(apiUrl(path), {
            ...init,
            headers
        });
    }
    catch (error) {
        if (error instanceof Error && error.name === 'AbortError') {
            throw error;
        }
        const detail = error instanceof Error ? String(error.message || '').trim() : '';
        const suffix = detail ? ` ${detail}` : '';
        throw new ApiError(`Could not reach backend API at ${apiBaseUrl}.${suffix}`.trim(), 0);
    }
    return parseJsonResponse(response);
}
export async function postApiJson(path, payload = {}, init = {}) {
    const headers = new Headers(init.headers || {});
    headers.set('Content-Type', 'application/json');
    return fetchApiJson(path, {
        ...init,
        method: 'POST',
        headers,
        body: JSON.stringify(payload)
    });
}
