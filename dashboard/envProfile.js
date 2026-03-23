const DEFAULT_ENV_PROFILE = 'dev';
const VALID_ENV_PROFILES = new Set(['dev', 'prod']);
function normalizeProfile(value) {
    const profile = String(value || '').trim().toLowerCase();
    return VALID_ENV_PROFILES.has(profile) ? profile : null;
}
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
export const envProfile = profileFromArgv() ||
    normalizeProfile(process.env.KELLY_ENV) ||
    DEFAULT_ENV_PROFILE;
process.env.KELLY_ENV = envProfile;
export const envFileName = `.env.${envProfile}`;
