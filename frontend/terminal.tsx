import {readFile} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import React, {createContext, useContext, useEffect, useMemo, useState} from 'react'
import {useStdout} from 'ink'

export interface TerminalMetrics {
  width: number
  height: number
  narrow: boolean
  compact: boolean
  wide: boolean
  backgroundColor?: string
}

const defaultMetrics: TerminalMetrics = {
  width: 80,
  height: 24,
  narrow: true,
  compact: true,
  wide: false,
  backgroundColor: undefined
}

const TerminalContext = createContext<TerminalMetrics>(defaultMetrics)

interface Props {
  children: React.ReactNode
  backgroundColor?: string
}

function expandHome(filePath: string): string {
  if (filePath === '~') return os.homedir()
  if (filePath.startsWith('~/')) return path.join(os.homedir(), filePath.slice(2))
  return filePath
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function normalizeHexColor(raw: string): string | undefined {
  const normalized = raw.trim().toLowerCase()
  if (/^#[0-9a-f]{6}$/.test(normalized)) return normalized
  if (/^#[0-9a-f]{3}$/.test(normalized)) {
    return `#${normalized
      .slice(1)
      .split('')
      .map((part) => part + part)
      .join('')}`
  }
  return undefined
}

function componentToByte(raw: string): string {
  const normalized = raw.trim()
  if (!normalized) return '00'
  const parsed = Number.parseInt(normalized, 16)
  if (!Number.isFinite(parsed)) return '00'
  const scale = Math.max((16 ** normalized.length) - 1, 1)
  return clamp(Math.round((parsed / scale) * 255), 0, 255).toString(16).padStart(2, '0')
}

function parseTerminalBackgroundResponse(raw: string): string | undefined {
  const match = raw.match(/\u001b\]11;(?:rgb:([0-9a-fA-F]+)\/([0-9a-fA-F]+)\/([0-9a-fA-F]+)|#?([0-9a-fA-F]{6}))[\u0007\u001b\\]/)
  if (!match) return undefined
  if (match[4]) return `#${match[4].toLowerCase()}`
  return `#${componentToByte(match[1] || '')}${componentToByte(match[2] || '')}${componentToByte(match[3] || '')}`
}

function parseGhosttyConfig(raw: string): {background?: string; theme?: string} {
  let background: string | undefined
  let theme: string | undefined

  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue
    const separator = trimmed.indexOf('=')
    if (separator === -1) continue
    const key = trimmed.slice(0, separator).trim()
    const value = trimmed.slice(separator + 1).trim()
    if (!value) continue

    if (key === 'background') {
      background = normalizeHexColor(value) || background
    } else if (key === 'theme') {
      theme = value
    }
  }

  return {background, theme}
}

async function readTextFile(filePath: string): Promise<string | undefined> {
  try {
    return await readFile(expandHome(filePath), 'utf8')
  } catch {
    return undefined
  }
}

async function detectGhosttyBackgroundColor(): Promise<string | undefined> {
  if (process.env.TERM_PROGRAM !== 'ghostty') {
    return undefined
  }

  const configPaths = [
    '~/Library/Application Support/com.mitchellh.ghostty/config',
    '~/.config/ghostty/config'
  ]

  for (const configPath of configPaths) {
    const config = await readTextFile(configPath)
    if (!config) continue

    const parsed = parseGhosttyConfig(config)
    if (parsed.background) return parsed.background

    if (parsed.theme) {
      const themeName = path.basename(parsed.theme)
      const theme = await readTextFile(`/Applications/Ghostty.app/Contents/Resources/ghostty/themes/${themeName}`)
      if (!theme) continue
      const themeBackground = parseGhosttyConfig(theme).background
      if (themeBackground) return themeBackground
    }
  }

  return undefined
}

export async function detectTerminalBackgroundColor(timeoutMs = 120): Promise<string | undefined> {
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    return await detectGhosttyBackgroundColor()
  }

  const stdin = process.stdin
  const stdout = process.stdout
  const canSetRawMode = typeof stdin.setRawMode === 'function'
  const wasRaw = Boolean((stdin as NodeJS.ReadStream & {isRaw?: boolean}).isRaw)
  const shouldPauseAfter = stdin.readableFlowing !== true

  const queriedColor = await new Promise<string | undefined>((resolve) => {
    let settled = false
    let buffer = ''
    let timer: NodeJS.Timeout | undefined

    const finish = (color?: string) => {
      if (settled) return
      settled = true
      if (timer) clearTimeout(timer)
      stdin.off('data', onData)
      if (canSetRawMode && !wasRaw) {
        try {
          stdin.setRawMode(false)
        } catch {}
      }
      if (shouldPauseAfter) stdin.pause()
      resolve(color)
    }

    const onData = (chunk: Buffer | string) => {
      buffer += typeof chunk === 'string' ? chunk : chunk.toString('utf8')
      const color = parseTerminalBackgroundResponse(buffer)
      if (color) {
        finish(color)
        return
      }
      if (buffer.length > 512) finish(undefined)
    }

    stdin.on('data', onData)
    if (canSetRawMode && !wasRaw) {
      try {
        stdin.setRawMode(true)
      } catch {}
    }
    stdin.resume()
    stdout.write('\u001b]11;?\u0007')
    timer = setTimeout(() => finish(undefined), timeoutMs)
  })

  return queriedColor || await detectGhosttyBackgroundColor()
}

export function TerminalSizeProvider({children, backgroundColor}: Props) {
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
    wide: (size.width || 80) >= 120,
    backgroundColor
  }), [backgroundColor, size])

  return (
    <TerminalContext.Provider value={metrics}>
      {children}
    </TerminalContext.Provider>
  )
}

export function useTerminalSize(): TerminalMetrics {
  return useContext(TerminalContext)
}
