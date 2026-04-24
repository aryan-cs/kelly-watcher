import { applyDashboardRuntimePatches } from './runtimePatches.js';
applyDashboardRuntimePatches();
await import('./dashboard.js');
