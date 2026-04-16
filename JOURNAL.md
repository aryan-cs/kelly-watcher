# Kelly Watcher Journal

Last updated: 2026-04-16 America/Chicago

You are one of 3 agents working on thsi codebase. Be sure to identify yourself for every entry and include all relevant information in what you do. This includes timestamps, summaries, etc. Make sure you do not overwrite someone else's work.

## Journal Entries
Add new entries below this line.

---
[2026-04-16 14:34 CDT] codex-docs
Task: Update README to reflect web-dashboard-only operator workflow.
Claims: `README.md`, `JOURNAL.md`
Status: Completed
Blockers: None
Next: Wait for follow-up docs or packaging requests; avoid runtime/discovery files currently owned by integration work.
Decisions: README now treats `dashboard-web` as the supported operator UI, removes terminal dashboard startup guidance, and documents browser/Tailscale access as the primary path.
Tests: None run; docs-only change

[2026-04-16 16:13 CT] codex-main
Task: Establish shared agent coordination and capture the implementation direction in one place.
Claims: `JOURNAL.md`
Status: In progress
Blockers: Other work already exists in the worktree; runtime DB integrity is unhealthy in the live environment.
Next: Implement DB-backed wallet membership, browser discovery management, and shadow-safe promotion flows.
Decisions: Wallet membership will move to SQLite as the canonical source of truth; the web dashboard is the operator surface; shadow mode only.
Tests: None yet

[2026-04-16 16:22 CT] codex-main
Task: Integration lead for DB-backed wallet registry, web operator flow, and discovery safety.
Claims: `JOURNAL.md`, integration/verification across runtime + dashboard changes
Status: In progress
Blockers: Worker slices still running; existing repo worktree is already dirty, so integration has to avoid trampling unrelated edits.
Next: Merge worker outputs, fill any remaining discovery/runtime gaps, run focused regression tests, and push a safe checkpoint branch.
Decisions: Created branch `codex/shadow-wallet-registry`; using `JOURNAL.md` as coordination source of truth for concurrent agents.
Tests: `uv run pytest tests/test_wallet_discovery.py tests/test_shadow_reset.py tests/test_runtime_fixes.py tests/test_db_recovery_api.py tests/test_watchlist_manager.py` -> 274 passed

[2026-04-16 14:36 CDT] codex-main
Task: Fail closed on stale/persisted `mode=live` while the project is supposed to remain shadow-safe, and align the web dashboard with that effective mode.
Claims: `src/kelly_watcher/main.py`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: In progress
Blockers: Local runtime ledger is still corrupt (`database disk image is malformed`), GitHub push still blocked by missing `gh` auth, and the worktree already contains other in-flight edits.
Next: Consider a follow-up web slice to expose configured-vs-effective mode more explicitly in the operational detail rows, then continue recovery/reset work on a clean ledger.
Decisions: Published bot-state `mode` should represent effective safe runtime mode, not merely configured intent. `configured_mode` is now tracked separately, and blocked live conditions force the published mode back to shadow with a `mode_block_reason`.
Tests: `uv run pytest tests/test_runtime_fixes.py -q -k 'base_bot_state_snapshot_clears_shadow_restart_state_by_default or write_bot_state_forces_shadow_mode_when_configured_live_is_blocked or dashboard_bot_state_snapshot_forces_shadow_mode_when_live_is_blocked_by_db_integrity'` -> 3 passed; `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit && uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed

[2026-04-16 16:33 CT] codex-main
Task: Re-read journal, verify shared state, and keep cross-agent coordination explicit.
Claims: `JOURNAL.md`, integration/verification across current runtime + dashboard edits
Status: In progress
Blockers: The worktree now shows active modifications in `src/kelly_watcher/dashboard_api.py`, `src/kelly_watcher/engine/watchlist_manager.py`, `src/kelly_watcher/main.py`, `src/kelly_watcher/runtime/tracker.py`, `src/kelly_watcher/shadow_reset.py`, `dashboard-web/src/*`, and related tests, but those changes are not yet described here by the other agents.
Next: Wait for worker completions, inspect diffs carefully, integrate the DB-backed wallet registry + discovery safety path, then rerun focused regression tests before any commit/push.
Decisions: Treat all currently modified runtime/dashboard files as potentially claimed until proven otherwise; avoid overwriting them blindly.
Tests: Live runtime DB integrity re-check still fails (`db_integrity_ok=False`), so wallet discovery/promotion must remain fail-closed against DB-health issues.

[2026-04-16 16:37 CT] codex-main
Task: Record completed browser/API slice for cross-agent visibility.
Claims: `JOURNAL.md`
Status: In progress
Blockers: Runtime/storage and discovery slices still need final integration before commit/push.
Next: Review the storage/runtime and discovery worker outputs, then rerun focused regression plus a browser build.
Decisions: Browser/API surface now includes `GET /api/wallets`, `GET /api/wallets/events`, `GET /api/discovery/candidates`, and `POST /api/discovery/scan`; responses fall back to derived DB snapshots when full managed-wallet tables do not exist yet, and all scan flows fail closed on DB-integrity problems.
Tests: Worker reported `uv run pytest tests/test_db_recovery_api.py tests/test_wallet_discovery.py` and `npm run build` in `dashboard-web`.

[2026-04-16 16:47 CT] codex-main
Task: Final integration verification after the storage/runtime, discovery, and browser/API slices all landed.
Claims: `JOURNAL.md`, integration/verification only
Status: Ready for checkpoint commit/push
Blockers: Live runtime DB is still malformed, so the real backend will remain fail-closed for discovery/promotion until DB recovery is done.
Next: Stage only the wallet-registry/discovery/web-flow files, commit a safe checkpoint on `codex/shadow-wallet-registry`, and push it.
Decisions: Kept the implementation shadow-only; managed-wallet startup paths now treat a missing `managed_wallets` table as an empty registry instead of crashing in mocked startup flows; browser build + focused regression are both green.
Tests: `uv run pytest tests/test_wallet_discovery.py tests/test_shadow_reset.py tests/test_runtime_fixes.py tests/test_db_recovery_api.py tests/test_watchlist_manager.py` -> 277 passed; `npm run build` in `dashboard-web` -> passed.
