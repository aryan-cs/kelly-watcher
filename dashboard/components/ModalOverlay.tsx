import React from 'react'
import {Box as InkBox} from 'ink'

interface Props {
  children: React.ReactNode
  backdrop?: React.ReactNode
  backgroundColor?: string
}

export function ModalOverlay({children}: Props) {
  return (
    <InkBox position="absolute" width="100%" height="100%">
      <InkBox position="absolute" width="100%" height="100%" justifyContent="center" alignItems="center">
        {children}
      </InkBox>
    </InkBox>
  )
}
