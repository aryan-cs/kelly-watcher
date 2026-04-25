import { fit, fitRight } from './format.js';
function joinedWidth(widths) {
    const visibleWidths = widths.filter((value) => value > 0);
    return visibleWidths.reduce((sum, value) => sum + value, 0) + Math.max(0, visibleWidths.length - 1);
}
export function getFeedLayout(width) {
    const safeWidth = Math.max(1, Math.floor(width));
    const minQuestionWidth = safeWidth < 36 ? 4 : safeWidth < 64 ? 8 : 12;
    let showId = safeWidth >= 72;
    let showUsername = safeWidth >= 72;
    let showPrice = safeWidth >= 58;
    let showShares = safeWidth >= 68;
    let showPaid = safeWidth >= 78;
    let showToWin = safeWidth >= 92;
    let showProfit = safeWidth >= 106;
    const timeWidth = safeWidth < 32 ? 5 : safeWidth < 48 ? 8 : 11;
    const actionWidth = safeWidth < 36 ? 3 : 4;
    const sideWidth = safeWidth < 44 ? Math.max(3, Math.min(10, Math.floor(safeWidth * 0.25))) : safeWidth < 64 ? 12 : 16;
    const fixedWidth = () => joinedWidth([
        showId ? 6 : 0,
        timeWidth,
        showUsername ? 14 : 0,
        minQuestionWidth,
        actionWidth,
        sideWidth,
        showPrice ? 5 : 0,
        showShares ? 7 : 0,
        showPaid ? 8 : 0,
        showToWin ? 8 : 0,
        showProfit ? 8 : 0
    ]);
    for (const hide of [
        () => { showProfit = false; },
        () => { showToWin = false; },
        () => { showPaid = false; },
        () => { showShares = false; },
        () => { showPrice = false; },
        () => { showId = false; },
        () => { showUsername = false; }
    ]) {
        if (fixedWidth() <= safeWidth) {
            break;
        }
        hide();
    }
    const finalIdWidth = showId ? 6 : 0;
    const finalUsernameWidth = showUsername ? 14 : 0;
    const finalPriceWidth = showPrice ? 5 : 0;
    const finalSharesWidth = showShares ? 7 : 0;
    const finalPaidWidth = showPaid ? 8 : 0;
    const finalToWinWidth = showToWin ? 8 : 0;
    const finalProfitWidth = showProfit ? 8 : 0;
    const finalFixed = joinedWidth([
        finalIdWidth,
        timeWidth,
        finalUsernameWidth,
        actionWidth,
        sideWidth,
        finalPriceWidth,
        finalSharesWidth,
        finalPaidWidth,
        finalToWinWidth,
        finalProfitWidth
    ]);
    const questionWidth = Math.max(1, safeWidth - finalFixed - 1);
    return {
        idWidth: finalIdWidth,
        timeWidth,
        usernameWidth: finalUsernameWidth,
        questionWidth,
        actionWidth,
        sideWidth,
        priceWidth: finalPriceWidth,
        sharesWidth: finalSharesWidth,
        paidWidth: finalPaidWidth,
        toWinWidth: finalToWinWidth,
        profitWidth: finalProfitWidth,
        showId,
        showUsername,
        showPrice,
        showShares,
        showPaid,
        showToWin,
        showProfit
    };
}
export function getSignalsLayout(width) {
    const safeWidth = Math.max(1, Math.floor(width));
    let showId = safeWidth >= 84;
    let showUsername = safeWidth >= 72;
    let showPrice = safeWidth >= 96;
    let showShares = safeWidth >= 106;
    let showSize = safeWidth >= 116;
    const showDecision = safeWidth >= 66;
    let showConfidence = safeWidth >= 128;
    const timeWidth = safeWidth < 32 ? 5 : safeWidth < 48 ? 8 : 11;
    const questionMinWidth = safeWidth < 36 ? 4 : safeWidth < 64 ? 8 : 12;
    const questionMaxWidth = Math.min(34, Math.max(1, Math.floor(safeWidth * 0.26)));
    const reasonMinWidth = safeWidth < 36 ? 4 : safeWidth < 64 ? 8 : 12;
    const actionWidth = 4;
    const sideWidth = safeWidth < 44 ? Math.max(3, Math.min(10, Math.floor(safeWidth * 0.25))) : safeWidth < 64 ? 13 : 16;
    const decisionWidth = showDecision ? 6 : 0;
    const fixedWithMinimums = () => joinedWidth([
        showId ? 6 : 0,
        timeWidth,
        showUsername ? 12 : 0,
        questionMinWidth,
        actionWidth,
        sideWidth,
        showPrice ? 5 : 0,
        showShares ? 7 : 0,
        showSize ? 8 : 0,
        decisionWidth,
        showConfidence ? 6 : 0,
        reasonMinWidth
    ]);
    for (const hide of [
        () => { showConfidence = false; },
        () => { showSize = false; },
        () => { showShares = false; },
        () => { showPrice = false; },
        () => { showId = false; },
        () => { showUsername = false; }
    ]) {
        if (fixedWithMinimums() <= safeWidth) {
            break;
        }
        hide();
    }
    const idWidth = showId ? 6 : 0;
    const usernameWidth = showUsername ? 12 : 0;
    const priceWidth = showPrice ? 5 : 0;
    const sharesWidth = showShares ? 7 : 0;
    const sizeWidth = showSize ? 8 : 0;
    const confidenceWidth = showConfidence ? 6 : 0;
    const fixedWidths = [
        idWidth,
        timeWidth,
        usernameWidth,
        actionWidth,
        sideWidth,
        priceWidth,
        sharesWidth,
        sizeWidth,
        decisionWidth,
        confidenceWidth
    ].filter((value) => value > 0);
    const spaces = Math.max(0, fixedWidths.length + 1);
    const fixedTotal = fixedWidths.reduce((sum, value) => sum + value, 0);
    const questionWidth = Math.max(1, Math.min(questionMaxWidth, safeWidth - fixedTotal - spaces - reasonMinWidth));
    const reasonWidth = Math.max(1, safeWidth - fixedTotal - questionWidth - spaces);
    return {
        idWidth,
        timeWidth,
        usernameWidth,
        questionWidth,
        reasonWidth,
        actionWidth,
        sideWidth,
        priceWidth,
        sharesWidth,
        sizeWidth,
        decisionWidth,
        confidenceWidth,
        showId,
        showUsername,
        showPrice,
        showShares,
        showSize,
        showDecision,
        showConfidence
    };
}
export function feedHeader(width) {
    const layout = getFeedLayout(width);
    const parts = [
        layout.showId ? fitRight('ID', layout.idWidth) : '',
        fit('TIME', layout.timeWidth),
        layout.showUsername ? fit('USERNAME', layout.usernameWidth) : '',
        fit('MARKET', layout.questionWidth),
        fit('ACTN', layout.actionWidth),
        fit('SIDE', layout.sideWidth),
        layout.showPrice ? fit('PRICE', layout.priceWidth) : '',
        layout.showShares ? fitRight('SHARES', layout.sharesWidth) : '',
        layout.showPaid ? fitRight('PAID', layout.paidWidth) : '',
        layout.showToWin ? fitRight('TO WIN', layout.toWinWidth) : '',
        layout.showProfit ? fitRight('PROFIT', layout.profitWidth) : ''
    ].filter(Boolean);
    return parts.join(' ');
}
export function signalsHeader(width) {
    const layout = getSignalsLayout(width);
    const parts = [
        layout.showId ? fitRight('ID', layout.idWidth) : '',
        fit('TIME', layout.timeWidth),
        layout.showUsername ? fit('USERNAME', layout.usernameWidth) : '',
        fit('MARKET', layout.questionWidth),
        fit('ACTN', layout.actionWidth),
        fit('SIDE', layout.sideWidth),
        layout.showPrice ? fit('PRICE', layout.priceWidth) : '',
        layout.showShares ? fitRight('SHARES', layout.sharesWidth) : '',
        layout.showSize ? fitRight('TOTAL', layout.sizeWidth) : '',
        layout.showDecision ? fit('DEC', layout.decisionWidth) : '',
        layout.showConfidence ? fit('CONF', layout.confidenceWidth) : '',
        fit('REASON', layout.reasonWidth)
    ].filter(Boolean);
    return parts.join(' ');
}
