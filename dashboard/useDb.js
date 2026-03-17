import { useEffect, useState } from 'react';
import Database from 'better-sqlite3';
import fs from 'fs';
import { dbPath } from './paths.js';
import { useRefreshToken } from './refresh.js';
export function useQuery(sql, params = [], intervalMs = 2000) {
    const [rows, setRows] = useState([]);
    const paramsKey = JSON.stringify(params);
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let lastMtimeMs = 0;
        const run = () => {
            try {
                const stat = fs.statSync(dbPath);
                if (stat.mtimeMs === lastMtimeMs)
                    return;
                lastMtimeMs = stat.mtimeMs;
                const db = new Database(dbPath, { readonly: true, fileMustExist: true });
                const result = db.prepare(sql).all(...params);
                db.close();
                setRows(result);
            }
            catch {
                setRows([]);
            }
        };
        lastMtimeMs = 0;
        run();
        fs.watchFile(dbPath, { interval: Math.min(intervalMs, 500) }, run);
        return () => fs.unwatchFile(dbPath, run);
    }, [sql, paramsKey, intervalMs, refreshToken]);
    return rows;
}
