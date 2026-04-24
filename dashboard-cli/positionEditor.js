import { postApiJson } from './api.js';
export const editablePositionStatuses = ['open', 'waiting', 'win', 'lose', 'exit'];
export async function savePositionManualEdit(input) {
    const response = await postApiJson('/api/positions/manual-edit', input);
    if (response && response.ok === false) {
        throw new Error(String(response.message || 'Manual position edit failed.'));
    }
}
