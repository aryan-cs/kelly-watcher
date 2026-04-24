export type EnvProfile = 'default'

export const envProfile: EnvProfile = 'default'
process.env.KELLY_ENV = envProfile

export const envFileName = 'config.env'
export const secretsEnvFileName = 'secrets.env'
