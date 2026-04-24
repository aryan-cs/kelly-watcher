import {postApiJson} from './api.js'

export interface WalletActionResult {
  ok: boolean
  message: string
}

export async function reactivateDroppedWallet(walletAddress: string): Promise<WalletActionResult> {
  return postApiJson<WalletActionResult>('/api/wallets/reactivate', {walletAddress})
}

export async function dropTrackedWallet(walletAddress: string, reason = 'manual dashboard drop'): Promise<WalletActionResult> {
  return postApiJson<WalletActionResult>('/api/wallets/drop', {walletAddress, reason})
}
