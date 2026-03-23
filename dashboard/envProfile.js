const DEFAULT_ENV_PROFILE = 'dev';
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
export const envProfile = profileFromArgv() || DEFAULT_ENV_PROFILE;
process.env.KELLY_ENV = envProfile;
export const envFileName = `.env.${envProfile}`;
