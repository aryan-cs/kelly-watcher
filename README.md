# Kelly Watcher

Kelly Watcher is a local, operator-driven Polymarket copy-trading system. It watches selected wallets, scores incoming trades, sizes positions with Kelly-style logic, executes in shadow or live mode, records everything to SQLite, and exposes a terminal dashboard for monitoring the bot in real time.

This repository is meant to be clonable by another developer without any private local state. Secrets, databases, logs, model artifacts, and other runtime files are intentionally excluded from git. A fresh clone should be able to install dependencies, create a `.env`, start in shadow mode, and begin operating from there.

## What This Repository Includes

- A Python backend that polls watched wallets and decides whether to copy trades.
- A shadow trading path that simulates fills from captured order books.
- A live trading path guarded by readiness and risk checks.
- A terminal dashboard built with Ink + React.
- Wallet discovery and identity resolution tools.
- A training and auto-retraining pipeline for the model-based signal path.
- Tests covering runtime behavior, training/search logic, market URL handling, CLI behavior, and retrain bookkeeping.

## What This Repository Does Not Commit

These are intentionally local-only and should stay out of version control:

- `.env` and any other secret-bearing env files
- `save/` runtime files, SQLite databases, logs, and model artifacts
- Python caches and Node modules
- one-off local artifacts such as `nohup.out` and `results.json`

The bot creates its runtime directories automatically on startup.

## High-Level Architecture

At a high level, the system works like this:

1. `tracker.py` polls watched Polymarket wallets for recent trades and loads market metadata, order books, and price history.
2. `watchlist_manager.py` keeps wallets in hot, warm, discovery, or dropped tiers so more promising wallets are polled more aggressively.
3. `signal_engine.py` scores each buy signal using either:
   - the heuristic pipeline, or
   - a deployed model artifact if a current compatible `save/model.joblib` exists.
4. `kelly.py` sizes the trade, then wallet trust and exposure guards shrink or block it.
5. `executor.py` either:
   - simulates a shadow order from the order book, or
   - posts a real Polymarket order in live mode.
6. `db.py` writes the durable record into `save/data/trading.db`.
7. `main.py` also emits a lightweight JSON event stream, bot state file, and an HTTP API for the dashboard.
8. `evaluator.py` resolves finished markets, computes PnL, and closes positions.
9. `train.py` and `auto_retrain.py` periodically retrain and optionally deploy a new model.

Important distinction:

- PnL, readiness checks, and open-position accounting are based on executed trades.
- The training set is broader: it includes resolved executed buys and can also include a narrow set of resolved skipped buys with counterfactual returns, down-weighted during training.

## Prerequisites

You need:

- Python 3.11+
- `uv`
- Node.js and `npm` for the dashboard
- network access to Polymarket APIs

Optional for live trading:

- a Polygon wallet with USDC
- `POLYGON_PRIVATE_KEY`
- `POLYGON_WALLET_ADDRESS`

Notes:

- The dashboard talks to the backend over HTTP, so it no longer needs direct SQLite access.
- The repository includes `uv.lock` and `dashboard/package-lock.json` so installs are reproducible.

## Split Mac/Windows Runtime

Production is intended to run split across the two Tailscale-connected machines:

- Windows backend/API: `100.91.53.63`
- Mac Ink dashboard frontend: `100.104.250.54`

Run the trading backend only on Windows. Run the React Ink terminal dashboard only on the Mac.

Use one repo-root `.env` per checkout. On the Windows backend `.env`, keep:

```env
DASHBOARD_API_HOST=100.91.53.63
DASHBOARD_API_PORT=8765
KELLY_API_BASE_URL=http://100.91.53.63:8765
```

On the Mac frontend `.env`, keep:

```env
KELLY_API_BASE_URL=http://100.91.53.63:8765
```

If you set `DASHBOARD_API_TOKEN` on Windows, set the same value as `KELLY_API_TOKEN` on the Mac.

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

### 3. Install dashboard dependencies

```bash
cd dashboard
npm install
cd ..
```

### 4. Create your local config

Copy `.env.example` to `.env`. Kelly Watcher now reads one repo-level `.env` only; `.env.dev`, `.env.prod`, and `save/.env.*` are ignored.

Examples:

```bash
cp .env.example .env
```

```powershell
Copy-Item .env.example .env
```

```bat
copy .env.example .env
```

At minimum for shadow mode, set:

- `WATCHED_WALLETS`
- `USE_REAL_MONEY=false`

`WATCHED_WALLETS` should be a comma-separated list of lowercase wallet addresses.

### 5. Populate a watchlist

If you already know the wallets, paste them into `.env`.

If you only know Polymarket handles or profile URLs, use:

```bash
uv run python resolve_wallet.py @some_user
uv run python resolve_wallet.py https://polymarket.com/@some_user
```

If you want to discover candidate wallets from leaderboard data, use:

```bash
uv run python rank_copytrade_wallets.py --top 20
```

Both tools print a ready-to-paste `WATCHED_WALLETS=...` line.

### 6. Start the backend

Preferred:

```bash
uv run main
```

Equivalent:

```bash
uv run python main.py
```

The backend will automatically:

- create `save/data/` and `save/logs/`
- initialize or migrate the SQLite schema
- load `.env`
- validate startup config
- start the polling loop
- emit `save/data/events.jsonl` and `save/data/bot_state.json`
- start the dashboard API on `http://127.0.0.1:8765` by default

If you want to run the dashboard on another computer, set:

- `DASHBOARD_API_HOST=100.91.53.63` on the Windows backend
- optionally `DASHBOARD_API_TOKEN=some-shared-secret`

### 7. Start the dashboard

In a second terminal:

```bash
cd dashboard
npm start
```

To run the dashboard on another computer, point it at the backend API:

```bash
cd dashboard
KELLY_API_BASE_URL=http://100.91.53.63:8765 npm start
```

If the backend sets `DASHBOARD_API_TOKEN`, also set:

```bash
cd dashboard
KELLY_API_BASE_URL=http://100.91.53.63:8765 KELLY_API_TOKEN=some-shared-secret npm start
```

You can also put those in the dashboard machine's repo-level `.env` instead of exporting them every time:

```env
KELLY_API_BASE_URL=http://100.91.53.63:8765
KELLY_API_TOKEN=some-shared-secret
```

The dashboard now reads `KELLY_API_BASE_URL` and `KELLY_API_TOKEN` from `.env`, with shell environment variables taking precedence if you set both.

For dashboard development from the TypeScript sources instead of the checked-in runtime JS:

```bash
cd dashboard
npm run dev
```

Windows helper:

```bat
start-backend-windows.cmd
```

Mac helper:

```bash
./start-dashboard-mac.sh
```

## Runtime Modes

### Shadow Mode

This is the default and the recommended starting point.

Requirements:

- `USE_REAL_MONEY=false`
- `WATCHED_WALLETS` configured

Behavior:

- no real orders are sent
- fills are simulated from the captured order book
- positions, skips, resolutions, and PnL are still logged normally
- the dashboard works the same way as in live mode

Reset helper:

```bash
uv run python restart_shadow.py
```

Foreground mode:

```bash
uv run python restart_shadow.py --foreground
```

Reset only, then start the bot yourself:

```bash
uv run python restart_shadow.py --reset-only
```

What `restart_shadow.py` does:

- refuses to run if `USE_REAL_MONEY=true`
- stops an existing bot process
- deletes shadow runtime state such as the SQLite DB and event stream
- preserves config, `WATCHED_WALLETS`, identity cache, logs, and your model artifact
- recreates the DB and restarts the bot

### Live Mode

Use live mode only after you have validated shadow behavior.

Required env values:

- `USE_REAL_MONEY=true`
- `POLYGON_PRIVATE_KEY`
- `POLYGON_WALLET_ADDRESS`

Recommended first-time setup:

```bash
uv run python polymarket_setup.py
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

`auto_retrain.py` and `train.py` handle scheduled and early retraining.

Current model behavior:

- the label mode is expected-return based
- the artifact is versioned by a data contract
- deployable models must pass internal search and holdout checks
- skipped-but-trainable counterfactual rows are down-weighted relative to executed fills

If a retrain passes deployment checks, the bot reloads the model automatically.

### 9. Dashboard

The dashboard is a terminal app, not a web app. It reads backend state through the local dashboard API:

- `save/data/trading.db`
- `save/data/events.jsonl`
- `save/data/bot_state.json`
- `save/data/identity_cache.json`

In production, run it on the Mac checkout and set `KELLY_API_BASE_URL=http://100.91.53.63:8765` in the Mac `.env`.

## Runtime Files

The bot reads and writes these local files during normal operation:

- `save/data/trading.db`: source-of-truth SQLite database
- `save/data/events.jsonl`: rolling event stream for the dashboard
- `save/data/bot_state.json`: lightweight runtime status
- `save/data/identity_cache.json`: wallet-to-username cache
- `save/logs/bot.log`: rotating backend log
- `save/logs/shadow_runtime.out`: background log when using `restart_shadow.py`
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

## Dashboard Guide

The terminal dashboard currently has six main pages.

### Page 1: Live Feed

Shows raw incoming watched-wallet trades before the bot makes a copy decision.

Useful for checking:

- whether the feed is alive
- whether watched wallets are active
- whether prices and sizes look sane

### Page 2: Signals

Shows accepted, rejected, skipped, and paused decisions after scoring.

Useful for checking:

- why a trade was accepted or rejected
- whether thresholds are too strict
- whether vetoes or risk controls are dominating

### Page 3: Performance

Shows open positions, closed positions, and performance state.

Useful for checking:

- current exposure
- recent exits
- shadow or live PnL
- time spent in positions

### Page 4: Models

Shows model and retrain health.

Useful for checking:

- whether the deployed model beats baseline
- whether retraining is succeeding
- whether the bot is on heuristics or a model-backed path

### Page 5: Wallets

Shows wallet-level quality and tracking information.

Useful for checking:

- which wallets are helping
- which wallets are stale or downgraded
- which ones have local resolved copied history

### Page 6: Settings

Shows runtime state plus editable env-backed config values.

Important behavior:

- some fields apply on the next loop
- some fields require a bot restart
- toggling live trading only edits `.env`; it does not hot-switch the running bot

## Dashboard Controls

Global:

- `1` through `6`: switch pages
- `r`: refresh
- `q`: quit

Page-specific:

- Live Feed: `Up/Down` scroll
- Signals: `Up/Down` scroll, `Left/Right` pan long text
- Performance: arrows to move, `j/k` to scroll, `Enter` for detail, `Esc` to close
- Models: arrows to move, `Enter` for detail
- Wallets: `Up/Down` select, `Enter` detail, `Esc` close
- Settings: arrows or `j/k` select, `Enter` or `e` edit, `Esc` cancel

## Common Commands

Start the bot:

```bash
uv run main
```

Start the dashboard:

```bash
cd dashboard
npm start
```

Run the full test suite:

```bash
uv run python -m unittest discover -s tests
```

Resolve a handle or profile into wallets:

```bash
uv run python resolve_wallet.py @some_user
```

Rank candidate wallets:

```bash
uv run python rank_copytrade_wallets.py --top 20
```

Run manual training:

```bash
uv run python train.py
```

Reset and restart the shadow account:

```bash
uv run python restart_shadow.py
```

Run one-time live collateral setup:

```bash
uv run python polymarket_setup.py
```

Run the bot in a restart loop on Windows:

```bat
watchdog.bat
```

## Environment Variables

All env parsing lives in `config.py`. Duration values typically accept forms such as `45s`, `10m`, `6h`, `7d`, or `unlimited`.

### Required or commonly changed

- `WATCHED_WALLETS`: comma-separated watched wallet addresses
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

## Repository Layout

Core runtime:

- `main.py`: startup, polling loop, scheduling, event emission, bot-state writes
- `tracker.py`: Polymarket trade and market data client
- `signal_engine.py`: heuristic/model decision logic
- `executor.py`: shadow and live execution
- `evaluator.py`: resolution and PnL updates
- `db.py`: SQLite schema and migrations
- `dedup.py`: duplicate and open-position gating

Training and model:

- `train.py`: feature loading, search, calibration, deployment decision
- `auto_retrain.py`: scheduled and early retrain orchestration
- `economic_model.py`: return-target transforms and sample weights
- `trade_contract.py`: SQL contract for trainable and executed rows
- `features.py`: shared feature list

Watchlist and wallet tooling:

- `watchlist_manager.py`: wallet tiering and auto-drop logic
- `wallet_trust.py`: sizing and trust tiers
- `resolve_wallet.py`: handle/profile URL to wallet resolver
- `rank_copytrade_wallets.py`: leaderboard-based discovery and ranking
- `identity_cache.py`: wallet and username cache

Dashboard:

- `dashboard/dashboard.tsx`: main terminal UI
- `dashboard/pages/*.tsx`: page views
- `dashboard/configEditor.ts`: env-backed editable settings
- `dashboard/settingsDanger.ts`: live toggle and shadow reset actions

Packaging and entrypoints:

- `pyproject.toml`: project metadata and the `main` console script
- `kelly_watcher/cli.py`: lightweight launcher so `uv run main` works cleanly
- `restart_shadow.py`: cross-platform shadow reset and restart helper

## Operational Notes

- This is an operator system, not a fire-and-forget hosted service.
- The dashboard expects local filesystem access to the repo runtime files.
- A fresh clone starts on heuristics unless you later train and deploy a model artifact.
- Runtime backups and scheduled jobs are driven by `main.py`, not by external infra.
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
- CLI launch behavior
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

### No trades are coming in

Check:

- `WATCHED_WALLETS` is populated
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
uv run python resolve_wallet.py <wallet-or-handle>
```

That updates `save/data/identity_cache.json`.

## Safety Notes

- Shadow mode is the default for a reason.
- Live mode should be treated as high risk and operationally supervised.
- Test and validate watchlist quality before trusting bankroll results.
- Keep secrets in `.env`, never in committed source files.
- Do not treat the dashboard event stream as the canonical ledger; use SQLite for durable records.
