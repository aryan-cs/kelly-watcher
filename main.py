from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

from alerter import send_alert
from auto_retrain import retrain_cycle, should_retrain_early
from beliefs import sync_belief_priors
from config import (
    ConfigError,
    discovery_poll_interval_multiplier,
    hot_wallet_count,
    live_min_shadow_resolved,
    live_require_shadow_history,
    max_bet_fraction,
    max_daily_loss_pct,
    max_feed_staleness_seconds,
    max_live_drawdown_pct,
    max_live_health_failures,
    max_market_horizon_seconds,
    max_source_trade_age_seconds,
    max_market_exposure_fraction,
    max_total_open_exposure_fraction,
    max_trader_exposure_fraction,
    min_execution_window_seconds,
    min_bet_usd,
    min_confidence,
    poll_interval,
    private_key,
    retrain_base_cadence,
    retrain_early_check_seconds,
    retrain_hour_local,
    use_real_money,
    wallet_inactivity_limit_seconds,
    wallet_slow_drop_max_tracking_age_seconds,
    wallet_cold_start_min_observed_buys,
    wallet_performance_drop_max_avg_return,
    wallet_performance_drop_max_win_rate,
    wallet_performance_drop_min_trades,
    wallet_discovery_min_observed_buys,
    wallet_discovery_min_resolved_buys,
    wallet_discovery_size_multiplier,
    wallet_probation_size_multiplier,
    wallet_trusted_min_resolved_copied_buys,
    wallet_address,
    warm_poll_interval_multiplier,
    warm_wallet_count,
    watched_wallets,
)
from db import DB_PATH, get_conn, init_db
from dedup import DedupeCache
from evaluator import daily_report, resolve_shadow_trades
from executor import PolymarketExecutor
from kelly import size_signal
from market_scorer import build_market_features
from signal_engine import SignalEngine
from trade_contract import RESOLVED_EXECUTED_ENTRY_SQL
from tracker import PolymarketTracker
from trader_scorer import get_trader_features, refresh_trader_cache
from wallet_trust import apply_wallet_trust_sizing, get_wallet_trust_state
from watchlist_manager import WatchlistManager

load_dotenv()

Path("logs").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler("logs/bot.log", maxBytes=10 * 1024 * 1024, backupCount=5),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

EVENT_FILE = Path("data/events.jsonl")
BOT_STATE_FILE = Path("data/bot_state.json")
_emit_count = 0
WATCHED_WALLETS = watched_wallets()
_HEURISTIC_CONF_RE = re.compile(r"heuristic conf ([0-9.]+) < min ([0-9.]+)", re.IGNORECASE)
_MODEL_EDGE_RE = re.compile(r"model edge (-?[0-9.]+) < threshold ([0-9.]+)", re.IGNORECASE)
_MAX_SIZE_RE = re.compile(r"max size \$([0-9.]+) < min \$([0-9.]+)", re.IGNORECASE)
_BANKROLL_RE = re.compile(r"available bankroll \$([0-9.]+) < min \$([0-9.]+)", re.IGNORECASE)
_SIZE_ZERO_RE = re.compile(r"size \$([0-9.]+) <= 0", re.IGNORECASE)
_CONF_RE = re.compile(r"conf ([0-9.]+) < min ([0-9.]+)", re.IGNORECASE)
_SCORE_RE = re.compile(r"score ([0-9.]+) < min ([0-9.]+)", re.IGNORECASE)
_INVALID_PRICE_RE = re.compile(r"invalid price ([0-9.]+)", re.IGNORECASE)
_EXPIRES_RE = re.compile(r"expires in <([0-9]+)s", re.IGNORECASE)
_MAX_HORIZON_RE = re.compile(r"beyond max horizon ([0-9.]+[smhdw])", re.IGNORECASE)


@dataclass
class LiveEntryGuard:
    start_equity: float
    drawdown_limit_pct: float
    stop_equity: float
    triggered: bool = False
    alerted: bool = False

    def block_reason(self, account_equity: float) -> str | None:
        if self.drawdown_limit_pct <= 0 or self.start_equity <= 0:
            return None
        if self.triggered or account_equity <= self.stop_equity + 1e-9:
            self.triggered = True
            return (
                f"live entry guard tripped after a {self.drawdown_limit_pct * 100:.1f}% drawdown "
                f"(start ${self.start_equity:.2f}, current ${account_equity:.2f})"
            )
        return None


@dataclass
class DailyLossGuard:
    start_equity: float
    loss_limit_pct: float
    day_key: str

    def block_reason(self, account_equity: float, now_ts: int) -> str | None:
        current_day = time.strftime("%Y-%m-%d", time.localtime(now_ts))
        if current_day != self.day_key:
            self.day_key = current_day
            self.start_equity = max(account_equity, 0.0)

        if self.start_equity <= 0 and account_equity > 0:
            self.start_equity = account_equity
        if self.loss_limit_pct <= 0 or self.start_equity <= 0:
            return None

        stop_equity = max(self.start_equity * (1.0 - self.loss_limit_pct), 0.0)
        if account_equity <= stop_equity + 1e-9:
            return (
                f"daily loss guard tripped after a {self.loss_limit_pct * 100:.1f}% drawdown "
                f"(today start ${self.start_equity:.2f}, current ${account_equity:.2f})"
            )
        return None


def _emit_event(payload: dict) -> None:
    global _emit_count
    EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
    _emit_count += 1
    if _emit_count % 100 == 0:
        try:
            lines = EVENT_FILE.read_text(encoding="utf-8").splitlines(True)
            if len(lines) > 1000:
                EVENT_FILE.write_text("".join(lines[-1000:]), encoding="utf-8")
        except Exception:
            pass


def _market_url_for_event(event) -> str | None:
    meta = getattr(event, "raw_market_metadata", None)
    if not isinstance(meta, dict):
        return None

    candidates = [meta]
    nested_event = meta.get("event")
    if isinstance(nested_event, dict):
        candidates.append(nested_event)

    for candidate in candidates:
        direct_url = str(candidate.get("url") or candidate.get("marketUrl") or "").strip()
        if (
            (direct_url.startswith("https://") or direct_url.startswith("http://"))
            and "polymarket.com/" in direct_url.lower()
        ):
            return direct_url

        slug = str(candidate.get("slug") or candidate.get("marketSlug") or "").strip().strip("/")
        if slug:
            return f"https://polymarket.com/event/{slug}"

    return None


def _event_market_payload(event) -> dict[str, str]:
    market_url = _market_url_for_event(event)
    return {"market_url": market_url} if market_url else {}


def _write_bot_state(**extra) -> None:
    existing: dict[str, object] = {}
    if BOT_STATE_FILE.exists():
        try:
            payload = json.loads(BOT_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                existing = payload
        except Exception:
            existing = {}

    state = dict(existing)
    state.update(
        {
            "started_at": int(extra.pop("started_at", state.get("started_at") or time.time())),
            "mode": "live" if use_real_money() else "shadow",
            "n_wallets": len(WATCHED_WALLETS),
            "poll_interval": poll_interval(),
        }
    )
    state.update(extra)
    BOT_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _format_percent_text(value: str) -> str:
    return f"{float(value) * 100:.1f}%"


def _humanize_market_veto(veto: str) -> str:
    detail = (veto or "").strip()
    expires_match = _EXPIRES_RE.fullmatch(detail)
    if expires_match:
        return f"too close to resolution, less than {expires_match.group(1)} seconds remained to place the trade"
    max_horizon_match = _MAX_HORIZON_RE.fullmatch(detail)
    if max_horizon_match:
        return f"market resolves too far out, beyond the {max_horizon_match.group(1)} maximum horizon"
    if detail == "crossed order book":
        return "market data looked invalid because the order book was crossed"
    if detail == "missing order book":
        return "market data was incomplete because there was no order book snapshot"
    if detail == "no visible order book depth":
        return "market looked too thin to trade because there was no visible order book depth"
    if detail == "invalid market mid":
        return "market data looked invalid because the midpoint price was out of bounds"
    if detail == "invalid order book values":
        return "market data looked invalid because the order book values were negative"
    return f"market veto, {detail}"


def _humanize_reason(reason: str) -> str:
    text = (reason or "").strip()
    if not text:
        return "trade was rejected for an unspecified reason"

    lower = text.lower()
    if lower.startswith("heuristic sizing, "):
        return _humanize_reason(text.split(",", 1)[1].strip())
    if lower.startswith("kelly, "):
        return _humanize_reason(text.split(",", 1)[1].strip())
    if lower.startswith("market veto, "):
        return _humanize_market_veto(text.split(",", 1)[1].strip())
    if lower == "observed sell - not copying exits yet":
        return "watched trader was exiting a position, and the bot only copies entries right now"
    if lower == "missing market snapshot":
        return "market data was unavailable when this trade was observed"
    if lower == "failed to build market features":
        return "could not build the market snapshot needed to score this trade"
    if lower == "duplicate trade_id":
        return "this trade was already seen, so it was skipped as a duplicate"
    if lower == "order in-flight":
        return "an order for this market was already being placed, so this trade was skipped"
    if lower == "position already open":
        return "we already had this side of the market open, so the trade was skipped"
    if lower == "passed heuristic threshold":
        return "signal confidence cleared the heuristic threshold"
    if lower == "passed model edge threshold":
        return "model edge cleared the required threshold"
    if lower == "passed all checks":
        return "signal cleared scoring, sizing, and risk checks"
    if lower == "signal rejected":
        return "trade did not pass the signal checks"
    if lower == "bankroll depleted":
        return "balance too low, no bankroll was available for a new trade"
    if lower == "negative kelly - no edge at this price/confidence":
        return "Kelly sizing found no positive edge at this price, so the trade was skipped"
    if lower == "shadow simulation rejected the buy because the order book had no asks for a full fill":
        return "simulated live buy could not fill because there were no asks on the book"
    if lower == "shadow simulation rejected the buy because there was not enough ask depth to fill the whole order":
        return "simulated live buy could not fill because the ask book was too thin for the full size"
    if lower == "shadow simulation rejected the sell because the order book had no bids for a full fill":
        return "simulated live sell could not fill because there were no bids on the book"
    if lower == "shadow simulation rejected the sell because there was not enough bid depth to fill the whole order":
        return "simulated live sell could not fill because the bid book was too thin for the full size"

    for pattern, formatter in (
        (_HEURISTIC_CONF_RE, lambda m: f"signal confidence was {_format_percent_text(m.group(1))}, below the {_format_percent_text(m.group(2))} minimum"),
        (_MODEL_EDGE_RE, lambda m: f"model edge was {_format_percent_text(m.group(1))}, below the {_format_percent_text(m.group(2))} threshold"),
        (_MAX_SIZE_RE, lambda m: f"balance too low, calculated size was ${m.group(1)} but minimum bet size is ${m.group(2)}"),
        (_BANKROLL_RE, lambda m: f"balance too low, available bankroll was ${m.group(1)} but minimum bet size is ${m.group(2)}"),
        (_SIZE_ZERO_RE, lambda m: f"calculated trade size was ${m.group(1)}, so no order was placed"),
        (_CONF_RE, lambda m: f"confidence was {_format_percent_text(m.group(1))}, below the {_format_percent_text(m.group(2))} minimum needed to place a trade"),
        (_SCORE_RE, lambda m: f"heuristic score was {_format_percent_text(m.group(1))}, below the {_format_percent_text(m.group(2))} minimum needed to place a trade"),
        (_INVALID_PRICE_RE, lambda m: f"trade was skipped because the market price looked invalid ({m.group(1)})"),
    ):
        match = pattern.fullmatch(text)
        if match:
            return formatter(match)

    return text


def _wait_for_next_poll(loop_started_at: float, state_snapshot: dict) -> None:
    last_interval = poll_interval()

    while True:
        current_interval = poll_interval()
        if current_interval != last_interval:
            logger.info("Poll interval updated to %ss", current_interval)
            last_interval = current_interval
            _write_bot_state(**state_snapshot)

        remaining = loop_started_at + current_interval - time.time()
        if remaining <= 0:
            return

        time.sleep(min(remaining, 1.0))


def _reject_event(event, confidence: float, amount_usd: float, reason: str) -> None:
    shares = amount_usd / event.price if event.price > 0 else 0.0
    _emit_event(
        {
            "type": "signal",
            "trade_id": event.trade_id,
            "market_id": event.market_id,
            "question": event.question,
            **_event_market_payload(event),
            "side": event.side,
            "action": event.action,
            "price": event.price,
            "shares": round(shares, 6),
            "amount_usd": amount_usd,
            "size_usd": amount_usd,
            "username": event.trader_name,
            "trader": event.trader_address,
            "decision": "REJECT",
            "confidence": confidence,
            "reason": reason,
            "ts": int(time.time()),
        }
    )


def _skip_event(event, amount_usd: float, reason: str, decision: str = "SKIP") -> None:
    shares = amount_usd / event.price if event.price > 0 else 0.0
    _emit_event(
        {
            "type": "signal",
            "trade_id": event.trade_id,
            "market_id": event.market_id,
            "question": event.question,
            **_event_market_payload(event),
            "side": event.side,
            "action": event.action,
            "price": event.price,
            "shares": round(shares, 6),
            "amount_usd": amount_usd,
            "size_usd": amount_usd,
            "username": event.trader_name,
            "trader": event.trader_address,
            "decision": decision,
            "confidence": 0.0,
            "reason": reason,
            "ts": int(time.time()),
        }
    )


def _ignore_event(event, amount_usd: float, reason: str) -> None:
    _skip_event(event, amount_usd, reason, decision="IGNORE")


def _pause_event(event, amount_usd: float, reason: str) -> None:
    _skip_event(event, amount_usd, reason, decision="PAUSE")


def _is_non_actionable_exit_reason(reason: str) -> bool:
    return (reason or "").strip().lower() == "watched trader exited, but we had no matching position open to close"


def process_event(
    event,
    engine,
    executor,
    dedup,
    bankroll,
    account_equity,
    entry_block_reason: str | None = None,
) -> float:
    _emit_event(
        {
            "type": "incoming",
            "trade_id": event.trade_id,
            "market_id": event.market_id,
            "question": event.question,
            **_event_market_payload(event),
            "side": event.side,
            "action": event.action,
            "price": event.price,
            "shares": event.shares,
            "amount_usd": event.size_usd,
            "size_usd": event.size_usd,
            "username": event.trader_name,
            "trader": event.trader_address,
            "ts": event.timestamp,
        }
    )

    if event.action == "sell":
        result = executor.execute_exit(
            trade_id=event.trade_id,
            market_id=event.market_id,
            token_id=event.token_id,
            side=event.side,
            event=event,
            dedup=dedup,
        )
        if result.placed:
            execution_price = (result.dollar_size / result.shares) if result.shares > 0 else event.price
            _emit_event(
                {
                    "type": "signal",
                    "trade_id": event.trade_id,
                    "market_id": event.market_id,
                    "question": event.question,
                    **_event_market_payload(event),
                    "side": event.side,
                    "action": event.action,
                    "price": round(execution_price, 6),
                    "shares": round(result.shares, 6),
                    "amount_usd": result.dollar_size,
                    "size_usd": result.dollar_size,
                    "username": event.trader_name,
                    "trader": event.trader_address,
                    "decision": "EXIT",
                    "confidence": 0.0,
                    "reason": result.reason,
                    "ts": int(time.time()),
                }
            )
            return result.dollar_size
        else:
            dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
            if _is_non_actionable_exit_reason(result.reason):
                _ignore_event(event, result.dollar_size, result.reason)
            else:
                executor.log_skip(
                    trade_id=event.trade_id,
                    market_id=event.market_id,
                    question=event.question,
                    trader_address=event.trader_address,
                    side=event.side,
                    price=event.price,
                    size_usd=result.dollar_size,
                    confidence=0.0,
                    kelly_f=0.0,
                    reason=result.reason,
                    event=event,
                )
                _skip_event(event, result.dollar_size, result.reason)
        return 0.0

    if event.action != "buy":
        reason = f"observed unsupported trader action, {event.action.upper()}"
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _ignore_event(event, 0.0, reason)
        return 0.0

    if entry_block_reason:
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _pause_event(event, 0.0, entry_block_reason)
        return 0.0

    if not event.snapshot:
        reason = _humanize_reason("missing market snapshot")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            event=event,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, 0.0, reason)
        return 0.0

    trader_f = get_trader_features(event.trader_address, event.size_usd)
    rough_market_price = float(event.snapshot.get("best_ask") or event.snapshot.get("mid") or event.price or 0.0)
    if not (0.01 < rough_market_price < 0.99):
        rough_market_price = event.price
    rough = size_signal(0.65, rough_market_price, bankroll, engine.sizing_mode()).get("dollar_size", 0.0)
    rough_size = rough if rough > 0 else max(min_bet_usd(), 5.0)
    rough_fill, rough_fill_reason = executor.estimate_entry_fill(getattr(event, "raw_orderbook", None), rough_size)
    if rough_fill is None:
        reason = _humanize_reason(rough_fill_reason or "shadow simulation buy failed")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=rough_size,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            event=event,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, rough_size, reason)
        return 0.0

    market_f = build_market_features(event.snapshot, event.close_time, rough_size, rough_fill.avg_price)
    if market_f is None:
        reason = _humanize_reason("failed to build market features")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            event=event,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, 0.0, reason)
        return 0.0

    signal = engine.evaluate(
        trader_f,
        market_f,
        rough_size,
        trader_address=event.trader_address,
    )
    if signal.get("veto"):
        reason = _humanize_market_veto(signal["veto"])
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, 0.0, reason)
        return 0.0

    if not signal.get("passed", False):
        reason = _humanize_reason(signal.get("reason") or "signal rejected")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=signal["confidence"],
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, signal["confidence"], 0.0, reason)
        return 0.0

    ok, gate_reason = dedup.gate(event.trade_id, event.market_id, event.side, event.token_id)
    preview_sizing = size_signal(
        signal["confidence"],
        rough_fill.avg_price if rough_fill.avg_price > 0 else event.price,
        bankroll,
        signal.get("mode", "heuristic"),
        min_confidence_override=signal.get("min_confidence"),
    )

    if not ok:
        reason = _humanize_reason(gate_reason)
        if gate_reason != "duplicate trade_id":
            executor.log_skip(
                trade_id=event.trade_id,
                market_id=event.market_id,
                question=event.question,
                trader_address=event.trader_address,
                side=event.side,
                price=event.price,
                size_usd=preview_sizing.get("dollar_size", 0.0),
                confidence=signal["confidence"],
                kelly_f=preview_sizing.get("kelly_f", 0.0),
                reason=reason,
                trader_f=trader_f,
                market_f=market_f,
                event=event,
                signal=signal,
            )
            dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
            _reject_event(event, signal["confidence"], preview_sizing.get("dollar_size", 0.0), reason)
        return 0.0

    trust_state = get_wallet_trust_state(event.trader_address)
    signal = dict(signal)
    signal["wallet_trust"] = trust_state.as_dict()
    if trust_state.skip_reason:
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=preview_sizing.get("dollar_size", 0.0),
            confidence=signal["confidence"],
            kelly_f=preview_sizing.get("kelly_f", 0.0),
            reason=trust_state.skip_reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _skip_event(event, preview_sizing.get("dollar_size", 0.0), trust_state.skip_reason)
        return 0.0

    sizing = apply_wallet_trust_sizing(preview_sizing, trust_state)
    fill_estimate = rough_fill
    fill_reason = None
    for _ in range(3):
        if sizing["dollar_size"] == 0.0:
            break
        fill_estimate, fill_reason = executor.estimate_entry_fill(
            getattr(event, "raw_orderbook", None),
            sizing["dollar_size"],
        )
        if fill_estimate is None:
            break
        next_sizing = size_signal(
            signal["confidence"],
            fill_estimate.avg_price if fill_estimate.avg_price > 0 else event.price,
            bankroll,
            signal.get("mode", "heuristic"),
            min_confidence_override=signal.get("min_confidence"),
        )
        next_sizing = apply_wallet_trust_sizing(next_sizing, trust_state)
        if (
            next_sizing["dollar_size"] == sizing["dollar_size"]
            and abs(next_sizing.get("kelly_f", 0.0) - sizing.get("kelly_f", 0.0)) < 1e-9
        ):
            sizing = next_sizing
            break
        sizing = next_sizing

    if sizing["dollar_size"] == 0.0:
        reason = _humanize_reason(sizing["reason"])
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=signal["confidence"],
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, signal["confidence"], 0.0, reason)
        return 0.0

    if fill_estimate is None:
        reason = _humanize_reason(fill_reason or "shadow simulation buy failed")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=sizing["dollar_size"],
            confidence=signal["confidence"],
            kelly_f=sizing.get("kelly_f", 0.0),
            reason=reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, signal["confidence"], sizing["dollar_size"], reason)
        return 0.0

    exposure_block_reason = executor.entry_risk_block_reason(
        market_id=event.market_id,
        trader_address=event.trader_address,
        proposed_size_usd=sizing["dollar_size"],
        account_equity=account_equity,
    )
    if exposure_block_reason:
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=sizing["dollar_size"],
            confidence=signal["confidence"],
            kelly_f=sizing.get("kelly_f", 0.0),
            reason=exposure_block_reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _skip_event(event, sizing["dollar_size"], exposure_block_reason)
        return 0.0

    market_f_final = build_market_features(
        event.snapshot,
        event.close_time,
        sizing["dollar_size"],
        fill_estimate.avg_price,
    )
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
        market_f=market_f_final or market_f,
        dedup=dedup,
    )

    if result.placed:
        execution_price = (result.dollar_size / result.shares) if result.shares > 0 else event.price
        _emit_event(
            {
                "type": "signal",
                "trade_id": event.trade_id,
                "market_id": event.market_id,
                "question": event.question,
                **_event_market_payload(event),
                "side": event.side,
                "action": event.action,
                "price": round(execution_price, 6),
                "shares": round(result.shares, 6),
                "amount_usd": result.dollar_size,
                "size_usd": result.dollar_size,
                "username": event.trader_name,
                "trader": event.trader_address,
                "decision": "ACCEPT",
                "confidence": signal["confidence"],
                "raw_confidence": signal.get("raw_confidence"),
                "signal_mode": signal.get("mode"),
                "belief_prior": signal.get("belief_prior"),
                "belief_blend": signal.get("belief_blend"),
                "belief_evidence": signal.get("belief_evidence"),
                "trader_score": signal.get("trader", {}).get("score"),
                "market_score": signal.get("market", {}).get("score"),
                "wallet_trust_tier": trust_state.tier,
                "wallet_trust_note": sizing.get("wallet_trust_note"),
                "shadow": result.shadow,
                "order_id": result.order_id,
                "reason": _humanize_reason("passed all checks"),
                "ts": int(time.time()),
            }
        )
        return -result.dollar_size
    else:
        _reject_event(event, signal["confidence"], 0.0, _humanize_reason(result.reason))
        return 0.0


def _backup_db() -> None:
    if DB_PATH.exists():
        shutil.copy(DB_PATH, DB_PATH.with_suffix(".db.bak"))


def _looks_like_placeholder(value: str) -> bool:
    text = (value or "").strip().lower()
    return (
        not text
        or "your_" in text
        or text.endswith("_here")
        or text in {"changeme", "replace_me"}
    )


def _resolved_shadow_trade_count() -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM trade_log
            WHERE real_money=0 AND {RESOLVED_EXECUTED_ENTRY_SQL}
            """
        ).fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def _validate_startup() -> None:
    errors: list[str] = []
    warnings: list[str] = []

    def _capture_config(getter):
        try:
            return getter()
        except ConfigError as exc:
            errors.append(str(exc))
            return None

    if not WATCHED_WALLETS:
        errors.append("WATCHED_WALLETS is empty")

    confidence = _capture_config(min_confidence)
    if confidence is not None and not (0.0 < confidence < 1.0):
        errors.append(f"MIN_CONFIDENCE must be between 0 and 1, got {confidence}")

    max_fraction = _capture_config(max_bet_fraction)
    if max_fraction is not None and not (0.0 < max_fraction <= 1.0):
        errors.append(f"MAX_BET_FRACTION must be between 0 and 1, got {max_fraction}")

    minimum_bet = _capture_config(min_bet_usd)
    if minimum_bet is not None and minimum_bet <= 0:
        errors.append(f"MIN_BET_USD must be positive, got {minimum_bet}")

    _capture_config(hot_wallet_count)
    _capture_config(warm_wallet_count)
    _capture_config(warm_poll_interval_multiplier)
    _capture_config(discovery_poll_interval_multiplier)
    _capture_config(wallet_inactivity_limit_seconds)
    _capture_config(wallet_slow_drop_max_tracking_age_seconds)
    _capture_config(wallet_performance_drop_min_trades)
    _capture_config(wallet_performance_drop_max_win_rate)
    _capture_config(wallet_performance_drop_max_avg_return)
    cold_start_min_observed = _capture_config(wallet_cold_start_min_observed_buys)
    discovery_min_observed = _capture_config(wallet_discovery_min_observed_buys)
    discovery_min_resolved = _capture_config(wallet_discovery_min_resolved_buys)
    discovery_multiplier = _capture_config(wallet_discovery_size_multiplier)
    probation_multiplier = _capture_config(wallet_probation_size_multiplier)
    trusted_min_resolved = _capture_config(wallet_trusted_min_resolved_copied_buys)
    min_window_seconds = _capture_config(min_execution_window_seconds)
    max_horizon_seconds = _capture_config(max_market_horizon_seconds)
    _capture_config(max_source_trade_age_seconds)

    if (
        cold_start_min_observed is not None
        and discovery_min_observed is not None
        and cold_start_min_observed > discovery_min_observed
    ):
        errors.append(
            "WALLET_COLD_START_MIN_OBSERVED_BUYS must be <= WALLET_DISCOVERY_MIN_OBSERVED_BUYS"
        )
    if (
        discovery_multiplier is not None
        and probation_multiplier is not None
        and discovery_multiplier > probation_multiplier
    ):
        warnings.append(
            "WALLET_DISCOVERY_SIZE_MULTIPLIER is greater than WALLET_PROBATION_SIZE_MULTIPLIER; discovery trades will size larger than probation trades"
        )
    if (
        min_window_seconds is not None
        and max_horizon_seconds is not None
        and max_horizon_seconds != float("inf")
        and min_window_seconds >= max_horizon_seconds
    ):
        errors.append("MIN_EXECUTION_WINDOW must be smaller than MAX_MARKET_HORIZON")
    if (
        discovery_min_resolved is not None
        and trusted_min_resolved is not None
        and discovery_min_resolved > trusted_min_resolved
    ):
        errors.append(
            "WALLET_DISCOVERY_MIN_RESOLVED_BUYS must be <= WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS"
        )

    if use_real_money():
        our_wallet = wallet_address()
        if _looks_like_placeholder(private_key()):
            errors.append("POLYGON_PRIVATE_KEY is missing or still set to a placeholder")
        if _looks_like_placeholder(our_wallet):
            errors.append("POLYGON_WALLET_ADDRESS is missing or still set to a placeholder")
        if our_wallet and our_wallet in WATCHED_WALLETS:
            errors.append("POLYGON_WALLET_ADDRESS is also in WATCHED_WALLETS, which can create a self-copy loop")
        if max_fraction is not None and max_fraction > 0.10:
            warnings.append(
                f"MAX_BET_FRACTION is {max_fraction:.2f}; consider keeping live single-trade risk at 10% or below"
            )
        current_interval = poll_interval()
        if current_interval < 0.25:
            warnings.append(
                f"POLL_INTERVAL_SECONDS is {current_interval:.2f}s; extremely fast live polling can amplify duplicate/latency risk"
            )
        live_drawdown_limit = _capture_config(max_live_drawdown_pct)
        if live_drawdown_limit is not None and not (0.0 <= live_drawdown_limit <= 1.0):
            errors.append(f"MAX_LIVE_DRAWDOWN_PCT must be between 0 and 1, got {live_drawdown_limit}")
        daily_loss_limit = _capture_config(max_daily_loss_pct)
        if daily_loss_limit is not None and not (0.0 <= daily_loss_limit <= 1.0):
            errors.append(f"MAX_DAILY_LOSS_PCT must be between 0 and 1, got {daily_loss_limit}")
        total_exposure_limit = _capture_config(max_total_open_exposure_fraction)
        if total_exposure_limit is not None and not (0.0 <= total_exposure_limit <= 1.0):
            errors.append(
                "MAX_TOTAL_OPEN_EXPOSURE_FRACTION must be between 0 and 1, "
                f"got {total_exposure_limit}"
            )
        market_exposure_limit = _capture_config(max_market_exposure_fraction)
        if market_exposure_limit is not None and not (0.0 <= market_exposure_limit <= 1.0):
            errors.append(
                "MAX_MARKET_EXPOSURE_FRACTION must be between 0 and 1, "
                f"got {market_exposure_limit}"
            )
        trader_exposure_limit = _capture_config(max_trader_exposure_fraction)
        if trader_exposure_limit is not None and not (0.0 <= trader_exposure_limit <= 1.0):
            errors.append(
                "MAX_TRADER_EXPOSURE_FRACTION must be between 0 and 1, "
                f"got {trader_exposure_limit}"
            )
        live_health_failure_limit = _capture_config(max_live_health_failures)
        if live_health_failure_limit is not None and live_health_failure_limit < 1:
            errors.append(
                "MAX_LIVE_HEALTH_FAILURES must be at least 1, "
                f"got {live_health_failure_limit}"
            )
        if live_require_shadow_history():
            resolved = _resolved_shadow_trade_count()
            minimum = _capture_config(live_min_shadow_resolved)
            if minimum is None:
                minimum = 0
            if resolved < minimum:
                errors.append(
                    f"LIVE mode is blocked until shadow history is available: {resolved} resolved shadow trades < required {minimum}"
                )

    for warning in warnings:
        logger.warning("Startup warning: %s", warning)

    if errors:
        message = "Startup validation failed:\n- " + "\n- ".join(errors)
        logger.error(message)
        if use_real_money():
            send_alert(message)
        raise RuntimeError(message)


def _init_live_entry_guard(executor: PolymarketExecutor) -> LiveEntryGuard | None:
    if not use_real_money():
        return None

    start_equity = max(executor.get_account_equity_usd(), 0.0)
    drawdown_limit_pct = max_live_drawdown_pct()
    stop_equity = max(start_equity * (1.0 - drawdown_limit_pct), 0.0)
    logger.info(
        "Live entry guard armed: start_equity=$%.2f stop_equity=$%.2f drawdown_limit=%.1f%%",
        start_equity,
        stop_equity,
        drawdown_limit_pct * 100.0,
    )
    return LiveEntryGuard(
        start_equity=start_equity,
        drawdown_limit_pct=drawdown_limit_pct,
        stop_equity=stop_equity,
    )


def _init_daily_loss_guard(executor: PolymarketExecutor) -> DailyLossGuard:
    start_equity = max(executor.get_account_equity_usd(), 0.0)
    return DailyLossGuard(
        start_equity=start_equity,
        loss_limit_pct=max_daily_loss_pct(),
        day_key=time.strftime("%Y-%m-%d", time.localtime()),
    )


def _entry_pause_reason(
    tracker: PolymarketTracker,
    executor: PolymarketExecutor,
    live_entry_guard: LiveEntryGuard | None,
    daily_loss_guard: DailyLossGuard,
    account_equity: float,
) -> str | None:
    now_ts = int(time.time())
    if live_entry_guard is not None:
        reason = live_entry_guard.block_reason(account_equity)
        if reason:
            return reason

    reason = daily_loss_guard.block_reason(account_equity, now_ts)
    if reason:
        return reason

    last_ok_at, consecutive_failures = tracker.trade_feed_health()
    if consecutive_failures >= max_live_health_failures():
        return (
            f"source trade feed degraded after {consecutive_failures} consecutive trade-feed failures"
        )
    if last_ok_at > 0 and (now_ts - last_ok_at) > max_feed_staleness_seconds():
        return (
            f"source trade feed is stale; the last successful trade poll was {now_ts - last_ok_at}s ago"
        )

    if use_real_money():
        live_reason = executor.live_entry_health_reason()
        if live_reason:
            return live_reason

    return None


def main() -> None:
    logger.info("=" * 60)
    logger.info("Polymarket copy-trading bot starting")
    logger.info("Mode: %s", "LIVE (REAL MONEY)" if use_real_money() else "SHADOW (no real money)")
    logger.info("=" * 60)

    init_db()
    _validate_startup()
    EVENT_FILE.touch(exist_ok=True)
    start_ts = int(time.time())
    watchlist = WatchlistManager(WATCHED_WALLETS)
    bot_state_snapshot: dict[str, object] = {
        "started_at": start_ts,
        "last_loop_started_at": 0,
        "last_activity_at": start_ts,
        "loop_in_progress": False,
        "last_poll_at": 0,
        "last_poll_duration_s": 0.0,
        "bankroll_usd": None,
        "last_event_count": 0,
        "polled_wallet_count": 0,
    }
    last_activity_write_at = 0.0
    current_loop_started_at = 0
    bot_state_lock = threading.Lock()

    def _persist_bot_state(**updates: object) -> None:
        with bot_state_lock:
            bot_state_snapshot.update(updates)
            _write_bot_state(**bot_state_snapshot)

    def _heartbeat(*, force: bool = False) -> None:
        nonlocal last_activity_write_at
        now_ts = time.time()
        if not force and (now_ts - last_activity_write_at) < 1.0:
            return
        last_activity_write_at = now_ts
        updates: dict[str, object] = {
            "last_activity_at": int(now_ts),
            "loop_in_progress": current_loop_started_at > 0,
        }
        if current_loop_started_at > 0:
            updates["last_loop_started_at"] = current_loop_started_at
        _persist_bot_state(**updates)

    _persist_bot_state(**watchlist.state_fields())
    sync_belief_priors()

    tracker = PolymarketTracker(WATCHED_WALLETS, activity_callback=_heartbeat)
    executor = PolymarketExecutor()
    executor.validate_live_wallet_ready(min_required_balance_usd=min_bet_usd())
    _persist_bot_state(bankroll_usd=round(executor.get_usdc_balance(), 2))
    tracker.prime_identities(watchlist.startup_wallets())
    live_entry_guard = _init_live_entry_guard(executor)
    daily_loss_guard = _init_daily_loss_guard(executor)
    engine = SignalEngine()
    dedup = DedupeCache()
    dedup.load_from_db()
    tracker.seen_ids.update(dedup.seen_ids)
    initial_live_sync_ok = dedup.sync_positions_from_api(tracker, wallet_address())
    if use_real_money() and not initial_live_sync_ok:
        raise RuntimeError("Initial live positions sync failed; refusing to start without a confirmed view of open positions")
    refresh_trader_cache(watchlist.startup_wallets())
    watchlist.refresh()
    _persist_bot_state(**watchlist.state_fields())
    resolve_shadow_trades()
    dedup.load_from_db()
    tracker.seen_ids.update(dedup.seen_ids)

    def _refresh_watchlist() -> None:
        refresh_trader_cache(watchlist.active_wallets())
        watchlist.refresh()
        _persist_bot_state(**watchlist.state_fields())

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: (resolve_shadow_trades(), dedup.load_from_db()),
        "interval",
        minutes=2,
        id="resolve_trades",
    )
    scheduler.add_job(
        daily_report,
        "cron",
        hour=8,
        minute=0,
        id="daily_report",
    )
    retrain_cadence = retrain_base_cadence()
    retrain_hour = retrain_hour_local()
    retrain_trigger = {"hour": retrain_hour, "minute": 0, "id": "scheduled_retrain"}
    if retrain_cadence == "weekly":
        scheduler.add_job(
            lambda: retrain_cycle(engine),
            "cron",
            day_of_week="mon",
            **retrain_trigger,
        )
    else:
        scheduler.add_job(
            lambda: retrain_cycle(engine),
            "cron",
            **retrain_trigger,
        )
    scheduler.add_job(
        lambda: should_retrain_early(engine) and retrain_cycle(engine),
        "interval",
        seconds=retrain_early_check_seconds(),
        id="early_retrain_check",
    )
    scheduler.add_job(
        lambda: dedup.sync_positions_from_api(tracker, wallet_address()),
        "interval",
        minutes=5,
        id="sync_positions",
    )
    scheduler.add_job(
        _refresh_watchlist,
        "interval",
        minutes=10,
        id="refresh_trader_cache",
    )
    scheduler.add_job(
        _backup_db,
        "cron",
        hour=4,
        id="db_backup",
    )
    scheduler.start()

    mode_str = "LIVE" if use_real_money() else "SHADOW"
    tier_state = watchlist.state_fields()
    send_alert(
        f"Bot started [{mode_str}]\n"
        f"Watching {len(tracker.wallets)} wallets\n"
        f"Tracked/Dropped: {tier_state['tracked_wallet_count']}/{tier_state['dropped_wallet_count']}\n"
        f"Hot/Warm/Discovery: {tier_state['hot_wallet_count']}/{tier_state['warm_wallet_count']}/{tier_state['discovery_wallet_count']}\n"
        f"Poll interval: {poll_interval()}s"
    )

    try:
        last_entry_pause_reason: str | None = None
        while True:
            loop_start = time.time()
            current_loop_started_at = int(loop_start)
            _heartbeat(force=True)
            event_count = 0
            polled_wallet_count = 0
            bankroll = 0.0
            account_equity = 0.0
            entry_block_reason = None
            try:
                bankroll = executor.get_usdc_balance()
                account_equity = executor.get_account_equity_usd() if live_entry_guard is not None else bankroll
                if bankroll < 1.0:
                    logger.warning("Low balance: $%.2f - skipping poll", bankroll)
                else:
                    _heartbeat()
                    watchlist.refresh()
                    poll_batches = watchlist.poll_batches()
                    polled_wallet_count = sum(len(batch.wallets) for batch in poll_batches)
                    _persist_bot_state(
                        polled_wallet_count=polled_wallet_count,
                        **watchlist.state_fields(),
                    )
                    events = []
                    for batch in poll_batches:
                        if not batch.wallets:
                            continue
                        events.extend(tracker.poll(list(batch.wallets), trade_limit=batch.trade_limit))
                    event_count = len(events)
                    for event in events:
                        _heartbeat()
                        entry_block_reason = _entry_pause_reason(
                            tracker,
                            executor,
                            live_entry_guard,
                            daily_loss_guard,
                            account_equity,
                        )
                        if entry_block_reason != last_entry_pause_reason:
                            if entry_block_reason:
                                logger.error(entry_block_reason)
                                send_alert(
                                    f"[ENTRY PAUSED]\n{entry_block_reason}\nNew entries are paused until the condition clears."
                                )
                            elif last_entry_pause_reason:
                                logger.info("Entry pause cleared: %s", last_entry_pause_reason)
                                send_alert("[ENTRY PAUSED] Condition cleared; new entries are enabled again.")
                            last_entry_pause_reason = entry_block_reason
                        try:
                            bankroll = max(
                                bankroll
                                + process_event(
                                    event,
                                    engine,
                                    executor,
                                    dedup,
                                    bankroll,
                                    account_equity,
                                    entry_block_reason=entry_block_reason,
                                ),
                                0.0,
                            )
                            account_equity = (
                                executor.get_account_equity_usd()
                                if live_entry_guard is not None
                                else bankroll
                            )
                        except Exception as exc:
                            logger.error(
                                "Event processing failed for trade %s: %s",
                                event.trade_id,
                                exc,
                                exc_info=True,
                            )
                            send_alert(f"[ERROR] Event {event.trade_id[:12]} failed: {exc}")
                        finally:
                            if event.trade_id in dedup.seen_ids:
                                tracker.seen_ids.add(event.trade_id)
            except Exception as exc:
                logger.error("Main loop error: %s", exc, exc_info=True)
                send_alert(f"[ERROR] Loop error: {exc}")

            elapsed = time.time() - loop_start
            state_snapshot = {
                "started_at": start_ts,
                "last_loop_started_at": current_loop_started_at,
                "last_activity_at": int(time.time()),
                "last_poll_at": int(time.time()),
                "last_poll_duration_s": round(elapsed, 3),
                "bankroll_usd": round(bankroll, 2),
                "last_event_count": event_count,
                "polled_wallet_count": polled_wallet_count,
                "loop_in_progress": False,
                **watchlist.state_fields(),
            }
            current_loop_started_at = 0
            _persist_bot_state(**state_snapshot)
            _wait_for_next_poll(loop_start, state_snapshot)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        send_alert("Bot stopped")
    finally:
        scheduler.shutdown(wait=False)
        tracker.close()


if __name__ == "__main__":
    main()
