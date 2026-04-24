import React, { memo, useMemo } from 'react';
import { Box, Text } from 'ink';
import { fit, fitRight, formatDisplayId, formatAdaptiveDollar, formatAdaptiveNumber, formatClock, formatNumber, formatPct, normalizeReasonText, shortAddress, terminalHyperlink } from '../format.js';
import { getFeedLayout, getSignalsLayout } from '../tableLayout.js';
import { outcomeColor, positiveDollarColor, probabilityColor, theme } from '../theme.js';
function sliceViewport(text, offset, width) {
    const start = Math.max(0, offset);
    const viewportWidth = Math.max(0, width);
    return text.slice(start, start + viewportWidth).padEnd(viewportWidth);
}
export const TradeRow = memo(function TradeRow({ displayId, ts, username, trader, question, marketUrl, side, action, price, shares, sizeUsd, decision, confidence, reason, layout = 'feed', maxWidth = 80, viewportOffset = 0 }) {
    const sideColor = useMemo(() => outcomeColor(side), [side]);
    const signalSpec = useMemo(() => getSignalsLayout(maxWidth), [maxWidth]);
    const spec = useMemo(() => getFeedLayout(maxWidth), [maxWidth]);
    const displayIdText = useMemo(() => formatDisplayId(displayId, layout === 'signals' ? signalSpec.idWidth : spec.idWidth), [displayId, layout, signalSpec.idWidth, spec.idWidth]);
    const actionText = useMemo(() => {
        const normalizedAction = action?.trim().toLowerCase();
        if (normalizedAction === 'buy' || normalizedAction === 'entry')
            return 'BUY';
        if (normalizedAction === 'sell' || normalizedAction === 'exit')
            return 'SELL';
        if (normalizedAction)
            return normalizedAction.toUpperCase();
        if (decision === 'EXIT') {
            return 'SELL';
        }
        const normalizedReason = normalizeReasonText(reason || '').toLowerCase();
        if (normalizedReason.includes('exiting a position') ||
            normalizedReason.includes('unsupported trader action, sell')) {
            return 'SELL';
        }
        return 'BUY';
    }, [action, decision, reason]);
    const actionColor = useMemo(() => outcomeColor(actionText), [actionText]);
    const priceColor = useMemo(() => probabilityColor(price), [price]);
    const confidenceColor = useMemo(() => (confidence != null ? probabilityColor(confidence) : theme.white), [confidence]);
    const decisionColor = useMemo(() => decision === 'ACCEPT'
        ? theme.green
        : decision === 'REJECT'
            ? theme.red
            : decision === 'SKIP' || decision === 'PAUSE'
                ? theme.yellow
                : decision === 'IGNORE'
                    ? theme.dim
                    : theme.white, [decision]);
    const displayShares = useMemo(() => shares ?? (price > 0 ? sizeUsd / price : 0), [shares, price, sizeUsd]);
    const isBuyLike = !action || action.toLowerCase() === 'buy';
    const toWinUsd = useMemo(() => (isBuyLike && displayShares > 0 ? displayShares : null), [isBuyLike, displayShares]);
    const profitUsd = useMemo(() => (toWinUsd != null ? toWinUsd - sizeUsd : null), [toWinUsd, sizeUsd]);
    const feedProfitColor = useMemo(() => (profitUsd != null ? positiveDollarColor(profitUsd, 100) : theme.dim), [profitUsd]);
    const feedToWinColor = useMemo(() => (toWinUsd != null ? positiveDollarColor(toWinUsd, 100) : theme.dim), [toWinUsd]);
    const signalDisplayName = useMemo(() => username || shortAddress(trader || '-'), [trader, username]);
    const signalDisplayReason = useMemo(() => normalizeReasonText(reason || '-'), [reason]);
    const visibleSignalReason = useMemo(() => sliceViewport(signalDisplayReason, viewportOffset, signalSpec.reasonWidth), [signalDisplayReason, viewportOffset, signalSpec.reasonWidth]);
    const signalQuestionText = useMemo(() => terminalHyperlink(fit(question, signalSpec.questionWidth), marketUrl), [question, signalSpec.questionWidth, marketUrl]);
    const feedQuestionText = useMemo(() => terminalHyperlink(fit(question, spec.questionWidth), marketUrl), [question, spec.questionWidth, marketUrl]);
    if (layout === 'signals') {
        return (React.createElement(Box, null,
            signalSpec.showId ? (React.createElement(React.Fragment, null,
                React.createElement(Text, { color: theme.dim }, fitRight(displayIdText, signalSpec.idWidth)),
                React.createElement(Text, null, " "))) : null,
            React.createElement(Text, null, fit(formatClock(ts), signalSpec.timeWidth)),
            signalSpec.showUsername ? (React.createElement(React.Fragment, null,
                React.createElement(Text, null, " "),
                React.createElement(Text, null, fit(signalDisplayName, signalSpec.usernameWidth)))) : null,
            React.createElement(Text, null, " "),
            React.createElement(Text, { color: marketUrl ? theme.accent : undefined }, signalQuestionText),
            React.createElement(Text, null, " "),
            React.createElement(Text, { color: actionColor }, fit(actionText, signalSpec.actionWidth)),
            React.createElement(Text, null, " "),
            React.createElement(Text, { color: sideColor }, fit(side.toUpperCase(), signalSpec.sideWidth)),
            signalSpec.showPrice ? (React.createElement(React.Fragment, null,
                React.createElement(Text, null, " "),
                React.createElement(Text, { color: priceColor }, fitRight(formatNumber(price), signalSpec.priceWidth)))) : null,
            signalSpec.showShares ? (React.createElement(React.Fragment, null,
                React.createElement(Text, null, " "),
                React.createElement(Text, null, fitRight(formatAdaptiveNumber(displayShares, signalSpec.sharesWidth), signalSpec.sharesWidth)))) : null,
            signalSpec.showSize ? (React.createElement(React.Fragment, null,
                React.createElement(Text, null, " "),
                React.createElement(Text, null, fitRight(formatAdaptiveDollar(sizeUsd, signalSpec.sizeWidth), signalSpec.sizeWidth)))) : null,
            signalSpec.showDecision ? (React.createElement(React.Fragment, null,
                React.createElement(Text, null, " "),
                React.createElement(Text, { color: decisionColor }, fit(decision || '-', signalSpec.decisionWidth)))) : null,
            signalSpec.showConfidence ? (React.createElement(React.Fragment, null,
                React.createElement(Text, null, " "),
                React.createElement(Text, { color: confidenceColor }, fitRight(confidence != null ? formatPct(confidence, 1) : '-', signalSpec.confidenceWidth)))) : null,
            React.createElement(Text, null, " "),
            React.createElement(Text, { color: theme.dim }, visibleSignalReason)));
    }
    const feedDisplayName = username || shortAddress(trader || '-');
    return (React.createElement(Box, null,
        spec.showId ? (React.createElement(React.Fragment, null,
            React.createElement(Text, { color: theme.dim }, fitRight(displayIdText, spec.idWidth)),
            React.createElement(Text, null, " "))) : null,
        React.createElement(Text, null, fit(formatClock(ts), spec.timeWidth)),
        spec.showUsername ? (React.createElement(React.Fragment, null,
            React.createElement(Text, null, " "),
            React.createElement(Text, null, fit(feedDisplayName, spec.usernameWidth)))) : null,
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: marketUrl ? theme.accent : undefined }, feedQuestionText),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: actionColor }, fit(actionText, spec.actionWidth)),
        React.createElement(Text, null, " "),
        React.createElement(Text, { color: sideColor }, fit(side.toUpperCase(), spec.sideWidth)),
        spec.showPrice ? (React.createElement(React.Fragment, null,
            React.createElement(Text, null, " "),
            React.createElement(Text, { color: priceColor }, fitRight(formatNumber(price), spec.priceWidth)))) : null,
        spec.showShares ? (React.createElement(React.Fragment, null,
            React.createElement(Text, null, " "),
            React.createElement(Text, null, fitRight(formatAdaptiveNumber(displayShares, spec.sharesWidth), spec.sharesWidth)))) : null,
        spec.showPaid ? (React.createElement(React.Fragment, null,
            React.createElement(Text, null, " "),
            React.createElement(Text, null, fitRight(formatAdaptiveDollar(sizeUsd, spec.paidWidth), spec.paidWidth)))) : null,
        spec.showToWin ? (React.createElement(React.Fragment, null,
            React.createElement(Text, null, " "),
            React.createElement(Text, { color: feedToWinColor }, fitRight(toWinUsd != null ? formatAdaptiveDollar(toWinUsd, spec.toWinWidth) : '-', spec.toWinWidth)))) : null,
        spec.showProfit ? (React.createElement(React.Fragment, null,
            React.createElement(Text, null, " "),
            React.createElement(Text, { color: feedProfitColor }, fitRight(profitUsd != null ? formatAdaptiveDollar(profitUsd, spec.profitWidth) : '-', spec.profitWidth)))) : null));
});
