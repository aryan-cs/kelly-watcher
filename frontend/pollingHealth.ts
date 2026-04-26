import {useEffect, useState} from 'react'

export interface PollingHealth {
  lastAttemptAt: number
  lastSuccessAt: number
  lastErrorAt: number
  lastError: string
  staleSourceCount: number
}

interface SourceFailure {
  at: number
  message: string
}

type PollingHealthListener = (health: PollingHealth) => void

const emptyHealth: PollingHealth = {
  lastAttemptAt: 0,
  lastSuccessAt: 0,
  lastErrorAt: 0,
  lastError: '',
  staleSourceCount: 0
}

export function pollingErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    const message = String(error.message || '').replace(/\s+/g, ' ').trim()
    if (message) {
      return message
    }
  }
  return fallback
}

export function createPollingHealthStore() {
  let health: PollingHealth = {...emptyHealth}
  const activeCounts = new Map<string, number>()
  const failures = new Map<string, SourceFailure>()
  const listeners = new Set<PollingHealthListener>()

  const latestFailure = (): SourceFailure | null => {
    let latest: SourceFailure | null = null
    for (const failure of failures.values()) {
      if (!latest || failure.at >= latest.at) {
        latest = failure
      }
    }
    return latest
  }

  const publish = (update: Partial<PollingHealth> = {}) => {
    const latest = latestFailure()
    health = {
      ...health,
      ...update,
      lastErrorAt: latest?.at || 0,
      lastError: latest?.message || '',
      staleSourceCount: failures.size
    }
    const snapshot = {...health}
    for (const listener of listeners) {
      listener(snapshot)
    }
  }

  const hasActiveSource = (sourceKey: string): boolean => (activeCounts.get(sourceKey) || 0) > 0

  return {
    clear() {
      failures.clear()
      health = {...emptyHealth}
      const snapshot = {...health}
      for (const listener of listeners) {
        listener(snapshot)
      }
    },
    register(sourceKey: string) {
      activeCounts.set(sourceKey, (activeCounts.get(sourceKey) || 0) + 1)
    },
    unregister(sourceKey: string) {
      const nextCount = (activeCounts.get(sourceKey) || 0) - 1
      if (nextCount > 0) {
        activeCounts.set(sourceKey, nextCount)
        return
      }
      activeCounts.delete(sourceKey)
      if (failures.delete(sourceKey)) {
        publish()
      }
    },
    recordAttempt(sourceKey: string, at = Date.now()) {
      if (!hasActiveSource(sourceKey)) {
        return
      }
      publish({lastAttemptAt: at})
    },
    recordSuccess(sourceKey: string, at = Date.now()) {
      if (!hasActiveSource(sourceKey)) {
        return
      }
      failures.delete(sourceKey)
      publish({lastSuccessAt: at})
    },
    recordFailure(sourceKey: string, message: string, at = Date.now()) {
      if (!hasActiveSource(sourceKey)) {
        return
      }
      failures.set(sourceKey, {at, message})
      publish({lastAttemptAt: Math.max(health.lastAttemptAt, at)})
    },
    useHealth(): PollingHealth {
      const [current, setCurrent] = useState<PollingHealth>(() => ({...health}))

      useEffect(() => {
        const listener: PollingHealthListener = (nextHealth) => {
          setCurrent({...nextHealth})
        }
        listeners.add(listener)
        setCurrent({...health})
        return () => {
          listeners.delete(listener)
        }
      }, [])

      return current
    }
  }
}
