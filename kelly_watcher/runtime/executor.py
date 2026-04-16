from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from kelly_watcher.integrations.alerter import (
    append_tracking_detail,
    build_lines,
    build_market_error_alert,
    build_market_line,
    build_trade_entry_alert,
    build_trade_exit_alert,
    send_alert,
)
from config import (
    approval_fixed_cost_usd,
    entry_fixed_cost_usd,
    expected_close_fixed_cost_usd,
    exit_fixed_cost_usd,
    include_expected_exit_fee_in_sizing,
    max_live_health_failures,
    max_market_exposure_fraction,
    max_orderbook_staleness_seconds,
    max_total_open_exposure_fraction,
    max_trader_exposure_fraction,
    settlement_fixed_cost_usd,
    shadow_bankroll_usd,
    use_real_money,
    wallet_address,
)
from kelly_watcher.data.db import current_promotion_epoch_id, get_conn
from kelly_watcher.engine.economics import (
    EntryEconomics,
    ExitEconomics,
    build_entry_economics,
    build_exit_economics,
    taker_fee_usd,
)
from kelly_watcher.data.market_urls import market_url_from_metadata
from kelly_watcher.engine.trade_contract import (
    DEFAULT_EXPERIMENT_ARM,
    OPEN_EXECUTED_ENTRY_SQL,
    remaining_entry_shares_expr,
    remaining_entry_size_expr,
    remaining_source_shares_expr,
)
from kelly_watcher.engine.wallet_trust import total_open_exposure_cap_fraction_for_wallet

logger = logging.getLogger(__name__)
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
LIVE_SYNC_ATTEMPTS = 4
LIVE_SYNC_DELAY_S = 0.35
USDC_DECIMALS = 1_000_000.0
EXECUTION_ORDERBOOK_TIMEOUT_S = 3.0
FEE_RATE_TIMEOUT_S = 3.0
FEE_RATE_CACHE_TTL_S = 300.0


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


@dataclass(frozen=True)
class TotalExposureDecision:
    allowed_size_usd: float
    clipped: bool
    block_reason: str | None = None


@dataclass
class LiveWalletStatus:
    balance_usd: float
    max_allowance_usd: float
    signer_address: str


@dataclass
class LiveExchangeFill:
    shares: float
    notional_usd: float
    avg_price: float
    source: str


def _to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _market_url_from_metadata(meta: Any) -> str | None:
    return market_url_from_metadata(meta)


class PolymarketExecutor:
    def __init__(self):
        self._clob = None
        self._last_live_balance_ok_at = 0
        self._last_live_position_sync_ok_at = 0
        self._consecutive_live_balance_failures = 0
        self._consecutive_live_position_sync_failures = 0
        self._fee_rate_cache: dict[str, tuple[float, int]] = {}
        self._conditional_allowance_cache: dict[str, bool] = {}
        self._init_clob()

    @staticmethod
    def _fees_enabled_from_metadata(meta: Any) -> bool | None:
        if not isinstance(meta, dict):
            return None
        raw_value = meta.get("feesEnabled")
        if raw_value is None:
            return None
        if isinstance(raw_value, bool):
            return raw_value
        normalized = str(raw_value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return None

    @staticmethod
    def _extract_fee_rate_bps(payload: Any) -> int | None:
        if not isinstance(payload, dict):
            return None
        raw_value = (
            payload.get("base_fee")
            or payload.get("baseFee")
            or payload.get("fee_rate_bps")
            or payload.get("feeRateBps")
        )
        if raw_value in {None, ""}:
            return None
        try:
            return max(int(round(float(raw_value))), 0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_orderbook_snapshot(raw_book: dict[str, Any] | None) -> dict[str, float] | None:
        if not isinstance(raw_book, dict):
            return None
        bids = raw_book.get("bids", [])
        asks = raw_book.get("asks", [])
        best_bid = _to_float(bids[0].get("price")) if bids else 0.0
        best_ask = _to_float(asks[0].get("price")) if asks else 0.0
        if best_bid <= 0 and best_ask <= 0:
            return None
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0,
            "bid_depth_usd": sum(_to_float(level.get("size")) * _to_float(level.get("price")) for level in bids[:5] if isinstance(level, dict)),
            "ask_depth_usd": sum(_to_float(level.get("size")) * _to_float(level.get("price")) for level in asks[:5] if isinstance(level, dict)),
        }

    def get_fee_rate_bps(
        self,
        token_id: str,
        *,
        market_meta: dict[str, Any] | None = None,
    ) -> tuple[int | None, str | None]:
        normalized_token = str(token_id or "").strip()
        if not normalized_token:
            return 0, None

        cache = getattr(self, "_fee_rate_cache", None)
        if cache is None:
            cache = {}
            self._fee_rate_cache = cache
        cached = cache.get(normalized_token)
        if cached is not None and (time.time() - cached[0]) <= FEE_RATE_CACHE_TTL_S:
            return cached[1], None

        fees_enabled = self._fees_enabled_from_metadata(market_meta)
        try:
            with httpx.Client(timeout=FEE_RATE_TIMEOUT_S, follow_redirects=True) as client:
                response = client.get(f"{CLOB_API}/fee-rate", params={"token_id": normalized_token})
            if response.status_code == 404:
                if fees_enabled is False:
                    cache[normalized_token] = (time.time(), 0)
                    return 0, None
                if cached is not None:
                    logger.warning("Using stale fee rate cache for %s after 404 lookup", normalized_token[:16])
                    return cached[1], None
                return None, "market fee rate was unavailable at execution time"
            response.raise_for_status()
            fee_rate_bps = self._extract_fee_rate_bps(response.json())
            if fee_rate_bps is None:
                if fees_enabled is False:
                    fee_rate_bps = 0
                elif cached is not None:
                    logger.warning("Using stale fee rate cache for %s after empty lookup payload", normalized_token[:16])
                    return cached[1], None
                else:
                    return None, "market fee rate was unavailable at execution time"
            cache[normalized_token] = (time.time(), fee_rate_bps)
            return fee_rate_bps, None
        except Exception as exc:
            logger.warning("Fee rate lookup failed for %s: %s", normalized_token[:16], exc)
            if cached is not None:
                logger.warning("Using stale fee rate cache for %s after refresh failure", normalized_token[:16])
                return cached[1], None
            if fees_enabled is False:
                return 0, None
            return None, "market fee rate was unavailable at execution time"

    def fetch_execution_orderbook(self, token_id: str) -> tuple[dict[str, float] | None, dict[str, Any] | None, int]:
        normalized_token = str(token_id or "").strip()
        if not normalized_token:
            return None, None, 0
        try:
            with httpx.Client(timeout=EXECUTION_ORDERBOOK_TIMEOUT_S, follow_redirects=True) as client:
                response = client.get(f"{CLOB_API}/book", params={"token_id": normalized_token})
            response.raise_for_status()
            raw_book = response.json()
        except Exception as exc:
            logger.warning("Execution orderbook refresh failed for %s: %s", normalized_token[:16], exc)
            return None, None, 0

        snapshot = self._build_orderbook_snapshot(raw_book)
        return snapshot, raw_book if isinstance(raw_book, dict) else None, int(time.time())

    def refresh_event_market_data(self, event) -> tuple[bool, str | None]:
        token_id = str(getattr(event, "token_id", "") or "").strip()
        if not token_id:
            return True, None

        snapshot = dict(getattr(event, "snapshot", None) or {})
        now_ts = int(time.time())
        current_age = (
            max(now_ts - int(getattr(event, "orderbook_fetched_at", 0) or 0), 0)
            if getattr(event, "orderbook_fetched_at", 0)
            else max_orderbook_staleness_seconds() + 1
        )
        should_refresh = getattr(event, "raw_orderbook", None) is None or current_age > max_orderbook_staleness_seconds()
        if should_refresh:
            refreshed_snapshot, raw_book, fetched_at = self.fetch_execution_orderbook(token_id)
            if raw_book is None or refreshed_snapshot is None:
                if getattr(event, "raw_orderbook", None) is None or current_age > max_orderbook_staleness_seconds():
                    return False, "current order book quote was unavailable at execution time"
            else:
                snapshot.update(refreshed_snapshot)
                event.raw_orderbook = raw_book
                event.orderbook_fetched_at = fetched_at

        fee_rate_bps, fee_reason = self.get_fee_rate_bps(
            token_id,
            market_meta=getattr(event, "raw_market_metadata", None),
        )
        if fee_reason:
            return False, fee_reason

        fees_enabled = self._fees_enabled_from_metadata(getattr(event, "raw_market_metadata", None))
        snapshot["fee_rate_bps"] = int(fee_rate_bps or 0)
        if fees_enabled is not None:
            snapshot["fees_enabled"] = fees_enabled
        elif int(fee_rate_bps or 0) > 0:
            snapshot["fees_enabled"] = True
        event.snapshot = snapshot
        return True, None

    def _conditional_allowance_ready(self, token_id: str) -> bool | None:
        normalized_token = str(token_id or "").strip()
        if not normalized_token or self._clob is None or not use_real_money():
            return True
        cache = getattr(self, "_conditional_allowance_cache", None)
        if cache is None:
            cache = {}
            self._conditional_allowance_cache = cache
        if normalized_token in cache:
            return cache[normalized_token]
        try:
            payload = self._get_live_allowance_payload(asset_type="CONDITIONAL", token_id=normalized_token)
        except Exception as exc:
            logger.warning("Conditional allowance pre-check failed for %s: %s", normalized_token[:16], exc)
            return None
        ready = self._payload_max_allowance_usd(payload) > 0.0
        cache[normalized_token] = ready
        return ready

    def estimate_entry_economics(
        self,
        *,
        token_id: str,
        fill: SimulatedFill,
        market_meta: dict[str, Any] | None = None,
    ) -> tuple[EntryEconomics | None, str | None]:
        fee_rate_bps, fee_reason = self.get_fee_rate_bps(token_id, market_meta=market_meta)
        if fee_reason:
            return None, fee_reason

        needs_approval = self._conditional_allowance_ready(token_id) is False
        fixed_cost = entry_fixed_cost_usd() + (approval_fixed_cost_usd() if needs_approval else 0.0)
        economics = build_entry_economics(
            gross_price=fill.avg_price,
            gross_shares=fill.shares,
            gross_spent_usd=fill.spent_usd,
            fee_rate_bps=int(fee_rate_bps or 0),
            fixed_cost_usd=fixed_cost,
            include_expected_exit_fee_in_sizing=include_expected_exit_fee_in_sizing(),
            expected_close_fixed_cost_usd=expected_close_fixed_cost_usd(),
        )
        if economics.net_shares <= 0 or economics.sizing_effective_price <= 0:
            return None, "fees consumed the entire quoted fill"
        return economics, None

    def _entry_economics_for_fill(
        self,
        *,
        token_id: str,
        fill: SimulatedFill,
        include_approval_cost: bool,
        market_meta: dict[str, Any] | None = None,
    ) -> tuple[EntryEconomics | None, str | None]:
        fee_rate_bps, fee_reason = self.get_fee_rate_bps(token_id, market_meta=market_meta)
        if fee_reason:
            return None, fee_reason
        economics = build_entry_economics(
            gross_price=fill.avg_price,
            gross_shares=fill.shares,
            gross_spent_usd=fill.spent_usd,
            fee_rate_bps=int(fee_rate_bps or 0),
            fixed_cost_usd=entry_fixed_cost_usd() + (approval_fixed_cost_usd() if include_approval_cost else 0.0),
            include_expected_exit_fee_in_sizing=False,
            expected_close_fixed_cost_usd=0.0,
        )
        if economics.net_shares <= 0 or economics.effective_entry_price <= 0:
            return None, "fees consumed the executed buy"
        return economics, None

    def _exit_economics_for_fill(
        self,
        *,
        token_id: str,
        gross_shares: float,
        gross_notional_usd: float,
        gross_price: float,
        market_meta: dict[str, Any] | None = None,
    ) -> tuple[ExitEconomics | None, str | None]:
        fee_rate_bps, fee_reason = self.get_fee_rate_bps(token_id, market_meta=market_meta)
        if fee_reason:
            return None, fee_reason
        economics = build_exit_economics(
            gross_price=gross_price,
            gross_shares=gross_shares,
            gross_notional_usd=gross_notional_usd,
            fee_rate_bps=int(fee_rate_bps or 0),
            fixed_cost_usd=exit_fixed_cost_usd(),
        )
        if economics.net_proceeds_usd <= 0:
            return None, "fees consumed the executed exit"
        return economics, None

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
            realized_pnl, spent = self._shadow_balance_components()
            return max(shadow_bankroll_usd() + realized_pnl - spent, 0.0)

        try:
            return self._get_live_wallet_status().balance_usd
        except Exception as exc:
            logger.error("Balance fetch failed: %s", exc)
            return 0.0

    @staticmethod
    def _live_position_mark_value(row: dict[str, Any] | None) -> float:
        if not row:
            return 0.0

        current_value = max(_to_float(row.get("currentValue")), 0.0)
        if current_value > 0:
            return current_value

        total_bought = PolymarketExecutor._live_position_cost(row)
        if total_bought <= 0:
            return 0.0

        cash_pnl = _to_float(row.get("cashPnl"))
        if abs(cash_pnl) > 1e-9:
            return max(total_bought + cash_pnl, 0.0)
        return total_bought

    def get_account_equity_usd(self) -> float:
        if not use_real_money():
            realized_pnl, _ = self._shadow_balance_components()
            # Shadow mode has no mark-to-market feed for open positions.
            # Use cost basis so deployed capital does not masquerade as a drawdown.
            return max(shadow_bankroll_usd() + realized_pnl, 0.0)

        balance_usd = self.get_usdc_balance()

        rows = self._fetch_live_positions()
        if rows is None:
            logger.warning("Account equity fell back to free USDC because live positions could not be refreshed")
            return balance_usd

        open_value_usd = sum(self._live_position_mark_value(row) for row in rows)
        return round(balance_usd + open_value_usd, 6)

    @staticmethod
    def _shadow_balance_components() -> tuple[float, float]:
        conn = get_conn()
        row = conn.execute(
            f"""
            SELECT
                SUM(
                    CASE
                        WHEN {OPEN_EXECUTED_ENTRY_SQL} THEN {remaining_entry_size_expr()}
                        ELSE 0
                    END
                ) AS remaining_cost,
            SUM(
                CASE
                    WHEN skipped=0 AND COALESCE(source_action, 'buy')='buy' AND exited_at IS NULL AND outcome IS NULL
                        THEN COALESCE(realized_exit_pnl_usd, 0)
                    WHEN skipped=0 AND COALESCE(source_action, 'buy')='buy'
                        THEN COALESCE(shadow_pnl_usd, 0)
                    ELSE 0
                END
            ) AS realized_pnl
        FROM trade_log
        WHERE real_money=0
          AND LOWER(COALESCE(experiment_arm, '{DEFAULT_EXPERIMENT_ARM}')) = '{DEFAULT_EXPERIMENT_ARM}'
        """
        ).fetchone()
        conn.close()
        realized_pnl = float(row["realized_pnl"] or 0.0)
        remaining_cost = float(row["remaining_cost"] or 0.0)
        return realized_pnl, remaining_cost

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

        try:
            payload = self._get_live_allowance_payload(asset_type="COLLATERAL")
            signer_address = str(self._clob.get_address() or "").strip().lower()
            status = LiveWalletStatus(
                balance_usd=self._parse_usdc_base_units(payload.get("balance")),
                max_allowance_usd=self._payload_max_allowance_usd(payload),
                signer_address=signer_address,
            )
            self._record_live_balance_result(True)
            return status
        except Exception:
            self._record_live_balance_result(False)
            raise

    def _record_live_balance_result(self, ok: bool) -> None:
        if ok:
            self._last_live_balance_ok_at = int(time.time())
            self._consecutive_live_balance_failures = 0
            return
        self._consecutive_live_balance_failures += 1

    def _record_live_position_sync_result(self, ok: bool) -> None:
        if ok:
            self._last_live_position_sync_ok_at = int(time.time())
            self._consecutive_live_position_sync_failures = 0
            return
        self._consecutive_live_position_sync_failures += 1

    @staticmethod
    def _payload_max_allowance_usd(payload: dict[str, Any]) -> float:
        allowances = payload.get("allowances")
        allowance_values = allowances.values() if isinstance(allowances, dict) else []
        return max(
            (PolymarketExecutor._parse_usdc_base_units(value) for value in allowance_values),
            default=0.0,
        )

    def _get_live_allowance_payload(
        self,
        *,
        asset_type: str,
        token_id: str | None = None,
    ) -> dict[str, Any]:
        if self._clob is None:
            raise RuntimeError("live trading requested, but the CLOB client was not initialized")

        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        normalized_asset = str(asset_type or "").strip().upper()
        if normalized_asset == "CONDITIONAL":
            if not str(token_id or "").strip():
                raise RuntimeError("conditional allowance checks require a token_id")
            params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=str(token_id).strip())
        else:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)

        payload = self._clob.get_balance_allowance(params)
        if not isinstance(payload, dict):
            raise RuntimeError("live balance check returned an unexpected response payload")
        return payload

    def _ensure_live_token_allowance(self, token_id: str) -> bool:
        normalized_token = str(token_id or "").strip()
        if not normalized_token or self._clob is None or not use_real_money():
            return False

        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        payload = self._get_live_allowance_payload(asset_type="CONDITIONAL", token_id=normalized_token)
        if self._payload_max_allowance_usd(payload) > 0.0:
            cache = getattr(self, "_conditional_allowance_cache", None)
            if cache is None:
                cache = {}
                self._conditional_allowance_cache = cache
            cache[normalized_token] = True
            return False

        logger.info("Conditional token allowance missing for %s; requesting approval", normalized_token[:16])
        params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=normalized_token)
        response = self._clob.update_balance_allowance(params)
        logger.info("Conditional token allowance update response: %s", response)

        for attempt in range(LIVE_SYNC_ATTEMPTS):
            payload = self._get_live_allowance_payload(asset_type="CONDITIONAL", token_id=normalized_token)
            if self._payload_max_allowance_usd(payload) > 0.0:
                logger.info("Conditional token allowance ready for %s", normalized_token[:16])
                cache = getattr(self, "_conditional_allowance_cache", None)
                if cache is None:
                    cache = {}
                    self._conditional_allowance_cache = cache
                cache[normalized_token] = True
                return True
            if attempt < LIVE_SYNC_ATTEMPTS - 1:
                time.sleep(LIVE_SYNC_DELAY_S * (attempt + 1))

        raise RuntimeError(
            f"conditional token allowance for {normalized_token[:16]} was still zero after requesting approval"
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
                normalized = [row for row in rows if isinstance(row, dict)]
                self._record_live_position_sync_result(True)
                return normalized
        except Exception as exc:
            self._record_live_position_sync_result(False)
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

    @staticmethod
    def _extract_order_id(payload: Any) -> str:
        if not isinstance(payload, dict):
            return "unknown"
        return str(payload.get("orderID") or payload.get("id") or "unknown")

    @staticmethod
    def _extract_associated_trade_ids(payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        trade_ids = payload.get("associate_trades") or payload.get("associateTrades") or payload.get("tradeIDs") or payload.get("tradeIds") or []
        if isinstance(trade_ids, (str, int)):
            trade_ids = [trade_ids]
        return [str(value).strip() for value in trade_ids if str(value).strip()]

    @staticmethod
    def _live_trade_status_ok(status: Any) -> bool:
        normalized = str(status or "").strip().upper()
        return normalized in {"MATCHED", "MINED", "CONFIRMED"}

    @staticmethod
    def _parse_live_order_response_fill(
        payload: Any,
        *,
        action: str,
    ) -> LiveExchangeFill | None:
        if not isinstance(payload, dict):
            return None

        success = payload.get("success")
        status = str(payload.get("status") or "").strip().lower()
        if success is False or status in {"failed", "rejected", "cancelled", "canceled"}:
            error_text = str(payload.get("errorMsg") or payload.get("error") or status or "order rejected").strip()
            raise RuntimeError(f"live {action} order was rejected by the exchange: {error_text}")

        taking_amount = _to_float(payload.get("takingAmount") or payload.get("taking_amount"))
        making_amount = _to_float(payload.get("makingAmount") or payload.get("making_amount"))
        if taking_amount <= 0 or making_amount <= 0:
            return None

        if action == "buy":
            shares = taking_amount
            notional_usd = making_amount
        else:
            shares = making_amount
            notional_usd = taking_amount

        if shares <= 0 or notional_usd <= 0:
            return None

        return LiveExchangeFill(
            shares=shares,
            notional_usd=notional_usd,
            avg_price=(notional_usd / shares) if shares > 0 else 0.0,
            source="post_order_response",
        )

    @staticmethod
    def _is_unfilled_fok_response(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        status = str(payload.get("status") or "").strip().lower()
        if status in {"cancelled", "canceled", "unfilled"}:
            return True
        return bool(payload.get("success")) is False and status in {"", "pending"}

    @staticmethod
    def _parse_live_trade_fill(
        rows: list[dict[str, Any]],
    ) -> LiveExchangeFill | None:
        total_shares = 0.0
        total_notional = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not PolymarketExecutor._live_trade_status_ok(row.get("status")):
                continue
            shares = _to_float(row.get("size") or row.get("filledSize") or row.get("sizeMatched"))
            price = _to_float(row.get("price"))
            if shares <= 0 or price <= 0:
                continue
            total_shares += shares
            total_notional += shares * price

        if total_shares <= 0 or total_notional <= 0:
            return None

        return LiveExchangeFill(
            shares=round(total_shares, 6),
            notional_usd=round(total_notional, 6),
            avg_price=round(total_notional / total_shares, 6),
            source="trade_reconciliation",
        )

    def _fetch_trade_rows_by_ids(self, trade_ids: list[str]) -> list[dict[str, Any]]:
        if self._clob is None or not trade_ids:
            return []

        from py_clob_client.clob_types import TradeParams

        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for trade_id in trade_ids:
            normalized = str(trade_id or "").strip()
            if not normalized or normalized in seen_ids:
                continue
            seen_ids.add(normalized)
            payload = self._clob.get_trades(TradeParams(id=normalized))
            for row in payload if isinstance(payload, list) else []:
                if isinstance(row, dict):
                    rows.append(row)
        return rows

    def _reconcile_live_order_fill(
        self,
        *,
        order_id: str,
        response: Any,
        action: str,
    ) -> LiveExchangeFill | None:
        direct_fill = self._parse_live_order_response_fill(response, action=action)
        if direct_fill is not None:
            return direct_fill

        known_trade_ids = set(self._extract_associated_trade_ids(response))
        for attempt in range(LIVE_SYNC_ATTEMPTS):
            order_payload = None
            if self._clob is not None and order_id and order_id != "unknown":
                try:
                    order_payload = self._clob.get_order(order_id)
                except Exception as exc:
                    logger.warning("Live order lookup failed for %s: %s", order_id, exc)
                known_trade_ids.update(self._extract_associated_trade_ids(order_payload))

            reconciled = self._parse_live_trade_fill(self._fetch_trade_rows_by_ids(sorted(known_trade_ids)))
            if reconciled is not None:
                return reconciled

            if attempt < LIVE_SYNC_ATTEMPTS - 1:
                time.sleep(LIVE_SYNC_DELAY_S * (attempt + 1))
        return None

    def live_entry_health_status(self) -> tuple[str, str] | None:
        threshold = max_live_health_failures()
        if self._consecutive_live_balance_failures >= threshold:
            return (
                "wallet_balance_failures",
                (
                    f"live balance health degraded after {self._consecutive_live_balance_failures} "
                    "consecutive wallet-balance failures"
                ),
            )
        if self._consecutive_live_position_sync_failures >= threshold:
            return (
                "position_sync_failures",
                (
                    f"live exchange sync degraded after {self._consecutive_live_position_sync_failures} "
                    "consecutive position-sync failures"
                ),
            )
        return None

    def live_entry_health_reason(self) -> str | None:
        status = self.live_entry_health_status()
        return status[1] if status is not None else None

    def _open_risk_snapshot(self, *, real_money: bool) -> tuple[float, dict[str, float], dict[str, float]]:
        mode_flag = 1 if real_money else 0
        conn = get_conn()
        try:
            position_rows = conn.execute(
                "SELECT market_id, size_usd FROM positions WHERE real_money=?",
                (mode_flag,),
            ).fetchall()
            trader_rows = conn.execute(
                f"""
                SELECT trader_address, SUM({remaining_entry_size_expr()}) AS size_usd
                FROM trade_log
                WHERE real_money=?
                  AND {OPEN_EXECUTED_ENTRY_SQL}
                GROUP BY trader_address
                """,
                (mode_flag,),
            ).fetchall()
        finally:
            conn.close()

        total_open = 0.0
        by_market: dict[str, float] = {}
        for row in position_rows:
            size_usd = float(row["size_usd"] or 0.0)
            if size_usd <= 0:
                continue
            market_id = str(row["market_id"] or "").strip()
            total_open += size_usd
            by_market[market_id] = by_market.get(market_id, 0.0) + size_usd

        by_trader = {
            str(row["trader_address"] or "").strip().lower(): float(row["size_usd"] or 0.0)
            for row in trader_rows
            if float(row["size_usd"] or 0.0) > 0
        }
        return total_open, by_market, by_trader

    def entry_risk_block_reason(
        self,
        *,
        market_id: str,
        trader_address: str,
        proposed_size_usd: float,
        account_equity: float,
    ) -> str | None:
        if proposed_size_usd <= 0:
            return None
        if account_equity <= 0:
            return "account equity was unavailable for exposure checks, so the trade was blocked"

        _, by_market, by_trader = self._open_risk_snapshot(real_money=use_real_money())

        market_key = str(market_id or "").strip()
        market_after = by_market.get(market_key, 0.0) + proposed_size_usd
        market_cap = account_equity * max_market_exposure_fraction()
        if market_after > market_cap + 1e-9:
            return (
                f"market exposure for {market_key[:12]} would be ${market_after:.2f}, "
                f"above the {max_market_exposure_fraction() * 100:.1f}% cap"
            )

        trader_key = str(trader_address or "").strip().lower()
        trader_after = by_trader.get(trader_key, 0.0) + proposed_size_usd
        trader_cap = account_equity * max_trader_exposure_fraction()
        if trader_after > trader_cap + 1e-9:
            display_trader = trader_key[:10] if trader_key else "unknown trader"
            return (
                f"trader exposure for {display_trader} would be ${trader_after:.2f}, "
                f"above the {max_trader_exposure_fraction() * 100:.1f}% cap"
            )

        return None

    def total_open_exposure_decision(
        self,
        *,
        proposed_size_usd: float,
        account_equity: float,
        trader_address: str = "",
    ) -> TotalExposureDecision:
        if proposed_size_usd <= 0:
            return TotalExposureDecision(allowed_size_usd=0.0, clipped=False)
        if account_equity <= 0:
            return TotalExposureDecision(
                allowed_size_usd=0.0,
                clipped=False,
                block_reason="account equity was unavailable for exposure checks, so the trade was blocked",
            )

        total_open, _, _ = self._open_risk_snapshot(real_money=use_real_money())
        effective_cap_fraction = total_open_exposure_cap_fraction_for_wallet(
            trader_address,
            max_total_open_exposure_fraction(),
        )
        total_cap = account_equity * effective_cap_fraction
        total_after = total_open + proposed_size_usd
        if total_after <= total_cap + 1e-9:
            return TotalExposureDecision(
                allowed_size_usd=round(proposed_size_usd, 2),
                clipped=False,
            )

        remaining_headroom = max(total_cap - total_open, 0.0)
        allowed_size_usd = max(0.0, int((remaining_headroom + 1e-9) * 100.0) / 100.0)
        if allowed_size_usd <= 0:
            return TotalExposureDecision(
                allowed_size_usd=0.0,
                clipped=False,
                block_reason=(
                    f"total open exposure would be ${total_after:.2f} on ${account_equity:.2f} equity, "
                    f"above the {effective_cap_fraction * 100:.1f}% cap"
                ),
            )

        return TotalExposureDecision(
            allowed_size_usd=allowed_size_usd,
            clipped=allowed_size_usd + 1e-9 < proposed_size_usd,
        )

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

    def estimate_exit_economics(
        self,
        *,
        token_id: str,
        fill: SimulatedFill,
        market_meta: dict[str, Any] | None = None,
    ) -> tuple[ExitEconomics | None, str | None]:
        return self._exit_economics_for_fill(
            token_id=token_id,
            gross_shares=fill.shares,
            gross_notional_usd=fill.spent_usd,
            gross_price=fill.avg_price,
            market_meta=market_meta,
        )

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
        ok, market_data_reason = self.refresh_event_market_data(event)
        if not ok:
            dedup.release(market_id, token_id, side)
            return ExecutionResult(
                False,
                True,
                None,
                0.0,
                market_data_reason or "execution market data refresh failed",
            )

        fill, reject_reason = self._simulate_shadow_buy(getattr(event, "raw_orderbook", None), dollar_size)
        if fill is None:
            dedup.release(market_id, token_id, side)
            return ExecutionResult(False, True, None, 0.0, reject_reason or "shadow simulation buy failed")

        entry_economics, economics_reason = self._entry_economics_for_fill(
            token_id=token_id,
            fill=fill,
            include_approval_cost=False,
            market_meta=getattr(event, "raw_market_metadata", None),
        )
        if entry_economics is None:
            dedup.release(market_id, token_id, side)
            return ExecutionResult(False, True, None, 0.0, economics_reason or "entry fee model rejected the buy")

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
            actual_entry_price=entry_economics.effective_entry_price,
            actual_entry_shares=entry_economics.net_shares,
            actual_entry_size_usd=entry_economics.total_cost_usd,
            entry_economics=entry_economics,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=_signal,
        )
        dedup.confirm(
            market_id,
            side,
            entry_economics.total_cost_usd,
            token_id,
            entry_economics.effective_entry_price,
            real_money=False,
        )
        dedup.mark_seen(trade_id, market_id, event.trader_address)
        logger.info(
            "[SHADOW] %s | %s | $%.2f | %.3f sh @ %.3f | conf=%.3f",
            event.question[:60],
            side.upper(),
            entry_economics.total_cost_usd,
            entry_economics.net_shares,
            entry_economics.effective_entry_price,
            confidence,
        )
        send_alert(
            build_trade_entry_alert(
                mode="shadow",
                side=side,
                shares=entry_economics.net_shares,
                price=entry_economics.effective_entry_price,
                total_usd=entry_economics.total_cost_usd,
                confidence=confidence,
                question=event.question,
                market_url=_market_url_from_metadata(getattr(event, "raw_market_metadata", None)),
                tracked_trader_name=getattr(event, "trader_name", None),
                tracked_trader_address=getattr(event, "trader_address", None),
            ),
            kind="buy",
        )
        return ExecutionResult(
            True,
            True,
            None,
            entry_economics.total_cost_usd,
            "ok",
            shares=entry_economics.net_shares,
            action="entry",
        )

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

            ok, market_data_reason = self.refresh_event_market_data(event)
            if not ok:
                raise RuntimeError(market_data_reason or "execution market data refresh failed")

            # Refuse the entry if we cannot guarantee that a later exit can be approved.
            approval_requested = self._ensure_live_token_allowance(token_id)
            balance_before = self.get_usdc_balance() if use_real_money() else 0.0
            # token_id already identifies the YES or NO outcome token we want.
            order = MarketOrderArgs(
                token_id=token_id,
                amount=dollar_size,
                side=BUY,
            )
            signed = self._clob.create_market_order(order)
            response = self._clob.post_order(signed, OrderType.FOK)
            order_id = self._extract_order_id(response)
            if self._is_unfilled_fok_response(response):
                dedup.release(market_id, token_id, side)
                logger.info(
                    "[LIVE] FOK order cancelled for %s at %s; no fill recorded",
                    market_id[:12],
                    token_id[:12],
                )
                return ExecutionResult(
                    False,
                    False,
                    order_id if order_id != "unknown" else None,
                    0.0,
                    "FOK order cancelled - order book too thin",
                )
            reconciled_fill = self._reconcile_live_order_fill(
                order_id=order_id,
                response=response,
                action="buy",
            )
            _, balance_spent = self._measure_live_balance_change(balance_before, expect_increase=False)
            live_position = self._sync_live_positions(
                dedup,
                market_id=market_id,
                token_id=token_id,
                side=side,
                expect_present=True,
            )
            position_shares = self._live_position_shares(live_position)
            position_spend = self._live_position_cost(live_position)
            actual_shares = reconciled_fill.shares if reconciled_fill is not None else position_shares
            actual_spend = (
                reconciled_fill.notional_usd
                if reconciled_fill is not None
                else (position_spend or balance_spent)
            )
            avg_price_from_position = _to_float((live_position or {}).get("avgPrice") or (live_position or {}).get("averagePrice"))
            fallback_price = avg_price_from_position or (reconciled_fill.avg_price if reconciled_fill is not None else 0.0)
            if actual_shares <= 0 and actual_spend > 0 and fallback_price > 0:
                actual_shares = actual_spend / fallback_price
            if actual_spend <= 0 and actual_shares > 0 and fallback_price > 0:
                actual_spend = actual_shares * fallback_price
            if actual_shares <= 0 or actual_spend <= 0:
                raise RuntimeError(
                    "live buy order posted but the fill could not be confirmed from exchange order, trade, balance, or position data"
                )
            gross_price = (
                (actual_spend / actual_shares)
                if actual_spend > 0 and actual_shares > 0
                else fallback_price
            )
            entry_economics, economics_reason = self._entry_economics_for_fill(
                token_id=token_id,
                fill=SimulatedFill(
                    spent_usd=actual_spend,
                    shares=actual_shares,
                    avg_price=gross_price,
                ),
                include_approval_cost=approval_requested,
                market_meta=getattr(event, "raw_market_metadata", None),
            )
            if entry_economics is None:
                raise RuntimeError(economics_reason or "entry fee model rejected the executed buy")

            actual_shares = entry_economics.net_shares
            actual_spend = entry_economics.total_cost_usd
            actual_price = entry_economics.effective_entry_price

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
                entry_economics=entry_economics,
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
                build_trade_entry_alert(
                    mode="live",
                    side=side,
                    shares=actual_shares,
                    price=actual_price,
                    total_usd=actual_spend,
                    confidence=confidence,
                    question=event.question,
                    market_url=_market_url_from_metadata(getattr(event, "raw_market_metadata", None)),
                    tracked_trader_name=getattr(event, "trader_name", None),
                    tracked_trader_address=getattr(event, "trader_address", None),
                ),
                kind="buy",
            )
            return ExecutionResult(True, False, order_id, actual_spend, "ok", shares=actual_shares, action="entry")
        except Exception as exc:
            dedup.release(market_id, token_id, side)
            logger.error("[LIVE ERROR] %s: %s", market_id, exc)
            send_alert(
                build_market_error_alert(
                    "live entry failed",
                    question=event.question,
                    market_url=_market_url_from_metadata(getattr(event, "raw_market_metadata", None)),
                    detail=str(exc),
                    tracked_trader_name=getattr(event, "trader_name", None),
                    tracked_trader_address=getattr(event, "trader_address", None),
                ),
                kind="error",
            )
            return ExecutionResult(False, False, None, 0.0, str(exc))

    def execute_exit(
        self,
        trade_id: str,
        market_id: str,
        token_id: str,
        side: str,
        event,
        dedup,
        reason_override: str | None = None,
    ) -> ExecutionResult:
        shadow = not use_real_money()
        if dedup.has_pending_position(market_id, token_id, side):
            return ExecutionResult(
                False,
                shadow,
                None,
                0.0,
                "matching position already has an order in-flight",
                action="exit",
            )
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

        observed_source_shares = float(getattr(event, "shares", 0.0) or 0.0)
        shares, exit_notional, pnl, exit_fraction = self._exit_trade_math(
            position,
            entries,
            exit_price,
            observed_source_shares,
        )
        if shares <= 0 or exit_notional <= 0:
            return ExecutionResult(
                False,
                shadow,
                None,
                0.0,
                "matching position was found, but its size could not be computed for exit",
                action="exit",
            )

        if not getattr(event, "token_id", None):
            event.token_id = token_id or str(position.get("token_id") or "")
        ok, market_data_reason = self.refresh_event_market_data(event)
        if not ok:
            return ExecutionResult(
                False,
                shadow,
                None,
                0.0,
                market_data_reason or "execution market data refresh failed",
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
                exit_fraction=exit_fraction,
                reason_override=reason_override,
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
            exit_fraction=exit_fraction,
            reason_override=reason_override,
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
                f"""
                SELECT id, trade_id, market_id, question, trader_address, side, token_id,
                       price_at_signal, signal_size_usd, actual_entry_price,
                       actual_entry_shares, actual_entry_size_usd, source_shares, confidence,
                       raw_confidence, signal_mode, trader_score, market_score,
                       decision_context_json, placed_at, kelly_fraction, market_close_ts, remaining_entry_shares,
                       remaining_entry_size_usd, remaining_source_shares,
                       exit_fee_rate_bps, exit_fee_usd, exit_fixed_cost_usd,
                       exit_gross_price, exit_gross_shares, exit_gross_size_usd,
                       realized_exit_shares, realized_exit_size_usd, realized_exit_pnl_usd,
                       partial_exit_count
                FROM trade_log
                WHERE market_id=?
                  AND real_money=?
                  AND {OPEN_EXECUTED_ENTRY_SQL}
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
    def _entry_open_shares(entry: dict) -> float:
        remaining = float(entry.get("remaining_entry_shares") or 0.0)
        if remaining > 0:
            return remaining
        actual = float(entry.get("actual_entry_shares") or 0.0)
        if actual > 0:
            return actual
        size = float(entry.get("remaining_entry_size_usd") or 0.0) or float(entry.get("actual_entry_size_usd") or 0.0) or float(entry.get("signal_size_usd") or 0.0)
        price = float(entry.get("actual_entry_price") or 0.0) or float(entry.get("price_at_signal") or 0.0)
        return (size / price) if size > 0 and price > 0 else 0.0

    @staticmethod
    def _entry_open_size(entry: dict) -> float:
        remaining = float(entry.get("remaining_entry_size_usd") or 0.0)
        if remaining > 0:
            return remaining
        return float(entry.get("actual_entry_size_usd") or 0.0) or float(entry.get("signal_size_usd") or 0.0)

    @staticmethod
    def _entry_open_source_shares(entry: dict) -> float:
        remaining = float(entry.get("remaining_source_shares") or 0.0)
        if remaining > 0:
            return remaining
        return float(entry.get("source_shares") or 0.0)

    def _exit_trade_math(
        self,
        position: dict,
        entries: list[dict],
        exit_price: float,
        observed_source_shares: float,
    ) -> tuple[float, float, float, float]:
        total_size_usd = sum(self._entry_open_size(entry) for entry in entries)
        if total_size_usd <= 0:
            total_size_usd = float(position.get("size_usd") or 0.0)

        total_actual_shares = sum(self._entry_open_shares(entry) for entry in entries)
        if total_actual_shares <= 0:
            entry_price = float(position.get("avg_price") or 0.0)
            if entry_price > 0 and total_size_usd > 0:
                total_actual_shares = total_size_usd / entry_price

        total_source_shares = sum(self._entry_open_source_shares(entry) for entry in entries)
        if total_actual_shares <= 0 or total_size_usd <= 0:
            return 0.0, 0.0, 0.0, 0.0

        if observed_source_shares > 0 and total_source_shares > 0:
            raw_fraction = observed_source_shares / total_source_shares
            exit_fraction = 1.0 if raw_fraction >= 0.90 else min(raw_fraction, 1.0)
        else:
            exit_fraction = 1.0

        shares = round(total_actual_shares * exit_fraction, 6)
        exit_notional = round(shares * exit_price, 6)
        pnl = round(exit_notional - (total_size_usd * exit_fraction), 2)
        return shares, exit_notional, pnl, exit_fraction

    @staticmethod
    def _exit_reason(exit_fraction: float) -> str:
        if exit_fraction >= 1.0 - 1e-9:
            return "watched trader exited, so we closed our matching position"
        return (
            f"watched trader sold {exit_fraction * 100:.1f}% of the source position, "
            "so we reduced our matching position"
        )

    def _refresh_position_from_trade_log(
        self,
        conn,
        *,
        market_id: str,
        token_id: str,
        side: str,
        real_money: bool,
    ) -> dict[str, float] | None:
        mode_flag = 1 if real_money else 0
        normalized_token = str(token_id or "").strip()
        normalized_side = str(side or "").strip().lower()

        if normalized_token:
            row = conn.execute(
                f"""
                SELECT
                    SUM({remaining_entry_size_expr()}) AS size_usd,
                    SUM({remaining_entry_shares_expr()}) AS shares,
                    MIN(placed_at) AS entered_at
                FROM trade_log
                WHERE market_id=?
                  AND real_money=?
                  AND token_id=?
                  AND {OPEN_EXECUTED_ENTRY_SQL}
                """,
                (market_id, mode_flag, normalized_token),
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT
                    SUM({remaining_entry_size_expr()}) AS size_usd,
                    SUM({remaining_entry_shares_expr()}) AS shares,
                    MIN(placed_at) AS entered_at
                FROM trade_log
                WHERE market_id=?
                  AND real_money=?
                  AND LOWER(side)=?
                  AND {OPEN_EXECUTED_ENTRY_SQL}
                """,
                (market_id, mode_flag, normalized_side),
            ).fetchone()

        size_usd = float(row["size_usd"] or 0.0)
        shares = float(row["shares"] or 0.0)
        if size_usd > 1e-9 and shares > 1e-9:
            avg_price = size_usd / shares
            conn.execute(
                "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
                (
                    market_id,
                    normalized_side,
                    size_usd,
                    avg_price,
                    normalized_token,
                    int(row["entered_at"] or time.time()),
                    mode_flag,
                ),
            )
            return {
                "size_usd": round(size_usd, 6),
                "shares": round(shares, 6),
                "avg_price": round(avg_price, 6),
            }

        if normalized_token:
            conn.execute(
                "DELETE FROM positions WHERE market_id=? AND token_id=? AND real_money=?",
                (market_id, normalized_token, mode_flag),
            )
        else:
            conn.execute(
                "DELETE FROM positions WHERE market_id=? AND LOWER(side)=? AND real_money=?",
                (market_id, normalized_side, mode_flag),
            )
        return None

    def _finalize_exit(
        self,
        *,
        entries: list[dict],
        position: dict,
        real_money: bool,
        exit_trade_id: str,
        exit_price: float,
        exit_fraction: float,
        exit_shares: float,
        exit_notional: float,
        exit_reason: str,
        exit_order_id: str | None,
        exit_economics: ExitEconomics | None = None,
        market_id: str,
        trader_address: str,
        dedup,
        refresh_position_from_trade_log: bool,
    ) -> tuple[float, float, float]:
        pnl_column = "actual_pnl_usd" if real_money else "shadow_pnl_usd"
        conn = get_conn()
        now_ts = int(time.time())
        total_shares = 0.0
        total_exit_notional = 0.0
        total_pnl = 0.0
        fraction = min(max(exit_fraction, 0.0), 1.0)
        active_entries = [
            entry
            for entry in entries
            if self._entry_open_shares(entry) > 1e-9 and self._entry_open_size(entry) > 1e-9
        ]

        remaining_exit_shares = float(exit_shares)
        remaining_exit_notional = float(exit_notional)
        remaining_exit_gross_shares = float(exit_economics.gross_shares) if exit_economics is not None else float(exit_shares)
        remaining_exit_gross_notional = (
            float(exit_economics.gross_notional_usd) if exit_economics is not None else float(exit_notional)
        )
        remaining_exit_fee_usd = float(exit_economics.exit_fee_usd) if exit_economics is not None else 0.0
        remaining_exit_fixed_cost = float(exit_economics.fixed_cost_usd) if exit_economics is not None else 0.0
        total_exit_gross_shares = remaining_exit_gross_shares
        total_exit_gross_notional = remaining_exit_gross_notional
        total_exit_fee_usd = remaining_exit_fee_usd
        total_exit_fixed_cost = remaining_exit_fixed_cost
        exit_fee_rate_bps = int(exit_economics.fee_rate_bps) if exit_economics is not None else 0
        for index, entry in enumerate(active_entries):
            open_shares = self._entry_open_shares(entry)
            open_size = self._entry_open_size(entry)
            open_source_shares = self._entry_open_source_shares(entry)
            if open_shares <= 1e-9 or open_size <= 1e-9:
                continue

            is_last = index == len(active_entries) - 1
            if is_last:
                entry_exit_shares = min(open_shares, max(remaining_exit_shares, 0.0))
                entry_exit_notional = max(round(remaining_exit_notional, 6), 0.0)
                entry_exit_gross_shares = max(round(remaining_exit_gross_shares, 6), 0.0)
                entry_exit_gross_notional = max(round(remaining_exit_gross_notional, 6), 0.0)
                entry_exit_fee_usd = max(round(remaining_exit_fee_usd, 6), 0.0)
                entry_exit_fixed_cost = max(round(remaining_exit_fixed_cost, 6), 0.0)
            else:
                entry_exit_shares = min(open_shares, round(open_shares * fraction, 6))
                share_ratio = (entry_exit_shares / exit_shares) if exit_shares > 0 else 0.0
                entry_exit_notional = max(round(exit_notional * share_ratio, 6), 0.0)
                entry_exit_gross_shares = max(round(total_exit_gross_shares * share_ratio, 6), 0.0)
                entry_exit_gross_notional = max(round(total_exit_gross_notional * share_ratio, 6), 0.0)
                entry_exit_fee_usd = max(round(total_exit_fee_usd * share_ratio, 6), 0.0)
                entry_exit_fixed_cost = max(round(total_exit_fixed_cost * share_ratio, 6), 0.0)
            if entry_exit_shares <= 1e-9:
                continue

            close_ratio = min(entry_exit_shares / open_shares, 1.0)
            closed_cost = round(open_size * close_ratio, 6)
            closed_source_shares = round(open_source_shares * close_ratio, 6)
            remaining_shares = max(round(open_shares - entry_exit_shares, 6), 0.0)
            remaining_size = max(round(open_size - closed_cost, 6), 0.0)
            remaining_source_shares = max(round(open_source_shares - closed_source_shares, 6), 0.0)
            realized_exit_shares = round(float(entry.get("realized_exit_shares") or 0.0) + entry_exit_shares, 6)
            realized_exit_size = round(float(entry.get("realized_exit_size_usd") or 0.0) + entry_exit_notional, 6)
            realized_exit_pnl = round(
                float(entry.get("realized_exit_pnl_usd") or 0.0) + entry_exit_notional - closed_cost,
                6,
            )
            cumulative_exit_fee_usd = round(float(entry.get("exit_fee_usd") or 0.0) + entry_exit_fee_usd, 6)
            cumulative_exit_fixed_cost = round(
                float(entry.get("exit_fixed_cost_usd") or 0.0) + entry_exit_fixed_cost,
                6,
            )
            cumulative_exit_gross_shares = round(
                float(entry.get("exit_gross_shares") or 0.0) + entry_exit_gross_shares,
                6,
            )
            cumulative_exit_gross_size = round(
                float(entry.get("exit_gross_size_usd") or 0.0) + entry_exit_gross_notional,
                6,
            )
            cumulative_exit_price = round(realized_exit_size / realized_exit_shares, 6) if realized_exit_shares > 0 else 0.0
            cumulative_exit_gross_price = (
                round(cumulative_exit_gross_size / cumulative_exit_gross_shares, 6)
                if cumulative_exit_gross_shares > 0
                else 0.0
            )
            prior_partial_count = int(entry.get("partial_exit_count") or 0)
            partial_count = prior_partial_count + (1 if remaining_shares > 1e-9 else 0)
            is_fully_closed = remaining_shares <= 1e-9 or remaining_size <= 1e-9

            total_shares += entry_exit_shares
            total_exit_notional += entry_exit_notional
            total_pnl += entry_exit_notional - closed_cost
            remaining_exit_shares = max(round(remaining_exit_shares - entry_exit_shares, 6), 0.0)
            remaining_exit_notional = max(round(remaining_exit_notional - entry_exit_notional, 6), 0.0)
            remaining_exit_gross_shares = max(round(remaining_exit_gross_shares - entry_exit_gross_shares, 6), 0.0)
            remaining_exit_gross_notional = max(round(remaining_exit_gross_notional - entry_exit_gross_notional, 6), 0.0)
            remaining_exit_fee_usd = max(round(remaining_exit_fee_usd - entry_exit_fee_usd, 6), 0.0)
            remaining_exit_fixed_cost = max(round(remaining_exit_fixed_cost - entry_exit_fixed_cost, 6), 0.0)

            if is_fully_closed:
                conn.execute(
                    f"""
                    UPDATE trade_log
                    SET exited_at=?,
                        exit_trade_id=?,
                        exit_price=?,
                        exit_shares=?,
                        exit_size_usd=?,
                        exit_fee_rate_bps=?,
                        exit_fee_usd=?,
                        exit_fixed_cost_usd=?,
                        exit_gross_price=?,
                        exit_gross_shares=?,
                        exit_gross_size_usd=?,
                        exit_order_id=?,
                        exit_reason=?,
                        resolved_at=COALESCE(resolved_at, ?),
                        remaining_entry_shares=0,
                        remaining_entry_size_usd=0,
                        remaining_source_shares=0,
                        realized_exit_shares=?,
                        realized_exit_size_usd=?,
                        realized_exit_pnl_usd=?,
                        partial_exit_count=?,
                        {pnl_column}=?
                    WHERE id=?
                    """,
                    (
                        now_ts,
                        exit_trade_id,
                        cumulative_exit_price,
                        realized_exit_shares,
                        realized_exit_size,
                        exit_fee_rate_bps or int(entry.get("exit_fee_rate_bps") or 0),
                        cumulative_exit_fee_usd,
                        cumulative_exit_fixed_cost,
                        cumulative_exit_gross_price,
                        cumulative_exit_gross_shares,
                        cumulative_exit_gross_size,
                        exit_order_id,
                        exit_reason,
                        now_ts,
                        realized_exit_shares,
                        realized_exit_size,
                        realized_exit_pnl,
                        partial_count,
                        round(realized_exit_pnl, 2),
                        int(entry["id"]),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE trade_log
                    SET exit_trade_id=?,
                        exit_price=?,
                        exit_shares=?,
                        exit_size_usd=?,
                        exit_fee_rate_bps=?,
                        exit_fee_usd=?,
                        exit_fixed_cost_usd=?,
                        exit_gross_price=?,
                        exit_gross_shares=?,
                        exit_gross_size_usd=?,
                        exit_order_id=?,
                        exit_reason=?,
                        remaining_entry_shares=?,
                        remaining_entry_size_usd=?,
                        remaining_source_shares=?,
                        realized_exit_shares=?,
                        realized_exit_size_usd=?,
                        realized_exit_pnl_usd=?,
                        partial_exit_count=?
                    WHERE id=?
                    """,
                    (
                        exit_trade_id,
                        cumulative_exit_price,
                        realized_exit_shares,
                        realized_exit_size,
                        exit_fee_rate_bps or int(entry.get("exit_fee_rate_bps") or 0),
                        cumulative_exit_fee_usd,
                        cumulative_exit_fixed_cost,
                        cumulative_exit_gross_price,
                        cumulative_exit_gross_shares,
                        cumulative_exit_gross_size,
                        exit_order_id,
                        exit_reason,
                        remaining_shares,
                        remaining_size,
                        remaining_source_shares,
                        realized_exit_shares,
                        realized_exit_size,
                        realized_exit_pnl,
                        partial_count,
                        int(entry["id"]),
                    ),
                )

        if refresh_position_from_trade_log:
            self._refresh_position_from_trade_log(
                conn,
                market_id=market_id,
                token_id=str(position.get("token_id") or ""),
                side=str(position.get("side") or ""),
                real_money=real_money,
            )
        conn.commit()
        conn.close()
        if refresh_position_from_trade_log:
            dedup.load_from_db(rebuild_shadow_positions=False)
        else:
            dedup.release(
                market_id,
                str(position.get("token_id") or ""),
                str(position.get("side") or ""),
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
        exit_fraction: float,
        reason_override: str | None = None,
    ) -> ExecutionResult:
        fill, reject_reason = self._simulate_shadow_sell(getattr(event, "raw_orderbook", None), shares)
        if fill is None:
            dedup.release(
                market_id,
                str(position.get("token_id") or token_id or ""),
                str(position.get("side") or event.side or ""),
            )
            return ExecutionResult(False, True, None, 0.0, reject_reason or "shadow simulation sell failed", action="exit")

        exit_economics, economics_reason = self._exit_economics_for_fill(
            token_id=token_id or str(position.get("token_id") or ""),
            gross_shares=fill.shares,
            gross_notional_usd=fill.spent_usd,
            gross_price=fill.avg_price,
            market_meta=getattr(event, "raw_market_metadata", None),
        )
        if exit_economics is None:
            dedup.release(
                market_id,
                str(position.get("token_id") or token_id or ""),
                str(position.get("side") or event.side or ""),
            )
            return ExecutionResult(False, True, None, 0.0, economics_reason or "exit fee model rejected the sell", action="exit")

        reason = reason_override or self._exit_reason(exit_fraction)
        shares, exit_notional, pnl = self._finalize_exit(
            entries=entries,
            position=position,
            real_money=False,
            exit_trade_id=trade_id,
            exit_price=exit_economics.effective_exit_price,
            exit_fraction=exit_fraction,
            exit_shares=fill.shares,
            exit_notional=exit_economics.net_proceeds_usd,
            exit_reason=reason,
            exit_order_id=None,
            exit_economics=exit_economics,
            market_id=market_id,
            trader_address=event.trader_address,
            dedup=dedup,
            refresh_position_from_trade_log=True,
        )
        logger.info(
            "[SHADOW EXIT] %s | %s | sold %.3f shares | est. pnl=%+.2f",
            event.question[:60],
            event.side.upper(),
            shares,
            pnl,
        )
        send_alert(
            build_trade_exit_alert(
                mode="shadow",
                side=event.side,
                shares=shares,
                price=exit_economics.effective_exit_price,
                total_usd=exit_notional,
                pnl_usd=pnl,
                question=event.question,
                market_url=_market_url_from_metadata(getattr(event, "raw_market_metadata", None)),
                tracked_trader_name=getattr(event, "trader_name", None),
                tracked_trader_address=getattr(event, "trader_address", None),
            ),
            kind="exit",
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
        exit_fraction: float,
        reason_override: str | None = None,
    ) -> ExecutionResult:
        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL

            self._ensure_live_token_allowance(token_id or str(position.get("token_id") or ""))
            balance_before = self.get_usdc_balance()
            total_open_shares = sum(self._entry_open_shares(entry) for entry in entries)
            order = MarketOrderArgs(
                token_id=token_id or str(position.get("token_id") or ""),
                amount=shares,
                side=SELL,
            )
            signed = self._clob.create_market_order(order)
            response = self._clob.post_order(signed, OrderType.FOK)
            order_id = self._extract_order_id(response)
            if self._is_unfilled_fok_response(response):
                dedup.release(
                    market_id,
                    str(position.get("token_id") or token_id or ""),
                    str(position.get("side") or ""),
                )
                logger.info(
                    "[LIVE EXIT] FOK order cancelled for %s at %s; no exit fill recorded",
                    market_id[:12],
                    str(position.get("token_id") or token_id or "")[:12],
                )
                return ExecutionResult(
                    False,
                    False,
                    order_id if order_id != "unknown" else None,
                    0.0,
                    "FOK order cancelled - order book too thin",
                    action="exit",
                )
            reconciled_fill = self._reconcile_live_order_fill(
                order_id=order_id,
                response=response,
                action="sell",
            )
            actual_exit_shares = reconciled_fill.shares if reconciled_fill is not None else shares
            _, balance_gained = self._measure_live_balance_change(balance_before, expect_increase=True)
            remaining_position = None
            remaining_shares = 0.0
            target_token = token_id or str(position.get("token_id") or "")
            target_side = str(position.get("side") or event.side or "")
            expected_remaining_shares = max(total_open_shares - actual_exit_shares, 0.0)
            for attempt in range(LIVE_SYNC_ATTEMPTS):
                rows = self._fetch_live_positions()
                if rows is not None:
                    dedup.sync_positions_from_rows(rows)
                remaining_position = self._match_live_position(rows or [], market_id, target_token, target_side)
                remaining_shares = self._live_position_shares(remaining_position)

                if expected_remaining_shares <= 1e-6:
                    if remaining_shares <= 1e-6:
                        break
                else:
                    tolerance = max(0.02, expected_remaining_shares * 0.2)
                    if remaining_shares > 1e-6 and abs(remaining_shares - expected_remaining_shares) <= tolerance:
                        break

                if attempt < LIVE_SYNC_ATTEMPTS - 1:
                    time.sleep(LIVE_SYNC_DELAY_S * (attempt + 1))
            else:
                if reconciled_fill is None:
                    logger.error(
                        "[LIVE EXIT] ambiguous exit state for %s: remaining_shares=%.3f expected=%.3f",
                        market_id[:12],
                        remaining_shares,
                        expected_remaining_shares,
                    )
                    send_alert(
                        build_lines(
                            append_tracking_detail(
                                "live exit sync is ambiguous",
                                getattr(event, "trader_name", None),
                                getattr(event, "trader_address", None),
                            ),
                            build_market_line(
                                event.question,
                                _market_url_from_metadata(getattr(event, "raw_market_metadata", None)),
                            ),
                            (
                                f"remaining {_to_float(remaining_shares):.3f} shares; "
                                f"expected {_to_float(expected_remaining_shares):.3f}"
                            ),
                        ),
                        kind="warning",
                    )
                    dedup.release(
                        market_id,
                        str(position.get("token_id") or token_id or ""),
                        str(position.get("side") or ""),
                    )
                    return ExecutionResult(
                        False,
                        False,
                        order_id if order_id != "unknown" else None,
                        0.0,
                        "exit state ambiguous after sync timeout",
                        action="exit",
                    )
                logger.warning(
                    "[LIVE EXIT] position sync timed out for %s but fill reconciliation succeeded; committing exit",
                    market_id[:12],
                )

            actual_exit_shares = (
                reconciled_fill.shares
                if reconciled_fill is not None
                else max(total_open_shares - remaining_shares, 0.0)
            )
            actual_exit_notional = (
                reconciled_fill.notional_usd
                if reconciled_fill is not None
                else balance_gained
            )
            if actual_exit_shares <= 0 or actual_exit_notional <= 0:
                raise RuntimeError(
                    "live exit order posted but the realized fill could not be confirmed from exchange order, trade, balance, or position data"
                )
            target_token = token_id or str(position.get("token_id") or "")
            gross_exit_price = (
                reconciled_fill.avg_price
                if reconciled_fill is not None and reconciled_fill.avg_price > 0
                else (
                    (actual_exit_notional / actual_exit_shares)
                    if actual_exit_notional > 0 and actual_exit_shares > 0
                    else exit_price
                )
            )
            if reconciled_fill is not None:
                exit_economics, economics_reason = self._exit_economics_for_fill(
                    token_id=target_token,
                    gross_shares=actual_exit_shares,
                    gross_notional_usd=actual_exit_notional,
                    gross_price=gross_exit_price,
                    market_meta=getattr(event, "raw_market_metadata", None),
                )
            else:
                fee_rate_bps, fee_reason = self.get_fee_rate_bps(
                    target_token,
                    market_meta=getattr(event, "raw_market_metadata", None),
                )
                if fee_reason:
                    raise RuntimeError(fee_reason)
                estimated_fee_usd = taker_fee_usd(actual_exit_shares, gross_exit_price, int(fee_rate_bps or 0))
                exit_economics = build_exit_economics(
                    gross_price=gross_exit_price,
                    gross_shares=actual_exit_shares,
                    gross_notional_usd=actual_exit_notional + estimated_fee_usd,
                    fee_rate_bps=int(fee_rate_bps or 0),
                    fixed_cost_usd=exit_fixed_cost_usd(),
                )
                economics_reason = None
            if exit_economics is None:
                raise RuntimeError(economics_reason or "exit fee model rejected the executed sell")

            actual_exit_price = exit_economics.effective_exit_price
            actual_exit_notional = exit_economics.net_proceeds_usd
            reason = reason_override or self._exit_reason(exit_fraction)
            shares, exit_notional, pnl = self._finalize_exit(
                entries=entries,
                position=position,
                real_money=True,
                exit_trade_id=trade_id,
                exit_price=actual_exit_price,
                exit_fraction=exit_fraction,
                exit_shares=actual_exit_shares,
                exit_notional=actual_exit_notional,
                exit_reason=reason,
                exit_order_id=order_id,
                exit_economics=exit_economics,
                market_id=market_id,
                trader_address=event.trader_address,
                dedup=dedup,
                refresh_position_from_trade_log=False,
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
                build_trade_exit_alert(
                    mode="live",
                    side=event.side,
                    shares=shares,
                    price=actual_exit_price,
                    total_usd=exit_notional,
                    pnl_usd=pnl,
                    question=event.question,
                    market_url=_market_url_from_metadata(getattr(event, "raw_market_metadata", None)),
                    tracked_trader_name=getattr(event, "trader_name", None),
                    tracked_trader_address=getattr(event, "trader_address", None),
                ),
                kind="exit",
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
            send_alert(
                build_market_error_alert(
                    "live exit failed",
                    question=event.question,
                    market_url=_market_url_from_metadata(getattr(event, "raw_market_metadata", None)),
                    detail=str(exc),
                    tracked_trader_name=getattr(event, "trader_name", None),
                    tracked_trader_address=getattr(event, "trader_address", None),
                ),
                kind="error",
            )
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
    entry_economics: EntryEconomics | None = None,
    trader_f=None,
    market_f=None,
    event=None,
    signal=None,
) -> int:
    market_price_1h_ago = (
        float(market_f.price_1h_ago)
        if market_f and market_f.price_1h_ago is not None
        else 0.0
    )
    market_volume_7d_avg = (
        float(market_f.volume_7d_avg_usd)
        if market_f and market_f.volume_7d_avg_usd is not None
        else None
    )
    spread = (
        (market_f.best_ask - market_f.best_bid) / market_f.mid
        if market_f and market_f.mid > 0
        else None
    )
    momentum = (
        abs(market_f.mid - market_price_1h_ago) / market_price_1h_ago
        if market_f and market_price_1h_ago > 0
        else None
    )
    volume_trend = (
        market_f.volume_24h_usd / (market_volume_7d_avg + 1e-6)
        if market_f and market_f.volume_24h_usd is not None and market_volume_7d_avg is not None
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
    source_action = str(getattr(event, "action", "") or "").strip().lower()
    is_executed_buy = (
        not skipped
        and source_action == "buy"
        and actual_entry_price is not None
        and actual_entry_shares is not None
        and actual_entry_size_usd is not None
    )
    entry_fee_rate_bps = float(entry_economics.fee_rate_bps) if entry_economics is not None else 0.0
    entry_fee_usd = float(entry_economics.entry_fee_usd) if entry_economics is not None else 0.0
    entry_fee_shares = float(entry_economics.entry_fee_shares) if entry_economics is not None else 0.0
    entry_fixed_cost = float(entry_economics.fixed_cost_usd) if entry_economics is not None else 0.0
    entry_gross_price = float(entry_economics.gross_price) if entry_economics is not None else None
    entry_gross_shares = float(entry_economics.gross_shares) if entry_economics is not None else None
    entry_gross_size_usd = float(entry_economics.gross_spent_usd) if entry_economics is not None else None
    segment_id = None
    policy_id = None
    policy_bundle_version = 0
    promotion_epoch_id = 0
    experiment_arm = DEFAULT_EXPERIMENT_ARM
    expected_edge = None
    expected_fill_cost_usd = None
    expected_exit_fee_usd = None
    expected_close_fixed_cost_usd = None
    if isinstance(signal, dict):
        segment_meta = signal.get("segment") if isinstance(signal.get("segment"), dict) else {}
        segment_id = signal.get("segment_id") or segment_meta.get("segment_id")
        policy_id = signal.get("policy_id") or segment_meta.get("policy_id")
        try:
            policy_bundle_version = int(
                signal.get("policy_bundle_version")
                or segment_meta.get("policy_bundle_version")
                or 0
            )
        except (TypeError, ValueError):
            policy_bundle_version = 0
        try:
            promotion_epoch_id = int(signal.get("promotion_epoch_id") or 0)
        except (TypeError, ValueError):
            promotion_epoch_id = 0
        signal_arm = str(signal.get("experiment_arm") or segment_meta.get("experiment_arm") or "").strip().lower()
        if signal_arm:
            experiment_arm = signal_arm
        expected_edge_value = signal.get("edge")
        try:
            expected_edge = float(expected_edge_value) if expected_edge_value is not None else None
        except (TypeError, ValueError):
            expected_edge = None
    event_segment_id = getattr(event, "segment_id", None)
    event_policy_id = getattr(event, "policy_id", None)
    event_policy_bundle_version = getattr(event, "policy_bundle_version", None)
    event_promotion_epoch_id = getattr(event, "promotion_epoch_id", None)
    event_experiment_arm = str(getattr(event, "experiment_arm", "") or "").strip().lower()
    if segment_id is None and event_segment_id is not None:
        segment_id = event_segment_id
    if policy_id is None and event_policy_id is not None:
        policy_id = event_policy_id
    try:
        policy_bundle_version = int(event_policy_bundle_version) if event_policy_bundle_version is not None else policy_bundle_version
    except (TypeError, ValueError):
        pass
    try:
        promotion_epoch_id = int(event_promotion_epoch_id) if event_promotion_epoch_id is not None else promotion_epoch_id
    except (TypeError, ValueError):
        pass
    if event_experiment_arm:
        experiment_arm = event_experiment_arm
    if entry_economics is not None:
        expected_fill_cost_usd = round(
            float(entry_economics.entry_fee_usd) + float(entry_economics.fixed_cost_usd),
            6,
        )
        expected_exit_fee_usd = float(entry_economics.expected_exit_fee_usd)
        expected_close_fixed_cost_usd = float(entry_economics.expected_close_fixed_cost_usd)
        try:
            expected_edge = round(float(confidence) - float(entry_economics.sizing_effective_price), 6)
        except (TypeError, ValueError):
            expected_edge = expected_edge
    if expected_edge is None:
        try:
            expected_edge = round(float(confidence) - float(price), 6)
        except (TypeError, ValueError):
            expected_edge = None
    if promotion_epoch_id <= 0:
        promotion_epoch_id = current_promotion_epoch_id()
    values = [
        trade_id,
        market_id,
        question,
        _market_url_from_metadata(getattr(event, "raw_market_metadata", None)),
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
        entry_fee_rate_bps,
        entry_fee_usd,
        entry_fee_shares,
        entry_fixed_cost,
        entry_gross_price,
        entry_gross_shares,
        entry_gross_size_usd,
        confidence,
        signal.get("raw_confidence") if isinstance(signal, dict) else None,
        kelly_f,
        signal.get("mode") if isinstance(signal, dict) else None,
        segment_id,
        policy_id,
        policy_bundle_version,
        promotion_epoch_id,
        experiment_arm,
        expected_edge,
        expected_fill_cost_usd,
        expected_exit_fee_usd,
        expected_close_fixed_cost_usd,
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
        float(actual_entry_shares or 0.0) if is_executed_buy else 0.0,
        float(actual_entry_size_usd or 0.0) if is_executed_buy else 0.0,
        float(getattr(event, "shares", 0.0) or 0.0) if is_executed_buy else 0.0,
        0.0,
        0.0,
        0.0,
        0,
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
            trade_id, market_id, question, market_url, trader_address, trader_name, side,
            token_id, source_action, source_ts, source_ts_raw, observed_at, poll_started_at,
            market_close_ts, metadata_fetched_at, orderbook_fetched_at, source_latency_s,
            observation_latency_s, processing_latency_s, source_shares, source_amount_usd,
            source_trade_json, market_metadata_json, orderbook_json, snapshot_json,
            price_at_signal, signal_size_usd, actual_entry_price, actual_entry_shares,
            actual_entry_size_usd, entry_fee_rate_bps, entry_fee_usd, entry_fee_shares,
            entry_fixed_cost_usd, entry_gross_price, entry_gross_shares, entry_gross_size_usd,
            confidence, raw_confidence, kelly_fraction,
            signal_mode, segment_id, policy_id, policy_bundle_version, promotion_epoch_id,
            experiment_arm, expected_edge, expected_fill_cost_usd, expected_exit_fee_usd,
            expected_close_fixed_cost_usd, belief_prior, belief_blend, belief_evidence, trader_score,
            market_score, market_veto, real_money, order_id, skipped, skip_reason, placed_at,
            remaining_entry_shares, remaining_entry_size_usd, remaining_source_shares,
            realized_exit_shares, realized_exit_size_usd, realized_exit_pnl_usd, partial_exit_count,
            f_trader_win_rate, f_trader_n_trades, f_conviction_ratio,
            f_trader_volume_usd, f_trader_avg_size_usd, f_account_age_days, f_consistency,
            f_trader_diversity, f_days_to_res, f_price, f_spread_pct, f_momentum_1h,
            f_volume_24h_usd, f_volume_7d_avg_usd, f_volume_trend, f_oi_usd, f_top_holder_pct,
            f_bid_depth_usd, f_ask_depth_usd, market_components_json, decision_context_json
        ) VALUES ({placeholders})
        """,
        values,
    )
    row_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()
    return row_id
