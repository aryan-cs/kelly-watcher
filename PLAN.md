# Polymarket Copy-Trading System — Implementation Plan

> A production-ready guide to building an intelligent copy-trading bot for Polymarket prediction markets. Tracks specific trader profiles by wallet address, scores each trade opportunity using trader and market features, sizes positions with the Kelly Criterion, and either simulates or executes copy trades depending on the `USE_REAL_MONEY` flag. The system runs in shadow mode indefinitely — logging every signal, decision, and hypothetical outcome — so you accumulate a clean performance record before committing real capital. When the numbers look good, you flip one flag.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Environment Setup with uv](#3-environment-setup-with-uv)
4. [Configuration and the USE_REAL_MONEY Flag](#4-configuration-and-the-use_real_money-flag)
5. [Polymarket Profile Tracker](#5-polymarket-profile-tracker)
6. [SQLite Data Layer](#6-sqlite-data-layer)
7. [Trader Scorer](#7-trader-scorer)
8. [Market Scorer](#8-market-scorer)
9. [Combined Signal Engine](#9-combined-signal-engine)
10. [Kelly Criterion Sizing](#10-kelly-criterion-sizing)
11. [Deduplication Layer](#11-deduplication-layer)
12. [Order Executor and Shadow Mode](#12-order-executor-and-shadow-mode)
13. [XGBoost Model Pipeline](#13-xgboost-model-pipeline)
14. [Auto-Retraining and Self-Improvement](#14-auto-retraining-and-self-improvement)
15. [Performance Evaluator](#15-performance-evaluator)
16. [Monitoring and Alerting](#16-monitoring-and-alerting)
17. [Main Loop](#17-main-loop)
18. [Windows Deployment with uv](#18-windows-deployment-with-uv)
19. [Going Live Checklist](#19-going-live-checklist)
20. [Full Project File Structure](#20-full-project-file-structure)
21. [Terminal Dashboard (Ink)](#21-terminal-dashboard-ink)

---

## 1. Project Overview

### What this system does

This bot watches a list of Polymarket trader profiles (identified by Polygon wallet address). When a watched trader places a new trade, the system:

1. Fetches trader history and current market conditions
2. Scores both dimensions to produce a confidence value P ∈ [0, 1]
3. Runs P through the Kelly Criterion to compute an optimal USDC position size
4. Either **simulates** the trade (shadow mode, `USE_REAL_MONEY=false`) or **executes** it on-chain (`USE_REAL_MONEY=true`)
5. Logs every decision — including skipped trades and their reasons — to SQLite
6. Resolves all shadow trades once markets close and computes hypothetical P&L
7. Retrains the XGBoost model weekly on the accumulated labeled data

The key insight is that shadow mode is not just a safety guard — it is an active data collection pipeline. Every shadow trade that resolves is a labeled training example. The longer the bot runs in shadow mode, the better the model gets. When the shadow P&L numbers are consistently strong, you flip `USE_REAL_MONEY=true` and the exact same logic runs with real capital.

### Why Polymarket

- **Public blockchain data**: Every trade is on-chain on Polygon. Any wallet address can be tracked by anyone, with no authentication required.
- **Official Data API**: `data-api.polymarket.com` returns full trade history by wallet address with a simple GET request.
- **Binary contracts**: Every market resolves YES or NO, mapping perfectly to a binary classifier.
- **USDC settlement**: No fiat conversion — everything is USDC on Polygon.
- **Deep liquidity**: Polymarket is the largest prediction market by volume, with many well-capitalized traders worth copying.

### Why shadow mode matters

Most copy-trading bots go live immediately and lose money discovering their model is wrong. This system inverts that: the default state is shadow mode, and going live requires an explicit decision based on real performance data. The shadow P&L report gives you answers to:

- What is the model's actual win rate on signals it would have acted on?
- What is the hypothetical return on capital over the past N weeks?
- Which trader profiles are generating the best signals?
- Are the XGBoost probability estimates calibrated (i.e., when it says 70%, does it win 70% of the time)?

You answer these questions in shadow mode, then flip the flag.

---

## 2. Architecture

```
+-----------------------------------------------------------------------+
|  POLYMARKET PROFILE TRACKER                                           |
|  data-api.polymarket.com/trades?maker_address=0x...                   |
|  (one request per watched wallet, no auth required)                   |
|         |                                                             |
|         v                                                             |
|  Unified TradeEvent                                                   |
|  {trade_id, market_id, question, side, price,                        |
|   size_usd, token_id, trader_address, timestamp}                     |
+-------------------------+---------------------------------------------+
                          |
+-------------------------v---------------------------------------------+
|  SIGNAL ENGINE                                                        |
|                                                                       |
|  Trader Scorer              Market Scorer                             |
|  (win rate + Bayes,         (spread, depth, time,                    |
|   conviction, age,           momentum, volume trend,                 |
|   consistency, diversity)    OI concentration)                       |
|         |                        |                                   |
|         +----------+-------------+                                   |
|                    v                                                  |
|          P = weighted geometric mean                                  |
|          (or XGBoost.predict_proba once trained)                     |
+--------------------+--------------------------------------------------+
                     |
+--------------------v--------------------------------------------------+
|  SIZING ENGINE                                                        |
|  half-Kelly(P, market_price, bankroll)                               |
|  -> dollar_size, capped at MAX_BET_FRACTION                          |
+--------------------+--------------------------------------------------+
                     |
+--------------------v--------------------------------------------------+
|  DEDUP GATE                                                           |
|  already_seen? | pending_order? | open_position?                     |
+--------------------+--------------------------------------------------+
                     |
          +----------+----------+
          |                     |
+---------v--------+  +---------v-----------+
| USE_REAL_MONEY   |  | USE_REAL_MONEY       |
|    = false       |  |    = true            |
|                  |  |                      |
| Shadow executor  |  | Live executor        |
| Log hypothetical |  | Sign + submit        |
| trade to SQLite  |  | on-chain order       |
| (no USDC spent)  |  | (real USDC spent)    |
+--------+---------+  +---------+-----------+
         |                      |
         +----------+-----------+
                    |
+-------------------v---------------------------------------------------+
|  MONITORING                                                           |
|  Trade log -> P&L resolver -> Performance evaluator -> Telegram      |
|  Auto-retrain weekly on resolved labeled trades                       |
+-----------------------------------------------------------------------+
```

---

## 3. Environment Setup with uv

`uv` is a fast Python package manager that replaces `pip` + `venv`. It installs packages from a lockfile in seconds rather than minutes, and makes the project fully reproducible.

### Install uv

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify:
```powershell
uv --version
```

### Create the project

```bash
mkdir polymarket-bot
cd polymarket-bot
uv init
```

This creates `pyproject.toml` and a minimal project scaffold.

### Add dependencies

```bash
uv add httpx apscheduler xgboost scikit-learn pandas numpy joblib \
       python-dotenv requests \
       py-clob-client eth-account web3
```

For Telegram alerts:
```bash
uv add python-telegram-bot
```

`uv` manages Python dependencies only. The terminal dashboard is a separate Node.js application that lives in the `dashboard/` subfolder with its own `package.json`. Install its dependencies once:

```bash
cd dashboard
npm install
cd ..
```

The `dashboard/package.json` (create this manually in the `dashboard/` folder):

```json
{
  "name": "polymarket-dashboard",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "start": "tsx dashboard.tsx"
  },
  "dependencies": {
    "ink": "^5.0.1",
    "react": "^18.3.1",
    "better-sqlite3": "^9.4.3"
  },
  "devDependencies": {
    "@types/better-sqlite3": "^7.6.8",
    "@types/react": "^18.3.1",
    "tsx": "^4.7.1",
    "typescript": "^5.4.2"
  }
}
```

`better-sqlite3` is a synchronous Node.js binding to SQLite — it lets the dashboard query `data/trading.db` directly at 2-second intervals without any server or IPC. `tsx` runs TypeScript files directly without a build step.

`uv` will create `uv.lock` — commit this file to lock exact dependency versions.

### Running the project

Always use `uv run` instead of activating a venv manually:

```bash
uv run python main.py
uv run python train.py
uv run python polymarket_setup.py
```

`uv run` automatically uses the project's managed environment. No `venv\Scripts\activate` needed.

### `pyproject.toml`

After `uv add`, your `pyproject.toml` will look like this. You can also edit it directly:

```toml
[project]
name = "polymarket-bot"
version = "0.1.0"
description = "Polymarket intelligent copy-trading bot"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "apscheduler>=3.10",
    "xgboost>=2.0",
    "scikit-learn>=1.4",
    "pandas>=2.2",
    "numpy>=1.26",
    "joblib>=1.3",
    "python-dotenv>=1.0",
    "requests>=2.31",
    "py-clob-client>=0.16",
    "eth-account>=0.11",
    "web3>=6.15",
    "python-telegram-bot>=21.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## 4. Configuration and the USE_REAL_MONEY Flag

### `.env`

```
# ── Polymarket ────────────────────────────────────────────────────────
POLYGON_PRIVATE_KEY=0x_your_private_key_here
POLYGON_WALLET_ADDRESS=0x_your_wallet_address_here

# Comma-separated list of wallet addresses to copy-trade.
# Use resolve_wallet.py to convert profile URLs or @handles to addresses:
#   uv run python resolve_wallet.py
# Then paste the "WATCHED_WALLETS=..." output line here.
WATCHED_WALLETS=

# ── Trading mode ──────────────────────────────────────────────────────
# USE_REAL_MONEY=false: shadow mode — all signals are evaluated and logged
#   but NO on-chain transactions are submitted. USDC balance is untouched.
#   Hypothetical P&L is computed when markets resolve, giving you a real
#   performance record with zero financial risk.
#
# USE_REAL_MONEY=true:  live mode — signals that pass all gates are executed
#   as real on-chain orders. Only flip this after reviewing shadow P&L.
USE_REAL_MONEY=false

# ── Risk parameters ───────────────────────────────────────────────────
# Maximum fraction of bankroll to risk on any single trade (Kelly cap).
# 0.05 = 5%. Raise this carefully and only after sustained shadow profitability.
MAX_BET_FRACTION=0.05

# Minimum confidence score required to act on a signal (0.0-1.0).
MIN_CONFIDENCE=0.60

# Minimum dollar size — ignore Kelly outputs smaller than this.
MIN_BET_USD=1.00

# ── Telegram alerts ───────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# ── System ────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS=45
LOG_LEVEL=INFO

# Path to the trained XGBoost model file.
# The signal engine loads this automatically on startup if it exists.
# Generated by train.py — do not set manually.
MODEL_PATH=model.joblib

# Simulated starting bankroll for shadow mode (in USD).
# Kelly sizing uses this as the bankroll when USE_REAL_MONEY=false.
# Set this to roughly the amount you plan to deploy when going live,
# so your shadow trade sizes are realistic.
SHADOW_BANKROLL_USD=3000
```

### `.gitignore`

```
.env
.venv/
*.joblib
data/
logs/
__pycache__/
*.pyc
*.pyo
.python-version
```

### Config loader

```python
# config.py
import os
from dotenv import load_dotenv

load_dotenv()


def use_real_money() -> bool:
    """
    Returns True only if USE_REAL_MONEY is explicitly set to "true".
    Defaults to False for safety — shadow mode is the default state.
    """
    return os.getenv("USE_REAL_MONEY", "false").lower() == "true"


def max_bet_fraction() -> float:
    return float(os.getenv("MAX_BET_FRACTION", "0.05"))


def min_confidence() -> float:
    return float(os.getenv("MIN_CONFIDENCE", "0.60"))


def min_bet_usd() -> float:
    return float(os.getenv("MIN_BET_USD", "1.00"))


def poll_interval() -> int:
    return int(os.getenv("POLL_INTERVAL_SECONDS", "45"))


def private_key() -> str:
    return os.getenv("POLYGON_PRIVATE_KEY", "")


def wallet_address() -> str:
    return os.getenv("POLYGON_WALLET_ADDRESS", "")
```

---

## 5. Polymarket Profile Tracker

Every Polymarket user profile has a URL of the form `https://polymarket.com/profile/0x1234...`. The hex address is the wallet address and is all you need to track that user permanently.

### Finding wallet addresses

Three ways:
1. **Profile URL**: visit someone's profile, copy the hex from the URL
2. **Leaderboard**: `data-api.polymarket.com/leaderboard?window=1w&limit=50` returns ranked traders with wallet addresses
3. **Market activity**: watch a market you care about and note large fills

### Tracker module

```python
# tracker.py
import time
import httpx
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"


@dataclass
class TradeEvent:
    trade_id:       str
    market_id:      str    # conditionId hex
    question:       str    # human-readable market title
    side:           str    # "yes" or "no" (normalized)
    price:          float  # 0.0-1.0
    size_usd:       float  # USDC size of the observed trade
    token_id:       str    # outcome token ID — used to place copy order
    trader_address: str    # wallet address of the trader being copied
    timestamp:      int    # unix seconds
    close_time:     str    # ISO8601 market close time
    snapshot:       Optional[dict] = field(default=None)


class PolymarketTracker:
    def __init__(self, wallet_addresses: list[str]):
        self.wallets   = [a.lower() for a in wallet_addresses]
        self.client    = httpx.Client(timeout=15.0, follow_redirects=True)
        self.seen_ids: set[str] = set()

    def add_wallet(self, address: str):
        a = address.lower()
        if a not in self.wallets:
            self.wallets.append(a)
            logger.info(f"Added wallet to watchlist: {a}")

    # ── Leaderboard ─────────────────────────────────────────────────────

    def get_leaderboard(self, window: str = "1w", limit: int = 50) -> list[dict]:
        """
        Fetch top traders. window options: "1d", "1w", "1m", "all".
        Each entry has: address, profit, volume, marketsTraded.
        Use this to auto-populate the watchlist from top performers.
        """
        try:
            resp = self.client.get(
                f"{DATA_API}/leaderboard",
                params={"window": window, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Leaderboard fetch failed: {e}")
            return []

    def add_top_traders(self, window: str = "1w", top_n: int = 20):
        """Add the top N leaderboard traders to the watchlist."""
        leaders = self.get_leaderboard(window=window, limit=top_n)
        for entry in leaders:
            addr = entry.get("address", "")
            if addr:
                self.add_wallet(addr)
        logger.info(f"Watchlist updated from leaderboard: {len(self.wallets)} wallets total")

    # ── Trade fetching ───────────────────────────────────────────────────

    def get_wallet_trades(self, address: str, limit: int = 50) -> list[dict]:
        """Fetch recent trades for a wallet. No auth required."""
        try:
            resp = self.client.get(
                f"{DATA_API}/trades",
                params={"maker_address": address, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Trade fetch failed for {address[:10]}...: {e}")
            return []

    def get_wallet_positions(self, address: str) -> list[dict]:
        """Fetch current open positions for a wallet. Used by dedup layer."""
        try:
            resp = self.client.get(
                f"{DATA_API}/positions",
                params={"user": address},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Position fetch failed for {address[:10]}...: {e}")
            return []

    # ── Market data ──────────────────────────────────────────────────────

    def get_market_metadata(self, condition_id: str) -> dict:
        """Fetch market title, close time, and resolution status."""
        try:
            resp = self.client.get(
                f"{GAMMA_API}/markets",
                params={"condition_id": condition_id},
            )
            resp.raise_for_status()
            markets = resp.json()
            return markets[0] if markets else {}
        except Exception as e:
            logger.error(f"Market metadata fetch failed ({condition_id[:12]}...): {e}")
            return {}

    def get_orderbook_snapshot(self, token_id: str) -> Optional[dict]:
        """Fetch current order book for an outcome token."""
        try:
            resp = self.client.get(
                f"{CLOB_API}/book",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            book     = resp.json()
            bids     = book.get("bids", [])
            asks     = book.get("asks", [])
            best_bid = float(bids[0]["price"]) if bids else 0.01
            best_ask = float(asks[0]["price"]) if asks else 0.99
            return {
                "best_bid":      best_bid,
                "best_ask":      best_ask,
                "mid":           (best_bid + best_ask) / 2,
                "bid_depth_usd": sum(float(b["size"]) * float(b["price"]) for b in bids[:5]),
                "ask_depth_usd": sum(float(a["size"]) * float(a["price"]) for a in asks[:5]),
            }
        except Exception as e:
            logger.error(f"Orderbook fetch failed ({token_id[:12]}...): {e}")
            return None

    def get_price_history(self, token_id: str, interval: str = "1h") -> list[dict]:
        """Fetch recent price history for momentum scoring."""
        try:
            resp = self.client.get(
                f"{CLOB_API}/prices-history",
                params={"token_id": token_id, "interval": interval},
            )
            resp.raise_for_status()
            return resp.json().get("history", [])
        except Exception as e:
            logger.warning(f"Price history fetch failed ({token_id[:12]}...): {e}")
            return []

    # ── Event parsing ────────────────────────────────────────────────────

    def _parse_raw_trade(self, raw: dict, address: str) -> Optional[TradeEvent]:
        try:
            condition_id = raw.get("conditionId", "")
            outcome      = raw.get("outcome", "YES").upper()
            side         = "yes" if outcome == "YES" else "no"
            price        = float(raw.get("price", 0.5))
            size         = float(raw.get("size", 0))
            token_id     = raw.get("asset_id", raw.get("tokenId", ""))

            if not condition_id or size <= 0 or not token_id:
                return None

            meta       = self.get_market_metadata(condition_id)
            close_time = meta.get("endDate", meta.get("closeTime", ""))
            question   = meta.get("question", meta.get("title", condition_id))

            return TradeEvent(
                trade_id=raw.get("id", ""),
                market_id=condition_id,
                question=question,
                side=side,
                price=price,
                size_usd=size,
                token_id=token_id,
                trader_address=address,
                timestamp=int(raw.get("timestamp", time.time())),
                close_time=close_time,
            )
        except Exception as e:
            logger.warning(f"Failed to parse trade event: {e}")
            return None

    # ── Main poll ────────────────────────────────────────────────────────

    def poll(self) -> list[TradeEvent]:
        """
        Poll all watched wallets. Returns new unseen trades sorted oldest-first.
        Call this every POLL_INTERVAL_SECONDS from the main loop.
        """
        new_events: list[TradeEvent] = []

        for address in self.wallets:
            raw_trades = self.get_wallet_trades(address)
            for raw in raw_trades:
                trade_id = raw.get("id", "")
                if not trade_id or trade_id in self.seen_ids:
                    continue
                self.seen_ids.add(trade_id)
                event = self._parse_raw_trade(raw, address)
                if event is None:
                    continue

                # Attach order book snapshot
                snap = self.get_orderbook_snapshot(event.token_id)
                if snap:
                    event.snapshot = snap

                # Attach 1h price history for momentum scoring
                history = self.get_price_history(event.token_id, interval="1h")
                if history:
                    event.snapshot = event.snapshot or {}
                    event.snapshot["price_history_1h"] = history

                new_events.append(event)

        new_events.sort(key=lambda e: e.timestamp)
        return new_events
```

---

## 6. SQLite Data Layer

All state — seen trades, open positions, trade log, model performance history — lives in a single SQLite file at `data/trading.db`.

```python
# db.py
import sqlite3
import os

DB_PATH = "data/trading.db"


def get_conn() -> sqlite3.Connection:
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safer concurrent writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""

        -- Rolling 24h window of trade IDs we've already processed
        CREATE TABLE IF NOT EXISTS seen_trades (
            trade_id   TEXT PRIMARY KEY,
            market_id  TEXT NOT NULL,
            trader_id  TEXT NOT NULL,
            seen_at    INTEGER NOT NULL
        );

        -- Every signal evaluated by the bot, whether acted on or not.
        -- Shadow trades have order_id = NULL and real_money = 0.
        -- This is the primary training dataset for XGBoost.
        CREATE TABLE IF NOT EXISTS trade_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id            TEXT NOT NULL,
            market_id           TEXT NOT NULL,
            question            TEXT,
            trader_address      TEXT NOT NULL,
            side                TEXT NOT NULL,
            price_at_signal     REAL NOT NULL,
            signal_size_usd     REAL NOT NULL,   -- Kelly-computed size
            confidence          REAL NOT NULL,
            kelly_fraction      REAL NOT NULL,
            real_money          INTEGER NOT NULL DEFAULT 0,   -- 0=shadow, 1=live
            order_id            TEXT,            -- NULL for shadow trades
            skipped             INTEGER NOT NULL DEFAULT 0,   -- 1 if signal was filtered
            skip_reason         TEXT,
            placed_at           INTEGER NOT NULL,
            resolved_at         INTEGER,
            outcome             INTEGER,         -- 1=win, 0=loss, NULL=unresolved
            shadow_pnl_usd      REAL,            -- hypothetical P&L (shadow mode)
            actual_pnl_usd      REAL,            -- real P&L (live mode)

            -- Feature snapshot at signal time (used for XGBoost training)
            f_trader_win_rate   REAL,
            f_trader_n_trades   INTEGER,
            f_conviction_ratio  REAL,
            f_trader_volume_usd REAL,
            f_account_age_days  INTEGER,
            f_consistency       REAL,
            f_days_to_res       REAL,
            f_price             REAL,
            f_spread_pct        REAL,
            f_momentum_1h       REAL,
            f_volume_trend      REAL,
            f_oi_usd            REAL,
            f_bid_depth_usd     REAL,
            f_ask_depth_usd     REAL
        );

        -- Current open positions (synced from wallet on startup and periodically)
        CREATE TABLE IF NOT EXISTS positions (
            market_id   TEXT PRIMARY KEY,
            side        TEXT NOT NULL,
            size_usd    REAL NOT NULL,
            avg_price   REAL NOT NULL,
            token_id    TEXT NOT NULL,
            entered_at  INTEGER NOT NULL,
            real_money  INTEGER NOT NULL DEFAULT 0
        );

        -- Trader stat cache (rebuilt from trade_log, TTL 1 hour)
        CREATE TABLE IF NOT EXISTS trader_cache (
            trader_address TEXT PRIMARY KEY,
            win_rate       REAL NOT NULL,
            n_trades       INTEGER NOT NULL,
            consistency    REAL NOT NULL,
            volume_usd     REAL NOT NULL,
            avg_size_usd   REAL NOT NULL,
            diversity      INTEGER NOT NULL,
            account_age_d  INTEGER NOT NULL,
            updated_at     INTEGER NOT NULL
        );

        -- Model version history (track every retrain)
        CREATE TABLE IF NOT EXISTS model_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            trained_at      INTEGER NOT NULL,
            n_samples       INTEGER NOT NULL,
            brier_score     REAL NOT NULL,
            log_loss        REAL NOT NULL,
            feature_cols    TEXT NOT NULL,   -- JSON array
            model_path      TEXT NOT NULL,
            deployed        INTEGER NOT NULL DEFAULT 0
        );

        -- Weekly performance snapshots for the dashboard
        CREATE TABLE IF NOT EXISTS perf_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at     INTEGER NOT NULL,
            mode            TEXT NOT NULL,     -- "shadow" or "live"
            n_signals       INTEGER NOT NULL,
            n_acted         INTEGER NOT NULL,
            n_resolved      INTEGER NOT NULL,
            win_rate        REAL,
            total_pnl_usd   REAL,
            avg_confidence  REAL,
            sharpe          REAL
        );

        CREATE INDEX IF NOT EXISTS idx_seen_trades_seen_at    ON seen_trades(seen_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_placed_at    ON trade_log(placed_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_outcome      ON trade_log(outcome);
        CREATE INDEX IF NOT EXISTS idx_trade_log_trader       ON trade_log(trader_address);
        CREATE INDEX IF NOT EXISTS idx_trade_log_real_money   ON trade_log(real_money);
        CREATE INDEX IF NOT EXISTS idx_trade_log_skipped      ON trade_log(skipped);
    """)
    conn.commit()
    conn.close()
    print("Database initialized.")
```

---

## 7. Trader Scorer

```python
# trader_scorer.py
import json
import time
import numpy as np
from dataclasses import dataclass
from db import get_conn


@dataclass
class TraderFeatures:
    win_rate:        float   # Bayesian-smoothed win rate (0-1)
    n_trades:        int     # resolved trades in history
    consistency:     float   # Sharpe-like: mean_return / std_return
    account_age_d:   int     # days since first observed trade
    volume_usd:      float   # total USDC traded (all time)
    avg_size_usd:    float   # average trade size
    diversity:       int     # unique markets traded
    conviction_ratio: float  # this trade size / avg_size_usd


def get_trader_features(trader_address: str,
                        observed_size_usd: float) -> TraderFeatures:
    """
    Build TraderFeatures from cached stats or compute from trade_log.
    Falls back to conservative defaults for unknown traders.
    """
    conn = get_conn()

    # Try cache first (1-hour TTL)
    row = conn.execute(
        "SELECT * FROM trader_cache WHERE trader_address=? AND updated_at>?",
        (trader_address.lower(), int(time.time()) - 3600)
    ).fetchone()

    if row:
        conn.close()
        avg = row["avg_size_usd"] or observed_size_usd
        return TraderFeatures(
            win_rate=row["win_rate"],
            n_trades=row["n_trades"],
            consistency=row["consistency"],
            account_age_d=row["account_age_d"],
            volume_usd=row["volume_usd"],
            avg_size_usd=avg,
            diversity=row["diversity"],
            conviction_ratio=observed_size_usd / avg if avg > 0 else 1.0,
        )

    # Compute from trade_log
    rows = conn.execute(
        """SELECT outcome, signal_size_usd, placed_at, market_id
           FROM trade_log
           WHERE trader_address=? AND outcome IS NOT NULL AND skipped=0
           ORDER BY placed_at DESC LIMIT 500""",
        (trader_address.lower(),)
    ).fetchall()
    conn.close()

    if not rows:
        return TraderFeatures(
            win_rate=0.5, n_trades=0, consistency=0.0,
            account_age_d=0, volume_usd=0.0,
            avg_size_usd=observed_size_usd,
            diversity=0, conviction_ratio=1.0,
        )

    wins        = sum(1 for r in rows if r["outcome"] == 1)
    returns     = [1.0 if r["outcome"] == 1 else -1.0 for r in rows]
    std         = float(np.std(returns)) if len(returns) > 1 else 1.0
    consistency = float(np.mean(returns)) / (std + 1e-6)
    sizes       = [r["signal_size_usd"] for r in rows]
    avg_size    = float(np.mean(sizes)) if sizes else observed_size_usd
    first_ts    = min(r["placed_at"] for r in rows)
    age_days    = int((time.time() - first_ts) / 86400)
    diversity   = len(set(r["market_id"] for r in rows))
    total_vol   = float(sum(sizes))

    # Bayesian-smoothed win rate
    prior     = 20
    win_rate  = (wins + 0.5 * prior) / (len(rows) + prior)

    # Update cache
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO trader_cache
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (trader_address.lower(), win_rate, len(rows), consistency,
         total_vol, avg_size, diversity, age_days, int(time.time()))
    )
    conn.commit()
    conn.close()

    return TraderFeatures(
        win_rate=win_rate, n_trades=len(rows), consistency=consistency,
        account_age_d=age_days, volume_usd=total_vol, avg_size_usd=avg_size,
        diversity=diversity,
        conviction_ratio=observed_size_usd / avg_size if avg_size > 0 else 1.0,
    )


class TraderScorer:
    """
    Scores a trader's reliability on a 0-1 scale.
    Weights are hand-tuned starting values; XGBoost supersedes this once trained.
    """

    WEIGHTS = {
        "win_rate":    0.30,
        "consistency": 0.15,
        "age":         0.10,
        "conviction":  0.15,
        "diversity":   0.05,
        # remaining 0.25 comes from market scorer in combined signal
    }

    def _score_win_rate(self, win_rate: float, n_trades: int) -> float:
        # Already Bayesian-smoothed in get_trader_features; just return it.
        return float(np.clip(win_rate, 0, 1))

    def _score_consistency(self, sharpe: float) -> float:
        # Sharpe < 0 -> 0, Sharpe >= 3 -> 1
        return float(np.clip(sharpe / 3.0, 0, 1))

    def _score_age(self, days: int) -> float:
        # Log scale: 30d -> 0.30, 180d -> 0.70, 365d -> 1.0
        return float(np.clip(np.log1p(days) / np.log1p(365), 0, 1))

    def _score_conviction(self, ratio: float) -> float:
        # Sigmoid centered at 1.0. 3x normal size -> ~0.98. 0.3x -> ~0.08.
        return float(1 / (1 + np.exp(-2 * (ratio - 1))))

    def _score_diversity(self, n_markets: int) -> float:
        return float(np.clip(n_markets / 10, 0, 1))

    def score(self, f: TraderFeatures) -> dict:
        components = {
            "win_rate":    self._score_win_rate(f.win_rate, f.n_trades),
            "consistency": self._score_consistency(f.consistency),
            "age":         self._score_age(f.account_age_d),
            "conviction":  self._score_conviction(f.conviction_ratio),
            "diversity":   self._score_diversity(f.diversity),
        }
        total_w    = sum(self.WEIGHTS.values())
        confidence = sum((self.WEIGHTS[k] / total_w) * v for k, v in components.items())
        return {
            "score":      round(float(confidence), 4),
            "components": {k: round(v, 3) for k, v in components.items()},
        }
```

---

## 8. Market Scorer

```python
# market_scorer.py
import numpy as np
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class MarketFeatures:
    best_bid:          float
    best_ask:          float
    mid:               float
    bid_depth_usd:     float
    ask_depth_usd:     float
    days_to_res:       float
    price_1h_ago:      float   # None if unavailable -> uses mid
    volume_24h_usd:    float
    volume_7d_avg_usd: float
    oi_usd:            float
    top_holder_pct:    float   # fraction of OI held by largest holder
    order_size_usd:    float   # Kelly-estimated size (for depth impact scoring)


def build_market_features(
    snapshot: dict,
    close_time_iso: str,
    order_size_usd: float,
) -> Optional[MarketFeatures]:
    """Build MarketFeatures from an orderbook snapshot dict."""
    if not snapshot:
        return None

    mid = snapshot.get("mid", (snapshot["best_bid"] + snapshot["best_ask"]) / 2)

    if close_time_iso:
        try:
            close_dt  = datetime.fromisoformat(close_time_iso.replace("Z", ""))
            days_to_res = max((close_dt - datetime.utcnow()).total_seconds() / 86400, 0)
        except Exception:
            days_to_res = 7.0   # fallback
    else:
        days_to_res = 7.0

    # 1h price from snapshot price history (attached by tracker)
    history = snapshot.get("price_history_1h", [])
    if history:
        price_1h_ago = float(history[0].get("p", mid))
    else:
        price_1h_ago = mid

    return MarketFeatures(
        best_bid=snapshot["best_bid"],
        best_ask=snapshot["best_ask"],
        mid=mid,
        bid_depth_usd=snapshot.get("bid_depth_usd", 0.0),
        ask_depth_usd=snapshot.get("ask_depth_usd", 0.0),
        days_to_res=days_to_res,
        price_1h_ago=price_1h_ago,
        volume_24h_usd=snapshot.get("volume_24h_usd", 0.0),
        volume_7d_avg_usd=snapshot.get("volume_7d_avg_usd", 1.0),
        oi_usd=snapshot.get("oi_usd", 0.0),
        top_holder_pct=snapshot.get("top_holder_pct", 0.1),
        order_size_usd=order_size_usd,
    )


class MarketScorer:
    WEIGHTS = {
        "spread":       0.25,
        "depth":        0.20,
        "time":         0.20,
        "momentum":     0.15,
        "vol_trend":    0.10,
        "oi_conc":      0.10,
    }

    # ── Hard disqualifiers ───────────────────────────────────────────────
    # These veto a trade before any weighted scoring.

    def _veto(self, f: MarketFeatures) -> Optional[str]:
        spread = (f.best_ask - f.best_bid) / f.mid if f.mid > 0 else 1.0
        if spread > 0.10:
            return f"spread {spread:.1%} > 10%"
        if f.days_to_res < 0.25:
            return "expires in <6h"
        if f.volume_24h_usd < 100:
            return "volume_24h <$100"
        if f.top_holder_pct > 0.75:
            return "OI >75% held by one trader"
        if f.mid <= 0.02 or f.mid >= 0.98:
            return "market near resolution already"
        return None

    def _score_spread(self, f: MarketFeatures) -> float:
        spread = (f.best_ask - f.best_bid) / f.mid if f.mid > 0 else 1.0
        return float(np.clip(1 - spread / 0.05, 0, 1))

    def _score_depth(self, f: MarketFeatures) -> float:
        depth = (f.bid_depth_usd + f.ask_depth_usd) / 2
        if depth <= 0:
            return 0.0
        return float(np.clip(1 - f.order_size_usd / depth, 0, 1))

    def _score_time(self, f: MarketFeatures) -> float:
        d = f.days_to_res
        if d < 0.5:
            return 0.0
        if d < 3:
            return float(np.interp(d, [0.5, 3.0], [0.1, 1.0]))
        if d <= 14:
            return 1.0
        return float(np.clip(1 - (d - 14) / 90, 0.4, 1.0))

    def _score_momentum(self, f: MarketFeatures) -> float:
        if f.price_1h_ago <= 0:
            return 0.5
        move = abs(f.mid - f.price_1h_ago) / f.price_1h_ago
        return float(np.clip(1 - move / 0.05, 0.2, 1.0))

    def _score_vol_trend(self, f: MarketFeatures) -> float:
        avg = f.volume_7d_avg_usd
        if avg <= 0:
            return 0.3
        ratio = f.volume_24h_usd / avg
        return float(np.clip(np.interp(ratio, [0.3, 1.0, 1.5], [0.0, 0.7, 1.0]), 0, 1))

    def _score_oi_conc(self, f: MarketFeatures) -> float:
        return float(np.clip(1 - f.top_holder_pct / 0.6, 0, 1))

    def score(self, f: MarketFeatures) -> dict:
        veto = self._veto(f)
        if veto:
            return {"score": 0.0, "veto": veto, "components": {}}

        components = {
            "spread":    self._score_spread(f),
            "depth":     self._score_depth(f),
            "time":      self._score_time(f),
            "momentum":  self._score_momentum(f),
            "vol_trend": self._score_vol_trend(f),
            "oi_conc":   self._score_oi_conc(f),
        }
        score = sum(self.WEIGHTS[k] * v for k, v in components.items())
        return {
            "score":      round(score, 4),
            "veto":       None,
            "components": {k: round(v, 3) for k, v in components.items()},
        }
```

---

## 9. Combined Signal Engine

```python
# signal_engine.py
import os
import numpy as np
import logging
from trader_scorer import TraderScorer, TraderFeatures
from market_scorer import MarketScorer, MarketFeatures

logger = logging.getLogger(__name__)

TRADER_WEIGHT = 0.60
MARKET_WEIGHT = 0.40


class SignalEngine:
    """
    Combines trader and market scores into a single confidence value.
    Uses a weighted geometric mean so a near-zero score on either input
    collapses the combined output — a good trader in a bad market is a bad copy.

    Swaps to XGBoost automatically when model.joblib is present and valid.
    """

    def __init__(self):
        self.trader_scorer = TraderScorer()
        self.market_scorer = MarketScorer()
        self._xgb          = None
        self._xgb_cols     = None
        self._try_load_xgb()

    def _try_load_xgb(self):
        model_path = os.getenv("MODEL_PATH", "model.joblib")
        if not os.path.exists(model_path):
            logger.info("No XGBoost model found — using heuristic scorer")
            return
        try:
            import joblib
            self._xgb, self._xgb_cols = joblib.load(model_path)
            logger.info(f"XGBoost model loaded from {model_path}")
        except Exception as e:
            logger.warning(f"Failed to load XGBoost model: {e} — using heuristic scorer")

    def reload_model(self):
        """Call after retraining to hot-swap the model without restarting."""
        self._xgb = None
        self._try_load_xgb()

    def evaluate(
        self,
        trader_features: TraderFeatures,
        market_features: MarketFeatures,
        order_size_usd: float = 10.0,
    ) -> dict:
        """
        Returns a dict with keys:
          confidence: float in [0, 1]
          passed:     bool (confidence >= MIN_CONFIDENCE)
          veto:       str or None
          mode:       "xgboost" or "heuristic"
          trader:     sub-scores dict
          market:     sub-scores dict
        """
        from config import min_confidence

        market_result = self.market_scorer.score(market_features)

        if market_result["veto"]:
            return {
                "confidence": 0.0, "passed": False,
                "veto": market_result["veto"], "mode": "veto",
                "trader": {}, "market": market_result,
            }

        if self._xgb is not None:
            return self._evaluate_xgb(trader_features, market_features, order_size_usd)

        return self._evaluate_heuristic(trader_features, market_features, market_result)

    def _evaluate_heuristic(self, tf: TraderFeatures,
                              mf: MarketFeatures, market_result: dict) -> dict:
        from config import min_confidence
        trader_result = self.trader_scorer.score(tf)
        t = trader_result["score"]
        m = market_result["score"]
        if t <= 0 or m <= 0:
            combined = 0.0
        else:
            combined = float(np.exp(
                TRADER_WEIGHT * np.log(t) + MARKET_WEIGHT * np.log(m)
            ))
        return {
            "confidence": round(combined, 4),
            "passed":     combined >= min_confidence(),
            "veto":       None,
            "mode":       "heuristic",
            "trader":     trader_result,
            "market":     market_result,
        }

    def _evaluate_xgb(self, tf: TraderFeatures,
                       mf: MarketFeatures, order_size_usd: float) -> dict:
        from config import min_confidence
        import numpy as np

        spread = (mf.best_ask - mf.best_bid) / mf.mid if mf.mid > 0 else 1.0
        momentum = abs(mf.mid - mf.price_1h_ago) / mf.price_1h_ago if mf.price_1h_ago > 0 else 0
        vol_trend = mf.volume_24h_usd / (mf.volume_7d_avg_usd + 1e-6)

        features = np.array([[
            tf.win_rate, tf.n_trades, tf.conviction_ratio,
            tf.volume_usd, tf.account_age_d, tf.consistency,
            mf.days_to_res, mf.mid, spread, momentum,
            vol_trend, mf.oi_usd, mf.bid_depth_usd, mf.ask_depth_usd,
        ]])

        confidence = float(self._xgb.predict_proba(features)[0, 1])
        return {
            "confidence": round(confidence, 4),
            "passed":     confidence >= min_confidence(),
            "veto":       None,
            "mode":       "xgboost",
            "trader":     {},
            "market":     {},
        }


# Feature column names — must match the array order in _evaluate_xgb
XGB_FEATURE_COLS = [
    "f_trader_win_rate", "f_trader_n_trades", "f_conviction_ratio",
    "f_trader_volume_usd", "f_account_age_days", "f_consistency",
    "f_days_to_res", "f_price", "f_spread_pct", "f_momentum_1h",
    "f_volume_trend", "f_oi_usd", "f_bid_depth_usd", "f_ask_depth_usd",
]
```

---

## 10. Kelly Criterion Sizing

For a binary outcome market priced at `p`, the Kelly formula is:

```
f* = (p_win × (b + 1) - 1) / b      where b = (1 - p) / p
```

Always use half-Kelly. Full Kelly assumes your probabilities are perfectly calibrated — they aren't. Half-Kelly captures ~75% of the theoretical optimal growth rate with dramatically lower variance.

```python
# kelly.py
import numpy as np
from config import max_bet_fraction, min_bet_usd, min_confidence

KELLY_FRACTION = 0.5   # always half-Kelly


def kelly_size(
    confidence: float,
    market_price: float,
    bankroll_usd: float,
) -> dict:
    """
    Returns:
      dollar_size:   USDC to spend (0.0 if no bet)
      kelly_f:       the capped Kelly fraction used
      full_kelly_f:  the raw uncapped Kelly fraction
      reason:        "ok" or a skip reason string
    """
    if confidence < min_confidence():
        return _no_bet(f"conf {confidence:.3f} < min {min_confidence():.2f}")

    if not (0.01 < market_price < 0.99):
        return _no_bet(f"invalid price {market_price:.3f}")

    b      = (1 - market_price) / market_price
    f_star = (confidence * (b + 1) - 1) / b

    if f_star <= 0:
        return _no_bet("negative Kelly — no edge at this price/confidence")

    f_scaled = f_star * KELLY_FRACTION
    f_capped = min(f_scaled, max_bet_fraction())
    size     = round(bankroll_usd * f_capped, 2)

    if size < min_bet_usd():
        return _no_bet(f"size ${size:.2f} < min ${min_bet_usd():.2f}")

    return {
        "dollar_size":  size,
        "kelly_f":      round(f_capped, 5),
        "full_kelly_f": round(f_star, 5),
        "reason":       "ok",
    }


def _no_bet(reason: str) -> dict:
    return {"dollar_size": 0.0, "kelly_f": 0.0, "full_kelly_f": 0.0, "reason": reason}
```

---

## 11. Deduplication Layer

```python
# dedup.py
import time
import logging
from dataclasses import dataclass, field
from db import get_conn

logger = logging.getLogger(__name__)

PENDING_TIMEOUT = 30       # seconds before pending order is released
SEEN_WINDOW     = 86400    # 24h rolling window


@dataclass
class DedupeCache:
    seen_ids:       set  = field(default_factory=set)
    open_positions: dict = field(default_factory=dict)   # market_id -> {side, size}
    pending:        dict = field(default_factory=dict)   # market_id -> timestamp

    def load_from_db(self):
        conn   = get_conn()
        cutoff = int(time.time()) - SEEN_WINDOW
        rows   = conn.execute(
            "SELECT trade_id FROM seen_trades WHERE seen_at > ?", (cutoff,)
        ).fetchall()
        self.seen_ids = {r["trade_id"] for r in rows}

        rows = conn.execute("SELECT * FROM positions").fetchall()
        self.open_positions = {
            r["market_id"]: {"side": r["side"], "size": r["size_usd"]}
            for r in rows
        }
        conn.close()
        logger.info(f"Dedup cache loaded: {len(self.seen_ids)} seen, "
                    f"{len(self.open_positions)} open positions")

    def sync_positions_from_api(self, tracker, our_address: str):
        """Sync open positions from the Polymarket API for our own wallet."""
        conn = get_conn()
        conn.execute("DELETE FROM positions")
        self.open_positions = {}
        for pos in tracker.get_wallet_positions(our_address):
            size = float(pos.get("size", 0))
            if size <= 0:
                continue
            mid       = pos.get("market_id") or pos.get("conditionId", "")
            outcome   = pos.get("outcome", "YES").upper()
            side      = "yes" if outcome == "YES" else "no"
            token_id  = pos.get("asset_id", "")
            avg_price = float(pos.get("avgPrice", 0.5))
            self.open_positions[mid] = {"side": side, "size": size}
            conn.execute(
                "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
                (mid, side, size, avg_price, token_id, int(time.time()), 0)
            )
        conn.commit()
        conn.close()

    def gate(self, trade_id: str, market_id: str, side: str,
             confidence: float) -> tuple[bool, str]:
        """
        Returns (ok, reason). All four checks must pass.
        """
        from config import min_confidence
        if trade_id in self.seen_ids:
            return False, "duplicate trade_id"
        if self._has_pending(market_id):
            return False, "order in-flight"
        if self._has_position(market_id, side):
            return False, "position already open"
        if confidence < min_confidence():
            return False, f"conf {confidence:.3f} below threshold"
        return True, "ok"

    def _has_pending(self, market_id: str) -> bool:
        ts = self.pending.get(market_id)
        if ts is None:
            return False
        if time.time() - ts > PENDING_TIMEOUT:
            del self.pending[market_id]
            return False
        return True

    def _has_position(self, market_id: str, side: str) -> bool:
        pos = self.open_positions.get(market_id)
        return pos is not None and pos["side"] == side

    def mark_seen(self, trade_id: str, market_id: str, trader_id: str):
        self.seen_ids.add(trade_id)
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO seen_trades VALUES (?,?,?,?)",
            (trade_id, market_id, trader_id, int(time.time()))
        )
        conn.execute(
            "DELETE FROM seen_trades WHERE seen_at < ?",
            (int(time.time()) - SEEN_WINDOW,)
        )
        conn.commit()
        conn.close()

    def mark_pending(self, market_id: str):
        self.pending[market_id] = time.time()

    def confirm(self, market_id: str, side: str, size_usd: float,
                token_id: str, real_money: bool):
        self.pending.pop(market_id, None)
        self.open_positions[market_id] = {"side": side, "size": size_usd}
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
            (market_id, side, size_usd, 0.0, token_id, int(time.time()),
             1 if real_money else 0)
        )
        conn.commit()
        conn.close()

    def release(self, market_id: str):
        """Release pending lock on order failure."""
        self.pending.pop(market_id, None)

    def clear_position(self, market_id: str):
        self.open_positions.pop(market_id, None)
        conn = get_conn()
        conn.execute("DELETE FROM positions WHERE market_id=?", (market_id,))
        conn.commit()
        conn.close()
```

---

## 12. Order Executor and Shadow Mode

This is the most important module. `USE_REAL_MONEY` is checked here at execution time — the signal engine, Kelly calculator, and dedup gate run identically in both modes. The only difference is whether USDC actually leaves your wallet.

### One-time wallet setup

Run this once before any live trading to approve Polymarket's contracts to spend your USDC:

```bash
uv run python polymarket_setup.py
```

```python
# polymarket_setup.py
import os
from py_clob_client.client import ClobClient
from dotenv import load_dotenv

load_dotenv()

client = ClobClient(
    "https://clob.polymarket.com",
    key=os.getenv("POLYGON_PRIVATE_KEY"),
    chain_id=137,
)
print("Approving USDC allowances for Polymarket contracts...")
result = client.set_allowances()
print(f"Done: {result}")
print("You only need to run this once per wallet.")
```

### Executor

```python
# executor.py
import os
import time
import logging
from dataclasses import dataclass
from typing import Optional
from db import get_conn
from config import use_real_money
from alerter import send_alert

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    placed:       bool
    shadow:       bool        # True if shadow mode
    order_id:     Optional[str]
    dollar_size:  float
    reason:       str         # "ok", skip reason, or error message


class PolymarketExecutor:
    def __init__(self):
        self._clob = None
        self._init_clob()

    def _init_clob(self):
        """
        Lazily initialize the CLOB client. Only needed for live trading.
        In shadow mode, no connection is made to the blockchain.
        """
        if not use_real_money():
            logger.info("Shadow mode active — CLOB client will not be initialized")
            return
        try:
            from py_clob_client.client import ClobClient
            self._clob = ClobClient(
                "https://clob.polymarket.com",
                key=os.getenv("POLYGON_PRIVATE_KEY"),
                chain_id=137,
                signature_type=0,
                funder=os.getenv("POLYGON_WALLET_ADDRESS"),
            )
            self._clob.set_api_creds(self._clob.create_or_derive_api_creds())
            logger.info("CLOB client initialized for live trading")
        except Exception as e:
            logger.error(f"CLOB client init failed: {e}")
            raise

    def get_usdc_balance(self) -> float:
        """
        In shadow mode: returns the simulated bankroll stored in the DB,
        initialized to SHADOW_BANKROLL_USD from .env (default $3000).
        In live mode: queries the real Polygon USDC balance.
        """
        if not use_real_money():
            conn = get_conn()
            row  = conn.execute(
                "SELECT SUM(signal_size_usd) as spent FROM trade_log "
                "WHERE real_money=0 AND skipped=0 AND outcome IS NULL"
            ).fetchone()
            conn.close()
            bankroll = float(os.getenv("SHADOW_BANKROLL_USD", "3000"))
            spent    = row["spent"] or 0.0
            return max(bankroll - spent, 0.0)
        try:
            raw = self._clob.get_balance()
            return float(raw) / 1e6   # USDC has 6 decimals on Polygon
        except Exception as e:
            logger.error(f"Balance fetch failed: {e}")
            return 0.0

    def execute(
        self,
        trade_id:    str,
        market_id:   str,
        token_id:    str,
        side:        str,
        dollar_size: float,
        kelly_f:     float,
        confidence:  float,
        signal:      dict,
        event,               # TradeEvent
        trader_f,            # TraderFeatures
        market_f,            # MarketFeatures
        dedup,               # DedupeCache
    ) -> ExecutionResult:

        shadow = not use_real_money()
        dedup.mark_pending(market_id)

        if shadow:
            result = self._execute_shadow(
                trade_id, market_id, token_id, side, dollar_size,
                kelly_f, confidence, signal, event, trader_f, market_f, dedup
            )
        else:
            result = self._execute_live(
                trade_id, market_id, token_id, side, dollar_size,
                kelly_f, confidence, signal, event, trader_f, market_f, dedup
            )

        return result

    def _execute_shadow(self, trade_id, market_id, token_id, side,
                         dollar_size, kelly_f, confidence, signal,
                         event, trader_f, market_f, dedup) -> ExecutionResult:
        """
        Shadow execution: log the hypothetical trade. No USDC spent.
        This is the core data collection mechanism.
        """
        log_trade(
            trade_id=trade_id, market_id=market_id,
            question=event.question, trader_address=event.trader_address,
            side=side, price=event.price, signal_size_usd=dollar_size,
            confidence=confidence, kelly_f=kelly_f,
            real_money=False, order_id=None,
            skipped=False, skip_reason=None,
            trader_f=trader_f, market_f=market_f,
        )
        dedup.confirm(market_id, side, dollar_size, token_id, real_money=False)
        dedup.mark_seen(trade_id, market_id, event.trader_address)

        logger.info(
            f"[SHADOW] {event.question[:60]} | {side.upper()} | "
            f"${dollar_size:.2f} | conf={confidence:.3f}"
        )
        send_alert(
            f"[SHADOW] {side.upper()} ${dollar_size:.2f}\n"
            f"{event.question[:80]}\n"
            f"conf={confidence:.3f} | kelly_f={kelly_f:.4f}"
        )
        return ExecutionResult(placed=True, shadow=True, order_id=None,
                               dollar_size=dollar_size, reason="ok")

    def _execute_live(self, trade_id, market_id, token_id, side,
                       dollar_size, kelly_f, confidence, signal,
                       event, trader_f, market_f, dedup) -> ExecutionResult:
        """
        Live execution: sign and submit an on-chain order.
        USDC will be spent from your wallet.
        """
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        try:
            order  = MarketOrderArgs(
                token_id=token_id,
                amount=dollar_size,
                side=BUY if side == "yes" else SELL,
            )
            signed   = self._clob.create_market_order(order)
            response = self._clob.post_order(signed, OrderType.FOK)
            order_id = response.get("orderID") or response.get("id", "unknown")

            log_trade(
                trade_id=trade_id, market_id=market_id,
                question=event.question, trader_address=event.trader_address,
                side=side, price=event.price, signal_size_usd=dollar_size,
                confidence=confidence, kelly_f=kelly_f,
                real_money=True, order_id=order_id,
                skipped=False, skip_reason=None,
                trader_f=trader_f, market_f=market_f,
            )
            dedup.confirm(market_id, side, dollar_size, token_id, real_money=True)
            dedup.mark_seen(trade_id, market_id, event.trader_address)

            logger.info(
                f"[LIVE] {event.question[:60]} | {side.upper()} | "
                f"${dollar_size:.2f} | conf={confidence:.3f} | order={order_id}"
            )
            send_alert(
                f"[LIVE] {side.upper()} ${dollar_size:.2f}\n"
                f"{event.question[:80]}\n"
                f"conf={confidence:.3f} | order={order_id}"
            )
            return ExecutionResult(placed=True, shadow=False, order_id=order_id,
                                   dollar_size=dollar_size, reason="ok")

        except Exception as e:
            dedup.release(market_id)
            logger.error(f"[LIVE ERROR] {market_id}: {e}")
            send_alert(f"[LIVE ERROR]\n{event.question[:80]}\n{e}")
            return ExecutionResult(placed=False, shadow=False, order_id=None,
                                   dollar_size=0.0, reason=str(e))

    def log_skip(self, trade_id: str, market_id: str, question: str,
                 trader_address: str, side: str, price: float,
                 size_usd: float, confidence: float, kelly_f: float,
                 reason: str, trader_f=None, market_f=None):
        """Log a skipped signal — just as important as acted signals for training."""
        log_trade(
            trade_id=trade_id, market_id=market_id, question=question,
            trader_address=trader_address, side=side, price=price,
            signal_size_usd=size_usd, confidence=confidence, kelly_f=kelly_f,
            real_money=False, order_id=None,
            skipped=True, skip_reason=reason,
            trader_f=trader_f, market_f=market_f,
        )


def log_trade(
    trade_id, market_id, question, trader_address, side, price,
    signal_size_usd, confidence, kelly_f, real_money, order_id,
    skipped, skip_reason, trader_f=None, market_f=None,
):
    """Write one row to trade_log with all feature values."""
    conn = get_conn()

    tf = trader_f
    mf = market_f
    spread = (mf.best_ask - mf.best_bid) / mf.mid if (mf and mf.mid > 0) else None
    momentum = abs(mf.mid - mf.price_1h_ago) / mf.price_1h_ago if (mf and mf.price_1h_ago > 0) else None
    vol_trend = mf.volume_24h_usd / (mf.volume_7d_avg_usd + 1e-6) if mf else None

    conn.execute(
        """INSERT INTO trade_log (
            trade_id, market_id, question, trader_address, side,
            price_at_signal, signal_size_usd, confidence, kelly_fraction,
            real_money, order_id, skipped, skip_reason, placed_at,
            f_trader_win_rate, f_trader_n_trades, f_conviction_ratio,
            f_trader_volume_usd, f_account_age_days, f_consistency,
            f_days_to_res, f_price, f_spread_pct, f_momentum_1h,
            f_volume_trend, f_oi_usd, f_bid_depth_usd, f_ask_depth_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            trade_id, market_id, question, trader_address.lower(), side,
            price, signal_size_usd, confidence, kelly_f,
            1 if real_money else 0,
            order_id, 1 if skipped else 0, skip_reason,
            int(time.time()),
            tf.win_rate         if tf else None,
            tf.n_trades         if tf else None,
            tf.conviction_ratio if tf else None,
            tf.volume_usd       if tf else None,
            tf.account_age_d    if tf else None,
            tf.consistency      if tf else None,
            mf.days_to_res     if mf else None,
            price,
            spread, momentum, vol_trend,
            mf.oi_usd          if mf else None,
            mf.bid_depth_usd   if mf else None,
            mf.ask_depth_usd   if mf else None,
        )
    )
    conn.commit()
    conn.close()
```

---

## 13. XGBoost Model Pipeline

### Feature columns

The feature columns must match the order used in `signal_engine.py`'s `_evaluate_xgb` method and the column names stored in `trade_log`:

```python
# features.py
FEATURE_COLS = [
    "f_trader_win_rate",
    "f_trader_n_trades",
    "f_conviction_ratio",
    "f_trader_volume_usd",
    "f_account_age_days",
    "f_consistency",
    "f_days_to_res",
    "f_price",
    "f_spread_pct",
    "f_momentum_1h",
    "f_volume_trend",
    "f_oi_usd",
    "f_bid_depth_usd",
    "f_ask_depth_usd",
]

LABEL_COL = "outcome"
```

### Training pipeline

```python
# train.py
import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import json
import time
import logging
from pathlib import Path
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import log_loss, brier_score_loss
from db import get_conn
from features import FEATURE_COLS, LABEL_COL

logger = logging.getLogger(__name__)

MODEL_PATH = "model.joblib"
MIN_SAMPLES = 200   # don't train with fewer rows than this


def load_training_data() -> pd.DataFrame:
    """
    Load all resolved, non-skipped trades from trade_log.
    Only rows with outcome IS NOT NULL are labeled training examples.
    Sort by placed_at ascending to preserve temporal order.
    """
    conn = get_conn()
    df = pd.read_sql_query(
        f"""
        SELECT {', '.join(FEATURE_COLS)}, {LABEL_COL}, placed_at
        FROM trade_log
        WHERE outcome IS NOT NULL
          AND skipped = 0
        ORDER BY placed_at ASC
        """,
        conn,
    )
    conn.close()
    return df


def train(df: pd.DataFrame = None) -> dict:
    """
    Train and calibrate an XGBoost binary classifier.
    Uses a temporal train/validation split (no lookahead leakage).
    Returns a metrics dict. Saves the model to MODEL_PATH.
    """
    if df is None:
        df = load_training_data()

    df = df.dropna(subset=FEATURE_COLS + [LABEL_COL])

    if len(df) < MIN_SAMPLES:
        logger.info(f"Training skipped: {len(df)} samples (need {MIN_SAMPLES})")
        return {"skipped": True, "n_samples": len(df)}

    logger.info(f"Training on {len(df)} samples...")

    X = df[FEATURE_COLS].values
    y = df[LABEL_COL].values

    # Temporal split: last 20% is validation.
    # NEVER use random split on time-series financial data — it leaks the future.
    split   = int(len(df) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    model = xgb.XGBClassifier(
        n_estimators=500,
        max_depth=4,               # shallow = less overfitting
        learning_rate=0.02,
        subsample=0.8,
        colsample_bytree=0.7,
        min_child_weight=15,       # regularize: require 15 samples per leaf
        gamma=0.1,                 # min loss reduction for a split
        reg_alpha=0.1,             # L1 regularization
        reg_lambda=1.0,            # L2 regularization
        scale_pos_weight=1.0,      # adjust if class imbalance is extreme
        eval_metric="logloss",
        early_stopping_rounds=40,
        random_state=42,
        verbosity=0,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    # Isotonic calibration: corrects XGBoost's systematic overconfidence.
    # This is critical — overconfident probabilities cause Kelly to overbbet.
    calibrated = CalibratedClassifierCV(model, method="isotonic", cv="prefit")
    calibrated.fit(X_val, y_val)

    preds  = calibrated.predict_proba(X_val)[:, 1]
    bl     = log_loss(y_val, np.full_like(preds, y_val.mean()))
    ll     = log_loss(y_val, preds)
    brier  = brier_score_loss(y_val, preds)

    # Feature importances
    importances = dict(zip(FEATURE_COLS, model.feature_importances_))
    top_features = sorted(importances.items(), key=lambda x: -x[1])

    metrics = {
        "n_samples":     len(df),
        "n_train":       split,
        "n_val":         len(df) - split,
        "log_loss":      round(ll, 4),
        "log_loss_base": round(bl, 4),
        "brier_score":   round(brier, 4),
        "beats_baseline": ll < bl,
        "top_features":  top_features[:5],
        "trained_at":    int(time.time()),
    }

    logger.info(f"Log loss: {ll:.4f} (baseline: {bl:.4f}) | Brier: {brier:.4f}")
    for feat, imp in top_features[:5]:
        logger.info(f"  {feat:<30} {imp:.4f}")

    if not metrics["beats_baseline"]:
        logger.warning("Model does NOT beat the baseline — not deploying")
        return metrics | {"deployed": False}

    # Save model and record in DB
    joblib.dump((calibrated, FEATURE_COLS), MODEL_PATH)

    conn = get_conn()
    conn.execute(
        """INSERT INTO model_history
           (trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed)
           VALUES (?,?,?,?,?,?,?)""",
        (int(time.time()), len(df), brier, ll,
         json.dumps(FEATURE_COLS), MODEL_PATH, 1)
    )
    # Mark all previous models as not deployed
    conn.execute(
        "UPDATE model_history SET deployed=0 WHERE deployed=1 AND trained_at < ?",
        (int(time.time()),)
    )
    conn.commit()
    conn.close()

    logger.info(f"Model saved to {MODEL_PATH}")
    return metrics | {"deployed": True}


def check_calibration(verbose: bool = True) -> dict:
    """
    Verify calibration by binning predictions into deciles and
    comparing predicted probability to actual win rate.
    If these diverge by >0.05 in any bin, Kelly sizing is unreliable.
    """
    df = load_training_data()
    if len(df) < 50:
        return {"error": "not enough data"}

    import os
    if not os.path.exists(MODEL_PATH):
        return {"error": "no model file"}

    model, cols = joblib.load(MODEL_PATH)
    df   = df.dropna(subset=cols + [LABEL_COL])
    X    = df[cols].values
    y    = df[LABEL_COL].values
    pred = model.predict_proba(X)[:, 1]

    bins   = np.linspace(0, 1, 11)
    result = []
    for i in range(len(bins) - 1):
        mask = (pred >= bins[i]) & (pred < bins[i+1])
        if mask.sum() < 5:
            continue
        bucket = {
            "pred_range":    f"{bins[i]:.1f}-{bins[i+1]:.1f}",
            "mean_pred":     round(pred[mask].mean(), 3),
            "actual_wr":     round(y[mask].mean(), 3),
            "n":             int(mask.sum()),
            "gap":           round(abs(pred[mask].mean() - y[mask].mean()), 3),
        }
        result.append(bucket)
        if verbose:
            flag = " ⚠️" if bucket["gap"] > 0.05 else ""
            logger.info(
                f"  {bucket['pred_range']}: pred={bucket['mean_pred']:.3f} "
                f"actual={bucket['actual_wr']:.3f} n={bucket['n']}{flag}"
            )

    return {"calibration_bins": result}
```

---

## 14. Auto-Retraining and Self-Improvement

The system retrains itself weekly. When a new model beats the baseline, it is hot-swapped into the running signal engine without a restart. When it doesn't beat the baseline, the current model (or heuristic) continues unchanged.

```python
# auto_retrain.py
import logging
import os
from train import train, check_calibration, MIN_SAMPLES, load_training_data
from alerter import send_alert

logger = logging.getLogger(__name__)


def retrain_cycle(signal_engine) -> bool:
    """
    Full retraining cycle. Called weekly by the scheduler.
    Returns True if a new model was deployed.

    signal_engine: the live SignalEngine instance — reloaded in-place if new model deployed.
    """
    df = load_training_data()
    n  = len(df)

    if n < MIN_SAMPLES:
        msg = f"Auto-retrain skipped: {n} labeled samples (need {MIN_SAMPLES})"
        logger.info(msg)
        send_alert(f"[RETRAIN] {msg}")
        return False

    logger.info(f"Starting auto-retrain on {n} samples...")
    metrics = train(df)

    if metrics.get("skipped"):
        return False

    if not metrics.get("deployed"):
        msg = (f"Retrain complete — model does NOT beat baseline\n"
               f"Brier: {metrics.get('brier_score')} | LL: {metrics.get('log_loss')}")
        logger.warning(msg)
        send_alert(f"[RETRAIN] {msg}")
        return False

    # Hot-swap the model in the running signal engine
    signal_engine.reload_model()

    # Check calibration
    cal = check_calibration(verbose=True)

    top = "\n".join(f"  {f}: {i:.4f}" for f, i in metrics.get("top_features", []))
    msg = (
        f"[RETRAIN] New model deployed\n"
        f"Samples: {n}\n"
        f"Brier: {metrics['brier_score']} (target <0.22)\n"
        f"Log loss: {metrics['log_loss']} (baseline: {metrics['log_loss_base']})\n"
        f"Top features:\n{top}"
    )
    logger.info(msg)
    send_alert(msg)
    return True


def should_retrain_early(signal_engine) -> bool:
    """
    Trigger an unscheduled retrain if:
    - We've accumulated 100 new labeled examples since last training
    - The current model's recent win rate has drifted >10% from its training win rate
    """
    conn = __import__("db").get_conn()

    # Last retrain time
    row = conn.execute(
        "SELECT trained_at FROM model_history WHERE deployed=1 ORDER BY trained_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if row is None:
        # No model yet — check if we have enough data now
        df = load_training_data()
        return len(df) >= MIN_SAMPLES

    last_retrain = row["trained_at"]
    conn = __import__("db").get_conn()
    new_labeled = conn.execute(
        "SELECT COUNT(*) as n FROM trade_log "
        "WHERE outcome IS NOT NULL AND skipped=0 AND placed_at > ?",
        (last_retrain,)
    ).fetchone()["n"]
    conn.close()

    if new_labeled >= 100:
        logger.info(f"Early retrain triggered: {new_labeled} new labeled samples since last train")
        return True

    return False
```

---

## 15. Performance Evaluator

This module resolves shadow trades once markets close and computes the hypothetical P&L that answers the key question: *is this model good enough to use real money?*

```python
# evaluator.py
import time
import httpx
import logging
import numpy as np
from db import get_conn
from alerter import send_alert

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


def resolve_shadow_trades():
    """
    Check all unresolved trades against the Gamma API.
    Updates outcome and shadow_pnl_usd for resolved markets.
    """
    conn       = get_conn()
    unresolved = conn.execute(
        "SELECT id, market_id, side, price_at_signal, signal_size_usd, real_money "
        "FROM trade_log WHERE outcome IS NULL AND skipped=0"
    ).fetchall()
    conn.close()

    if not unresolved:
        return

    client = httpx.Client(timeout=10.0)
    resolved_count = 0

    for row in unresolved:
        try:
            resp    = client.get(f"{GAMMA_API}/markets",
                                  params={"condition_id": row["market_id"]})
            markets = resp.json()
            if not markets:
                continue
            m = markets[0]
            if not m.get("closed", False):
                continue

            # Find the winning token (price at 1.0 post-resolution)
            result = None
            for token in m.get("tokens", []):
                if float(token.get("price", 0)) >= 0.99:
                    result = token.get("outcome", "").upper()
                    break

            if not result:
                continue

            won  = (row["side"].upper() == result)
            p    = row["price_at_signal"]
            size = row["signal_size_usd"]
            # P&L: winning a YES at price p returns (1-p)/p per dollar risked
            pnl  = round(size * (1 - p) / p, 2) if won else round(-size, 2)

            conn = get_conn()
            conn.execute(
                """UPDATE trade_log
                   SET outcome=?, shadow_pnl_usd=?, actual_pnl_usd=?, resolved_at=?
                   WHERE id=?""",
                (
                    1 if won else 0,
                    pnl if row["real_money"] == 0 else None,
                    pnl if row["real_money"] == 1 else None,
                    int(time.time()),
                    row["id"],
                )
            )
            conn.commit()
            conn.close()
            resolved_count += 1

        except Exception as e:
            logger.error(f"Resolution check failed ({row['market_id'][:12]}...): {e}")

    if resolved_count > 0:
        logger.info(f"Resolved {resolved_count} trades")


def compute_performance_report(mode: str = "shadow") -> dict:
    """
    Compute a performance report for shadow or live trades.
    mode: "shadow" or "live"
    """
    col  = "shadow_pnl_usd" if mode == "shadow" else "actual_pnl_usd"
    real = 0 if mode == "shadow" else 1

    conn = get_conn()

    summary = conn.execute(
        f"""
        SELECT
            COUNT(*) as total_signals,
            SUM(CASE WHEN skipped=0 THEN 1 ELSE 0 END) as acted,
            SUM(CASE WHEN outcome IS NOT NULL AND skipped=0 THEN 1 ELSE 0 END) as resolved,
            SUM(CASE WHEN outcome=1 AND skipped=0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome=0 AND skipped=0 THEN 1 ELSE 0 END) as losses,
            ROUND(SUM({col}), 2) as total_pnl,
            ROUND(AVG(confidence), 3) as avg_confidence,
            ROUND(AVG(signal_size_usd), 2) as avg_size
        FROM trade_log
        WHERE real_money=? AND skipped=0
        """,
        (real,)
    ).fetchone()

    # By-trader breakdown
    traders = conn.execute(
        f"""
        SELECT trader_address,
               COUNT(*) as n,
               SUM(CASE WHEN outcome=1 THEN 1 ELSE 0 END) as wins,
               ROUND(SUM({col}), 2) as pnl
        FROM trade_log
        WHERE real_money=? AND skipped=0 AND outcome IS NOT NULL
        GROUP BY trader_address
        ORDER BY pnl DESC
        LIMIT 10
        """,
        (real,)
    ).fetchall()

    # Recent week P&L
    week_ago = int(time.time()) - 7 * 86400
    weekly   = conn.execute(
        f"SELECT ROUND(SUM({col}), 2) as pnl FROM trade_log "
        f"WHERE real_money=? AND skipped=0 AND placed_at > ?",
        (real, week_ago)
    ).fetchone()

    conn.close()

    resolved = summary["resolved"] or 1
    acted    = summary["acted"]    or 1
    win_rate = (summary["wins"] or 0) / resolved

    # Sharpe-like: mean daily P&L / std daily P&L
    conn = get_conn()
    daily = conn.execute(
        f"""
        SELECT strftime('%Y-%m-%d', datetime(placed_at, 'unixepoch')) as day,
               SUM({col}) as day_pnl
        FROM trade_log
        WHERE real_money=? AND skipped=0 AND outcome IS NOT NULL
        GROUP BY day ORDER BY day
        """,
        (real,)
    ).fetchall()
    conn.close()

    day_pnls = [r["day_pnl"] for r in daily if r["day_pnl"] is not None]
    sharpe   = float(np.mean(day_pnls) / (np.std(day_pnls) + 1e-6)) if len(day_pnls) > 1 else 0.0

    return {
        "mode":             mode,
        "total_signals":    summary["total_signals"],
        "acted":            summary["acted"],
        "resolved":         summary["resolved"],
        "win_rate":         round(win_rate, 3),
        "total_pnl_usd":    summary["total_pnl"],
        "weekly_pnl_usd":   weekly["pnl"],
        "avg_confidence":   summary["avg_confidence"],
        "avg_size_usd":     summary["avg_size"],
        "sharpe":           round(sharpe, 3),
        "top_traders":      [dict(r) for r in traders],
    }


def daily_report():
    """Send a daily summary via Telegram."""
    resolve_shadow_trades()
    shadow = compute_performance_report("shadow")
    live   = compute_performance_report("live")

    lines = [
        "=== Daily Performance Report ===",
        "",
        f"[SHADOW] {shadow['resolved']} resolved | "
        f"WR: {shadow['win_rate']:.0%} | "
        f"P&L: ${shadow['total_pnl_usd']:.2f} | "
        f"7d: ${shadow['weekly_pnl_usd']:.2f}",
        f"[SHADOW] Sharpe: {shadow['sharpe']:.2f} | "
        f"Avg conf: {shadow['avg_confidence']:.3f}",
    ]

    if live["acted"] > 0:
        lines += [
            "",
            f"[LIVE] {live['resolved']} resolved | "
            f"WR: {live['win_rate']:.0%} | "
            f"P&L: ${live['total_pnl_usd']:.2f}",
        ]

    if shadow["top_traders"]:
        lines.append("\nTop shadow traders (by P&L):")
        for t in shadow["top_traders"][:3]:
            lines.append(
                f"  {t['trader_address'][:10]}... "
                f"{t['wins']}/{t['n']} | ${t['pnl']:.2f}"
            )

    send_alert("\n".join(lines))
```

---

## 16. Monitoring and Alerting

```python
# alerter.py
import os
import logging
import requests

logger = logging.getLogger(__name__)


def send_alert(message: str, silent: bool = False):
    """
    Send a Telegram message. silent=True skips sending (useful in tests).
    Truncates to 4096 chars (Telegram limit).
    """
    if silent:
        return
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.debug(f"Telegram not configured. Message: {message[:100]}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:4096]},
            timeout=5,
        )
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")
```

---

## 17. Main Loop

```python
# main.py
import os
import json
import time
import logging
from logging.handlers import RotatingFileHandler
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler("logs/bot.log", maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

from db import init_db
from config import use_real_money, poll_interval, wallet_address
from tracker import PolymarketTracker
from executor import PolymarketExecutor
from signal_engine import SignalEngine
from kelly import kelly_size
from dedup import DedupeCache
from trader_scorer import get_trader_features
from market_scorer import build_market_features
from evaluator import resolve_shadow_trades, daily_report
from auto_retrain import retrain_cycle, should_retrain_early
from alerter import send_alert


# ── Event stream for the dashboard ───────────────────────────────────────
# The dashboard tails data/events.jsonl to populate the Live Feed and
# Signals pages. One JSON line is written per trade event processed.
# The file is rotated to keep the last 1000 lines every 100 writes.

_EVENT_FILE  = "data/events.jsonl"
_emit_count  = 0

def _emit_event(payload: dict):
    """Append a single event as a JSON line to the dashboard event stream."""
    global _emit_count
    with open(_EVENT_FILE, "a") as f:
        f.write(json.dumps(payload) + "\n")
    _emit_count += 1
    if _emit_count % 100 == 0:
        try:
            with open(_EVENT_FILE, "r") as f:
                lines = f.readlines()
            if len(lines) > 1000:
                with open(_EVENT_FILE, "w") as f:
                    f.writelines(lines[-1000:])
        except Exception:
            pass


# ── Wallets to watch ──────────────────────────────────────────────────────
# Populated from the WATCHED_WALLETS .env variable.
# Use resolve_wallet.py to convert any profile URL or @handle to an address,
# then paste the output line directly into your .env file:
#
#   uv run python resolve_wallet.py
#   > https://polymarket.com/@Inkar
#   > 0x4f2a...9c3e
#   .env line:  WATCHED_WALLETS=0x4f2a...9c3e,0xdef...,0x123...

def _load_watched_wallets() -> list[str]:
    raw = os.getenv("WATCHED_WALLETS", "").strip()
    if not raw:
        logger.warning(
            "WATCHED_WALLETS is empty. Run resolve_wallet.py to add profiles, "
            "then paste the output into your .env file."
        )
        return []
    wallets = [w.strip().lower() for w in raw.split(",") if w.strip()]
    logger.info(f"Loaded {len(wallets)} watched wallet(s) from .env")
    return wallets

WATCHED_WALLETS = _load_watched_wallets()


def process_event(event, engine, executor, dedup, bankroll):
    """Process one incoming trade event through the full pipeline."""

    # Emit to the dashboard live feed (unfiltered, before any scoring)
    _emit_event({
        "type":     "incoming",
        "trade_id": event.trade_id,
        "market_id": event.market_id,
        "question": event.question,
        "side":     event.side,
        "price":    event.price,
        "size_usd": event.size_usd,
        "trader":   event.trader_address,
        "ts":       event.timestamp,
    })

    # Mark seen immediately so parallel processing doesn't double-handle
    dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)

    if not event.snapshot:
        logger.debug(f"No snapshot for {event.market_id[:12]}... — skipping")
        return

    # Build features
    trader_f = get_trader_features(event.trader_address, event.size_usd)

    # First-pass Kelly with rough confidence for depth scoring
    rough_size = kelly_size(0.65, event.price, bankroll).get("dollar_size", 5.0)
    market_f   = build_market_features(event.snapshot, event.close_time, rough_size)

    if market_f is None:
        return

    # Signal evaluation
    signal = engine.evaluate(trader_f, market_f, rough_size)

    # If market veto fired, log and skip
    if signal.get("veto"):
        reason = f"market veto: {signal['veto']}"
        executor.log_skip(
            trade_id=event.trade_id, market_id=event.market_id,
            question=event.question, trader_address=event.trader_address,
            side=event.side, price=event.price,
            size_usd=0.0, confidence=0.0, kelly_f=0.0,
            reason=reason, trader_f=trader_f, market_f=market_f,
        )
        _emit_event({
            "type": "signal", "trade_id": event.trade_id,
            "market_id": event.market_id, "question": event.question,
            "side": event.side, "price": event.price, "size_usd": 0.0,
            "decision": "REJECT", "confidence": 0.0,
            "reason": reason, "ts": int(time.time()),
        })
        return

    # Final Kelly with real confidence
    sizing = kelly_size(signal["confidence"], event.price, bankroll)

    # Dedup gate
    ok, reason = dedup.gate(
        event.trade_id, event.market_id, event.side, signal["confidence"]
    )
    if not ok:
        logger.debug(f"[GATE] {event.market_id[:16]}... blocked: {reason}")
        if reason not in ("duplicate trade_id",):
            executor.log_skip(
                trade_id=event.trade_id, market_id=event.market_id,
                question=event.question, trader_address=event.trader_address,
                side=event.side, price=event.price,
                size_usd=sizing.get("dollar_size", 0),
                confidence=signal["confidence"],
                kelly_f=sizing.get("kelly_f", 0), reason=reason,
                trader_f=trader_f, market_f=market_f,
            )
            _emit_event({
                "type": "signal", "trade_id": event.trade_id,
                "market_id": event.market_id, "question": event.question,
                "side": event.side, "price": event.price,
                "size_usd": sizing.get("dollar_size", 0),
                "decision": "REJECT", "confidence": signal["confidence"],
                "reason": reason, "ts": int(time.time()),
            })
        return

    if sizing["dollar_size"] == 0.0:
        reason = f"Kelly: {sizing['reason']}"
        executor.log_skip(
            trade_id=event.trade_id, market_id=event.market_id,
            question=event.question, trader_address=event.trader_address,
            side=event.side, price=event.price,
            size_usd=0.0, confidence=signal["confidence"],
            kelly_f=0.0, reason=reason,
            trader_f=trader_f, market_f=market_f,
        )
        _emit_event({
            "type": "signal", "trade_id": event.trade_id,
            "market_id": event.market_id, "question": event.question,
            "side": event.side, "price": event.price, "size_usd": 0.0,
            "decision": "REJECT", "confidence": signal["confidence"],
            "reason": reason, "ts": int(time.time()),
        })
        return

    # Recalculate market features with actual order size for accurate depth scoring
    market_f_final = build_market_features(
        event.snapshot, event.close_time, sizing["dollar_size"]
    )

    # Execute (shadow or live depending on USE_REAL_MONEY)
    result = executor.execute(
        trade_id=event.trade_id,
        market_id=event.market_id,
        token_id=event.token_id,
        side=event.side,
        dollar_size=sizing["dollar_size"],
        kelly_f=sizing["kelly_f"],
        confidence=signal["confidence"],
        signal=signal,
        event=event,
        trader_f=trader_f,
        market_f=market_f_final,
        dedup=dedup,
    )

    # Emit accepted signal to dashboard
    _emit_event({
        "type":       "signal",
        "trade_id":   event.trade_id,
        "market_id":  event.market_id,
        "question":   event.question,
        "side":       event.side,
        "price":      event.price,
        "size_usd":   sizing["dollar_size"],
        "decision":   "ACCEPT",
        "confidence": signal["confidence"],
        "shadow":     result.shadow,
        "order_id":   result.order_id,
        "reason":     "ok",
        "ts":         int(time.time()),
    })


def main():
    logger.info("=" * 60)
    logger.info("Polymarket copy-trading bot starting")
    logger.info(f"Mode: {'LIVE (REAL MONEY)' if use_real_money() else 'SHADOW (no real money)'}")
    logger.info("=" * 60)

    init_db()

    # Write bot_state.json so the dashboard can read uptime and mode
    with open("data/bot_state.json", "w") as f:
        json.dump({
            "started_at":   int(time.time()),
            "mode":         "live" if use_real_money() else "shadow",
            "n_wallets":    len(WATCHED_WALLETS),
            "poll_interval": poll_interval(),
        }, f)

    # Ensure the event stream file exists so the dashboard doesn't error on startup
    if not os.path.exists(_EVENT_FILE):
        open(_EVENT_FILE, "w").close()

    tracker  = PolymarketTracker(WATCHED_WALLETS)
    executor = PolymarketExecutor()
    engine   = SignalEngine()
    dedup    = DedupeCache()

    dedup.load_from_db()
    dedup.sync_positions_from_api(tracker, wallet_address())

    # Optionally auto-populate watchlist from leaderboard on startup
    # tracker.add_top_traders(window="1w", top_n=20)

    # Background jobs
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        lambda: dedup.sync_positions_from_api(tracker, wallet_address()),
        "interval", minutes=5, id="sync_positions",
    )
    scheduler.add_job(
        resolve_shadow_trades,
        "interval", hours=1, id="resolve_trades",
    )
    scheduler.add_job(
        daily_report,
        "cron", hour=8, minute=0, id="daily_report",
    )
    scheduler.add_job(
        lambda: retrain_cycle(engine),
        "cron", day_of_week="mon", hour=3, id="weekly_retrain",
    )
    scheduler.add_job(
        lambda: should_retrain_early(engine) and retrain_cycle(engine),
        "interval", hours=24, id="early_retrain_check",
    )
    scheduler.add_job(
        lambda: __import__("shutil").copy("data/trading.db", "data/trading.db.bak"),
        "cron", hour=4, id="db_backup",
    )

    scheduler.start()

    mode_str = "LIVE" if use_real_money() else "SHADOW"
    send_alert(
        f"Bot started [{mode_str}]\n"
        f"Watching {len(tracker.wallets)} wallets\n"
        f"Poll interval: {poll_interval()}s"
    )

    try:
        while True:
            loop_start = time.time()
            try:
                bankroll = executor.get_usdc_balance()
                if bankroll < 1.0:
                    logger.warning(f"Low balance: ${bankroll:.2f} — skipping poll")
                else:
                    events = tracker.poll()
                    for event in events:
                        process_event(event, engine, executor, dedup, bankroll)
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                send_alert(f"[ERROR] Loop error: {e}")

            elapsed = time.time() - loop_start
            sleep   = max(0, poll_interval() - elapsed)
            time.sleep(sleep)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)
        send_alert("Bot stopped")


if __name__ == "__main__":
    main()
```

---

## 18. Windows Deployment with uv

### Watchdog script

Create `watchdog.bat` in the project root:

```batch
@echo off
:loop
uv run python main.py
echo Bot exited with code %errorlevel%. Restarting in 10 seconds...
timeout /t 10
goto loop
```

### Auto-start via Task Scheduler

1. Open Task Scheduler (`taskschd.msc`)
2. Create Basic Task → name it "Polymarket Bot"
3. Trigger: "When the computer starts"
4. Action: Start a program
   - Program: `C:\path\to\polymarket-bot\watchdog.bat`
   - Start in: `C:\path\to\polymarket-bot`
5. Conditions: uncheck "Only start if on AC power"
6. Settings: "If already running, do not start a new instance"

### Prevent sleep

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
```

### Daily SQLite backup

The scheduler in `main.py` can be extended:

```python
import shutil
scheduler.add_job(
    lambda: shutil.copy("data/trading.db", "data/trading.db.bak"),
    "cron", hour=4, id="db_backup",
)
```

### Environment variable for uv

If uv is not in the system PATH for the Task Scheduler user, use the full path:

```batch
@echo off
:loop
C:\Users\<yourname>\.local\bin\uv.exe run python main.py
echo Restarting in 10 seconds...
timeout /t 10
goto loop
```

---

## 19. Going Live Checklist

This checklist must be completed before setting `USE_REAL_MONEY=true`. Every item corresponds to a question the shadow P&L report should be able to answer.

### Shadow performance gates

- [ ] Minimum 4 weeks of shadow trading data collected
- [ ] At least 100 resolved shadow trades (markets closed with known outcome)
- [ ] Shadow win rate >= 55% (meaningfully above 50% random baseline)
- [ ] Shadow total P&L is positive
- [ ] Shadow weekly P&L has been positive for at least 3 of the last 4 weeks
- [ ] Shadow Sharpe ratio > 0.5
- [ ] XGBoost model has been trained and beats the log-loss baseline
- [ ] Calibration check passes (predicted probabilities within 5% of actual win rates per decile)

### Model quality

- [ ] Brier score < 0.22 on holdout set
- [ ] Model has been retrained at least once on live-collected data (not just bootstrap)
- [ ] Feature importances look sensible (win rate and conviction should be near the top)
- [ ] No single feature dominates with importance > 0.5 (suggests overfitting)

### Infrastructure

- [ ] `polymarket_setup.py` run once — USDC allowances confirmed
- [ ] Live USDC balance confirmed: `uv run python -c "from executor import PolymarketExecutor; e = PolymarketExecutor(); print(e.get_usdc_balance())"`
- [ ] Telegram alerts firing correctly
- [ ] Watchdog bat tested — restarts after crash
- [ ] Windows never-sleep applied
- [ ] SQLite daily backup scheduled
- [ ] Logs rotate correctly (check `logs/bot.log` size)
- [ ] `.env` has `USE_REAL_MONEY=false` — double-check before flipping

### Risk parameters

- [ ] `MAX_BET_FRACTION=0.05` or lower confirmed in `.env`
- [ ] `MIN_CONFIDENCE=0.60` or higher confirmed in `.env`
- [ ] `SHADOW_BANKROLL_USD` set to roughly your planned live bankroll (for realistic sizing)
- [ ] You understand that `USE_REAL_MONEY=true` means real USDC leaves your wallet

### The flip

When all gates are green:
1. Stop the running bot (`Ctrl+C` or kill the Task Scheduler job)
2. Edit `.env`: change `USE_REAL_MONEY=false` to `USE_REAL_MONEY=true`
3. Restart: `watchdog.bat`
4. Confirm Telegram alert says `[LIVE]` not `[SHADOW]`
5. Watch the first few trades carefully

---

## 20. Full Project File Structure

```
polymarket-bot/
|
|-- .env                     # Secrets + config — never commit
|-- .env.example             # Template with all keys, blank values — commit this
|-- .gitignore
|-- pyproject.toml           # uv Python project definition and dependencies
|-- uv.lock                  # Locked Python dependency versions — commit this
|-- watchdog.bat             # Windows crash-restart wrapper
|-- README.md
|
|-- main.py                  # Entry point: polling loop + scheduler
|-- config.py                # .env loader and typed accessors
|-- db.py                    # SQLite schema and connection helper
|
|-- tracker.py               # Polymarket profile tracker + API clients
|-- executor.py              # Shadow/live execution + trade logging
|-- dedup.py                 # Deduplication cache + position state
|
|-- trader_scorer.py         # Trader feature builder + heuristic scorer
|-- market_scorer.py         # Market condition scorer + feature builder
|-- signal_engine.py         # Combined signal (heuristic -> XGBoost)
|-- kelly.py                 # Kelly Criterion position sizing
|-- features.py              # Feature column definitions (shared reference)
|
|-- train.py                 # XGBoost training + calibration pipeline
|-- auto_retrain.py          # Scheduled self-improvement logic
|-- evaluator.py             # P&L resolution + performance reporting
|-- alerter.py               # Telegram push notifications
|
|-- polymarket_setup.py      # One-time USDC allowance approval (run once)
|-- resolve_wallet.py        # Resolve profile URL / @handle -> wallet address
|
|-- model.joblib             # Trained model (generated — do not commit)
|
|-- data/
|   |-- trading.db           # SQLite database (generated — do not commit)
|   |-- trading.db.bak       # Daily backup (generated — do not commit)
|   |-- events.jsonl         # Live event stream for dashboard (generated)
|   +-- bot_state.json       # Bot uptime + mode for dashboard (generated)
|
|-- logs/
|   +-- bot.log              # Rotating log file (generated — do not commit)
|
+-- dashboard/               # Terminal dashboard (separate Node.js app)
    |-- package.json         # Node dependencies (ink, better-sqlite3, tsx)
    |-- package-lock.json    # Locked Node dependency versions — commit this
    |-- tsconfig.json        # TypeScript config
    |-- dashboard.tsx        # Entry point — renders <App>, run with tsx
    |-- theme.ts             # Color constants (#ff1c68 / #24ff7b / #ff0f0f)
    |-- useDb.ts             # SQLite polling hook (better-sqlite3, 2s interval)
    |-- useEventStream.ts    # Tails data/events.jsonl for live feed
    |-- pages/
    |   |-- LiveFeed.tsx     # Page 1 — raw incoming trades
    |   |-- Signals.tsx      # Page 2 — ACCEPT/REJECT decisions + reasons
    |   |-- Performance.tsx  # Page 3 — shadow vs live P&L, daily bars
    |   |-- Models.tsx       # Page 4 — XGBoost importances + calibration
    |   |-- Wallets.tsx      # Page 5 — watchlist stats + recent activity
    |   +-- Settings.tsx     # Page 6 — .env values read-only
    +-- components/
        |-- Box.tsx          # Bordered frame with optional accent color
        |-- StatRow.tsx      # Label + value row
        |-- BarSparkline.tsx # ASCII bar █░ with #24ff7b / #ff0f0f coloring
        +-- TradeRow.tsx     # One formatted row in a trade table
```

The `dashboard/` folder is a completely independent Node application. It reads from `data/trading.db` and `data/events.jsonl` — both written by the Python bot — but never imports Python code and runs in a separate terminal process. You can kill and restart the dashboard at any time without affecting the bot.

### `dashboard/tsconfig.json`

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true
  }
}
```

### Quick-start commands

```bash
# ── First time setup ─────────────────────────────────────────────────
uv sync                           # install Python dependencies
cp .env.example .env              # fill in your values

# ── Add wallets to watch ─────────────────────────────────────────────
# Converts profile URLs, @handles, or raw addresses -> wallet addresses
# and prints a WATCHED_WALLETS= line ready to paste into .env
uv run python resolve_wallet.py
# > https://polymarket.com/@Inkar
#   0x4f2a9c3e...
# > @SomeOtherTrader
#   0xab3f1e9c...
# > done
# .env line: WATCHED_WALLETS=0x4f2a9c3e...,0xab3f1e9c...

# ── One-time wallet approval (live trading only) ──────────────────────
uv run python polymarket_setup.py

# ── Initialize database ──────────────────────────────────────────────
uv run python -c "from db import init_db; init_db()"

# ── Run the bot (shadow mode by default) ─────────────────────────────
uv run python main.py

# ── Run the dashboard (separate terminal) ────────────────────────────
cd dashboard
npm install                       # first time only
npm start
# or:  npx tsx dashboard.tsx

# ── Check shadow P&L ─────────────────────────────────────────────────
uv run python -c "
from evaluator import resolve_shadow_trades, compute_performance_report
resolve_shadow_trades()
r = compute_performance_report('shadow')
print(f'Win rate:       {r[\"win_rate\"]:.1%}')
print(f'Total P&L:      \${r[\"total_pnl_usd\"]:.2f}')
print(f'Resolved trades:{r[\"resolved\"]}')
print(f'Sharpe:         {r[\"sharpe\"]:.2f}')
"

# ── Manually trigger retraining ──────────────────────────────────────
uv run python train.py

# ── Check model calibration ──────────────────────────────────────────
uv run python -c "from train import check_calibration; check_calibration()"
```

---

## 21. Terminal Dashboard (Ink)

The bot runs headlessly 24/7, but you need a way to watch it in real time without reading raw log files. The dashboard is a full-screen terminal UI built with **Ink** (React for the terminal) that reads directly from the SQLite database and a live event queue — the same data the bot uses internally, rendered as interactive panes.

It runs as a completely separate process. The bot and the dashboard never share memory — the dashboard is a read-only consumer that queries `data/trading.db` and subscribes to a local JSON socket the bot writes events to. This means you can kill and restart the dashboard at any time without affecting the bot.

### Technology

The dashboard is a Node.js app written in TypeScript. It lives in `dashboard/` and has no Python dependency. Install once with `npm install` inside that folder (see Section 20 quick-start).

Key packages:

| Package | Role |
|---|---|
| `ink` | React for the terminal — renders components as TTY output |
| `better-sqlite3` | Synchronous SQLite bindings — reads `data/trading.db` directly |
| `tsx` | Runs `.tsx` files without a build step |
| `react` | Component model (Ink's peer dependency) |

Ink renders React components to the terminal using Yoga flexbox layout. Every component is a standard React functional component. State management uses plain `useState` + `useEffect` with `setInterval` for the 2-second DB poll and `fs.watchFile` for the event stream.

The `dashboard/` folder structure is shown in full in Section 20. The entry point is `dashboard/dashboard.tsx`.

Run with:

```bash
cd dashboard && npm start
```

### Layout structure

Every page shares the same outer chrome:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  POLYMARKET BOT  [SHADOW]    1:Feed  2:Signals  3:Perf  4:Models  5:Wallets  6:Settings │
│─────────────────────────────────────────────────────────────────────────│
│                                                                          │
│  [page content here]                                                     │
│                                                                          │
│─────────────────────────────────────────────────────────────────────────│
│  polling wallets: 3   last poll: 2s ago   db: 1,204 rows   q: exit      │
└─────────────────────────────────────────────────────────────────────────┘
```

The outer border is drawn by `<Box>`. `<Header>` renders the top nav bar. A `<Footer>` renders the status line. The page component fills the middle. Nothing scrolls horizontally — all content fits the terminal width (min 100 cols recommended).

### Page 1 — Live feed

Shows a scrolling table of every raw trade event received from watched wallets in the current session. This is the unfiltered stream — every trade the poller sees, before the signal engine runs.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Live feed                                           last update: 1s ago│
│─────────────────────────────────────────────────────────────────────────│
│  TIME     WALLET        MARKET                        SIDE   PRICE  SIZE │
│  ─────────────────────────────────────────────────────────────────────  │
│  14:32:01  0x4f2a…9c3e  Will BTC exceed $100k by...   YES   0.61  $240  │
│  14:31:47  0x9b1d…2a7f  Fed rate cut in March?         NO   0.38   $85  │
│  14:31:12  0x4f2a…9c3e  Super Bowl winner — Chiefs?   YES   0.54  $500  │
│  14:30:55  0x7c8e…4d1a  US recession by Q3 2025?       NO   0.29   $60  │
│  14:30:31  0xab3f…1e9c  Nvidia stock >$200 by June?   YES   0.72  $180  │
│  14:29:44  0x9b1d…2a7f  Will GPT-5 launch in Jan?      NO   0.41  $120  │
│  ...                                                                     │
│  showing last 50 events  (total session: 147)                           │
└─────────────────────────────────────────────────────────────────────────┘
```

Implementation notes:
- `useEventStream` subscribes to the bot's socket and appends new events to a capped ring buffer (last 50)
- Wallet addresses truncated to `0x????…????` format
- Market question truncated to 42 chars with `…`
- Side column colored: YES = `#24ff7b` (green), NO = `#ff0f0f` (red) using Ink's `<Text color>` prop
- Time shown as `HH:MM:SS` local time
- Bottom line shows count since session start

### Page 2 — Signals

Shows the pipeline output for each trade the signal engine evaluated. For every incoming trade event, this page shows the decision (ACCEPT / REJECT / SKIP) and the reasons. This is the most operationally useful page.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Signals                         filter: [all ▾]     showing: 50 of 312 │
│─────────────────────────────────────────────────────────────────────────│
│  TIME     MARKET                  DECISION   CONF   SIZE    REASON       │
│  ──────────────────────────────────────────────────────────────────────  │
│  14:32:01  Will BTC exceed $100k  ACCEPT     0.71  $18.40  ok            │
│  14:31:47  Fed rate cut March?    REJECT     0.48   —      conf < 0.60   │
│  14:31:12  Super Bowl — Chiefs?   ACCEPT     0.68  $24.10  ok            │
│  14:30:55  US recession Q3?       REJECT     0.00   —      spread 14.2%  │
│  14:30:31  Nvidia >$200 June?     REJECT     0.62   —      pos open      │
│  14:29:44  GPT-5 launch Jan?      REJECT     0.55   —      conf < 0.60   │
│  14:28:19  Euro Cup winner — Eng  ACCEPT     0.73  $31.20  ok            │
│  ...                                                                     │
│─────────────────────────────────────────────────────────────────────────│
│  session:  312 evaluated   18 accepted (5.8%)   294 rejected             │
│  today:    accept rate 6.1%   avg conf on accepts: 0.694                 │
└─────────────────────────────────────────────────────────────────────────┘
```

Implementation notes:
- Filter control cycles between `all`, `accepted`, `rejected` with left/right arrow keys
- ACCEPT row highlighted with `#24ff7b` (green) text; REJECT in `#ff0f0f` (red) dimmed text; SKIP (dedup) in white dimmed
- Reason column shows the first applicable skip reason from the dedup/veto chain:
  `conf < 0.60` | `spread X%` | `pos open` | `in-flight` | `duplicate` | `kelly: no edge` | `expires <6h`
- CONF column shows 0.00 for market vetoes (the scorer never ran)
- SIZE column shows `—` for rejected trades
- Reads from `trade_log` where `placed_at > session_start`, refreshed every 2s

### Page 3 — Performance

Shows shadow and live P&L in parallel. This is the "should I flip `USE_REAL_MONEY`?" page.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Performance                    period: [7d ▾]    mode: shadow + live   │
│─────────────────────────────────────────────────────────────────────────│
│                                                                          │
│  SHADOW                              LIVE                                │
│  ─────────────────────────────────   ─────────────────────────────────  │
│  Total P&L       +$214.30            Total P&L       +$0.00             │
│  Win rate        58.3%  (7d: 61%)    Win rate        —                  │
│  Resolved        144 trades          Resolved        0 trades           │
│  Sharpe          0.74                Sharpe          —                  │
│  Avg confidence  0.691               Avg conf        —                  │
│  Avg size        $22.40              Avg size        —                  │
│                                                                          │
│─────────────────────────────────────────────────────────────────────────│
│  Daily P&L (shadow, last 7 days)                                         │
│                                                                          │
│  Mon  ████████████  +$48.20                                             │
│  Tue  ██████░░░░░░  +$21.10                                             │
│  Wed  ████████████████  +$61.30                                         │
│  Thu  ██░░░░░░░░░░  -$12.40                                             │
│  Fri  █████████  +$38.80                                                │
│  Sat  ███████░░░  +$31.20                                               │
│  Sun  ████████  +$26.10  ← today                                        │
│                                                                          │
│─────────────────────────────────────────────────────────────────────────│
│  Top wallets by shadow P&L                                               │
│  0x4f2a…9c3e   42 trades  WR 64%  +$98.40  ████████████                │
│  0xab3f…1e9c   31 trades  WR 61%  +$74.20  █████████                   │
│  0x9b1d…2a7f   28 trades  WR 54%  +$28.10  ████                        │
│  0x7c8e…4d1a   19 trades  WR 47%  -$14.50  ██ (negative)               │
└─────────────────────────────────────────────────────────────────────────┘
```

Implementation notes:
- Period selector cycles 1d / 7d / 30d / all with left/right arrow keys
- Bar sparklines are ASCII: each `█` = $10 of P&L; positive = `#24ff7b` (green), negative = `#ff0f0f` (red)
- Shadow and live panels always shown side by side; live panel shows `—` until real trades exist
- "Going live" threshold indicators: win rate and Sharpe render in `#24ff7b` when they cross targets (≥55%, ≥0.5), `#ff0f0f` when below

### Page 4 — Models

The model introspection page. Shows both the heuristic and XGBoost models simultaneously so you can understand what's driving each signal.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Models              active: XGBoost (deployed Mon Jan 13 03:00)        │
│─────────────────────────────────────────────────────────────────────────│
│  XGBOOST FEATURE IMPORTANCES        CALIBRATION (predicted vs actual)   │
│                                                                          │
│  conviction ratio   ████████████  0.24    0.0–0.1   pred ██  act ██    │
│  win rate           ██████████    0.20    0.1–0.2   pred ████ act ████  │
│  days to res.       ████████      0.16    0.2–0.3   pred █████ act████  │
│  spread pct         ███████       0.13    0.3–0.4   pred ██████ act██   │
│  consistency        █████         0.10    0.4–0.5   pred████████ act██  │
│  momentum 1h        ████          0.07    0.5–0.6   pred██████ act████  │
│  account age        ██            0.04    0.6–0.7   pred████████ act██  │
│  bid depth          █             0.03    0.7–1.0   pred████ act ███    │
│                                                                          │
│  Brier: 0.198  LL: 0.631  baseline LL: 0.693  samples: 847             │
│─────────────────────────────────────────────────────────────────────────│
│  HEURISTIC FALLBACK — weights and last signal component scores           │
│                                                                          │
│  win rate      (w=0.30)  score=0.71  ███████████                        │
│  conviction    (w=0.15)  score=0.88  ██████████████                     │
│  consistency   (w=0.15)  score=0.55  █████████                          │
│  account age   (w=0.10)  score=0.82  █████████████                      │
│  diversity     (w=0.05)  score=0.60  ██████████                         │
│  spread        (w=0.25)  score=0.76  ████████████                       │
│                                                                          │
│─────────────────────────────────────────────────────────────────────────│
│  RETRAIN HISTORY                                                          │
│  Jan 13 03:00   n=847   Brier 0.198   LL 0.631  [deployed]              │
│  Jan  6 03:00   n=703   Brier 0.211   LL 0.648  [superseded]            │
│  Dec 30 03:00   n=541   Brier 0.224   LL 0.661  [superseded]            │
└─────────────────────────────────────────────────────────────────────────┘
```

Implementation notes:
- Feature importance bars scaled so the largest value = full width (40 chars)
- Calibration: two bars per decile row — predicted (`#ff1c68` pink `▓`) and actual (`#24ff7b` green `░`) — gap between them shows miscalibration
- "Last signal component scores" section shows the scores from the most recently evaluated trade, so you can see the model's reasoning in real time
- Heuristic section always visible even when XGBoost is active — useful for debugging unexpected decisions
- Retrain history read from `model_history` table; latest row highlighted

### Page 5 — Wallets

The watchlist management page. Shows stats for every tracked wallet and lets you see their recent activity.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Wallets          tracking: 4 wallets     [A] add wallet                │
│─────────────────────────────────────────────────────────────────────────│
│  ADDRESS            TRADES  WIN RATE  SHADOW P&L  LAST SEEN   STATUS    │
│  ──────────────────────────────────────────────────────────────────────  │
│  0x4f2a…9c3e [▶]     142    64.1%    +$98.40     14:32:01    active     │
│  0xab3f…1e9c [▶]      98    61.2%    +$74.20     14:30:31    active     │
│  0x9b1d…2a7f [▶]      87    54.0%    +$28.10     14:31:47    active     │
│  0x7c8e…4d1a [▶]      61    47.5%    -$14.50     08:12:33    inactive   │
│                                                                          │
│─────────────────────────────────────────────────────────────────────────│
│  Selected: 0x4f2a…9c3e  (↑↓ to select, Enter to expand)                │
│                                                                          │
│  Recent trades:                                                          │
│  14:32:01  Will BTC exceed $100k by Feb?    YES  0.61  $240  → ACCEPT   │
│  14:31:12  Super Bowl winner — Chiefs?      YES  0.54  $500  → ACCEPT   │
│  13:44:08  EU AI Act enforcement delayed?   NO   0.33  $120  → REJECT   │
│  12:19:55  Nvidia >$200 by June?            YES  0.72  $180  → ACCEPT   │
│  11:02:41  Fed pivot before June?           YES  0.58   $90  → REJECT   │
└─────────────────────────────────────────────────────────────────────────┘
```

Implementation notes:
- Up/down arrow keys move selection between wallets
- `[A]` key opens an inline prompt: "Enter wallet address: 0x_____" — validates that it looks like a Polygon address before calling `tracker.add_wallet()`
- `[D]` key on a selected row marks the wallet inactive (removes from poll list, keeps history)
- Status column: `active` (seen in last 4h), `quiet` (4–24h), `inactive` (>24h or manually removed)
- Win rate rendered in `#24ff7b` if ≥55%, `#ff0f0f` if <45%, plain white otherwise
- Shadow P&L bar: `████` in `#24ff7b` for positive wallets, `#ff0f0f` for negative, scaled to the highest absolute P&L in the list

### Page 6 — Settings

All runtime configuration displayed read-only. These are the values currently loaded from `.env`. To change them, edit `.env` and restart the bot; the dashboard will reflect the new values on next poll.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Settings                                   config source: .env         │
│─────────────────────────────────────────────────────────────────────────│
│  TRADING MODE                                                            │
│  USE_REAL_MONEY          false              shadow mode active           │
│  SHADOW_BANKROLL_USD     $1,000.00          simulated starting balance   │
│                                                                          │
│  RISK PARAMETERS                                                         │
│  MAX_BET_FRACTION        0.05               max 5% of bankroll per trade │
│  MIN_CONFIDENCE          0.60               min signal score to act      │
│  MIN_BET_USD             $1.00              ignore Kelly outputs < this  │
│  KELLY_FRACTION          0.50               half-Kelly (recommended)     │
│                                                                          │
│  SYSTEM                                                                  │
│  POLL_INTERVAL_SECONDS   45                 seconds between wallet polls │
│  LOG_LEVEL               INFO                                            │
│  MODEL_PATH              model.joblib       active XGBoost model         │
│                                                                          │
│  WALLET                                                                  │
│  POLYGON_WALLET_ADDRESS  0x????…????        your trading wallet          │
│  POLYGON_PRIVATE_KEY     ••••••••••••       hidden                       │
│  WATCHED_WALLETS         4 wallets          use resolve_wallet.py to add │
│                                                                          │
│  ALERTS                                                                  │
│  TELEGRAM_BOT_TOKEN      ••••••••••••       hidden                       │
│  TELEGRAM_CHAT_ID        configured                                      │
│                                                                          │
│─────────────────────────────────────────────────────────────────────────│
│  DB stats: 1,204 trade_log rows  |  847 resolved  |  db size: 1.2 MB   │
│  Bot uptime: 6d 14h 22m          |  Model version: Jan 13 03:00         │
└─────────────────────────────────────────────────────────────────────────┘
```

Implementation notes:
- Private key and bot token always shown as `••••••••••••` — never revealed in the dashboard
- Wallet address shown truncated: `0x????…????`
- DB stats queried live: `SELECT COUNT(*), ...` on each refresh
- Uptime read from a `bot_started_at` timestamp written to a `data/bot_state.json` file by `main.py` on startup
- No editing in this page — it is intentionally read-only. The `USE_REAL_MONEY` flag is too consequential to toggle from a keypress.

### Core shared modules

#### `useDb.ts` — polling hook

```typescript
// dashboard/useDb.ts
import { useState, useEffect } from 'react'
import Database from 'better-sqlite3'

const DB_PATH = 'data/trading.db'

export function useQuery<T>(sql: string, params: unknown[] = [], intervalMs = 2000): T[] {
  const [rows, setRows] = useState<T[]>([])

  useEffect(() => {
    const run = () => {
      try {
        const db = new Database(DB_PATH, { readonly: true, fileMustExist: true })
        const result = db.prepare(sql).all(...params) as T[]
        db.close()
        setRows(result)
      } catch {
        // DB not ready yet — silently retry
      }
    }
    run()
    const id = setInterval(run, intervalMs)
    return () => clearInterval(id)
  }, [sql, intervalMs])

  return rows
}
```

#### `useEventStream.ts` — live events

```typescript
// dashboard/useEventStream.ts
// The bot writes new trade events to data/events.jsonl (one JSON line per event).
// This hook tails that file using fs.watchFile and parses new lines.
import { useState, useEffect } from 'react'
import fs from 'fs'

export interface LiveEvent {
  type:      'incoming' | 'signal' | 'placed' | 'skipped'
  trade_id:  string
  market_id: string
  question:  string
  side:      string
  price:     number
  size_usd:  number
  decision?: string
  confidence?: number
  reason?:   string
  ts:        number
}

export function useEventStream(maxEvents = 50): LiveEvent[] {
  const [events, setEvents] = useState<LiveEvent[]>([])

  useEffect(() => {
    const path = 'data/events.jsonl'
    let lastSize = 0

    const read = () => {
      try {
        const stat = fs.statSync(path)
        if (stat.size === lastSize) return
        const content = fs.readFileSync(path, 'utf8')
        lastSize = stat.size
        const lines = content.trim().split('\n').filter(Boolean)
        const parsed = lines.map(l => JSON.parse(l) as LiveEvent)
        setEvents(parsed.slice(-maxEvents))
      } catch {}
    }

    read()
    const watcher = fs.watchFile(path, { interval: 500 }, read)
    return () => fs.unwatchFile(path)
  }, [maxEvents])

  return events
}
```

The bot's `process_event` function (Section 17) calls `_emit_event()` at every decision point — incoming trade, accept, and each reject reason — so the event stream is populated automatically. No additional wiring is needed.

#### `dashboard/theme.ts` — single source of truth for all colors

```typescript
// dashboard/theme.ts
// All color constants for the dashboard.
// Black/white base with three brand accent colors.
// Import this everywhere instead of hardcoding color strings.

export const theme = {
  // Brand accents
  accent:  '#ff1c68',   // hot pink  — used for: borders, active nav tab, header title, mode badge
  green:   '#24ff7b',   // neon green — used for: ACCEPT, YES, positive P&L, thresholds met
  red:     '#ff0f0f',   // bright red — used for: REJECT, NO, negative P&L, thresholds not met

  // Base
  white:   'white',     // primary text, values, numbers
  dim:     'gray',      // secondary text, labels, muted rows
  border:  'gray',      // box borders (Ink only supports named colors for borderColor)
} as const

export type ThemeColor = typeof theme[keyof typeof theme]
```

#### `components/Box.tsx`

```typescript
// dashboard/components/Box.tsx
import React from 'react'
import { Box as InkBox, Text } from 'ink'
import { theme } from '../theme.js'

interface Props {
  title?: string
  children: React.ReactNode
  width?: string | number
  height?: string | number
  accent?: boolean   // if true, draws the border in theme.accent (#ff1c68)
}

export function Box({ title, children, width = '100%', height, accent = false }: Props) {
  return (
    <InkBox
      borderStyle="single"
      borderColor={accent ? theme.accent : theme.border}
      flexDirection="column"
      width={width}
      height={height}
      padding={1}
    >
      {title && (
        <InkBox marginBottom={1}>
          <Text color={theme.accent} bold>{title}</Text>
        </InkBox>
      )}
      {children}
    </InkBox>
  )
}
```

#### `components/BarSparkline.tsx`

```typescript
// dashboard/components/BarSparkline.tsx
import React from 'react'
import { Text } from 'ink'
import { theme } from '../theme.js'

interface Props {
  value:    number    // 0–1 fraction of total width to fill
  width?:   number   // total bar width in chars, default 20
  label?:   string   // optional text printed after the bar
  positive?: boolean  // if false, force red regardless of value sign (e.g. reject counts)
}

export function BarSparkline({ value, width = 20, label, positive }: Props) {
  const filled = Math.round(Math.abs(value) * width)
  const empty  = width - filled
  const bar    = '█'.repeat(Math.max(0, filled)) + '░'.repeat(Math.max(0, empty))

  // Color logic:
  //   positive=undefined → infer from sign of value (green if ≥0, red if <0)
  //   positive=true      → always green
  //   positive=false     → always red
  const color = (positive ?? value >= 0) ? theme.green : theme.red

  return (
    <Text>
      <Text color={color}>{bar}</Text>
      {label != null && <Text color={theme.dim}>  {label}</Text>}
    </Text>
  )
}
```

#### `dashboard/dashboard.tsx` — root app

```typescript
// dashboard/dashboard.tsx
import React, { useState } from 'react'
import { render, useInput, Box, Text } from 'ink'
import { theme } from './theme.js'
import { LiveFeed }    from './pages/LiveFeed.js'
import { Signals }     from './pages/Signals.js'
import { Performance } from './pages/Performance.js'
import { Models }      from './pages/Models.js'
import { Wallets }     from './pages/Wallets.js'
import { Settings }    from './pages/Settings.js'

type Page = 1 | 2 | 3 | 4 | 5 | 6

const PAGES: Record<Page, { label: string; Component: React.FC }> = {
  1: { label: 'Feed',        Component: LiveFeed },
  2: { label: 'Signals',     Component: Signals },
  3: { label: 'Performance', Component: Performance },
  4: { label: 'Models',      Component: Models },
  5: { label: 'Wallets',     Component: Wallets },
  6: { label: 'Settings',    Component: Settings },
}

// Read USE_REAL_MONEY from env at startup — shown in the header badge.
const IS_LIVE = process.env.USE_REAL_MONEY?.toLowerCase() === 'true'

function App() {
  const [page, setPage] = useState<Page>(1)
  const { Component } = PAGES[page]

  useInput((input) => {
    if (input === 'q') process.exit(0)
    const n = parseInt(input)
    if (n >= 1 && n <= 6) setPage(n as Page)
  })

  return (
    <Box
      flexDirection="column"
      borderStyle="single"
      borderColor={theme.accent}   // outer frame always in brand pink
      padding={0}
    >
      {/* ── Header ─────────────────────────────────────────────────── */}
      <Box borderStyle="single" borderColor={theme.border} paddingX={1} flexDirection="row">
        {/* Logo */}
        <Text color={theme.accent} bold>POLYMARKET BOT</Text>
        <Text>  </Text>

        {/* Mode badge */}
        <Text color={IS_LIVE ? theme.green : theme.red} bold>
          {IS_LIVE ? '[LIVE]' : '[SHADOW]'}
        </Text>
        <Text>  </Text>

        {/* Nav tabs */}
        {(Object.entries(PAGES) as [string, { label: string }][]).map(([n, { label }]) => (
          <React.Fragment key={n}>
            <Text color={page === Number(n) ? theme.accent : theme.dim}>
              {n}:{label}
            </Text>
            <Text>  </Text>
          </React.Fragment>
        ))}
      </Box>

      {/* ── Page content ───────────────────────────────────────────── */}
      <Box flexGrow={1} padding={1}>
        <Component />
      </Box>

      {/* ── Footer ─────────────────────────────────────────────────── */}
      <Box borderStyle="single" borderColor={theme.border} paddingX={1}>
        <Text color={theme.dim}>1–6 navigate  </Text>
        <Text color={theme.accent}>q</Text>
        <Text color={theme.dim}> quit</Text>
      </Box>
    </Box>
  )
}

render(<App />)
```

### Running the dashboard

The dashboard and bot run simultaneously in two separate terminal windows (or two panes in Windows Terminal):

```bash
# Terminal 1 — bot
uv run python main.py

# Terminal 2 — dashboard
cd dashboard
npm start
```

`npm start` runs `tsx dashboard.tsx` as defined in `dashboard/package.json`. The dashboard starts immediately; it will show empty tables until the bot begins writing events and DB rows.

---

*Build and run in shadow mode first. The shadow P&L report is the answer to the question "should I use real money?" — let the data answer it. The self-retraining loop means the model improves passively as long as the bot is running and markets are resolving. When the performance numbers are consistently good over several weeks, flip the flag.*
