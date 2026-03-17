import React, {memo, useMemo} from 'react'
import {Box, Text} from 'ink'
import {
  fit,
  fitRight,
  formatDisplayId,
  formatAdaptiveDollar,
  formatAdaptiveNumber,
  formatClock,
  formatNumber,
  formatPct,
  normalizeReasonText,
  shortAddress
} from '../format.js'
import {getFeedLayout, getSignalsLayout} from '../tableLayout.js'
import {outcomeColor, positiveDollarColor, probabilityColor, theme} from '../theme.js'

interface Props {
  displayId?: number | null
  ts: number
  username?: string
  trader?: string
  question: string
  side: string
  action?: string
  price: number
  shares?: number
  sizeUsd: number
  decision?: string
  confidence?: number
  reason?: string
  layout?: 'feed' | 'signals'
  maxWidth?: number
  viewportOffset?: number
}

function sliceViewport(text: string, offset: number, width: number): string {
  const start = Math.max(0, offset)
  const viewportWidth = Math.max(0, width)
  return text.slice(start, start + viewportWidth).padEnd(viewportWidth)
}

export const TradeRow = memo(function TradeRow({
  displayId,
  ts,
  username,
  trader,
  question,
  side,
  action,
  price,
  shares,
  sizeUsd,
  decision,
  confidence,
  reason,
  layout = 'feed',
  maxWidth = 80,
  viewportOffset = 0
}: Props) {
  const sideColor = useMemo(() => outcomeColor(side), [side])
  const signalSpec = useMemo(() => getSignalsLayout(maxWidth), [maxWidth])
  const spec = useMemo(() => getFeedLayout(maxWidth), [maxWidth])
  const displayIdText = useMemo(
    () => formatDisplayId(displayId, layout === 'signals' ? signalSpec.idWidth : spec.idWidth),
    [displayId, layout, signalSpec.idWidth, spec.idWidth]
  )
  const actionText = useMemo(() => {
    const normalizedAction = action?.trim().toLowerCase()
    if (normalizedAction === 'buy' || normalizedAction === 'entry') return 'BUY'
    if (normalizedAction === 'sell' || normalizedAction === 'exit') return 'SELL'
    if (normalizedAction) return normalizedAction.toUpperCase()
    if (decision === 'EXIT') {
      return 'SELL'
    }
    const normalizedReason = normalizeReasonText(reason || '').toLowerCase()
    if (
      normalizedReason.includes('exiting a position') ||
      normalizedReason.includes('unsupported trader action, sell')
    ) {
      return 'SELL'
    }
    return 'BUY'
  }, [action, decision, reason])
  const actionColor = useMemo(() => outcomeColor(actionText), [actionText])
  const priceColor = useMemo(() => probabilityColor(price), [price])
  const confidenceColor = useMemo(
    () => (confidence != null ? probabilityColor(confidence) : theme.white),
    [confidence]
  )
  const decisionColor = useMemo(
    () =>
      decision === 'ACCEPT'
        ? theme.green
        : decision === 'REJECT'
          ? theme.red
          : decision === 'SKIP'
            ? theme.yellow
            : theme.white,
    [decision]
  )
  const displayShares = useMemo(() => shares ?? (price > 0 ? sizeUsd / price : 0), [shares, price, sizeUsd])
  const isBuyLike = !action || action.toLowerCase() === 'buy'
  const toWinUsd = useMemo(
    () => (isBuyLike && displayShares > 0 ? displayShares : null),
    [isBuyLike, displayShares]
  )
  const profitUsd = useMemo(
    () => (toWinUsd != null ? toWinUsd - sizeUsd : null),
    [toWinUsd, sizeUsd]
  )
  const feedProfitColor = useMemo(
    () => (profitUsd != null ? positiveDollarColor(profitUsd, 100) : theme.dim),
    [profitUsd]
  )
  const feedToWinColor = useMemo(
    () => (toWinUsd != null ? positiveDollarColor(toWinUsd, 100) : theme.dim),
    [toWinUsd]
  )
  const signalDisplayName = useMemo(() => username || shortAddress(trader || '-'), [trader, username])
  const signalDisplayReason = useMemo(() => normalizeReasonText(reason || '-'), [reason])
  const visibleSignalReason = useMemo(
    () => sliceViewport(signalDisplayReason, viewportOffset, signalSpec.reasonWidth),
    [signalDisplayReason, viewportOffset, signalSpec.reasonWidth]
  )

  if (layout === 'signals') {
    return (
      <Box>
        {signalSpec.showId ? (
          <>
            <Text color={theme.dim}>{fitRight(displayIdText, signalSpec.idWidth)}</Text>
            <Text> </Text>
          </>
        ) : null}
        <Text>{fit(formatClock(ts), signalSpec.timeWidth)}</Text>
        {signalSpec.showUsername ? (
          <>
            <Text> </Text>
            <Text>{fit(signalDisplayName, signalSpec.usernameWidth)}</Text>
          </>
        ) : null}
        <Text> </Text>
        <Text>{fit(question, signalSpec.questionWidth)}</Text>
        <Text> </Text>
        <Text color={actionColor}>{fit(actionText, signalSpec.actionWidth)}</Text>
        <Text> </Text>
        <Text color={sideColor}>{fit(side.toUpperCase(), signalSpec.sideWidth)}</Text>
        {signalSpec.showPrice ? (
          <>
            <Text> </Text>
            <Text color={priceColor}>{fitRight(formatNumber(price), signalSpec.priceWidth)}</Text>
          </>
        ) : null}
        {signalSpec.showShares ? (
          <>
            <Text> </Text>
            <Text>{fitRight(formatAdaptiveNumber(displayShares, signalSpec.sharesWidth), signalSpec.sharesWidth)}</Text>
          </>
        ) : null}
        {signalSpec.showSize ? (
          <>
            <Text> </Text>
            <Text>{fitRight(formatAdaptiveDollar(sizeUsd, signalSpec.sizeWidth), signalSpec.sizeWidth)}</Text>
          </>
        ) : null}
        {signalSpec.showDecision ? (
          <>
            <Text> </Text>
            <Text color={decisionColor}>{fit(decision || '-', signalSpec.decisionWidth)}</Text>
          </>
        ) : null}
        {signalSpec.showConfidence ? (
          <>
            <Text> </Text>
            <Text color={confidenceColor}>
              {fitRight(confidence != null ? formatPct(confidence, 1) : '-', signalSpec.confidenceWidth)}
            </Text>
          </>
        ) : null}
        <Text> </Text>
        <Text color={theme.dim}>{visibleSignalReason}</Text>
      </Box>
    )
  }

  const feedDisplayName = username || shortAddress(trader || '-')

  return (
    <Box>
      {spec.showId ? (
        <>
          <Text color={theme.dim}>{fitRight(displayIdText, spec.idWidth)}</Text>
          <Text> </Text>
        </>
      ) : null}
      <Text>{fit(formatClock(ts), spec.timeWidth)}</Text>
      {spec.showUsername ? (
        <>
          <Text> </Text>
          <Text>{fit(feedDisplayName, spec.usernameWidth)}</Text>
        </>
      ) : null}
      <Text> </Text>
      <Text>{fit(question, spec.questionWidth)}</Text>
      <Text> </Text>
      <Text color={actionColor}>{fit(actionText, spec.actionWidth)}</Text>
      <Text> </Text>
      <Text color={sideColor}>{fit(side.toUpperCase(), spec.sideWidth)}</Text>
      {spec.showPrice ? (
        <>
          <Text> </Text>
          <Text color={priceColor}>{fitRight(formatNumber(price), spec.priceWidth)}</Text>
        </>
      ) : null}
      {spec.showShares ? (
        <>
          <Text> </Text>
          <Text>{fitRight(formatAdaptiveNumber(displayShares, spec.sharesWidth), spec.sharesWidth)}</Text>
        </>
      ) : null}
      {spec.showPaid ? (
        <>
          <Text> </Text>
          <Text>{fitRight(formatAdaptiveDollar(sizeUsd, spec.paidWidth), spec.paidWidth)}</Text>
        </>
      ) : null}
      {spec.showToWin ? (
        <>
          <Text> </Text>
          <Text color={feedToWinColor}>
            {fitRight(
              toWinUsd != null ? formatAdaptiveDollar(toWinUsd, spec.toWinWidth) : '-',
              spec.toWinWidth
            )}
          </Text>
        </>
      ) : null}
      {spec.showProfit ? (
        <>
          <Text> </Text>
          <Text color={feedProfitColor}>
            {fitRight(
              profitUsd != null ? formatAdaptiveDollar(profitUsd, spec.profitWidth) : '-',
              spec.profitWidth
            )}
          </Text>
        </>
      ) : null}
    </Box>
  )
})
