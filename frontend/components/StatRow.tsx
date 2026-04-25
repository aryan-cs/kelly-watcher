import React from 'react'
import {Box, Spacer, Text} from 'ink'
import {truncate} from '../format.js'
import {theme} from '../theme.js'
import {useTerminalSize} from '../terminal.js'

interface Props {
  label: string
  value: string
  color?: string
  width?: number
}

export function StatRow({label, value, color = theme.white, width}: Props) {
  const terminal = useTerminalSize()
  const rowWidth = Math.max(1, Math.floor(width ?? (terminal.width - 4)))
  const maxLabel = Math.min(terminal.compact ? 16 : 24, Math.max(1, Math.floor(rowWidth * 0.58)))
  const maxValue = Math.min(terminal.compact ? 14 : 24, Math.max(0, rowWidth - maxLabel - 1))
  const hasGap = rowWidth > maxLabel + maxValue

  return (
    <Box width={rowWidth} flexShrink={0}>
      <Text color={theme.dim}>{truncate(label, maxLabel)}</Text>
      {hasGap ? <Spacer /> : null}
      <Box width={maxValue} justifyContent="flex-end" flexShrink={0}>
        <Text color={color}>{truncate(value, maxValue)}</Text>
      </Box>
    </Box>
  )
}
