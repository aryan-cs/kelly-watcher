import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
const DEFAULT_ENV_PROFILE = 'dev';
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');
function profileFromArgv(argv = process.argv.slice(2)) {
    const wantsDev = argv.includes('--dev');
    const wantsProd = argv.includes('--prod');
    if (wantsDev && wantsProd) {
        throw new Error('Pass only one of --dev or --prod.');
    }
    if (wantsProd)
        return 'prod';
    if (wantsDev)
        return 'dev';
    return null;
}
function profileFromEnv() {
    const raw = String(process.env.KELLY_ENV || '').trim().toLowerCase();
    if (raw === 'dev' || raw === 'prod') {
        return raw;
    }
    return null;
}
function readEnvFileValue(envPath, key) {
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
            return rest.join('=').trim().replace(/^['"]|['"]$/g, '');
        }
    }
    catch {
        return '';
    }
    return '';
}
function isLocalApiBaseUrl(url) {
    const value = String(url || '').trim().toLowerCase();
    return !value || /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(value);
}
function inferProfileFromEnvFiles() {
    const prodPath = path.resolve(projectRoot, '.env.prod');
    const devPath = path.resolve(projectRoot, '.env.dev');
    if (!fs.existsSync(prodPath)) {
        return null;
    }
    const prodApiBaseUrl = readEnvFileValue(prodPath, 'KELLY_API_BASE_URL');
    if (isLocalApiBaseUrl(prodApiBaseUrl)) {
        return null;
    }
    const devApiBaseUrl = readEnvFileValue(devPath, 'KELLY_API_BASE_URL');
    if (!devApiBaseUrl || isLocalApiBaseUrl(devApiBaseUrl)) {
        return 'prod';
    }
    return null;
}
export const envProfile = profileFromArgv() || profileFromEnv() || inferProfileFromEnvFiles() || DEFAULT_ENV_PROFILE;
process.env.KELLY_ENV = envProfile;
export const envFileName = `.env.${envProfile}`;
