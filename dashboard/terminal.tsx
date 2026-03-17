import React, {createContext, useContext, useEffect, useMemo, useState} from 'react'
import {useStdout} from 'ink'

export interface TerminalMetrics {
  width: number
  height: number
  narrow: boolean
  compact: boolean
  wide: boolean
}

const defaultMetrics: TerminalMetrics = {
  width: 80,
  height: 24,
  narrow: true,
  compact: true,
  wide: false
}

const TerminalContext = createContext<TerminalMetrics>(defaultMetrics)

interface Props {
  children: React.ReactNode
}

export function TerminalSizeProvider({children}: Props) {
  const {stdout} = useStdout()
  const [size, setSize] = useState(() => ({
    width: stdout.columns || 80,
    height: stdout.rows || 24
  }))

  useEffect(() => {
    const update = () => {
      setSize({
        width: stdout.columns || 80,
        height: stdout.rows || 24
      })
    }

    update()
    stdout.on('resize', update)
    return () => {
      stdout.off('resize', update)
    }
  }, [stdout])

  const metrics = useMemo<TerminalMetrics>(() => ({
    width: Math.max(60, size.width || 80),
    height: Math.max(18, size.height || 24),
    narrow: (size.width || 80) < 100,
    compact: (size.width || 80) < 84,
    wide: (size.width || 80) >= 120
  }), [size])

  return (
    <TerminalContext.Provider value={metrics}>
      {children}
    </TerminalContext.Provider>
  )
}

export function useTerminalSize(): TerminalMetrics {
  return useContext(TerminalContext)
}

