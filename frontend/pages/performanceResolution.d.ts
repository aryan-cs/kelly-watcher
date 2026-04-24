export interface ResolutionTimingFormatters {
  formatShortDateTime: (ts: number) => string
  timeUntil: (ts: number) => string
}

export interface ResolutionTimingResult {
  statusLabel: string
  resolutionLabel: string
  resolutionTone: 'dim' | 'yellow' | 'red'
  lagSeconds: number | null
  isResolved: boolean
  isPastClose: boolean
  isUnresolvedAfterClose: boolean
}

export interface PerformanceResolutionDisplay {
  resolutionTiming: ResolutionTimingResult
  resolutionLabel: string
  resolutionTone: 'dim' | 'yellow' | 'red'
  statusLabel: string
  statusTone: 'dim' | 'yellow' | 'red' | 'green'
  ttrLabel: string
}

export declare function formatResolutionLag(seconds: number): string
export declare function buildResolutionTiming(
  row: {
    status?: string | null
    market_close_ts?: number | null
    resolution_ts?: number | null
    entered_at?: number | null
  },
  nowTs: number,
  formatters: ResolutionTimingFormatters
): ResolutionTimingResult
export declare function buildPerformanceResolutionDisplay(
  row: {
    status?: string | null
    market_close_ts?: number | null
    resolution_ts?: number | null
    entered_at?: number | null
    pnl_usd?: number | null
  },
  nowTs: number,
  formatters: ResolutionTimingFormatters
): PerformanceResolutionDisplay
