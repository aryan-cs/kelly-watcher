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

[2026-04-16 14:39 CDT] codex-main
Task: Expose maintenance controls in the web dashboard now that browser is the only operator surface.
Claims: `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`, `JOURNAL.md`
Status: In progress
Blockers: Backend DB is still corrupt, so `Archive Trade Log` and some recovery flows are expected to fail closed until the ledger is repaired or reset. GitHub push still blocked by missing `gh` auth.
Next: Consider a follow-up readout panel for recovery candidate paths / archive cutoff timestamps if operators need more detail, then continue clean-ledger recovery work.
Decisions: Web dashboard now exposes `Restart Shadow`, `Recover DB`, and `Archive Trade Log` buttons directly in the Operational Status panel, reusing the backend’s existing fail-closed API responses rather than creating any new maintenance path.
Tests: `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 14:42 CDT] codex-main
Task: Make web maintenance controls state-aware so they fail closed before the operator clicks them.
Claims: `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`, `JOURNAL.md`
Status: In progress
Blockers: Active DB corruption still means `Recover DB` / `Archive Trade Log` will often be unavailable for real operational reasons; GitHub push still blocked by missing `gh` auth.
Next: If needed, add deeper maintenance detail such as recovery candidate path, latest verified backup time, and archive preserve/cutoff timestamps; then continue clean-ledger recovery work.
Decisions: The web dashboard now disables `Restart Shadow`, `Recover DB`, and `Archive Trade Log` when backend state already implies they will fail, and it renders per-action helper text using existing bot-state fields (`startup_blocked`, `shadow_restart_pending`, `db_integrity_*`, `db_recovery_candidate_*`, `trade_log_archive_*`) instead of inventing new backend logic.
Tests: `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 14:45 CDT] codex-main
Task: Surface deeper maintenance detail in the web dashboard so blocked recovery/archive states are diagnosable without leaving the browser.
Claims: `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`, `JOURNAL.md`
Status: In progress
Blockers: Runtime DB remains corrupt, so the newly surfaced recovery candidate and archive detail currently describe blocked/unavailable states rather than a usable path. GitHub push still blocked by missing `gh` auth.
Next: If needed, add a dedicated recovery/backup subsection with full paths or copy actions; otherwise continue on clean-ledger recovery and post-epoch evidence.
Decisions: The web dashboard now surfaces configured-vs-effective mode, mode override reason, recovery candidate/source filenames, latest verified backup timestamp, and archive window/result details using backend fields that already existed. This keeps the browser as a complete operator surface instead of forcing shell inspection for maintenance context.
Tests: `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 16:47 CT] codex-main
Task: Final integration verification after the storage/runtime, discovery, and browser/API slices all landed.
Claims: `JOURNAL.md`, integration/verification only
Status: Ready for checkpoint commit/push
Blockers: Live runtime DB is still malformed, so the real backend will remain fail-closed for discovery/promotion until DB recovery is done.
Next: Stage only the wallet-registry/discovery/web-flow files, commit a safe checkpoint on `codex/shadow-wallet-registry`, and push it.
Decisions: Kept the implementation shadow-only; managed-wallet startup paths now treat a missing `managed_wallets` table as an empty registry instead of crashing in mocked startup flows; browser build + focused regression are both green.
Tests: `uv run pytest tests/test_wallet_discovery.py tests/test_shadow_reset.py tests/test_runtime_fixes.py tests/test_db_recovery_api.py tests/test_watchlist_manager.py` -> 277 passed; `npm run build` in `dashboard-web` -> passed.

[2026-04-16 16:50 CT] codex-main
Task: Record the verified remote checkpoint for the shadow wallet-registry/discovery work.
Claims: `JOURNAL.md`
Status: Checkpoint pushed
Blockers: Live DB integrity is still the main operational blocker before discovery/promotion can be trusted outside fail-closed mode.
Next: Either continue with DB recovery hardening or let the other agents branch off this checkpoint.
Decisions: Committed only the DB-backed wallet registry, discovery, browser/operator flow, README, and relevant tests; intentionally left unrelated `dashboard-cli`, DB-recovery, and trade-log-archive worktree changes unstaged.
Tests: Final staged verification passed: `uv run pytest tests/test_wallet_discovery.py tests/test_shadow_reset.py tests/test_runtime_fixes.py tests/test_db_recovery_api.py tests/test_watchlist_manager.py` -> 277 passed; `npm run build` -> passed.

[2026-04-16 17:01 CT] codex-main
Task: Verify and package the remaining browser-only maintenance controls that were still local after the prior push.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`
Status: In progress
Blockers: Runtime DB is still malformed in the live environment, so `Recover DB` and archive actions are expected to fail closed until recovery succeeds.
Next: Commit/push the web maintenance-controls slice, then continue on DB-recovery and post-promotion shadow-evidence improvements.
Decisions: The supported operator workflow should include shadow restart, DB recovery, and trade-log archive controls directly in the browser because terminal-only operations are no longer the supported path.
Tests: `uv run pytest tests/test_dashboard_web_source.py tests/test_db_recovery_api.py -q` -> passed; `npm run build` in `dashboard-web` -> passed.

[2026-04-16 14:58 CDT] codex-main
Task: Complete the shadow-only safety fix for live-mode shutdown and expose it in the web dashboard.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: Hot SQLite ledger is still malformed, so this only fixes the control plane; it does not make the runtime evidence trustworthy or recovery candidate available.
Next: Use the new web `Set Shadow-Only` control if `configured_mode=live`, then continue with actual `Recover DB` / `Shadow Reset` and clean post-epoch evidence collection.
Decisions: `USE_REAL_MONEY=false` writes are now treated as a safety action that stays available during `startup_blocked`, recovery-only startup, shadow-restart-pending, and DB-integrity failure. The browser now exposes `Set Shadow-Only` via `POST /api/live-mode` so operators can clear a stale live config without leaving the web dashboard.
Tests: `uv run pytest tests/test_runtime_fixes.py -q -k 'set_live_mode_response_blocks_when_bot_state_is_stale or set_live_mode_response_blocks_when_db_integrity_fails or set_live_mode_response_blocks_when_startup_is_blocked or set_live_mode_response_disables_live_mode_without_readiness_check or set_live_mode_response_blocks_disable_while_shadow_restart_pending or set_live_mode_response_blocks_disable_while_startup_is_blocked or set_live_mode_response_disables_live_mode_while_db_integrity_fails or dashboard_bot_state_snapshot_forces_shadow_mode_when_live_is_blocked_by_db_integrity'` -> 8 passed; `uv run pytest tests/test_live_mode_api_gate.py -q` -> 6 passed; `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit && uv run pytest tests/test_dashboard_web_source.py -q && npm run build` -> passed

[2026-04-16 14:48 CDT] codex-main
Task: Let operators force the persisted config back to shadow-only from the web even while the runtime is blocked, and stop treating that safety action like a risky mutation.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: In progress
Blockers: The hot SQLite ledger is still malformed, so the runtime remains fail-closed and GitHub push is still blocked by missing `gh` auth.
Next: Allow `USE_REAL_MONEY=false` writes during blocked/restart states, add a browser `Set Shadow-Only` / `Live OFF` control, then rerun focused backend + web verification.
Decisions: Turning live off is a safety action, not a shadow mutation. The web dashboard already shows configured-vs-effective mode truthfully, but it still needs a direct browser control to clear a stale `configured_mode=live`.
Tests: Pending

[2026-04-16 17:18 CT] codex-main
Task: Finish the recovery control-plane slice so the browser reflects DB-backed wallet provenance and shadow-reset wallet handling correctly.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `src/kelly_watcher/shadow_reset.py`, `dashboard-web/src/App.tsx`, `dashboard-web/src/styles.css`, `dashboard-web/src/api.ts`, `tests/test_runtime_fixes.py`, `tests/test_shadow_reset.py`, `tests/test_dashboard_web_source.py`
Status: In progress
Blockers: Runtime DB integrity is still bad in the live environment, so all recovery/promotion paths must remain fail-closed even after this UX/control-plane cleanup.
Next: Verify the config/reset semantics, run focused pytest + web build, then commit/push this slice if it is clean.
Decisions: Browser config should distinguish live DB-backed wallets from legacy env bootstrap values, and shadow reset should never fall back to stale env wallets after the registry migration.
Tests: Pending

[2026-04-16 15:10 CDT] codex-main
Task: Add recovery inventory so the browser can show every retained verified-backup candidate that was checked, not just a single unavailable/ready summary.
Claims: `JOURNAL.md`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/main.py`, `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_db_recovery.py`, `tests/test_dashboard_web_source.py`
Status: In progress
Blockers: Runtime DB is still malformed, and startup-blocked mode means the browser may need to rely on persisted or fallback recovery state until the service is re-established on a clean base.
Next: Extend `db_recovery_state()` with checked-candidate inventory, persist the new fields into bot state, then render a browser recovery inventory summary with per-candidate status and file names.
Decisions: The next useful operator feature is visibility, not another blind retry. Recovery should explain which retained backups were checked, which one is selected, and why the rest failed.
Tests: Pending

[2026-04-16 15:24 CDT] codex-main
Task: Complete recovery inventory and make stale browser state fall back to a cached live recovery snapshot.
Claims: `JOURNAL.md`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/main.py`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_db_recovery.py`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: The hot runtime ledger is still malformed, so this improves recovery visibility and candidate consistency only; it does not repair the DB or prove profitability.
Next: Use the browser to inspect the now-published recovery inventory, then run `Recover DB` or `Shadow Reset` onto a clean base and continue with post-epoch routed evidence collection.
Decisions: `db_recovery_state()` now publishes per-candidate recovery inventory and deterministic backup ordering based on the timestamp embedded in retained backup filenames instead of mutable filesystem `mtime`. `dashboard_api._bot_state_snapshot()` now backfills missing recovery inventory and selected-candidate fields from a short-lived cached recovery snapshot so the browser stays truthful even while startup is blocked.
Tests: `uv run pytest tests/test_db_recovery.py -q` -> 10 passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'verified_backup_history_paths_prefer_filename_timestamp_over_mtime or recovery_state_ or base_bot_state_snapshot_clears_shadow_restart_state_by_default or dashboard_bot_state_snapshot_falls_back_to_cached_recovery_inventory_when_missing or dashboard_bot_state_snapshot_forces_shadow_mode_when_live_is_blocked_by_db_integrity'` -> 8 passed; `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit && uv run pytest tests/test_dashboard_web_source.py -q && npm run build` -> passed; direct snapshot probe now reports `candidate_ready=True`, `candidate_mode=integrity_only`, `inventory_count=1`.

[2026-04-16 15:37 CDT] codex-main
Task: Normalize startup validation failures into a first-class blocked state for the browser, and label them explicitly as startup failures instead of generic blocking.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: This makes the unhealthy state more honest, but the hot SQLite ledger is still malformed and startup is still actually failing until DB recovery/reset and config cleanup happen.
Next: Use the browser with the clearer startup-failure state to recover/reset the DB and fix invalid config, then continue on clean post-epoch routed evidence.
Decisions: `dashboard_api._bot_state_snapshot()` now treats `startup_failed` / `startup_validation_failed` as blocked even when the persisted file forgot to set `startup_blocked=true`. The browser now types and surfaces `startup_failed`, `startup_validation_failed`, and `startup_failure_message`, and it labels the operational state as `Startup Failed` / `Validation Failed` instead of a generic `Blocked`.
Tests: `uv run pytest tests/test_runtime_fixes.py -q -k 'dashboard_bot_state_snapshot_marks_startup_validation_failure_as_blocked or dashboard_bot_state_snapshot_forces_shadow_mode_when_live_is_blocked_by_db_integrity or dashboard_bot_state_snapshot_falls_back_to_cached_recovery_inventory_when_missing or base_bot_state_snapshot_clears_shadow_restart_state_by_default'` -> 4 passed; `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit && uv run pytest tests/test_dashboard_web_source.py -q && npm run build` -> passed; direct snapshot probe now reports `startup_blocked=True`, `startup_failed=True`, `startup_validation_failed=True`.

[2026-04-16 17:33 CT] codex-main
Task: Land the recovery control-plane cleanup and lock down remaining env-backed wallet fallbacks.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `src/kelly_watcher/main.py`, `src/kelly_watcher/shadow_reset.py`, `dashboard-web/src/App.tsx`, `dashboard-web/src/api.ts`, `dashboard-web/src/styles.css`, `tests/test_runtime_fixes.py`, `tests/test_shadow_reset.py`, `tests/test_dashboard_web_source.py`
Status: Ready for checkpoint commit/push
Blockers: Live runtime DB is still malformed, so discovery/promotion remain intentionally fail-closed until DB recovery succeeds. Unrelated `dashboard-cli/*`, `tests/test_db_recovery.py`, and `tests/test_trade_log_archive.py` work is still in the worktree and must stay out of this checkpoint.
Next: Stage only the recovery-control-plane files, commit/push, then move on to wallet-specific post-promotion shadow evidence and probation reporting.
Decisions: `WATCHED_WALLETS` is now treated as bootstrap-only everywhere in the browser config path; runtime wallet loading no longer falls back to env when the DB registry is empty or unavailable; startup bootstrap import now depends on total managed-wallet registry count instead of active-wallet count; shadow reset no longer repopulates wallets from stale env values when the DB registry cannot be read.
Tests: `uv run pytest tests/test_runtime_fixes.py tests/test_shadow_reset.py tests/test_dashboard_web_source.py tests/test_db_recovery_api.py -q` -> 266 passed, 63 subtests passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 17:34 CT] codex-main
Task: Capture parallel audit findings from the other agents for the next implementation slice.
Claims: `JOURNAL.md`
Status: Completed
Blockers: None
Next: Use these findings to scope the next shadow-safety pass after the current checkpoint push.
Decisions: Galileo found one remaining class of env fallback that is now fixed locally in `main.py`/`dashboard_api.py`. Linnaeus confirmed the next highest-value gap is wallet-specific post-promotion evidence: per-wallet since-promotion copied trades/PnL/skip-rate, a promotion-aware probation ramp in trust/watchlist logic, and dashboard visibility into whether auto-promoted wallets are actually proving themselves in shadow mode.
Tests: Audit only; no direct tests

[2026-04-16 17:38 CT] codex-main
Task: Publish the recovery-control-plane checkpoint for the rest of the team.
Claims: `JOURNAL.md`
Status: Pushed
Blockers: Live DB corruption still blocks trustworthy discovery/promotion in the real environment until recovery succeeds.
Next: Start the wallet post-promotion evidence/probation slice from commit `edb9fe9`.
Decisions: Pushed `codex/shadow-wallet-registry` at commit `edb9fe9` after staging only the browser recovery controls, bootstrap-only wallet semantics, runtime env-fallback removal, and focused regression updates. Left unrelated `dashboard-cli/*`, `tests/test_db_recovery.py`, and `tests/test_trade_log_archive.py` changes untouched.
Tests: Same verification as the prior entry; no new code changes after push

[2026-04-16 17:47 CT] codex-main
Task: Implement wallet-specific post-promotion shadow evidence and probation plumbing for auto-promoted wallets.
Claims: `JOURNAL.md`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/runtime/wallet_discovery.py`, `src/kelly_watcher/engine/wallet_trust.py`, `src/kelly_watcher/engine/watchlist_manager.py`, `src/kelly_watcher/dashboard_api.py`, `tests/test_wallet_discovery.py`, `tests/test_watchlist_manager.py`, `tests/test_db_recovery_api.py`, `tests/test_runtime_fixes.py`
Status: In progress
Blockers: `src/kelly_watcher/main.py` is currently dirty in the shared worktree, so this slice should avoid adding more `main.py` churn unless absolutely necessary. Live DB corruption still means the feature must stay fail-closed and shadow-only.
Next: Inspect promotion metadata/trade tables, design a DB-backed “since promoted” evidence snapshot, wire it into wallet/trust/watchlist APIs, then run focused regression tests and push a checkpoint.
Decisions: Prefer DB/API/watchlist integration over more control-plane work. Keep this slice centered on “did auto-promoted wallets actually help in shadow mode?” rather than broad new discovery heuristics.
Tests: Pending

[2026-04-16 15:45 CDT] codex-main
Task: Surface startup validation diagnostics in the web dashboard as actionable issue lines instead of one generic blocked/failure sentence.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`
Status: In progress
Blockers: Startup is still genuinely unhealthy until DB recovery/reset and config cleanup happen; this tranche improves visibility only.
Next: Parse startup failure text into issue/warning lines, render them in the existing Operational Status panel, then rerun focused web verification.
Decisions: Keep this slice browser-only. The API already carries enough startup failure text; the web needs to present it as actionable diagnostics rather than a single long reason string.
Tests: Pending

[2026-04-16 15:11 CDT] codex-main
Task: Finish the browser-only blocked-state diagnostics pass so query-backed web panels stop looking like ordinary empty datasets during startup/recovery failures.
Claims: `JOURNAL.md`, `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: This is a visibility-only improvement; the hot SQLite ledger is still malformed and startup is still genuinely blocked until DB recovery/reset and config cleanup happen.
Next: Continue with the wallet post-promotion evidence/probation slice already in progress, or use the web dashboard to run `Recover DB` / `Shadow Reset` on a clean base.
Decisions: Added `startup_detail` to the web bot-state contract so the browser can fall back to the full startup diagnostic payload if shorter message fields ever drift. Wallet Registry, Discovery, and Membership Timeline now fail closed with `...queries are paused:` copy during startup-blocked, recovery-only, pending-restart, or DB-integrity-failed states instead of rendering normal empty-state text.
Tests: `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 15:18 CDT] codex-main
Task: Surface wallet trust/probation state next to post-promotion evidence in the web-managed wallet view.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: This improves operator visibility only; the hot SQLite ledger is still malformed, so recovery/reset and fresh post-epoch routed evidence are still the real blockers before any profitability claims.
Next: Continue with the broader wallet post-promotion evidence/probation slice already in progress, ideally by adding aggregate wallet probation reporting and promotion outcomes once the clean-ledger path is available.
Decisions: Managed-wallet rows now include cached trust snapshots (`trust_tier`, `trust_size_multiplier`, `trust_note`) so the browser can show whether an auto-promoted wallet is still in promotion probation versus genuinely trusted. The web wallet cards now display trust tier and current effective sizing alongside the existing post-promotion copied-trade evidence. While wiring that in, focused regression exposed a real bug in `_managed_wallet_rows()` where the `managed_wallets` branch built SQL without `SELECT`; fixed that query directly.
Tests: `uv run pytest tests/test_runtime_fixes.py -q -k 'managed_wallet_rows_include_post_promotion_shadow_evidence'` -> 1 passed; `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 15:20 CDT] codex-main
Task: Add aggregate promotion/probation reporting to the browser wallet registry so operators can see whether promoted wallets are actually clearing proof.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: Browser-only visibility slice; the hot SQLite ledger is still malformed, so these summaries help interpretation but do not create trustworthy evidence by themselves.
Next: Continue with promotion outcomes and aggregate wallet probation reporting on the backend once the clean-ledger path is available, or use the browser to recover/reset and start a fresh evidence epoch.
Decisions: The wallet registry panel now summarizes four operator-facing promotion metrics directly from the existing managed-wallet payload: total auto-promoted wallets, wallets still awaiting proof, wallets whose post-promotion evidence is ready, and a blocker summary with the leading probation notes. This keeps the browser aligned with the “is auto-promotion actually helping?” question without adding more backend contract churn.
Tests: `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 15:23 CDT] codex-main
Task: Make the new browser promotion/probation summary fail closed during startup/recovery/DB-integrity blocks.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: Visibility-only slice; runtime DB corruption still means the underlying evidence is not trustworthy until recovery/reset succeeds.
Next: Continue with backend promotion-outcome reporting once the clean-ledger path is available, or keep tightening browser/operator honesty around blocked states as new summary panels are added.
Decisions: The aggregate wallet promotion rows (`Auto-Promoted`, `Awaiting Proof`, `Evidence Ready`, `Probation Blockers`) now reuse the same blocked-state reason as the wallet registry panel itself. That prevents the browser from saying “no promoted wallets” when the real state is “wallet queries are paused because startup/recovery is unhealthy.”
Tests: `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 15:24 CDT] codex-main
Task: Harden post-promotion shadow evidence so auto-promoted wallets only clear probation on truly post-promotion, copyable behavior; fix a managed-wallet query regression; fail closed on unreadable wallet-registry resets.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `src/kelly_watcher/engine/wallet_trust.py`, `src/kelly_watcher/engine/watchlist_manager.py`, `src/kelly_watcher/shadow_reset.py`, `tests/test_wallet_trust.py`, `tests/test_watchlist_manager.py`, `tests/test_shadow_reset.py`, `tests/test_runtime_fixes.py`
Status: Completed
Blockers: Live runtime DB integrity is still unhealthy, so discovery/promotion remain shadow-only and fail-closed. One additional runtime gap found by audit is still open in `src/kelly_watcher/main.py::_refresh_managed_wallet_registry`: an empty DB-backed registry does not yet clear the in-memory tracker/watchlist set, and I intentionally left `main.py` alone because it is already dirty in the shared tree.
Next: Coordinate the `main.py` empty-registry fail-open fix with the agent owning that file, then keep tightening wallet-level shadow evidence/reporting once the runtime is on a clean ledger again.
Decisions: Post-promotion evidence now uses trade placement time instead of resolution time, so pre-promotion trades that resolve later cannot count as post-promotion proof. Promotion probation now requires a composite gate, not just resolved copied fills: enough post-promotion buy opportunities, enough post-promotion copied fills, and an acceptable post-promotion uncopyable skip rate. The managed-wallet dashboard query bug in `_managed_wallet_rows()` is fixed. `shadow_reset.apply_wallet_mode_for_reset()` now fails closed when the DB-backed managed wallet registry cannot be read, instead of snapshotting an empty registry and risking destructive resets.
Tests: `python -m py_compile src/kelly_watcher/dashboard_api.py src/kelly_watcher/engine/wallet_trust.py src/kelly_watcher/engine/watchlist_manager.py src/kelly_watcher/shadow_reset.py tests/test_wallet_trust.py tests/test_watchlist_manager.py tests/test_shadow_reset.py` -> passed; `uv run pytest tests/test_wallet_trust.py tests/test_watchlist_manager.py tests/test_shadow_reset.py tests/test_runtime_fixes.py -q` -> 283 passed; `uv run pytest tests/test_wallet_trust.py tests/test_watchlist_manager.py tests/test_wallet_discovery.py tests/test_runtime_fixes.py tests/test_dashboard_web_source.py tests/test_shadow_reset.py -q` -> 287 passed; `npm run build` in `dashboard-web` -> passed
