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

from alerter import (
    build_bullets,
    build_lines,
    build_market_error_alert,
    build_trade_resolution_alert,
    send_alert,
)
from auto_retrain import retrain_cycle_report, should_retrain_early
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
    retrain_min_samples,
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
    wallet_quality_size_max_multiplier,
    wallet_quality_size_min_multiplier,
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
from market_urls import market_url_from_metadata
from signal_engine import SignalEngine
from telegram_runtime import service_telegram_commands
from trade_contract import RESOLVED_EXECUTED_ENTRY_SQL
from tracker import PolymarketTracker, TradeEvent
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
MANUAL_RETRAIN_REQUEST_FILE = Path("data/manual_retrain_request.json")
MANUAL_TRADE_REQUEST_FILE = Path("data/manual_trade_request.json")
_emit_count = 0
_event_lock = threading.Lock()
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
    _equity_locked: bool = False

    def block_reason(self, account_equity: float, now_ts: int) -> str | None:
        current_day = time.strftime("%Y-%m-%d", time.localtime(now_ts))
        if current_day != self.day_key:
            self.day_key = current_day
            self._equity_locked = False

        if not self._equity_locked and account_equity > 0:
            self.start_equity = account_equity
            self._equity_locked = True
        if self.loss_limit_pct <= 0 or self.start_equity <= 0:
            return None

        stop_equity = max(self.start_equity * (1.0 - self.loss_limit_pct), 0.0)
        if account_equity <= stop_equity + 1e-9:
            return (
                f"daily loss guard tripped after a {self.loss_limit_pct * 100:.1f}% drawdown "
                f"(today start ${self.start_equity:.2f}, current ${account_equity:.2f})"
            )
        return None


@dataclass(frozen=True)
class ManualTradeRequest:
    action: str
    market_id: str
    token_id: str
    side: str
    question: str
    trader_address: str
    amount_usd: float | None
    request_id: str
    requested_at: int
    source: str


def _emit_event(payload: dict) -> None:
    global _emit_count
    EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _event_lock:
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
    return market_url_from_metadata(getattr(event, "raw_market_metadata", None))


def _event_market_payload(event) -> dict[str, str]:
    market_url = _market_url_for_event(event)
    return {"market_url": market_url} if market_url else {}


def _repair_event_file_market_urls() -> None:
    if not EVENT_FILE.exists():
        return

    try:
        lines = EVENT_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return

    trade_ids: list[str] = []
    parsed_rows: list[dict[str, object] | None] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            parsed_rows.append(None)
            continue
        if not isinstance(payload, dict):
            parsed_rows.append(None)
            continue
        parsed_rows.append(payload)
        trade_id = str(payload.get("trade_id") or "").strip()
        if trade_id:
            trade_ids.append(trade_id)

    if not trade_ids:
        return

    conn = get_conn()
    placeholders = ",".join("?" for _ in trade_ids)
    rows = conn.execute(
        f"SELECT trade_id, market_url FROM trade_log WHERE trade_id IN ({placeholders})",
        tuple(trade_ids),
    ).fetchall()
    conn.close()
    market_url_by_trade_id = {
        str(row["trade_id"] or "").strip(): str(row["market_url"] or "").strip()
        for row in rows
        if str(row["trade_id"] or "").strip() and str(row["market_url"] or "").strip()
    }

    updated = False
    repaired_lines: list[str] = []
    for original_line, payload in zip(lines, parsed_rows):
        if payload is None:
            repaired_lines.append(original_line)
            continue
        trade_id = str(payload.get("trade_id") or "").strip()
        canonical_url = market_url_by_trade_id.get(trade_id)
        if canonical_url and str(payload.get("market_url") or "").strip() != canonical_url:
            payload["market_url"] = canonical_url
            repaired_lines.append(json.dumps(payload, separators=(",", ":"), default=str))
            updated = True
            continue
        repaired_lines.append(original_line)

    if updated:
        EVENT_FILE.write_text("\n".join(repaired_lines) + "\n", encoding="utf-8")


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


def _send_resolution_alerts(resolved_rows: list[dict[str, object]]) -> None:
    for row in resolved_rows:
        if not bool(row.get("executed")):
            continue

        mode = "live" if bool(row.get("real_money")) else "shadow"
        side = str(row.get("side") or "").strip()
        pnl = float(row.get("pnl") or 0.0)
        question = str(row.get("question") or row.get("market_id") or "").strip()
        market_url = str(row.get("market_url") or "").strip()
        send_alert(
            build_trade_resolution_alert(
                mode=mode,
                won=bool(row.get("won")),
                side=side,
                pnl_usd=pnl,
                question=question,
                market_url=market_url,
                tracked_trader_name=str(row.get("trader_name") or "").strip() or None,
                tracked_trader_address=str(row.get("trader_address") or "").strip() or None,
            ),
            kind="resolution",
        )


def _resolve_trades_and_alert() -> list[dict[str, object]]:
    resolved_rows = resolve_shadow_trades()
    _send_resolution_alerts(resolved_rows)
    return resolved_rows


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


def _wait_for_next_poll(loop_started_at: float, state_snapshot: dict, on_tick=None) -> None:
    last_interval = poll_interval()

    while True:
        if on_tick is not None:
            on_tick()

        current_interval = poll_interval()
        if current_interval != last_interval:
            logger.info("Poll interval updated to %ss", current_interval)
            last_interval = current_interval
            _write_bot_state(**state_snapshot)

        remaining = loop_started_at + current_interval - time.time()
        if remaining <= 0:
            return

        time.sleep(min(remaining, 1.0))


def _run_telegram_command_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            service_telegram_commands()
        except Exception:
            logger.exception("Telegram command service crashed")
        stop_event.wait(0.5)


def _parse_manual_trade_request_payload(payload: dict) -> ManualTradeRequest:
    if not isinstance(payload, dict):
        raise ValueError("manual trade request payload must be an object")

    raw_action = str(payload.get("action") or "").strip().lower()
    action_aliases = {
        "buy": "buy_more",
        "buy_more": "buy_more",
        "cash_out": "cash_out",
        "sell": "cash_out",
        "sell_all": "cash_out",
    }
    action = action_aliases.get(raw_action)
    if not action:
        raise ValueError(f"unsupported manual trade action: {raw_action or '-'}")

    market_id = str(payload.get("market_id") or "").strip()
    token_id = str(payload.get("token_id") or "").strip()
    side = str(payload.get("side") or "").strip().lower()
    question = str(payload.get("question") or "").strip()
    trader_address = str(payload.get("trader_address") or "").strip().lower()
    request_id = str(payload.get("request_id") or "").strip()
    requested_at = int(payload.get("requested_at") or 0)
    source = str(payload.get("source") or "unknown").strip().lower() or "unknown"
    amount_raw = payload.get("amount_usd")
    amount_usd = float(amount_raw) if amount_raw is not None else None

    if not market_id:
        raise ValueError("manual trade request is missing market_id")
    if not token_id:
        raise ValueError("manual trade request is missing token_id")
    if not side:
        raise ValueError("manual trade request is missing side")
    if action == "buy_more" and (amount_usd is None or amount_usd <= 0):
        raise ValueError("manual buy request must include a positive amount_usd")

    return ManualTradeRequest(
        action=action,
        market_id=market_id,
        token_id=token_id,
        side=side,
        question=question,
        trader_address=trader_address,
        amount_usd=amount_usd,
        request_id=request_id,
        requested_at=requested_at,
        source=source,
    )


def _consume_manual_retrain_request(run_retrain_job) -> bool:
    if not MANUAL_RETRAIN_REQUEST_FILE.exists():
        return False

    try:
        payload = json.loads(MANUAL_RETRAIN_REQUEST_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request payload must be an object")
    except Exception as exc:
        logger.warning("Discarding invalid manual retrain request: %s", exc)
        try:
            MANUAL_RETRAIN_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return False

    try:
        MANUAL_RETRAIN_REQUEST_FILE.unlink()
    except FileNotFoundError:
        pass

    requested_at = int(payload.get("requested_at") or 0)
    source = str(payload.get("source") or "unknown").strip().lower() or "unknown"
    request_id = str(payload.get("request_id") or "").strip()
    now_ts = int(time.time())
    if requested_at > 0 and (now_ts - requested_at) > 900:
        logger.info(
            "Ignoring stale manual retrain request from %s (age=%ss, request_id=%s)",
            source,
            now_ts - requested_at,
            request_id or "-",
        )
        return False

    logger.info(
        "Manual retrain requested by %s (request_id=%s)",
        source,
        request_id or "-",
    )
    run_retrain_job(f"manual_{source}")
    return True


def _consume_manual_trade_request(handle_request) -> bool:
    if not MANUAL_TRADE_REQUEST_FILE.exists():
        return False

    try:
        payload = json.loads(MANUAL_TRADE_REQUEST_FILE.read_text(encoding="utf-8"))
        request = _parse_manual_trade_request_payload(payload)
    except Exception as exc:
        logger.warning("Discarding invalid manual trade request: %s", exc)
        try:
            MANUAL_TRADE_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return False

    try:
        MANUAL_TRADE_REQUEST_FILE.unlink()
    except FileNotFoundError:
        pass

    now_ts = int(time.time())
    if request.requested_at > 0 and (now_ts - request.requested_at) > 900:
        logger.info(
            "Ignoring stale manual trade request from %s (action=%s age=%ss request_id=%s)",
            request.source,
            request.action,
            now_ts - request.requested_at,
            request.request_id or "-",
        )
        return False

    logger.info(
        "Manual trade requested by %s (action=%s market=%s request_id=%s)",
        request.source,
        request.action,
        request.market_id[:12],
        request.request_id or "-",
    )
    handle_request(request)
    return True


def _manual_trade_event(
    request: ManualTradeRequest,
    *,
    trade_id: str,
    question: str,
    price: float,
    shares: float,
    size_usd: float,
    close_time: str,
    snapshot: dict | None,
    raw_market_metadata: dict | None,
    raw_orderbook: dict | None,
    metadata_fetched_at: int,
    orderbook_fetched_at: int,
) -> TradeEvent:
    now_ts = int(time.time())
    action = "buy" if request.action == "buy_more" else "sell"
    trader_address = request.trader_address or "manual-dashboard"
    return TradeEvent(
        trade_id=trade_id,
        market_id=request.market_id,
        question=question,
        side=request.side,
        action=action,
        price=price,
        shares=shares,
        size_usd=size_usd,
        token_id=request.token_id,
        trader_name="Manual Dashboard",
        trader_address=trader_address,
        timestamp=now_ts,
        close_time=close_time,
        snapshot=snapshot,
        raw_trade={
            "source": request.source,
            "request_id": request.request_id,
            "requested_at": request.requested_at,
            "manual_action": request.action,
            "amount_usd": request.amount_usd,
        },
        raw_market_metadata=raw_market_metadata or {},
        raw_orderbook=raw_orderbook,
        source_ts_raw=str(request.requested_at or now_ts),
        observed_at=now_ts,
        poll_started_at=now_ts,
        metadata_fetched_at=metadata_fetched_at,
        orderbook_fetched_at=orderbook_fetched_at,
        market_close_ts=PolymarketTracker._normalize_timestamp(close_time) if close_time else 0,
    )


def _process_manual_trade_request(
    request: ManualTradeRequest,
    *,
    tracker: PolymarketTracker,
    executor: PolymarketExecutor,
    dedup: DedupeCache,
    live_entry_guard: LiveEntryGuard | None,
    daily_loss_guard: DailyLossGuard | None,
) -> None:
    meta, metadata_fetched_at = tracker.get_market_metadata(request.market_id)
    question = request.question or str(meta.get("question") or meta.get("title") or request.market_id)
    close_time = str(meta.get("endDate") or meta.get("closedTime") or meta.get("closeTime") or "").strip()
    snapshot = dict(PolymarketTracker._metadata_snapshot(meta))
    orderbook_snapshot, raw_book, orderbook_fetched_at = tracker.get_orderbook_snapshot(request.token_id)
    if orderbook_snapshot:
        snapshot.update(orderbook_snapshot)

    manual_trade_id = f"manual-{request.action}-{request.request_id or int(time.time())}"
    trader_address = request.trader_address or "manual-dashboard"

    if request.action == "buy_more":
        account_equity = (
            executor.get_account_equity_usd()
            if live_entry_guard is not None
            else executor.get_usdc_balance()
        )
        entry_block_reason = _entry_pause_reason(
            tracker,
            executor,
            live_entry_guard,
            daily_loss_guard,
            account_equity,
        )
        amount_usd = float(request.amount_usd or 0.0)
        if entry_block_reason:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=float(snapshot.get("best_ask") or snapshot.get("mid") or 0.0),
                shares=0.0,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _pause_event(event, amount_usd, entry_block_reason)
            return

        fill_estimate, fill_reason = executor.estimate_entry_fill(raw_book, amount_usd)
        if fill_estimate is None:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=float(snapshot.get("best_ask") or snapshot.get("mid") or 0.0),
                shares=0.0,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _skip_event(event, amount_usd, _humanize_reason(fill_reason or "manual buy quote failed"), decision="MANUAL")
            return

        exposure_block_reason = executor.entry_risk_block_reason(
            market_id=request.market_id,
            trader_address=trader_address,
            proposed_size_usd=amount_usd,
            account_equity=account_equity,
        )
        if exposure_block_reason:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=fill_estimate.avg_price,
                shares=fill_estimate.shares,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _skip_event(event, amount_usd, exposure_block_reason, decision="MANUAL")
            return

        market_f = build_market_features(snapshot, close_time, amount_usd, fill_estimate.avg_price)
        if market_f is None:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=fill_estimate.avg_price,
                shares=fill_estimate.shares,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _skip_event(event, amount_usd, "manual buy could not build market features", decision="MANUAL")
            return

        event = _manual_trade_event(
            request,
            trade_id=manual_trade_id,
            question=question,
            price=fill_estimate.avg_price,
            shares=fill_estimate.shares,
            size_usd=amount_usd,
            close_time=close_time,
            snapshot=snapshot,
            raw_market_metadata=meta,
            raw_orderbook=raw_book,
            metadata_fetched_at=metadata_fetched_at,
            orderbook_fetched_at=orderbook_fetched_at,
        )
        result = executor.execute(
            trade_id=manual_trade_id,
            market_id=request.market_id,
            token_id=request.token_id,
            side=request.side,
            dollar_size=amount_usd,
            kelly_f=0.0,
            confidence=0.0,
            signal={
                "mode": "manual",
                "manual": True,
                "source": request.source,
                "trader": {"score": None},
                "market": {"score": None},
            },
            event=event,
            trader_f=None,
            market_f=market_f,
            dedup=dedup,
        )
        if use_real_money():
            dedup.sync_positions_from_api(tracker, wallet_address())
        else:
            dedup.load_from_db(rebuild_shadow_positions=True)
        if result.placed:
            execution_price = (result.dollar_size / result.shares) if result.shares > 0 else event.price
            _emit_event(
                {
                    "type": "signal",
                    "trade_id": manual_trade_id,
                    "market_id": request.market_id,
                    "question": question,
                    "market_url": market_url_from_metadata(meta),
                    "side": request.side,
                    "action": "buy",
                    "price": round(execution_price, 6),
                    "shares": round(result.shares, 6),
                    "amount_usd": result.dollar_size,
                    "size_usd": result.dollar_size,
                    "username": "Manual Dashboard",
                    "trader": trader_address,
                    "decision": "MANUAL BUY",
                    "confidence": None,
                    "signal_mode": "manual",
                    "shadow": result.shadow,
                    "order_id": result.order_id,
                    "reason": "operator requested a manual buy from the dashboard",
                    "ts": int(time.time()),
                }
            )
        else:
            _skip_event(event, amount_usd, _humanize_reason(result.reason), decision="MANUAL")
        return

    position = dedup.get_position(request.market_id, request.token_id, request.side)
    position_size_usd = float((position or {}).get("size") or 0.0)
    price = float(snapshot.get("best_bid") or snapshot.get("mid") or 0.0)
    event = _manual_trade_event(
        request,
        trade_id=manual_trade_id,
        question=question,
        price=price,
        shares=0.0,
        size_usd=position_size_usd,
        close_time=close_time,
        snapshot=snapshot,
        raw_market_metadata=meta,
        raw_orderbook=raw_book,
        metadata_fetched_at=metadata_fetched_at,
        orderbook_fetched_at=orderbook_fetched_at,
    )
    result = executor.execute_exit(
        trade_id=manual_trade_id,
        market_id=request.market_id,
        token_id=request.token_id,
        side=request.side,
        event=event,
        dedup=dedup,
    )
    if use_real_money():
        dedup.sync_positions_from_api(tracker, wallet_address())
    if result.placed:
        execution_price = (result.dollar_size / result.shares) if result.shares > 0 else event.price
        _emit_event(
            {
                "type": "signal",
                "trade_id": manual_trade_id,
                "market_id": request.market_id,
                "question": question,
                "market_url": market_url_from_metadata(meta),
                "side": request.side,
                "action": "sell",
                "price": round(execution_price, 6),
                "shares": round(result.shares, 6),
                "amount_usd": result.dollar_size,
                "size_usd": result.dollar_size,
                "username": "Manual Dashboard",
                "trader": trader_address,
                "decision": "MANUAL EXIT",
                "confidence": None,
                "shadow": result.shadow,
                "order_id": result.order_id,
                "reason": "operator requested a manual cash out from the dashboard",
                "ts": int(time.time()),
            }
        )
        return

    _skip_event(event, position_size_usd, _humanize_reason(result.reason), decision="MANUAL")


def _log_runtime_ready(
    tracker: PolymarketTracker,
    watchlist: WatchlistManager,
) -> None:
    tier_state = watchlist.state_fields()
    logger.info(
        "Startup complete. Polling %s wallets every %ss "
        "(tracked=%s, dropped=%s, hot/warm/discovery=%s/%s/%s)",
        len(tracker.wallets),
        poll_interval(),
        tier_state["tracked_wallet_count"],
        tier_state["dropped_wallet_count"],
        tier_state["hot_wallet_count"],
        tier_state["warm_wallet_count"],
        tier_state["discovery_wallet_count"],
    )
    logger.info(
        "Runtime files: db=%s state=%s events=%s",
        DB_PATH,
        BOT_STATE_FILE,
        EVENT_FILE,
    )
    logger.info(
        "Console output stays quiet between events. Use %s or the dashboard to confirm liveness.",
        BOT_STATE_FILE,
    )


def _log_first_poll_summary(
    *,
    elapsed: float,
    polled_wallet_count: int,
    event_count: int,
    bankroll: float,
) -> None:
    logger.info(
        "First poll completed in %.2fs: wallets=%s events=%s bankroll=$%.2f",
        elapsed,
        polled_wallet_count,
        event_count,
        bankroll,
    )


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
    wallet_quality_score = signal.get("trader", {}).get("score")
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

    max_wallet_size_usd = round(bankroll * max_bet_fraction(), 2)
    sizing = apply_wallet_trust_sizing(
        preview_sizing,
        trust_state,
        quality_score=wallet_quality_score,
        max_size_usd=max_wallet_size_usd,
    )
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
        next_sizing = apply_wallet_trust_sizing(
            next_sizing,
            trust_state,
            quality_score=wallet_quality_score,
            max_size_usd=max_wallet_size_usd,
        )
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
                "wallet_quality_score": sizing.get("wallet_quality_score"),
                "wallet_quality_multiplier": sizing.get("wallet_quality_multiplier"),
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
    quality_min_multiplier = _capture_config(wallet_quality_size_min_multiplier)
    quality_max_multiplier = _capture_config(wallet_quality_size_max_multiplier)
    probation_multiplier = _capture_config(wallet_probation_size_multiplier)
    trusted_min_resolved = _capture_config(wallet_trusted_min_resolved_copied_buys)
    min_window_seconds = _capture_config(min_execution_window_seconds)
    max_horizon_seconds = _capture_config(max_market_horizon_seconds)
    _capture_config(max_source_trade_age_seconds)
    _capture_config(retrain_min_samples)

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
        quality_min_multiplier is not None
        and quality_max_multiplier is not None
        and quality_min_multiplier > quality_max_multiplier
    ):
        errors.append(
            "WALLET_QUALITY_SIZE_MIN_MULTIPLIER must be <= WALLET_QUALITY_SIZE_MAX_MULTIPLIER"
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
            send_alert(build_lines("startup validation failed", build_bullets(errors)), kind="error")
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

    daily_loss_guard.loss_limit_pct = max_daily_loss_pct()
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
    _repair_event_file_market_urls()
    start_ts = int(time.time())
    watchlist = WatchlistManager(WATCHED_WALLETS)
    bot_state_snapshot: dict[str, object] = {
        "started_at": start_ts,
        "last_loop_started_at": 0,
        "last_activity_at": start_ts,
        "loop_in_progress": False,
        "startup_detail": "starting bot",
        "last_poll_at": 0,
        "last_poll_duration_s": 0.0,
        "bankroll_usd": None,
        "last_event_count": 0,
        "polled_wallet_count": 0,
        "retrain_in_progress": False,
        "retrain_started_at": 0,
        "last_retrain_started_at": 0,
        "last_retrain_finished_at": 0,
        "last_retrain_status": "",
        "last_retrain_message": "",
        "last_retrain_sample_count": 0,
        "last_retrain_min_samples": 0,
        "last_retrain_trigger": "",
        "last_retrain_deployed": False,
    }
    last_activity_write_at = 0.0
    current_loop_started_at = 0
    bot_state_lock = threading.Lock()
    retrain_lock = threading.Lock()

    def _persist_bot_state(**updates: object) -> None:
        with bot_state_lock:
            bot_state_snapshot.update(updates)
            _write_bot_state(**bot_state_snapshot)

    def _set_startup_detail(detail: str) -> None:
        _persist_bot_state(startup_detail=str(detail or "").strip())
        _heartbeat(force=True)

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

    def _run_retrain_job(trigger: str) -> bool:
        if not retrain_lock.acquire(blocking=False):
            message = f"Retrain request ignored: already running ({trigger})"
            logger.info(message)
            _persist_bot_state(
                last_retrain_status="already_running",
                last_retrain_message=message,
                last_retrain_trigger=trigger,
            )
            return False

        started_at = int(time.time())
        _persist_bot_state(
            retrain_in_progress=True,
            retrain_started_at=started_at,
            last_retrain_started_at=started_at,
            last_retrain_status="running",
            last_retrain_message=f"Retrain running ({trigger})",
            last_retrain_trigger=trigger,
        )
        _heartbeat(force=True)
        try:
            report = retrain_cycle_report(engine, trigger=trigger, started_at=started_at)
            finished_at = int(time.time())
            _persist_bot_state(
                retrain_in_progress=False,
                retrain_started_at=0,
                last_retrain_finished_at=finished_at,
                last_retrain_status=str(report.get("status") or ""),
                last_retrain_message=str(report.get("message") or ""),
                last_retrain_sample_count=int(report.get("sample_count") or 0),
                last_retrain_min_samples=int(report.get("min_samples") or 0),
                last_retrain_trigger=trigger,
                last_retrain_deployed=bool(report.get("deployed")),
            )
            return bool(report.get("ok"))
        except Exception as exc:
            finished_at = int(time.time())
            message = f"Retrain failed: {exc}"
            logger.exception(message)
            _persist_bot_state(
                retrain_in_progress=False,
                retrain_started_at=0,
                last_retrain_finished_at=finished_at,
                last_retrain_status="failed",
                last_retrain_message=message,
                last_retrain_trigger=trigger,
                last_retrain_deployed=False,
            )
            raise
        finally:
            _heartbeat(force=True)
            retrain_lock.release()

    def _service_runtime_requests() -> None:
        _consume_manual_retrain_request(_run_retrain_job)
        _consume_manual_trade_request(
            lambda request: _process_manual_trade_request(
                request,
                tracker=tracker,
                executor=executor,
                dedup=dedup,
                live_entry_guard=live_entry_guard,
                daily_loss_guard=daily_loss_guard,
            )
        )

    _set_startup_detail("loading watchlist")
    _persist_bot_state(**watchlist.state_fields())
    _set_startup_detail("syncing belief priors")
    sync_belief_priors()

    _set_startup_detail("creating tracker")
    tracker = PolymarketTracker(WATCHED_WALLETS, activity_callback=_heartbeat)
    _set_startup_detail("connecting executor")
    executor = PolymarketExecutor()
    _set_startup_detail("checking wallet balance")
    executor.validate_live_wallet_ready(min_required_balance_usd=min_bet_usd())
    _persist_bot_state(bankroll_usd=round(executor.get_usdc_balance(), 2))
    _set_startup_detail("starting telegram replies")
    telegram_command_stop = threading.Event()
    telegram_command_thread = threading.Thread(
        target=_run_telegram_command_loop,
        args=(telegram_command_stop,),
        name="telegram-command-loop",
        daemon=True,
    )
    telegram_command_thread.start()
    startup_wallets = watchlist.startup_wallets()
    _set_startup_detail(f"priming {len(startup_wallets)} identities")
    tracker.prime_identities(startup_wallets)
    _set_startup_detail("initializing risk guards")
    live_entry_guard = _init_live_entry_guard(executor)
    daily_loss_guard = _init_daily_loss_guard(executor)
    _set_startup_detail("loading signal engine")
    engine = SignalEngine()
    _set_startup_detail("loading trade cache")
    dedup = DedupeCache()
    dedup.load_from_db(rebuild_shadow_positions=True)
    tracker.seen_ids.update(dedup.seen_ids)
    _set_startup_detail(
        f"loaded {len(dedup.seen_ids)} seen ids, {len(dedup.open_positions)} open positions"
    )
    _set_startup_detail("syncing live positions" if use_real_money() else "rebuilding shadow positions")
    initial_live_sync_ok = dedup.sync_positions_from_api(tracker, wallet_address())
    if use_real_money() and not initial_live_sync_ok:
        raise RuntimeError("Initial live positions sync failed; refusing to start without a confirmed view of open positions")
    _set_startup_detail(f"refreshing {len(startup_wallets)} trader profiles")
    refresh_trader_cache(startup_wallets)
    _set_startup_detail("refreshing watchlist")
    watchlist.refresh(run_auto_drop=True)
    _persist_bot_state(**watchlist.state_fields())
    _set_startup_detail("resolving historical trades")
    _resolve_trades_and_alert()
    _set_startup_detail("refreshing trade cache")
    dedup.load_from_db(rebuild_shadow_positions=False)
    tracker.seen_ids.update(dedup.seen_ids)

    def _refresh_watchlist() -> None:
        refresh_trader_cache(watchlist.active_wallets())
        watchlist.refresh(run_auto_drop=True)
        _persist_bot_state(**watchlist.state_fields())

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: (_resolve_trades_and_alert(), dedup.load_from_db(rebuild_shadow_positions=False)),
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
            lambda: _run_retrain_job("scheduled"),
            "cron",
            day_of_week="mon",
            **retrain_trigger,
        )
    else:
        scheduler.add_job(
            lambda: _run_retrain_job("scheduled"),
            "cron",
            **retrain_trigger,
        )
    scheduler.add_job(
        lambda: should_retrain_early(engine) and _run_retrain_job("early"),
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
    if should_retrain_early(engine):
        _set_startup_detail("running startup retrain")
        _run_retrain_job("startup")
    _set_startup_detail("starting scheduler")
    scheduler.start()
    _log_runtime_ready(tracker, watchlist)
    _persist_bot_state(startup_detail="waiting for first poll")

    mode_str = "LIVE" if use_real_money() else "SHADOW"
    tier_state = watchlist.state_fields()
    send_alert(
        build_lines(
            f"bot started in {mode_str.lower()} mode",
            f"watching {len(tracker.wallets)} wallets",
            (
                "tracked/dropped: "
                f"{tier_state['tracked_wallet_count']}/{tier_state['dropped_wallet_count']}"
            ),
            (
                "hot/warm/discovery: "
                f"{tier_state['hot_wallet_count']}/{tier_state['warm_wallet_count']}/{tier_state['discovery_wallet_count']}"
            ),
            f"poll interval: {poll_interval()}s",
        ),
        kind="status",
    )

    try:
        last_entry_pause_reason: str | None = None
        first_poll_logged = False
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
                    watchlist.refresh(run_auto_drop=False)
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
                                    build_lines(
                                        "entries paused",
                                        entry_block_reason,
                                        "new entries are paused until the condition clears",
                                    ),
                                    kind="warning",
                                )
                            elif last_entry_pause_reason:
                                logger.info("Entry pause cleared: %s", last_entry_pause_reason)
                                send_alert(
                                    build_lines(
                                        "entries resumed",
                                        "the pause condition cleared and new entries are enabled again",
                                    ),
                                    kind="status",
                                )
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
                            send_alert(
                                build_market_error_alert(
                                    "event processing failed",
                                    question=event.question,
                                    market_url=_market_url_for_event(event),
                                    detail=f"trade {event.trade_id[:12]} failed: {exc}",
                                    tracked_trader_name=getattr(event, "trader_name", None),
                                    tracked_trader_address=getattr(event, "trader_address", None),
                                ),
                                kind="error",
                            )
                        finally:
                            if event.trade_id in dedup.seen_ids:
                                tracker.seen_ids.add(event.trade_id)
            except Exception as exc:
                logger.error("Main loop error: %s", exc, exc_info=True)
                send_alert(build_lines("bot loop error", str(exc)), kind="error")

            elapsed = time.time() - loop_start
            state_snapshot = {
                "started_at": start_ts,
                "last_loop_started_at": current_loop_started_at,
                "last_activity_at": int(time.time()),
                "startup_detail": "",
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
            _service_runtime_requests()
            if not first_poll_logged:
                _log_first_poll_summary(
                    elapsed=elapsed,
                    polled_wallet_count=polled_wallet_count,
                    event_count=event_count,
                    bankroll=bankroll,
                )
                first_poll_logged = True
            _wait_for_next_poll(loop_start, state_snapshot, _service_runtime_requests)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        send_alert("bot stopped", kind="status")
    finally:
        telegram_command_stop.set()
        telegram_command_thread.join(timeout=1.0)
        scheduler.shutdown(wait=False)
        tracker.close()


if __name__ == "__main__":
    main()
