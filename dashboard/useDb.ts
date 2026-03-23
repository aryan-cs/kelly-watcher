import {useEffect, useState} from 'react'
import {postApiJson} from './api.js'
import {useRefreshToken} from './refresh.js'

interface QueryResponse<T> {
  rows?: T[]
}

const queryCache = new Map<string, unknown[]>()

export function clearQueryCache(): void {
  queryCache.clear()
}

export function useQuery<T>(sql: string, params: unknown[] = [], intervalMs = 2000): T[] {
  const paramsKey = JSON.stringify(params)
  const cacheKey = `${sql}\u0000${paramsKey}`
  const [rows, setRows] = useState<T[]>(() => (queryCache.get(cacheKey) as T[] | undefined) || [])
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let activeController: AbortController | null = null

    setRows((queryCache.get(cacheKey) as T[] | undefined) || [])

    const schedule = () => {
      if (cancelled) {
        return
      }
      timer = setTimeout(() => {
        void run()
      }, Math.max(intervalMs, 250))
    }

    const run = async () => {
      const controller = new AbortController()
      activeController = controller
      try {
        const response = await postApiJson<QueryResponse<T>>('/api/query', {sql, params}, {signal: controller.signal})
        const nextRows = Array.isArray(response.rows) ? response.rows : []
        queryCache.set(cacheKey, nextRows as unknown[])
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
    }
  }, [cacheKey, intervalMs, paramsKey, refreshToken, sql])

  return rows
}
