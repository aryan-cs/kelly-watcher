# Kelly Watcher

Kelly Watcher is a Polymarket copy-trading system with:

- a shadow-mode execution path that mirrors real entry and exit logic
- a live trading path guarded by risk controls and readiness checks
- an event-driven terminal dashboard
- wallet discovery and identity helper scripts
- model training and auto-retraining on resolved, fill-aware trades

The project is built around one rule: only real, fill-aware executed trades should count as evidence for performance, training, and live-readiness decisions.

## What The Bot Does

At a high level:

1. The bot polls Polymarket for trades from a configured watchlist of wallets.
2. Each incoming watched trade is normalized into a `TradeEvent` with real market metadata, order book data, and price history when available.
3. The bot scores the watched trader and the market.
4. It turns that into a confidence estimate using either:
   - the heuristic scorer, or
   - a trained XGBoost model if a current deployable model exists
5. It sizes the position with Kelly sizing and then applies risk guards.
6. It either:
   - skips the trade,
   - records a rejected signal, or
   - executes a shadow or live order
7. It logs everything to SQLite and emits a compact JSON event stream for the dashboard.
8. Later, resolved trades are graded and can become training data for the next model retrain.

## Repository Map

### Core runtime

- `main.py`: main bot loop, event emission, startup checks, scheduled retraining, bot-state file
- `tracker.py`: Polymarket data client for watched-wallet trades, market metadata, order books, and price history
- `signal_engine.py`: combines trader score + market score, uses heuristic or XGBoost path
- `executor.py`: shadow fills, live orders, partial exits, trade logging
- `evaluator.py`: resolves shadow trades and computes P&L summaries
- `dedup.py`: avoids duplicate/in-flight/already-open copies
- `db.py`: SQLite schema and schema migration helper
- `config.py`: all `.env` parsing and config validation
- `trade_contract.py`: shared SQL contract for what counts as executed/open/resolved trades

### Scoring and features

- `trader_scorer.py`: computes trader quality from cached, remote, and local history
- `market_scorer.py`: scores market quality from spread, depth, time-to-resolution, volume, concentration, and related inputs
- `features.py`: feature list and feature map used by training/inference
- `beliefs.py`: heuristic-prior adjustment layer learned from historical buckets
- `kelly.py`: Kelly sizing

### Training and maintenance

- `train.py`: loads fill-aware resolved trades, trains/calibrates model, writes `model.joblib`
- `auto_retrain.py`: scheduled and early retraining logic
- `alerter.py`: Telegram notifications

### Wallet discovery and identity helpers

- `resolve_wallet.py`: converts Polymarket profile URLs, handles, and wallet addresses into normalized watchlist wallets
- `rank_copytrade_wallets.py`: ranks leaderboard wallets by copy-tradability, not just raw leaderboard P&L
- `identity_cache.py`: wallet <-> username cache and resolver
- `polymarket_setup.py`: one-time live-wallet allowance setup for USDC collateral

### Dashboard

- `dashboard/dashboard.tsx`: the terminal app shell, keybindings, page routing
- `dashboard/pages/LiveFeed.tsx`: page 1, raw incoming watched trades
- `dashboard/pages/Signals.tsx`: page 2, scored/accepted/rejected signals
- `dashboard/pages/Performance.tsx`: page 3, open and past copied positions
- `dashboard/pages/Models.tsx`: page 4, model quality and training cycle
- `dashboard/pages/Wallets.tsx`: page 5, watched-wallet quality and stats
- `dashboard/pages/Settings.tsx`: page 6, live config/status view

### Tests

- `tests/test_runtime_fixes.py`: runtime regression coverage
- `tests/test_rank_copytrade_wallets.py`: wallet-ranking tests
- `tests/test_daily_pnl_close_timestamps.py`: daily P&L timestamp behavior

## Data Files

The bot writes and reads a small set of runtime files:

- `data/trading.db`: main SQLite database
- `data/events.jsonl`: dashboard event stream
- `data/bot_state.json`: lightweight runtime status for the dashboard
- `data/identity_cache.json`: wallet <-> username cache
- `model.joblib`: currently deployed trained model artifact
- `logs/bot.log`: rotating bot logs

Important note:

- `events.jsonl` is for the dashboard only
- `trade_log` inside `data/trading.db` is the durable source of truth

## Getting It Running

### 1. Install Python dependencies

```bash
uv sync
```

### 2. Create your env file

```bash
cp .env.example .env
```

At minimum, set:

- `WATCHED_WALLETS`
- `USE_REAL_MONEY=false` for shadow mode

If you want live mode later, you also need:

- `POLYGON_PRIVATE_KEY`
- `POLYGON_WALLET_ADDRESS`

### 3. Initialize the database

```bash
uv run python -c "from db import init_db; init_db()"
```

### 4. Start the backend

```bash
uv run python main.py
```

### 5. Start the dashboard

In a second terminal:

```bash
cd dashboard
npm install
npm start
```

The dashboard reads:

- `data/trading.db`
- `data/events.jsonl`
- `data/bot_state.json`
- `data/identity_cache.json`

If your terminal supports OSC-8 hyperlinks, market names on pages 1, 2, and 3 open the real Polymarket market page.

## Quick Operational Modes

### Shadow mode

This is the default.

- `USE_REAL_MONEY=false`
- no live orders are sent
- the bot still fetches live market data
- shadow entries/exits use order-book-aware fill simulation
- trades, skips, features, and P&L are logged normally

### Live mode

Enable only after shadow validation:

```bash
USE_REAL_MONEY=true
```

Live mode requires:

- a valid wallet address and matching private key
- Polymarket collateral approval
- enough shadow history if `LIVE_REQUIRE_SHADOW_HISTORY=true`
- healthy data feed / live account checks

Run the collateral setup once before live trading:

```bash
uv run python polymarket_setup.py
```

## Dashboard Guide

The terminal dashboard has 6 pages.

### Page 1: Tracker

Shows raw incoming watched-wallet trades before your bot decides whether to copy them.

Columns include:

- time
- watched username/wallet
- market name
- buy/sell action
- side
- price
- size

Use it to answer:

- Is the feed alive?
- Are the watched wallets still active?
- Are market names and trade sizes sane?

### Page 2: Signals

Shows the bot's scored decisions after filtering.

This is where you see:

- accepted signals
- rejected signals
- skipped signals
- confidence
- reason text

Use it to answer:

- Why was a trade copied or rejected?
- Is the confidence threshold too strict?
- Are risk checks or market vetoes firing too often?

### Page 3: Perf

Shows copied positions and historical outcomes.

This page is split between:

- current/open positions
- past/resolved positions

Use it to answer:

- What positions are still open?
- What has resolved already?
- What is shadow or live P&L doing?
- How long has capital been tied up?

### Page 4: Models

Explains the scoring system and shows model health.

It includes boxes for:

- prediction quality
- tracker health
- confidence calibration
- signal mode comparison
- heuristic/model composition
- retraining cadence

Use it to answer:

- Is the deployed model better than baseline?
- Is confidence calibrated?
- Is XGBoost outperforming the heuristic path?
- How often has the model retrained?

### Page 5: Wallets

Shows wallet-level quality information for watched traders.

It merges:

- observed local bot history from `trade_log`
- cached trader stats from `trader_cache`
- username mapping from `identity_cache.json`

Use it to answer:

- Which watched wallets are actually helping?
- Which wallets are stale or inactive?
- Which wallets have strong or weak resolved history?

### Page 6: Config

Shows runtime state plus editable config values.

It includes:

- mode
- poll interval
- bankroll
- database counts
- editable `.env` fields
- watched wallets from `.env`

Use it to answer:

- What config is active right now?
- Is the bot connected and polling?
- Did my `.env` update take effect?

## Dashboard Controls

Global controls:

- `1` to `6`: switch pages
- `r`: refresh
- `q`: quit dashboard

Page-specific controls:

- Tracker: `Up/Down` scroll, double-tap `Up` to jump latest
- Signals: `Up/Down` scroll, `Left/Right` pan long reason text
- Perf: arrows to change box, `j/k` to scroll positions, `Enter` for daily detail, `Esc` to close
- Models: arrows to move between boxes, `Enter` for help/detail
- Wallets: `Up/Down` select wallet, `Enter` detail, `Esc` close
- Config: arrows or `j/k` select editable field, `e` or `Enter` edit, `Esc` cancel

## How Wallet Discovery Works

There are two main helper flows:

### 1. Resolve a profile/handle/wallet into a watchlist entry

Use `resolve_wallet.py`.

It accepts:

- full Polymarket profile URLs
- `@handles`
- raw wallet addresses
- piped stdin

Examples:

```bash
uv run python resolve_wallet.py @tradername
uv run python resolve_wallet.py https://polymarket.com/@tradername
uv run python resolve_wallet.py 0xabc123...
printf '%s\n' '@alpha' '@beta' | uv run python resolve_wallet.py
```

What it does:

- normalizes wallet addresses
- resolves username -> wallet
- resolves wallet -> username
- prints a final comma-separated `WATCHED_WALLETS=...` line you can paste into `.env`

### 2. Find good wallets to track

Use `rank_copytrade_wallets.py`.

This script does more than scrape leaderboard P&L. It tries to answer: "Is this trader actually copyable?"

It pulls:

- leaderboard entries
- recent trades
- closed positions
- market close timestamps

Then it scores wallets using things like:

- realized performance
- win rate and ROI
- recent activity
- how often buys happen early enough to copy
- late-buy ratio
- minimum sample thresholds

Example:

```bash
uv run python rank_copytrade_wallets.py --top 10
```

Useful variants:

```bash
uv run python rank_copytrade_wallets.py --time-period WEEK --top 20
uv run python rank_copytrade_wallets.py --wallets-only
uv run python rank_copytrade_wallets.py --show-rejected
uv run python rank_copytrade_wallets.py --json-out ranked_wallets.json
```

The script prints:

- a ranked table
- acceptance/rejection summary
- a ready-to-paste `WATCHED_WALLETS=...` line

## Username <-> Wallet Mapping

Identity handling lives in `identity_cache.py`.

It keeps a bidirectional cache in `data/identity_cache.json`:

- wallet -> username
- username -> wallet

This cache is used by:

- `resolve_wallet.py`
- `tracker.py`
- dashboard wallet display

Behavior:

- if a clean observed username exists, it is remembered
- if a placeholder username is detected, it is ignored
- wallet lookups can resolve by scraping Polymarket profile pages
- handle lookups can resolve by scraping profile pages for wallet addresses

In practice:

- `resolve_wallet.py` is the operator-facing CLI
- `identity_cache.py` is the shared library backing it

## Trading Logic In More Detail

### Trade ingestion

`tracker.py`:

- polls `data-api.polymarket.com` for watched-wallet trades
- normalizes timestamps, price, size, token ID, side, and market ID
- fetches Gamma market metadata
- fetches CLOB order books
- fetches price history when available
- applies wallet cursors so old trades do not replay forever
- drops stale trades and invalid trades instead of inventing fallback values

### Trader scoring

`trader_scorer.py` builds trader features from:

- remote closed/open positions
- cached `trader_cache`
- local resolved trade history when needed

Features include:

- shrunk win rate
- trade count
- consistency
- volume
- average size
- diversity
- account age
- conviction ratio

### Market scoring

`market_scorer.py` builds a market quality score from:

- spread
- visible book depth
- time until resolution
- 1h momentum
- 24h volume
- 7d average volume trend
- open interest
- top-holder concentration

It also vetoes markets that are clearly unsafe or invalid, such as:

- crossed books
- missing books
- too little depth
- too close to expiry
- beyond your configured max horizon

### Signal generation

`signal_engine.py` then:

- runs the market veto first
- scores trader quality
- scores market quality
- combines them heuristically, or
- uses the deployed XGBoost model if a valid current artifact exists

The model artifact must match the current data contract or it is ignored.

### Sizing and execution

`kelly.py` sizes the trade.

`executor.py` then:

- simulates shadow fills from captured order books, or
- places live orders through the CLOB client
- supports partial exits
- logs every accepted or skipped trade into `trade_log`

### Resolution and retraining

`evaluator.py` resolves shadow trades when outcomes are known.

`train.py` then trains only on:

- resolved trades
- executed buys
- fill-aware rows

The model is only deployed if it clears internal validation checks.

`auto_retrain.py` handles:

- scheduled retrains
- early retrains after enough new resolved labels arrive

## Important Config Values

### Watchlist and mode

- `WATCHED_WALLETS`: comma-separated wallets to copy
- `USE_REAL_MONEY`: `false` for shadow, `true` for live

### Sizing and trade filters

- `MAX_BET_FRACTION`
- `MIN_CONFIDENCE`
- `MIN_BET_USD`
- `SHADOW_BANKROLL_USD`

### Live/shadow safety

- `MAX_LIVE_DRAWDOWN_PCT`
- `MAX_DAILY_LOSS_PCT`
- `MAX_TOTAL_OPEN_EXPOSURE_FRACTION`
- `MAX_MARKET_EXPOSURE_FRACTION`
- `MAX_TRADER_EXPOSURE_FRACTION`
- `MAX_LIVE_HEALTH_FAILURES`
- `LIVE_REQUIRE_SHADOW_HISTORY`
- `LIVE_MIN_SHADOW_RESOLVED`

### Feed/runtime behavior

- `POLL_INTERVAL_SECONDS`
- `MAX_SOURCE_TRADE_AGE`
- `MAX_FEED_STALENESS`
- `MAX_MARKET_HORIZON`

### Training

- `RETRAIN_BASE_CADENCE`
- `RETRAIN_HOUR_LOCAL`
- `RETRAIN_EARLY_CHECK_INTERVAL`
- `RETRAIN_MIN_NEW_LABELS`
- `MODEL_PATH`

### Alerts

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Common Commands

### Start the bot

```bash
uv run python main.py
```

### Start the dashboard

```bash
cd dashboard
npm start
```

### Initialize or migrate the database

```bash
uv run python -c "from db import init_db; init_db()"
```

### Resolve a profile or handle into wallets

```bash
uv run python resolve_wallet.py @someuser
```

### Rank wallets for a new watchlist

```bash
uv run python rank_copytrade_wallets.py --top 10
```

### Run the one-time live allowance setup

```bash
uv run python polymarket_setup.py
```

### Train the model manually

```bash
uv run python train.py
```

### Run tests

```bash
uv run python -m unittest discover -s tests
```

### Reset shadow state and restart

```bash
./restart_shadow.sh
```

Foreground mode:

```bash
./restart_shadow.sh --foreground
```

## How Shadow Reset Works

`restart_shadow.sh` is the safe reset helper for shadow mode.

It:

- refuses to run if `USE_REAL_MONEY=true`
- kills an existing `main.py` bot process
- clears shadow runtime state
- preserves your config, identity cache, logs, and model artifact
- reinitializes the database
- starts the bot again

It removes:

- `data/trading.db`
- WAL/SHM files
- `data/events.jsonl`
- `data/bot_state.json`
- `data/shadow_bot.pid`

## Testing And Validation

Current test entry point:

```bash
uv run python -m unittest discover -s tests
```

This repo also benefits from quick manual checks:

- backend boots cleanly in shadow mode
- dashboard starts and updates live
- incoming trades appear on page 1
- scored signals appear on page 2
- performance rows appear on page 3
- hyperlinks open real Polymarket pages on pages 1, 2, and 3

## Troubleshooting

### The dashboard is blank

Check that the backend is running and writing:

- `data/events.jsonl`
- `data/bot_state.json`
- `data/trading.db`

### No trades are coming in

Check:

- `WATCHED_WALLETS` is populated
- the watched wallets are active
- poll interval is not unrealistically low
- logs are not full of `429` errors

### The model is not being used

That usually means:

- `model.joblib` does not exist, or
- it was trained under an old data contract and is being ignored on purpose

In that case the bot falls back to the heuristic scorer.

### A username is wrong or missing

Use:

```bash
uv run python resolve_wallet.py <wallet-or-handle>
```

That will refresh the identity mapping and update `data/identity_cache.json`.

## Safety Notes

- Shadow mode is the default.
- Live mode should only be used after corrected shadow validation.
- Training, readiness, and evaluation are designed to rely on fill-aware executed trades, not hypothetical skipped rows.
- The live system still depends on real operational discipline: watchlist quality, exchange health, data feed health, and bankroll sizing all matter.

This repo is best treated as an operator system, not a fire-and-forget bot.
