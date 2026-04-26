import {useEffect, useState} from 'react'
import {fetchApiJson} from './api.js'
import {createPollingHealthStore, pollingErrorMessage, type PollingHealth} from './pollingHealth.js'
import {useRefreshToken} from './refresh.js'
import {isShadowRestartPending} from './useBotState.js'

interface IdentityResponse {
  wallets?: Record<string, string>
}

let identityCache = new Map<string, string>()
const IDENTITY_HEALTH_KEY = 'identity-cache'
const identityHealthStore = createPollingHealthStore()

export type IdentityHealth = PollingHealth

export function clearIdentityCache(): void {
  identityCache = new Map<string, string>()
  identityHealthStore.clear()
}

export function useIdentityHealth(): IdentityHealth {
  return identityHealthStore.useHealth()
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

function normalizeIdentityMap(payload: IdentityResponse): Map<string, string> {
  const lookup = new Map<string, string>()
  for (const [wallet, usernameValue] of Object.entries(payload.wallets || {})) {
    const normalizedWallet = wallet.trim().toLowerCase()
    const username = String(usernameValue || '').trim()
    if (!normalizedWallet || isPlaceholderUsername(username, normalizedWallet)) {
      continue
    }
    lookup.set(normalizedWallet, username)
  }
  return lookup
}

export function readIdentityMap(): Map<string, string> {
  return new Map(identityCache)
}

export function useIdentityMap(intervalMs = 1000): Map<string, string> {
  const [lookup, setLookup] = useState<Map<string, string>>(() => readIdentityMap())
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    setLookup(new Map(identityCache))
    identityHealthStore.register(IDENTITY_HEALTH_KEY)

    const schedule = () => {
      if (cancelled) {
        return
      }
      timer = setTimeout(() => {
        void read()
      }, Math.max(intervalMs, 250))
    }

    const read = async () => {
      if (isShadowRestartPending()) {
        if (!cancelled) {
          setLookup(new Map(identityCache))
        }
        schedule()
        return
      }
      try {
        identityHealthStore.recordAttempt(IDENTITY_HEALTH_KEY)
        const payload = await fetchApiJson<IdentityResponse>('/api/identities')
        const nextLookup = normalizeIdentityMap(payload)
        identityCache = nextLookup
        identityHealthStore.recordSuccess(IDENTITY_HEALTH_KEY)
        if (!cancelled) {
          setLookup(new Map(nextLookup))
        }
      } catch (error) {
        if (!cancelled) {
          setLookup(new Map(identityCache))
          identityHealthStore.recordFailure(
            IDENTITY_HEALTH_KEY,
            pollingErrorMessage(error, 'Identity cache request failed.')
          )
        }
      } finally {
        schedule()
      }
    }

    void read()

    return () => {
      cancelled = true
      if (timer) {
        clearTimeout(timer)
      }
      identityHealthStore.unregister(IDENTITY_HEALTH_KEY)
    }
  }, [intervalMs, refreshToken])

  return lookup
}
