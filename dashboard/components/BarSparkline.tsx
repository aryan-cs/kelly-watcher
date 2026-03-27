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
}

export function BarSparkline({
  value,
  width = 20,
  label,
  positive,
  centered = false,
  axisChar = '|',
  color
}: Props) {
  const magnitude = Math.max(0, Math.min(1, Math.abs(value)))
  const fillColor = color || ((positive ?? value >= 0) ? theme.green : theme.red)

  if (centered) {
    const halfWidth = Math.max(1, Math.floor((width - 1) / 2))
    const filled = Math.round(magnitude * halfWidth)
    const empty = Math.max(0, halfWidth - filled)
    const leftEmpty = ' '.repeat(empty)
    const rightEmpty = ' '.repeat(empty)
    const leftBlank = ' '.repeat(halfWidth)
    const rightBlank = ' '.repeat(halfWidth)
    const filledBar = ' '.repeat(filled)

    return (
      <Text>
        {(positive ?? value >= 0) ? (
          <>
            <Text>{leftBlank}</Text>
            <Text color={theme.dim}>{axisChar}</Text>
            <Text backgroundColor={fillColor}>{filledBar}</Text>
            <Text>{rightEmpty}</Text>
          </>
        ) : (
          <>
            <Text>{leftEmpty}</Text>
            <Text backgroundColor={fillColor}>{filledBar}</Text>
            <Text color={theme.dim}>{axisChar}</Text>
            <Text>{rightBlank}</Text>
          </>
        )}
        {label ? <Text color={theme.dim}>  {label}</Text> : null}
      </Text>
    )
  }

  const filled = Math.round(magnitude * width)
  const empty = Math.max(0, width - filled)
  const bar = '█'.repeat(filled) + '░'.repeat(empty)

  return (
    <Text>
      <Text color={fillColor}>{bar}</Text>
      {label ? <Text color={theme.dim}>  {label}</Text> : null}
    </Text>
  )
}
