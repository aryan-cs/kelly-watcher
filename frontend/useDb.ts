import {useEffect, useState} from 'react'
import {postApiJson} from './api.js'
import {createPollingHealthStore, pollingErrorMessage, type PollingHealth} from './pollingHealth.js'
import {useRefreshToken} from './refresh.js'
import {isShadowRestartPending} from './useBotState.js'

interface QueryResponse<T> {
  rows?: T[]
}

const queryCache = new Map<string, unknown[]>()
const queryHealthStore = createPollingHealthStore()

export type QueryHealth = PollingHealth

export function clearQueryCache(): void {
  queryCache.clear()
  queryHealthStore.clear()
}

export function useQueryHealth(): QueryHealth {
  return queryHealthStore.useHealth()
}

export function useQuery<T>(sql: string, params: unknown[] = [], intervalMs = 1000): T[] {
  const paramsKey = JSON.stringify(params)
  const cacheKey = `${sql}\u0000${paramsKey}`
  const [rows, setRows] = useState<T[]>(() => (queryCache.get(cacheKey) as T[] | undefined) || [])
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let activeController: AbortController | null = null

    setRows((queryCache.get(cacheKey) as T[] | undefined) || [])
    queryHealthStore.register(cacheKey)

    const schedule = () => {
      if (cancelled) {
        return
      }
      timer = setTimeout(() => {
        void run()
      }, Math.max(intervalMs, 250))
    }

    const run = async () => {
      if (isShadowRestartPending()) {
        if (!cancelled) {
          setRows((queryCache.get(cacheKey) as T[] | undefined) || [])
        }
        schedule()
        return
      }
      const controller = new AbortController()
      activeController = controller
      queryHealthStore.recordAttempt(cacheKey)
      try {
        const response = await postApiJson<QueryResponse<T>>('/api/query', {sql, params}, {signal: controller.signal})
        const nextRows = Array.isArray(response.rows) ? response.rows : []
        queryCache.set(cacheKey, nextRows as unknown[])
        queryHealthStore.recordSuccess(cacheKey)
        if (!cancelled) {
          setRows(nextRows)
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
          return
        }
        const cachedRows = queryCache.get(cacheKey)
        if (!cancelled && cachedRows) {
          setRows(cachedRows as T[])
        }
        queryHealthStore.recordFailure(cacheKey, pollingErrorMessage(error, 'Query API request failed.'))
      } finally {
        if (activeController === controller) {
          activeController = null
        }
        schedule()
      }
    }

    void run()

    return () => {
      cancelled = true
      if (timer) {
        clearTimeout(timer)
      }
      activeController?.abort()
      queryHealthStore.unregister(cacheKey)
    }
  }, [cacheKey, intervalMs, paramsKey, refreshToken, sql])

  return rows
}
