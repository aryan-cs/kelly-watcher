from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import replay_search


class ReplaySearchTrustGateTest(unittest.TestCase):
    @staticmethod
    def _fake_run_replay(
        *,
        policy,
        db_path=None,
        label="",
        notes="",
        start_ts=None,
        end_ts=None,
        initial_state=None,
    ) -> dict[str, object]:
        min_confidence = float(policy.as_dict()["min_confidence"])
        return {
            "run_id": 1,
            "total_pnl_usd": round(25.0 + min_confidence, 6),
            "max_drawdown_pct": 0.05,
            "accepted_count": 6,
            "resolved_count": 6,
            "rejected_count": 0,
            "unresolved_count": 0,
            "trade_count": 6,
            "win_rate": 2.0 / 3.0,
        }

    def test_main_blocks_runtime_db_without_active_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_db = Path(tmpdir) / "runtime.db"
            argv = [
                "replay_search.py",
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
            ]
            with (
                patch.object(replay_search, "TRADING_DB_PATH", runtime_db),
                patch.object(
                    replay_search,
                    "database_integrity_state",
                    return_value={
                        "db_integrity_known": True,
                        "db_integrity_ok": True,
                        "db_integrity_message": "",
                    },
                ),
                patch.object(
                    replay_search,
                    "read_shadow_evidence_epoch",
                    return_value={
                        "shadow_evidence_epoch_known": False,
                        "shadow_evidence_epoch_started_at": 0,
                        "shadow_evidence_epoch_source": "",
                        "shadow_evidence_epoch_request_id": "",
                        "shadow_evidence_epoch_message": "",
                    },
                ),
                patch.object(replay_search, "run_replay") as run_replay,
                patch("sys.argv", argv),
            ):
                with self.assertRaises(SystemExit) as exc:
                    replay_search.main()

        self.assertIn("current evidence window is not active yet", str(exc.exception))
        run_replay.assert_not_called()

    def test_runtime_trust_helper_blocks_insufficient_routed_history(self) -> None:
        preview = SimpleNamespace(
            data_warning="",
            resolved=5,
            routed_resolved=5,
            routed_legacy_resolved=0,
        )
        runtime_db = Path("/tmp/runtime-trading.db")
        with (
            patch.object(replay_search, "TRADING_DB_PATH", runtime_db),
            patch.object(
                replay_search,
                "database_integrity_state",
                return_value={
                    "db_integrity_known": True,
                    "db_integrity_ok": True,
                    "db_integrity_message": "",
                },
            ),
            patch.object(
                replay_search,
                "read_shadow_evidence_epoch",
                return_value={
                    "shadow_evidence_epoch_known": True,
                    "shadow_evidence_epoch_started_at": 1_700_000_000,
                    "shadow_evidence_epoch_source": "shadow_reset",
                    "shadow_evidence_epoch_request_id": "",
                    "shadow_evidence_epoch_message": "",
                },
            ),
            patch.object(replay_search, "compute_tracker_preview_summary", return_value=preview),
            patch.object(
                replay_search,
                "compute_segment_shadow_report",
                return_value={
                    "routed_resolved": 5,
                    "legacy_unassigned_resolved": 0,
                },
            ),
        ):
            reason = replay_search._runtime_replay_search_trust_block_reason(
                db_path=runtime_db,
                mode="shadow",
            )

        self.assertIn("need 5/20 routed resolved shadow trades", reason)

    def test_runtime_trust_helper_uses_latest_promotion_within_epoch(self) -> None:
        preview = SimpleNamespace(
            data_warning="",
            resolved=25,
            routed_resolved=25,
            routed_legacy_resolved=0,
        )
        runtime_db = Path("/tmp/runtime-trading.db")
        with (
            patch.object(replay_search, "TRADING_DB_PATH", runtime_db),
            patch.object(
                replay_search,
                "database_integrity_state",
                return_value={
                    "db_integrity_known": True,
                    "db_integrity_ok": True,
                    "db_integrity_message": "",
                },
            ),
            patch.object(
                replay_search,
                "read_shadow_evidence_epoch",
                return_value={
                    "shadow_evidence_epoch_known": True,
                    "shadow_evidence_epoch_started_at": 1_700_000_000,
                    "shadow_evidence_epoch_source": "shadow_reset",
                    "shadow_evidence_epoch_request_id": "",
                    "shadow_evidence_epoch_message": "",
                },
            ),
            patch.object(
                replay_search,
                "_latest_applied_replay_promotion_at",
                return_value=1_700_000_500,
            ),
            patch.object(replay_search, "compute_tracker_preview_summary", return_value=preview) as preview_mock,
            patch.object(
                replay_search,
                "compute_segment_shadow_report",
                return_value={
                    "routed_resolved": 25,
                    "legacy_unassigned_resolved": 0,
                    "total_segments": 10,
                    "ready_count": 10,
                    "blocked_count": 0,
                    "summary": "",
                },
            ) as report_mock,
        ):
            reason = replay_search._runtime_replay_search_trust_block_reason(
                db_path=runtime_db,
                mode="shadow",
            )

        self.assertEqual(reason, "")
        preview_mock.assert_called_once_with(
            mode="shadow",
            db_path=runtime_db,
            use_bot_state_balance=False,
            since_ts=1_700_000_500,
            apply_shadow_evidence_epoch=False,
        )
        report_mock.assert_called_once_with(
            mode="shadow",
            since_ts=1_700_000_500,
            db_path=runtime_db,
        )

    def test_main_allows_explicit_snapshot_db_without_runtime_shadow_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_db = Path(tmpdir) / "runtime.db"
            snapshot_db = Path(tmpdir) / "snapshot.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(snapshot_db),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
            ]
            with (
                patch.object(replay_search, "TRADING_DB_PATH", runtime_db),
                patch.object(replay_search, "database_integrity_state") as integrity_state,
                patch.object(replay_search, "read_shadow_evidence_epoch") as read_epoch,
                patch.object(replay_search, "run_replay", side_effect=self._fake_run_replay) as run_replay,
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertGreater(int(payload["search_run_id"]), 0)
        integrity_state.assert_not_called()
        read_epoch.assert_not_called()
        self.assertGreaterEqual(run_replay.call_count, 1)


if __name__ == "__main__":
    unittest.main()
