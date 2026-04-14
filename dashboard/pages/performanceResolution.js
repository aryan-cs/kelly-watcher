const RESOLVED_STATUSES = new Set(['win', 'lose'])

function asNumber(value) {
  const numeric = Number(value || 0)
  return Number.isFinite(numeric) ? numeric : 0
}

function asOptionalNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

export function formatResolutionLag(seconds) {
  const totalSeconds = Math.max(0, Math.floor(asNumber(seconds)))
  if (totalSeconds <= 0) {
    return '0s'
  }

  const days = Math.floor(totalSeconds / 86400)
  const hours = Math.floor((totalSeconds % 86400) / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const remainderSeconds = totalSeconds % 60

  if (days > 0) {
    return hours > 0 ? `${days}d ${hours}h` : `${days}d`
  }
  if (hours > 0) {
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`
  }
  if (minutes > 0) {
    return `${minutes}m`
  }
  return `${remainderSeconds}s`
}

export function buildResolutionTiming(
  row,
  nowTs,
  {
    formatShortDateTime,
    timeUntil
  }
) {
  const status = String(row?.status || '').trim().toLowerCase()
  const marketCloseTs = asNumber(row?.market_close_ts)
  const resolutionTs = asNumber(row?.resolution_ts)
  const enteredAt = asNumber(row?.entered_at)
  const resolvedClockTs = resolutionTs > 0 ? resolutionTs : (marketCloseTs > 0 ? marketCloseTs : enteredAt)
  const isResolved = RESOLVED_STATUSES.has(status)
  const isExit = status === 'exit'
  const isPastClose = marketCloseTs > 0 && marketCloseTs <= asNumber(nowTs)
  const isFutureClose = marketCloseTs > asNumber(nowTs)
  const lagSeconds = (isResolved || isExit) && marketCloseTs > 0 && resolutionTs > 0
    ? Math.max(resolutionTs - marketCloseTs, 0)
    : isPastClose
      ? Math.max(asNumber(nowTs) - marketCloseTs, 0)
      : null

  if (isExit) {
    const label = resolvedClockTs > 0 ? `exit ${formatShortDateTime(resolvedClockTs)}` : 'exit'
    return {
      statusLabel: 'exit',
      resolutionLabel: lagSeconds != null && lagSeconds > 0
        ? `${label} +${formatResolutionLag(lagSeconds)}`
        : label,
      resolutionTone: lagSeconds != null && lagSeconds > 0 ? 'yellow' : 'dim',
      lagSeconds,
      isResolved: false,
      isPastClose,
      isUnresolvedAfterClose: false
    }
  }

  if (isResolved) {
    const label = resolvedClockTs > 0 ? formatShortDateTime(resolvedClockTs) : '-'
    return {
      statusLabel: status,
      resolutionLabel: lagSeconds != null && lagSeconds > 0
        ? `${label} +${formatResolutionLag(lagSeconds)}`
        : label,
      resolutionTone: lagSeconds != null && lagSeconds > 0 ? 'yellow' : 'dim',
      lagSeconds,
      isResolved: true,
      isPastClose: false,
      isUnresolvedAfterClose: false
    }
  }

  if (isPastClose) {
    const closeLabel = marketCloseTs > 0 ? formatShortDateTime(marketCloseTs) : '-'
    return {
      statusLabel: status || 'waiting',
      resolutionLabel: lagSeconds != null && lagSeconds > 0
        ? `${closeLabel} +${formatResolutionLag(lagSeconds)}`
        : closeLabel,
      resolutionTone: 'red',
      lagSeconds,
      isResolved: false,
      isPastClose: true,
      isUnresolvedAfterClose: true
    }
  }

  if (isFutureClose) {
    return {
      statusLabel: status || 'open',
      resolutionLabel: `closes in ${timeUntil(marketCloseTs)}`,
      resolutionTone: 'yellow',
      lagSeconds: null,
      isResolved: false,
      isPastClose: false,
      isUnresolvedAfterClose: false
    }
  }

  return {
    statusLabel: status || 'open',
    resolutionLabel: resolvedClockTs > 0 ? formatShortDateTime(resolvedClockTs) : '-',
    resolutionTone: 'dim',
    lagSeconds: null,
    isResolved: false,
    isPastClose: false,
    isUnresolvedAfterClose: false
  }
}

export function buildPerformanceResolutionDisplay(
  row,
  nowTs,
  formatters
) {
  const status = String(row?.status || '').trim().toLowerCase()
  const profit = asOptionalNumber(row?.pnl_usd)
  const resolutionTiming = buildResolutionTiming(row, nowTs, formatters)

  const statusLabel =
    status === 'cashing_out'
      ? 'cashing out'
      : status === 'win'
        ? 'win'
        : status === 'lose'
          ? 'lose'
          : status === 'exit'
            ? profit != null && profit > 0
              ? 'exit up'
              : profit != null && profit < 0
                ? 'exit down'
                : 'exited'
            : 'waiting'

  const statusTone =
    status === 'cashing_out'
      ? 'yellow'
      : status === 'win'
        ? 'green'
        : status === 'lose'
          ? 'red'
          : status === 'exit'
            ? profit != null && profit > 0
              ? 'green'
              : profit != null && profit < 0
                ? 'red'
                : 'yellow'
            : 'yellow'

  return {
    resolutionTiming,
    resolutionLabel: resolutionTiming.resolutionLabel,
    resolutionTone: resolutionTiming.resolutionTone,
    statusLabel,
    statusTone,
    ttrLabel: formatters.timeUntil(asNumber(row?.market_close_ts))
  }
}
