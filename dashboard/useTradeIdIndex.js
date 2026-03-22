import { useMemo } from 'react';
import { useQuery } from './useDb.js';
import { useEventStream } from './useEventStream.js';
const TRADE_LOG_TRADE_IDS_SQL = `
SELECT trade_id
FROM trade_log
WHERE trade_id IS NOT NULL
  AND TRIM(trade_id) <> ''
ORDER BY id ASC
`;
function normalizeTradeId(value) {
    const tradeId = String(value ?? '').trim();
    return tradeId || null;
}
export function useTradeIdIndex() {
    const tradeLogTradeIds = useQuery(TRADE_LOG_TRADE_IDS_SQL, [], 2000);
    const events = useEventStream(1000);
    return useMemo(() => {
        const lookup = new Map();
        let maxId = 0;
        const assign = (tradeId) => {
            if (!tradeId || lookup.has(tradeId)) {
                return;
            }
            maxId += 1;
            lookup.set(tradeId, maxId);
        };
        for (const event of events) {
            assign(normalizeTradeId(event.trade_id));
        }
        for (const row of tradeLogTradeIds) {
            assign(normalizeTradeId(row.trade_id));
        }
        return { lookup, maxId };
    }, [events, tradeLogTradeIds]);
}
