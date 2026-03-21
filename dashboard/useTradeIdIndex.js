import fs from 'fs';
import { useEffect, useMemo, useState } from 'react';
import { eventsPath } from './paths.js';
import { useRefreshToken } from './refresh.js';
import { useQuery } from './useDb.js';
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
    const [eventTradeIds, setEventTradeIds] = useState([]);
    const refreshToken = useRefreshToken();
    const tradeLogTradeIds = useQuery(TRADE_LOG_TRADE_IDS_SQL, [], 2000);
    useEffect(() => {
        let lastMtimeMs = 0;
        const read = () => {
            try {
                if (!fs.existsSync(eventsPath)) {
                    setEventTradeIds([]);
                    lastMtimeMs = 0;
                    return;
                }
                const stat = fs.statSync(eventsPath);
                if (stat.mtimeMs === lastMtimeMs)
                    return;
                lastMtimeMs = stat.mtimeMs;
                const content = fs.readFileSync(eventsPath, 'utf8').trim();
                const lines = content ? content.split('\n').filter(Boolean) : [];
                const tradeIds = [];
                const seen = new Set();
                for (const line of lines) {
                    const payload = JSON.parse(line);
                    const tradeId = normalizeTradeId(payload.trade_id);
                    if (!tradeId || seen.has(tradeId)) {
                        continue;
                    }
                    seen.add(tradeId);
                    tradeIds.push(tradeId);
                }
                setEventTradeIds(tradeIds);
            }
            catch {
                setEventTradeIds([]);
            }
        };
        lastMtimeMs = 0;
        read();
        fs.watchFile(eventsPath, { interval: 500 }, read);
        return () => {
            fs.unwatchFile(eventsPath, read);
        };
    }, [refreshToken]);
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
        // Preserve the live-feed numbering, then backfill historical trades from the DB
        // so current and past positions keep stable IDs after the event log rolls over.
        for (const tradeId of eventTradeIds) {
            assign(tradeId);
        }
        for (const row of tradeLogTradeIds) {
            assign(normalizeTradeId(row.trade_id));
        }
        return { lookup, maxId };
    }, [eventTradeIds, tradeLogTradeIds]);
}
