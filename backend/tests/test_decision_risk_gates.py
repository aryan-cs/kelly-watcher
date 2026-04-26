from __future__ import annotations

import sys
import time
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

if "apscheduler.schedulers.background" not in sys.modules:
    apscheduler_module = types.ModuleType("apscheduler")
    executors_module = types.ModuleType("apscheduler.executors")
    pool_module = types.ModuleType("apscheduler.executors.pool")
    schedulers_module = types.ModuleType("apscheduler.schedulers")
    background_module = types.ModuleType("apscheduler.schedulers.background")

    class _SchedulerThreadPoolExecutor:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class _BackgroundScheduler:
        def add_job(self, *args, **kwargs) -> None:
            return None

        def start(self) -> None:
            return None

    pool_module.ThreadPoolExecutor = _SchedulerThreadPoolExecutor
    executors_module.pool = pool_module
    background_module.BackgroundScheduler = _BackgroundScheduler
    apscheduler_module.executors = executors_module
    schedulers_module.background = background_module
    apscheduler_module.schedulers = schedulers_module
    sys.modules["apscheduler"] = apscheduler_module
    sys.modules["apscheduler.executors"] = executors_module
    sys.modules["apscheduler.executors.pool"] = pool_module
    sys.modules["apscheduler.schedulers"] = schedulers_module
    sys.modules["apscheduler.schedulers.background"] = background_module

import kelly_watcher.main as main


class DecisionRiskGateTest(unittest.TestCase):
    def test_process_event_rechecks_final_quote_against_signal_gates(self) -> None:
        event = SimpleNamespace(
            trade_id="trade-final-quote",
            market_id="market-final-quote",
            question="Will final quote still be acceptable?",
            side="yes",
            action="buy",
            price=0.70,
            shares=10.0,
            size_usd=7.0,
            token_id="token-final-quote",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=int(time.time()),
            close_time="2030-01-01T00:00:00Z",
            market_close_ts=1_893_456_000,
            watch_tier="hot",
            snapshot={
                "best_bid": 0.68,
                "best_ask": 0.70,
                "mid": 0.69,
                "bid_depth_usd": 500.0,
                "ask_depth_usd": 500.0,
            },
            raw_orderbook={"asks": [{"price": "0.70", "size": "100"}]},
            raw_market_metadata={},
        )

        class _Engine:
            def __init__(self) -> None:
                self.prices: list[float] = []

            def sizing_mode(self) -> str:
                return "heuristic"

            def evaluate(self, _trader_f, market_f, _order_size_usd, **_kwargs) -> dict:
                self.prices.append(market_f.execution_price)
                if market_f.execution_price >= 0.75:
                    return {
                        "confidence": 0.82,
                        "passed": False,
                        "veto": None,
                        "reason": "heuristic entry price 0.780 outside band 0.650-0.750",
                        "mode": "heuristic",
                        "min_confidence": 0.55,
                        "trader": {"score": 0.9},
                        "market": {"score": 0.9},
                    }
                return {
                    "confidence": 0.82,
                    "passed": True,
                    "veto": None,
                    "reason": "passed heuristic threshold",
                    "mode": "heuristic",
                    "min_confidence": 0.55,
                    "trader": {"score": 0.9},
                    "market": {"score": 0.9},
                }

        class _Executor:
            def __init__(self) -> None:
                self.economics_calls = 0
                self.log_skip = Mock()
                self.execute = Mock()

            def refresh_event_market_data(self, _event):
                return True, None

            def estimate_entry_fill(self, _raw_book, amount):
                return SimpleNamespace(spent_usd=float(amount), shares=float(amount) / 0.70, avg_price=0.70), None

            def estimate_entry_economics(self, *, token_id, fill, market_meta=None):
                self.economics_calls += 1
                effective_price = 0.70 if self.economics_calls == 1 else 0.78
                return (
                    SimpleNamespace(
                        sizing_effective_price=effective_price,
                        total_cost_usd=fill.spent_usd,
                        effective_entry_price=effective_price,
                        net_shares=fill.shares,
                    ),
                    None,
                )

            def entry_risk_block_reason(self, **_kwargs):
                return None

        engine = _Engine()
        executor = _Executor()
        dedup = SimpleNamespace(gate=Mock(return_value=(True, None)), mark_seen=Mock())
        trust_state = SimpleNamespace(skip_reason=None, as_dict=lambda: {"tier": "trusted"})
        size_calls: list[tuple] = []

        def fake_size_signal(*args, **kwargs):
            size_calls.append((args, kwargs))
            return {"dollar_size": 10.0 if len(size_calls) == 1 else 20.0, "kelly_f": 0.02, "reason": "ok"}

        with patch.object(main, "_emit_event"), patch.object(
            main, "get_trader_features", return_value=SimpleNamespace()
        ), patch.object(main, "size_signal", side_effect=fake_size_signal), patch.object(
            main, "get_wallet_trust_state", return_value=trust_state
        ), patch.object(
            main, "apply_wallet_trust_sizing", side_effect=lambda sizing, *_args, **_kwargs: sizing
        ), patch.object(
            main,
            "_apply_total_exposure_cap_to_sizing",
            side_effect=lambda _executor, sizing, **_kwargs: (sizing, None),
        ), patch.object(
            main,
            "_apply_total_exposure_cap_to_entry_cost",
            side_effect=lambda _executor, requested_size_usd, **_kwargs: (requested_size_usd, None, None),
        ):
            spent = main.process_event(event, engine, executor, dedup, bankroll=1_000.0, account_equity=1_000.0)

        self.assertEqual(spent, 0.0)
        self.assertEqual(engine.prices, [0.70, 0.78])
        executor.execute.assert_not_called()
        dedup.mark_seen.assert_called_once_with(event.trade_id, event.market_id, event.trader_address)
        self.assertIn("outside band", executor.log_skip.call_args.kwargs["reason"])


if __name__ == "__main__":
    unittest.main()
