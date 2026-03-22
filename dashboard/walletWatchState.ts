import {postApiJson} from './api.js'

interface WalletActionResult {
  ok: boolean
  message: string
}

export async function reactivateDroppedWallet(walletAddress: string): Promise<boolean> {
  const response = await postApiJson<WalletActionResult>('/api/wallets/reactivate', {walletAddress})
  return Boolean(response.ok)
}

export async function dropTrackedWallet(walletAddress: string, reason = 'manual dashboard drop'): Promise<boolean> {
  const response = await postApiJson<WalletActionResult>('/api/wallets/drop', {walletAddress, reason})
  return Boolean(response.ok)
}
