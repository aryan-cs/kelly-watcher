import { postApiJson } from './api.js';
export async function reactivateDroppedWallet(walletAddress) {
    const response = await postApiJson('/api/wallets/reactivate', { walletAddress });
    return Boolean(response.ok);
}
export async function dropTrackedWallet(walletAddress, reason = 'manual dashboard drop') {
    const response = await postApiJson('/api/wallets/drop', { walletAddress, reason });
    return Boolean(response.ok);
}
