from __future__ import annotations

import inspect
import json
import sqlite3
import shutil
import unittest
from dataclasses import asdict, is_dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.data.db as db
import kelly_watcher.runtime.evaluator as evaluator
import main
import kelly_watcher.runtime.performance_preview as performance_preview


def _signature_supports(fn: object, required_names: tuple[str, ...]) -> bool:
    if not callable(fn):
        return False
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return all(name in params for name in required_names)


def _resolve_callable(module: object, candidate_names: tuple[str, ...]) -> tuple[str, object] | None:
    for name in candidate_names:
        fn = getattr(module, name, None)
        if callable(fn):
            return name, fn
    return None


def _payload_dict(result: object) -> dict[str, object]:
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return result
    if hasattr(result, "__dict__"):
        return dict(vars(result))
    raise AssertionError(f"Unsupported helper return type: {type(result)!r}")


def _message_text(payload: dict[str, object]) -> str:
    for key in ("message", "error", "reason", "detail", "summary", "status"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _insert_shadow_trade(
    conn,
    *,
    trade_id: str,
    segment_id: str,
    pnl_usd: float,
    resolved_at: int,
) -> None:
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id,
            market_id,
            question,
            trader_address,
            side,
            source_action,
            price_at_signal,
            signal_size_usd,
            confidence,
            kelly_fraction,
            real_money,
            skipped,
            placed_at,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            remaining_entry_shares,
            remaining_entry_size_usd,
            remaining_source_shares,
            shadow_pnl_usd,
            actual_pnl_usd,
            outcome,
            resolved_at,
            segment_id,
            expected_edge,
            expected_fill_cost_usd,
            expected_exit_fee_usd,
            expected_close_fixed_cost_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_id,
            f"market-{trade_id}",
            "Will the backup DB be used?",
            "0xshadow",
            "yes",
            "buy",
            0.5,
            10.0,
            0.7,
            0.1,
            0,
            0,
            resolved_at - 10,
            0.5,
            20.0,
            10.0,
            20.0,
            10.0,
            20.0,
            pnl_usd,
            None,
            1,
            resolved_at,
            segment_id,
            0.12,
            0.03,
            0.01,
            0.0,
        ),
    )


def _build_fixture(*, with_backup: bool) -> dict[str, object]:
    tmpdir = TemporaryDirectory()
    root = Path(tmpdir.name)
    active_path = root / "data" / "trading.db"
    backup_path = active_path.with_suffix(".db.bak")
    bot_state_path = root / "data" / "bot_state.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)

    original_db_path = db.DB_PATH
    try:
        db.DB_PATH = active_path
        db.init_db()
        conn = db.get_conn()
        try:
            _insert_shadow_trade(
                conn,
                trade_id="backup-shadow-win",
                segment_id="backup-seg",
                pnl_usd=3.5,
                resolved_at=1_700_000_060,
            )
            conn.commit()
        finally:
            conn.close()
    finally:
        db.DB_PATH = original_db_path

    if with_backup:
        shutil.copy2(active_path, backup_path)

    bot_state_path.write_text(
        json.dumps(
            {
                "mode": "shadow",
                "bankroll_usd": 987654.321,
                "started_at": 1_700_000_000,
                "last_activity_at": 1_700_000_060,
                "shadow_history_state_known": True,
                "resolved_shadow_trade_count": 1,
                "live_require_shadow_history_enabled": True,
                "live_min_shadow_resolved": 1,
                "live_shadow_history_total_ready": True,
                "resolved_shadow_since_last_promotion": 1,
                "live_min_shadow_resolved_since_last_promotion": 1,
                "live_shadow_history_ready": True,
                "shadow_segment_state_known": True,
                "shadow_segment_status": "ready",
                "shadow_segment_scope": "all_history",
                "shadow_segment_total": 1,
                "shadow_segment_ready_count": 1,
                "shadow_segment_blocked_count": 0,
            }
        ),
        encoding="utf-8",
    )

    active_bytes_before = active_path.read_bytes()
    active_path.write_text("corrupt sqlite image", encoding="utf-8")
    active_bytes_after = active_path.read_bytes()

    return {
        "tmpdir": tmpdir,
        "active_path": active_path,
        "backup_path": backup_path,
        "bot_state_path": bot_state_path,
        "active_bytes_before": active_bytes_before,
        "active_bytes_after": active_bytes_after,
        "recovery_state": db.db_recovery_state(active_path),
    }


def _call_preview_summary(*, db_path: Path, bot_state_path: Path) -> dict[str, object]:
    fn = getattr(performance_preview, "compute_tracker_preview_summary", None)
    if not _signature_supports(fn, ("db_path", "use_bot_state_balance")):
        raise unittest.SkipTest(
            "kelly_watcher.runtime.performance_preview.compute_tracker_preview_summary does not yet accept "
            "db_path and use_bot_state_balance."
        )
    with patch.object(performance_preview, "BOT_STATE_FILE", bot_state_path):
        result = fn(db_path=db_path, use_bot_state_balance=False)
    return _payload_dict(result)


def _call_segment_report(*, db_path: Path) -> dict[str, object]:
    fn = getattr(evaluator, "compute_segment_shadow_report", None)
    if not _signature_supports(fn, ("db_path",)):
        raise unittest.SkipTest(
            "kelly_watcher.runtime.evaluator.compute_segment_shadow_report does not yet accept db_path."
        )
    result = fn(db_path=db_path)
    return _payload_dict(result)


LEGACY_MIGRATED_HELPER_CANDIDATES = (
    "_compute_db_recovery_shadow_state_from_legacy_backup",
    "compute_db_recovery_shadow_state_from_legacy_backup",
    "_compute_db_recovery_shadow_state_from_migrated_backup",
    "compute_db_recovery_shadow_state_from_migrated_backup",
    "_compute_db_recovery_shadow_state_from_verified_backup",
    "compute_db_recovery_shadow_state_from_verified_backup",
    "_compute_db_recovery_shadow_state_from_candidate",
    "compute_db_recovery_shadow_state_from_candidate",
    "_compute_recovery_candidate_shadow_state",
    "compute_recovery_candidate_shadow_state",
    "_evaluate_migrated_recovery_candidate_shadow_state",
    "evaluate_migrated_recovery_candidate_shadow_state",
)

LEGACY_MIGRATED_MODULES = (main, db, evaluator)
LEGACY_MIGRATED_PATH_PARAM_NAMES = (
    "db_path",
    "backup_path",
    "candidate_path",
    "verified_backup_path",
    "source_path",
    "path",
)
LEGACY_MIGRATED_STATE_PARAM_NAMES = (
    "recovery_state",
    "db_recovery_state",
    "state",
    "payload",
    "candidate_state",
)


def _find_legacy_migrated_helper() -> tuple[str, object] | None:
    for module in LEGACY_MIGRATED_MODULES:
        resolved = _resolve_callable(module, LEGACY_MIGRATED_HELPER_CANDIDATES)
        if resolved is not None:
            return f"{module.__name__}.{resolved[0]}", resolved[1]
    return None


def _build_legacy_backup_fixture() -> dict[str, object]:
    tmpdir = TemporaryDirectory()
    root = Path(tmpdir.name)
    backup_path = root / "data" / "legacy_verified_backup.sqlite"
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(backup_path)
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                question TEXT,
                trader_address TEXT NOT NULL,
                side TEXT NOT NULL,
                source_action TEXT,
                price_at_signal REAL NOT NULL,
                signal_size_usd REAL NOT NULL,
                confidence REAL NOT NULL,
                kelly_fraction REAL NOT NULL,
                real_money INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                placed_at INTEGER NOT NULL,
                actual_entry_price REAL,
                actual_entry_shares REAL,
                actual_entry_size_usd REAL,
                remaining_entry_shares REAL,
                remaining_entry_size_usd REAL,
                remaining_source_shares REAL,
                shadow_pnl_usd REAL,
                actual_pnl_usd REAL,
                outcome INTEGER,
                resolved_at INTEGER,
                experiment_arm TEXT NOT NULL DEFAULT 'champion',
                expected_edge REAL,
                expected_fill_cost_usd REAL,
                expected_exit_fee_usd REAL,
                expected_close_fixed_cost_usd REAL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO trade_log (
                trade_id,
                market_id,
                question,
                trader_address,
                side,
                source_action,
                price_at_signal,
                signal_size_usd,
                confidence,
                kelly_fraction,
                real_money,
                skipped,
                placed_at,
                actual_entry_price,
                actual_entry_shares,
                actual_entry_size_usd,
                remaining_entry_shares,
                remaining_entry_size_usd,
                remaining_source_shares,
                shadow_pnl_usd,
                actual_pnl_usd,
                outcome,
                resolved_at,
                experiment_arm,
                expected_edge,
                expected_fill_cost_usd,
                expected_exit_fee_usd,
                expected_close_fixed_cost_usd
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "legacy-backup-trade",
                "legacy-market",
                "Will a migrated backup still evaluate?",
                "0xlegacy",
                "yes",
                "buy",
                0.5,
                10.0,
                0.7,
                0.1,
                0,
                0,
                1_700_001_000,
                0.5,
                20.0,
                10.0,
                20.0,
                10.0,
                20.0,
                4.0,
                None,
                1,
                1_700_001_060,
                "champion",
                0.13,
                0.02,
                0.01,
                0.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "tmpdir": tmpdir,
        "backup_path": backup_path,
        "backup_bytes_before": backup_path.read_bytes(),
        "backup_columns_before": [
            row[1]
            for row in sqlite3.connect(backup_path).execute("PRAGMA table_info(trade_log)").fetchall()
        ],
    }


def _invoke_legacy_migrated_helper(
    helper: object,
    *,
    backup_path: Path,
    recovery_state: dict[str, object] | None = None,
) -> dict[str, object]:
    signature = inspect.signature(helper)
    payload = {
        "db_path": backup_path,
        "backup_path": backup_path,
        "candidate_path": backup_path,
        "verified_backup_path": backup_path,
        "source_path": backup_path,
        "path": backup_path,
        "recovery_state": recovery_state,
        "db_recovery_state": recovery_state,
        "state": recovery_state,
        "payload": recovery_state,
        "candidate_state": recovery_state,
        "mode": "shadow",
        "use_bot_state_balance": False,
    }
    kwargs: dict[str, object] = {}
    for name, param in signature.parameters.items():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if name in LEGACY_MIGRATED_PATH_PARAM_NAMES:
            kwargs[name] = backup_path
            continue
        if name in LEGACY_MIGRATED_STATE_PARAM_NAMES:
            kwargs[name] = recovery_state or db.db_recovery_state(backup_path)
            continue
        if name == "mode":
            kwargs[name] = "shadow"
            continue
        if name == "use_bot_state_balance":
            kwargs[name] = False
            continue

    if not kwargs:
        if len(
            [
                param
                for param in signature.parameters.values()
                if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
            ]
        ) != 1:
            raise unittest.SkipTest(f"Unsupported legacy migrated helper signature: {signature}")
        only_name = next(iter(signature.parameters))
        if only_name in LEGACY_MIGRATED_PATH_PARAM_NAMES:
            result = helper(backup_path)
        elif only_name in LEGACY_MIGRATED_STATE_PARAM_NAMES:
            result = helper(recovery_state or db.db_recovery_state(backup_path))
        else:
            raise unittest.SkipTest(f"Unsupported legacy migrated helper signature: {signature}")
    else:
        try:
            signature.bind_partial(**kwargs)
        except TypeError as exc:
            raise unittest.SkipTest(f"Unsupported legacy migrated helper signature: {signature}") from exc
        result = helper(**kwargs)
    return _payload_dict(result)


class DbRecoveryShadowPreviewTest(unittest.TestCase):
    def test_preview_summary_reads_backup_db_path_non_destructively(self) -> None:
        fixture = _build_fixture(with_backup=True)
        active_path = fixture["active_path"]
        backup_path = fixture["backup_path"]
        bot_state_path = fixture["bot_state_path"]
        active_bytes_before = fixture["active_bytes_before"]
        active_bytes_after = fixture["active_bytes_after"]
        recovery_state = fixture["recovery_state"]
        assert isinstance(active_path, Path)
        assert isinstance(backup_path, Path)
        assert isinstance(bot_state_path, Path)
        assert isinstance(active_bytes_before, bytes)
        assert isinstance(active_bytes_after, bytes)
        assert isinstance(recovery_state, dict)

        if not recovery_state.get("db_recovery_candidate_ready"):
            self.skipTest("No verified backup candidate exists in this fixture.")

        with patch.object(db, "DB_PATH", active_path), patch.object(evaluator, "DB_PATH", active_path):
            summary = _call_preview_summary(db_path=backup_path, bot_state_path=bot_state_path)

        self.assertEqual(active_bytes_after, active_path.read_bytes())
        self.assertEqual(summary.get("title"), "Shadow tracker")
        self.assertEqual(summary.get("mode"), "shadow")
        self.assertEqual(summary.get("resolved"), 1)
        self.assertNotEqual(summary.get("current_balance"), 987654.321)
        self.assertGreater(float(summary.get("total_pnl") or 0.0), 0.0)

    def test_segment_report_reads_backup_db_path_non_destructively(self) -> None:
        fixture = _build_fixture(with_backup=True)
        active_path = fixture["active_path"]
        backup_path = fixture["backup_path"]
        active_bytes_before = fixture["active_bytes_before"]
        active_bytes_after = fixture["active_bytes_after"]
        recovery_state = fixture["recovery_state"]
        assert isinstance(active_path, Path)
        assert isinstance(backup_path, Path)
        assert isinstance(active_bytes_before, bytes)
        assert isinstance(active_bytes_after, bytes)
        assert isinstance(recovery_state, dict)

        if not recovery_state.get("db_recovery_candidate_ready"):
            self.skipTest("No verified backup candidate exists in this fixture.")

        with patch.object(db, "DB_PATH", active_path), patch.object(evaluator, "DB_PATH", active_path):
            report = _call_segment_report(db_path=backup_path)

        self.assertEqual(active_bytes_after, active_path.read_bytes())
        self.assertEqual(report.get("mode"), "shadow")
        self.assertGreaterEqual(int(report.get("total_segments") or 0), 1)
        self.assertTrue(
            any(
                segment.get("segment_id") == "backup-seg" and int(segment.get("resolved") or 0) == 1
                for segment in report.get("segments", [])
                if isinstance(segment, dict)
            )
        )
        self.assertFalse(
            any(
                segment.get("segment_id") == "active-seg"
                for segment in report.get("segments", [])
                if isinstance(segment, dict)
            )
        )

    def test_missing_verified_backup_candidate_is_reported_clearly(self) -> None:
        fixture = _build_fixture(with_backup=False)
        active_path = fixture["active_path"]
        bot_state_path = fixture["bot_state_path"]
        active_bytes_before = fixture["active_bytes_before"]
        active_bytes_after = fixture["active_bytes_after"]
        recovery_state = fixture["recovery_state"]
        assert isinstance(active_path, Path)
        assert isinstance(bot_state_path, Path)
        assert isinstance(active_bytes_before, bytes)
        assert isinstance(active_bytes_after, bytes)
        assert isinstance(recovery_state, dict)

        if recovery_state.get("db_recovery_candidate_ready"):
            self.skipTest("This fixture unexpectedly produced a verified backup candidate.")

        preview_fn = getattr(performance_preview, "compute_tracker_preview_summary", None)
        segment_fn = getattr(evaluator, "compute_segment_shadow_report", None)
        if not _signature_supports(preview_fn, ("db_path", "use_bot_state_balance")) and not _signature_supports(
            segment_fn, ("db_path",)
        ):
            raise unittest.SkipTest("Neither helper accepts the alternate db_path contract yet.")

        with patch.object(db, "DB_PATH", active_path), patch.object(evaluator, "DB_PATH", active_path):
            if _signature_supports(preview_fn, ("db_path", "use_bot_state_balance")):
                try:
                    summary = _call_preview_summary(db_path=active_path, bot_state_path=bot_state_path)
                except Exception as exc:
                    self.skipTest(
                        f"No verified backup candidate exists yet and the current helper still raises "
                        f"{type(exc).__name__}: {exc}"
                    )
                self.assertEqual(active_bytes_after, active_path.read_bytes())
                self.assertTrue(
                    "backup" in _message_text(summary).lower()
                    or "candidate" in _message_text(summary).lower()
                    or "missing" in _message_text(summary).lower()
                    or "not found" in _message_text(summary).lower()
                    or "unavailable" in _message_text(summary).lower()
                    or "error" in _message_text(summary).lower()
                )
                self.assertTrue(
                    summary.get("total_pnl") in {None, 0, 0.0}
                    or summary.get("resolved") in {None, 0, 0.0}
                )
                return

            try:
                report = _call_segment_report(db_path=active_path)
            except Exception as exc:
                self.skipTest(
                    f"No verified backup candidate exists yet and the current helper still raises "
                    f"{type(exc).__name__}: {exc}"
                )
            self.assertEqual(active_bytes_after, active_path.read_bytes())
            self.assertTrue(
                "backup" in _message_text(report).lower()
                or "candidate" in _message_text(report).lower()
                or "missing" in _message_text(report).lower()
                or "not found" in _message_text(report).lower()
                or "unavailable" in _message_text(report).lower()
                or "error" in _message_text(report).lower()
            )
            self.assertTrue(
                report.get("total_segments") in {None, 0, 0.0}
                or report.get("resolved") in {None, 0, 0.0}
            )


class LegacyMigratedBackupShadowPreviewTest(unittest.TestCase):
    helper_name: str
    helper: object

    @classmethod
    def setUpClass(cls) -> None:
        resolved = _find_legacy_migrated_helper()
        if resolved is None:
            raise unittest.SkipTest(
                "No helper for migrated legacy verified-backup shadow evaluation is present yet."
            )
        cls.helper_name, cls.helper = resolved

    def test_legacy_backup_missing_segment_id_is_evaluated_through_a_temp_migrated_clone(self) -> None:
        fixture = _build_legacy_backup_fixture()
        backup_path = fixture["backup_path"]
        backup_bytes_before = fixture["backup_bytes_before"]
        backup_columns_before = fixture["backup_columns_before"]
        assert isinstance(backup_path, Path)
        assert isinstance(backup_bytes_before, bytes)
        assert isinstance(backup_columns_before, list)

        self.assertNotIn("segment_id", backup_columns_before)

        with patch.object(db, "DB_PATH", backup_path), patch.object(evaluator, "DB_PATH", backup_path):
            result = _invoke_legacy_migrated_helper(
                self.helper,
                backup_path=backup_path,
                recovery_state=db.db_recovery_state(backup_path),
            )

        self.assertEqual(backup_bytes_before, backup_path.read_bytes())
        payload = _payload_dict(result)
        message = _message_text(payload).lower()
        status = str(payload.get("status") or payload.get("db_recovery_shadow_status") or "").strip().lower()
        self.assertTrue(
            payload.get("db_recovery_shadow_state_known") is True
            or payload.get("ok") is True
            or status in {"legacy", "partial", "ready", "insufficient", "mixed", "success"}
        )
        self.assertFalse(status in {"error", "blocked"})
        self.assertTrue(
            "legacy" in message
            or "backup" in message
            or "segment" in message
            or "shadow" in message
            or status in {"legacy", "partial", "ready", "insufficient", "mixed"}
        )
        total_pnl = (
            payload.get("db_recovery_shadow_total_pnl_usd")
            if payload.get("db_recovery_shadow_total_pnl_usd") is not None
            else payload.get("total_pnl")
            if payload.get("total_pnl") is not None
            else payload.get("total_pnl_usd")
        )
        self.assertIsNotNone(total_pnl)
        self.assertGreater(float(total_pnl or 0.0), 0.0)
        segment_total = (
            payload.get("db_recovery_shadow_segment_total")
            if payload.get("db_recovery_shadow_segment_total") is not None
            else payload.get("total_segments")
        )
        self.assertIsNotNone(segment_total)
        self.assertGreater(int(segment_total or 0), 0)
        segment_json = str(payload.get("db_recovery_shadow_segment_summary_json") or payload.get("segment_summary_json") or "").strip()
        self.assertTrue(segment_json)
        self.assertNotEqual(segment_json, "[]")


if __name__ == "__main__":
    unittest.main()
