import fs from 'fs';
import { useEffect, useState } from 'react';
import { botStatePath } from './paths.js';
import { useRefreshToken } from './refresh.js';
export function useBotState(intervalMs = 2000) {
    const [state, setState] = useState({});
    const refreshToken = useRefreshToken();
    useEffect(() => {
        let lastMtimeMs = 0;
        const read = () => {
            try {
                const stat = fs.statSync(botStatePath);
                if (stat.mtimeMs === lastMtimeMs)
                    return;
                lastMtimeMs = stat.mtimeMs;
                const payload = JSON.parse(fs.readFileSync(botStatePath, 'utf8'));
                setState(payload);
            }
            catch {
                setState({});
            }
        };
        lastMtimeMs = 0;
        read();
        fs.watchFile(botStatePath, { interval: Math.min(intervalMs, 500) }, read);
        return () => fs.unwatchFile(botStatePath, read);
    }, [intervalMs, refreshToken]);
    return state;
}
