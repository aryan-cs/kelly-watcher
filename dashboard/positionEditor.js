import { postApiJson } from './api.js';
export const editablePositionStatuses = ['open', 'waiting', 'win', 'lose', 'exit'];
export async function savePositionManualEdit(input) {
    await postApiJson('/api/positions/manual-edit', input);
}
