import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { useStdout } from 'ink';
const defaultMetrics = {
    width: 80,
    height: 24,
    narrow: true,
    compact: true,
    wide: false
};
const TerminalContext = createContext(defaultMetrics);
export function TerminalSizeProvider({ children }) {
    const { stdout } = useStdout();
    const [size, setSize] = useState(() => ({
        width: stdout.columns || 80,
        height: stdout.rows || 24
    }));
    useEffect(() => {
        const update = () => {
            setSize({
                width: stdout.columns || 80,
                height: stdout.rows || 24
            });
        };
        update();
        stdout.on('resize', update);
        return () => {
            stdout.off('resize', update);
        };
    }, [stdout]);
    const metrics = useMemo(() => ({
        width: Math.max(60, size.width || 80),
        height: Math.max(18, size.height || 24),
        narrow: (size.width || 80) < 100,
        compact: (size.width || 80) < 84,
        wide: (size.width || 80) >= 120
    }), [size]);
    return (React.createElement(TerminalContext.Provider, { value: metrics }, children));
}
export function useTerminalSize() {
    return useContext(TerminalContext);
}
