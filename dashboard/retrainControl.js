import { postApiJson } from './api.js';
export async function requestManualRetrain() {
    return postApiJson('/api/manual-retrain');
}
