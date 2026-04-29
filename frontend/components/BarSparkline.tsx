import React from 'react'
import {Text} from 'ink'
import {theme} from '../theme.js'

interface Props {
  value: number
  width?: number
  label?: string
  positive?: boolean
  centered?: boolean
  axisChar?: string
  color?: string
  backgroundColor?: string
}

export function BarSparkline({
  value,
  width = 20,
  label,
  positive,
  centered = false,
  axisChar = '|',
  color,
  backgroundColor
}: Props) {
  const magnitude = Math.max(0, Math.min(1, Math.abs(value)))
  const fillColor = color || ((positive ?? value >= 0) ? theme.green : theme.red)

  if (centered) {
    const slotWidth = 2
    const safeWidth = Math.max(1, width)
    const halfWidth = Math.max(1, Math.floor((safeWidth - 1) / (slotWidth * 2)))
    const filled = Math.round(magnitude * halfWidth)
    const empty = Math.max(0, halfWidth - filled)
    const renderedWidth = (halfWidth * slotWidth * 2) + 1
    const trailingPad = Math.max(0, safeWidth - renderedWidth)
    const leftEmpty = ' '.repeat(empty * slotWidth)
    const rightEmpty = ' '.repeat(empty * slotWidth)
    const leftBlank = ' '.repeat(halfWidth * slotWidth)
    const rightBlank = ' '.repeat(halfWidth * slotWidth)
    const filledBar = ' '.repeat(filled * slotWidth)

    return (
      <Text backgroundColor={backgroundColor}>
        {(positive ?? value >= 0) ? (
          <>
            <Text backgroundColor={backgroundColor}>{leftBlank}</Text>
            <Text color={theme.dim} backgroundColor={backgroundColor}>{axisChar}</Text>
            <Text backgroundColor={fillColor}>{filledBar}</Text>
            <Text backgroundColor={backgroundColor}>{rightEmpty}</Text>
            {trailingPad > 0 ? <Text backgroundColor={backgroundColor}>{' '.repeat(trailingPad)}</Text> : null}
          </>
        ) : (
          <>
            {trailingPad > 0 ? <Text backgroundColor={backgroundColor}>{' '.repeat(trailingPad)}</Text> : null}
            <Text backgroundColor={backgroundColor}>{leftEmpty}</Text>
            <Text backgroundColor={fillColor}>{filledBar}</Text>
            <Text color={theme.dim} backgroundColor={backgroundColor}>{axisChar}</Text>
            <Text backgroundColor={backgroundColor}>{rightBlank}</Text>
          </>
        )}
        {label ? <Text color={theme.dim} backgroundColor={backgroundColor}>  {label}</Text> : null}
      </Text>
    )
  }

  const filled = Math.round(magnitude * width)
  const empty = Math.max(0, width - filled)
  const bar = '█'.repeat(filled) + '░'.repeat(empty)

  return (
    <Text backgroundColor={backgroundColor}>
      <Text color={fillColor} backgroundColor={backgroundColor}>{bar}</Text>
      {label ? <Text color={theme.dim} backgroundColor={backgroundColor}>  {label}</Text> : null}
    </Text>
  )
}
