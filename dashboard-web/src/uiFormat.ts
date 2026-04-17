import {feedTheme} from './feedUtils'

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.replace('#', '')
  return [
    Number.parseInt(normalized.slice(0, 2), 16),
    Number.parseInt(normalized.slice(2, 4), 16),
    Number.parseInt(normalized.slice(4, 6), 16)
  ]
}

function rgbToHex(red: number, green: number, blue: number): string {
  return `#${[red, green, blue]
    .map((channel) => clamp(Math.round(channel), 0, 255).toString(16).padStart(2, '0'))
    .join('')}`
}

function blendHex(left: string, right: string, t: number): string {
  const [lr, lg, lb] = hexToRgb(left)
  const [rr, rg, rb] = hexToRgb(right)
  const mix = clamp(t, 0, 1)
  return rgbToHex(lr + (rr - lr) * mix, lg + (rg - lg) * mix, lb + (rb - lb) * mix)
}

export function formatInteger(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-'
  return new Intl.NumberFormat('en-US', {maximumFractionDigits: 0}).format(value)
}

export function formatDecimal(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '-'
  return value.toFixed(digits)
}

export function formatMoney(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '-'
  return `$${new Intl.NumberFormat('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  }).format(value)}`
}

export function formatPercentFromRatio(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '-'
  return `${(value * 100).toFixed(digits)}%`
}

export function formatTimestamp(value: number | null | undefined): string {
  if (!value) return '-'
  return new Date(value * 1000).toLocaleString([], {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true
  })
}

export function formatRelativeAge(value: number | null | undefined): string {
  if (!value) return '-'
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - value))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export function formatBytes(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value) || value < 0) return '-'
  if (value < 1024) return `${formatInteger(value)} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let scaled = value / 1024
  let unitIndex = 0
  while (scaled >= 1024 && unitIndex < units.length - 1) {
    scaled /= 1024
    unitIndex += 1
  }
  const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2
  return `${formatDecimal(scaled, digits)} ${units[unitIndex]}`
}

export function toneColor(tone: 'positive' | 'negative' | 'warning' | 'accent' | 'muted' | 'default' = 'default'): string | undefined {
  if (tone === 'positive') return feedTheme.green
  if (tone === 'negative') return feedTheme.red
  if (tone === 'warning') return feedTheme.yellow
  if (tone === 'accent') return feedTheme.blue
  if (tone === 'muted') return feedTheme.dim
  return undefined
}

export function booleanTone(value: boolean | null | undefined): 'positive' | 'negative' | 'muted' {
  if (value == null) return 'muted'
  return value ? 'positive' : 'negative'
}

export function resolveToneColor(tone: string | null | undefined): string | undefined {
  if (!tone) return undefined
  return toneColor(tone as 'positive' | 'negative' | 'warning' | 'accent' | 'muted' | 'default') || tone
}

export function goodTradePnlUsd(
  bankrollUsd: number | null | undefined,
  tradeNotionalUsd: number | null | undefined = 0
): number {
  const bankroll = Math.max(0, Number(bankrollUsd || 0))
  const tradeNotional = Math.max(0, Number(tradeNotionalUsd || 0))
  return Math.max(10, bankroll * 0.08, tradeNotional * 0.2)
}

export function signedGradientColor(
  value: number | null | undefined,
  maxMagnitude: number | null | undefined
): string | undefined {
  if (value == null || Number.isNaN(value)) return undefined
  const scale = Math.max(1, Number(maxMagnitude || 0))
  const normalized = clamp(value / scale, -1, 1)
  if (normalized < 0) {
    return blendHex(feedTheme.red, feedTheme.yellow, normalized + 1)
  }
  return blendHex(feedTheme.yellow, feedTheme.green, normalized)
}

export function moneyMetricColor(
  value: number | null | undefined,
  bankrollUsd: number | null | undefined,
  tradeNotionalUsd: number | null | undefined = 0
): string | undefined {
  return signedGradientColor(value, goodTradePnlUsd(bankrollUsd, tradeNotionalUsd))
}

export function returnMetricColor(
  value: number | null | undefined,
  bankrollUsd: number | null | undefined,
  tradeNotionalUsd: number | null | undefined
): string | undefined {
  if (value == null || Number.isNaN(value)) return undefined
  const referenceNotional = Math.max(0, Number(tradeNotionalUsd || 0))
  return moneyMetricColor(value * referenceNotional, bankrollUsd, referenceNotional)
}

export function centeredRatioGradient(
  value: number | null | undefined,
  center: number,
  span: number,
  invert = false
): string | undefined {
  if (value == null || Number.isNaN(value) || span <= 0) return undefined
  const signed = invert ? center - value : value - center
  return signedGradientColor(signed, span)
}

export function cutoffRatioGradient(
  value: number | null | undefined,
  cutoff: number | null | undefined
): string | undefined {
  if (value == null || Number.isNaN(value)) return undefined
  const normalizedValue = clamp(value, 0, 1)
  const normalizedCutoff = clamp(cutoff ?? 0.5, 0, 1)

  if (normalizedValue <= normalizedCutoff) {
    const t = normalizedCutoff <= 0 ? 1 : normalizedValue / normalizedCutoff
    return blendHex(feedTheme.red, feedTheme.yellow, t)
  }

  const remainder = 1 - normalizedCutoff
  const t = remainder <= 0 ? 1 : (normalizedValue - normalizedCutoff) / remainder
  return blendHex(feedTheme.yellow, feedTheme.green, t)
}
