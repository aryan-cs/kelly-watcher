import React from 'react'
import {Box as InkBox, Text} from 'ink'
import {theme} from '../theme.js'

interface Props {
  title?: string
  children: React.ReactNode
  width?: string | number
  height?: string | number
  flexShrink?: number
  accent?: boolean
}

export function Box({title, children, width = '100%', height, flexShrink = 1, accent = false}: Props) {
  return (
    <InkBox
      borderStyle="round"
      borderColor={accent ? theme.accent : theme.border}
      flexDirection="column"
      width={width}
      height={height}
      paddingX={1}
      flexShrink={flexShrink}
      overflow="hidden"
    >
      {title ? (
        <InkBox>
          <Text color={theme.accent} bold>{title}</Text>
        </InkBox>
      ) : null}
      {children}
    </InkBox>
  )
}
