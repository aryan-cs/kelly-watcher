import React from 'react'
import {Box, Text} from 'ink'
import {truncate} from '../format.js'
import {theme} from '../theme.js'
import {useTerminalSize} from '../terminal.js'

interface Props {
  label: string
  value: string
  color?: string
}

export function StatRow({label, value, color = theme.white}: Props) {
  const terminal = useTerminalSize()
  const maxLabel = terminal.compact ? 16 : 24
  const maxValue = terminal.compact ? 14 : 24

  return (
    <Box justifyContent="space-between">
      <Text color={theme.dim}>{truncate(label, maxLabel)}</Text>
      <Text color={color}>{truncate(value, maxValue)}</Text>
    </Box>
  )
}
