import React, {useEffect} from 'react'
import {Box as InkBox, Text} from 'ink'
import {Box} from '../components/Box.js'
import {TradeRow} from '../components/TradeRow.js'
import {rowsForHeight} from '../responsive.js'
import {feedHeader} from '../tableLayout.js'
import {useTerminalSize} from '../terminal.js'
import {theme} from '../theme.js'
import {useEventStream} from '../useEventStream.js'
import {useTradeIdIndex} from '../useTradeIdIndex.js'

interface LiveFeedProps {
  scrollOffset?: number
  onScrollOffsetChange?: (offset: number) => void
}

export function LiveFeed({scrollOffset = 0, onScrollOffsetChange}: LiveFeedProps) {
  const terminal = useTerminalSize()
  const rowCount = rowsForHeight(terminal.height, 10, 4)
  const tableWidth = Math.max(1, terminal.width - 8)
  const allEvents = useEventStream(1000)
  const {lookup: tradeIdLookup} = useTradeIdIndex(allEvents)
  const allIncoming = allEvents.filter((event) => event.type === 'incoming').reverse()
  const maxOffset = Math.max(0, allIncoming.length - rowCount)
  const effectiveOffset = Math.max(0, Math.min(scrollOffset, maxOffset))
  const events = allIncoming.slice(effectiveOffset, effectiveOffset + rowCount)

  useEffect(() => {
    if (scrollOffset !== effectiveOffset) {
      onScrollOffsetChange?.(effectiveOffset)
    }
  }, [effectiveOffset, onScrollOffsetChange, scrollOffset])

  return (
    <Box height="100%">
      <Text color={theme.dim}>{feedHeader(tableWidth)}</Text>
      <InkBox flexDirection="column" marginTop={1}>
        {events.length ? (
          events.map((event, index) => (
            <TradeRow
              key={`incoming-${effectiveOffset + index}-${event.trade_id}-${event.ts}`}
              layout="feed"
              maxWidth={tableWidth}
              displayId={tradeIdLookup.get(event.trade_id)}
              ts={event.ts}
              username={event.username}
              trader={event.trader}
              question={event.question}
              marketUrl={event.market_url}
              side={event.side}
              action={event.action}
              price={event.price}
              shares={event.shares ?? event.size_usd}
              sizeUsd={event.amount_usd ?? event.size_usd * event.price}
            />
          ))
        ) : (
          <Text color={theme.dim}>Waiting for incoming trade events...</Text>
        )}
      </InkBox>
      <InkBox marginTop={1}>
        <Text color={theme.dim}>
          showing {events.length} of {allIncoming.length} events{effectiveOffset > 0 ? `  scroll: +${effectiveOffset}` : ''}
        </Text>
      </InkBox>
    </Box>
  )
}
