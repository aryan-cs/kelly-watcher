import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
const DEFAULT_ENV_PROFILE = 'default';
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');
function profileFromArgv(argv = process.argv.slice(2)) {
    void argv;
    return null;
}
function profileFromEnv() {
    const raw = String(process.env.KELLY_ENV || '').trim().toLowerCase();
    if (raw === 'default') {
        return 'default';
    }
    return null;
}
function inferProfileFromEnvFiles() {
    void fs;
    void projectRoot;
    return null;
}
function truthy(value) {
    return ['1', 'true', 'yes', 'on'].includes(String(value || '').trim().toLowerCase());
}
export const localMode = process.argv.includes('--local') || truthy(process.env.KELLY_LOCAL_MODE);
export const envProfile = profileFromArgv() || profileFromEnv() || inferProfileFromEnvFiles() || DEFAULT_ENV_PROFILE;
process.env.KELLY_ENV = envProfile;
if (localMode) {
    process.env.KELLY_LOCAL_MODE = '1';
}
export const envFileName = 'kelly-config.env';
