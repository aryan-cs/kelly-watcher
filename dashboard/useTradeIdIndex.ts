import {useMemo} from 'react'
import {useQuery} from './useDb.js'
import {type LiveEvent, useEventStream} from './useEventStream.js'

const TRADE_LOG_TRADE_IDS_SQL = `
SELECT trade_id
FROM trade_log
WHERE trade_id IS NOT NULL
  AND TRIM(trade_id) <> ''
ORDER BY id ASC
`

function normalizeTradeId(value: unknown): string | null {
  const tradeId = String(value ?? '').trim()
  return tradeId || null
}

export function useTradeIdIndex(eventsOverride?: LiveEvent[]): {lookup: Map<string, number>; maxId: number} {
  const tradeLogTradeIds = useQuery<{trade_id: string}>(TRADE_LOG_TRADE_IDS_SQL, [], 2000)
  const polledEvents = useEventStream(1000)
  const events = eventsOverride || polledEvents

  return useMemo(() => {
    const lookup = new Map<string, number>()
    let maxId = 0

    const assign = (tradeId: string | null) => {
      if (!tradeId || lookup.has(tradeId)) {
        return
      }
      maxId += 1
      lookup.set(tradeId, maxId)
    }

    for (const event of events) {
      assign(normalizeTradeId(event.trade_id))
    }
    for (const row of tradeLogTradeIds) {
      assign(normalizeTradeId(row.trade_id))
    }

    return {lookup, maxId}
  }, [events, tradeLogTradeIds])
}
