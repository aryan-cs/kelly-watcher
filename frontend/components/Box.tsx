import React from 'react'
import {Box as InkBox, Text} from 'ink'
import {theme} from '../theme.js'

interface Props {
  title?: string
  children: React.ReactNode
  width?: string | number
  height?: string | number
  accent?: boolean
}

export function Box({title, children, width = '100%', height, accent = false}: Props) {
  return (
    <InkBox
      borderStyle="round"
      borderColor={accent ? theme.accent : theme.border}
      flexDirection="column"
      width={width}
      height={height}
      paddingX={1}
      flexShrink={0}
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
