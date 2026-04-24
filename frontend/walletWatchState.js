import { postApiJson } from './api.js';
export async function reactivateDroppedWallet(walletAddress) {
    return postApiJson('/api/wallets/reactivate', { walletAddress });
}
export async function dropTrackedWallet(walletAddress, reason = 'manual dashboard drop') {
    return postApiJson('/api/wallets/drop', { walletAddress, reason });
}
