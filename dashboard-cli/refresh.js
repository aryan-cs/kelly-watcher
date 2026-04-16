import React, { createContext, useContext } from 'react';
const RefreshContext = createContext({ refreshToken: 0 });
export function ManualRefreshProvider({ refreshToken, children }) {
    return (React.createElement(RefreshContext.Provider, { value: { refreshToken } }, children));
}
export function useRefreshToken() {
    return useContext(RefreshContext).refreshToken;
}
