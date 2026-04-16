# Kelly Watcher

Kelly Watcher is a local, operator-driven Polymarket copy-trading system. It watches selected wallets, scores incoming trades, sizes positions with Kelly-style logic, executes in shadow or live mode, records everything to SQLite, and exposes a web dashboard for monitoring and control in real time. Wallet registry state and discovery candidates are surfaced through the browser and backed by SQLite when the runtime has the needed tables.

This repository is meant to be clonable by another developer without any private local state. Secrets, databases, logs, model artifacts, and other runtime files are intentionally excluded from git. A fresh clone should be able to install dependencies, create `save/.env.dev`, start in shadow mode, and begin operating from there.

## What This Repository Includes

- A Python backend that polls watched wallets and decides whether to copy trades.
- A shadow trading path that simulates fills from captured order books.
- A live trading path guarded by readiness and risk checks.
- A responsive web dashboard that can be viewed from any device on the network.
- Wallet discovery, lifecycle, and identity resolution tools.
- A training and auto-retraining pipeline for the model-based signal path.
- Tests covering runtime behavior, training/search logic, market URL handling, dashboard/API behavior, and retrain bookkeeping.

## What This Repository Does Not Commit

These are intentionally local-only and should stay out of version control:

- `save/.env.*` and any other secret-bearing env files
- `save/data/` runtime files and SQLite databases
- `save/logs/`
- `save/model.joblib`
- Python caches and Node modules
- one-off local artifacts such as `nohup.out` and `results.json`

The bot creates its runtime directories automatically on startup.

## High-Level Architecture

At a high level, the system works like this:

1. `src/kelly_watcher/runtime/tracker.py` polls watched Polymarket wallets for recent trades and loads market metadata, order books, and price history.
2. `src/kelly_watcher/engine/watchlist_manager.py` keeps wallets in hot, warm, discovery, or dropped tiers so more promising wallets are polled more aggressively.
3. `src/kelly_watcher/engine/signal_engine.py` scores each buy signal using either:
   - the heuristic pipeline, or
   - a deployed model artifact if a current compatible `model.joblib` exists.
4. `src/kelly_watcher/engine/kelly.py` sizes the trade, then wallet trust and exposure guards shrink or block it.
5. `src/kelly_watcher/runtime/executor.py` either:
   - simulates a shadow order from the order book, or
   - posts a real Polymarket order in live mode.
6. `src/kelly_watcher/data/db.py` writes the durable record into `save/data/trading.db`.
7. `src/kelly_watcher/main.py` also emits a lightweight JSON event stream, bot state file, and an HTTP API for the dashboard.
8. `src/kelly_watcher/runtime/evaluator.py` resolves finished markets, computes PnL, and closes positions.
9. `src/kelly_watcher/research/train.py` and `src/kelly_watcher/research/auto_retrain.py` periodically retrain and optionally deploy a new model.

## Repository Layout

The codebase is organized so the repo root only keeps project-level metadata and apps, while the Python runtime lives under `src/kelly_watcher/`:

- `src/kelly_watcher/engine/` contains scoring, Kelly sizing, trust, belief, and watchlist logic.
- `src/kelly_watcher/data/` contains SQLite access and cached market or identity helpers.
- `src/kelly_watcher/runtime/` contains polling, execution, evaluation, and live runtime support.
- `src/kelly_watcher/research/` contains replay, search, training, and auto-retrain code.
- `src/kelly_watcher/integrations/` contains alerting and Telegram integration code.
- `src/kelly_watcher/tools/` contains operator utilities such as wallet ranking and setup helpers.
- `dashboard-web/` contains the supported browser dashboard.
- `dashboard-cli/` is legacy/internal and is no longer part of the recommended operator workflow.
- `docs/` holds planning and support documentation, and `tests/` holds the regression suite.

Important distinction:

- PnL, readiness checks, and open-position accounting are based on executed trades.
- The training set is broader: it includes resolved executed buys and can also include a narrow set of resolved skipped buys with counterfactual returns, down-weighted during training.

## Prerequisites

You need:

- Python 3.11+
- `uv`
- Node.js and `npm` for the web dashboard
- network access to Polymarket APIs

Optional for live trading:

- a Polygon wallet with USDC
- `POLYGON_PRIVATE_KEY`
- `POLYGON_WALLET_ADDRESS`

Notes:

- The web dashboard talks to the backend over HTTP, so it does not need direct SQLite access.
- The repository includes `uv.lock` and `dashboard-web/package-lock.json` so installs are reproducible for the supported operator path.

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/aryan-cs/kelly-watcher.git
cd kelly-watcher
```

### 2. Install backend dependencies

```bash
uv sync
```

### 3. Install web dashboard dependencies

```bash
cd dashboard-web
npm install
cd ..
```

### 4. Create your local config

Preferred location: copy `.env.example` to `save/.env.dev`.

Examples:

```bash
mkdir -p save
cp .env.example save/.env.dev
```

```powershell
New-Item -ItemType Directory -Force save | Out-Null
Copy-Item .env.example save/.env.dev
```

```bat
if not exist save mkdir save
copy .env.example save\.env.dev
```

At minimum for shadow mode, set:

- `USE_REAL_MONEY=false`

If this is the first time you are bootstrapping the bot, you can seed `WATCHED_WALLETS` once. After the first import, wallet membership lives in SQLite-backed runtime state and `WATCHED_WALLETS` should be treated as bootstrap-only.

### 5. Populate a watchlist

If you already know the wallets, you may paste them into `save/.env.dev` for the one-time bootstrap import. After that, the backend runs from the DB-backed managed wallet registry instead of the env file.

If you only know Polymarket handles or profile URLs, use:

```bash
uv run resolve-wallet @some_user
uv run resolve-wallet https://polymarket.com/@some_user
```

The normal workflow is to let the backend run its scheduled wallet-discovery scan and auto-promote fully copyable wallets into the managed registry while staying in shadow mode.

If you want to inspect or debug discovery from the terminal, you can still use:

```bash
uv run rank-copytrade-wallets --top 20
```

For a manual one-shot refresh of the backend candidate cache, you can still use:

```bash
uv run discover-copytrade-wallets
```

These commands are diagnostics only. The canonical wallet-finding workflow is the backend scheduler plus the DB-backed managed wallet registry.

### 6. Start the backend

Preferred:

```bash
uv run kelly-watcher
```

Equivalent:

```bash
uv run python -m kelly_watcher.main
```

The backend will automatically:

- create `save/data/` and `save/logs/`
- initialize or migrate the SQLite schema
- load `save/.env.dev` by default
- validate startup config
- start the polling loop
- run a background wallet-discovery scan on its own cadence when enabled
- emit `save/data/events.jsonl` and `save/data/bot_state.json`
- start the dashboard API on `http://127.0.0.1:8765` by default
- serve `dashboard-web/dist` at `/` when the web app has been built

If you want to run the dashboard on another computer, set:

- `DASHBOARD_API_HOST=0.0.0.0`
- optionally `DASHBOARD_API_TOKEN=some-shared-secret`
- set `DASHBOARD_WEB_URL=http://your-tailscale-magicdns-name:8765` if you want Telegram `/link` replies to return an explicit browser URL

If your real shadow runtime lives on a Windows machine and you are developing on a Mac, treat the Windows backend as the integration target. The Mac is for local development and testing; the Windows shadow runtime is the source of truth for live shadow evidence.

### 7. Start the web dashboard

In a second terminal, use one of these browser workflows.

Web dashboard for development:

```bash
cd dashboard-web
npm run dev
```

The Vite dev server runs on `http://127.0.0.1:5173` by default.

For UI work, `npm run dev` now defaults to a local mock/template data mode so you can iterate on layout without touching the backend or pushing every change to Windows.

If you want the dev server to talk to a real backend instead, opt into API mode:

```bash
cd dashboard-web
VITE_DASHBOARD_DATA_MODE=api VITE_KELLY_API_BASE_URL=http://BACKEND_HOST:8765 npm run dev
```

Web dashboard for deployment:

```bash
cd dashboard-web
npm run build
cd ..
uv run kelly-watcher
```

Then open `http://BACKEND_HOST:8765` from any phone or laptop that can reach the backend machine.

To run the web dashboard against a backend on another computer during development, point it at the backend API:

```bash
cd dashboard-web
VITE_DASHBOARD_DATA_MODE=api VITE_KELLY_API_BASE_URL=http://BACKEND_HOST:8765 npm run dev
```

If the backend sets `DASHBOARD_API_TOKEN`, also set:

```bash
cd dashboard-web
VITE_DASHBOARD_DATA_MODE=api VITE_KELLY_API_BASE_URL=http://BACKEND_HOST:8765 VITE_KELLY_API_TOKEN=some-shared-secret npm run dev
```

You can also put those in `dashboard-web/.env.local` instead of exporting them every time:

```env
VITE_DASHBOARD_DATA_MODE=api
VITE_KELLY_API_BASE_URL=http://windows-box.tailnet-name.ts.net:8765
VITE_KELLY_API_TOKEN=some-shared-secret
```

The web dashboard reads its API base URL and token from `dashboard-web/.env.local`, with shell environment variables taking precedence if you set both.

## Runtime Modes

### Shadow Mode

This is the default and the recommended starting point.

Requirements:

- `USE_REAL_MONEY=false`
- the wallet registry has been bootstrapped once, or the backend can import `WATCHED_WALLETS` on first run

Behavior:

- no real orders are sent
- fills are simulated from the captured order book
- positions, skips, resolutions, and PnL are still logged normally
- the web dashboard works the same way as in live mode

Reset helper:

```bash
uv run shadow-reset
```

Foreground mode:

```bash
uv run shadow-reset --foreground
```

Reset only, then start the bot yourself:

```bash
uv run shadow-reset --reset-only
```

What `shadow-reset` does:

- refuses to run if `USE_REAL_MONEY=true`
- stops an existing bot process
- deletes shadow runtime state such as the SQLite DB and event stream
- preserves config, identity cache, logs, and your model artifact
- recreates the DB and restarts the bot

### Live Mode

Use live mode only after you have validated shadow behavior.

Required env values:

- `USE_REAL_MONEY=true`
- `POLYGON_PRIVATE_KEY`
- `POLYGON_WALLET_ADDRESS`

Recommended first-time setup:

```bash
uv run polymarket-setup
```

Live startup also enforces operational checks such as:

- wallet/private-key consistency
- minimum balance availability
- live allowance checks
- live position sync health
- optional shadow-history requirement if `LIVE_REQUIRE_SHADOW_HISTORY=true`

Important live behavior:

- USDC collateral approval is typically a one-time step.
- Conditional token approvals are requested automatically when opening a live position.
- Entry guards can pause new entries after drawdown or daily-loss limits are hit.

## How The System Works

### 1. Trade Ingestion

`tracker.py` polls Polymarket data endpoints for watched-wallet activity and normalizes each incoming source trade into a structured event. It fetches:

- source trade details
- market metadata
- order books
- price history when available

It also manages cursors so old trades do not replay forever.

### 2. Watchlist Tiering

`watchlist_manager.py` separates wallets into tiers:

- hot
- warm
- discovery
- dropped

Hot wallets are polled most frequently. Lower tiers are polled less often. Wallets can be demoted or dropped when they become stale, underperform, or repeatedly produce uncopyable signals.

### 3. Trader and Market Scoring

`trader_scorer.py` builds trader-level features such as:

- win rate
- sample count
- volume
- average size
- account age
- conviction ratio

`market_scorer.py` scores the market itself using inputs such as:

- spread
- visible depth
- time to resolution
- momentum
- volume
- concentration

Unsafe markets are vetoed before sizing.

### 4. Signal Selection

`signal_engine.py` then decides whether the trade passes.

It has two paths:

- Heuristic path: combines trader and market quality into a confidence estimate, then applies adaptive floors and belief adjustments.
- Model path: if `MODEL_PATH` points to a compatible artifact, the engine uses the trained model instead of the pure heuristic scorer.

If the model artifact is missing, stale, or incompatible with the current training contract, the system falls back to heuristics automatically.

### 5. Position Sizing

`kelly.py` computes the base Kelly-style size. That output is then adjusted by:

- the minimum confidence threshold
- the minimum bet size
- bankroll availability
- wallet trust and quality multipliers
- portfolio exposure caps

### 6. Execution

`executor.py` handles both entries and exits.

Shadow mode:

- simulates fills from the current order book
- rejects trades that would not have filled cleanly

Live mode:

- initializes the CLOB client
- checks live balances and allowances
- posts market orders
- reconciles live fills and positions

### 7. Resolution and PnL

`evaluator.py` periodically checks whether copied markets resolved. It updates:

- resolved outcome
- remaining position size
- shadow or live PnL
- training labels

It also contains sports-page fallbacks for certain market types when the direct market payload is not enough.

### 8. Retraining

`src/kelly_watcher/research/auto_retrain.py` and `src/kelly_watcher/research/train.py` handle scheduled and early retraining.

Current model behavior:

- the label mode is expected-return based
- the artifact is versioned by a data contract
- deployable models must pass internal search and holdout checks
- skipped-but-trainable counterfactual rows are down-weighted relative to executed fills

If a retrain passes deployment checks, the bot reloads the model automatically.

### 9. Dashboard

The supported operator frontend is `dashboard-web`, the responsive browser UI served by the backend or by Vite during development.

The web dashboard uses the HTTP API exposed by `src/kelly_watcher/dashboard_api.py`.

Recommended approach:

- develop the web app with Vite in `dashboard-web/`
- build it with `npm run build`
- let the Python backend serve the built files from `dashboard-web/dist`
- use the browser to review managed wallets, trigger discovery scans, and inspect wallet lifecycle events

That keeps local development fast on macOS and keeps Windows deployment simple because production only needs the Python bot plus static files.

## Runtime Files

The bot reads and writes these local files during normal operation:

- `save/data/trading.db`: source-of-truth SQLite database
- `save/data/events.jsonl`: rolling event stream for the dashboard
- `save/data/bot_state.json`: lightweight runtime status
- `save/data/identity_cache.json`: wallet-to-username cache
- `save/logs/bot.log`: rotating backend log
- `save/logs/shadow_runtime.out`: background log when using `shadow-reset`
- `save/model.joblib`: optional deployed model artifact

Important note:

- `trade_log` in SQLite is the durable record.
- `events.jsonl` exists to drive the dashboard and should be treated as a convenience stream, not as the canonical ledger.

## Key Database Tables

The most important tables in `save/data/trading.db` are:

- `trade_log`: all copied trades, skips, fills, exits, resolutions, and features
- `positions`: currently open positions
- `trader_cache`: cached trader stats used by scoring
- `model_history`: deployed model artifacts and metrics
- `retrain_runs`: every retrain attempt, including skipped and rejected runs
- `wallet_cursors`: per-wallet polling cursors
- `wallet_watch_state`: tracked/dropped wallet state
- `wallet_discovery_candidates`: cached discovery shortlist from the backend scan

## Dashboard Guide

The web dashboard is the supported operator surface. It is built to work from any device that can reach the backend over your local network or Tailscale network.

### Overview Cards

The top summary row shows:

- current mode
- runtime duration
- bankroll
- polling state
- model/runtime compatibility
- shadow-readiness status

### Incoming Feed

Shows recent watched-wallet trades before the bot makes a copy decision.

Useful for checking:

- whether the feed is alive
- whether watched wallets are active
- whether prices and sizes look sane

### Decision Stream

Shows recent scored signals after confidence, market, and risk checks.

Useful for checking:

- why a trade was accepted or rejected
- whether thresholds are too strict
- whether vetoes or risk controls are dominating

### System Detail

Shows operator-facing runtime facts such as API target, last poll time, retrain state, replay-search state, and shadow-restart status.

Useful for checking:

- whether the browser is pointed at the right backend
- whether auth is required or the saved token was rejected
- whether retrain, replay-search, or restart work is queued or running

### Token Prompt

If `DASHBOARD_API_TOKEN` is enabled on the backend, the web dashboard prompts for a bearer token on that device and stores it in the browser unless you inject it through Vite env vars during development.

### Wallet Registry

Shows the current wallet registry snapshot from the backend, including each wallet's status and any discovery context that is already cached in SQLite. Use the action button on each row to drop or reactivate a wallet.

### Discovery

Shows discovery candidates cached by the backend and includes a `Scan now` button. This is the browser path for refreshing candidate wallets without waiting for the scheduler.

### Membership Timeline

Shows wallet lifecycle events or, when the event table is not yet available, a snapshot-derived timeline of the current wallet state. It is meant to keep the browser honest about which data is historical and which data is current-state only.

## Dashboard Workflow

- Open the backend URL or the Vite dev server in a browser.
- Use `Refresh now` to force an immediate reload.
- If auth is enabled, paste the shared token for that device.
- Open market links from the incoming-feed or decision-stream cards when you need the Polymarket page.
- Use the wallet registry and discovery panels to manage the tracked-wallet set and trigger scans from the browser.

## Common Commands

Start the bot:

```bash
uv run kelly-watcher
```

Start the web dashboard in development:

```bash
cd dashboard-web
npm run dev
```

Run the full test suite:

```bash
uv run python -m unittest discover -s tests
```

Resolve a handle or profile into wallets:

```bash
uv run resolve-wallet @some_user
```

Rank candidate wallets:

```bash
uv run rank-copytrade-wallets --top 20
```

Refresh the backend-style candidate cache for diagnostics:

```bash
uv run discover-copytrade-wallets
```

Run manual training:

```bash
uv run python -m kelly_watcher.research.train
```

Reset and restart the shadow account:

```bash
uv run shadow-reset
```

Run one-time live collateral setup:

```bash
uv run polymarket-setup
```

On Windows, run the same backend command directly from Command Prompt, PowerShell, or Task Scheduler:

```bat
uv run kelly-watcher
```

## Environment Variables

All env parsing lives in `src/kelly_watcher/config.py`. Duration values typically accept forms such as `45s`, `10m`, `6h`, `7d`, or `unlimited`.

### Required or commonly changed

- `WATCHED_WALLETS`: optional one-time bootstrap list of comma-separated watched wallet addresses
- `USE_REAL_MONEY`: `false` for shadow, `true` for live
- `MIN_CONFIDENCE`: minimum signal confidence
- `MIN_BET_USD`: minimum order size
- `MAX_BET_FRACTION`: Kelly cap as a fraction of bankroll
- `SHADOW_BANKROLL_USD`: paper bankroll in shadow mode
- `MODEL_PATH`: optional deployed model artifact path

### Live trading and account safety

- `POLYGON_PRIVATE_KEY`
- `POLYGON_WALLET_ADDRESS`
- `MAX_LIVE_DRAWDOWN_PCT`
- `MAX_DAILY_LOSS_PCT`
- `MAX_TOTAL_OPEN_EXPOSURE_FRACTION`
- `MAX_MARKET_EXPOSURE_FRACTION`
- `MAX_TRADER_EXPOSURE_FRACTION`
- `MAX_LIVE_HEALTH_FAILURES`
- `LIVE_REQUIRE_SHADOW_HISTORY`
- `LIVE_MIN_SHADOW_RESOLVED`

### Polling and market timing

- `POLL_INTERVAL_SECONDS`
- `HOT_WALLET_COUNT`
- `WARM_WALLET_COUNT`
- `WARM_POLL_INTERVAL_MULTIPLIER`
- `DISCOVERY_POLL_INTERVAL_MULTIPLIER`
- `WALLET_DISCOVERY_ENABLED`
- `WALLET_DISCOVERY_SCAN_INTERVAL`
- `WALLET_DISCOVERY_LEADERBOARD_PAGES`
- `WALLET_DISCOVERY_LEADERBOARD_PER_PAGE`
- `WALLET_DISCOVERY_ANALYZE_LIMIT`
- `WALLET_DISCOVERY_CANDIDATE_LIMIT`
- `MAX_SOURCE_TRADE_AGE`
- `MAX_FEED_STALENESS`
- `MIN_EXECUTION_WINDOW`
- `MAX_MARKET_HORIZON`

### Wallet quality, discovery, and auto-drop

- `WALLET_INACTIVITY_LIMIT`
- `WALLET_SLOW_DROP_MAX_TRACKING_AGE`
- `WALLET_PERFORMANCE_DROP_MIN_TRADES`
- `WALLET_PERFORMANCE_DROP_MAX_WIN_RATE`
- `WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN`
- `WALLET_UNCOPYABLE_PENALTY_MIN_BUYS`
- `WALLET_UNCOPYABLE_PENALTY_WEIGHT`
- `WALLET_UNCOPYABLE_DROP_MIN_BUYS`
- `WALLET_UNCOPYABLE_DROP_MAX_SKIP_RATE`
- `WALLET_UNCOPYABLE_DROP_MAX_RESOLVED_COPIED`
- `WALLET_COLD_START_MIN_OBSERVED_BUYS`
- `WALLET_DISCOVERY_MIN_OBSERVED_BUYS`
- `WALLET_DISCOVERY_MIN_RESOLVED_BUYS`
- `WALLET_DISCOVERY_SIZE_MULTIPLIER`
- `WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS`
- `WALLET_PROBATION_SIZE_MULTIPLIER`
- `WALLET_QUALITY_SIZE_MIN_MULTIPLIER`
- `WALLET_QUALITY_SIZE_MAX_MULTIPLIER`

Wallet membership lives in SQLite-backed runtime state. `WATCHED_WALLETS` is only a first-run import source.

### Retraining and logging

- `RETRAIN_BASE_CADENCE`
- `RETRAIN_HOUR_LOCAL`
- `RETRAIN_EARLY_CHECK_INTERVAL`
- `RETRAIN_MIN_NEW_LABELS`
- `RETRAIN_MIN_SAMPLES`
- `LOG_LEVEL`

### Alerts

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `DASHBOARD_WEB_URL`: public browser URL returned by Telegram `/link`; on Tailscale this should usually be your MagicDNS host like `http://windows-box.tailnet-name.ts.net:8765`

## Repository Layout

Core runtime:

- `src/kelly_watcher/main.py`: startup, polling loop, scheduling, event emission, bot-state writes
- `src/kelly_watcher/runtime/tracker.py`: Polymarket trade and market data client
- `src/kelly_watcher/engine/signal_engine.py`: heuristic/model decision logic
- `src/kelly_watcher/runtime/executor.py`: shadow and live execution
- `src/kelly_watcher/runtime/evaluator.py`: resolution and PnL updates
- `src/kelly_watcher/data/db.py`: SQLite schema and migrations
- `src/kelly_watcher/engine/dedup.py`: duplicate and open-position gating

Training and model:

- `src/kelly_watcher/research/train.py`: feature loading, search, calibration, deployment decision
- `src/kelly_watcher/research/auto_retrain.py`: scheduled and early retrain orchestration
- `src/kelly_watcher/engine/economic_model.py`: return-target transforms and sample weights
- `src/kelly_watcher/engine/trade_contract.py`: SQL contract for trainable and executed rows
- `src/kelly_watcher/engine/features.py`: shared feature list

Watchlist and wallet tooling:

- `src/kelly_watcher/engine/watchlist_manager.py`: wallet tiering and auto-drop logic
- `src/kelly_watcher/engine/wallet_trust.py`: sizing and trust tiers
- `src/kelly_watcher/runtime/wallet_discovery.py`: background candidate discovery and candidate-cache persistence
- `src/kelly_watcher/tools/discover_copytrade_wallets.py`: one-shot candidate scan and cached shortlist printer
- `src/kelly_watcher/tools/resolve_wallet.py`: handle/profile URL to wallet resolver
- `src/kelly_watcher/tools/rank_copytrade_wallets.py`: leaderboard-based discovery and ranking
- `src/kelly_watcher/data/identity_cache.py`: wallet and username cache

Dashboard:

- `dashboard-web/src/App.tsx`: responsive browser dashboard
- `dashboard-web/src/api.ts`: browser API client and token handling
- `src/kelly_watcher/dashboard_api.py`: backend API and static asset serving for the browser UI

Packaging and entrypoints:

- `pyproject.toml`: project metadata and console scripts such as `kelly-watcher`, `shadow-reset`, and `resolve-wallet`
- `src/kelly_watcher/cli.py`: lightweight launcher for the packaged backend entrypoint
- `src/kelly_watcher/shadow_reset.py`: cross-platform shadow reset and restart helper

## Operational Notes

- This is an operator system, not a fire-and-forget hosted service.
- The supported operator interface is the web dashboard.
- The wallet registry and discovery panels are the primary workflow; terminal wallet ops are legacy.
- `dashboard-web` can be reached from any device that can hit the backend host and port.
- A fresh clone starts on heuristics unless you later train and deploy a model artifact.
- Runtime backups and scheduled jobs are driven by `src/kelly_watcher/main.py`, not by external infra.
- The bot will refuse unsafe live startup states rather than silently continuing.

Current scheduled tasks inside the main process include:

- trade resolution checks every 2 minutes
- daily report at 08:00 local time
- DB backup at 04:00 local time
- scheduled retrain at the configured cadence/hour
- early retrain checks at `RETRAIN_EARLY_CHECK_INTERVAL`
- watchlist refresh and cache refresh jobs

## Testing

Primary test command:

```bash
uv run python -m unittest discover -s tests
```

Areas covered by tests include:

- runtime and startup regressions
- dashboard/API behavior
- expected-return model handling
- training-data contract rules
- search/holdout planning
- retrain run bookkeeping
- market URL handling
- wallet trust and watchlist management

## Troubleshooting

### The dashboard is blank

Check that the backend is running and writing:

- `save/data/trading.db`
- `save/data/events.jsonl`
- `save/data/bot_state.json`

For the web dashboard, also confirm `dashboard-web/dist` exists if you are opening the backend root URL instead of using Vite dev mode.

### No trades are coming in

Check:

- the wallet registry has been bootstrapped or imported
- the watched wallets are actually active
- your poll interval is sane
- the logs are not showing repeated API failures or rate limits

### The bot says the model is unavailable

That usually means one of:

- `save/model.joblib` does not exist
- the artifact was trained under an older data contract
- the artifact failed to load

In all of those cases the bot falls back to heuristics automatically.

### Live mode refuses to start

Common reasons:

- wallet/private-key mismatch
- missing balance or allowance
- live position sync failure
- `LIVE_REQUIRE_SHADOW_HISTORY=true` but you do not have enough resolved shadow trades yet

### A username is missing or wrong

Refresh it with:

```bash
uv run resolve-wallet <wallet-or-handle>
```

That updates `save/data/identity_cache.json`.

## Safety Notes

- Shadow mode is the default for a reason.
- Live mode should be treated as high risk and operationally supervised.
- Test and validate watchlist quality before trusting bankroll results.
- Keep secrets in `save/.env.*`, never in committed source files.
- Do not treat the dashboard event stream as the canonical ledger; use SQLite for durable records.
