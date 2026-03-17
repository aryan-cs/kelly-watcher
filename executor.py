from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from alerter import send_alert
from config import shadow_bankroll_usd, use_real_money, wallet_address
from db import get_conn

logger = logging.getLogger(__name__)
DATA_API = "https://data-api.polymarket.com"
LIVE_SYNC_ATTEMPTS = 4
LIVE_SYNC_DELAY_S = 0.35
USDC_DECIMALS = 1_000_000.0


@dataclass
class ExecutionResult:
    placed: bool
    shadow: bool
    order_id: Optional[str]
    dollar_size: float
    reason: str
    shares: float = 0.0
    pnl_usd: float | None = None
    action: str = "entry"


@dataclass
class SimulatedFill:
    spent_usd: float
    shares: float
    avg_price: float


@dataclass
class LiveWalletStatus:
    balance_usd: float
    max_allowance_usd: float
    signer_address: str


def _to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


class PolymarketExecutor:
    def __init__(self):
        self._clob = None
        self._init_clob()

    def _init_clob(self) -> None:
        if not use_real_money():
            logger.info("Shadow mode active - CLOB client not initialized")
            return

        try:
            from py_clob_client.client import ClobClient
            from config import private_key, wallet_address

            self._clob = ClobClient(
                "https://clob.polymarket.com",
                key=private_key(),
                chain_id=137,
                signature_type=0,
                funder=wallet_address(),
            )
            self._clob.set_api_creds(self._clob.create_or_derive_api_creds())
            logger.info("CLOB client initialized for live trading")
        except Exception as exc:
            logger.error("CLOB client init failed: %s", exc)
            raise

    def get_usdc_balance(self) -> float:
        if not use_real_money():
            conn = get_conn()
            row = conn.execute(
                """
                SELECT
                    SUM(
                        CASE
                            WHEN outcome IS NULL AND exited_at IS NULL THEN COALESCE(actual_entry_size_usd, signal_size_usd)
                            ELSE 0
                        END
                    ) AS spent,
                    SUM(COALESCE(shadow_pnl_usd, 0)) AS realized_pnl
                FROM trade_log
                WHERE real_money=0 AND skipped=0
                """
            ).fetchone()
            conn.close()
            spent = float(row["spent"] or 0.0)
            realized_pnl = float(row["realized_pnl"] or 0.0)
            return max(shadow_bankroll_usd() + realized_pnl - spent, 0.0)

        try:
            return self._get_live_wallet_status().balance_usd
        except Exception as exc:
            logger.error("Balance fetch failed: %s", exc)
            return 0.0

    @staticmethod
    def _parse_usdc_base_units(raw_value: Any) -> float:
        text = str(raw_value or "").strip()
        if not text:
            return 0.0
        try:
            amount = float(text)
        except (TypeError, ValueError):
            return 0.0
        if "." in text or "e" in text.lower():
            return max(amount, 0.0)
        return max(amount / USDC_DECIMALS, 0.0)

    def _get_live_wallet_status(self) -> LiveWalletStatus:
        if self._clob is None:
            raise RuntimeError("live trading requested, but the CLOB client was not initialized")

        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        payload = self._clob.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        if not isinstance(payload, dict):
            raise RuntimeError("live balance check returned an unexpected response payload")

        allowances = payload.get("allowances")
        allowance_values = allowances.values() if isinstance(allowances, dict) else []
        max_allowance_usd = max(
            (self._parse_usdc_base_units(value) for value in allowance_values),
            default=0.0,
        )
        signer_address = str(self._clob.get_address() or "").strip().lower()
        return LiveWalletStatus(
            balance_usd=self._parse_usdc_base_units(payload.get("balance")),
            max_allowance_usd=max_allowance_usd,
            signer_address=signer_address,
        )

    def validate_live_wallet_ready(self, *, min_required_balance_usd: float) -> LiveWalletStatus | None:
        if not use_real_money():
            return None

        status = self._get_live_wallet_status()
        configured_wallet = wallet_address()
        issues: list[str] = []
        if configured_wallet and status.signer_address and status.signer_address != configured_wallet:
            issues.append(
                "POLYGON_PRIVATE_KEY does not match POLYGON_WALLET_ADDRESS for signature_type=0 live trading"
            )
        if status.max_allowance_usd <= 0.0:
            issues.append(
                "live trading wallet has no collateral allowance set; run `uv run python polymarket_setup.py` first"
            )
        if status.balance_usd + 1e-9 < max(min_required_balance_usd, 0.0):
            issues.append(
                f"live trading wallet balance was ${status.balance_usd:.2f}, below the required ${min_required_balance_usd:.2f}"
            )
        if issues:
            raise RuntimeError("; ".join(issues))

        logger.info(
            "Live wallet ready: signer=%s balance=$%.2f max_allowance=$%.2f",
            status.signer_address or "unknown",
            status.balance_usd,
            status.max_allowance_usd,
        )
        return status

    @staticmethod
    def _book_levels(raw_book: dict[str, Any] | None, side: str) -> list[tuple[float, float]]:
        if not isinstance(raw_book, dict):
            return []

        levels = raw_book.get(side, [])
        normalized: list[tuple[float, float]] = []
        for level in levels if isinstance(levels, list) else []:
            if not isinstance(level, dict):
                continue
            price = _to_float(level.get("price"))
            size = _to_float(level.get("size"))
            if price <= 0 or size <= 0:
                continue
            normalized.append((price, size))

        reverse = side == "bids"
        normalized.sort(key=lambda item: item[0], reverse=reverse)
        return normalized

    @classmethod
    def _simulate_shadow_buy(
        cls,
        raw_book: dict[str, Any] | None,
        dollar_size: float,
    ) -> tuple[SimulatedFill | None, str | None]:
        if dollar_size <= 0:
            return None, "shadow simulation rejected the buy because the requested size was $0.00"

        asks = cls._book_levels(raw_book, "asks")
        if not asks:
            return None, "shadow simulation rejected the buy because the order book had no asks for a full fill"

        remaining_usd = float(dollar_size)
        filled_shares = 0.0
        spent_usd = 0.0
        for price, available_shares in asks:
            if remaining_usd <= 1e-9:
                break
            take_shares = min(available_shares, remaining_usd / price)
            if take_shares <= 0:
                continue
            cost = take_shares * price
            filled_shares += take_shares
            spent_usd += cost
            remaining_usd -= cost

        if remaining_usd > 0.01 or filled_shares <= 0:
            return None, "shadow simulation rejected the buy because there was not enough ask depth to fill the whole order"

        spent_usd = round(float(dollar_size), 6)
        avg_price = spent_usd / filled_shares if filled_shares > 0 else 0.0
        return SimulatedFill(spent_usd=spent_usd, shares=filled_shares, avg_price=avg_price), None

    @classmethod
    def _simulate_shadow_sell(
        cls,
        raw_book: dict[str, Any] | None,
        shares: float,
    ) -> tuple[SimulatedFill | None, str | None]:
        if shares <= 0:
            return None, "shadow simulation rejected the sell because the requested share size was 0.000"

        bids = cls._book_levels(raw_book, "bids")
        if not bids:
            return None, "shadow simulation rejected the sell because the order book had no bids for a full fill"

        remaining_shares = float(shares)
        exit_notional = 0.0
        for price, available_shares in bids:
            if remaining_shares <= 1e-9:
                break
            take_shares = min(available_shares, remaining_shares)
            if take_shares <= 0:
                continue
            exit_notional += take_shares * price
            remaining_shares -= take_shares

        if remaining_shares > 1e-6:
            return None, "shadow simulation rejected the sell because there was not enough bid depth to fill the whole order"

        exit_notional = round(exit_notional, 6)
        avg_price = exit_notional / shares if shares > 0 else 0.0
        return SimulatedFill(spent_usd=exit_notional, shares=shares, avg_price=avg_price), None

    def _fetch_live_positions(self) -> list[dict[str, Any]] | None:
        if not use_real_money():
            return []

        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                response = client.get(
                    f"{DATA_API}/positions",
                    params={"user": wallet_address()},
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload if isinstance(payload, list) else payload.get("positions", [])
                return [row for row in rows if isinstance(row, dict)]
        except Exception as exc:
            logger.warning("Live position sync failed: %s", exc)
            return None

    @staticmethod
    def _match_live_position(
        rows: list[dict[str, Any]],
        market_id: str,
        token_id: str,
        side: str,
    ) -> dict[str, Any] | None:
        normalized_token = str(token_id or "").strip()
        normalized_side = str(side or "").strip().lower()
        normalized_market = str(market_id or "").strip().lower()

        for row in rows:
            if normalized_token and str(row.get("asset") or row.get("asset_id") or row.get("tokenId") or "").strip() == normalized_token:
                return row
        for row in rows:
            row_market = str(row.get("conditionId") or row.get("market_id") or "").strip().lower()
            row_side = str(row.get("outcome") or row.get("title") or "").strip().lower()
            if row_market == normalized_market and row_side == normalized_side:
                return row
        return None

    @staticmethod
    def _live_position_shares(row: dict[str, Any] | None) -> float:
        if not row:
            return 0.0
        shares = _to_float(row.get("size"))
        if shares > 0:
            return shares
        total_bought = max(_to_float(row.get("totalBought")), 0.0)
        avg_price = _to_float(row.get("avgPrice") or row.get("averagePrice"))
        return (total_bought / avg_price) if total_bought > 0 and avg_price > 0 else 0.0

    @staticmethod
    def _live_position_cost(row: dict[str, Any] | None) -> float:
        if not row:
            return 0.0
        total_bought = max(_to_float(row.get("totalBought")), 0.0)
        if total_bought > 0:
            return total_bought
        initial_value = max(_to_float(row.get("initialValue")), 0.0)
        if initial_value > 0:
            return initial_value
        shares = PolymarketExecutor._live_position_shares(row)
        avg_price = _to_float(row.get("avgPrice") or row.get("averagePrice"))
        return shares * avg_price if shares > 0 and avg_price > 0 else 0.0

    def _sync_live_positions(
        self,
        dedup,
        *,
        market_id: str,
        token_id: str,
        side: str,
        expect_present: bool,
    ) -> dict[str, Any] | None:
        last_match = None
        for attempt in range(LIVE_SYNC_ATTEMPTS):
            rows = self._fetch_live_positions()
            if rows is not None:
                dedup.sync_positions_from_rows(rows)
            last_match = self._match_live_position(rows or [], market_id, token_id, side)
            if expect_present == bool(last_match):
                return last_match
            if attempt < LIVE_SYNC_ATTEMPTS - 1:
                time.sleep(LIVE_SYNC_DELAY_S * (attempt + 1))
        return last_match

    def _measure_live_balance_change(
        self,
        before_balance: float,
        *,
        expect_increase: bool,
    ) -> tuple[float, float]:
        best_balance = before_balance
        for attempt in range(LIVE_SYNC_ATTEMPTS):
            candidate = self.get_usdc_balance()
            if expect_increase:
                best_balance = max(best_balance, candidate)
                delta = best_balance - before_balance
            else:
                best_balance = min(best_balance, candidate)
                delta = before_balance - best_balance
            if delta > 0.0:
                return best_balance, round(delta, 6)
            if attempt < LIVE_SYNC_ATTEMPTS - 1:
                time.sleep(LIVE_SYNC_DELAY_S * (attempt + 1))
        return best_balance, 0.0

    def estimate_entry_fill(
        self,
        raw_book: dict[str, Any] | None,
        dollar_size: float,
    ) -> tuple[SimulatedFill | None, str | None]:
        return self._simulate_shadow_buy(raw_book, dollar_size)

    def estimate_exit_fill(
        self,
        raw_book: dict[str, Any] | None,
        shares: float,
    ) -> tuple[SimulatedFill | None, str | None]:
        return self._simulate_shadow_sell(raw_book, shares)

    def execute(
        self,
        trade_id: str,
        market_id: str,
        token_id: str,
        side: str,
        dollar_size: float,
        kelly_f: float,
        confidence: float,
        signal: dict,
        event,
        trader_f,
        market_f,
        dedup,
    ) -> ExecutionResult:
        shadow = not use_real_money()
        dedup.mark_pending(market_id, token_id, side)

        if shadow:
            return self._execute_shadow(
                trade_id,
                market_id,
                token_id,
                side,
                dollar_size,
                kelly_f,
                confidence,
                signal,
                event,
                trader_f,
                market_f,
                dedup,
            )

        return self._execute_live(
            trade_id,
            market_id,
            token_id,
            side,
            dollar_size,
            kelly_f,
            confidence,
            signal,
            event,
            trader_f,
            market_f,
            dedup,
        )

    def _execute_shadow(
        self,
        trade_id,
        market_id,
        token_id,
        side,
        dollar_size,
        kelly_f,
        confidence,
        _signal,
        event,
        trader_f,
        market_f,
        dedup,
    ) -> ExecutionResult:
        fill, reject_reason = self._simulate_shadow_buy(getattr(event, "raw_orderbook", None), dollar_size)
        if fill is None:
            dedup.release(market_id, token_id, side)
            return ExecutionResult(False, True, None, 0.0, reject_reason or "shadow simulation buy failed")

        log_trade(
            trade_id=trade_id,
            market_id=market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=side,
            price=event.price,
            signal_size_usd=dollar_size,
            confidence=confidence,
            kelly_f=kelly_f,
            real_money=False,
            order_id=None,
            skipped=False,
            skip_reason=None,
            actual_entry_price=fill.avg_price,
            actual_entry_shares=fill.shares,
            actual_entry_size_usd=fill.spent_usd,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=_signal,
        )
        dedup.confirm(market_id, side, fill.spent_usd, token_id, fill.avg_price, real_money=False)
        dedup.mark_seen(trade_id, market_id, event.trader_address)
        logger.info(
            "[SHADOW] %s | %s | $%.2f | %.3f sh @ %.3f | conf=%.3f",
            event.question[:60],
            side.upper(),
            fill.spent_usd,
            fill.shares,
            fill.avg_price,
            confidence,
        )
        send_alert(
            f"[SHADOW] {side.upper()} ${fill.spent_usd:.2f}\n"
            f"{event.question[:80]}\n"
            f"conf={confidence:.3f} | fill={fill.shares:.3f} @ {fill.avg_price:.3f} | kelly_f={kelly_f:.4f}"
        )
        return ExecutionResult(True, True, None, fill.spent_usd, "ok", shares=fill.shares, action="entry")

    def _execute_live(
        self,
        trade_id,
        market_id,
        token_id,
        side,
        dollar_size,
        kelly_f,
        confidence,
        _signal,
        event,
        trader_f,
        market_f,
        dedup,
    ) -> ExecutionResult:
        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            balance_before = self.get_usdc_balance()
            # token_id already identifies the YES or NO outcome token we want.
            order = MarketOrderArgs(
                token_id=token_id,
                amount=dollar_size,
                side=BUY,
            )
            signed = self._clob.create_market_order(order)
            response = self._clob.post_order(signed, OrderType.FOK)
            order_id = response.get("orderID") or response.get("id", "unknown")
            expected_fill, _ = self.estimate_entry_fill(getattr(event, "raw_orderbook", None), dollar_size)
            expected_price = (
                (expected_fill.avg_price if expected_fill is not None else 0.0)
                or _to_float(getattr(market_f, "execution_price", 0.0))
                or event.price
            )
            expected_shares = (
                (expected_fill.shares if expected_fill is not None else 0.0)
                or ((dollar_size / expected_price) if expected_price > 0 else 0.0)
            )
            expected_spend = (
                (expected_fill.spent_usd if expected_fill is not None else 0.0)
                or dollar_size
            )
            _, balance_spent = self._measure_live_balance_change(balance_before, expect_increase=False)
            live_position = self._sync_live_positions(
                dedup,
                market_id=market_id,
                token_id=token_id,
                side=side,
                expect_present=True,
            )
            actual_shares = self._live_position_shares(live_position) or expected_shares
            actual_spend = balance_spent or self._live_position_cost(live_position) or expected_spend
            avg_price_from_position = _to_float((live_position or {}).get("avgPrice") or (live_position or {}).get("averagePrice"))
            if actual_shares <= 0 and actual_spend > 0 and (avg_price_from_position or expected_price) > 0:
                actual_shares = actual_spend / (avg_price_from_position or expected_price)
            if actual_spend <= 0 and actual_shares > 0 and (avg_price_from_position or expected_price) > 0:
                actual_spend = actual_shares * (avg_price_from_position or expected_price)
            actual_price = (
                (actual_spend / actual_shares)
                if actual_spend > 0 and actual_shares > 0
                else avg_price_from_position or expected_price
            )

            log_trade(
                trade_id=trade_id,
                market_id=market_id,
                question=event.question,
                trader_address=event.trader_address,
                side=side,
                price=event.price,
                signal_size_usd=dollar_size,
                confidence=confidence,
                kelly_f=kelly_f,
                real_money=True,
                order_id=order_id,
                skipped=False,
                skip_reason=None,
                actual_entry_price=actual_price,
                actual_entry_shares=actual_shares,
                actual_entry_size_usd=actual_spend,
                trader_f=trader_f,
                market_f=market_f,
                event=event,
                signal=_signal,
            )
            dedup.confirm(
                market_id,
                side,
                actual_spend,
                token_id,
                actual_price,
                real_money=True,
            )
            dedup.mark_seen(trade_id, market_id, event.trader_address)
            logger.info(
                "[LIVE] %s | %s | $%.2f | %.3f sh @ %.3f | conf=%.3f | order=%s",
                event.question[:60],
                side.upper(),
                actual_spend,
                actual_shares,
                actual_price,
                confidence,
                order_id,
            )
            send_alert(
                f"[LIVE] {side.upper()} ${actual_spend:.2f}\n"
                f"{event.question[:80]}\n"
                f"conf={confidence:.3f} | fill={actual_shares:.3f} @ {actual_price:.3f} | order={order_id}"
            )
            return ExecutionResult(True, False, order_id, actual_spend, "ok", shares=actual_shares, action="entry")
        except Exception as exc:
            dedup.release(market_id, token_id, side)
            logger.error("[LIVE ERROR] %s: %s", market_id, exc)
            send_alert(f"[LIVE ERROR]\n{event.question[:80]}\n{exc}")
            return ExecutionResult(False, False, None, 0.0, str(exc))

    def execute_exit(
        self,
        trade_id: str,
        market_id: str,
        token_id: str,
        side: str,
        event,
        dedup,
    ) -> ExecutionResult:
        shadow = not use_real_money()
        if not shadow:
            self._sync_live_positions(
                dedup,
                market_id=market_id,
                token_id=token_id,
                side=side,
                expect_present=True,
            )
        position_state = self._load_open_position_state(market_id, side, token_id, real_money=not shadow)
        if position_state is None:
            return ExecutionResult(
                False,
                shadow,
                None,
                0.0,
                "watched trader exited, but we had no matching position open to close",
                action="exit",
            )

        position, entries = position_state
        exit_price = float(event.price or 0.0)
        if exit_price <= 0:
            return ExecutionResult(
                False,
                shadow,
                None,
                0.0,
                f"exit price looked invalid ({exit_price})",
                action="exit",
            )

        shares, exit_notional, pnl = self._exit_trade_math(position, entries, exit_price)
        if shares <= 0 or exit_notional <= 0:
            return ExecutionResult(
                False,
                shadow,
                None,
                0.0,
                "matching position was found, but its size could not be computed for exit",
                action="exit",
            )

        dedup.mark_pending(
            market_id,
            str(position.get("token_id") or token_id or ""),
            str(position.get("side") or side or ""),
        )
        if shadow:
            return self._execute_shadow_exit(
                trade_id=trade_id,
                market_id=market_id,
                token_id=token_id,
                event=event,
                dedup=dedup,
                position=position,
                entries=entries,
                exit_price=exit_price,
                shares=shares,
                exit_notional=exit_notional,
                pnl=pnl,
            )

        return self._execute_live_exit(
            trade_id=trade_id,
            market_id=market_id,
            token_id=token_id,
            event=event,
            dedup=dedup,
            position=position,
            entries=entries,
            exit_price=exit_price,
            shares=shares,
            exit_notional=exit_notional,
            pnl=pnl,
        )

    def _load_open_position_state(
        self,
        market_id: str,
        side: str,
        token_id: str,
        *,
        real_money: bool,
    ) -> tuple[dict, list[dict]] | None:
        conn = get_conn()
        try:
            positions = conn.execute(
                """
                SELECT market_id, side, size_usd, avg_price, token_id, entered_at, real_money
                FROM positions
                WHERE market_id=? AND real_money=?
                """,
                (market_id, 1 if real_money else 0),
            ).fetchall()
            if not positions:
                return None

            requested_side = str(side or "").strip().lower()
            requested_token = str(token_id or "").strip()
            position = next(
                (
                    row
                    for row in positions
                    if requested_token and str(row["token_id"] or "").strip() == requested_token
                ),
                None,
            )
            if position is None:
                position = next(
                    (
                        row
                        for row in positions
                        if requested_side and str(row["side"] or "").strip().lower() == requested_side
                    ),
                    None,
                )
            if position is None:
                return None

            position_side = str(position["side"] or "").strip().lower()
            position_token = str(position["token_id"] or "").strip()

            candidates = conn.execute(
                """
                SELECT id, trade_id, market_id, question, trader_address, side, token_id,
                       price_at_signal, signal_size_usd, actual_entry_price,
                       actual_entry_shares, actual_entry_size_usd, source_shares, confidence,
                       kelly_fraction, market_close_ts
                FROM trade_log
                WHERE market_id=?
                  AND real_money=?
                  AND skipped=0
                  AND outcome IS NULL
                  AND exited_at IS NULL
                  AND COALESCE(source_action, 'buy')='buy'
                ORDER BY placed_at DESC, id DESC
                """,
                (market_id, 1 if real_money else 0),
            ).fetchall()
            entries = [
                dict(row)
                for row in candidates
                if (
                    position_token
                    and str(row["token_id"] or "").strip() == position_token
                )
                or str(row["side"] or "").strip().lower() == position_side
            ]
            if not entries:
                return None

            return dict(position), entries
        finally:
            conn.close()

    @staticmethod
    def _exit_trade_math(position: dict, entries: list[dict], exit_price: float) -> tuple[float, float, float]:
        size_usd = float(position.get("size_usd") or 0.0)
        if size_usd <= 0:
            size_usd = sum(
                float(entry.get("actual_entry_size_usd") or 0.0)
                or float(entry.get("signal_size_usd") or 0.0)
                for entry in entries
            )

        entry_price = float(position.get("avg_price") or 0.0)
        shares = 0.0
        if entry_price > 0 and size_usd > 0:
            shares = size_usd / entry_price
        if shares <= 0:
            shares = sum(
                float(entry.get("actual_entry_shares") or 0.0)
                or float(entry.get("source_shares") or 0.0)
                or (
                    (
                        float(entry.get("actual_entry_size_usd") or 0.0)
                        or float(entry.get("signal_size_usd") or 0.0)
                    )
                    / (
                        float(entry.get("actual_entry_price") or 0.0)
                        or float(entry.get("price_at_signal") or 0.0)
                    )
                    if (
                        float(entry.get("actual_entry_price") or 0.0)
                        or float(entry.get("price_at_signal") or 0.0)
                    ) > 0
                    else 0.0
                )
                for entry in entries
            )
        if shares <= 0:
            return 0.0, 0.0, 0.0
        exit_notional = round(shares * exit_price, 6)
        pnl = round(exit_notional - size_usd, 2)
        return shares, exit_notional, pnl

    def _finalize_exit(
        self,
        *,
        entries: list[dict],
        position: dict,
        real_money: bool,
        exit_trade_id: str,
        exit_price: float,
        exit_reason: str,
        exit_order_id: str | None,
        market_id: str,
        trader_address: str,
        dedup,
    ) -> tuple[float, float, float]:
        pnl_column = "actual_pnl_usd" if real_money else "shadow_pnl_usd"
        conn = get_conn()
        now_ts = int(time.time())
        total_shares = 0.0
        total_exit_notional = 0.0
        total_pnl = 0.0
        fallback_price = float(position.get("avg_price") or 0.0)

        for entry in entries:
            entry_size = float(entry.get("actual_entry_size_usd") or 0.0) or float(entry.get("signal_size_usd") or 0.0)
            entry_price = float(entry.get("actual_entry_price") or 0.0) or float(entry.get("price_at_signal") or 0.0) or fallback_price
            entry_shares = float(entry.get("actual_entry_shares") or 0.0) or float(entry.get("source_shares") or 0.0)
            if entry_shares <= 0 and entry_price > 0 and entry_size > 0:
                entry_shares = entry_size / entry_price
            if entry_shares <= 0:
                continue

            entry_exit_notional = round(entry_shares * exit_price, 6)
            entry_pnl = round(entry_exit_notional - entry_size, 2)
            total_shares += entry_shares
            total_exit_notional += entry_exit_notional
            total_pnl += entry_pnl

            conn.execute(
                f"""
                UPDATE trade_log
                SET exited_at=?,
                    exit_trade_id=?,
                    exit_price=?,
                    exit_shares=?,
                    exit_size_usd=?,
                    exit_order_id=?,
                    exit_reason=?,
                    resolved_at=?,
                    {pnl_column}=?
                WHERE id=?
                """,
                (
                    now_ts,
                    exit_trade_id,
                    exit_price,
                    entry_shares,
                    entry_exit_notional,
                    exit_order_id,
                    exit_reason,
                    now_ts,
                    entry_pnl,
                    int(entry["id"]),
                ),
            )
        conn.commit()
        conn.close()
        dedup.clear_position(
            market_id,
            str(position.get("token_id") or ""),
            str(position.get("side") or ""),
            real_money=real_money,
        )
        dedup.mark_seen(exit_trade_id, market_id, trader_address)
        return round(total_shares, 6), round(total_exit_notional, 6), round(total_pnl, 2)

    def _execute_shadow_exit(
        self,
        *,
        trade_id: str,
        market_id: str,
        token_id: str,
        event,
        dedup,
        position: dict,
        entries: list[dict],
        exit_price: float,
        shares: float,
        exit_notional: float,
        pnl: float,
    ) -> ExecutionResult:
        fill, reject_reason = self._simulate_shadow_sell(getattr(event, "raw_orderbook", None), shares)
        if fill is None:
            dedup.release(
                market_id,
                str(position.get("token_id") or token_id or ""),
                str(position.get("side") or event.side or ""),
            )
            return ExecutionResult(False, True, None, 0.0, reject_reason or "shadow simulation sell failed", action="exit")

        reason = "watched trader exited, so we closed our matching position"
        shares, exit_notional, pnl = self._finalize_exit(
            entries=entries,
            position=position,
            real_money=False,
            exit_trade_id=trade_id,
            exit_price=fill.avg_price,
            exit_reason=reason,
            exit_order_id=None,
            market_id=market_id,
            trader_address=event.trader_address,
            dedup=dedup,
        )
        logger.info(
            "[SHADOW EXIT] %s | %s | sold %.3f shares | est. pnl=%+.2f",
            event.question[:60],
            event.side.upper(),
            shares,
            pnl,
        )
        send_alert(
            f"[SHADOW EXIT] {event.side.upper()} {shares:.3f} shares\n"
            f"{event.question[:80]}\n"
            f"exit @ {fill.avg_price:.3f} | pnl={pnl:+.2f}"
        )
        return ExecutionResult(
            True,
            True,
            None,
            exit_notional,
            reason,
            shares=shares,
            pnl_usd=pnl,
            action="exit",
        )

    def _execute_live_exit(
        self,
        *,
        trade_id: str,
        market_id: str,
        token_id: str,
        event,
        dedup,
        position: dict,
        entries: list[dict],
        exit_price: float,
        shares: float,
        exit_notional: float,
        pnl: float,
    ) -> ExecutionResult:
        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL

            balance_before = self.get_usdc_balance()
            order = MarketOrderArgs(
                token_id=token_id or str(position.get("token_id") or ""),
                amount=shares,
                side=SELL,
            )
            signed = self._clob.create_market_order(order)
            response = self._clob.post_order(signed, OrderType.FOK)
            order_id = response.get("orderID") or response.get("id", "unknown")
            expected_fill, _ = self.estimate_exit_fill(getattr(event, "raw_orderbook", None), shares)
            _, balance_gained = self._measure_live_balance_change(balance_before, expect_increase=True)
            remaining_position = self._sync_live_positions(
                dedup,
                market_id=market_id,
                token_id=token_id or str(position.get("token_id") or ""),
                side=str(position.get("side") or event.side or ""),
                expect_present=False,
            )
            if self._live_position_shares(remaining_position) > 1e-6:
                raise RuntimeError("live exit order posted but the position still appeared open after sync")

            actual_exit_notional = balance_gained or (expected_fill.spent_usd if expected_fill is not None else 0.0) or exit_notional
            actual_exit_price = (
                (actual_exit_notional / shares)
                if actual_exit_notional > 0 and shares > 0
                else (expected_fill.avg_price if expected_fill is not None else exit_price)
            )
            reason = "watched trader exited, so we closed our matching position"
            shares, exit_notional, pnl = self._finalize_exit(
                entries=entries,
                position=position,
                real_money=True,
                exit_trade_id=trade_id,
                exit_price=actual_exit_price,
                exit_reason=reason,
                exit_order_id=order_id,
                market_id=market_id,
                trader_address=event.trader_address,
                dedup=dedup,
            )
            logger.info(
                "[LIVE EXIT] %s | %s | sold %.3f shares | est. pnl=%+.2f | order=%s",
                event.question[:60],
                event.side.upper(),
                shares,
                pnl,
                order_id,
            )
            send_alert(
                f"[LIVE EXIT] {event.side.upper()} {shares:.3f} shares\n"
                f"{event.question[:80]}\n"
                f"exit @ {actual_exit_price:.3f} | pnl={pnl:+.2f} | order={order_id}"
            )
            return ExecutionResult(
                True,
                False,
                order_id,
                exit_notional,
                reason,
                shares=shares,
                pnl_usd=pnl,
                action="exit",
            )
        except Exception as exc:
            dedup.release(
                market_id,
                str(position.get("token_id") or token_id or ""),
                str(position.get("side") or ""),
            )
            logger.error("[LIVE EXIT ERROR] %s: %s", market_id, exc)
            send_alert(f"[LIVE EXIT ERROR]\n{event.question[:80]}\n{exc}")
            return ExecutionResult(
                False,
                False,
                None,
                0.0,
                f"live exit failed, {exc}",
                action="exit",
            )

    def log_skip(
        self,
        trade_id: str,
        market_id: str,
        question: str,
        trader_address: str,
        side: str,
        price: float,
        size_usd: float,
        confidence: float,
        kelly_f: float,
        reason: str,
        trader_f=None,
        market_f=None,
        event=None,
        signal=None,
    ) -> None:
        log_trade(
            trade_id=trade_id,
            market_id=market_id,
            question=question,
            trader_address=trader_address,
            side=side,
            price=price,
            signal_size_usd=size_usd,
            confidence=confidence,
            kelly_f=kelly_f,
            real_money=use_real_money(),
            order_id=None,
            skipped=True,
            skip_reason=reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )


def log_trade(
    trade_id,
    market_id,
    question,
    trader_address,
    side,
    price,
    signal_size_usd,
    confidence,
    kelly_f,
    real_money,
    order_id,
    skipped,
    skip_reason,
    actual_entry_price=None,
    actual_entry_shares=None,
    actual_entry_size_usd=None,
    trader_f=None,
    market_f=None,
    event=None,
    signal=None,
) -> None:
    spread = (
        (market_f.best_ask - market_f.best_bid) / market_f.mid
        if market_f and market_f.mid > 0
        else None
    )
    momentum = (
        abs(market_f.mid - market_f.price_1h_ago) / market_f.price_1h_ago
        if market_f and market_f.price_1h_ago > 0
        else None
    )
    volume_trend = (
        market_f.volume_24h_usd / (market_f.volume_7d_avg_usd + 1e-6)
        if market_f
        else None
    )
    market_components = signal.get("market", {}).get("components", {}) if isinstance(signal, dict) else {}
    feature_price = (
        float(actual_entry_price)
        if actual_entry_price is not None
        else (
            float(getattr(market_f, "execution_price", 0.0))
            if market_f and getattr(market_f, "execution_price", 0.0)
            else float(price)
        )
    )
    placed_at = int(time.time())
    source_ts = getattr(event, "timestamp", None)
    observed_at = getattr(event, "observed_at", None)
    poll_started_at = getattr(event, "poll_started_at", None)
    market_close_ts = getattr(event, "market_close_ts", None)
    metadata_fetched_at = getattr(event, "metadata_fetched_at", None)
    orderbook_fetched_at = getattr(event, "orderbook_fetched_at", None)
    source_latency_s = (
        round(max(placed_at - source_ts, 0), 3)
        if isinstance(source_ts, (int, float)) and source_ts > 0
        else None
    )
    observation_latency_s = (
        round(max(observed_at - source_ts, 0), 3)
        if isinstance(source_ts, (int, float))
        and source_ts > 0
        and isinstance(observed_at, (int, float))
        and observed_at > 0
        else None
    )
    processing_latency_s = (
        round(max(placed_at - observed_at, 0), 3)
        if isinstance(observed_at, (int, float)) and observed_at > 0
        else None
    )
    decision_context = {
        "event": {
            "question": getattr(event, "question", question),
            "token_id": getattr(event, "token_id", None),
            "action": getattr(event, "action", None),
            "timestamp": getattr(event, "timestamp", None),
            "timestamp_raw": getattr(event, "source_ts_raw", None),
            "price": getattr(event, "price", price),
            "shares": getattr(event, "shares", None),
            "amount_usd": getattr(event, "size_usd", None),
            "close_time": getattr(event, "close_time", None),
            "market_close_ts": market_close_ts,
            "snapshot": getattr(event, "snapshot", None),
        },
        "timing": {
            "poll_started_at": poll_started_at,
            "observed_at": observed_at,
            "metadata_fetched_at": metadata_fetched_at,
            "orderbook_fetched_at": orderbook_fetched_at,
            "placed_at": placed_at,
            "source_latency_s": source_latency_s,
            "observation_latency_s": observation_latency_s,
            "processing_latency_s": processing_latency_s,
        },
        "signal": signal if isinstance(signal, dict) else None,
    }
    values = [
        trade_id,
        market_id,
        question,
        trader_address.lower(),
        getattr(event, "trader_name", None),
        side,
        getattr(event, "token_id", None),
        getattr(event, "action", None),
        source_ts,
        getattr(event, "source_ts_raw", None),
        observed_at,
        poll_started_at,
        market_close_ts,
        metadata_fetched_at,
        orderbook_fetched_at,
        source_latency_s,
        observation_latency_s,
        processing_latency_s,
        getattr(event, "shares", None),
        getattr(event, "size_usd", None),
        json.dumps(getattr(event, "raw_trade", None), separators=(",", ":"), default=str)
        if getattr(event, "raw_trade", None)
        else None,
        json.dumps(getattr(event, "raw_market_metadata", None), separators=(",", ":"), default=str)
        if getattr(event, "raw_market_metadata", None)
        else None,
        json.dumps(getattr(event, "raw_orderbook", None), separators=(",", ":"), default=str)
        if getattr(event, "raw_orderbook", None)
        else None,
        json.dumps(getattr(event, "snapshot", None), separators=(",", ":"), default=str)
        if getattr(event, "snapshot", None)
        else None,
        price,
        signal_size_usd,
        actual_entry_price,
        actual_entry_shares,
        actual_entry_size_usd,
        confidence,
        signal.get("raw_confidence") if isinstance(signal, dict) else None,
        kelly_f,
        signal.get("mode") if isinstance(signal, dict) else None,
        signal.get("belief_prior") if isinstance(signal, dict) else None,
        signal.get("belief_blend") if isinstance(signal, dict) else None,
        signal.get("belief_evidence") if isinstance(signal, dict) else None,
        signal.get("trader", {}).get("score") if isinstance(signal, dict) else None,
        signal.get("market", {}).get("score") if isinstance(signal, dict) else None,
        signal.get("veto") if isinstance(signal, dict) else None,
        1 if real_money else 0,
        order_id,
        1 if skipped else 0,
        skip_reason,
        placed_at,
        trader_f.win_rate if trader_f else None,
        trader_f.n_trades if trader_f else None,
        trader_f.conviction_ratio if trader_f else None,
        trader_f.volume_usd if trader_f else None,
        trader_f.avg_size_usd if trader_f else None,
        trader_f.account_age_d if trader_f else None,
        trader_f.consistency if trader_f else None,
        trader_f.diversity if trader_f else None,
        market_f.days_to_res if market_f else None,
        feature_price,
        spread,
        momentum,
        market_f.volume_24h_usd if market_f else None,
        market_f.volume_7d_avg_usd if market_f else None,
        volume_trend,
        market_f.oi_usd if market_f else None,
        market_f.top_holder_pct if market_f else None,
        market_f.bid_depth_usd if market_f else None,
        market_f.ask_depth_usd if market_f else None,
        json.dumps(market_components, separators=(",", ":")) if market_components else None,
        json.dumps(decision_context, separators=(",", ":"), default=str),
    ]
    placeholders = ",".join(["?"] * len(values))

    conn = get_conn()
    conn.execute(
        f"""
        INSERT INTO trade_log (
            trade_id, market_id, question, trader_address, trader_name, side,
            token_id, source_action, source_ts, source_ts_raw, observed_at, poll_started_at,
            market_close_ts, metadata_fetched_at, orderbook_fetched_at, source_latency_s,
            observation_latency_s, processing_latency_s, source_shares, source_amount_usd,
            source_trade_json, market_metadata_json, orderbook_json, snapshot_json,
            price_at_signal, signal_size_usd, actual_entry_price, actual_entry_shares,
            actual_entry_size_usd, confidence, raw_confidence, kelly_fraction,
            signal_mode, belief_prior, belief_blend, belief_evidence, trader_score,
            market_score, market_veto, real_money, order_id, skipped, skip_reason, placed_at,
            f_trader_win_rate, f_trader_n_trades, f_conviction_ratio,
            f_trader_volume_usd, f_trader_avg_size_usd, f_account_age_days, f_consistency,
            f_trader_diversity, f_days_to_res, f_price, f_spread_pct, f_momentum_1h,
            f_volume_24h_usd, f_volume_7d_avg_usd, f_volume_trend, f_oi_usd, f_top_holder_pct,
            f_bid_depth_usd, f_ask_depth_usd, market_components_json, decision_context_json
        ) VALUES ({placeholders})
        """,
        values,
    )
    conn.commit()
    conn.close()
