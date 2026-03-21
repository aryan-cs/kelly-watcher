import React from 'react';
import { Box as InkBox } from 'ink';
export function ModalOverlay({ children }) {
    return (React.createElement(InkBox, { position: "absolute", width: "100%", height: "100%" },
        React.createElement(InkBox, { position: "absolute", width: "100%", height: "100%", justifyContent: "center", alignItems: "center" }, children)));
}
