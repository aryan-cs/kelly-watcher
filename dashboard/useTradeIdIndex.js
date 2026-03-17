import fs from 'fs';
import { useEffect, useMemo, useState } from 'react';
import { eventsPath } from './paths.js';
import { useRefreshToken } from './refresh.js';
export function useTradeIdIndex() {
    const [entries, setEntries] = useState([]);
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let lastMtimeMs = 0;
        const read = () => {
            try {
                const stat = fs.statSync(eventsPath);
                if (stat.mtimeMs === lastMtimeMs)
                    return;
                lastMtimeMs = stat.mtimeMs;
                const content = fs.readFileSync(eventsPath, 'utf8').trim();
                const lines = content ? content.split('\n').filter(Boolean) : [];
                const lookup = new Map();
                let nextId = 1;
                for (const line of lines) {
                    const payload = JSON.parse(line);
                    const tradeId = payload.trade_id?.trim();
                    if (!tradeId || lookup.has(tradeId)) {
                        continue;
                    }
                    lookup.set(tradeId, nextId);
                    nextId += 1;
                }
                setEntries(Array.from(lookup.entries(), ([tradeId, displayId]) => ({ tradeId, displayId })));
            }
            catch {
                setEntries([]);
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
        for (const entry of entries) {
            lookup.set(entry.tradeId, entry.displayId);
            if (entry.displayId > maxId) {
                maxId = entry.displayId;
            }
        }
        return { lookup, maxId };
    }, [entries]);
}
