import fs from 'fs'
import path from 'path'
import {fileURLToPath} from 'url'
import {envFileName, envProfile} from './envProfile.js'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

export const dashboardDir = __dirname
export const projectRoot = path.resolve(dashboardDir, '..')
export const saveDir = path.resolve(projectRoot, 'save')
export const dataDir = path.resolve(saveDir, 'data')
export const dbPath = path.resolve(dataDir, 'trading.db')
export const eventsPath = path.resolve(dataDir, 'events.jsonl')
export const identityPath = path.resolve(dataDir, 'identity_cache.json')
export const botStatePath = path.resolve(dataDir, 'bot_state.json')
export const retrainRequestPath = path.resolve(dataDir, 'manual_retrain_request.json')
export const manualTradeRequestPath = path.resolve(dataDir, 'manual_trade_request.json')
export const saveEnvPath = path.resolve(saveDir, envFileName)
export const envPath = path.resolve(projectRoot, envFileName)
export const legacyEnvPath = path.resolve(projectRoot, '.env')
export const envExamplePath = path.resolve(projectRoot, '.env.example')
export const envReadPath =
  fs.existsSync(saveEnvPath)
    ? saveEnvPath
    : fs.existsSync(envPath)
      ? envPath
    : envProfile === 'dev' && fs.existsSync(legacyEnvPath)
      ? legacyEnvPath
      : envPath
