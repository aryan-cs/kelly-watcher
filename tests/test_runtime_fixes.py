from __future__ import annotations

import json
import os
import sys
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

if "apscheduler.schedulers.background" not in sys.modules:
    apscheduler_module = types.ModuleType("apscheduler")
    schedulers_module = types.ModuleType("apscheduler.schedulers")
    background_module = types.ModuleType("apscheduler.schedulers.background")

    class _BackgroundScheduler:
        def add_job(self, *args, **kwargs) -> None:
            return None

        def start(self) -> None:
            return None

    background_module.BackgroundScheduler = _BackgroundScheduler
    schedulers_module.background = background_module
    apscheduler_module.schedulers = schedulers_module
    sys.modules["apscheduler"] = apscheduler_module
    sys.modules["apscheduler.schedulers"] = schedulers_module
    sys.modules["apscheduler.schedulers.background"] = background_module

if "py_clob_client.clob_types" not in sys.modules:
    py_clob_client_module = types.ModuleType("py_clob_client")
    clob_types_module = types.ModuleType("py_clob_client.clob_types")
    order_builder_module = types.ModuleType("py_clob_client.order_builder")
    constants_module = types.ModuleType("py_clob_client.order_builder.constants")
    client_module = types.ModuleType("py_clob_client.client")

    class _MarketOrderArgs:
        def __init__(self, *, token_id: str, amount: float, side: str) -> None:
            self.token_id = token_id
            self.amount = amount
            self.side = side

    class _OrderType:
        FOK = "FOK"

    class _ClobClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def set_api_creds(self, *args, **kwargs) -> None:
            return None

        def create_or_derive_api_creds(self):
            return {}

    clob_types_module.MarketOrderArgs = _MarketOrderArgs
    clob_types_module.OrderType = _OrderType
    constants_module.BUY = "BUY"
    constants_module.SELL = "SELL"
    client_module.ClobClient = _ClobClient
    order_builder_module.constants = constants_module
    py_clob_client_module.clob_types = clob_types_module
    py_clob_client_module.order_builder = order_builder_module
    py_clob_client_module.client = client_module
    sys.modules["py_clob_client"] = py_clob_client_module
    sys.modules["py_clob_client.clob_types"] = clob_types_module
    sys.modules["py_clob_client.order_builder"] = order_builder_module
    sys.modules["py_clob_client.order_builder.constants"] = constants_module
    sys.modules["py_clob_client.client"] = client_module

import auto_retrain
import alerter
import beliefs
import config
import dedup
import db
import dashboard_api
import evaluator
import httpx
import identity_cache
import main
import tracker
import trader_scorer
from executor import ExecutionResult, PolymarketExecutor, SimulatedFill, TotalExposureDecision, log_trade
from economics import build_entry_economics
from market_scorer import MarketScorer, build_market_features
from trader_scorer import TraderScorer


def _insert_open_position_for_stop_loss_test(
    *,
    market_id: str = "market-stop",
    token_id: str = "token-stop",
    side: str = "yes",
    question: str = "Will the stop loss trigger?",
    trader_address: str = "0xabc",
    trader_name: str = "Trader",
    size_usd: float = 100.0,
    shares: float = 200.0,
    entry_price: float = 0.50,
    entered_at: int | None = None,
    real_money: bool = False,
) -> None:
    now_ts = int(time.time())
    opened_at = entered_at if entered_at is not None else (now_ts - 3600)
    conn = db.get_conn()
    conn.execute(
        """
        INSERT INTO positions (
            market_id, side, size_usd, avg_price, token_id, entered_at, real_money
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            market_id,
            side,
            size_usd,
            entry_price,
            token_id,
            opened_at,
            1 if real_money else 0,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id, market_id, question, trader_address, trader_name, side, token_id,
            source_action, price_at_signal, signal_size_usd, confidence, kelly_fraction,
            real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
            actual_entry_size_usd, remaining_entry_shares, remaining_entry_size_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"entry-{market_id}",
            market_id,
            question,
            trader_address,
            trader_name,
            side,
            token_id,
            "buy",
            entry_price,
            size_usd,
            0.70,
            0.10,
            1 if real_money else 0,
            0,
            opened_at,
            entry_price,
            shares,
            size_usd,
            shares,
            size_usd,
        ),
    )
    conn.commit()
    conn.close()


def _insert_resolved_shadow_trade_for_promotion_test(
    *,
    trade_id: str,
    resolved_at: int,
    market_id: str = "market-promotion",
    question: str = "Will the promotion gate pass?",
    trader_address: str = "0xabc",
    side: str = "yes",
    signal_size_usd: float = 10.0,
    entry_price: float = 0.4,
    entry_shares: float = 25.0,
    shadow_pnl_usd: float = 1.5,
) -> None:
    placed_at = int(resolved_at) - 120
    conn = db.get_conn()
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id, market_id, question, trader_address, side, source_action,
            price_at_signal, signal_size_usd, confidence, kelly_fraction,
            real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
            actual_entry_size_usd, shadow_pnl_usd, resolved_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_id,
            market_id,
            question,
            trader_address,
            side,
            "buy",
            entry_price,
            signal_size_usd,
            0.7,
            0.1,
            0,
            0,
            placed_at,
            entry_price,
            entry_shares,
            signal_size_usd,
            shadow_pnl_usd,
            resolved_at,
        ),
    )
    conn.commit()
    conn.close()


class RuntimeFixesTest(unittest.TestCase):
    def test_send_alert_suppresses_non_trade_notifications(self) -> None:
        client = Mock()
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            alerter.send_alert("Bot started")

        client.post.assert_not_called()

    def test_send_alert_allows_buy_notifications(self) -> None:
        client = Mock()
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            alerter.send_alert("Bought", kind="buy")

        client.post.assert_called_once()
        self.assertEqual(client.post.call_args.kwargs["json"]["text"], "bought")

    def test_send_alert_allows_retrain_notifications(self) -> None:
        client = Mock()
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            alerter.send_alert("Retrain accepted", kind="retrain")

        client.post.assert_called_once()

    def test_send_alert_allows_status_notifications(self) -> None:
        client = Mock()
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            alerter.send_alert("bot started", kind="status")

        client.post.assert_called_once()

    def test_build_trade_entry_alert_formats_market_line(self) -> None:
        message = alerter.build_trade_entry_alert(
            mode="shadow",
            side="yes",
            shares=12.5,
            price=0.437,
            total_usd=5.46,
            confidence=0.713,
            question="Will BTC finish March above $90k?",
            market_url="https://polymarket.com/event/btc-above-90k",
        )

        self.assertEqual(
            message,
            "shadow bought 12.5 YES shares @ 43.7 cents for a total of $5.46, 71.3% confident\n\n"
            "Will BTC finish March above $90k?: https://polymarket.com/event/btc-above-90k",
        )

    def test_build_trade_resolution_alert_formats_loss(self) -> None:
        message = alerter.build_trade_resolution_alert(
            mode="live",
            won=False,
            side="no",
            pnl_usd=-3.25,
            question="Will Team A win?",
            market_url="https://polymarket.com/event/team-a-win",
        )

        self.assertEqual(
            message,
            "❌ live lost NO, lost $3.25\n\nWill Team A win?: https://polymarket.com/event/team-a-win",
        )

    def test_build_trade_resolution_alert_includes_tracked_trader(self) -> None:
        message = alerter.build_trade_resolution_alert(
            mode="shadow",
            won=True,
            side="yes",
            pnl_usd=4.0,
            question="Will BTC finish March above $90k?",
            market_url="https://polymarket.com/event/btc-above-90k",
            tracked_trader_name="TraderOne",
            tracked_trader_address="0x1234567890abcdef1234567890abcdef12345678",
        )

        self.assertEqual(
            message,
            "✅ shadow won YES, made $4.00 | tracking TraderOne (0x123456...345678)\n\n"
            "Will BTC finish March above $90k?: https://polymarket.com/event/btc-above-90k",
        )

    def test_send_telegram_message_lowercases_non_url_text(self) -> None:
        client = Mock()
        response = Mock()
        response.raise_for_status = Mock(return_value=None)
        client.post.return_value = response
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            ok = alerter.send_telegram_message("Hello Trader\nWill BTC Win? https://polymarket.com/Event/ABC")

        self.assertTrue(ok)
        self.assertEqual(
            client.post.call_args.kwargs["json"]["text"],
            "hello trader\nwill btc win? https://polymarket.com/Event/ABC",
        )

    def test_resolve_wallet_for_username_returns_wallet_and_caches_identity(self) -> None:
        class _Response:
            def __init__(self, text: str, status_code: int = 200) -> None:
                self.text = text
                self.status_code = status_code

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        "request failed",
                        request=httpx.Request("GET", "https://polymarket.com"),
                        response=httpx.Response(self.status_code),
                    )
                return None

        class _Client:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def get(self, url: str) -> _Response:
                self.calls.append(url)
                if url != "https://polymarket.com/@TraderName":
                    return _Response("", status_code=404)
                return _Response(
                    """
                    <html>
                      0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
                      <script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">
                        {"props":{"pageProps":{"proxyAddress":"0x1234567890abcdef1234567890abcdef12345678"}}}
                      </script>
                      0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
                    </html>
                    """
                )

        with TemporaryDirectory() as tmpdir:
            original_cache_path = identity_cache.CACHE_PATH
            try:
                identity_cache.CACHE_PATH = Path(tmpdir) / "identity_cache.json"
                client = _Client()
                resolved = identity_cache.resolve_wallet_for_username("TraderName", client)
                self.assertEqual(resolved, "0x1234567890abcdef1234567890abcdef12345678")
                self.assertEqual(
                    identity_cache.lookup_wallet("TraderName"),
                    "0x1234567890abcdef1234567890abcdef12345678",
                )
                self.assertEqual(client.calls, ["https://polymarket.com/@TraderName"])
                self.assertEqual(
                    identity_cache.lookup_username("0x1234567890abcdef1234567890abcdef12345678"),
                    "tradername",
                )
            finally:
                identity_cache.CACHE_PATH = original_cache_path

    def test_live_account_equity_includes_open_positions(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor.get_usdc_balance = lambda: 80.0
        executor._fetch_live_positions = lambda: [
            {"currentValue": "25.5"},
            {"totalBought": "10", "cashPnl": "2"},
        ]

        with patch("executor.use_real_money", return_value=True):
            self.assertEqual(executor.get_account_equity_usd(), 117.5)

    def test_shadow_account_equity_uses_cost_basis_for_open_positions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "trading.db"
            with patch.object(db, "DB_PATH", db_path):
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, remaining_entry_shares, remaining_entry_size_usd,
                        realized_exit_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "open-1",
                        "market-1",
                        "0xabc",
                        "yes",
                        "buy",
                        0.5,
                        30.0,
                        0.7,
                        0.1,
                        0,
                        0,
                        1_700_000_000,
                        0.5,
                        60.0,
                        30.0,
                        60.0,
                        30.0,
                        5.0,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd, exited_at, outcome
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "closed-1",
                        "market-2",
                        "0xdef",
                        "yes",
                        "buy",
                        0.4,
                        20.0,
                        0.72,
                        0.08,
                        0,
                        0,
                        1_700_000_100,
                        0.4,
                        50.0,
                        20.0,
                        12.0,
                        1_700_000_200,
                        1,
                    ),
                )
                conn.commit()
                conn.close()

                executor = object.__new__(PolymarketExecutor)
                with patch("executor.use_real_money", return_value=False), patch(
                    "executor.shadow_bankroll_usd", return_value=100.0
                ):
                    self.assertEqual(executor.get_usdc_balance(), 87.0)
                    self.assertEqual(executor.get_account_equity_usd(), 117.0)

    def test_max_daily_loss_pct_reloads_from_env_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("MAX_DAILY_LOSS_PCT=0.12\n", encoding="utf-8")
            with patch.object(config, "ENV_PATH", env_path), patch.dict(
                "os.environ",
                {"MAX_DAILY_LOSS_PCT": "0.05"},
                clear=False,
            ):
                self.assertAlmostEqual(config.max_daily_loss_pct(), 0.12)
                env_path.write_text("MAX_DAILY_LOSS_PCT=0.07\n", encoding="utf-8")
                self.assertAlmostEqual(config.max_daily_loss_pct(), 0.07)

    def test_dashboard_config_snapshot_includes_max_market_horizon_after_write(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "save" / ".env.dev"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            repo_env_path = Path(tmpdir) / ".env.dev"
            env_example_path = Path(tmpdir) / ".env.example"
            repo_env_path.write_text("MAX_MARKET_HORIZON=365d\n", encoding="utf-8")
            env_example_path.write_text("", encoding="utf-8")

            with patch.object(dashboard_api, "ENV_PATH", env_path), patch.object(
                dashboard_api, "REPO_ROOT", Path(tmpdir)
            ), patch.object(
                dashboard_api, "ENV_EXAMPLE_PATH", env_example_path
            ):
                dashboard_api._write_env_value("MAX_MARKET_HORIZON", "7d")
                snapshot = dashboard_api._config_snapshot()

            self.assertEqual(snapshot["safe_values"]["MAX_MARKET_HORIZON"], "7d")
            self.assertIn("MAX_MARKET_HORIZON=7d", env_path.read_text(encoding="utf-8"))

    def test_dashboard_config_snapshot_includes_open_exposure_cap_after_write(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "save" / ".env.dev"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            repo_env_path = Path(tmpdir) / ".env.dev"
            env_example_path = Path(tmpdir) / ".env.example"
            repo_env_path.write_text("MAX_TOTAL_OPEN_EXPOSURE_FRACTION=0.60\n", encoding="utf-8")
            env_example_path.write_text("", encoding="utf-8")

            with patch.object(dashboard_api, "ENV_PATH", env_path), patch.object(
                dashboard_api, "REPO_ROOT", Path(tmpdir)
            ), patch.object(
                dashboard_api, "ENV_EXAMPLE_PATH", env_example_path
            ):
                dashboard_api._write_env_value("MAX_TOTAL_OPEN_EXPOSURE_FRACTION", "0.42")
                snapshot = dashboard_api._config_snapshot()

            self.assertEqual(snapshot["safe_values"]["MAX_TOTAL_OPEN_EXPOSURE_FRACTION"], "0.42")
            self.assertIn("MAX_TOTAL_OPEN_EXPOSURE_FRACTION=0.42", env_path.read_text(encoding="utf-8"))

    def test_dashboard_config_snapshot_includes_heuristic_min_entry_price_after_write(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "save" / ".env.dev"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            repo_env_path = Path(tmpdir) / ".env.dev"
            env_example_path = Path(tmpdir) / ".env.example"
            repo_env_path.write_text("HEURISTIC_MIN_ENTRY_PRICE=0.35\n", encoding="utf-8")
            env_example_path.write_text("", encoding="utf-8")

            with patch.object(dashboard_api, "ENV_PATH", env_path), patch.object(
                dashboard_api, "REPO_ROOT", Path(tmpdir)
            ), patch.object(
                dashboard_api, "ENV_EXAMPLE_PATH", env_example_path
            ):
                dashboard_api._write_env_value("HEURISTIC_MIN_ENTRY_PRICE", "0.50")
                snapshot = dashboard_api._config_snapshot()

            self.assertEqual(snapshot["safe_values"]["HEURISTIC_MIN_ENTRY_PRICE"], "0.50")
            self.assertIn("HEURISTIC_MIN_ENTRY_PRICE=0.50", env_path.read_text(encoding="utf-8"))

    def test_dashboard_config_snapshot_includes_replay_search_constraints_file_after_write(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "save" / ".env.dev"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            repo_env_path = Path(tmpdir) / ".env.dev"
            env_example_path = Path(tmpdir) / ".env.example"
            repo_env_path.write_text("REPLAY_SEARCH_CONSTRAINTS_FILE=old.json\n", encoding="utf-8")
            env_example_path.write_text("", encoding="utf-8")

            with patch.object(dashboard_api, "ENV_PATH", env_path), patch.object(
                dashboard_api, "REPO_ROOT", Path(tmpdir)
            ), patch.object(
                dashboard_api, "ENV_EXAMPLE_PATH", env_example_path
            ):
                dashboard_api._write_env_value("REPLAY_SEARCH_CONSTRAINTS_FILE", "replay_search_specs/constraints.json")
                snapshot = dashboard_api._config_snapshot()

            self.assertEqual(
                snapshot["safe_values"]["REPLAY_SEARCH_CONSTRAINTS_FILE"],
                "replay_search_specs/constraints.json",
            )
            self.assertIn(
                "REPLAY_SEARCH_CONSTRAINTS_FILE=replay_search_specs/constraints.json",
                env_path.read_text(encoding="utf-8"),
            )

    def test_build_replay_search_command_includes_file_backed_specs_and_inline_overrides(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            base_policy_path = tmp_path / "base_policy.json"
            grid_path = tmp_path / "grid.json"
            constraints_path = tmp_path / "constraints.json"
            db_path = tmp_path / "data" / "trading.db"
            base_policy_path.write_text(
                json.dumps({"mode": "shadow", "min_confidence": 0.57}),
                encoding="utf-8",
            )
            grid_path.write_text(
                json.dumps({"min_confidence": [0.55, 0.6]}),
                encoding="utf-8",
            )
            constraints_path.write_text(
                json.dumps({"min_accepted_count": 5}),
                encoding="utf-8",
            )

            with patch.object(config, "ENV_PATH", tmp_path / ".env"), patch.dict(
                "os.environ",
                {
                    "REPLAY_SEARCH_LABEL_PREFIX": "nightly",
                    "REPLAY_SEARCH_NOTES": "overnight sweep",
                    "REPLAY_SEARCH_TOP": "7",
                    "REPLAY_SEARCH_MAX_COMBOS": "123",
                    "REPLAY_SEARCH_WINDOW_DAYS": "21",
                    "REPLAY_SEARCH_WINDOW_COUNT": "4",
                    "REPLAY_SEARCH_BASE_POLICY_FILE": str(base_policy_path),
                    "REPLAY_SEARCH_BASE_POLICY_JSON": json.dumps(
                        {"min_confidence": 0.58, "max_bet_fraction": 0.03}
                    ),
                    "REPLAY_SEARCH_GRID_FILE": str(grid_path),
                    "REPLAY_SEARCH_GRID_JSON": json.dumps(
                        {"max_bet_fraction": [0.03, 0.04]}
                    ),
                    "REPLAY_SEARCH_CONSTRAINTS_FILE": str(constraints_path),
                    "REPLAY_SEARCH_CONSTRAINTS_JSON": json.dumps(
                        {"max_drawdown_pct": 0.1}
                    ),
                },
                clear=True,
            ), patch.object(main, "DB_PATH", db_path):
                command = main._build_replay_search_command()

        def _arg_value(flag: str) -> str:
            index = command.index(flag)
            return command[index + 1]

        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("replay_search.py"))
        self.assertEqual(_arg_value("--db"), str(db_path))
        self.assertEqual(_arg_value("--label-prefix"), "nightly")
        self.assertEqual(_arg_value("--notes"), "overnight sweep")
        self.assertEqual(_arg_value("--top"), "7")
        self.assertEqual(_arg_value("--max-combos"), "123")
        self.assertEqual(_arg_value("--window-days"), "21")
        self.assertEqual(_arg_value("--window-count"), "4")
        self.assertEqual(_arg_value("--base-policy-file"), str(base_policy_path))
        self.assertEqual(_arg_value("--grid-file"), str(grid_path))
        self.assertEqual(_arg_value("--constraints-file"), str(constraints_path))
        self.assertEqual(
            json.loads(_arg_value("--base-policy-json")),
            {
                "max_bet_fraction": 0.03,
                "min_confidence": 0.58,
                "mode": "shadow",
            },
        )
        self.assertEqual(
            json.loads(_arg_value("--grid-json")),
            {
                "max_bet_fraction": [0.03, 0.04],
                "min_confidence": [0.55, 0.6],
            },
        )
        self.assertEqual(
            json.loads(_arg_value("--constraints-json")),
            {
                "max_drawdown_pct": 0.1,
                "min_accepted_count": 5,
            },
        )

    def test_build_replay_search_command_includes_request_token_and_trigger_override(self) -> None:
        with (
            patch.object(main, "replay_search_label_prefix", return_value="scheduled"),
            patch.object(main, "replay_search_top", return_value=10),
            patch.object(main, "replay_search_max_combos", return_value=256),
            patch.object(main, "replay_search_window_days", return_value=14),
            patch.object(main, "replay_search_window_count", return_value=6),
            patch.object(main, "replay_search_notes", return_value="nightly"),
            patch.object(main, "replay_search_base_policy_file", return_value=""),
            patch.object(main, "replay_search_grid_file", return_value=""),
            patch.object(main, "replay_search_constraints_file", return_value=""),
            patch.object(main, "replay_search_score_weights_file", return_value=""),
            patch.object(main, "replay_search_base_policy", return_value={}),
            patch.object(main, "replay_search_grid", return_value={"min_confidence": [0.62]}),
            patch.object(main, "replay_search_constraints", return_value={}),
            patch.object(main, "replay_search_score_weights", return_value={}),
        ):
            command = main._build_replay_search_command(request_token="req-123", trigger="manual")

        self.assertIn("--request-token", command)
        self.assertIn("req-123", command)
        self.assertIn("--trigger", command)
        self.assertIn("manual", command)

    def test_load_replay_search_run_after_scopes_to_request_token(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()
                conn = db.get_conn()
                try:
                    before_id = int(
                        conn.execute(
                            """
                            INSERT INTO replay_search_runs (
                                started_at, finished_at, request_token, label_prefix, status,
                                base_policy_json, grid_json, constraints_json
                            ) VALUES (?,?,?,?,?,?,?,?)
                            """,
                            (100, 110, "before", "scheduled", "completed", "{}", "{}", "{}"),
                        ).lastrowid
                        or 0
                    )
                    wanted_id = int(
                        conn.execute(
                            """
                            INSERT INTO replay_search_runs (
                                started_at, finished_at, request_token, label_prefix, status,
                                base_policy_json, grid_json, constraints_json
                            ) VALUES (?,?,?,?,?,?,?,?)
                            """,
                            (120, 130, "req-123", "scheduled", "completed", "{}", "{}", "{}"),
                        ).lastrowid
                        or 0
                    )
                    other_id = int(
                        conn.execute(
                            """
                            INSERT INTO replay_search_runs (
                                started_at, finished_at, request_token, label_prefix, status,
                                base_policy_json, grid_json, constraints_json
                            ) VALUES (?,?,?,?,?,?,?,?)
                            """,
                            (140, 150, "other", "manual", "completed", "{}", "{}", "{}"),
                        ).lastrowid
                        or 0
                    )
                    conn.commit()
                    row = main._load_replay_search_run_after(before_id, request_token="req-123")
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

        assert row is not None
        self.assertEqual(int(row["id"]), wanted_id)
        self.assertNotEqual(int(row["id"]), other_id)

    def test_latest_replay_search_state_payload_loads_latest_persisted_run_for_restart(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()
                conn = db.get_conn()
                try:
                    conn.execute(
                        """
                        INSERT INTO replay_search_runs (
                            started_at, finished_at, request_token, trigger, label_prefix, status, status_message,
                            base_policy_json, grid_json, constraints_json,
                            candidate_count, feasible_count, best_feasible_score, best_feasible_total_pnl_usd
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            100, 110, "older", "scheduled", "scheduled", "completed",
                            "Replay search completed (run=1, candidates=4, feasible=1)",
                            "{}", "{}", "{}", 4, 1, 0.25, 3.0,
                        ),
                    )
                    latest_id = int(
                        conn.execute(
                            """
                            INSERT INTO replay_search_runs (
                                started_at, finished_at, request_token, trigger, label_prefix, status, status_message,
                                base_policy_json, grid_json, constraints_json,
                                candidate_count, feasible_count, best_feasible_score, best_feasible_total_pnl_usd
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                200, 230, "latest", "manual", "manual", "completed",
                                "Replay search completed (run=2, candidates=17, feasible=6); promotion applied",
                                "{}", "{}", "{}", 17, 6, 1.75, 42.5,
                            ),
                        ).lastrowid
                        or 0
                    )
                    conn.commit()
                    row = main._latest_replay_search_run()
                    payload = main._latest_replay_search_state_payload(row)
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

        assert row is not None
        self.assertEqual(int(row["id"]), latest_id)
        self.assertEqual(payload["last_replay_search_started_at"], 200)
        self.assertEqual(payload["last_replay_search_finished_at"], 230)
        self.assertEqual(payload["last_replay_search_status"], "completed")
        self.assertEqual(payload["last_replay_search_trigger"], "manual")
        self.assertEqual(payload["last_replay_search_scope"], "shadow_only")
        self.assertEqual(payload["last_replay_search_run_id"], latest_id)
        self.assertEqual(payload["last_replay_search_candidate_count"], 17)
        self.assertEqual(payload["last_replay_search_feasible_count"], 6)
        self.assertAlmostEqual(float(payload["last_replay_search_best_score"]), 1.75)
        self.assertAlmostEqual(float(payload["last_replay_search_best_pnl_usd"]), 42.5)
        self.assertEqual(
            payload["last_replay_search_message"],
            f"Replay search completed (run={latest_id}, candidates=17, feasible=6); promotion applied",
        )

    def test_latest_replay_search_run_uses_started_at_when_finished_at_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()
                conn = db.get_conn()
                try:
                    conn.execute(
                        """
                        INSERT INTO replay_search_runs (
                            started_at, finished_at, request_token, label_prefix, status,
                            base_policy_json, grid_json, constraints_json
                        ) VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (100, 180, "older", "scheduled", "completed", "{}", "{}", "{}"),
                    )
                    expected_id = int(
                        conn.execute(
                            """
                            INSERT INTO replay_search_runs (
                                started_at, finished_at, request_token, label_prefix, status,
                                base_policy_json, grid_json, constraints_json
                            ) VALUES (?,?,?,?,?,?,?,?)
                            """,
                            (250, 0, "latest", "scheduled", "failed", "{}", "{}", "{}"),
                        ).lastrowid
                        or 0
                    )
                    conn.commit()
                    row = main._latest_replay_search_run()
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

        assert row is not None
        self.assertEqual(int(row["id"]), expected_id)

    def test_persist_replay_search_run_runtime_context_updates_trigger_message_and_status(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()
                conn = db.get_conn()
                try:
                    run_id = int(
                        conn.execute(
                            """
                            INSERT INTO replay_search_runs (
                                started_at, finished_at, request_token, label_prefix, status,
                                base_policy_json, grid_json, constraints_json
                            ) VALUES (?,?,?,?,?,?,?,?)
                            """,
                            (100, 110, "req-1", "scheduled", "completed", "{}", "{}", "{}"),
                        ).lastrowid
                        or 0
                    )
                    conn.commit()
                finally:
                    conn.close()

                main._persist_replay_search_run_runtime_context(
                    run_id,
                    trigger="post_retrain_manual",
                    message="Replay search completed (run=1, candidates=4, feasible=2); promotion applied",
                    status="completed",
                )

                conn = db.get_conn()
                try:
                    row = conn.execute(
                        """
                        SELECT trigger, status_message, status
                        FROM replay_search_runs
                        WHERE id=?
                        """,
                        (run_id,),
                    ).fetchone()
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

        assert row is not None
        self.assertEqual(row["trigger"], "post_retrain_manual")
        self.assertEqual(
            row["status_message"],
            "Replay search completed (run=1, candidates=4, feasible=2); promotion applied",
        )
        self.assertEqual(row["status"], "completed")

    def test_insert_replay_search_failure_run_persists_durable_failed_row(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()

                row = main._insert_replay_search_failure_run(
                    started_at=100,
                    finished_at=120,
                    request_token="req-failed",
                    trigger="scheduled",
                    label_prefix="scheduled",
                    notes="nightly",
                    message="Replay search failed with exit code 2: traceback tail",
                )
                payload = main._latest_replay_search_state_payload(row)
            finally:
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

        assert row is not None
        self.assertEqual(row["request_token"], "req-failed")
        self.assertEqual(row["trigger"], "scheduled")
        self.assertEqual(row["label_prefix"], "scheduled")
        self.assertEqual(row["notes"], "nightly")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["status_message"], "Replay search failed with exit code 2: traceback tail")
        self.assertEqual(payload["last_replay_search_status"], "failed")
        self.assertEqual(payload["last_replay_search_trigger"], "scheduled")
        self.assertEqual(payload["last_replay_search_message"], "Replay search failed with exit code 2: traceback tail")

    def test_latest_retrain_state_payload_loads_latest_persisted_run_for_restart(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()
                conn = db.get_conn()
                try:
                    conn.execute(
                        """
                        INSERT INTO retrain_runs (
                            started_at, finished_at, trigger, status, ok, deployed,
                            sample_count, min_samples, message
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (100, 110, "scheduled", "completed_not_deployed", 0, 0, 20, 25, "older retrain"),
                    )
                    latest_id = int(
                        conn.execute(
                            """
                            INSERT INTO retrain_runs (
                                started_at, finished_at, trigger, status, ok, deployed,
                                sample_count, min_samples, message
                            ) VALUES (?,?,?,?,?,?,?,?,?)
                            """,
                            (200, 240, "manual", "deployed", 1, 1, 42, 30, "latest retrain"),
                        ).lastrowid
                        or 0
                    )
                    conn.commit()
                    row = main._latest_retrain_run()
                    payload = main._latest_retrain_state_payload(row)
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

        assert row is not None
        self.assertEqual(int(row["id"]), latest_id)
        self.assertEqual(payload["last_retrain_started_at"], 200)
        self.assertEqual(payload["last_retrain_finished_at"], 240)
        self.assertEqual(payload["last_retrain_status"], "deployed")
        self.assertEqual(payload["last_retrain_message"], "latest retrain")
        self.assertEqual(payload["last_retrain_sample_count"], 42)
        self.assertEqual(payload["last_retrain_min_samples"], 30)
        self.assertEqual(payload["last_retrain_trigger"], "manual")
        self.assertTrue(payload["last_retrain_deployed"])

    def test_latest_retrain_run_uses_started_at_when_finished_at_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()
                conn = db.get_conn()
                try:
                    conn.execute(
                        """
                        INSERT INTO retrain_runs (
                            started_at, finished_at, trigger, status, ok, deployed,
                            sample_count, min_samples, message
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (100, 180, "scheduled", "deployed", 1, 1, 30, 20, "older retrain"),
                    )
                    expected_id = int(
                        conn.execute(
                            """
                            INSERT INTO retrain_runs (
                                started_at, finished_at, trigger, status, ok, deployed,
                                sample_count, min_samples, message
                            ) VALUES (?,?,?,?,?,?,?,?,?)
                            """,
                            (250, 0, "manual", "failed", 0, 0, 33, 20, "latest retrain"),
                        ).lastrowid
                        or 0
                    )
                    conn.commit()
                    row = main._latest_retrain_run()
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

        assert row is not None
        self.assertEqual(int(row["id"]), expected_id)

    def test_dashboard_spawn_shadow_restart_process_writes_request_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            request_file = data_dir / "shadow_reset_request.json"
            with patch.object(dashboard_api, "DATA_DIR", data_dir), patch.object(
                dashboard_api, "SHADOW_RESET_REQUEST_FILE", request_file
            ):
                result = dashboard_api._spawn_shadow_restart_process(wallet_mode="clear_all")
                payload = json.loads(request_file.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertEqual(payload["wallet_mode"], "clear_all")
            self.assertTrue(str(payload["request_id"]).startswith("shadow-reset-"))

    def test_dashboard_spawn_shadow_restart_process_supports_keep_active_mode(self) -> None:
        with TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            request_file = data_dir / "shadow_reset_request.json"
            with patch.object(dashboard_api, "DATA_DIR", data_dir), patch.object(
                dashboard_api, "SHADOW_RESET_REQUEST_FILE", request_file
            ):
                result = dashboard_api._spawn_shadow_restart_process(wallet_mode="keep_active")
                payload = json.loads(request_file.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertEqual(payload["wallet_mode"], "keep_active")

    def test_dashboard_launch_shadow_restart_queues_request(self) -> None:
        with patch.object(dashboard_api, "_live_trading_enabled_in_config", return_value=False), patch.object(
            dashboard_api, "_current_bot_mode", return_value="shadow"
        ), patch.object(dashboard_api, "use_real_money", return_value=False), patch.object(
            dashboard_api, "_spawn_shadow_restart_process", return_value={"ok": True, "message": "queued"}
        ) as spawn_mock:
            result = dashboard_api._launch_shadow_restart(wallet_mode="keep_all")

        self.assertTrue(result["ok"])
        self.assertIn("wipe state and restart itself", result["message"])
        spawn_mock.assert_called_once_with("keep_all")

    def test_consume_shadow_reset_request_reads_valid_request(self) -> None:
        with TemporaryDirectory() as tmpdir:
            request_file = Path(tmpdir) / "shadow_reset_request.json"
            request_file.write_text(
                json.dumps(
                    {
                        "wallet_mode": "keep_all",
                        "request_id": "shadow-reset-1",
                        "requested_at": int(time.time()),
                        "source": "dashboard",
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(main, "SHADOW_RESET_REQUEST_FILE", request_file):
                request = main._consume_shadow_reset_request()

        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.wallet_mode, "keep_all")
        self.assertEqual(request.request_id, "shadow-reset-1")
        self.assertFalse(request_file.exists())

    def test_wait_for_next_poll_returns_when_shutdown_requested(self) -> None:
        stop_event = main.threading.Event()
        stop_event.set()
        started_at = time.time()

        main._wait_for_next_poll(started_at, {"started_at": 1}, stop_event=stop_event)

        self.assertLess(time.time() - started_at, 0.5)

    def test_entry_pause_reason_refreshes_daily_loss_guard_from_config(self) -> None:
        now_ts = int(time.time())
        guard = main.DailyLossGuard(
            start_equity=100.0,
            loss_limit_pct=0.10,
            day_key=time.strftime("%Y-%m-%d", time.localtime(now_ts)),
            _equity_locked=True,
        )
        tracker_stub = SimpleNamespace(trade_feed_health=lambda: (now_ts, 0))
        executor_stub = SimpleNamespace(live_entry_health_reason=lambda: None)

        with patch("main.max_daily_loss_pct", return_value=0.0), patch("main.use_real_money", return_value=False):
            reason = main._entry_pause_reason(
                tracker_stub,
                executor_stub,
                None,
                guard,
                89.0,
            )

        self.assertIsNone(reason)
        self.assertEqual(guard.loss_limit_pct, 0.0)

    def test_entry_pause_alert_tracker_debounces_state_flaps(self) -> None:
        tracker = main.EntryPauseAlertTracker(required_stable_loops=2)
        first_state = main.EntryPauseState(
            key="trade_feed_stale",
            reason="source trade feed is stale; the last successful trade poll was 181s ago",
        )
        second_state = main.EntryPauseState(
            key="trade_feed_stale",
            reason="source trade feed is stale; the last successful trade poll was 192s ago",
        )

        self.assertIsNone(tracker.update(first_state))

        paused_transition = tracker.update(second_state)
        self.assertEqual(paused_transition, ("paused", second_state))

        self.assertIsNone(tracker.update(second_state))
        self.assertIsNone(tracker.update(None))

        resumed_transition = tracker.update(None)
        self.assertEqual(resumed_transition[0], "resumed")
        self.assertEqual(resumed_transition[1], second_state)

    def test_entry_pause_state_uses_stable_live_health_key(self) -> None:
        now_ts = int(time.time())
        guard = main.DailyLossGuard(
            start_equity=100.0,
            loss_limit_pct=0.10,
            day_key=time.strftime("%Y-%m-%d", time.localtime(now_ts)),
            _equity_locked=True,
        )
        tracker_stub = SimpleNamespace(trade_feed_health=lambda: (now_ts, 0))
        executor_stub = SimpleNamespace(
            live_entry_health_status=lambda: (
                "wallet_balance_failures",
                "live balance health degraded after 4 consecutive wallet-balance failures",
            )
        )

        with patch("main.use_real_money", return_value=True):
            state = main._entry_pause_state(
                tracker_stub,
                executor_stub,
                None,
                guard,
                100.0,
            )

        self.assertIsNotNone(state)
        self.assertEqual(state.key, "live_health:wallet_balance_failures")
        self.assertIn("wallet-balance failures", state.reason)

    def test_live_entry_health_status_keeps_key_stable_as_failure_count_changes(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._consecutive_live_balance_failures = 3
        executor._consecutive_live_position_sync_failures = 0

        with patch("executor.max_live_health_failures", return_value=3):
            first_status = executor.live_entry_health_status()
            executor._consecutive_live_balance_failures = 5
            second_status = executor.live_entry_health_status()

        self.assertEqual(first_status[0], "wallet_balance_failures")
        self.assertEqual(second_status[0], "wallet_balance_failures")
        self.assertNotEqual(first_status[1], second_status[1])

    def test_resolve_shadow_trades_labels_exited_rows_without_overwriting_realized_pnl(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at, exited_at,
                        resolved_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-1",
                        "market-1",
                        "Will it happen?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.40,
                        10.0,
                        0.75,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        1_700_000_100,
                        1_700_000_100,
                        0.40,
                        25.0,
                        10.0,
                        3.21,
                    ),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": True,
                    "tokens": [
                        {"outcome": "yes", "winner": True},
                        {"outcome": "no", "winner": False},
                    ],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, counterfactual_return,
                           shadow_pnl_usd, exited_at, resolved_at, label_applied_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-1",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "yes")
                self.assertAlmostEqual(float(row["counterfactual_return"]), 1.5, places=6)
                self.assertAlmostEqual(float(row["shadow_pnl_usd"]), 3.21, places=2)
                self.assertEqual(int(row["exited_at"]), 1_700_000_100)
                self.assertEqual(int(row["resolved_at"]), 1_700_000_100)
                self.assertGreater(int(row["label_applied_at"]), 0)
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_ignores_live_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        token_id, price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "live-trade-1",
                        "market-live",
                        "Live unresolved trade",
                        "0xabc",
                        "yes",
                        "buy",
                        "token-live",
                        0.5,
                        10.0,
                        0.7,
                        0.1,
                        1,
                        0,
                        1_700_000_000,
                        0.5,
                        20.0,
                        10.0,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO positions (
                        market_id, token_id, side, size_usd, avg_price, entered_at, real_money
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    ("market-live", "token-live", "yes", 10.0, 0.5, 1_700_000_000, 1),
                )
                conn.commit()
                conn.close()

                resolved = evaluator.resolve_shadow_trades(trade_id="live-trade-1", forced_outcome="yes")

                self.assertEqual(resolved, [])
                conn = db.get_conn()
                row = conn.execute(
                    "SELECT outcome, actual_pnl_usd FROM trade_log WHERE trade_id='live-trade-1'"
                ).fetchone()
                position = conn.execute(
                    "SELECT size_usd FROM positions WHERE market_id='market-live' AND token_id='token-live' AND real_money=1"
                ).fetchone()
                conn.close()

                self.assertIsNone(row["outcome"])
                self.assertIsNone(row["actual_pnl_usd"])
                self.assertAlmostEqual(float(position["size_usd"]), 10.0, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_send_resolution_alerts_only_notifies_executed_positions(self) -> None:
        resolved_rows = [
            {
                "executed": True,
                "real_money": 0,
                "won": True,
                "side": "yes",
                "pnl": 3.5,
                "question": "Will it happen?",
                "market_resolved_outcome": "yes",
                "market_url": "https://polymarket.com/event/will-it-happen",
            },
            {
                "executed": False,
                "real_money": 0,
                "won": False,
                "side": "no",
                "pnl": 0.0,
                "question": "Skipped trade",
            },
        ]

        with patch("main.send_alert") as alert_mock:
            main._send_resolution_alerts(resolved_rows)

        alert_mock.assert_called_once()
        self.assertEqual(
            alert_mock.call_args.args[0],
            "✅ shadow won YES, made $3.50\n\nWill it happen?: https://polymarket.com/event/will-it-happen",
        )
        self.assertEqual(alert_mock.call_args.kwargs["kind"], "resolution")

    def test_resolve_shadow_trades_does_not_resolve_open_markets_from_prices(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-early",
                        "market-eth-1900",
                        "Will the price of Ethereum be above $1,900 on March 20?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.996,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": False,
                    "tokens": [
                        {"outcome": "Yes", "winner": False, "price": "0.994"},
                        {"outcome": "No", "winner": False, "price": "0.006"},
                    ],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(resolved, [])

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at, label_applied_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-early",),
                ).fetchone()
                conn.close()

                self.assertIsNone(row["outcome"])
                self.assertIsNone(row["market_resolved_outcome"])
                self.assertIsNone(row["resolved_at"])
                self.assertIsNone(row["label_applied_at"])
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_resolves_closed_markets_from_token_winner(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-after-end",
                        "market-eth-1900",
                        "Will the price of Ethereum be above $1,900 on March 20?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.996,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": True,
                    "tokens": [
                        {"outcome": "Yes", "winner": True},
                        {"outcome": "No", "winner": False},
                    ],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ), patch("evaluator.time.time", return_value=1_700_000_123):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at, label_applied_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-after-end",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "yes")
                self.assertEqual(int(row["resolved_at"]), 1_700_000_123)
                self.assertEqual(int(row["label_applied_at"]), 1_700_000_123)
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_deletes_positions_when_token_id_delete_misses(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-token-mismatch",
                        "market-token-mismatch",
                        "Will it happen?",
                        "0xabc",
                        "yes",
                        "TOKEN-1",
                        "buy",
                        0.4,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        0.4,
                        25.0,
                        10.0,
                    ),
                )
                conn.execute(
                    "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                    ("market-token-mismatch", "yes", 10.0, 0.4, "token-1", 1_700_000_000, 0),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": True,
                    "tokens": [
                        {"outcome": "yes", "winner": True},
                        {"outcome": "no", "winner": False},
                    ],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)

                conn = db.get_conn()
                position = conn.execute(
                    "SELECT 1 FROM positions WHERE market_id=? AND real_money=0",
                    ("market-token-mismatch",),
                ).fetchone()
                conn.close()

                self.assertIsNone(position)
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_waits_for_polymarket_closed_even_if_event_ended(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-sports-ended",
                        "market-favbet-bo3",
                        "Counter-Strike: Favbet vs ESC Gaming (BO3) - CCT Europe Series #18 Playoffs",
                        "0xabc",
                        "favbet",
                        "buy",
                        0.5,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": False,
                    "tokens": [
                        {"outcome": "Favbet", "winner": True},
                        {"outcome": "ESC Gaming", "winner": False},
                    ],
                    "events": [{"ended": True, "live": False}],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(resolved, [])

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at, label_applied_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-sports-ended",),
                ).fetchone()
                conn.close()

                self.assertIsNone(row["outcome"])
                self.assertIsNone(row["market_resolved_outcome"])
                self.assertIsNone(row["resolved_at"])
                self.assertIsNone(row["label_applied_at"])
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_falls_back_to_sports_page_for_team_market(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-sports-page-team",
                        "0x00342368bcca5a5b6e21d837edaebc7efaab6e48ce9041c6a215ddc7d22420d6",
                        "High Point Panthers vs. Wisconsin Badgers",
                        "https://polymarket.com/event/cbb-hpnt-wisc-2026-03-19",
                        "0xabc",
                        "high point panthers",
                        "token-team",
                        "buy",
                        0.45,
                        10.0,
                        0.64,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                sports_snapshot = {
                    "ended": True,
                    "score": "83-82",
                    "canonicalUrl": "https://polymarket.com/sports/cbb/cbb-hpnt-wisc-2026-03-19",
                    "event": {
                        "slug": "cbb-hpnt-wisc-2026-03-19",
                        "markets": [
                            {
                                "conditionId": "0x00342368bcca5a5b6e21d837edaebc7efaab6e48ce9041c6a215ddc7d22420d6",
                                "question": "High Point Panthers vs. Wisconsin Badgers",
                                "sportsMarketType": "moneyline",
                                "outcomes": ["High Point Panthers", "Wisconsin Badgers"],
                                "teams": [
                                    {"name": "High Point Panthers", "score": 83},
                                    {"name": "Wisconsin Badgers", "score": 82},
                                ],
                            }
                        ],
                    },
                }
                with patch("evaluator._fetch_market", return_value=None) as fetch_market_mock, patch(
                    "evaluator._fetch_sports_page_snapshot", return_value=sports_snapshot
                ), patch("evaluator.sync_belief_priors", return_value=0):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)
                fetch_market_mock.assert_not_called()

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-sports-page-team",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "high point panthers")
                resolution_payload = json.loads(row["resolution_json"])
                self.assertEqual(resolution_payload["source"], "sports_page")
                self.assertEqual(resolution_payload["score"], "83-82")
                self.assertTrue(resolution_payload["closed"])
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_falls_back_to_sports_page_for_yes_no_team_win_market(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-sports-page-yes-no",
                        "market-yes-no",
                        "Will High Point Panthers win on 2026-03-19?",
                        "https://polymarket.com/event/cbb-hpnt-wisc-2026-03-19",
                        "0xabc",
                        "yes",
                        "token-yes-no",
                        "buy",
                        0.44,
                        10.0,
                        0.64,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                sports_snapshot = {
                    "ended": True,
                    "score": "83-82",
                    "canonicalUrl": "https://polymarket.com/sports/cbb/cbb-hpnt-wisc-2026-03-19",
                    "event": {
                        "slug": "cbb-hpnt-wisc-2026-03-19",
                        "markets": [
                            {
                                "conditionId": "different-condition-id",
                                "question": "High Point Panthers vs. Wisconsin Badgers",
                                "sportsMarketType": "moneyline",
                                "outcomes": ["High Point Panthers", "Wisconsin Badgers"],
                                "teams": [
                                    {"name": "High Point Panthers", "score": 83},
                                    {"name": "Wisconsin Badgers", "score": 82},
                                ],
                            }
                        ],
                    },
                }
                with patch(
                    "evaluator._fetch_market",
                    return_value={
                        "closed": False,
                        "tokens": [
                            {"outcome": "Yes", "winner": False},
                            {"outcome": "No", "winner": False},
                        ],
                    },
                ), patch("evaluator._fetch_sports_page_snapshot", return_value=sports_snapshot), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-sports-page-yes-no",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "yes")
            finally:
                db.DB_PATH = original_db_path

    def test_sports_route_candidates_support_valorant_slugs(self) -> None:
        routes = evaluator._sports_route_candidates("val-uwgc-og-2026-03-25", None)
        self.assertIn("valorant", routes)
        self.assertIn("esports", routes)
        self.assertEqual(routes[0], "valorant")

    def test_resolve_shadow_trades_can_target_one_valorant_match_by_question(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-valorant-target",
                        "market-valorant-target",
                        "Valorant: University War GC vs Olimpo Gold (BO3) - VCT Game Changers Latin America South Group Stage",
                        "https://polymarket.com/event/val-uwgc-og-2026-03-25",
                        "0xabc",
                        "olimpo gold",
                        "token-valorant-target",
                        "buy",
                        0.43,
                        10.0,
                        0.68,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-valorant-other",
                        "market-valorant-other",
                        "Valorant: Evil Geniuses Academy vs Azure Dragon Gaming (BO3)",
                        "https://polymarket.com/event/val-ega-adg-2026-03-24",
                        "0xdef",
                        "evil geniuses academy",
                        "token-valorant-other",
                        "buy",
                        0.51,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_001,
                    ),
                )
                conn.commit()
                conn.close()

                sports_snapshot = {
                    "ended": True,
                    "canonicalUrl": "https://polymarket.com/sports/valorant/val-uwgc-og-2026-03-25",
                    "event": {
                        "slug": "val-uwgc-og-2026-03-25",
                        "finishedTimestamp": "2026-03-25T03:00:00Z",
                        "markets": [
                            {
                                "conditionId": "market-valorant-target",
                                "question": "University War GC vs Olimpo Gold",
                                "sportsMarketType": "moneyline",
                                "outcomes": ["University War GC", "Olimpo Gold"],
                                "teams": [
                                    {"name": "University War GC", "score": 0},
                                    {"name": "Olimpo Gold", "score": 2},
                                ],
                            }
                        ],
                    },
                }
                with patch("evaluator._fetch_market", return_value=None), patch(
                    "evaluator._fetch_sports_page_snapshot", return_value=sports_snapshot
                ), patch("evaluator.sync_belief_priors", return_value=0), patch(
                    "evaluator.time.time", return_value=1_700_000_123
                ):
                    resolved = evaluator.resolve_shadow_trades(
                        question_contains="University War GC vs Olimpo Gold"
                    )

                self.assertEqual(len(resolved), 1)
                self.assertEqual(resolved[0]["trade_id"], "trade-valorant-target")
                self.assertEqual(resolved[0]["market_resolved_outcome"], "olimpo gold")

                conn = db.get_conn()
                target_row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-valorant-target",),
                ).fetchone()
                other_row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-valorant-other",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(target_row["outcome"]), 1)
                self.assertEqual(target_row["market_resolved_outcome"], "olimpo gold")
                self.assertEqual(int(target_row["resolved_at"]), 1_700_000_123)
                self.assertIsNone(other_row["outcome"])
                self.assertIsNone(other_row["market_resolved_outcome"])
                self.assertIsNone(other_row["resolved_at"])
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_can_force_manual_outcome_for_targeted_match(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-valorant-force",
                        "market-valorant-force",
                        "Valorant: University War GC vs Olimpo Gold (BO3) - VCT Game Changers Latin America South Group Stage",
                        "https://polymarket.com/event/val-uw-og2-2026-03-17",
                        "0xabc",
                        "olimpo gold",
                        "token-valorant-force",
                        "buy",
                        0.43,
                        10.0,
                        0.68,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                with patch("evaluator.sync_belief_priors", return_value=0), patch(
                    "evaluator.time.time", return_value=1_700_000_456
                ):
                    resolved = evaluator.resolve_shadow_trades(
                        question_contains="University War GC vs Olimpo Gold",
                        forced_outcome="olimpo gold",
                    )

                self.assertEqual(len(resolved), 1)
                self.assertEqual(resolved[0]["trade_id"], "trade-valorant-force")
                self.assertTrue(resolved[0]["won"])
                self.assertEqual(resolved[0]["market_resolved_outcome"], "olimpo gold")

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-valorant-force",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "olimpo gold")
                self.assertEqual(int(row["resolved_at"]), 1_700_000_456)
                resolution_json = json.loads(row["resolution_json"])
                self.assertEqual(resolution_json["source"], "manual_override")
                self.assertEqual(resolution_json["forcedOutcome"], "olimpo gold")
            finally:
                db.DB_PATH = original_db_path

    def test_cleanup_premature_resolutions_reopens_bad_rows_and_rebuilds_beliefs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                premature_resolution_json = json.dumps(
                    {
                        "closed": False,
                        "endDate": "2026-03-20T16:00:00Z",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.994", "0.006"]',
                        "events": [{"ended": False, "live": True}],
                    }
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, signal_mode, placed_at,
                        resolved_at, label_applied_at, outcome, market_resolved_outcome,
                        counterfactual_return, resolution_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "skip-bad",
                        "market-skip",
                        "Will it happen?",
                        "0xskip",
                        "yes",
                        "buy",
                        0.9,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        1,
                        "confidence was 0.61 below the 0.62 minimum",
                        "heuristic",
                        1_700_000_000,
                        1_700_000_100,
                        1_700_000_100,
                        1,
                        "yes",
                        0.111111,
                        premature_resolution_json,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, actual_entry_price,
                        actual_entry_shares, actual_entry_size_usd, source_shares, confidence,
                        kelly_fraction, real_money, skipped, placed_at, resolved_at,
                        label_applied_at, outcome, market_resolved_outcome, counterfactual_return,
                        shadow_pnl_usd, remaining_entry_shares, remaining_entry_size_usd,
                        remaining_source_shares, resolution_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "open-bad",
                        "market-open",
                        "Counter-Strike: Favbet vs ESC Gaming - Map 2 Winner",
                        "0xopen",
                        "favbet",
                        "token-open",
                        "buy",
                        0.5,
                        1.0,
                        0.5,
                        2.0,
                        1.0,
                        19.0,
                        0.617,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        1_700_000_100,
                        1_700_000_100,
                        0,
                        "esc gaming",
                        -1.0,
                        -1.0,
                        0.0,
                        0.0,
                        0.0,
                        premature_resolution_json,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, actual_entry_price,
                        actual_entry_shares, actual_entry_size_usd, source_shares, confidence,
                        kelly_fraction, real_money, skipped, placed_at, exited_at,
                        resolved_at, shadow_pnl_usd, resolution_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "resolved-good",
                        "market-good",
                        "Already settled",
                        "0xgood",
                        "yes",
                        "token-good",
                        "buy",
                        0.4,
                        10.0,
                        0.4,
                        25.0,
                        10.0,
                        100.0,
                        0.7,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        1_700_000_200,
                        1_700_000_200,
                        3.21,
                        json.dumps({"closed": True, "winner": "yes", "endDate": "2026-03-19T16:00:00Z"}),
                    ),
                )
                conn.execute(
                    "INSERT INTO belief_updates (trade_log_id, applied_at) VALUES (?, ?)",
                    (999, 1_700_000_300),
                )
                conn.execute(
                    "INSERT INTO belief_priors (feature_name, bucket, wins, losses, updated_at) VALUES (?,?,?,?,?)",
                    ("__global__", "all", 99.0, 1.0, 1_700_000_300),
                )
                conn.commit()
                conn.close()

                backup_path = Path(tmpdir) / "premature_cleanup_test.bak"
                result = evaluator.cleanup_premature_resolutions(backup_path=backup_path)

                self.assertEqual(result["rows_cleaned"], 2)
                self.assertEqual(result["skipped_rows_cleaned"], 1)
                self.assertEqual(result["open_positions_reopened"], 1)
                self.assertEqual(result["exited_rows_preserved"], 0)
                self.assertEqual(result["belief_rows_reapplied"], 1)
                self.assertTrue(backup_path.exists())

                conn = db.get_conn()
                skipped_row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, counterfactual_return,
                           resolved_at, label_applied_at, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("skip-bad",),
                ).fetchone()
                open_row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, shadow_pnl_usd, actual_pnl_usd,
                           remaining_entry_shares, remaining_entry_size_usd, remaining_source_shares,
                           resolved_at, label_applied_at, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("open-bad",),
                ).fetchone()
                position_row = conn.execute(
                    """
                    SELECT size_usd, avg_price, token_id, side
                    FROM positions
                    WHERE market_id=? AND token_id=? AND real_money=0
                    """,
                    ("market-open", "token-open"),
                ).fetchone()
                belief_updates_count = conn.execute("SELECT COUNT(*) AS n FROM belief_updates").fetchone()["n"]
                belief_priors_count = conn.execute("SELECT COUNT(*) AS n FROM belief_priors").fetchone()["n"]
                conn.close()

                self.assertIsNone(skipped_row["outcome"])
                self.assertIsNone(skipped_row["market_resolved_outcome"])
                self.assertIsNone(skipped_row["counterfactual_return"])
                self.assertIsNone(skipped_row["resolved_at"])
                self.assertIsNone(skipped_row["label_applied_at"])
                self.assertIsNone(skipped_row["resolution_json"])

                self.assertIsNone(open_row["outcome"])
                self.assertIsNone(open_row["market_resolved_outcome"])
                self.assertIsNone(open_row["shadow_pnl_usd"])
                self.assertIsNone(open_row["actual_pnl_usd"])
                self.assertAlmostEqual(float(open_row["remaining_entry_shares"]), 2.0, places=6)
                self.assertAlmostEqual(float(open_row["remaining_entry_size_usd"]), 1.0, places=6)
                self.assertAlmostEqual(float(open_row["remaining_source_shares"]), 19.0, places=6)
                self.assertIsNone(open_row["resolved_at"])
                self.assertIsNone(open_row["label_applied_at"])
                self.assertIsNone(open_row["resolution_json"])

                self.assertIsNotNone(position_row)
                self.assertAlmostEqual(float(position_row["size_usd"]), 1.0, places=6)
                self.assertAlmostEqual(float(position_row["avg_price"]), 0.5, places=6)
                self.assertEqual(position_row["token_id"], "token-open")
                self.assertEqual(position_row["side"], "favbet")

                self.assertEqual(int(belief_updates_count), 1)
                self.assertGreater(int(belief_priors_count), 0)
            finally:
                db.DB_PATH = original_db_path

    def test_cleanup_premature_resolutions_clears_closed_false_rows_even_when_event_ended(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, actual_entry_price,
                        actual_entry_shares, actual_entry_size_usd, source_shares, confidence,
                        kelly_fraction, real_money, skipped, placed_at, resolved_at,
                        label_applied_at, outcome, market_resolved_outcome, counterfactual_return,
                        shadow_pnl_usd, remaining_entry_shares, remaining_entry_size_usd,
                        remaining_source_shares, resolution_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "ended-but-open",
                        "market-ended-open",
                        "Spread: Wisconsin Badgers (-10.5)",
                        "0xabc",
                        "wisconsin badgers",
                        "buy",
                        0.51,
                        1.0,
                        0.51,
                        1.9607843137,
                        1.0,
                        20.0,
                        0.64,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        1_700_000_200,
                        1_700_000_200,
                        0,
                        "high point panthers",
                        -1.0,
                        -1.0,
                        0.0,
                        0.0,
                        0.0,
                        json.dumps(
                            {
                                "closed": False,
                                "events": [{"ended": True, "score": "83-82"}],
                                "tokens": [
                                    {"outcome": "Wisconsin Badgers", "winner": False},
                                    {"outcome": "High Point Panthers", "winner": True},
                                ],
                            }
                        ),
                    ),
                )
                conn.commit()
                conn.close()

                result = evaluator.cleanup_premature_resolutions(backup_path=Path(tmpdir) / "cleanup.bak")
                self.assertEqual(result["rows_cleaned"], 1)
                self.assertEqual(result["open_positions_reopened"], 1)

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, shadow_pnl_usd,
                           remaining_entry_shares, remaining_entry_size_usd, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("ended-but-open",),
                ).fetchone()
                conn.close()

                self.assertIsNone(row["outcome"])
                self.assertIsNone(row["market_resolved_outcome"])
                self.assertIsNone(row["shadow_pnl_usd"])
                self.assertAlmostEqual(float(row["remaining_entry_shares"]), 1.960784, places=5)
                self.assertAlmostEqual(float(row["remaining_entry_size_usd"]), 1.0, places=6)
                self.assertIsNone(row["resolution_json"])
            finally:
                db.DB_PATH = original_db_path

    def test_early_retrain_counts_recent_labels_not_old_entry_times(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO model_history (
                        trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (2_000, 250, 0.2, 0.6, "[]", "model.joblib", 1),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, entry_gross_price, entry_gross_shares,
                        entry_gross_size_usd, shadow_pnl_usd, resolved_at, label_applied_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-2",
                        "market-2",
                        "Old trade, new label",
                        "0xdef",
                        "yes",
                        "buy",
                        0.45,
                        12.0,
                        0.72,
                        0.08,
                        0,
                        0,
                        1_000,
                        0.45,
                        26.666667,
                        12.0,
                        0.45,
                        26.666667,
                        12.0,
                        2.5,
                        1_500,
                        3_000,
                    ),
                )
                conn.commit()
                conn.close()

                with patch("auto_retrain.retrain_min_new_labels", return_value=1):
                    self.assertTrue(auto_retrain.should_retrain_early(None))
            finally:
                db.DB_PATH = original_db_path

    def test_early_retrain_without_deployed_model_uses_configured_min_samples(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                with patch("auto_retrain.load_training_data", return_value=[object()] * 149), patch(
                    "auto_retrain.min_samples_required", return_value=150
                ):
                    self.assertFalse(auto_retrain.should_retrain_early(None))

                with patch("auto_retrain.load_training_data", return_value=[object()] * 150), patch(
                    "auto_retrain.min_samples_required", return_value=150
                ):
                    self.assertTrue(auto_retrain.should_retrain_early(None))
            finally:
                db.DB_PATH = original_db_path

    def test_live_order_response_fill_overrides_book_estimate_for_entries(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._clob = SimpleNamespace(
            create_market_order=lambda order: order,
            post_order=lambda signed, order_type: {
                "success": True,
                "status": "matched",
                "orderID": "order-1",
                "makingAmount": "10.0",
                "takingAmount": "20.0",
            },
        )
        executor.refresh_event_market_data = lambda event: (True, None)
        executor.get_fee_rate_bps = lambda token_id, market_meta=None: (0, None)
        executor._ensure_live_token_allowance = lambda token_id: None
        executor.get_usdc_balance = lambda: 100.0
        executor._measure_live_balance_change = lambda before, expect_increase=False: (before, 0.0)
        executor._sync_live_positions = lambda *args, **kwargs: None
        executor.estimate_entry_fill = lambda raw_book, amount: (SimpleNamespace(spent_usd=7.0, shares=7.0, avg_price=1.0), None)
        executor._reconcile_live_order_fill = PolymarketExecutor._reconcile_live_order_fill.__get__(executor, PolymarketExecutor)
        dedup_cache = SimpleNamespace(
            confirm=lambda *args, **kwargs: None,
            mark_seen=lambda *args, **kwargs: None,
            release=lambda *args, **kwargs: None,
        )
        event = SimpleNamespace(
            question="Will it happen?",
            trader_address="0xabc",
            price=0.5,
            raw_orderbook=None,
            token_id="token-1",
            trader_name="Trader",
            raw_trade=None,
            raw_market_metadata=None,
            snapshot=None,
            action="buy",
            timestamp=1_700_000_000,
            observed_at=1_700_000_001,
            poll_started_at=1_700_000_000,
            market_close_ts=1_700_010_000,
            metadata_fetched_at=1_700_000_000,
            orderbook_fetched_at=1_700_000_000,
            source_ts_raw="1700000000",
            shares=5.0,
            size_usd=2.5,
        )
        market_f = SimpleNamespace(
            execution_price=0.75,
            price_1h_ago=None,
            volume_7d_avg_usd=None,
            best_ask=0.5,
            best_bid=0.49,
            mid=0.495,
            volume_24h_usd=None,
            oi_usd=None,
            top_holder_pct=None,
            bid_depth_usd=None,
            ask_depth_usd=None,
            days_to_res=None,
        )
        captured: dict[str, float] = {}

        with patch("executor.log_trade", side_effect=lambda **kwargs: captured.update(kwargs) or 1), patch("executor.send_alert"):
            result = executor._execute_live(
                "trade-1",
                "market-1",
                "token-1",
                "yes",
                10.0,
                0.1,
                0.7,
                {"mode": "heuristic"},
                event,
                None,
                market_f,
                dedup_cache,
            )

        self.assertTrue(result.placed)
        self.assertAlmostEqual(result.dollar_size, 10.0, places=6)
        self.assertAlmostEqual(result.shares, 20.0, places=6)
        self.assertAlmostEqual(float(captured["actual_entry_price"]), 0.5, places=6)
        self.assertAlmostEqual(float(captured["actual_entry_shares"]), 20.0, places=6)
        self.assertAlmostEqual(float(captured["actual_entry_size_usd"]), 10.0, places=6)

    def test_log_trade_persists_entry_fee_breakdown(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                event = SimpleNamespace(
                    token_id="token-1",
                    action="buy",
                    timestamp=1_700_000_000,
                    observed_at=1_700_000_001,
                    poll_started_at=1_700_000_000,
                    market_close_ts=1_700_010_000,
                    metadata_fetched_at=1_700_000_000,
                    orderbook_fetched_at=1_700_000_000,
                    source_ts_raw="1700000000",
                    shares=50.0,
                    size_usd=25.0,
                    question="Will it happen?",
                    raw_trade=None,
                    raw_market_metadata=None,
                    raw_orderbook=None,
                    snapshot={"fee_rate_bps": 10},
                    trader_address="0xabc",
                    trader_name="Trader",
                )
                economics = build_entry_economics(
                    gross_price=0.5,
                    gross_shares=20.0,
                    gross_spent_usd=10.0,
                    fee_rate_bps=10,
                    fixed_cost_usd=0.2,
                    include_expected_exit_fee_in_sizing=False,
                    expected_close_fixed_cost_usd=0.0,
                )
                row_id = log_trade(
                    trade_id="buy-1",
                    market_id="market-1",
                    question="Will it happen?",
                    trader_address="0xabc",
                    side="yes",
                    price=0.5,
                    signal_size_usd=10.0,
                    confidence=0.7,
                    kelly_f=0.1,
                    real_money=False,
                    order_id=None,
                    skipped=False,
                    skip_reason=None,
                    actual_entry_price=economics.effective_entry_price,
                    actual_entry_shares=economics.net_shares,
                    actual_entry_size_usd=economics.total_cost_usd,
                    entry_economics=economics,
                    event=event,
                    signal={"mode": "heuristic"},
                )

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT entry_fee_rate_bps, entry_fee_usd, entry_fee_shares,
                           entry_fixed_cost_usd, entry_gross_price, entry_gross_shares,
                           entry_gross_size_usd
                    FROM trade_log
                    WHERE id=?
                    """,
                    (row_id,),
                ).fetchone()
                conn.close()

                self.assertAlmostEqual(float(row["entry_fee_rate_bps"]), 10.0, places=6)
                self.assertAlmostEqual(float(row["entry_fee_usd"]), economics.entry_fee_usd, places=6)
                self.assertAlmostEqual(float(row["entry_fee_shares"]), economics.entry_fee_shares, places=6)
                self.assertAlmostEqual(float(row["entry_fixed_cost_usd"]), economics.fixed_cost_usd, places=6)
                self.assertAlmostEqual(float(row["entry_gross_price"]), economics.gross_price, places=6)
                self.assertAlmostEqual(float(row["entry_gross_shares"]), economics.gross_shares, places=6)
                self.assertAlmostEqual(float(row["entry_gross_size_usd"]), economics.gross_spent_usd, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_applies_settlement_cost_and_fee_aware_counterfactuals(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        token_id, price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, remaining_entry_shares, remaining_entry_size_usd,
                        snapshot_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "open-win",
                        "market-1",
                        "Open winner",
                        "0xabc",
                        "yes",
                        "buy",
                        "token-1",
                        0.50,
                        10.0,
                        0.7,
                        0.1,
                        0,
                        0,
                        1_700_000_000,
                        0.51,
                        19.98,
                        10.2,
                        19.98,
                        10.2,
                        json.dumps({"fee_rate_bps": 10}),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        token_id, price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, snapshot_json, skip_reason
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "skip-win",
                        "market-2",
                        "Skipped winner",
                        "0xdef",
                        "yes",
                        "buy",
                        "token-2",
                        0.50,
                        10.0,
                        0.58,
                        0.05,
                        0,
                        1,
                        1_700_000_100,
                        json.dumps({"fee_rate_bps": 10}),
                        "signal confidence was 58.0%, below the 60.0% minimum",
                    ),
                )
                conn.commit()
                conn.close()

                with patch("evaluator.settlement_fixed_cost_usd", return_value=0.25), patch(
                    "evaluator.entry_fixed_cost_usd", return_value=0.20
                ):
                    resolved = evaluator.resolve_shadow_trades(question_contains="winner", forced_outcome="yes")

                self.assertEqual(len(resolved), 2)
                conn = db.get_conn()
                open_row = conn.execute(
                    "SELECT shadow_pnl_usd, resolution_fixed_cost_usd FROM trade_log WHERE trade_id='open-win'"
                ).fetchone()
                skipped_row = conn.execute(
                    "SELECT counterfactual_return FROM trade_log WHERE trade_id='skip-win'"
                ).fetchone()
                conn.close()

                self.assertAlmostEqual(float(open_row["shadow_pnl_usd"]), 9.53, places=2)
                self.assertAlmostEqual(float(open_row["resolution_fixed_cost_usd"]), 0.25, places=6)
                self.assertLess(float(skipped_row["counterfactual_return"]), 0.955)
            finally:
                db.DB_PATH = original_db_path

    def test_validate_startup_fails_closed_on_invalid_live_risk_limit(self) -> None:
        valid_wallet = "0x1111111111111111111111111111111111111111"
        watched_wallet = "0x2222222222222222222222222222222222222222"
        with patch.dict(
            "os.environ",
            {
                "USE_REAL_MONEY": "true",
                "POLYGON_PRIVATE_KEY": "0xabc123",
                "POLYGON_WALLET_ADDRESS": valid_wallet,
                "MAX_MARKET_EXPOSURE_FRACTION": "abc",
            },
            clear=False,
        ), patch.object(main, "WATCHED_WALLETS", [watched_wallet]), patch("main._resolved_shadow_trade_count", return_value=999), patch("main.send_alert"):
            with self.assertRaises(RuntimeError) as ctx:
                main._validate_startup()
        self.assertIn("MAX_MARKET_EXPOSURE_FRACTION must be numeric", str(ctx.exception))

    def test_validate_startup_blocks_live_until_post_promotion_shadow_history_is_available(self) -> None:
        valid_wallet = "0x1111111111111111111111111111111111111111"
        watched_wallet = "0x2222222222222222222222222222222222222222"

        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()
                conn = db.get_conn()
                try:
                    run_id = int(
                        conn.execute(
                            """
                            INSERT INTO replay_search_runs (
                                started_at, finished_at, label_prefix, status,
                                base_policy_json, grid_json, constraints_json
                            ) VALUES (?,?,?,?,?,?,?)
                            """,
                            (
                                1_700_000_110,
                                1_700_000_115,
                                "scheduled",
                                "completed",
                                "{}",
                                "{}",
                                "{}",
                            ),
                        ).lastrowid
                        or 0
                    )
                    candidate_id = int(
                        conn.execute(
                            """
                            INSERT INTO replay_search_candidates (
                                replay_search_run_id, candidate_index, score
                            ) VALUES (?,?,?)
                            """,
                            (run_id, 0, 42.0),
                        ).lastrowid
                        or 0
                    )
                    conn.commit()
                finally:
                    conn.close()

                _insert_resolved_shadow_trade_for_promotion_test(
                    trade_id="shadow-before-promotion",
                    resolved_at=1_700_000_100,
                )
                promotion_id = main._insert_replay_promotion(
                    {
                        "requested_at": 1_700_000_120,
                        "finished_at": 1_700_000_140,
                        "applied_at": 1_700_000_150,
                        "trigger": "scheduled_replay_search",
                        "scope": "shadow_only",
                        "source_mode": "shadow",
                        "status": "applied",
                        "reason": "auto-promoted best feasible replay candidate",
                        "replay_search_run_id": run_id,
                        "replay_search_candidate_id": candidate_id,
                        "config_json": {"MIN_CONFIDENCE": 0.6},
                        "previous_config_json": {"MIN_CONFIDENCE": 0.55},
                        "updated_keys_json": ["MIN_CONFIDENCE"],
                        "candidate_result_json": {"score_breakdown": {"score_usd": 42.0}},
                        "score": 42.0,
                        "score_delta": 5.5,
                        "total_pnl_usd": 120.0,
                        "pnl_delta_usd": 18.0,
                        "shadow_resolved_count": 1,
                        "shadow_resolved_since_previous": 1,
                    }
                )
                _insert_resolved_shadow_trade_for_promotion_test(
                    trade_id="shadow-after-promotion",
                    resolved_at=1_700_000_200,
                )

                conn = db.get_conn()
                try:
                    promotion_row = conn.execute(
                        """
                        SELECT trigger, scope, source_mode, status, replay_search_run_id,
                               replay_search_candidate_id, config_json, previous_config_json,
                               updated_keys_json, candidate_result_json, score_delta,
                               pnl_delta_usd, shadow_resolved_count, shadow_resolved_since_previous
                        FROM replay_promotions
                        WHERE id=?
                        """,
                        (promotion_id,),
                    ).fetchone()
                finally:
                    conn.close()

                with patch.dict(
                    "os.environ",
                    {
                        "USE_REAL_MONEY": "true",
                        "POLYGON_PRIVATE_KEY": "0xabc123",
                        "POLYGON_WALLET_ADDRESS": valid_wallet,
                        "LIVE_REQUIRE_SHADOW_HISTORY": "false",
                        "LIVE_MIN_SHADOW_RESOLVED_SINCE_PROMOTION": "2",
                    },
                    clear=False,
                ), patch.object(main, "WATCHED_WALLETS", [watched_wallet]), patch("main.send_alert"):
                    with self.assertRaises(RuntimeError) as ctx:
                        main._validate_startup()
            finally:
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

        self.assertIsNotNone(promotion_row)
        assert promotion_row is not None
        self.assertEqual(promotion_row["trigger"], "scheduled_replay_search")
        self.assertEqual(promotion_row["scope"], "shadow_only")
        self.assertEqual(promotion_row["source_mode"], "shadow")
        self.assertEqual(promotion_row["status"], "applied")
        self.assertGreater(int(promotion_row["replay_search_run_id"]), 0)
        self.assertGreater(int(promotion_row["replay_search_candidate_id"]), 0)
        self.assertEqual(json.loads(promotion_row["config_json"]), {"MIN_CONFIDENCE": 0.6})
        self.assertEqual(json.loads(promotion_row["previous_config_json"]), {"MIN_CONFIDENCE": 0.55})
        self.assertEqual(json.loads(promotion_row["updated_keys_json"]), ["MIN_CONFIDENCE"])
        self.assertEqual(
            json.loads(promotion_row["candidate_result_json"]),
            {"score_breakdown": {"score_usd": 42.0}},
        )
        self.assertAlmostEqual(float(promotion_row["score_delta"]), 5.5)
        self.assertAlmostEqual(float(promotion_row["pnl_delta_usd"]), 18.0)
        self.assertEqual(int(promotion_row["shadow_resolved_count"]), 1)
        self.assertEqual(int(promotion_row["shadow_resolved_since_previous"]), 1)
        self.assertIn(
            "1 resolved shadow trades since last replay promotion at 1700000150 < required 2",
            str(ctx.exception),
        )

    def test_build_replay_search_command_includes_file_specs_and_json_overlays(self) -> None:
        with (
            patch.object(main, "replay_search_label_prefix", return_value="scheduled"),
            patch.object(main, "replay_search_top", return_value=10),
            patch.object(main, "replay_search_max_combos", return_value=256),
            patch.object(main, "replay_search_window_days", return_value=14),
            patch.object(main, "replay_search_window_count", return_value=6),
            patch.object(main, "replay_search_notes", return_value="nightly"),
            patch.object(main, "replay_search_base_policy_file", return_value="replay_search_specs/base_policy.json"),
            patch.object(main, "replay_search_grid_file", return_value="replay_search_specs/grid.json"),
            patch.object(main, "replay_search_constraints_file", return_value="replay_search_specs/constraints.json"),
            patch.object(main, "replay_search_score_weights_file", return_value="replay_search_specs/score_weights.json"),
            patch.object(main, "replay_search_base_policy", return_value={"mode": "shadow", "min_confidence": 0.66}),
            patch.object(main, "replay_search_grid", return_value={"min_confidence": [0.62, 0.66]}),
            patch.object(main, "replay_search_constraints", return_value={"min_accepted_count": 12}),
            patch.object(main, "replay_search_score_weights", return_value={"worst_window_penalty": 0.1}),
        ):
            command = main._build_replay_search_command()

        self.assertIn("--base-policy-file", command)
        self.assertIn("replay_search_specs/base_policy.json", command)
        self.assertIn("--grid-file", command)
        self.assertIn("replay_search_specs/grid.json", command)
        self.assertIn("--constraints-file", command)
        self.assertIn("replay_search_specs/constraints.json", command)
        self.assertIn("--score-weights-file", command)
        self.assertIn("replay_search_specs/score_weights.json", command)
        self.assertIn("--base-policy-json", command)
        self.assertIn(json.dumps({"mode": "shadow", "min_confidence": 0.66}, separators=(",", ":"), sort_keys=True), command)
        self.assertIn("--grid-json", command)
        self.assertIn(json.dumps({"min_confidence": [0.62, 0.66]}, separators=(",", ":"), sort_keys=True), command)
        self.assertIn("--constraints-json", command)
        self.assertIn(json.dumps({"min_accepted_count": 12}, separators=(",", ":"), sort_keys=True), command)
        self.assertIn("--score-weights-json", command)
        self.assertIn(json.dumps({"worst_window_penalty": 0.1}, separators=(",", ":"), sort_keys=True), command)

    def test_replay_search_transient_status_state_clears_stale_summary_fields(self) -> None:
        running_state = main._replay_search_transient_status_state(
            status="running",
            message="Replay search running (scheduled)",
            trigger="scheduled",
            started_at=1_700_000_123,
        )
        busy_state = main._replay_search_transient_status_state(
            status="already_running",
            message="Replay search request ignored: already running (manual)",
            trigger="manual",
        )

        self.assertTrue(running_state["replay_search_in_progress"])
        self.assertEqual(running_state["replay_search_started_at"], 1_700_000_123)
        self.assertEqual(running_state["last_replay_search_started_at"], 1_700_000_123)
        self.assertEqual(running_state["last_replay_search_candidate_count"], 0)
        self.assertEqual(running_state["last_replay_search_feasible_count"], 0)
        self.assertEqual(running_state["last_replay_search_run_id"], 0)
        self.assertIsNone(running_state["last_replay_search_best_score"])
        self.assertIsNone(running_state["last_replay_search_best_pnl_usd"])

        self.assertEqual(busy_state["last_replay_search_status"], "already_running")
        self.assertEqual(busy_state["last_replay_search_trigger"], "manual")
        self.assertEqual(busy_state["last_replay_search_candidate_count"], 0)
        self.assertEqual(busy_state["last_replay_search_feasible_count"], 0)
        self.assertEqual(busy_state["last_replay_search_run_id"], 0)
        self.assertIsNone(busy_state["last_replay_search_best_score"])
        self.assertIsNone(busy_state["last_replay_search_best_pnl_usd"])
        self.assertNotIn("replay_search_in_progress", busy_state)

    def test_replay_promotion_state_updates_only_advances_applied_baseline_on_apply(self) -> None:
        skipped_updates = main._replay_promotion_state_updates(
            {
                "promotion_id": 9,
                "event_at": 1_700_000_111,
                "applied_at": 0,
                "status": "skipped_score_delta",
                "message": "score delta too small",
                "scope": "shadow_only",
                "run_id": 21,
                "candidate_id": 3,
                "score_delta": 0.0,
                "pnl_delta_usd": 0.0,
            }
        )
        applied_updates = main._replay_promotion_state_updates(
            {
                "promotion_id": 10,
                "applied_at": 1_700_000_123,
                "status": "applied",
                "message": "auto-promoted best feasible replay candidate",
                "scope": "shadow_only",
                "run_id": 22,
                "candidate_id": 4,
                "score_delta": 1.25,
                "pnl_delta_usd": 18.0,
            }
        )

        self.assertEqual(skipped_updates["last_replay_promotion_status"], "skipped_score_delta")
        self.assertEqual(skipped_updates["last_replay_promotion_run_id"], 21)
        self.assertEqual(skipped_updates["last_replay_promotion_at"], 1_700_000_111)
        self.assertNotIn("last_applied_replay_promotion_id", skipped_updates)

        self.assertEqual(applied_updates["last_replay_promotion_id"], 10)
        self.assertEqual(applied_updates["last_applied_replay_promotion_id"], 10)
        self.assertEqual(applied_updates["last_applied_replay_promotion_at"], 1_700_000_123)
        self.assertEqual(applied_updates["last_applied_replay_promotion_status"], "applied")
        self.assertEqual(applied_updates["last_applied_replay_promotion_run_id"], 22)
        self.assertAlmostEqual(float(applied_updates["last_applied_replay_promotion_pnl_delta_usd"]), 18.0)

    def test_shadow_history_state_payload_reports_base_and_promo_gates(self) -> None:
        payload = main._shadow_history_state_payload(
            total_resolved_shadow=8,
            resolved_since_promotion=3,
            last_promotion={
                "id": 17,
                "applied_at": 1_700_000_456,
                "status": "applied",
                "reason": "auto-promoted best feasible replay candidate",
                "scope": "shadow_only",
                "replay_search_run_id": 31,
                "replay_search_candidate_id": 6,
                "score_delta": 2.5,
                "pnl_delta_usd": 12.0,
            },
            require_total_history=True,
            minimum_total=10,
            minimum_since_promotion=5,
        )

        self.assertTrue(payload["shadow_history_state_known"])
        self.assertEqual(payload["resolved_shadow_trade_count"], 8)
        self.assertTrue(payload["live_require_shadow_history_enabled"])
        self.assertEqual(payload["live_min_shadow_resolved"], 10)
        self.assertFalse(payload["live_shadow_history_total_ready"])
        self.assertEqual(payload["resolved_shadow_since_last_promotion"], 3)
        self.assertEqual(payload["live_min_shadow_resolved_since_last_promotion"], 5)
        self.assertFalse(payload["live_shadow_history_ready"])
        self.assertEqual(payload["last_applied_replay_promotion_id"], 17)
        self.assertEqual(payload["last_applied_replay_promotion_at"], 1_700_000_456)
        self.assertEqual(payload["last_applied_replay_promotion_run_id"], 31)

    def test_latest_replay_promotion_prefers_latest_attempt_over_latest_applied(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()
                applied_id = main._insert_replay_promotion(
                    {
                        "requested_at": 1_700_000_100,
                        "finished_at": 1_700_000_110,
                        "applied_at": 1_700_000_110,
                        "trigger": "scheduled_replay_search",
                        "scope": "shadow_only",
                        "source_mode": "shadow",
                        "status": "applied",
                        "reason": "auto-promoted best feasible replay candidate",
                        "replay_search_run_id": None,
                        "replay_search_candidate_id": None,
                    }
                )
                skipped_id = main._insert_replay_promotion(
                    {
                        "requested_at": 1_700_000_200,
                        "finished_at": 1_700_000_220,
                        "applied_at": 0,
                        "trigger": "scheduled_replay_search",
                        "scope": "shadow_only",
                        "source_mode": "shadow",
                        "status": "skipped_score_delta",
                        "reason": "score delta too small",
                        "replay_search_run_id": None,
                        "replay_search_candidate_id": None,
                    }
                )

                latest_attempt = main._latest_replay_promotion()
                latest_applied = main._latest_applied_replay_promotion()
                latest_attempt_state = main._latest_replay_promotion_state_payload(latest_attempt)
            finally:
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

        self.assertIsNotNone(latest_attempt)
        self.assertIsNotNone(latest_applied)
        assert latest_attempt is not None
        assert latest_applied is not None
        self.assertEqual(int(latest_attempt["id"]), skipped_id)
        self.assertEqual(int(latest_applied["id"]), applied_id)
        self.assertEqual(latest_attempt_state["last_replay_promotion_id"], skipped_id)
        self.assertEqual(latest_attempt_state["last_replay_promotion_status"], "skipped_score_delta")
        self.assertEqual(latest_attempt_state["last_replay_promotion_at"], 1_700_000_220)

    def test_apply_env_config_payload_only_writes_promotable_replay_keys(self) -> None:
        with patch.object(main, "_write_env_value") as write_env_value:
            result = main._apply_env_config_payload(
                {
                    "MIN_CONFIDENCE": 0.66,
                    "UNSAFE_EXTRA_KEY": "ignored",
                    "ALLOW_XGBOOST": False,
                }
            )

        self.assertEqual(result["applied_keys"], ["ALLOW_XGBOOST", "MIN_CONFIDENCE"])
        self.assertEqual(result["ignored_keys"], ["UNSAFE_EXTRA_KEY"])
        self.assertEqual(result["config"], {"ALLOW_XGBOOST": False, "MIN_CONFIDENCE": 0.66})
        self.assertEqual(
            write_env_value.call_args_list,
            [
                unittest.mock.call("ALLOW_XGBOOST", "false"),
                unittest.mock.call("MIN_CONFIDENCE", "0.66"),
            ],
        )

    def test_apply_env_config_payload_rejects_non_promotable_only_payloads(self) -> None:
        with self.assertRaisesRegex(ValueError, "did not contain any promotable config keys"):
            main._apply_env_config_payload({"UNSAFE_EXTRA_KEY": "ignored"})

    def test_restore_env_config_payload_rolls_back_env_file_and_process_env(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("MIN_CONFIDENCE=0.55\n", encoding="utf-8")
            original_value = os.environ.get("MIN_CONFIDENCE")
            os.environ["MIN_CONFIDENCE"] = "0.55"

            def fake_write_env_value(key: str, value: str) -> None:
                env_path.write_text(f"{key}={value}\n", encoding="utf-8")

            try:
                with (
                    patch.object(main, "ENV_PATH", env_path),
                    patch.object(main, "_write_env_value", side_effect=fake_write_env_value),
                ):
                    result = main._apply_env_config_payload({"MIN_CONFIDENCE": 0.61})
                    self.assertEqual(env_path.read_text(encoding="utf-8"), "MIN_CONFIDENCE=0.61\n")
                    self.assertEqual(os.environ.get("MIN_CONFIDENCE"), "0.61")
                    main._restore_env_config_payload(result["snapshot"])

                self.assertEqual(env_path.read_text(encoding="utf-8"), "MIN_CONFIDENCE=0.55\n")
                self.assertEqual(os.environ.get("MIN_CONFIDENCE"), "0.55")
            finally:
                if original_value is None:
                    os.environ.pop("MIN_CONFIDENCE", None)
                else:
                    os.environ["MIN_CONFIDENCE"] = original_value

    def test_replay_search_file_getters_default_to_checked_in_specs(self) -> None:
        with (
            patch.object(config, "_get_env_file_value", return_value=None),
            patch.object(config, "_get", side_effect=lambda _name, default="": default),
        ):
            self.assertEqual(config.replay_search_base_policy_file(), "replay_search_specs/base_policy.json")
            self.assertEqual(config.replay_search_grid_file(), "replay_search_specs/grid.json")
            self.assertEqual(config.replay_search_constraints_file(), "replay_search_specs/constraints.json")
            self.assertEqual(config.replay_search_score_weights_file(), "replay_search_specs/score_weights.json")

    def test_replay_search_score_weights_reject_unknown_keys_before_subprocess(self) -> None:
        with (
            patch.object(config, "_load_json_object_file", return_value={}),
            patch.object(config, "_get_env_file_json_object", return_value={"not_a_real_penalty": 1}),
        ):
            with self.assertRaisesRegex(config.ConfigError, "Unknown replay-search score-weight key"):
                config.replay_search_score_weights()

    def test_replay_search_score_weights_reject_invalid_values_before_subprocess(self) -> None:
        with (
            patch.object(config, "_load_json_object_file", return_value={}),
            patch.object(config, "_get_env_file_json_object", return_value={"drawdown_penalty": "abc"}),
        ):
            with self.assertRaisesRegex(config.ConfigError, "must be a finite non-negative number"):
                config.replay_search_score_weights()

        with (
            patch.object(config, "_load_json_object_file", return_value={}),
            patch.object(config, "_get_env_file_json_object", return_value={"drawdown_penalty": -1}),
        ):
            with self.assertRaisesRegex(config.ConfigError, "must be a finite non-negative number"):
                config.replay_search_score_weights()

    def test_replay_auto_promote_defaults_true_without_explicit_env(self) -> None:
        def fake_get(name: str, default: str = "") -> str:
            return default

        with (
            patch.object(config, "_get_env_file_value", return_value=None),
            patch.object(config, "_get", side_effect=fake_get),
        ):
            self.assertTrue(config.replay_auto_promote())

    def test_validate_startup_blocks_live_until_post_promotion_shadow_history_is_ready(self) -> None:
        valid_wallet = "0x1111111111111111111111111111111111111111"
        watched_wallet = "0x2222222222222222222222222222222222222222"
        with patch.dict(
            "os.environ",
            {
                "USE_REAL_MONEY": "true",
                "POLYGON_PRIVATE_KEY": "0xabc123",
                "POLYGON_WALLET_ADDRESS": valid_wallet,
                "LIVE_MIN_SHADOW_RESOLVED_SINCE_PROMOTION": "20",
            },
            clear=False,
        ), patch.object(main, "WATCHED_WALLETS", [watched_wallet]), patch(
            "main._resolved_shadow_trade_count", return_value=999
        ), patch(
            "main._resolved_shadow_trade_count_since_last_promotion",
            return_value=(5, {"applied_at": 1_700_000_000}),
        ), patch("main.send_alert"):
            with self.assertRaises(RuntimeError) as ctx:
                main._validate_startup()

        self.assertIn("LIVE mode is blocked until post-promotion shadow history is available", str(ctx.exception))
        self.assertIn("5 resolved shadow trades since last replay promotion at 1700000000 < required 20", str(ctx.exception))

    def test_entry_risk_does_not_block_only_because_many_positions_are_open(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (20.0, {"other-market": 5.0}, {"0xother": 5.0})

        with patch("executor.use_real_money", return_value=True), patch("executor.max_total_open_exposure_fraction", return_value=0.60), patch("executor.max_market_exposure_fraction", return_value=0.20), patch("executor.max_trader_exposure_fraction", return_value=0.30):
            reason = executor.entry_risk_block_reason(
                market_id="market-1",
                trader_address="0xabc",
                proposed_size_usd=5.0,
                account_equity=100.0,
            )

        self.assertIsNone(reason)

    def test_entry_risk_does_not_hard_block_after_total_exposure_clip(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (680.0, {"other-market": 120.0}, {"0xother": 120.0})

        with patch("executor.use_real_money", return_value=True), patch(
            "executor.max_total_open_exposure_fraction", return_value=0.30
        ), patch("executor.max_market_exposure_fraction", return_value=0.20), patch(
            "executor.max_trader_exposure_fraction", return_value=0.20
        ):
            reason = executor.entry_risk_block_reason(
                market_id="market-1",
                trader_address="0xabc",
                proposed_size_usd=11.59,
                account_equity=2305.31,
            )

        self.assertIsNone(reason)

    def test_entry_risk_still_blocks_market_concentration(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (50.0, {"market-1": 15.0}, {"0xother": 5.0})

        with patch("executor.use_real_money", return_value=True), patch(
            "executor.max_market_exposure_fraction", return_value=0.20
        ), patch("executor.max_trader_exposure_fraction", return_value=0.30):
            reason = executor.entry_risk_block_reason(
                market_id="market-1",
                trader_address="0xabc",
                proposed_size_usd=10.5,
                account_equity=100.0,
            )

        self.assertIn("market exposure for market-1 would be $25.50", str(reason))

    def test_entry_risk_still_blocks_trader_concentration(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (50.0, {"other-market": 5.0}, {"0xabc": 25.0})

        with patch("executor.use_real_money", return_value=True), patch(
            "executor.max_market_exposure_fraction", return_value=0.40
        ), patch("executor.max_trader_exposure_fraction", return_value=0.30):
            reason = executor.entry_risk_block_reason(
                market_id="market-1",
                trader_address="0xabc",
                proposed_size_usd=6.0,
                account_equity=100.0,
            )

        self.assertIn("trader exposure for 0xabc would be $31.00", str(reason))

    def test_total_open_exposure_decision_clips_to_remaining_headroom(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (260.0, {}, {})

        with patch("executor.use_real_money", return_value=True), patch(
            "executor.max_total_open_exposure_fraction", return_value=0.30
        ):
            decision = executor.total_open_exposure_decision(
                proposed_size_usd=80.0,
                account_equity=1000.0,
            )

        self.assertTrue(decision.clipped)
        self.assertIsNone(decision.block_reason)
        self.assertEqual(decision.allowed_size_usd, 40.0)

    def test_apply_total_exposure_cap_to_sizing_rescales_effective_fraction(self) -> None:
        executor = Mock()
        executor.total_open_exposure_decision.return_value = TotalExposureDecision(
            allowed_size_usd=40.0,
            clipped=True,
        )

        sizing, clip_note = main._apply_total_exposure_cap_to_sizing(
            executor,
            {"dollar_size": 80.0, "kelly_f": 0.08, "reason": "ok"},
            bankroll=1000.0,
            account_equity=1000.0,
        )

        self.assertEqual(sizing["dollar_size"], 40.0)
        self.assertAlmostEqual(sizing["kelly_f"], 0.04, places=6)
        self.assertEqual(sizing["reason"], "ok")
        self.assertEqual(clip_note, "total exposure cap clipped size from $80.00 to $40.00")

    def test_apply_total_exposure_cap_to_sizing_blocks_when_headroom_falls_below_minimum_bet(self) -> None:
        executor = Mock()
        executor.total_open_exposure_decision.return_value = TotalExposureDecision(
            allowed_size_usd=0.75,
            clipped=True,
        )

        with patch("main.min_bet_usd", return_value=1.0):
            sizing, clip_note = main._apply_total_exposure_cap_to_sizing(
                executor,
                {"dollar_size": 10.0, "kelly_f": 0.01, "reason": "ok"},
                bankroll=1000.0,
                account_equity=1000.0,
            )

        self.assertEqual(sizing["dollar_size"], 0.0)
        self.assertEqual(sizing["kelly_f"], 0.0)
        self.assertIn("remaining total exposure headroom was $0.75", sizing["reason"])
        self.assertIsNone(clip_note)

    def test_apply_total_exposure_cap_to_entry_cost_converts_total_headroom_back_to_gross_size(self) -> None:
        executor = Mock()
        executor.total_open_exposure_decision.return_value = TotalExposureDecision(
            allowed_size_usd=9.75,
            clipped=True,
        )
        fill_economics = build_entry_economics(
            gross_price=0.5,
            gross_shares=20.0,
            gross_spent_usd=10.0,
            fee_rate_bps=0,
            fixed_cost_usd=0.25,
            include_expected_exit_fee_in_sizing=False,
            expected_close_fixed_cost_usd=0.0,
        )

        allowed_size_usd, block_reason, clip_note = main._apply_total_exposure_cap_to_entry_cost(
            executor,
            requested_size_usd=10.0,
            fill_economics=fill_economics,
            account_equity=1000.0,
        )

        self.assertAlmostEqual(allowed_size_usd, 9.5, places=6)
        self.assertIsNone(block_reason)
        self.assertEqual(clip_note, "total exposure cap clipped size from $10.00 to $9.50")

    def test_get_fee_rate_bps_uses_stale_cache_when_refresh_fails(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._fee_rate_cache = {"token-1": (600.0, 17)}

        with patch("executor.time.time", return_value=1_000.0), patch(
            "executor.httpx.Client", side_effect=RuntimeError("boom")
        ):
            fee_rate_bps, fee_reason = executor.get_fee_rate_bps("token-1")

        self.assertEqual(fee_rate_bps, 17)
        self.assertIsNone(fee_reason)

    def test_get_fee_rate_bps_blocks_when_lookup_fails_without_cache(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._fee_rate_cache = {}

        with patch("executor.httpx.Client", side_effect=RuntimeError("boom")):
            fee_rate_bps, fee_reason = executor.get_fee_rate_bps("token-1")

        self.assertIsNone(fee_rate_bps)
        self.assertIn("unavailable", str(fee_reason))

    def test_trader_cache_refresh_rotates_batched_wallets(self) -> None:
        wallets = ["0x1", "0x2", "0x3"]
        calls: list[str] = []
        original_cursor = trader_scorer._refresh_cursor
        try:
            trader_scorer._refresh_cursor = 0
            with patch.object(trader_scorer, "TRADER_CACHE_REFRESH_BATCH_SIZE", 2), patch("trader_scorer._trader_cache_updated_at_map", return_value={}), patch("trader_scorer.get_trader_features", side_effect=lambda wallet, observed_size_usd, force_refresh=False: calls.append(wallet) or SimpleNamespace()):
                trader_scorer.refresh_trader_cache(wallets)
                trader_scorer.refresh_trader_cache(wallets)
        finally:
            trader_scorer._refresh_cursor = original_cursor

        self.assertEqual(calls[:2], ["0x1", "0x2"])
        self.assertEqual(calls[2:], ["0x3", "0x1"])

    def test_trader_remote_429_arms_backoff(self) -> None:
        response = httpx.Response(
            429,
            headers={"Retry-After": "30"},
            request=httpx.Request("GET", "https://data-api.polymarket.com/closed-positions"),
        )

        class FakeClient:
            def get(self, url, params=None):
                return response

        original_backoff = trader_scorer._remote_backoff_until
        try:
            trader_scorer._remote_backoff_until = 0.0
            with patch("time.time", return_value=1_000.0):
                payload, ok = trader_scorer._request_data_api_json(
                    FakeClient(),
                    "https://data-api.polymarket.com/closed-positions",
                    params={"user": "0xabc"},
                    failure_log="test failure",
                )
                active = trader_scorer._remote_backoff_active(1_000.0)
                remaining = trader_scorer._remote_backoff_remaining_seconds(1_000.0)
        finally:
            trader_scorer._remote_backoff_until = original_backoff

        self.assertIsNone(payload)
        self.assertFalse(ok)
        self.assertTrue(active)
        self.assertGreaterEqual(remaining, trader_scorer.REMOTE_BACKOFF_DEFAULT_S)

    def test_sync_belief_priors_expands_sql_contract_macros(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd, resolved_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "belief-1",
                        "market-1",
                        "Will it happen?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.4,
                        10.0,
                        0.7,
                        0.1,
                        0,
                        0,
                        1_700_000_000,
                        0.4,
                        25.0,
                        10.0,
                        1.5,
                        1_700_000_100,
                    ),
                )
                conn.commit()
                conn.close()

                beliefs.invalidate_belief_cache()
                applied = beliefs.sync_belief_priors()

                self.assertEqual(applied, 1)

                conn = db.get_conn()
                update_count = conn.execute("SELECT COUNT(*) AS n FROM belief_updates").fetchone()["n"]
                prior_count = conn.execute("SELECT COUNT(*) AS n FROM belief_priors").fetchone()["n"]
                conn.close()

                self.assertEqual(update_count, 1)
                self.assertGreater(prior_count, 0)
            finally:
                db.DB_PATH = original_db_path

    def test_startup_validation_reports_bad_numeric_config_cleanly(self) -> None:
        with patch("main.WATCHED_WALLETS", ["0xabc"]), patch("main.min_confidence", side_effect=main.ConfigError("MIN_CONFIDENCE must be numeric, got 'abc'")):
            with self.assertRaisesRegex(RuntimeError, "MIN_CONFIDENCE must be numeric, got 'abc'"):
                main._validate_startup()

    def test_persist_startup_validation_failure_writes_state_even_when_runtime_getters_are_broken(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = main.BOT_STATE_FILE
            try:
                main.BOT_STATE_FILE = Path(tmpdir) / "bot_state.json"
                with (
                    patch("main.use_real_money", side_effect=main.ConfigError("USE_REAL_MONEY must be boolean")),
                    patch("main.poll_interval", side_effect=main.ConfigError("POLL_INTERVAL must be numeric")),
                    patch.object(main, "WATCHED_WALLETS", ["0xabc"]),
                ):
                    main._persist_startup_validation_failure(
                        ["MIN_CONFIDENCE must be numeric, got 'abc'", "MAX_BET_FRACTION must be between 0 and 1, got 2"],
                        ["warning text"],
                    )

                payload = json.loads(main.BOT_STATE_FILE.read_text(encoding="utf-8"))
                self.assertTrue(payload["startup_failed"])
                self.assertTrue(payload["startup_validation_failed"])
                self.assertEqual(payload["startup_detail"], "startup validation failed: 2 errors")
                self.assertIn("MIN_CONFIDENCE must be numeric, got 'abc'", payload["startup_failure_message"])
                self.assertIn("MIN_CONFIDENCE must be numeric, got 'abc'", payload["startup_validation_message"])
                self.assertIn("warning text", payload["startup_validation_message"])
                self.assertEqual(payload["mode"], "shadow")
                self.assertEqual(payload["poll_interval"], 0.0)
                self.assertEqual(payload["n_wallets"], 1)
                self.assertEqual(payload["last_poll_at"], 0)
                self.assertFalse(payload["loop_in_progress"])
            finally:
                main.BOT_STATE_FILE = original_state_file

    def test_persist_startup_validation_failure_clears_stale_prior_session_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = main.BOT_STATE_FILE
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                main.BOT_STATE_FILE = Path(tmpdir) / "bot_state.json"
                db.DB_PATH = Path(tmpdir) / "data" / "empty.db"
                main.DB_PATH = db.DB_PATH
                main.BOT_STATE_FILE.write_text(
                    json.dumps(
                        {
                            "session_id": "old-session",
                            "last_replay_search_status": "completed",
                            "last_replay_promotion_status": "applied",
                            "shadow_history_state_known": True,
                            "resolved_shadow_trade_count": 99,
                            "loaded_scorer": "xgboost",
                            "model_artifact_exists": True,
                            "model_artifact_path": "/tmp/old-model.joblib",
                            "last_poll_at": 123,
                            "loop_in_progress": True,
                        }
                    ),
                    encoding="utf-8",
                )
                with (
                    patch("main.use_real_money", return_value=False),
                    patch("main.poll_interval", return_value=5.0),
                    patch.object(main, "WATCHED_WALLETS", ["0xabc"]),
                ):
                    main._persist_startup_validation_failure(
                        ["MIN_CONFIDENCE must be numeric, got 'abc'"],
                        [],
                    )

                payload = json.loads(main.BOT_STATE_FILE.read_text(encoding="utf-8"))
                self.assertTrue(payload["startup_failed"])
                self.assertTrue(payload["startup_validation_failed"])
                self.assertNotEqual(payload["session_id"], "old-session")
                self.assertEqual(payload["last_poll_at"], 0)
                self.assertFalse(payload["loop_in_progress"])
                self.assertEqual(payload["last_replay_search_status"], "")
                self.assertEqual(payload["last_replay_promotion_status"], "")
                self.assertFalse(payload["shadow_history_state_known"])
                self.assertEqual(payload["resolved_shadow_trade_count"], 0)
                self.assertEqual(payload["loaded_scorer"], "heuristic")
                self.assertFalse(payload["model_artifact_exists"])
                self.assertEqual(payload["model_artifact_path"], "")
            finally:
                main.BOT_STATE_FILE = original_state_file
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

    def test_persist_startup_failure_rehydrates_durable_persisted_history(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = main.BOT_STATE_FILE
            original_db_path = db.DB_PATH
            original_main_db_path = main.DB_PATH
            try:
                main.BOT_STATE_FILE = Path(tmpdir) / "bot_state.json"
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                main.DB_PATH = db.DB_PATH
                db.init_db()
                conn = db.get_conn()
                try:
                    conn.execute(
                        """
                        INSERT INTO retrain_runs (
                            started_at, finished_at, trigger, status, ok, deployed,
                            sample_count, min_samples, message
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (100, 120, "manual", "deployed", 1, 1, 42, 30, "latest retrain"),
                    )
                    replay_search_run_id = int(
                        conn.execute(
                            """
                            INSERT INTO replay_search_runs (
                                started_at, finished_at, request_token, label_prefix, status,
                                base_policy_json, grid_json, constraints_json,
                                candidate_count, feasible_count, best_feasible_score, best_feasible_total_pnl_usd
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (200, 230, "req-1", "scheduled", "completed", "{}", "{}", "{}", 17, 6, 1.75, 42.5),
                        ).lastrowid
                        or 0
                    )
                    conn.commit()
                finally:
                    conn.close()

                main._insert_replay_promotion(
                    {
                        "requested_at": 300,
                        "finished_at": 310,
                        "applied_at": 315,
                        "trigger": "scheduled_replay_search",
                        "scope": "shadow_only",
                        "source_mode": "shadow",
                        "status": "applied",
                        "reason": "auto-promoted best feasible replay candidate",
                        "replay_search_run_id": replay_search_run_id,
                        "replay_search_candidate_id": None,
                    }
                )
                main._insert_replay_promotion(
                    {
                        "requested_at": 320,
                        "finished_at": 330,
                        "applied_at": 0,
                        "trigger": "scheduled_replay_search",
                        "scope": "shadow_only",
                        "source_mode": "shadow",
                        "status": "skipped_score_delta",
                        "reason": "score delta too small",
                        "replay_search_run_id": replay_search_run_id,
                        "replay_search_candidate_id": None,
                    }
                )
                _insert_resolved_shadow_trade_for_promotion_test(
                    trade_id="shadow-after-promotion",
                    resolved_at=400,
                )

                with (
                    patch("main.use_real_money", return_value=False),
                    patch("main.poll_interval", return_value=5.0),
                    patch.object(main, "WATCHED_WALLETS", ["0xabc"]),
                    patch("main.live_require_shadow_history", return_value=True),
                    patch("main.live_min_shadow_resolved", return_value=2),
                    patch("main.live_min_shadow_resolved_since_promotion", return_value=1),
                ):
                    main._persist_startup_failure_state(
                        detail="startup failed: belief sync exploded",
                        message="startup failed: belief sync exploded",
                        validation_failed=False,
                    )

                payload = json.loads(main.BOT_STATE_FILE.read_text(encoding="utf-8"))
                self.assertTrue(payload["startup_failed"])
                self.assertTrue(payload["shadow_history_state_known"])
                self.assertEqual(payload["resolved_shadow_trade_count"], 1)
                self.assertEqual(payload["resolved_shadow_since_last_promotion"], 1)
                self.assertTrue(payload["live_require_shadow_history_enabled"])
                self.assertEqual(payload["live_min_shadow_resolved"], 2)
                self.assertFalse(payload["live_shadow_history_total_ready"])
                self.assertEqual(payload["live_min_shadow_resolved_since_last_promotion"], 1)
                self.assertTrue(payload["live_shadow_history_ready"])
                self.assertEqual(payload["last_retrain_status"], "deployed")
                self.assertEqual(payload["last_retrain_message"], "latest retrain")
                self.assertEqual(payload["last_replay_search_status"], "completed")
                self.assertEqual(payload["last_replay_search_run_id"], replay_search_run_id)
                self.assertEqual(payload["last_replay_promotion_status"], "skipped_score_delta")
                self.assertEqual(payload["last_replay_promotion_message"], "score delta too small")
                self.assertEqual(payload["last_applied_replay_promotion_status"], "applied")
                self.assertEqual(
                    payload["last_applied_replay_promotion_message"],
                    "auto-promoted best feasible replay candidate",
                )
            finally:
                main.BOT_STATE_FILE = original_state_file
                db.DB_PATH = original_db_path
                main.DB_PATH = original_main_db_path

    def test_validate_startup_persists_failure_state_before_raising(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = main.BOT_STATE_FILE
            try:
                main.BOT_STATE_FILE = Path(tmpdir) / "bot_state.json"
                with (
                    patch.object(main, "WATCHED_WALLETS", ["0xabc"]),
                    patch("main.min_confidence", side_effect=main.ConfigError("MIN_CONFIDENCE must be numeric, got 'abc'")),
                    patch("main.poll_interval", side_effect=main.ConfigError("POLL_INTERVAL must be numeric")),
                    patch("main.use_real_money", return_value=False),
                ):
                    with self.assertRaisesRegex(RuntimeError, "MIN_CONFIDENCE must be numeric, got 'abc'"):
                        main._validate_startup()

                payload = json.loads(main.BOT_STATE_FILE.read_text(encoding="utf-8"))
                self.assertTrue(payload["startup_failed"])
                self.assertTrue(payload["startup_validation_failed"])
                self.assertEqual(
                    payload["startup_detail"],
                    "startup validation failed: MIN_CONFIDENCE must be numeric, got 'abc'",
                )
                self.assertIn("MIN_CONFIDENCE must be numeric, got 'abc'", payload["startup_failure_message"])
                self.assertIn("MIN_CONFIDENCE must be numeric, got 'abc'", payload["startup_validation_message"])
                self.assertEqual(payload["poll_interval"], 0.0)
            finally:
                main.BOT_STATE_FILE = original_state_file

    def test_main_persists_late_startup_failure_after_dashboard_server_starts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = main.BOT_STATE_FILE
            original_event_file = main.EVENT_FILE
            dashboard_server = SimpleNamespace(stop=Mock())
            watchlist_stub = SimpleNamespace(
                state_fields=lambda: {
                    "tracked_wallet_count": 1,
                    "dropped_wallet_count": 0,
                    "hot_wallet_count": 1,
                    "warm_wallet_count": 0,
                    "discovery_wallet_count": 0,
                },
                startup_wallets=lambda: [],
            )
            try:
                main.BOT_STATE_FILE = Path(tmpdir) / "bot_state.json"
                main.EVENT_FILE = Path(tmpdir) / "events.jsonl"
                with (
                    patch.object(main, "WATCHED_WALLETS", ["0xabc"]),
                    patch("main.init_db"),
                    patch("main._validate_startup"),
                    patch("main._write_bot_pid_file"),
                    patch("main._clear_bot_pid_file"),
                    patch("main._repair_event_file_market_urls"),
                    patch("main._install_shutdown_signal_handlers", return_value=[]),
                    patch("main._restore_shutdown_signal_handlers"),
                    patch("main._latest_replay_promotion", return_value=None),
                    patch("main._latest_applied_replay_promotion", return_value=None),
                    patch("main.WatchlistManager", return_value=watchlist_stub),
                    patch("main.start_dashboard_api_server", return_value=dashboard_server),
                    patch("main.sync_belief_priors", side_effect=RuntimeError("belief sync exploded")),
                    patch("main.use_real_money", return_value=False),
                    patch("main.poll_interval", return_value=5.0),
                    patch("main.send_alert"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "belief sync exploded"):
                        main.main()

                payload = json.loads(main.BOT_STATE_FILE.read_text(encoding="utf-8"))
                self.assertTrue(payload["startup_failed"])
                self.assertFalse(payload["startup_validation_failed"])
                self.assertEqual(payload["startup_detail"], "startup failed: belief sync exploded")
                self.assertEqual(payload["startup_failure_message"], "startup failed: belief sync exploded")
                self.assertEqual(payload["mode"], "shadow")
                self.assertEqual(payload["poll_interval"], 5.0)
                dashboard_server.stop.assert_called_once()
            finally:
                main.BOT_STATE_FILE = original_state_file
                main.EVENT_FILE = original_event_file

    def test_partial_bot_state_heartbeat_preserves_last_completed_poll(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = main.BOT_STATE_FILE
            try:
                main.BOT_STATE_FILE = Path(tmpdir) / "bot_state.json"
                with patch("main.use_real_money", return_value=False), patch("main.poll_interval", return_value=2.0), patch.object(main, "WATCHED_WALLETS", ["0xabc"]):
                    main._write_bot_state(
                        started_at=100,
                        last_poll_at=120,
                        last_poll_duration_s=8.5,
                        loop_in_progress=False,
                    )
                    main._write_bot_state(
                        last_activity_at=130,
                        last_loop_started_at=125,
                        loop_in_progress=True,
                    )

                payload = json.loads(main.BOT_STATE_FILE.read_text(encoding="utf-8"))
                self.assertEqual(payload["last_poll_at"], 120)
                self.assertEqual(payload["last_poll_duration_s"], 8.5)
                self.assertEqual(payload["last_activity_at"], 130)
                self.assertEqual(payload["last_loop_started_at"], 125)
                self.assertTrue(payload["loop_in_progress"])
            finally:
                main.BOT_STATE_FILE = original_state_file

    def test_log_runtime_ready_explains_quiet_console_and_runtime_files(self) -> None:
        tracker_stub = SimpleNamespace(wallets=["0xabc", "0xdef"])
        watchlist_stub = SimpleNamespace(
            state_fields=lambda: {
                "tracked_wallet_count": 2,
                "dropped_wallet_count": 1,
                "hot_wallet_count": 1,
                "warm_wallet_count": 1,
                "discovery_wallet_count": 0,
            }
        )

        with patch("main.poll_interval", return_value=5.0):
            with self.assertLogs("main", level="INFO") as captured:
                main._log_runtime_ready(tracker_stub, watchlist_stub)

        output = "\n".join(captured.output)
        self.assertIn("Startup complete. Polling 2 wallets every 5.0s", output)
        self.assertIn("Runtime files: db=", output)
        self.assertIn("Console output stays quiet between events.", output)

    def test_log_first_poll_summary_reports_initial_poll_completion(self) -> None:
        with self.assertLogs("main", level="INFO") as captured:
            main._log_first_poll_summary(
                elapsed=3.25,
                polled_wallet_count=4,
                event_count=0,
                bankroll=1000.0,
            )

        output = "\n".join(captured.output)
        self.assertIn("First poll completed in 3.25s: wallets=4 events=0 bankroll=$1000.00", output)

    def test_run_deferred_startup_tasks_continues_after_step_failure(self) -> None:
        tracker_stub = SimpleNamespace(
            prime_identities=Mock(side_effect=RuntimeError("identity failure")),
            seen_ids=set(),
        )
        watchlist_stub = SimpleNamespace(
            refresh=Mock(),
            state_fields=lambda: {"tracked_wallet_count": 2},
        )
        dedup_stub = SimpleNamespace(
            load_from_db=Mock(),
            seen_ids={"trade-1", "trade-2"},
        )
        run_retrain_job = Mock()

        with (
            patch("main.refresh_trader_cache") as refresh_cache,
            patch("main._resolve_trades_and_alert", return_value=[]),
            patch("main.should_retrain_early", return_value=True),
        ):
            persist_state = Mock()
            main._run_deferred_startup_tasks(
                startup_wallets=["0xabc"],
                tracker=tracker_stub,
                watchlist=watchlist_stub,
                dedup=dedup_stub,
                engine=SimpleNamespace(),
                persist_state=persist_state,
                run_retrain_job=run_retrain_job,
            )

        refresh_cache.assert_called_once_with(["0xabc"])
        watchlist_stub.refresh.assert_called_once_with(run_auto_drop=True)
        persist_state.assert_called_once_with(tracked_wallet_count=2)
        dedup_stub.load_from_db.assert_called_once_with(rebuild_shadow_positions=False)
        self.assertEqual(tracker_stub.seen_ids, {"trade-1", "trade-2"})
        run_retrain_job.assert_called_once_with("startup")

    def test_prime_identities_uses_fresh_client_not_shared_poll_client(self) -> None:
        class DummyClient:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        wallet = "0x" + ("1" * 40)
        with patch.object(tracker.PolymarketTracker, "_load_wallet_cursors", return_value={}):
            poller = tracker.PolymarketTracker([wallet])
        shared_client = poller.client
        dummy_client = DummyClient()
        try:
            with (
                patch.object(tracker.PolymarketTracker, "_new_http_client", return_value=dummy_client),
                patch("tracker.resolve_username_for_wallet") as resolve_username,
            ):
                poller.prime_identities([wallet])

            resolve_username.assert_called_once()
            self.assertIs(resolve_username.call_args.kwargs["client"], dummy_client)
            self.assertIsNot(resolve_username.call_args.kwargs["client"], shared_client)
        finally:
            poller.close()

    def test_partial_exit_keeps_remaining_shadow_position_and_realized_pnl(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                buy_event = SimpleNamespace(
                    action="buy",
                    token_id="token-1",
                    timestamp=1_700_000_000,
                    trader_name="Trader",
                    observed_at=1_700_000_005,
                    poll_started_at=1_700_000_004,
                    market_close_ts=1_700_010_000,
                    metadata_fetched_at=1_700_000_004,
                    orderbook_fetched_at=1_700_000_004,
                    source_ts_raw="1700000000",
                    shares=100.0,
                    size_usd=50.0,
                    question="Will it happen?",
                    raw_trade=None,
                    raw_market_metadata=None,
                    raw_orderbook=None,
                    snapshot=None,
                    trader_address="0xabc",
                )
                row_id = log_trade(
                    trade_id="buy-1",
                    market_id="market-1",
                    question="Will it happen?",
                    trader_address="0xabc",
                    side="yes",
                    price=0.5,
                    signal_size_usd=50.0,
                    confidence=0.7,
                    kelly_f=0.1,
                    real_money=False,
                    order_id=None,
                    skipped=False,
                    skip_reason=None,
                    actual_entry_price=0.5,
                    actual_entry_shares=100.0,
                    actual_entry_size_usd=50.0,
                    event=buy_event,
                    signal={"mode": "heuristic"},
                )
                conn = db.get_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
                    ("market-1", "yes", 50.0, 0.5, "token-1", 1_700_000_000, 0),
                )
                conn.commit()
                entry_row = conn.execute("SELECT * FROM trade_log WHERE id=?", (row_id,)).fetchone()
                conn.close()

                cache = dedup.DedupeCache()
                cache.load_from_db(rebuild_shadow_positions=True)
                executor = object.__new__(PolymarketExecutor)
                shares, exit_notional, pnl = executor._finalize_exit(
                    entries=[dict(entry_row)],
                    position={"market_id": "market-1", "token_id": "token-1", "side": "yes"},
                    real_money=False,
                    exit_trade_id="sell-1",
                    exit_price=0.6,
                    exit_fraction=0.4,
                    exit_shares=40.0,
                    exit_notional=24.0,
                    exit_reason="partial exit",
                    exit_order_id=None,
                    market_id="market-1",
                    trader_address="0xabc",
                    dedup=cache,
                    refresh_position_from_trade_log=True,
                )

                self.assertAlmostEqual(shares, 40.0, places=6)
                self.assertAlmostEqual(exit_notional, 24.0, places=6)
                self.assertAlmostEqual(pnl, 4.0, places=2)

                conn = db.get_conn()
                updated = conn.execute(
                    """
                    SELECT remaining_entry_shares, remaining_entry_size_usd, realized_exit_shares,
                           realized_exit_size_usd, realized_exit_pnl_usd, partial_exit_count, shadow_pnl_usd
                    FROM trade_log
                    WHERE id=?
                    """,
                    (row_id,),
                ).fetchone()
                position = conn.execute(
                    "SELECT size_usd, avg_price FROM positions WHERE market_id=? AND token_id=? AND real_money=0",
                    ("market-1", "token-1"),
                ).fetchone()
                conn.close()

                self.assertAlmostEqual(float(updated["remaining_entry_shares"]), 60.0, places=6)
                self.assertAlmostEqual(float(updated["remaining_entry_size_usd"]), 30.0, places=6)
                self.assertAlmostEqual(float(updated["realized_exit_shares"]), 40.0, places=6)
                self.assertAlmostEqual(float(updated["realized_exit_size_usd"]), 24.0, places=6)
                self.assertAlmostEqual(float(updated["realized_exit_pnl_usd"]), 4.0, places=6)
                self.assertEqual(int(updated["partial_exit_count"]), 1)
                self.assertIsNone(updated["shadow_pnl_usd"])
                self.assertAlmostEqual(float(position["size_usd"]), 30.0, places=6)
                self.assertAlmostEqual(float(position["avg_price"]), 0.5, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_resolved_shadow_trade_count_uses_fill_aware_realized_rows_only(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("skip-1", "m0", "Skipped", "0x1", "yes", "buy", 0.5, 10.0, 0.6, 0.1, 0, 0, 10, 1.0),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("open-1", "m1", "Open", "0x2", "yes", "buy", 0.5, 10.0, 0.6, 0.1, 0, 0, 11, 0.5, 20.0, 10.0),
                )
                conn.execute(
                    f"""
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("resolved-1", "m2", "Resolved", "0x3", "yes", "buy", 0.5, 10.0, 0.6, 0.1, 0, 0, 12, 0.5, 20.0, 10.0, 3.5),
                )
                conn.commit()
                conn.close()

                self.assertEqual(main._resolved_shadow_trade_count(), 1)
            finally:
                db.DB_PATH = original_db_path

    def test_tracker_cursor_and_stale_checks(self) -> None:
        stale_event = tracker.TradeEvent(
            trade_id="t-old",
            market_id="m1",
            question="Question",
            side="yes",
            action="buy",
            price=0.5,
            shares=10.0,
            size_usd=5.0,
            token_id="token-1",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=100,
            close_time="",
        )
        duplicate_event = tracker.TradeEvent(
            trade_id="t-1",
            market_id="m1",
            question="Question",
            side="yes",
            action="buy",
            price=0.5,
            shares=10.0,
            size_usd=5.0,
            token_id="token-1",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=200,
            close_time="",
        )
        new_same_ts = tracker.TradeEvent(
            trade_id="t-2",
            market_id="m1",
            question="Question",
            side="yes",
            action="buy",
            price=0.5,
            shares=10.0,
            size_usd=5.0,
            token_id="token-1",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=200,
            close_time="",
        )
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        tracker_obj.wallet_cursors = {"0xabc": tracker.WalletCursor(last_source_ts=200, last_trade_ids={"t-1"})}

        self.assertFalse(tracker_obj._is_new_for_wallet("0xabc", duplicate_event))
        self.assertTrue(tracker_obj._is_new_for_wallet("0xabc", new_same_ts))
        with patch("tracker.max_source_trade_age_seconds", return_value=30):
            self.assertTrue(tracker_obj._is_stale_event(stale_event, poll_started_at=200))
            self.assertFalse(tracker_obj._is_stale_event(new_same_ts, poll_started_at=220))

    def test_tracker_rejects_missing_timestamp_and_missing_price(self) -> None:
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        tracker_obj.client = object()
        tracker_obj.get_market_metadata = lambda _condition_id: (
            {
                "question": "Will it happen?",
                "endDate": "2030-01-01T00:00:00Z",
                "outcomes": '["Yes","No"]',
                "clobTokenIds": '["token-yes","token-no"]',
            },
            123,
        )

        raw_missing_timestamp = {
            "conditionId": "market-1",
            "side": "BUY",
            "asset": "token-yes",
            "size": 10,
            "price": 0.55,
        }
        raw_missing_price = {
            "conditionId": "market-1",
            "side": "BUY",
            "asset": "token-yes",
            "size": 10,
            "timestamp": 1_700_000_000,
        }

        with patch("tracker.hydrate_observed_identity", return_value="Trader"):
            self.assertIsNone(tracker_obj._parse_raw_trade(raw_missing_timestamp, "0xabc", 1_700_000_010))
            self.assertIsNone(tracker_obj._parse_raw_trade(raw_missing_price, "0xabc", 1_700_000_010))

    def test_tracker_resolves_outcome_from_metadata_token_map(self) -> None:
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        tracker_obj.client = object()
        tracker_obj.get_market_metadata = lambda _condition_id: (
            {
                "question": "Will it happen?",
                "endDate": "2030-01-01T00:00:00Z",
                "outcomes": '["Yes","No"]',
                "clobTokenIds": '["token-yes","token-no"]',
            },
            456,
        )
        raw = {
            "conditionId": "market-1",
            "side": "BUY",
            "asset": "token-no",
            "size": 12,
            "price": 0.42,
            "timestamp": 1_700_000_000,
            "title": "Will it happen?",
        }

        with patch("tracker.hydrate_observed_identity", return_value="Trader"):
            event = tracker_obj._parse_raw_trade(raw, "0xabc", 1_700_000_010)

        self.assertIsNotNone(event)
        self.assertEqual(event.side, "no")
        self.assertEqual(event.price, 0.42)
        self.assertEqual(event.timestamp, 1_700_000_000)

    def test_tracker_batches_wallet_trade_fetches_in_parallel(self) -> None:
        tracker_obj = tracker.PolymarketTracker(["0x1", "0x2", "0x3", "0x4"])
        tracker_obj.get_wallet_trades = lambda address, limit=50: time.sleep(0.12) or []
        try:
            started = time.perf_counter()
            result = tracker_obj._fetch_wallet_trades_batch(["0x1", "0x2", "0x3", "0x4"])
            elapsed = time.perf_counter() - started
        finally:
            tracker_obj.close()

        self.assertEqual(result, {"0x1": [], "0x2": [], "0x3": [], "0x4": []})
        self.assertLess(elapsed, 0.35)

    def test_tracker_load_wallet_cursors_ignores_malformed_json_bytes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO wallet_cursors (
                        wallet_address, last_source_ts, last_trade_ids_json, updated_at
                    ) VALUES (?, ?, CAST(X'80FF5B22' AS BLOB), ?)
                    """,
                    ("0xabc", 123, int(time.time())),
                )
                conn.commit()
                conn.close()

                tracker_obj = tracker.PolymarketTracker(["0xabc"])
                try:
                    cursor = tracker_obj.wallet_cursors["0xabc"]
                finally:
                    tracker_obj.close()

                self.assertEqual(cursor.last_source_ts, 123)
                self.assertEqual(cursor.last_trade_ids, set())
            finally:
                db.DB_PATH = original_db_path

    def test_tracker_poll_filters_old_rows_before_metadata_and_skips_price_history_fetch(self) -> None:
        tracker_obj = tracker.PolymarketTracker(["0xabc"])
        now_ts = int(time.time())
        tracker_obj.wallet_cursors = {
            "0xabc": tracker.WalletCursor(last_source_ts=now_ts - 90, last_trade_ids={"t-seen"})
        }
        tracker_obj._flush_dirty_wallet_cursors = lambda: None

        old_raw = {
            "id": "t-old",
            "conditionId": "market-old",
            "side": "BUY",
            "asset": "token-old",
            "size": 10,
            "price": 0.41,
            "timestamp": now_ts - 120,
            "title": "Old market",
        }
        new_raw = {
            "id": "t-new",
            "conditionId": "market-new",
            "side": "BUY",
            "asset": "token-yes",
            "size": 10,
            "price": 0.55,
            "timestamp": now_ts - 30,
            "title": "New market",
        }

        metadata_calls: list[tuple[str, ...]] = []
        orderbook_calls: list[tuple[str, ...]] = []
        price_history_calls: list[str] = []

        tracker_obj._fetch_wallet_trades_batch = lambda wallets, limit=50: {"0xabc": [old_raw, new_raw]}
        tracker_obj._fetch_market_metadata_batch = (
            lambda condition_ids: metadata_calls.append(tuple(condition_ids)) or {
                "market-new": (
                    {
                        "conditionId": "market-new",
                        "question": "Will it happen?",
                        "endDate": "2030-01-01T00:00:00Z",
                        "outcomes": '["Yes","No"]',
                        "clobTokenIds": '["token-yes","token-no"]',
                    },
                    123,
                )
            }
        )
        tracker_obj._fetch_orderbook_snapshots_batch = (
            lambda token_ids: orderbook_calls.append(tuple(token_ids)) or {
                "token-yes": (
                    {
                        "best_bid": 0.54,
                        "best_ask": 0.56,
                        "mid": 0.55,
                        "bid_depth_usd": 500.0,
                        "ask_depth_usd": 500.0,
                    },
                    {"bids": [], "asks": []},
                    456,
                )
            }
        )
        tracker_obj.get_price_history = lambda token_id, interval="1h": price_history_calls.append(token_id) or []

        try:
            with patch("tracker.hydrate_observed_identity", return_value="Trader"):
                events = tracker_obj.poll(["0xabc"], trade_limit=50)
        finally:
            tracker_obj.close()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trade_id, "t-new")
        self.assertEqual(metadata_calls, [("market-new",)])
        self.assertEqual(orderbook_calls, [("token-yes",)])
        self.assertEqual(price_history_calls, [])

    def test_tracker_market_metadata_cache_reuses_recent_fetch(self) -> None:
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        calls: list[str] = []

        def fake_request_json(url: str, **kwargs):
            calls.append(url)
            return ([{"conditionId": "market-1", "question": "Question"}], True)

        tracker_obj._market_metadata_cache = {}
        tracker_obj._request_json = fake_request_json

        first = tracker_obj.get_market_metadata("market-1")
        second = tracker_obj.get_market_metadata("market-1")

        self.assertEqual(first[0]["question"], "Question")
        self.assertEqual(second[0]["question"], "Question")
        self.assertEqual(len(calls), 1)

    def test_hydrate_observed_identity_skips_network_when_disabled(self) -> None:
        wallet = "0x1111111111111111111111111111111111111111"
        with patch("identity_cache.lookup_username", return_value=None), patch(
            "identity_cache.resolve_username_for_wallet"
        ) as resolve_mock:
            resolved = identity_cache.hydrate_observed_identity(
                wallet,
                "",
                allow_network=False,
            )

        self.assertEqual(resolved, "")
        resolve_mock.assert_not_called()

    def test_market_scorer_handles_missing_optional_features(self) -> None:
        snapshot = {
            "best_bid": 0.49,
            "best_ask": 0.51,
            "mid": 0.5,
            "volume_24h_usd": 1000.0,
            "oi_usd": 2500.0,
            "bid_depth_usd": 800.0,
            "ask_depth_usd": 700.0,
            "top_holder_pct": None,
            "price_history_1h": [],
        }
        features = build_market_features(snapshot, "2030-01-01T00:00:00Z", order_size_usd=25.0, execution_price=0.5)
        self.assertIsNotNone(features)
        self.assertIsNone(features.price_1h_ago)
        scorer = MarketScorer()
        score = scorer.score(features)
        self.assertGreaterEqual(score["score"], 0.0)
        self.assertLessEqual(score["score"], 1.0)

    def test_market_scorer_rejects_missing_or_bad_close_time(self) -> None:
        snapshot = {
            "best_bid": 0.49,
            "best_ask": 0.51,
            "mid": 0.5,
            "bid_depth_usd": 800.0,
            "ask_depth_usd": 700.0,
        }
        self.assertIsNone(build_market_features(snapshot, "", order_size_usd=25.0, execution_price=0.5))
        self.assertIsNone(
            build_market_features(snapshot, "not-a-timestamp", order_size_usd=25.0, execution_price=0.5)
        )

    def test_trader_score_win_rate_shrinks_small_samples(self) -> None:
        low_evidence = TraderScorer._score_win_rate(0.9, 2)
        high_evidence = TraderScorer._score_win_rate(0.9, 200)

        self.assertGreater(low_evidence, 0.5)
        self.assertLess(low_evidence, 0.9)
        self.assertGreater(high_evidence, low_evidence)

    def test_stop_loss_checks_trigger_exit_when_estimated_loss_breaches_threshold(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                _insert_open_position_for_stop_loss_test()

                tracker_obj = Mock()
                tracker_obj.get_market_metadata.return_value = (
                    {
                        "question": "Will the stop loss trigger?",
                        "endDate": "2030-01-01T00:00:00Z",
                    },
                    111,
                )
                tracker_obj.get_orderbook_snapshot.return_value = (
                    {
                        "best_bid": 0.38,
                        "best_ask": 0.39,
                        "mid": 0.385,
                    },
                    {
                        "bids": [{"price": "0.38", "size": "200"}],
                        "asks": [{"price": "0.39", "size": "200"}],
                    },
                    222,
                )

                entry = {
                    "remaining_entry_shares": 200.0,
                    "remaining_entry_size_usd": 100.0,
                }
                executor_obj = Mock()
                executor_obj._load_open_position_state.return_value = (
                    {"token_id": "token-stop", "side": "yes", "size_usd": 100.0},
                    [entry],
                )
                executor_obj._entry_open_shares = lambda row: float(row["remaining_entry_shares"])
                executor_obj._entry_open_size = lambda row: float(row["remaining_entry_size_usd"])
                executor_obj.estimate_exit_fill.return_value = (
                    SimulatedFill(spent_usd=76.0, shares=200.0, avg_price=0.38),
                    None,
                )
                executor_obj.estimate_exit_economics.return_value = (
                    SimpleNamespace(net_proceeds_usd=76.0, effective_exit_price=0.38),
                    None,
                )
                executor_obj.execute_exit.return_value = ExecutionResult(
                    True,
                    True,
                    None,
                    76.0,
                    "stop-loss triggered",
                    shares=200.0,
                    pnl_usd=-24.0,
                    action="exit",
                )
                dedup_cache = Mock()

                with patch.dict(
                    os.environ,
                    {
                        "USE_REAL_MONEY": "false",
                        "STOP_LOSS_ENABLED": "true",
                        "STOP_LOSS_MAX_LOSS_PCT": "0.20",
                        "STOP_LOSS_MIN_HOLD": "20m",
                    },
                    clear=False,
                ), patch.object(main, "_emit_event") as emit_mock:
                    main._run_stop_loss_checks(tracker_obj, executor_obj, dedup_cache)

                executor_obj.execute_exit.assert_called_once()
                execute_kwargs = executor_obj.execute_exit.call_args.kwargs
                self.assertTrue(execute_kwargs["trade_id"].startswith("stop-loss-"))
                self.assertIn("exit confirmed", execute_kwargs["reason_override"])
                self.assertIn("-24.0%", execute_kwargs["reason_override"])

                emit_mock.assert_called_once()
                payload = emit_mock.call_args.args[0]
                self.assertEqual(payload["decision"], "STOP LOSS")
                self.assertEqual(payload["action"], "sell")
                self.assertAlmostEqual(payload["estimated_return"], -0.24, places=6)
                self.assertEqual(payload["size_usd"], 76.0)

                conn = db.get_conn()
                audit_row = conn.execute(
                    """
                    SELECT decision, reason, estimated_return_pct, loss_limit_pct
                    FROM exit_audits
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()
                self.assertEqual(audit_row["decision"], "exit")
                self.assertIn("exit confirmed", audit_row["reason"])
                self.assertAlmostEqual(float(audit_row["estimated_return_pct"]), -0.24, places=6)
                self.assertAlmostEqual(float(audit_row["loss_limit_pct"]), 0.20, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_stop_loss_checks_respect_min_hold_window(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                _insert_open_position_for_stop_loss_test(entered_at=int(time.time()) - 60)

                tracker_obj = Mock()
                executor_obj = Mock()
                dedup_cache = Mock()

                with patch.dict(
                    os.environ,
                    {
                        "USE_REAL_MONEY": "false",
                        "STOP_LOSS_ENABLED": "true",
                        "STOP_LOSS_MAX_LOSS_PCT": "0.20",
                        "STOP_LOSS_MIN_HOLD": "20m",
                    },
                    clear=False,
                ):
                    main._run_stop_loss_checks(tracker_obj, executor_obj, dedup_cache)

                tracker_obj.get_market_metadata.assert_not_called()
                tracker_obj.get_orderbook_snapshot.assert_not_called()
                executor_obj.execute_exit.assert_not_called()
            finally:
                db.DB_PATH = original_db_path

    def test_stop_loss_checks_hold_when_quote_quality_is_weak(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                _insert_open_position_for_stop_loss_test()

                tracker_obj = Mock()
                tracker_obj.get_market_metadata.return_value = (
                    {
                        "question": "Will the stop loss hold?",
                        "endDate": "2030-01-01T00:00:00Z",
                    },
                    111,
                )
                tracker_obj.get_orderbook_snapshot.return_value = (
                    {
                        "best_bid": 0.36,
                        "best_ask": 0.46,
                        "mid": 0.41,
                        "bid_depth_usd": 90.0,
                        "ask_depth_usd": 90.0,
                    },
                    {
                        "bids": [{"price": "0.36", "size": "300"}],
                        "asks": [{"price": "0.46", "size": "300"}],
                    },
                    222,
                )

                entry = {
                    "remaining_entry_shares": 200.0,
                    "remaining_entry_size_usd": 100.0,
                }
                executor_obj = Mock()
                executor_obj._load_open_position_state.return_value = (
                    {"token_id": "token-stop", "side": "yes", "size_usd": 100.0},
                    [entry],
                )
                executor_obj._entry_open_shares = lambda row: float(row["remaining_entry_shares"])
                executor_obj._entry_open_size = lambda row: float(row["remaining_entry_size_usd"])
                executor_obj.estimate_exit_fill.return_value = (
                    SimulatedFill(spent_usd=72.0, shares=200.0, avg_price=0.36),
                    None,
                )
                executor_obj.estimate_exit_economics.return_value = (
                    SimpleNamespace(net_proceeds_usd=72.0, effective_exit_price=0.36),
                    None,
                )
                dedup_cache = Mock()

                with patch.dict(
                    os.environ,
                    {
                        "USE_REAL_MONEY": "false",
                        "STOP_LOSS_ENABLED": "true",
                        "STOP_LOSS_MAX_LOSS_PCT": "0.20",
                        "STOP_LOSS_MIN_HOLD": "20m",
                    },
                    clear=False,
                ), patch.object(main, "_emit_event") as emit_mock:
                    main._run_stop_loss_checks(tracker_obj, executor_obj, dedup_cache)

                executor_obj.execute_exit.assert_not_called()
                emit_mock.assert_not_called()

                conn = db.get_conn()
                audit_row = conn.execute(
                    """
                    SELECT decision, reason, metadata_json
                    FROM exit_audits
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()
                self.assertEqual(audit_row["decision"], "hold")
                self.assertIn("spread is too wide", audit_row["reason"])
                self.assertIn("spread_pct", str(audit_row["metadata_json"]))
            finally:
                db.DB_PATH = original_db_path

    def test_stop_loss_checks_hold_when_bid_depth_is_too_shallow(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                _insert_open_position_for_stop_loss_test()

                tracker_obj = Mock()
                tracker_obj.get_market_metadata.return_value = (
                    {
                        "question": "Will the stop loss hold for shallow depth?",
                        "endDate": "2030-01-01T00:00:00Z",
                    },
                    111,
                )
                tracker_obj.get_orderbook_snapshot.return_value = (
                    {
                        "best_bid": 0.38,
                        "best_ask": 0.39,
                        "mid": 0.385,
                        "bid_depth_usd": 50.0,
                        "ask_depth_usd": 50.0,
                    },
                    {
                        "bids": [{"price": "0.38", "size": "132"}],
                        "asks": [{"price": "0.39", "size": "132"}],
                    },
                    222,
                )

                entry = {
                    "remaining_entry_shares": 200.0,
                    "remaining_entry_size_usd": 100.0,
                }
                executor_obj = Mock()
                executor_obj._load_open_position_state.return_value = (
                    {"token_id": "token-stop", "side": "yes", "size_usd": 100.0},
                    [entry],
                )
                executor_obj._entry_open_shares = lambda row: float(row["remaining_entry_shares"])
                executor_obj._entry_open_size = lambda row: float(row["remaining_entry_size_usd"])
                executor_obj.estimate_exit_fill.return_value = (
                    SimulatedFill(spent_usd=76.0, shares=200.0, avg_price=0.38),
                    None,
                )
                executor_obj.estimate_exit_economics.return_value = (
                    SimpleNamespace(net_proceeds_usd=76.0, effective_exit_price=0.38),
                    None,
                )
                dedup_cache = Mock()

                with patch.dict(
                    os.environ,
                    {
                        "USE_REAL_MONEY": "false",
                        "STOP_LOSS_ENABLED": "true",
                        "STOP_LOSS_MAX_LOSS_PCT": "0.20",
                        "STOP_LOSS_MIN_HOLD": "20m",
                    },
                    clear=False,
                ), patch.object(main, "_emit_event") as emit_mock:
                    main._run_stop_loss_checks(tracker_obj, executor_obj, dedup_cache)

                executor_obj.execute_exit.assert_not_called()
                emit_mock.assert_not_called()

                conn = db.get_conn()
                audit_row = conn.execute(
                    """
                    SELECT decision, reason, metadata_json
                    FROM exit_audits
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()
                self.assertEqual(audit_row["decision"], "hold")
                self.assertIn("visible bid depth", audit_row["reason"])
                self.assertIn("min_depth_multiple", str(audit_row["metadata_json"]))
            finally:
                db.DB_PATH = original_db_path

    def test_stop_loss_checks_hard_exit_overrides_quote_quality_hold(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                _insert_open_position_for_stop_loss_test()

                tracker_obj = Mock()
                tracker_obj.get_market_metadata.return_value = (
                    {
                        "question": "Will the hard exit trigger?",
                        "endDate": "2030-01-01T00:00:00Z",
                    },
                    111,
                )
                tracker_obj.get_orderbook_snapshot.return_value = (
                    {
                        "best_bid": 0.34,
                        "best_ask": 0.46,
                        "mid": 0.40,
                        "bid_depth_usd": 90.0,
                        "ask_depth_usd": 90.0,
                    },
                    {
                        "bids": [{"price": "0.34", "size": "300"}],
                        "asks": [{"price": "0.46", "size": "300"}],
                    },
                    222,
                )

                entry = {
                    "remaining_entry_shares": 200.0,
                    "remaining_entry_size_usd": 100.0,
                }
                executor_obj = Mock()
                executor_obj._load_open_position_state.return_value = (
                    {"token_id": "token-stop", "side": "yes", "size_usd": 100.0},
                    [entry],
                )
                executor_obj._entry_open_shares = lambda row: float(row["remaining_entry_shares"])
                executor_obj._entry_open_size = lambda row: float(row["remaining_entry_size_usd"])
                executor_obj.estimate_exit_fill.return_value = (
                    SimulatedFill(spent_usd=68.0, shares=200.0, avg_price=0.34),
                    None,
                )
                executor_obj.estimate_exit_economics.return_value = (
                    SimpleNamespace(net_proceeds_usd=68.0, effective_exit_price=0.34),
                    None,
                )
                executor_obj.execute_exit.return_value = ExecutionResult(
                    True,
                    True,
                    None,
                    68.0,
                    "hard exit",
                    shares=200.0,
                    pnl_usd=-32.0,
                    action="exit",
                )
                dedup_cache = Mock()

                with patch.dict(
                    os.environ,
                    {
                        "USE_REAL_MONEY": "false",
                        "STOP_LOSS_ENABLED": "true",
                        "STOP_LOSS_MAX_LOSS_PCT": "0.20",
                        "STOP_LOSS_MIN_HOLD": "20m",
                    },
                    clear=False,
                ):
                    main._run_stop_loss_checks(tracker_obj, executor_obj, dedup_cache)

                executor_obj.execute_exit.assert_called_once()
                self.assertIn("hard exit", executor_obj.execute_exit.call_args.kwargs["reason_override"])

                conn = db.get_conn()
                audit_row = conn.execute(
                    """
                    SELECT decision, reason
                    FROM exit_audits
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()
                self.assertEqual(audit_row["decision"], "exit")
                self.assertIn("hard exit", audit_row["reason"])
            finally:
                db.DB_PATH = original_db_path

    def test_shadow_exit_uses_reason_override_when_provided(self) -> None:
        executor_obj = object.__new__(PolymarketExecutor)
        executor_obj._simulate_shadow_sell = Mock(
            return_value=(SimulatedFill(spent_usd=76.0, shares=200.0, avg_price=0.38), None)
        )
        executor_obj._exit_economics_for_fill = Mock(
            return_value=(
                SimpleNamespace(
                    effective_exit_price=0.38,
                    net_proceeds_usd=76.0,
                    gross_shares=200.0,
                    gross_notional_usd=76.0,
                    exit_fee_usd=0.0,
                    fixed_cost_usd=0.0,
                    fee_rate_bps=0,
                ),
                None,
            )
        )
        executor_obj._finalize_exit = Mock(return_value=(200.0, 76.0, -24.0))
        event = SimpleNamespace(
            question="Will the stop loss trigger?",
            side="yes",
            raw_orderbook={"bids": [{"price": "0.38", "size": "200"}]},
            raw_market_metadata={},
            trader_name="Trader",
            trader_address="0xabc",
        )
        dedup_cache = Mock()

        with patch("executor.build_trade_exit_alert", return_value="alert"), patch("executor.send_alert"):
            result = executor_obj._execute_shadow_exit(
                trade_id="stop-loss-test",
                market_id="market-stop",
                token_id="token-stop",
                event=event,
                dedup=dedup_cache,
                position={"token_id": "token-stop", "side": "yes"},
                entries=[{"id": 1}],
                exit_price=0.38,
                shares=200.0,
                exit_notional=76.0,
                pnl=-24.0,
                exit_fraction=1.0,
                reason_override="custom stop loss reason",
            )

        self.assertTrue(result.placed)
        self.assertEqual(result.reason, "custom stop loss reason")
        self.assertEqual(
            executor_obj._finalize_exit.call_args.kwargs["exit_reason"],
            "custom stop loss reason",
        )


if __name__ == "__main__":
    unittest.main()
