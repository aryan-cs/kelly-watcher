import fs from 'fs'
import {identityPath} from './paths.js'

interface IdentityCachePayload {
  wallets?: Record<string, {username?: string}>
}

export function isPlaceholderUsername(username: string | undefined, wallet?: string): boolean {
  const display = (username || '').trim()
  if (!display) {
    return true
  }

  const normalizedWallet = (wallet || '').trim().toLowerCase()
  const normalizedUsername = display.toLowerCase()
  if (normalizedWallet && normalizedUsername === normalizedWallet) {
    return true
  }

  if (normalizedWallet && normalizedUsername.startsWith(`${normalizedWallet}-`)) {
    const suffix = normalizedUsername.slice(normalizedWallet.length + 1)
    if (/^\d+$/.test(suffix)) {
      return true
    }
  }

  return false
}

export function readIdentityMap(): Map<string, string> {
  try {
    const payload = JSON.parse(fs.readFileSync(identityPath, 'utf8')) as IdentityCachePayload
    const lookup = new Map<string, string>()
    for (const [wallet, entry] of Object.entries(payload.wallets || {})) {
      const username = (entry?.username || '').trim()
      const normalizedWallet = wallet.trim().toLowerCase()
      if (!normalizedWallet || isPlaceholderUsername(username, normalizedWallet)) {
        continue
      }
      lookup.set(normalizedWallet, username)
    }
    return lookup
  } catch {
    return new Map()
  }
}
