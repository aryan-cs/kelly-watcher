#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required but was not found on PATH."
  exit 1
fi

MODE="$(uv run python -c "from config import use_real_money; print('live' if use_real_money() else 'shadow')")"
INITIAL_BANKROLL="$(uv run python -c "from config import shadow_bankroll_usd; print(f'{shadow_bankroll_usd():.2f}')")"

if [ "$MODE" != "shadow" ]; then
  echo "Refusing to reset while USE_REAL_MONEY=true. Switch back to shadow mode first."
  exit 1
fi

mkdir -p data logs

RESET_FILES=(
  "data/trading.db"
  "data/trading.db-shm"
  "data/trading.db-wal"
  "data/events.jsonl"
  "data/bot_state.json"
  "data/shadow_bot.pid"
)

PRESERVED_FILES=(
  ".env"
  ".env.example"
  "data/identity_cache.json"
  "model.joblib"
  "logs/"
)

load_bot_pids() {
  BOT_PIDS=()
  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    BOT_PIDS+=("$pid")
  done < <(pgrep -f "python main.py" || true)
}

load_bot_pids
if [ "${#BOT_PIDS[@]}" -gt 0 ]; then
  echo "Stopping existing bot process(es): ${BOT_PIDS[*]}"
  kill "${BOT_PIDS[@]}" || true
  sleep 2

  load_bot_pids
  if [ "${#BOT_PIDS[@]}" -gt 0 ]; then
    echo "Force-stopping remaining bot process(es): ${BOT_PIDS[*]}"
    kill -9 "${BOT_PIDS[@]}" || true
  fi
fi

echo "Resetting shadow runtime state back to the configured bankroll of \$${INITIAL_BANKROLL}..."
echo "Preserving config/settings files: ${PRESERVED_FILES[*]}"
rm -f "${RESET_FILES[@]}"

uv run python -c "from db import init_db; init_db()"

if [ "${1:-}" = "--foreground" ]; then
  echo "Starting shadow bot in foreground..."
  exec env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/kelly-watcher-pycache uv run python main.py
fi

echo "Starting shadow bot in background..."
nohup env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/kelly-watcher-pycache uv run python main.py > logs/shadow_runtime.out 2>&1 &
BOT_PID=$!
echo "$BOT_PID" > data/shadow_bot.pid

echo "Shadow bot restarted."
echo "PID: $BOT_PID"
echo "Initial bankroll: \$${INITIAL_BANKROLL}"
echo "Background log: logs/shadow_runtime.out"
echo "PID file: data/shadow_bot.pid"
