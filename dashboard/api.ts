import fs from 'fs'
import {envExamplePath, envPath} from './paths.js'

export class ApiError extends Error {
  status: number

  constructor(message: string, status = 500) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function sourceEnvPath(): string {
  return fs.existsSync(envPath) ? envPath : envExamplePath
}

function stripMatchingQuotes(value: string): string {
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1)
  }
  return value
}

function readEnvFileValue(key: string): string {
  try {
    const lines = fs.readFileSync(sourceEnvPath(), 'utf8').split(/\r?\n/)
    for (const rawLine of lines) {
      const line = rawLine.trim()
      if (!line || line.startsWith('#') || !line.includes('=')) {
        continue
      }
      const [currentKey, ...rest] = line.split('=')
      if (currentKey.trim() !== key) {
        continue
      }
      return stripMatchingQuotes(rest.join('=').trim())
    }
  } catch {
    return ''
  }
  return ''
}

function readRuntimeEnv(key: string, fallback = ''): string {
  const processValue = String(process.env[key] || '').trim()
  if (processValue) {
    return processValue
  }
  const fileValue = readEnvFileValue(key).trim()
  return fileValue || fallback
}

const rawApiBaseUrl = readRuntimeEnv('KELLY_API_BASE_URL', 'http://127.0.0.1:8765')
export const apiBaseUrl = rawApiBaseUrl.replace(/\/+$/, '')
const apiToken = readRuntimeEnv('KELLY_API_TOKEN')

function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path
  }
  return `${apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text()
  let payload: unknown = {}

  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      if (!response.ok) {
        throw new ApiError(text, response.status)
      }
      throw new ApiError('Invalid JSON response from backend API.', response.status)
    }
  }

  if (!response.ok) {
    const message =
      typeof payload === 'object' && payload && 'message' in payload
        ? String((payload as {message?: unknown}).message || '')
        : ''
    throw new ApiError(message || `Backend API request failed with status ${response.status}.`, response.status)
  }

  return payload as T
}

export async function fetchApiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {})
  headers.set('Accept', 'application/json')
  if (apiToken) {
    headers.set('Authorization', `Bearer ${apiToken}`)
  }

  let response: Response
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      headers
    })
  } catch (error) {
    const detail = error instanceof Error ? String(error.message || '').trim() : ''
    const suffix = detail ? ` ${detail}` : ''
    throw new ApiError(`Could not reach backend API at ${apiBaseUrl}.${suffix}`.trim(), 0)
  }
  return parseJsonResponse<T>(response)
}

export async function postApiJson<T>(path: string, payload: unknown = {}): Promise<T> {
  const headers = new Headers()
  headers.set('Content-Type', 'application/json')
  return fetchApiJson<T>(path, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload)
  })
}
