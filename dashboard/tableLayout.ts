import {fit, fitRight} from './format.js'

export interface FeedLayout {
  idWidth: number
  timeWidth: number
  usernameWidth: number
  questionWidth: number
  actionWidth: number
  sideWidth: number
  priceWidth: number
  sharesWidth: number
  paidWidth: number
  toWinWidth: number
  profitWidth: number
  showId: boolean
  showUsername: boolean
  showPrice: boolean
  showShares: boolean
  showPaid: boolean
  showToWin: boolean
  showProfit: boolean
}

export interface SignalsLayout {
  idWidth: number
  timeWidth: number
  usernameWidth: number
  questionWidth: number
  reasonWidth: number
  actionWidth: number
  sideWidth: number
  priceWidth: number
  sharesWidth: number
  sizeWidth: number
  decisionWidth: number
  confidenceWidth: number
  showId: boolean
  showUsername: boolean
  showPrice: boolean
  showShares: boolean
  showSize: boolean
  showDecision: boolean
  showConfidence: boolean
}

export function getFeedLayout(width: number): FeedLayout {
  const showId = width >= 72
  const showUsername = width >= 72
  const showPrice = width >= 58
  const showShares = width >= 68
  const showPaid = width >= 78
  const showToWin = width >= 92
  const showProfit = width >= 106
  const idWidth = showId ? 6 : 0
  const timeWidth = 11
  const usernameWidth = showUsername ? 14 : 0
  const actionWidth = 4
  const sideWidth = 4
  const priceWidth = showPrice ? 5 : 0
  const sharesWidth = showShares ? 7 : 0
  const paidWidth = showPaid ? 8 : 0
  const toWinWidth = showToWin ? 8 : 0
  const profitWidth = showProfit ? 8 : 0
  const widths = [
    idWidth,
    timeWidth,
    usernameWidth,
    actionWidth,
    sideWidth,
    priceWidth,
    sharesWidth,
    paidWidth,
    toWinWidth,
    profitWidth
  ].filter((value) => value > 0)
  const spaces = Math.max(0, widths.length)
  const questionWidth = Math.max(12, width - widths.reduce((sum, value) => sum + value, 0) - spaces)

  return {
    idWidth,
    timeWidth,
    usernameWidth,
    questionWidth,
    actionWidth,
    sideWidth,
    priceWidth,
    sharesWidth,
    paidWidth,
    toWinWidth,
    profitWidth,
    showId,
    showUsername,
    showPrice,
    showShares,
    showPaid,
    showToWin,
    showProfit
  }
}

export function getSignalsLayout(width: number): SignalsLayout {
  const showId = width >= 84
  const showUsername = width >= 72
  const showPrice = width >= 96
  const showShares = width >= 106
  const showSize = width >= 116
  const showDecision = true
  const showConfidence = width >= 128
  const idWidth = showId ? 6 : 0
  const timeWidth = 11
  const usernameWidth = showUsername ? 14 : 0
  const questionMinWidth = 12
  const questionMaxWidth = Math.min(36, Math.floor(width * 0.28))
  const reasonMinWidth = 12
  const actionWidth = 4
  const sideWidth = 4
  const priceWidth = showPrice ? 5 : 0
  const sharesWidth = showShares ? 7 : 0
  const sizeWidth = showSize ? 8 : 0
  const decisionWidth = showDecision ? 6 : 0
  const confidenceWidth = showConfidence ? 5 : 0
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
  ].filter((value) => value > 0)
  const spaces = Math.max(0, fixedWidths.length + 1)
  const fixedTotal = fixedWidths.reduce((sum, value) => sum + value, 0)
  const questionWidth = Math.max(
    questionMinWidth,
    Math.min(questionMaxWidth, width - fixedTotal - spaces - reasonMinWidth)
  )
  const reasonWidth = Math.max(reasonMinWidth, width - fixedTotal - questionWidth - spaces)

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
  }
}

export function feedHeader(width: number): string {
  const layout = getFeedLayout(width)
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
  ].filter(Boolean)

  return parts.join(' ')
}

export function signalsHeader(width: number): string {
  const layout = getSignalsLayout(width)
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
  ].filter(Boolean)

  return parts.join(' ')
}
