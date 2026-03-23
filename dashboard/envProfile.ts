export type EnvProfile = 'dev' | 'prod'

const DEFAULT_ENV_PROFILE: EnvProfile = 'dev'

function profileFromArgv(argv: string[] = process.argv.slice(2)): EnvProfile | null {
  const wantsDev = argv.includes('--dev')
  const wantsProd = argv.includes('--prod')
  if (wantsDev && wantsProd) {
    throw new Error('Pass only one of --dev or --prod.')
  }
  if (wantsProd) return 'prod'
  if (wantsDev) return 'dev'
  return null
}

export const envProfile: EnvProfile = profileFromArgv() || DEFAULT_ENV_PROFILE

process.env.KELLY_ENV = envProfile

export const envFileName = `.env.${envProfile}`
