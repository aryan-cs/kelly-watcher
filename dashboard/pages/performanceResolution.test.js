import assert from 'node:assert/strict'

import {buildPerformanceResolutionDisplay, buildResolutionTiming, formatResolutionLag} from './performanceResolution.js'

const formatShortDateTime = (ts) => `dt:${ts}`
const timeUntil = (ts) => `until:${ts}`

const nowTs = 1_700_000_000

assert.equal(formatResolutionLag(59), '59s')
assert.equal(formatResolutionLag(3_660), '1h 1m')
assert.equal(formatResolutionLag(7_200), '2h')
assert.equal(formatResolutionLag(90_061), '1d 1h')

const unresolved = buildResolutionTiming(
  {
    status: 'waiting',
    market_close_ts: nowTs - 7_200,
    resolution_ts: 0,
    entered_at: nowTs - 10_000
  },
  nowTs,
  {
    formatShortDateTime,
    timeUntil
  }
)

assert.equal(unresolved.statusLabel, 'waiting')
assert.equal(unresolved.resolutionLabel, 'dt:1699992800 +2h')
assert.equal(unresolved.resolutionTone, 'red')
assert.equal(unresolved.isUnresolvedAfterClose, true)

const resolved = buildResolutionTiming(
  {
    status: 'exit',
    market_close_ts: nowTs - 7_200,
    resolution_ts: nowTs - 3_600,
    entered_at: nowTs - 10_000
  },
  nowTs,
  {
    formatShortDateTime,
    timeUntil
  }
)

assert.equal(resolved.statusLabel, 'exit')
assert.equal(resolved.resolutionLabel, 'exit dt:1699996400 +1h')
assert.equal(resolved.resolutionTone, 'yellow')
assert.equal(resolved.isResolved, false)

const renderedExit = buildPerformanceResolutionDisplay(
  {
    status: 'exit',
    market_close_ts: nowTs - 7_200,
    resolution_ts: nowTs - 3_600,
    entered_at: nowTs - 10_000,
    pnl_usd: 4.25
  },
  nowTs,
  {
    formatShortDateTime,
    timeUntil
  }
)

assert.equal(renderedExit.resolutionLabel, 'exit dt:1699996400 +1h')
assert.equal(renderedExit.resolutionTone, 'yellow')
assert.equal(renderedExit.statusLabel, 'exit up')
assert.equal(renderedExit.statusTone, 'green')

const future = buildResolutionTiming(
  {
    status: 'open',
    market_close_ts: nowTs + 3_600,
    resolution_ts: 0,
    entered_at: nowTs - 100
  },
  nowTs,
  {
    formatShortDateTime,
    timeUntil
  }
)

assert.equal(future.statusLabel, 'open')
assert.equal(future.resolutionLabel, 'closes in until:1700003600')
assert.equal(future.resolutionTone, 'yellow')
assert.equal(future.isPastClose, false)

const renderedFuture = buildPerformanceResolutionDisplay(
  {
    status: 'open',
    market_close_ts: nowTs + 3_600,
    resolution_ts: 0,
    entered_at: nowTs - 100,
    pnl_usd: null
  },
  nowTs,
  {
    formatShortDateTime,
    timeUntil
  }
)

assert.equal(renderedFuture.resolutionLabel, 'closes in until:1700003600')
assert.equal(renderedFuture.resolutionTone, 'yellow')
assert.equal(renderedFuture.statusLabel, 'waiting')
assert.equal(renderedFuture.statusTone, 'yellow')
assert.equal(renderedFuture.ttrLabel, 'until:1700003600')

const renderedWaiting = buildPerformanceResolutionDisplay(
  {
    status: 'waiting',
    market_close_ts: nowTs - 7_200,
    resolution_ts: 0,
    entered_at: nowTs - 10_000,
    pnl_usd: null
  },
  nowTs,
  {
    formatShortDateTime,
    timeUntil
  }
)

assert.equal(renderedWaiting.resolutionLabel, 'dt:1699992800 +2h')
assert.equal(renderedWaiting.resolutionTone, 'red')
assert.equal(renderedWaiting.statusLabel, 'waiting')
assert.equal(renderedWaiting.statusTone, 'yellow')

console.log('performanceResolution helper tests passed')
