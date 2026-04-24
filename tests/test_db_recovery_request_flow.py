from __future__ import annotations

import inspect
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import dashboard_api
import main


QUEUE_ENTRYPOINT_NAMES = (
    "_launch_db_recovery",
    "_queue_db_recovery_request",
    "_spawn_db_recovery_process",
)

CONSUME_ENTRYPOINT_NAMES = (
    "_consume_db_recovery_request",
    "_consume_shadow_db_recovery_request",
)

REQUEST_FILE_ATTR_NAMES = (
    "DB_RECOVERY_REQUEST_FILE",
    "DB_RECOVERY_REQUEST_PATH",
    "DB_RECOVERY_QUEUE_FILE",
    "DB_RECOVERY_QUEUE_PATH",
    "SHADOW_DB_RECOVERY_REQUEST_FILE",
    "SHADOW_DB_RECOVERY_REQUEST_PATH",
)


def _find_callable(module, names: tuple[str, ...]):
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return name, fn
    return "", None


def _request_file_attr_names(module) -> list[str]:
    names = []
    for attr_name in REQUEST_FILE_ATTR_NAMES:
        if hasattr(module, attr_name):
            names.append(attr_name)
    return names or list(REQUEST_FILE_ATTR_NAMES)


def _payload_value(payload: object, *names: str) -> object:
    if isinstance(payload, dict):
        for name in names:
            if name in payload:
                return payload[name]
        return None
    for name in names:
        if hasattr(payload, name):
            return getattr(payload, name)
    return None


def _require_path(payload: object, *names: str) -> Path:
    value = _payload_value(payload, *names)
    if value is None:
        raise AssertionError(f"Missing expected payload field(s): {', '.join(names)}")
    return Path(value)


def _resolved_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _invoke_helper(fn, *, payload: dict[str, object] | None = None, request_file: Path | None = None):
    signature = inspect.signature(fn)
    params = list(signature.parameters.values())

    if not params:
        return fn()

    if len(params) == 1:
        param_name = params[0].name.lower()
        if payload is not None and param_name in {"payload", "request", "body", "data"}:
            return fn(payload)
        if request_file is not None and param_name in {"path", "request_file", "request_path", "file_path"}:
            return fn(request_file)

    kwargs: dict[str, object] = {}
    if payload is not None:
        candidate_field_names = {
            "candidate_path": ("candidate_path", "backup_path", "db_recovery_candidate_path"),
            "candidate_source_path": (
                "candidate_source_path",
                "source_path",
                "db_recovery_candidate_source_path",
            ),
            "request_id": ("request_id",),
            "requested_at": ("requested_at",),
            "source": ("source",),
        }
        for param in params:
            lowered = param.name.lower()
            for value_name, aliases in candidate_field_names.items():
                if lowered == value_name or lowered in aliases:
                    value = _payload_value(payload, *aliases)
                    if value is not None:
                        kwargs[param.name] = value
                    break

    if request_file is not None:
        for param in params:
            lowered = param.name.lower()
            if lowered in {"path", "request_file", "request_path", "file_path"} and param.name not in kwargs:
                kwargs[param.name] = request_file

    if len(kwargs) == len(params):
        return fn(**kwargs)

    if payload is not None:
        return fn(payload)

    return fn()


class DbRecoveryRequestFlowTest(unittest.TestCase):
    def test_queue_writes_request_json_with_paths_and_request_id(self) -> None:
        entrypoint_name, entrypoint = _find_callable(dashboard_api, QUEUE_ENTRYPOINT_NAMES)
        if entrypoint is None:
            self.skipTest(
                "DB recovery queue helper is not present in this checkout yet; "
                "the test activates once the new dashboard helper exists."
            )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            data_dir = tmpdir_path / "data"
            request_file = data_dir / "db_recovery_request.json"
            candidate_path = tmpdir_path / "candidate.sqlite"
            source_path = tmpdir_path / "source.sqlite"
            candidate_path.touch()
            source_path.touch()

            payload = {
                "candidate_path": candidate_path,
                "candidate_source_path": source_path,
                "request_id": "db-recovery-request-123",
                "requested_at": 1_700_000_000,
                "source": "dashboard_api",
            }

            with ExitStack() as stack:
                stack.enter_context(patch.object(dashboard_api, "DATA_DIR", data_dir, create=True))
                stack.enter_context(patch.object(dashboard_api, "SHADOW_RESET_REQUEST_FILE", request_file, create=True))
                for attr_name in _request_file_attr_names(dashboard_api):
                    stack.enter_context(patch.object(dashboard_api, attr_name, request_file, create=True))
                stack.enter_context(
                    patch.object(
                        dashboard_api,
                        "_bot_state_snapshot",
                        return_value={
                            "mode": "shadow",
                            "db_recovery_state_known": True,
                            "db_recovery_candidate_ready": True,
                            "db_recovery_candidate_path": str(candidate_path),
                            "db_recovery_candidate_source_path": str(source_path),
                        },
                    )
                )
                stack.enter_context(
                    patch.object(dashboard_api, "_live_trading_enabled_in_config", return_value=False)
                )
                stack.enter_context(patch.object(dashboard_api, "use_real_money", return_value=False))
                stack.enter_context(patch.object(dashboard_api.time, "time", return_value=1_700_000_000))
                stack.enter_context(patch.object(dashboard_api.os, "getpid", return_value=4321))

                result = _invoke_helper(
                    entrypoint,
                    payload=payload,
                    request_file=request_file,
                )

            self.assertTrue(request_file.exists(), f"{entrypoint_name} did not write a request file.")
            written = json.loads(request_file.read_text(encoding="utf-8"))
            self.assertIsInstance(written, dict)

        self.assertEqual(
            _require_path(written, "candidate_path", "backup_path", "db_recovery_candidate_path"),
            candidate_path,
        )
        self.assertEqual(
            _require_path(
                written,
                "candidate_source_path",
                "source_path",
                "db_recovery_candidate_source_path",
            ),
            source_path,
        )
        self.assertEqual(_payload_value(written, "request_id"), "db-recovery-request-123")
        self.assertEqual(_payload_value(written, "source"), "dashboard_api")
        self.assertEqual(_payload_value(written, "requested_at"), 1_700_000_000)
        self.assertTrue(bool(result) or result is None)

    def test_consume_returns_parsed_request_and_deletes_file(self) -> None:
        entrypoint_name, entrypoint = _find_callable(main, CONSUME_ENTRYPOINT_NAMES)
        if entrypoint is None:
            self.skipTest(
                "DB recovery consume helper is not present in this checkout yet; "
                "the test activates once the new runtime helper exists."
            )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            data_dir = tmpdir_path / "data"
            request_file = data_dir / "db_recovery_request.json"
            candidate_path = tmpdir_path / "candidate.sqlite"
            source_path = tmpdir_path / "source.sqlite"
            candidate_path.touch()
            source_path.touch()

            payload = {
                "candidate_path": str(candidate_path),
                "candidate_source_path": str(source_path),
                "request_id": "db-recovery-request-456",
                "requested_at": 1_700_000_000,
                "source": "dashboard_api",
            }
            request_file.parent.mkdir(parents=True, exist_ok=True)
            request_file.write_text(json.dumps(payload), encoding="utf-8")

            with ExitStack() as stack:
                stack.enter_context(patch.object(main, "DATA_DIR", data_dir, create=True))
                stack.enter_context(patch.object(main, "SHADOW_RESET_REQUEST_FILE", request_file, create=True))
                for attr_name in _request_file_attr_names(main):
                    stack.enter_context(patch.object(main, attr_name, request_file, create=True))
                stack.enter_context(
                    patch.object(
                        main,
                        "db_recovery_state",
                        return_value={
                            "db_recovery_state_known": True,
                            "db_recovery_candidate_ready": True,
                            "db_recovery_candidate_path": str(candidate_path),
                            "db_recovery_candidate_source_path": str(source_path),
                        },
                    )
                )
                stack.enter_context(patch.object(main.time, "time", return_value=1_700_000_010))

                request = _invoke_helper(entrypoint, request_file=request_file)

            self.assertFalse(request_file.exists(), f"{entrypoint_name} should delete the request file after pickup.")
            self.assertIsNotNone(request)
            self.assertEqual(
                _resolved_path(_require_path(request, "candidate_path", "backup_path", "db_recovery_candidate_path")),
                _resolved_path(candidate_path),
            )
            self.assertEqual(
                _resolved_path(
                    _require_path(
                        request,
                        "candidate_source_path",
                        "source_path",
                        "db_recovery_candidate_source_path",
                    )
                ),
                _resolved_path(source_path),
            )
            self.assertEqual(_payload_value(request, "request_id"), "db-recovery-request-456")
            self.assertEqual(_payload_value(request, "source"), "dashboard_api")

    def test_stale_or_invalid_requests_are_ignored(self) -> None:
        entrypoint_name, entrypoint = _find_callable(main, CONSUME_ENTRYPOINT_NAMES)
        if entrypoint is None:
            self.skipTest(
                "DB recovery consume helper is not present in this checkout yet; "
                "the test activates once the new runtime helper exists."
            )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            data_dir = tmpdir_path / "data"
            request_file = data_dir / "db_recovery_request.json"
            request_file.parent.mkdir(parents=True, exist_ok=True)

            stale_payload = {
                "candidate_path": str(tmpdir_path / "candidate.sqlite"),
                "candidate_source_path": str(tmpdir_path / "source.sqlite"),
                "request_id": "db-recovery-stale",
                "requested_at": 1_700_000_000,
                "source": "dashboard_api",
            }
            request_file.write_text(json.dumps(stale_payload), encoding="utf-8")

            with ExitStack() as stack:
                stack.enter_context(patch.object(main, "DATA_DIR", data_dir, create=True))
                stack.enter_context(patch.object(main, "SHADOW_RESET_REQUEST_FILE", request_file, create=True))
                for attr_name in _request_file_attr_names(main):
                    stack.enter_context(patch.object(main, attr_name, request_file, create=True))
                stack.enter_context(
                    patch.object(
                        main,
                        "db_recovery_state",
                        return_value={
                            "db_recovery_state_known": True,
                            "db_recovery_candidate_ready": True,
                            "db_recovery_candidate_path": str(tmpdir_path / "candidate.sqlite"),
                            "db_recovery_candidate_source_path": str(tmpdir_path / "source.sqlite"),
                        },
                    )
                )
                stack.enter_context(patch.object(main.time, "time", return_value=1_700_001_001))

                stale_result = _invoke_helper(entrypoint, request_file=request_file)

            self.assertIsNone(stale_result, f"{entrypoint_name} should ignore stale requests.")
            self.assertFalse(request_file.exists(), "Stale requests should be removed after pickup.")

            request_file.write_text("{not-json", encoding="utf-8")
            with ExitStack() as stack:
                stack.enter_context(patch.object(main, "DATA_DIR", data_dir, create=True))
                stack.enter_context(patch.object(main, "SHADOW_RESET_REQUEST_FILE", request_file, create=True))
                for attr_name in _request_file_attr_names(main):
                    stack.enter_context(patch.object(main, attr_name, request_file, create=True))

                invalid_result = _invoke_helper(entrypoint, request_file=request_file)

            self.assertIsNone(invalid_result, f"{entrypoint_name} should ignore invalid requests.")
            self.assertFalse(request_file.exists(), "Invalid requests should be removed after pickup.")

    def test_launch_blocks_direct_candidate_override_when_payload_does_not_match_verified_candidate(self) -> None:
        entrypoint = getattr(dashboard_api, "_launch_db_recovery", None)
        if not callable(entrypoint):
            self.skipTest("_launch_db_recovery is not present in this checkout yet.")

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            verified_candidate = tmpdir_path / "verified.sqlite"
            verified_source = tmpdir_path / "source.sqlite"
            wrong_candidate = tmpdir_path / "wrong.sqlite"
            verified_candidate.touch()
            verified_source.touch()
            wrong_candidate.touch()

            with patch.object(
                dashboard_api,
                "_bot_state_snapshot",
                return_value={
                    "mode": "shadow",
                    "db_recovery_state_known": True,
                    "db_recovery_candidate_ready": True,
                    "db_recovery_candidate_path": str(verified_candidate),
                    "db_recovery_candidate_source_path": str(verified_source),
                },
            ), patch.object(
                dashboard_api, "_live_trading_enabled_in_config", return_value=False
            ), patch.object(
                dashboard_api, "use_real_money", return_value=False
            ), patch.object(
                dashboard_api, "_spawn_db_recovery_process"
            ) as spawn_mock:
                result = entrypoint(
                    {
                        "candidate_path": str(wrong_candidate),
                        "candidate_source_path": str(verified_source),
                    }
                )

        self.assertIsInstance(result, dict)
        self.assertFalse(bool(result.get("ok")))
        self.assertIn("candidate_path overrides", str(result.get("message", "")))
        spawn_mock.assert_not_called()

    def test_launch_accepts_direct_candidate_override_when_path_is_symlink_to_verified_candidate(self) -> None:
        entrypoint = getattr(dashboard_api, "_launch_db_recovery", None)
        if not callable(entrypoint):
            self.skipTest("_launch_db_recovery is not present in this checkout yet.")

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            verified_candidate = tmpdir_path / "verified.sqlite"
            verified_source = tmpdir_path / "source.sqlite"
            candidate_alias = tmpdir_path / "verified-alias.sqlite"
            source_alias = tmpdir_path / "source-alias.sqlite"
            verified_candidate.touch()
            verified_source.touch()
            candidate_alias.symlink_to(verified_candidate)
            source_alias.symlink_to(verified_source)

            with patch.object(
                dashboard_api,
                "_bot_state_snapshot",
                return_value={
                    "mode": "shadow",
                    "db_recovery_state_known": True,
                    "db_recovery_candidate_ready": True,
                    "db_recovery_candidate_path": str(verified_candidate),
                    "db_recovery_candidate_source_path": str(verified_source),
                },
            ), patch.object(
                dashboard_api, "_live_trading_enabled_in_config", return_value=False
            ), patch.object(
                dashboard_api, "use_real_money", return_value=False
            ), patch.object(
                dashboard_api, "_spawn_db_recovery_process", return_value={"ok": True, "message": "DB recovery queued."}
            ) as spawn_mock:
                result = entrypoint(
                    {
                        "candidate_path": str(candidate_alias),
                        "candidate_source_path": str(source_alias),
                    }
                )

        self.assertTrue(bool(result.get("ok")))
        spawn_mock.assert_called_once()

    def test_consume_ignores_request_when_candidate_does_not_match_verified_backup(self) -> None:
        entrypoint_name, entrypoint = _find_callable(main, CONSUME_ENTRYPOINT_NAMES)
        if entrypoint is None:
            self.skipTest(
                "DB recovery consume helper is not present in this checkout yet; "
                "the test activates once the new runtime helper exists."
            )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            data_dir = tmpdir_path / "data"
            request_file = data_dir / "db_recovery_request.json"
            request_file.parent.mkdir(parents=True, exist_ok=True)
            request_file.write_text(
                json.dumps(
                    {
                        "candidate_path": str(tmpdir_path / "wrong.sqlite"),
                        "candidate_source_path": str(tmpdir_path / "source.sqlite"),
                        "request_id": "db-recovery-mismatch",
                        "requested_at": 1_700_000_000,
                        "source": "dashboard_api",
                    }
                ),
                encoding="utf-8",
            )

            with ExitStack() as stack:
                stack.enter_context(patch.object(main, "DATA_DIR", data_dir, create=True))
                stack.enter_context(patch.object(main, "SHADOW_RESET_REQUEST_FILE", request_file, create=True))
                for attr_name in _request_file_attr_names(main):
                    stack.enter_context(patch.object(main, attr_name, request_file, create=True))
                stack.enter_context(
                    patch.object(
                        main,
                        "db_recovery_state",
                        return_value={
                            "db_recovery_state_known": True,
                            "db_recovery_candidate_ready": True,
                            "db_recovery_candidate_path": str(tmpdir_path / "verified.sqlite"),
                            "db_recovery_candidate_source_path": str(tmpdir_path / "source.sqlite"),
                        },
                    )
                )
                stack.enter_context(patch.object(main.time, "time", return_value=1_700_000_010))

                result = _invoke_helper(entrypoint, request_file=request_file)

            self.assertIsNone(result, f"{entrypoint_name} should ignore mismatched recovery requests.")
            self.assertFalse(request_file.exists(), "Mismatched requests should be removed after pickup.")

    def test_consume_accepts_request_when_symlinked_paths_resolve_to_verified_backup(self) -> None:
        entrypoint_name, entrypoint = _find_callable(main, CONSUME_ENTRYPOINT_NAMES)
        if entrypoint is None:
            self.skipTest(
                "DB recovery consume helper is not present in this checkout yet; "
                "the test activates once the new runtime helper exists."
            )

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            data_dir = tmpdir_path / "data"
            request_file = data_dir / "db_recovery_request.json"
            request_file.parent.mkdir(parents=True, exist_ok=True)
            verified_candidate = tmpdir_path / "verified.sqlite"
            verified_source = tmpdir_path / "source.sqlite"
            candidate_alias = tmpdir_path / "verified-alias.sqlite"
            source_alias = tmpdir_path / "source-alias.sqlite"
            verified_candidate.touch()
            verified_source.touch()
            candidate_alias.symlink_to(verified_candidate)
            source_alias.symlink_to(verified_source)
            request_file.write_text(
                json.dumps(
                    {
                        "candidate_path": str(candidate_alias),
                        "candidate_source_path": str(source_alias),
                        "request_id": "db-recovery-symlink",
                        "requested_at": 1_700_000_000,
                        "source": "dashboard_api",
                    }
                ),
                encoding="utf-8",
            )

            with ExitStack() as stack:
                stack.enter_context(patch.object(main, "DATA_DIR", data_dir, create=True))
                stack.enter_context(patch.object(main, "SHADOW_RESET_REQUEST_FILE", request_file, create=True))
                for attr_name in _request_file_attr_names(main):
                    stack.enter_context(patch.object(main, attr_name, request_file, create=True))
                stack.enter_context(
                    patch.object(
                        main,
                        "db_recovery_state",
                        return_value={
                            "db_recovery_state_known": True,
                            "db_recovery_candidate_ready": True,
                            "db_recovery_candidate_path": str(verified_candidate),
                            "db_recovery_candidate_source_path": str(verified_source),
                        },
                    )
                )
                stack.enter_context(patch.object(main.time, "time", return_value=1_700_000_010))

                result = _invoke_helper(entrypoint, request_file=request_file)

            self.assertIsNotNone(result, f"{entrypoint_name} should accept symlink-equivalent recovery requests.")
            self.assertFalse(request_file.exists(), "Accepted requests should be removed after pickup.")


if __name__ == "__main__":
    unittest.main()
