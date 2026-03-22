import { postApiJson } from './api.js';
export async function requestManualTrade(input) {
    return postApiJson('/api/manual-trade', input);
}
