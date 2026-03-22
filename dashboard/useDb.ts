import {useEffect, useState} from 'react'
import {postApiJson} from './api.js'
import {useRefreshToken} from './refresh.js'

interface QueryResponse<T> {
  rows?: T[]
}

export function useQuery<T>(sql: string, params: unknown[] = [], intervalMs = 2000): T[] {
  const [rows, setRows] = useState<T[]>([])
  const paramsKey = JSON.stringify(params)
  const refreshToken = useRefreshToken()

  useEffect(() => {
    let cancelled = false

    const run = async () => {
      try {
        const response = await postApiJson<QueryResponse<T>>('/api/query', {sql, params})
        if (!cancelled) {
          setRows(Array.isArray(response.rows) ? response.rows : [])
        }
      } catch {
        if (!cancelled) {
          setRows([])
        }
      }
    }

    void run()
    const timer = setInterval(() => {
      void run()
    }, Math.max(intervalMs, 250))

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [sql, paramsKey, intervalMs, refreshToken])

  return rows
}
