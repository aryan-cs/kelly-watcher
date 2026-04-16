# Kelly Watcher Journal

Last updated: 2026-04-16 America/Chicago

You are one of 3 agents working on thsi codebase. Be sure to identify yourself for every entry and include all relevant information in what you do. This includes timestamps, summaries, etc. Make sure you do not overwrite someone else's work.

## Journal Entries
Add new entries below this line.

---
[2026-04-16 17:37 CDT] codex-main
Task: Surface wallet-family evidence in the operator daily report so the recent family metrics are visible without opening raw shadow-state payloads.
Claims: `JOURNAL.md`, `src/kelly_watcher/runtime/evaluator.py`, `tests/test_segment_shadow_report.py`
Status: Completed
Blockers: Reporting is still downstream of an unhealthy runtime. The active ledger remains corrupt, recovery is still only `integrity_only`, and there is still no clean post-epoch routed evidence to justify live expectations.
Next: Use the new daily-report visibility to measure whether the recent family-aware gates and probation clamp actually improve family-level shadow outcomes before adding any more family-specific engine rules.
Decisions: `daily_report()` now emits a first-class `wallet families:` line whenever `compute_segment_shadow_report()` has non-empty family history. The line reuses existing fields only: `wallet_family_history_status`, `wallet_family_coverage_pct`, and `wallet_family_summary`. I deliberately kept this backend-only and stayed out of the active `dashboard-web` rewrite.
Notes for other agents: The operator alert now carries wallet-family coverage and top-family P&L summaries. If you touch daily-report formatting, preserve this line unless you replace it with a richer but equally honest report surface. The next family tranche should be measurement-driven, not another blind threshold.
Tests: `uv run python -m py_compile src/kelly_watcher/runtime/evaluator.py tests/test_segment_shadow_report.py` -> pending; `uv run pytest tests/test_segment_shadow_report.py -q -k 'daily_report_uses_epoch_scoped_shadow_reporting_and_segment_scope or report_summarizes_wallet_family_coverage_from_decision_context'` -> pending; `git diff --check -- JOURNAL.md src/kelly_watcher/runtime/evaluator.py tests/test_segment_shadow_report.py` -> pending

[2026-04-16 17:41 CDT] codex-ui
Task: Keep dashboard table rows single-line by preventing wrapped market/reason text and widening the `Signals` reason column.
Claims: `JOURNAL.md`, `dashboard-web/src/styles.css`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: None.
Next: Continue tightening table proportions page by page, but keep row wrapping disabled so the dashboard reads like a dense market terminal rather than a document layout.
Decisions: Long market names should truncate with ellipsis instead of increasing row height. I switched market links back to explicit single-line overflow handling and widened the `Signals` reason column from the previous narrow setting so more explanation text fits before truncation. The result keeps every visible row to one line while preserving compact density.
Tests: `uv run pytest tests/test_dashboard_web_source.py -q` -> pending; `npm run build` in `dashboard-web` -> pending

[2026-04-16 17:36 CDT] codex-ui
Task: Port the web `Signals` page to the same semantic-table layout used on `Tracker`, while keeping the CLI columns, values, and color rules.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `dashboard-web/src/mockDashboard.ts`, `dashboard-web/src/signalsFeed.tsx`, `dashboard-web/src/styles.css`, `dashboard-web/src/feedUtils.ts`, `dashboard-web/src/trackerFeed.tsx`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: None.
Next: Continue page-by-page UI work locally in `dashboard-web` and keep using the shared feed utilities for any event-table pages instead of duplicating polling and formatting logic.
Decisions: The `Signals` page now uses a semantic HTML table with reusable column definitions, browser-native sizing, scrollable overflow, and spacing that fits the formal Bloomberg-like direction. It preserves the CLI column set (`ID`, `TIME`, `USERNAME`, `MARKET`, `ACTN`, `SIDE`, `PRICE`, `SHARES`, `TOTAL`, `DEC`, `CONF`, `REASON`), resolves missing signal actions from the incoming feed, and keeps the old decision/probability/outcome color rules. I also aligned the source test with the shared `feedUtils.ts` polling refactor so the assertions match the real file boundaries.
Tests: `uv run pytest tests/test_dashboard_web_source.py -q` -> 8 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 17:23 CDT] codex-main
Task: Tighten the wallet-family sizing path so post-promotion probation wallets cannot receive quality-based or family-based size boosts before they have actually earned post-promotion evidence.
Claims: `JOURNAL.md`, `src/kelly_watcher/engine/wallet_trust.py`, `tests/test_wallet_trust.py`
Status: Completed
Blockers: This is still an engine-only conservatism slice. The hot ledger remains corrupt, recovery is still only `integrity_only`, and we still do not have clean post-epoch routed evidence to justify live expectations.
Next: Measure whether the recent family-aware gates plus this probation clamp actually improve post-promotion copied outcomes. If not, stop stacking new family rules and wait for clean evidence before adding more complexity.
Decisions: `apply_wallet_trust_sizing()` now caps both quality uplift and family uplift at `100%` whenever `post_promotion_baseline_at > 0` but `post_promotion_evidence_ready` is still false. That means a promoted wallet in probation can still be downscaled, but it cannot be scaled up just because its local quality score is high or because a future family rule would otherwise boost it. I also added an explicit operator note in `wallet_trust_note` so this cap is visible in downstream runtime metadata.
Notes for other agents: This is the narrower sizing-only rule one audit recommended. It intentionally does not touch confidence floors, signal acceptance, or training. If a future tranche revisits family-aware adaptive floors, keep this cap in place for promotion probation.
Tests: `uv run python -m py_compile src/kelly_watcher/engine/wallet_trust.py tests/test_wallet_trust.py` -> passed; `uv run pytest tests/test_wallet_trust.py -q -k 'post_promotion_probation_caps_quality_and_family_uplifts or family_multiplier_scales_trusted_wallet_size or quality_multiplier_scales_trusted_wallet_size_within_cap or quality_multiplier_respects_hard_max_size_cap'` -> 4 passed; `git diff --check -- src/kelly_watcher/engine/wallet_trust.py tests/test_wallet_trust.py` -> passed

[2026-04-16 17:15 CDT] codex-main
Task: Add the next family-aware engine hook by tightening adaptive confidence floors for drag-prone wallet families before the rest of the entry pipeline runs.
Claims: `JOURNAL.md`, `src/kelly_watcher/engine/wallet_trust.py`, `src/kelly_watcher/engine/adaptive_confidence.py`, `tests/test_wallet_trust.py`, `tests/test_adaptive_confidence_floor.py`
Status: Completed
Blockers: This is still a conservative decision-quality slice only. The hot ledger remains corrupt, recovery is still only `integrity_only`, and we still do not have clean post-epoch routed evidence. One audit argued for keeping the next tranche sizing-only; I chose the narrower adaptive-floor version because it is still bounded by the existing confidence-floor clamp and does not require schema or UI work.
Next: Measure whether the new family-aware admission tightening actually improves family-level shadow outcomes. If it does, the next engine tranche can either extend family-aware adaptive floors slightly or add stricter family-aware probation in sizing. If it does not, revert to sizing-only family rules and avoid stacking more family-aware gates.
Decisions: `wallet_trust.py` now exposes `wallet_family_confidence_floor_uplift()`, deliberately narrower than the post-slippage edge uplift: `liquidity_sensitive` +0.010, `timing_sensitive` / `thin_edge` / `promotion_proof` +0.005, and `core` / `scalable` unchanged. `adaptive_confidence.py` now applies that uplift after the existing bucket and local-return logic and before the existing clamp, so drag-prone families need slightly higher confidence to pass the heuristic/model admission floor while still staying bounded by `MAX_RAISE`. Notes for other agents: I did not widen this to every family and I did not let any family lower the floor. This is intentionally asymmetric and conservative.
Tests: `uv run python -m py_compile src/kelly_watcher/engine/wallet_trust.py src/kelly_watcher/engine/adaptive_confidence.py tests/test_wallet_trust.py tests/test_adaptive_confidence_floor.py` -> passed; `uv run pytest tests/test_wallet_trust.py tests/test_adaptive_confidence_floor.py tests/test_expected_return_model.py -q` -> 46 passed; `git diff --check -- src/kelly_watcher/engine/wallet_trust.py src/kelly_watcher/engine/adaptive_confidence.py tests/test_wallet_trust.py tests/test_adaptive_confidence_floor.py` -> passed

[2026-04-16 17:08 CDT] codex-main
Task: Add the first family-aware runtime admission rule so drag-prone wallet families need more net edge after slippage before an XGBoost trade is allowed through.
Claims: `JOURNAL.md`, `src/kelly_watcher/engine/wallet_trust.py`, `src/kelly_watcher/main.py`, `tests/test_wallet_trust.py`, `tests/test_runtime_fixes.py`
Status: Completed
Blockers: This improves decision conservatism only. The hot ledger is still corrupt, recovery is still only `integrity_only`, and we still do not have clean post-epoch routed evidence to justify live expectations.
Next: If we continue along this path, the next engine-side tranche should probably be family-aware adaptive confidence floors in `adaptive_confidence.py`, but only after we observe whether this narrower post-slippage gate materially improves family-level shadow outcomes.
Decisions: I chose the narrowest safe engine hook instead of touching global model thresholds. `wallet_trust.py` now exposes a conservative `wallet_family_edge_threshold_uplift()` mapping: `liquidity_sensitive` +2.0 points of required edge, `timing_sensitive` +1.5 points, and `thin_edge` / `promotion_proof` +1.0 point. `main._model_fill_edge_block_reason()` now applies that uplift on top of the model’s base edge threshold after slippage, using the already persisted `signal["wallet_trust"]["family"]`. That means drag-prone families now need more post-slippage edge before entry survives, while `core` / `scalable` remain unchanged. Notes for other agents: I intentionally did not touch `adaptive_confidence.py` yet; this tranche is a narrower runtime gate and should be measured first against the new family reporting before we stack more family-aware decision rules.
Tests: `uv run python -m py_compile src/kelly_watcher/engine/wallet_trust.py src/kelly_watcher/main.py tests/test_wallet_trust.py tests/test_runtime_fixes.py` -> passed; `uv run pytest tests/test_wallet_trust.py tests/test_runtime_fixes.py -q -k 'wallet_family_edge_threshold_uplift or model_fill_edge_block_reason_uses_wallet_family_edge_uplift or model_fill_edge_block_reason_allows_stronger_edge_for_draggy_wallet_family or managed_wallet_rows_include_post_promotion_shadow_evidence'` -> 4 passed; `git diff --check -- src/kelly_watcher/engine/wallet_trust.py src/kelly_watcher/main.py tests/test_wallet_trust.py tests/test_runtime_fixes.py` -> passed

[2026-04-16 16:52 CDT] codex-ui
Task: Replace the CLI-style character grid on the web `Tracker` page with a proper responsive web table.
Claims: `JOURNAL.md`, `dashboard-web/src/trackerFeed.tsx`, `dashboard-web/src/styles.css`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: None.
Next: Keep iterating on visual polish and the remaining pages locally under the semantic-table structure instead of reverting to terminal-width math.
Decisions: The `Tracker` page should use browser-native layout instead of terminal width math. It now uses a semantic `<table>` with a reusable column definition, natural browser sizing, column padding, scrollable overflow, and tabular-number styling. The UI no longer depends on a hardcoded table width or `ch`-based grid template.
Tests: `uv run pytest tests/test_dashboard_web_source.py -q` -> 6 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 16:58 CDT] codex-main
Task: Close the remaining backend evidence bypass by forcing all training entry paths to honor the active routed post-epoch provenance contract, including direct `train(df=...)` callers.
Claims: `JOURNAL.md`, `src/kelly_watcher/research/train.py`, `tests/test_training_data_contract.py`
Status: Completed
Blockers: This hardens provenance only. The hot ledger is still corrupt, recovery is still only `integrity_only`, and we still do not have clean post-epoch routed shadow evidence to justify live expectations.
Next: With training provenance now hardened end to end, the next engine-side step should stay on read-only family/probation measurement or conservative family thresholds, not model proliferation. Operationally, the real next step is still recover/reset onto a clean ledger and collect fresh routed evidence.
Decisions: `train.py` now validates the active evidence contract even when a caller provides a preloaded dataframe. If there is no active evidence epoch, if `training_routed_only` is false, or if `training_since_ts` does not match the current effective evidence window, training now skips with explicit untrusted provenance instead of silently trusting the caller’s rows. This closes the last easy path for internal callers to train on non-clean or mismatched history while still allowing the normal auto-retrain path to proceed with the active routed scope.
Notes for other agents: Do not work around this by passing ad hoc dataframes without the current active evidence window. If a future training flow truly needs alternate scope, it should add an explicit reviewed contract instead of bypassing `train.py`.
Tests: `uv run python -m py_compile src/kelly_watcher/research/train.py tests/test_training_data_contract.py` -> passed; `uv run pytest tests/test_training_data_contract.py tests/test_retrain_runs.py tests/test_expected_return_model.py -q` -> 47 passed; `git diff --check -- src/kelly_watcher/research/train.py tests/test_training_data_contract.py` -> passed

[2026-04-16 16:43 CDT] codex-ui
Task: Apply uppercase presentation across the web UI while keeping the browser tab title lowercase `kelly-watcher`.
Claims: `JOURNAL.md`, `dashboard-web/index.html`, `dashboard-web/src/styles.css`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: None.
Next: Keep iterating on layout/content under this typography rule; tab title can stay lowercase unless product naming changes again.
Decisions: This is presentation-only. The browser UI now renders uppercase through CSS, while the document title remains literal lowercase in `index.html`.
Tests: `uv run pytest tests/test_dashboard_web_source.py -q` -> 6 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 16:46 CDT] codex-main
Task: Add the first read-only wallet-family evidence/reporting tranche so the new family sizing layer can be measured before any schema or training changes.
Claims: `JOURNAL.md`, `src/kelly_watcher/runtime/evaluator.py`, `src/kelly_watcher/runtime/performance_preview.py`, `src/kelly_watcher/main.py`, `tests/test_segment_shadow_report.py`, `tests/test_routed_shadow_evidence.py`, `tests/test_telegram_commands.py`
Status: Completed
Blockers: The hot ledger is still corrupt, startup is still blocked, and recovery is still only `integrity_only`, so these family metrics are for shadow-side attribution only and do not make the system investable yet. I also avoided browser wiring because `dashboard-web/src/App.tsx` is currently under an active mock-shell rewrite by `codex-ui`.
Next: Use these family summaries to decide whether the wallet-family bootstrap is producing real differentiated outcomes. The next safe engine step is per-family thresholds / calibration / probation reporting on clean post-epoch routed evidence, not a schema migration or per-family training fork yet.
Decisions: I kept this tranche read-only and JSON-derived. `compute_segment_shadow_report()` now derives wallet-family attribution from `decision_context_json.signal.wallet_trust.family`, publishes `wallet_families`, family coverage, unassigned-family counts, and a compact family summary without touching `trade_log` schema. `compute_tracker_preview_summary()` and `/balance` rendering now expose the same family coverage/history and a compact top-family line so operators can see whether family attribution is mostly classified or mostly still unassigned. `main._segment_shadow_state_payload()` now persists the family summary alongside the segment summary for future API/UI use. Notes for other agents: do not add new `trade_log` columns for wallet families yet; the existing decision-context path is enough for the first proof tranche. Also do not hang new browser features off the current `dashboard-web/src/App.tsx` until the mock-shell rewrite settles.
Tests: `uv run python -m py_compile src/kelly_watcher/runtime/evaluator.py src/kelly_watcher/runtime/performance_preview.py src/kelly_watcher/main.py tests/test_segment_shadow_report.py tests/test_routed_shadow_evidence.py tests/test_telegram_commands.py` -> passed; `uv run pytest tests/test_segment_shadow_report.py tests/test_routed_shadow_evidence.py tests/test_telegram_commands.py -q` -> 27 passed; `uv run pytest tests/test_wallet_trust.py tests/test_runtime_fixes.py -q -k 'managed_wallet_rows_include_post_promotion_shadow_evidence'` -> 1 passed; `git diff --check -- JOURNAL.md src/kelly_watcher/runtime/evaluator.py src/kelly_watcher/runtime/performance_preview.py src/kelly_watcher/main.py tests/test_segment_shadow_report.py tests/test_routed_shadow_evidence.py tests/test_telegram_commands.py` -> passed

[2026-04-16 16:39 CDT] codex-ui
Task: Port the old React Ink incoming-trades feed into the web `Tracker` page.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `dashboard-web/src/mockDashboard.ts`, `dashboard-web/src/trackerFeed.tsx`, `dashboard-web/src/styles.css`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: None. This stays local/mock-first and does not depend on the broken live backend state.
Next: Refresh the local Vite page and assess the feed visually. After approval, continue porting the next CLI page into the same shell instead of pushing piecemeal.
Decisions: The `Tracker` page now renders a compact fixed-width terminal ledger using the same feed columns as the CLI: `ID`, `TIME`, `USERNAME`, `MARKET`, `ACTN`, `SIDE`, `PRICE`, `SHARES`, `PAID`, `TO WIN`, `PROFIT`. It preserves the old feed’s action/side/probability/P&L color rules, polls `/api/events?max=250` in API mode, and uses local template incoming-trade data in mock mode.
Tests: `uv run pytest tests/test_dashboard_web_source.py -q` -> 5 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 16:33 CDT] codex-ui
Task: Align the web shell with the old React Ink dashboard page map and make the nav visually compact, formal, and terminal-like.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `dashboard-web/src/mockDashboard.ts`, `dashboard-web/src/styles.css`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: None beyond the intentional reset of the prior browser UI; this is a visual/navigation-only slice on top of the new mock-mode workflow.
Next: Keep iterating locally on page layout/content under this shell; do not push until the page bodies are ready to land together.
Decisions: The web shell now mirrors the React Ink CLI page map exactly: `Tracker`, `Signals`, `Perf`, `Models`, `Wallets`, `Config`. The brand text is `kelly-watcher`, and the nav styling has been flattened into a compact strip with square separators instead of rounded chip-like controls.
Tests: `uv run pytest tests/test_dashboard_web_source.py -q` -> 4 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 16:29 CDT] codex-main
Task: Start the first engine-side wallet-family layer after the backend planning pass, while keeping the tranche safe for shadow-only operation and not colliding with the active web rewrite in the shared worktree.
Claims: `JOURNAL.md`, `src/kelly_watcher/engine/wallet_trust.py`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/api.ts`, `tests/test_wallet_trust.py`, `tests/test_runtime_fixes.py`
Status: Completed
Blockers: The live ledger is still corrupt, recovery is still only `integrity_only`, and the current shared-worktree `dashboard-web/src/App.tsx` has been replaced by a minimal mock shell that no longer matches the earlier operational dashboard. I therefore kept this slice backend/engine-only and avoided more browser wiring to prevent trampling another agent’s front-end rewrite.
Next: Once the browser implementation stabilizes again, surface the new wallet-family fields there. Engine-side next step is to use these families for per-family thresholds / calibration / probation, but only after clean post-epoch routed evidence exists on a healthy ledger.
Decisions: Added a conservative heuristic wallet-family bootstrap inside `wallet_trust.py` rather than pretending we already have learned clustering. Trusted wallets can now be tagged as `scalable`, `core`, `thin_edge`, `timing_sensitive`, `liquidity_sensitive`, `promotion_proof`, `developing`, or `emerging` based on local copied returns, post-promotion proof state, and execution-drag composition. Family multipliers are bounded and only materially change sizing for trusted wallets with strong clean evidence (`scalable` +10%) or clear execution drag / thin-edge patterns (`timing_sensitive` -10%, `liquidity_sensitive` -15%, `thin_edge` -10%). `dashboard_api` now carries `wallet_family`, `wallet_family_multiplier`, and `wallet_family_note` through managed-wallet rows so the operator surface can consume them once the shared front-end settles.
Notes for other agents: I deliberately did not touch `dashboard-web/src/App.tsx` further because it no longer matches the large operational dashboard file that earlier tests and journal entries referenced; it is currently a tiny mock shell importing `mockDashboard`. If that rewrite is intentional, wire the new `wallet_family*` API fields into the new UI there instead of reviving the older browser layout accidentally.
Tests: `uv run python -m py_compile src/kelly_watcher/engine/wallet_trust.py src/kelly_watcher/dashboard_api.py tests/test_wallet_trust.py tests/test_runtime_fixes.py tests/test_db_recovery_request_flow.py` -> passed; `uv run pytest tests/test_wallet_trust.py -q` -> 16 passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'managed_wallet_rows_include_post_promotion_shadow_evidence'` -> 1 passed; `uv run pytest tests/test_db_recovery_request_flow.py -q -k 'launch_allows_recovery_while_startup_is_recovery_only'` -> 1 passed; `node ./node_modules/typescript/bin/tsc -p tsconfig.app.json --noEmit` -> passed

[2026-04-16 16:31 CDT] codex-ui
Task: Repair the local dashboard dev commands so `npm run dev` and related scripts do not fail on broken `node_modules/.bin/*` permissions.
Claims: `JOURNAL.md`, `dashboard-web/package.json`
Status: Completed
Blockers: The local `dashboard-web/node_modules/.bin/vite` and `dashboard-web/node_modules/.bin/tsc` files are present but not executable (`-rw-------`), so shell-based npm scripts fail before the dashboard can start.
Next: Keep using `npm run dev` / `npm run build` normally; the scripts now bypass the broken shim executables and should stay stable across this checkout and the Windows box.
Decisions: Script entrypoints should not rely on filesystem execute bits. `dashboard-web/package.json` now calls `node ./node_modules/vite/bin/vite.js` and `node ./node_modules/typescript/bin/tsc` directly for `dev`, `build`, and `preview`.
Tests: `npm run build` in `dashboard-web` -> passed; `npm run dev` in `dashboard-web` -> Vite started and served `http://localhost:5173/`; `curl -I http://127.0.0.1:5173` -> `HTTP/1.1 200 OK`

[2026-04-16 16:24 CDT] codex-ui
Task: Create a local mock-data frontend workflow so we can iterate on the web dashboard on macOS without repeatedly pushing/pulling to the Windows backend.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `dashboard-web/src/mockDashboard.ts`, `dashboard-web/src/styles.css`, `tests/test_dashboard_web_source.py`, `README.md`
Status: Completed
Blockers: `dashboard-web/src/App.tsx` and `dashboard-web/src/styles.css` already contained active in-flight operator UI, so this slice intentionally resets the browser to a minimal shell rather than preserving the current dashboard panels.
Next: Use `cd dashboard-web && npm run dev` locally for design iteration; do not push to Windows until the UI shell is approved and ready to land as one batch.
Decisions: Local `npm run dev` is now the fast design loop. The frontend defaults to mock/template data during Vite development, the browser shell is reset to a solid `#0f0f0f` background with a top nav only, and the docs now require `VITE_DASHBOARD_DATA_MODE=api` when developers want the Vite server to talk to a real backend.
Tests: `uv run pytest tests/test_dashboard_web_source.py -q` -> 4 passed; `node ./node_modules/typescript/bin/tsc -p tsconfig.app.json --noEmit` -> passed; `node ./node_modules/typescript/bin/tsc -p tsconfig.node.json --noEmit` -> passed; `node ./node_modules/vite/bin/vite.js build` -> passed

[2026-04-16 16:21 CDT] codex-main
Task: Write the next full implementation plan before more code churn: finish backend/control-plane trust first, then move into clustered engine work aimed at profit maximization under real execution constraints.
Claims: `JOURNAL.md`
Status: Completed
Blockers: The hot ledger is still corrupt, recovery is still only `integrity_only`, and we still do not have clean post-epoch routed shadow evidence. GitHub push remains blocked locally because `gh auth status` still reports no logged-in host.
Next: Execute Phase 1 immediately: keep backend state truthfully shadow-only, keep recovery/reset reachable from the browser during blocked startup, then recover/reset onto a clean ledger before doing any profit-seeking engine promotion.
Decisions: We are not treating “maximize profits” as a license for optimistic shortcuts. The optimization target stays net expected P&L after fees, slippage, latency, and missed-fill risk. Engine work should only proceed on trustworthy post-epoch routed evidence, and clustered wallet families should be added as policy/model families on top of the fixed segment router rather than replacing it prematurely.
Plan:
- Phase 1 — Backend/control-plane truth: keep `mode` effectively shadow whenever startup is blocked, recovery-only, restart-pending, or DB-integrity-failed; keep `configured_mode` separate; let live-disable stay available; keep recovery/reset reachable even while startup is blocked.
- Phase 2 — Clean evidence base: recover or reset onto a clean ledger; preserve archive/storage integrity; block replay/training/readiness on legacy-contaminated, untrusted, or pre-promotion mixed data.
- Phase 3 — Execution-realism economics: keep quote-vs-fill separation; size on net edge after fees, slippage, latency, and fill uncertainty; treat actual fills as audit/execution data, not signal features.
- Phase 4 — Wallet-family clustering: keep fixed segment routing as the outer router, then add wallet-family / cluster policies only once enough clean post-promotion evidence exists; sparse or unstable clusters fall back to the safest fixed-segment policy.
- Phase 5 — Per-cluster policies and calibration: move toward per-cluster thresholds, calibration, trust ramps, and probation instead of one global XGBoost policy for every wallet; require minimum routed resolved counts and time-split holdouts before cluster promotion.
- Phase 6 — Promotion and readiness gates: promote only when post-promotion copied results are positive after costs and slippage; judge readiness using profit factor, expectancy, drawdown, calibration error, and fill-drift metrics, not just win rate.
Notes for other agents: I asked Copernicus to audit backend prerequisites and Socrates to audit the clustered-engine direction. Their common conclusion was: do not build cluster-specific intelligence on top of a lying control plane or mixed-history evidence. The next backend tranche should stay focused on recovery/reset reachability and shadow-only truth; the next engine tranche should define wallet-family clustering as a second-layer policy family with strict clean-evidence gates.
Tests: Planning-only slice; no code tests run.

[2026-04-16 16:16 CDT] codex-main
Task: Add a browser-only config repair path for startup-blocking settings and make the repair flow explicit about stale startup diagnostics versus current stored values.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/App.tsx`, `dashboard-web/src/api.ts`, `dashboard-web/src/styles.css`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: This repairs the browser/operator path only. The hot ledger is still corrupt, recovery is still only `integrity_only`, and we still do not have clean post-epoch routed shadow evidence.
Next: Use the new browser controls to clear bad overrides and/or stale live config, then `Restart Shadow` to re-run startup validation. After that, the real operational next step is still `Recover DB` or `Shadow Reset` onto a clean ledger.
Decisions: `/api/config/value` and the new `/api/config/clear` now stay available during startup-blocked or restart-pending states so bad runtime settings can be repaired from the web dashboard. `USE_REAL_MONEY` still delegates through the existing live-mode API, and `WATCHED_WALLETS` remains bootstrap-only. The browser now polls `/api/config`, surfaces startup-linked keys from the last failed startup, and makes it explicit that the rows show current stored values while the diagnostics come from the last failed boot; operators are told to restart shadow after a repair to confirm validation is clean. Note for the other agents: `dashboard-web/node_modules/.bin/tsc` is not executable in this checkout, so web verification must use `node ./node_modules/typescript/bin/tsc ...` and `node ./node_modules/vite/bin/vite.js build` unless someone fixes the file mode.
Tests: `uv run python -m py_compile src/kelly_watcher/dashboard_api.py tests/test_runtime_fixes.py` -> passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'config_value_response_blocks_watched_wallets_edit_when_registry_is_db_backed or config_value_response_blocks_watched_wallets_edit_even_when_registry_is_unavailable or config_value_response_allows_repair_while_shadow_restart_pending or config_value_response_allows_repair_while_startup_is_blocked or config_clear_response_deletes_runtime_setting_while_startup_is_blocked or config_clear_response_still_delegates_live_mode_keys or config_value_response_still_delegates_live_mode_keys'` -> 7 passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `node ./node_modules/typescript/bin/tsc -p tsconfig.app.json --noEmit` -> passed; `node ./node_modules/typescript/bin/tsc -p tsconfig.node.json --noEmit` -> passed; `node ./node_modules/vite/bin/vite.js build` -> passed

[2026-04-16 16:06 CDT] codex-main
Task: Make persisted startup failure state honest on disk so `bot_state.json` no longer says `startup_failed=true` while `startup_blocked=false`.
Claims: `JOURNAL.md`, `src/kelly_watcher/main.py`, `tests/test_runtime_fixes.py`
Status: Completed
Blockers: This fixes observability/control-plane truthfulness only. The runtime ledger is still corrupt and the recovery candidate is still only `integrity_only`, so the project remains non-investable.
Next: Keep the web dashboard and persisted bot state aligned, then focus on actual clean-ledger recovery / shadow reset and fresh post-epoch routed evidence. Browser paused-query copy already exists for wallet/discovery/timeline panels, so avoid more `dashboard-web/src/App.tsx` churn on that front.
Decisions: `_write_bot_state()` now normalizes any startup failure or validation failure into `startup_blocked=true` before computing effective mode, and it backfills `startup_block_reason` from the failure/validation message or detail if needed. `_effective_runtime_mode()` now also treats startup failure/validation failure as a live-blocking condition directly, so a stale `configured_mode=live` cannot leak through if startup has already failed. `_persist_startup_failure_state()` now persists blocked startup state directly instead of relying on `dashboard_api._bot_state_snapshot()` to repair it on read.
Tests: `uv run python -m py_compile src/kelly_watcher/main.py tests/test_runtime_fixes.py` -> passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'write_bot_state_marks_startup_failure_as_blocked_on_disk or write_bot_state_forces_shadow_mode_when_configured_live_is_blocked or persist_startup_validation_failure_writes_state_even_when_runtime_getters_are_broken or persist_startup_validation_failure_clears_stale_prior_session_state or persist_startup_failure_rehydrates_durable_persisted_history or validate_startup_persists_failure_state_before_raising or main_persists_late_startup_failure_after_dashboard_server_starts or dashboard_bot_state_snapshot_marks_startup_validation_failure_as_blocked'` -> 8 passed

[2026-04-16 16:03 CDT] codex-main
Task: Audit and clean incorrect filesystem junk after concern about a nested `kelly-watcher` folder inside the repo root.
Claims: `JOURNAL.md`
Status: Completed
Blockers: None for this cleanup slice. The package layout itself remains intentional and should not be deleted.
Next: Leave `src/kelly_watcher` alone unless we explicitly choose a repo-wide Python package refactor; continue on runtime/recovery/evidence work instead.
Decisions: Audited the tree and confirmed there is no literal `src/kelly-watcher` directory in this checkout. The only package directory under `src/` is `src/kelly_watcher`, which matches `pyproject.toml` (`packages = ["src/kelly_watcher"]`) and all Python import paths, so deleting or renaming it would be a breaking refactor rather than cleanup. Removed actual junk/generated filesystem artifacts instead: `.DS_Store`, `__pycache__`, and stale `dashboard-web/dist`.
Tests: Tree verification only: `find src tests dashboard-web dashboard-cli -type d -name '__pycache__' -o -name '.DS_Store'` -> empty; `test -d dashboard-web/dist && echo exists || echo missing` -> `missing`; `python - <<'PY' ... Path('src').iterdir()` -> only `kelly_watcher`

[2026-04-16 16:00 CDT] codex-main
Task: Split quote-price decisioning from fill-price economics in the entry/replay/training path, and fix the managed-wallet bootstrap crash seen in startup logs.
Claims: `JOURNAL.md`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/env_profile.py`, `src/kelly_watcher/engine/kelly.py`, `src/kelly_watcher/main.py`, `src/kelly_watcher/research/replay.py`, `src/kelly_watcher/research/train.py`, `src/kelly_watcher/runtime/executor.py`, `tests/test_kelly.py`, `tests/test_replay.py`, `tests/test_training_data_contract.py`, `tests/test_runtime_fixes.py`, `tests/test_managed_wallet_bootstrap.py`
Status: Completed
Blockers: Live SQLite ledger is still malformed, recovery is still only `integrity_only`, and GitHub push is still blocked locally because `gh auth status` reports no logged-in host.
Next: Use the web dashboard to recover/reset onto a clean ledger, then collect fresh post-epoch routed shadow evidence. Follow-up backend work should split execution drag further into timing vs liquidity causes and keep browser recovery state honest.
Decisions: XGBoost sizing is now quote-anchored while fill economics stay as a post-sizing feasibility/slippage check; replay now keeps quote price separate from effective fill price; training `effective_price` now uses `price_at_signal` instead of falling back to actual fill; logged `expected_edge` stays on the quote side while fill costs remain in the dedicated fill-cost fields. Also restored `src/kelly_watcher/env_profile.py` because the file had been deleted in the shared worktree while imports still depended on it, which was breaking test collection unrelated to this tranche. The screenshot-reported startup crash came from `upsert_managed_wallets()` supplying 4 values to a 5-placeholder `wallet_watch_state` insert; that path is now fixed and covered.
Tests: `uv run python -m py_compile src/kelly_watcher/env_profile.py src/kelly_watcher/data/db.py src/kelly_watcher/engine/kelly.py src/kelly_watcher/main.py src/kelly_watcher/research/replay.py src/kelly_watcher/research/train.py src/kelly_watcher/runtime/executor.py tests/test_kelly.py tests/test_replay.py tests/test_training_data_contract.py tests/test_runtime_fixes.py tests/test_managed_wallet_bootstrap.py` -> passed; `uv run pytest tests/test_kelly.py tests/test_replay.py tests/test_training_data_contract.py tests/test_runtime_fixes.py tests/test_managed_wallet_bootstrap.py -q` -> 274 passed

[2026-04-16 15:58 CDT] codex-config
Task: Collapse env profiles into one `.env` and move non-secret operator settings to SQLite-backed runtime settings.
Claims: `JOURNAL.md`, `src/kelly_watcher/env_profile.py`, `src/kelly_watcher/config.py`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/dashboard_api.py`, `src/kelly_watcher/main.py`, `src/kelly_watcher/shadow_reset.py`, `src/kelly_watcher/cli.py`, `.env.example`, `README.md`, `tests/test_env_profile_and_save_layout.py`, `tests/test_runtime_fixes.py`
Status: In progress
Blockers: Shared worktree is already dirty and `main.py` / `dashboard_api.py` are active integration points, so changes must stay scoped to config persistence rather than wallet-discovery logic.
Next: Add a `runtime_settings` table + migration helper, strip non-secret values out of the persistent env file, make config reads prefer DB for non-secret keys, and update docs/tests for the single-env workflow.
Decisions: Keep `.env` for secrets/deployment knobs only; treat operator/runtime tuning as DB-backed; migrate legacy `WATCHED_WALLETS` into the managed-wallet registry instead of keeping it in `.env`.
Tests: Pending

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

[2026-04-16 15:25 CDT] codex-main
Task: Extend the browser wallet registry from promotion status into promotion outcomes.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: Browser-only reporting slice; the hot SQLite ledger is still malformed, so these outcome metrics remain observational until recovery/reset succeeds and new clean evidence accumulates.
Next: Continue with backend promotion outcome reporting and clean-ledger shadow evidence collection, or keep refining browser/operator reporting around post-promotion wallet performance if new payload fields land.
Decisions: The wallet registry summary now includes `Ready Rate`, `Copy Quality`, and `Post-Promotion P&L` in addition to the earlier promotion/probation rows. These are derived entirely from the existing managed-wallet payload, weighted by resolved copied trades where appropriate, and they also fail closed under the same blocked-state reason as the wallet panel itself.
Tests: `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 18:07 CDT] codex-main
Task: Patch execution-path realism gaps called out in the latest review: pre-execution source-latency veto, shadow buy book-walk spend preservation, and fee-aware replay sizing where skipped rows still rely on quoted entry price.
Claims: `JOURNAL.md`, `src/kelly_watcher/runtime/executor.py`, `src/kelly_watcher/main.py`, `src/kelly_watcher/config.py`, `.env.example`, `src/kelly_watcher/research/replay.py`, `tests/test_runtime_fixes.py`, `tests/test_replay.py`, `tests/test_kelly.py`
Status: Completed
Blockers: `src/kelly_watcher/main.py` is already dirty in the shared worktree, so edits need to stay narrowly scoped around `process_event` / manual entry and avoid the managed-wallet refresh area. Hot runtime DB is still malformed, so verification will rely on focused unit/regression tests plus read-only SQL samples where possible.
Next: If another agent touches runtime/operator controls next, reuse the new `entry_latency_block_reason()` helper instead of hand-rolling source-age checks. The next higher-value backend slice is still clean-ledger recovery plus evidence collection, not more UI polish.
Decisions: The original audit was partially stale: current execution economics, skipped counterfactual labels, and the training contract already have fee-aware paths. This tranche therefore targeted the real remaining leaks: shadow-buy cost reporting, replay sizing on skipped fee-enabled rows, and the missing pre-execution latency veto. Added `MAX_SOURCE_LATENCY` as a separate operator knob instead of overloading `MAX_SOURCE_TRADE_AGE`, since one is an intake filter and the other is an order-placement safety gate.
Tests: `uv run python -m py_compile src/kelly_watcher/config.py src/kelly_watcher/runtime/executor.py src/kelly_watcher/main.py src/kelly_watcher/research/replay.py tests/test_kelly.py tests/test_replay.py tests/test_runtime_fixes.py` -> passed; `uv run pytest tests/test_kelly.py tests/test_replay.py -q` -> 19 passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'shadow_buy_simulation_preserves_walked_book_spend_with_tolerance or entry_latency_block_reason_uses_source_latency_limit or live_order_response_fill_overrides_book_estimate_for_entries or log_trade_persists_entry_fee_breakdown'` -> 4 passed; `uv run pytest tests/test_live_mode_api_gate.py tests/test_dashboard_web_source.py -q` -> 8 passed

[2026-04-16 15:27 CDT] codex-main
Task: Extend browser promotion outcomes with active-vs-dropped coverage and execution drag.
Claims: `JOURNAL.md`, `dashboard-web/src/App.tsx`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: Browser-only reporting slice; runtime DB corruption still means these outcome metrics are descriptive rather than investable evidence until recovery/reset succeeds.
Next: If backend payload churn is acceptable later, split execution drag into timing vs liquidity causes using the already-persisted post-promotion skip counters; otherwise keep the next slice on clean-ledger recovery and fresh post-epoch routed evidence.
Decisions: The wallet registry summary now adds `Active Promoted` and `Execution Drag` rows. `Active Promoted` shows how many promoted wallets are still active versus dropped, and `Execution Drag` shows weighted post-promotion uncopyable skip rate plus raw skipped/total buy counts. Both rows fail closed under the same blocked-state reason as the rest of the wallet promotion summary.
Tests: `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 15:29 CDT] codex-main
Task: Split post-promotion execution drag into timing vs liquidity causes in the browser wallet outcomes.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: This is still operator visibility, not investable evidence; runtime DB corruption and startup failure remain the real blockers.
Next: For the other agents: if you touch runtime summaries or wallet policy telemetry next, consider exposing the same timing/liquidity split beyond the web wallet registry so we can compare execution drag across browser and backend status surfaces without re-deriving it.
Decisions: Managed-wallet API rows now expose `post_promotion_timing_skips` and `post_promotion_liquidity_skips`, and the browser wallet registry summary now adds a `Drag Causes` row showing timing vs liquidity counts and percentages across promoted wallets. The new row reuses the same blocked-state guard as the rest of the promotion summary, so it fails closed when wallet queries are paused.
Tests: `python -m py_compile src/kelly_watcher/dashboard_api.py tests/test_runtime_fixes.py tests/test_dashboard_web_source.py` -> passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'managed_wallet_rows_include_post_promotion_shadow_evidence'` -> 1 passed; `./dashboard-web/node_modules/.bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 15:24 CDT] codex-main
Task: Harden post-promotion shadow evidence so auto-promoted wallets only clear probation on truly post-promotion, copyable behavior; fix a managed-wallet query regression; fail closed on unreadable wallet-registry resets.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `src/kelly_watcher/engine/wallet_trust.py`, `src/kelly_watcher/engine/watchlist_manager.py`, `src/kelly_watcher/shadow_reset.py`, `tests/test_wallet_trust.py`, `tests/test_watchlist_manager.py`, `tests/test_shadow_reset.py`, `tests/test_runtime_fixes.py`
Status: Completed
Blockers: Live runtime DB integrity is still unhealthy, so discovery/promotion remain shadow-only and fail-closed. One additional runtime gap found by audit is still open in `src/kelly_watcher/main.py::_refresh_managed_wallet_registry`: an empty DB-backed registry does not yet clear the in-memory tracker/watchlist set, and I intentionally left `main.py` alone because it is already dirty in the shared tree.
Next: Coordinate the `main.py` empty-registry fail-open fix with the agent owning that file, then keep tightening wallet-level shadow evidence/reporting once the runtime is on a clean ledger again.
Decisions: Post-promotion evidence now uses trade placement time instead of resolution time, so pre-promotion trades that resolve later cannot count as post-promotion proof. Promotion probation now requires a composite gate, not just resolved copied fills: enough post-promotion buy opportunities, enough post-promotion copied fills, and an acceptable post-promotion uncopyable skip rate. The managed-wallet dashboard query bug in `_managed_wallet_rows()` is fixed. `shadow_reset.apply_wallet_mode_for_reset()` now fails closed when the DB-backed managed wallet registry cannot be read, instead of snapshotting an empty registry and risking destructive resets.
Tests: `python -m py_compile src/kelly_watcher/dashboard_api.py src/kelly_watcher/engine/wallet_trust.py src/kelly_watcher/engine/watchlist_manager.py src/kelly_watcher/shadow_reset.py tests/test_wallet_trust.py tests/test_watchlist_manager.py tests/test_shadow_reset.py` -> passed; `uv run pytest tests/test_wallet_trust.py tests/test_watchlist_manager.py tests/test_shadow_reset.py tests/test_runtime_fixes.py -q` -> 283 passed; `uv run pytest tests/test_wallet_trust.py tests/test_watchlist_manager.py tests/test_wallet_discovery.py tests/test_runtime_fixes.py tests/test_dashboard_web_source.py tests/test_shadow_reset.py -q` -> 287 passed; `npm run build` in `dashboard-web` -> passed

[2026-04-16 15:28 CDT] codex-main
Task: Patch the remaining runtime fail-open gap so an empty DB-backed managed-wallet registry clears the in-memory tracker/watchlist instead of leaving stale wallets active.
Claims: `JOURNAL.md`, `src/kelly_watcher/main.py`, `tests/test_runtime_fixes.py`
Status: In progress
Blockers: `src/kelly_watcher/main.py` is already dirty in the shared tree from another slice, so this patch must stay tightly scoped to `_refresh_managed_wallet_registry` and a focused test.
Next: Inspect the existing refresh logic, make the empty-registry branch update runtime state instead of returning early, rerun focused runtime regression, and push another checkpoint if the patch is clean.
Decisions: Narrowing the claim to one function in `main.py` to avoid trampling unrelated recovery/control-plane edits already in flight elsewhere.
Tests: Pending

[2026-04-16 15:32 CDT] codex-main
Task: Complete the empty-registry runtime clear-on-sync fix.
Claims: `JOURNAL.md`, `src/kelly_watcher/main.py`, `tests/test_runtime_fixes.py`
Status: Completed
Blockers: Live DB corruption still blocks trustworthy shadow evidence collection and discovery/promotion remain fail-closed. I intentionally kept `main.py` edits limited to `_refresh_managed_wallet_registry`; unrelated recovery inventory work already present in that file remains untouched.
Next: The next highest-value wallet-tracking slice after this is a fresh post-promotion baseline regression for reactivated wallets so they do not inherit stale proof windows across drop/reactivate cycles.
Decisions: `_refresh_managed_wallet_registry` no longer returns early on `[]`. If the DB-backed managed-wallet registry becomes empty after startup, the runtime now clears `runtime_wallets`, `watchlist.replace_wallets([])`, `tracker.replace_wallets([])`, refreshes trader cache for `[]`, and persists the new empty registry state. Added a `main.main()`-level regression that captures the `managed_wallet_registry_sync` scheduler callback and proves that an empty registry actively clears stale runtime wallets instead of leaving them polled.
Tests: `python -m py_compile src/kelly_watcher/main.py tests/test_runtime_fixes.py` -> passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'registry_sync_clears_runtime_wallets_when_managed_registry_becomes_empty or managed_wallet_rows_include_post_promotion_shadow_evidence or runtime_managed_wallets_does_not_fallback_to_bootstrap_env'` -> 3 passed; `uv run pytest tests/test_runtime_fixes.py -q` -> 236 passed

[2026-04-16 15:36 CDT] codex-main
Task: Make wallet reactivation a fresh post-promotion proof boundary and keep the operator audit trail in sync.
Claims: `JOURNAL.md`, `src/kelly_watcher/engine/watchlist_manager.py`, `src/kelly_watcher/dashboard_api.py`, `tests/test_watchlist_manager.py`, `tests/test_wallet_trust.py`, `tests/test_runtime_fixes.py`
Status: In progress
Blockers: Need to keep this slice scoped away from unrelated browser/runtime work already dirty in the tree.
Next: Add regressions proving that auto-promoted wallets do not inherit stale proof across drop/reactivate cycles, and wire reactivation events into the DB-backed timeline if the current path is missing them.
Decisions: Treat reactivation as a first-class lifecycle boundary, not just a status toggle.
Tests: Pending

[2026-04-16 19:47 CDT] codex-main
Task: Finish the fresh-proof-window slice for reactivation and close the restore/provenance gap in shadow reset.
Claims: `JOURNAL.md`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/shadow_reset.py`, `src/kelly_watcher/engine/watchlist_manager.py`, `src/kelly_watcher/dashboard_api.py`, `tests/test_watchlist_manager.py`, `tests/test_wallet_trust.py`, `tests/test_runtime_fixes.py`, `tests/test_shadow_reset.py`
Status: Completed
Blockers: Unrelated browser and dashboard-cli work is still dirty in the shared tree, so commit/staging must stay scoped to this runtime/storage/test slice only. Live DB corruption is still the operational blocker before shadow evidence is trustworthy enough for any live discussion.
Next: Highest-value follow-up is wallet-level post-restore / post-reactivation evidence reporting in the browser so operators can see exactly which promoted wallets are still in a fresh proof window after reset/reactivation.
Decisions: Reactivation is now a durable lifecycle boundary, not just a watch-state toggle. Both `watchlist_manager.reactivate_wallet()` and `dashboard_api._reactivate_wallet()` now write `wallet_membership_events` entries with explicit `baseline_at`. Shadow-reset wallet snapshots now preserve registry provenance, promotion metadata, and disabled state instead of only wallet addresses, and restore replays that metadata back into `managed_wallets` while still forcing a fresh restore boundary for auto-promoted wallets.
Tests: `python -m py_compile src/kelly_watcher/data/db.py src/kelly_watcher/shadow_reset.py src/kelly_watcher/engine/watchlist_manager.py src/kelly_watcher/dashboard_api.py tests/test_watchlist_manager.py tests/test_wallet_trust.py tests/test_runtime_fixes.py tests/test_shadow_reset.py` -> passed; `uv run pytest tests/test_watchlist_manager.py -q -k 'recently_auto_promoted_wallet_is_protected_from_stale_uncopyable_drop_history or auto_promoted_wallet_can_drop_on_bad_post_promotion_local_results or reactivation_resets_post_promotion_evidence_window'` -> 3 passed; `uv run pytest tests/test_wallet_trust.py -q -k 'reactivation_resets_post_promotion_trust_window or auto_promoted_wallet_stays_in_promotion_probation_until_post_promotion_history_exists'` -> 2 passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'dashboard_reactivate_wallet_records_membership_boundary_event'` -> 1 passed; `uv run pytest tests/test_shadow_reset.py -q -k 'restore_managed_wallet_registry_snapshot_preserves_provenance_and_disabled_state or run_keep_active_wallets_writes_snapshot_before_restart or apply_wallet_mode_for_reset_fails_closed_when_registry_load_fails'` -> 3 passed; `uv run pytest tests/test_watchlist_manager.py tests/test_wallet_trust.py tests/test_runtime_fixes.py tests/test_shadow_reset.py -q` -> 290 passed

[2026-04-16 20:02 CDT] codex-main
Task: Surface fresh-proof-window provenance in the managed-wallet browser flow.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: In progress
Blockers: `dashboard_api.py`, `dashboard-web/src/App.tsx`, `dashboard-web/src/api.ts`, and `tests/test_runtime_fixes.py` are already dirty in the shared tree from other slices, so edits and staging need to stay surgical.
Next: Add boundary provenance fields (`promote` / `reactivate` / `restore`) to managed-wallet payloads, render them on wallet cards, and lock the contract with focused backend/browser source tests.
Decisions: Keep the slice small and high-signal. Operators mainly need to know why a wallet is in a fresh proof window and when that window started, not a full new dashboard section.
Tests: Pending

[2026-04-16 20:15 CDT] codex-main
Task: Complete the fresh-proof-window provenance UI slice for managed wallets.
Claims: `JOURNAL.md`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/api.ts`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: Shared worktree is still dirty in these files from unrelated slices, so commit/staging must remain patch-scoped. Live DB corruption remains the main operational blocker before shadow returns are meaningful enough to discuss live switching.
Next: Highest-value follow-up is richer browser reporting for post-restore/post-reactivation cohorts: counts and filters for wallets whose proof windows were reset by shadow reset versus manual/operator reactivation.
Decisions: Managed-wallet rows now surface both the original auto-promotion time (`post_promotion_promoted_at`) and the current proof-window provenance (`post_promotion_boundary_action/source/reason`). Wallet cards now distinguish `promoted`, `proof reset by reactivation`, and `proof reset by shadow reset`, and when a wallet has been reset after its original promotion they also show the original `auto-promoted ...` age so operators can see both facts at once.
Tests: `python -m py_compile src/kelly_watcher/dashboard_api.py tests/test_runtime_fixes.py tests/test_dashboard_web_source.py` -> passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'managed_wallet_rows_include_post_promotion_shadow_evidence or dashboard_reactivate_wallet_records_membership_boundary_event or managed_wallet_rows_surface_restore_proof_window_boundary'` -> 3 passed; `node ./dashboard-web/node_modules/typescript/bin/tsc -p dashboard-web/tsconfig.app.json --noEmit && uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `node ./node_modules/typescript/bin/tsc -p tsconfig.app.json --noEmit && node ./node_modules/typescript/bin/tsc -p tsconfig.node.json --noEmit && node ./node_modules/vite/bin/vite.js build` in `dashboard-web` -> passed

[2026-04-16 20:38 CDT] codex-main
Task: Fail closed when the canonical managed-wallet registry is missing or unreadable.
Claims: `JOURNAL.md`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/main.py`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: In progress
Blockers: `main.py` and `dashboard_api.py` are shared integration points, so the slice needs to stay narrow: bootstrap gating and wallet-registry reporting only.
Next: Prevent bootstrap import after snapshot-restore failure or unknown registry state, remove the `wallet_watch_state` fallback from the managed-wallet dashboard view, and make the browser surface the blocked canonical-registry message directly.
Decisions: In shadow mode we should rather show “canonical registry unavailable” than synthesize a wallet inventory from legacy watch-state rows. Startup bootstrap from `WATCHED_WALLETS` should only happen when the DB-backed registry is explicitly available and known-empty.
Tests: Pending

[2026-04-16 20:47 CDT] codex-main
Task: Complete the managed-wallet fail-closed safety slice.
Claims: `JOURNAL.md`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/main.py`, `src/kelly_watcher/dashboard_api.py`, `dashboard-web/src/App.tsx`, `tests/test_runtime_fixes.py`, `tests/test_dashboard_web_source.py`
Status: Completed
Blockers: Live DB integrity is still the larger operational blocker; this slice makes the system more honest, not magically evidence-ready.
Next: Highest-value follow-up is a tighter distinction between “managed_wallets table missing” and “managed_wallets table exists but registry contents are empty/corrupt,” ideally surfaced in bot-state so the browser can show canonical-registry health without inferring from endpoint payloads.
Decisions: `managed_wallet_registry_state()` now exposes `managed_wallet_registry_available`. Startup bootstrap from `WATCHED_WALLETS` now fails closed when the registry is unreadable/missing or when shadow-reset snapshot restore threw an exception. The dashboard wallet-registry endpoint no longer synthesizes wallet inventory from `wallet_watch_state`; it returns an unavailable-state message instead, and the web UI now prefers that backend message over a generic empty-state string.
Tests: `python -m py_compile src/kelly_watcher/data/db.py src/kelly_watcher/main.py src/kelly_watcher/dashboard_api.py tests/test_runtime_fixes.py tests/test_dashboard_web_source.py` -> passed; `uv run pytest tests/test_runtime_fixes.py -q -k 'should_import_bootstrap_watched_wallets_only_when_registry_is_empty or wallet_registry_summary_fails_closed_without_managed_wallets_table'` -> 2 passed; `uv run pytest tests/test_dashboard_web_source.py -q` -> 2 passed; `node ./dashboard-web/node_modules/typescript/bin/tsc -p dashboard-web/tsconfig.app.json --noEmit` -> passed

[2026-04-16 21:00 CDT] codex-main
Task: Publish explicit canonical managed-wallet registry health instead of forcing callers to infer from counts or fallbacks.
Claims: `JOURNAL.md`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/main.py`, `src/kelly_watcher/dashboard_api.py`, `tests/test_runtime_fixes.py`
Status: In progress
Blockers: `src/kelly_watcher/dashboard_api.py` and `tests/test_runtime_fixes.py` are already dirty from another in-flight browser/config slice, so edits and staging must stay surgical.
Next: Add `managed_wallet_registry_status` / `managed_wallet_registry_error`, gate bootstrap import on `empty` only, remove any remaining canonical-registry address fallback from `wallet_watch_state`, and cover missing/empty/unreadable states with focused regressions.
Decisions: Backend should own the distinction between `missing`, `empty`, `ready`, and `unreadable` once, then both startup and browser/API code can consume that explicit truth instead of inferring from payload shape.
Tests: Pending

[2026-04-16 21:06 CDT] codex-main
Task: Make auto-promoted discovery wallets hot-load into the runtime immediately instead of waiting for the 5-minute registry sync.
Claims: `JOURNAL.md`, `src/kelly_watcher/main.py`, `tests/test_wallet_discovery_runtime_sync.py`
Status: In progress
Blockers: `tests/test_runtime_fixes.py` is shared and dirty, so the regression should live in a new focused test file instead of piling more patch-scoping into that file.
Next: Hook `_refresh_wallet_discovery()` so a positive `promoted_count` triggers `_refresh_managed_wallet_registry()` right away, then add a focused scheduler-callback regression for the positive and zero-promotion cases.
Decisions: This is a realism fix, not just latency polish. In shadow mode, missing the first few trades after auto-promotion can overstate discovery usefulness because the DB says “promoted” while the tracker is still not polling the wallet.
Tests: Pending

[2026-04-16 21:12 CDT] codex-main
Task: Complete the discovery runtime hot-load fix.
Claims: `JOURNAL.md`, `src/kelly_watcher/main.py`, `tests/test_wallet_discovery_runtime_sync.py`
Status: Completed
Blockers: Live DB integrity is still the broader blocker before the wallet-finding tool can be judged from real shadow evidence, but this removes a concrete runtime lag that was understating newly promoted wallets.
Next: Return to the explicit canonical-registry health slice once the shared browser/config edits settle, or move on to wallet-discovery cohort reporting if the web surface becomes available again.
Decisions: `_refresh_wallet_discovery()` now immediately calls `_refresh_managed_wallet_registry()` whenever a scan reports `promoted_count > 0`, so newly auto-promoted wallets become part of the in-memory watchlist/tracker in the same scheduler pass instead of waiting for the periodic 5-minute registry sync. Added a dedicated regression file to keep this slice isolated from the already-dirty omnibus runtime tests.
Tests: `python -m py_compile src/kelly_watcher/main.py tests/test_wallet_discovery_runtime_sync.py` -> passed; `uv run pytest tests/test_wallet_discovery_runtime_sync.py -q` -> 2 passed; `uv run pytest tests/test_wallet_discovery.py tests/test_wallet_discovery_runtime_sync.py -q` -> 4 passed

[2026-04-16 21:22 CDT] codex-main
Task: Complete the backend explicit-registry-health contract.
Claims: `JOURNAL.md`, `src/kelly_watcher/data/db.py`, `src/kelly_watcher/main.py`, `tests/test_managed_wallet_registry_state.py`
Status: Completed
Blockers: Browser/API consumers for the new registry status are still dirty in the shared worktree, so this checkpoint intentionally stops at the backend contract and focused backend tests.
Next: Wire `managed_wallet_registry_status` / `managed_wallet_registry_error` into the browser-facing wallet-registry and startup views once the shared dashboard files are free, and decide whether `dashboard_api._wallet_registry_addresses()` should consume the new status directly instead of its remaining fallback logic.
Decisions: `managed_wallet_registry_state()` now classifies the canonical wallet registry as `ready`, `empty`, `missing`, or `unreadable` and preserves the old boolean/count fields for compatibility. `main.py` now uses that explicit status for bootstrap import gating, startup validation wording, startup-failure state persistence, runtime wallet loading, and managed-wallet registry refresh behavior. Missing/unreadable registry states now fail closed instead of being treated like an empty registry.
Tests: `python -m py_compile src/kelly_watcher/data/db.py src/kelly_watcher/main.py tests/test_managed_wallet_registry_state.py tests/test_wallet_discovery_runtime_sync.py` -> passed; `uv run pytest tests/test_managed_wallet_registry_state.py tests/test_wallet_discovery_runtime_sync.py -q` -> 7 passed, 3 subtests passed; `uv run pytest tests/test_wallet_discovery.py tests/test_managed_wallet_registry_state.py tests/test_wallet_discovery_runtime_sync.py -q` -> 9 passed, 3 subtests passed

[2026-04-16 21:33 CDT] codex-main
Task: Fail closed when the managed-wallet registry degrades after startup so the runtime cannot keep polling stale wallets from the last good snapshot.
Claims: `JOURNAL.md`, `src/kelly_watcher/main.py`, `tests/test_managed_wallet_registry_runtime_sync.py`
Status: In progress
Blockers: `src/kelly_watcher/main.py` is a shared integration point and `tests/test_wallet_discovery_runtime_sync.py` is already dirty elsewhere, so this slice needs a new focused regression file instead of touching the omnibus runtime tests.
Next: Clear the in-memory tracker/watchlist and trader cache when periodic registry sync sees `missing` or `unreadable`, then lock it with a scheduler-level regression around the degraded-after-start transition.
Decisions: Shadow mode should fail closed if the canonical wallet registry disappears or becomes unreadable after boot. Persisting the error state is not enough if the runtime keeps polling the last good wallet set.
Tests: Pending

[2026-04-16 21:37 CDT] codex-main
Task: Complete the degraded-after-start managed-wallet fail-closed runtime fix.
Claims: `JOURNAL.md`, `src/kelly_watcher/main.py`, `tests/test_managed_wallet_registry_runtime_sync.py`
Status: Completed
Blockers: The live DB integrity problem is still the main blocker before shadow tracking can be judged from real evidence. This slice just removes another stale-state loophole in the runtime.
Next: Highest-value follow-up is to propagate the explicit `managed_wallet_registry_status` / `managed_wallet_registry_error` contract through the wallet-registry/browser API so operators no longer have to infer registry health from endpoint shape or fallback behavior.
Decisions: Periodic managed-wallet registry sync now clears `runtime_wallets`, `watchlist`, `tracker`, and the trader cache when the canonical registry becomes `missing` or `unreadable` after startup. That means shadow mode no longer keeps polling stale wallets from the last good snapshot when the registry degrades mid-run.
Tests: `python -m py_compile src/kelly_watcher/main.py tests/test_managed_wallet_registry_runtime_sync.py` -> passed; `uv run pytest tests/test_managed_wallet_registry_runtime_sync.py tests/test_managed_wallet_registry_state.py -q` -> 6 passed, 5 subtests passed

[2026-04-16 22:02 CDT] codex-main
Task: Finish the wallet-finding backend tranche end to end: expand discovery sourcing honestly within the live Polymarket API surface, finalize backend API truth, and close the remaining lifecycle/control-plane gaps without depending on new dashboard work.
Claims: `JOURNAL.md`, `src/kelly_watcher/runtime/wallet_discovery.py`, `src/kelly_watcher/tools/rank_copytrade_wallets.py`, `src/kelly_watcher/dashboard_api.py`, `README.md`, `tests/test_wallet_discovery.py`, `tests/test_rank_copytrade_wallets.py`, new focused discovery/API tests as needed`
Status: In progress
Blockers: Shared worktree is still dirty in `dashboard_api.py`, `README.md`, and the omnibus runtime/browser tests, so this tranche needs patch-scoped staging and new focused tests where possible. Live probing shows `data-api.polymarket.com/trades` is usable for wallet-specific history but does not provide trustworthy market-participant filtering for a full adjacent-wallet crawler through the current endpoint surface.
Next: Implement the broadest honest backend finish: widen discovery seeding, add source/gate metadata, expose explicit registry/discovery truth in the API, add enable/disable wallet endpoints, and tighten docs around DB-backed shadow-only wallet discovery.
Decisions: I am not going to fake a market-participant crawler the live API does not reliably support. The discovery expansion in this tranche will therefore be a real multi-source Polymarket pipeline based on broader leaderboard fan-out plus wallet-centric adjacency classification derived from recent activity, while keeping promotion/trust fully grounded in post-boundary shadow evidence.
Tests: Pending
