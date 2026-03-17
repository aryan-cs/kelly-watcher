# Kelly Watcher

Polymarket copy-trading bot with shadow-mode logging, Kelly sizing, model training, and a terminal dashboard.

## Quick start

```bash
uv sync
cp .env.example .env
uv run python -c "from db import init_db; init_db()"
uv run python main.py
```

Use `WATCHED_WALLETS` in `.env` to define the Polymarket wallets to track. Shadow mode is the default; no live orders are placed until `USE_REAL_MONEY=true`.

## Main pieces

- `main.py`: poll loop, scheduling, dashboard event stream
- `tracker.py`: Polymarket trade and market data client
- `signal_engine.py`: heuristic or XGBoost confidence scoring
- `executor.py`: shadow or live order execution
- `evaluator.py`: trade resolution and P&L reporting
- `train.py`: model training and calibration
- `dashboard/`: Ink terminal dashboard

## Useful commands

```bash
uv run python resolve_wallet.py
uv run python polymarket_setup.py
uv run python train.py
```

## Safety

- `USE_REAL_MONEY=false` is the default.
- Shadow trades still log features, decisions, and hypothetical P&L.
- Shadow mode now simulates market-order fills from the captured order book, so shadow entries and exits fail when a full-fill live FOK order would likely fail.
- Review the shadow performance before switching to live trading.
- Live mode now refuses to start with placeholder credentials, an empty watchlist, or too little shadow history unless you explicitly relax those checks in `.env`.
- A live entry circuit breaker pauses new buys after the configured bankroll drawdown threshold (`MAX_LIVE_DRAWDOWN_PCT`) while still allowing mirrored exits to keep reducing exposure.
