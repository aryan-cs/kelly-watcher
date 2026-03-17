import React, {createContext, useContext} from 'react'

interface RefreshContextValue {
  refreshToken: number
}

const RefreshContext = createContext<RefreshContextValue>({refreshToken: 0})

interface ManualRefreshProviderProps {
  refreshToken: number
  children: React.ReactNode
}

export function ManualRefreshProvider({refreshToken, children}: ManualRefreshProviderProps) {
  return (
    <RefreshContext.Provider value={{refreshToken}}>
      {children}
    </RefreshContext.Provider>
  )
}

export function useRefreshToken(): number {
  return useContext(RefreshContext).refreshToken
}

