import React from 'react'
import {Box as InkBox, Transform} from 'ink'
import {modalScrimColor} from '../theme.js'

interface Props {
  children: React.ReactNode
  backdrop?: React.ReactNode
  backgroundColor?: string
}

const ANSI_DIM_ON = '\u001b[2m'
const ANSI_DIM_OFF = '\u001b[22m'
const ANSI_BG_RESET = '\u001b[49m'
const ANSI_SGR_PATTERN = /\u001b\[([0-9;]*)m/g
const ANSI_FULL_RESET_PATTERN = /\u001b\[(?:0)?m/g
const ANSI_DIM_RESET_PATTERN = /\u001b\[22m/g
const ANSI_BG_RESET_PATTERN = /\u001b\[49m/g
const ANSI_16_COLORS: Array<[number, number, number]> = [
  [0, 0, 0],
  [205, 49, 49],
  [13, 188, 121],
  [229, 229, 16],
  [36, 114, 200],
  [188, 63, 188],
  [17, 168, 205],
  [229, 229, 229],
  [102, 102, 102],
  [241, 76, 76],
  [35, 209, 139],
  [245, 245, 67],
  [59, 142, 234],
  [214, 112, 214],
  [41, 184, 219],
  [255, 255, 255]
]

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.replace('#', '')
  return [
    Number.parseInt(normalized.slice(0, 2), 16),
    Number.parseInt(normalized.slice(2, 4), 16),
    Number.parseInt(normalized.slice(4, 6), 16)
  ]
}

function backgroundAnsiCode(hex: string): string {
  const [red, green, blue] = hexToRgb(hex)
  return `\u001b[48;2;${red};${green};${blue}m`
}

function clampChannel(value: number): number {
  return Math.max(0, Math.min(255, Math.round(value)))
}

function blendRgb(
  red: number,
  green: number,
  blue: number,
  scrimRgb: [number, number, number],
  strength: number
): [number, number, number] {
  return [
    clampChannel(red + ((scrimRgb[0] - red) * strength)),
    clampChannel(green + ((scrimRgb[1] - green) * strength)),
    clampChannel(blue + ((scrimRgb[2] - blue) * strength))
  ]
}

function ansiIndexedColorToRgb(index: number): [number, number, number] {
  if (index < 16) {
    return ANSI_16_COLORS[index] || ANSI_16_COLORS[0]
  }
  if (index >= 232) {
    const gray = clampChannel(8 + ((index - 232) * 10))
    return [gray, gray, gray]
  }
  const value = Math.max(0, index - 16)
  const red = Math.floor(value / 36)
  const green = Math.floor((value % 36) / 6)
  const blue = value % 6
  const cubeToRgb = (channel: number) => (channel === 0 ? 0 : 55 + (channel * 40))
  return [cubeToRgb(red), cubeToRgb(green), cubeToRgb(blue)]
}

function ansiNamedColorToRgb(code: number): [number, number, number] | null {
  if (code >= 30 && code <= 37) {
    return ANSI_16_COLORS[code - 30] || null
  }
  if (code >= 90 && code <= 97) {
    return ANSI_16_COLORS[(code - 90) + 8] || null
  }
  if (code >= 40 && code <= 47) {
    return ANSI_16_COLORS[code - 40] || null
  }
  if (code >= 100 && code <= 107) {
    return ANSI_16_COLORS[(code - 100) + 8] || null
  }
  return null
}

function transformAnsiSequence(code: string, scrimRgb: [number, number, number]): string {
  if (!code.trim()) {
    return `\u001b[${code}m`
  }
  const params = code.split(';')
  const transformed: string[] = []
  for (let index = 0; index < params.length; index += 1) {
    const param = Number.parseInt(params[index] || '', 10)
    if (!Number.isFinite(param)) {
      transformed.push(params[index] || '')
      continue
    }

    if ((param === 38 || param === 48) && params[index + 1] === '2' && index + 4 < params.length) {
      const red = Number.parseInt(params[index + 2] || '', 10)
      const green = Number.parseInt(params[index + 3] || '', 10)
      const blue = Number.parseInt(params[index + 4] || '', 10)
      if (Number.isFinite(red) && Number.isFinite(green) && Number.isFinite(blue)) {
        const [nextRed, nextGreen, nextBlue] = blendRgb(
          red,
          green,
          blue,
          scrimRgb,
          param === 38 ? 0.42 : 0.32
        )
        transformed.push(String(param), '2', String(nextRed), String(nextGreen), String(nextBlue))
        index += 4
        continue
      }
    }

    if ((param === 38 || param === 48) && params[index + 1] === '5' && index + 2 < params.length) {
      const colorIndex = Number.parseInt(params[index + 2] || '', 10)
      if (Number.isFinite(colorIndex)) {
        const [red, green, blue] = ansiIndexedColorToRgb(colorIndex)
        const [nextRed, nextGreen, nextBlue] = blendRgb(
          red,
          green,
          blue,
          scrimRgb,
          param === 38 ? 0.42 : 0.32
        )
        transformed.push(String(param), '2', String(nextRed), String(nextGreen), String(nextBlue))
        index += 2
        continue
      }
    }

    const namedColor = ansiNamedColorToRgb(param)
    if (namedColor) {
      const [red, green, blue] = blendRgb(
        namedColor[0],
        namedColor[1],
        namedColor[2],
        scrimRgb,
        param >= 40 && param <= 107 ? 0.32 : 0.42
      )
      transformed.push(param >= 40 && param <= 107 ? '48' : '38', '2', String(red), String(green), String(blue))
      continue
    }

    transformed.push(String(param))
  }
  return `\u001b[${transformed.join(';')}m`
}

function applyOverlayLine(line: string, backgroundCode: string, scrimRgb: [number, number, number]): string {
  const recolored = line.replace(ANSI_SGR_PATTERN, (_, code: string) => transformAnsiSequence(code, scrimRgb))
  return `${backgroundCode}${ANSI_DIM_ON}${recolored}`
    .replace(ANSI_FULL_RESET_PATTERN, (match) => `${match}${backgroundCode}${ANSI_DIM_ON}`)
    .replace(ANSI_BG_RESET_PATTERN, (match) => `${match}${backgroundCode}`)
    .replace(ANSI_DIM_RESET_PATTERN, (match) => `${match}${ANSI_DIM_ON}`)
    .concat(ANSI_BG_RESET, ANSI_DIM_OFF)
}

export function ModalOverlay({children, backdrop, backgroundColor}: Props) {
  const scrimColor = modalScrimColor(backgroundColor)
  const backgroundCode = backgroundAnsiCode(scrimColor)
  const scrimRgb = hexToRgb(scrimColor)

  return (
    <InkBox position="absolute" width="100%" height="100%">
      {backdrop ? (
        <InkBox position="absolute" width="100%" height="100%">
          <Transform transform={(line) => applyOverlayLine(line, backgroundCode, scrimRgb)}>
            {backdrop}
          </Transform>
        </InkBox>
      ) : null}
      <InkBox position="absolute" width="100%" height="100%" justifyContent="center" alignItems="center">
        {children}
      </InkBox>
    </InkBox>
  )
}
