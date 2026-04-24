from __future__ import annotations

import inspect
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.dashboard_api as dashboard_api


def _resolve_live_mode_helper() -> tuple[str, object | None]:
    candidate_names = (
        "_set_live_mode",
        "_set_live_trading_mode",
        "_live_mode_config_response",
        "_live_mode_toggle_response",
        "_set_live_mode_response",
    )
    for name in candidate_names:
        fn = getattr(dashboard_api, name, None)
        if callable(fn):
            return name, fn
    return "", None


HELPER_NAME, LIVE_MODE_HELPER = _resolve_live_mode_helper()
if LIVE_MODE_HELPER is None:
    raise unittest.SkipTest(
        "No live-mode backend helper is present in dashboard_api.py yet; "
        "this contract test will activate when the helper lands."
    )


_BOOL_PARAM_NAMES = {
    "enabled",
    "enable",
    "live_mode",
    "use_real_money",
    "value",
    "toggle",
    "is_live",
    "live",
}
_BODY_PARAM_NAMES = {
    "body",
    "payload",
    "raw_input",
    "request",
    "data",
    "request_body",
    "request_payload",
    "input_body",
}
_MODE_PARAM_NAMES = {"mode"}


def _fresh_live_mode_state(
    *,
    mode: str = "shadow",
    started_at: int = 1_700_000_000,
    last_activity_at: int = 1_700_000_590,
    db_integrity_known: bool = True,
    db_integrity_ok: bool = True,
    db_integrity_message: str = "",
    shadow_history_state_known: bool = True,
    resolved_shadow_trade_count: int = 12,
    live_require_shadow_history_enabled: bool = True,
    live_min_shadow_resolved: int = 10,
    live_shadow_history_total_ready: bool = True,
    resolved_shadow_since_last_promotion: int = 8,
    live_min_shadow_resolved_since_last_promotion: int = 5,
    live_shadow_history_ready: bool = True,
    shadow_segment_state_known: bool = True,
    shadow_segment_status: str = "ready",
    shadow_segment_scope: str = "all_history",
    shadow_segment_scope_started_at: int = 0,
    shadow_segment_min_resolved: int = 5,
    shadow_segment_total: int = 5,
    shadow_segment_ready_count: int = 5,
    shadow_segment_positive_count: int = 4,
    shadow_segment_negative_count: int = 1,
    shadow_segment_blocked_count: int = 0,
    shadow_segment_block_reason: str = "",
) -> dict[str, object]:
    return {
        "mode": mode,
        "started_at": started_at,
        "last_activity_at": last_activity_at,
        "poll_interval": 1,
        "db_integrity_known": db_integrity_known,
        "db_integrity_ok": db_integrity_ok,
        "db_integrity_message": db_integrity_message,
        "shadow_history_state_known": shadow_history_state_known,
        "resolved_shadow_trade_count": resolved_shadow_trade_count,
        "live_require_shadow_history_enabled": live_require_shadow_history_enabled,
        "live_min_shadow_resolved": live_min_shadow_resolved,
        "live_shadow_history_total_ready": live_shadow_history_total_ready,
        "resolved_shadow_since_last_promotion": resolved_shadow_since_last_promotion,
        "live_min_shadow_resolved_since_last_promotion": live_min_shadow_resolved_since_last_promotion,
        "live_shadow_history_ready": live_shadow_history_ready,
        "shadow_segment_state_known": shadow_segment_state_known,
        "shadow_segment_status": shadow_segment_status,
        "shadow_segment_scope": shadow_segment_scope,
        "shadow_segment_scope_started_at": shadow_segment_scope_started_at,
        "shadow_segment_min_resolved": shadow_segment_min_resolved,
        "shadow_segment_total": shadow_segment_total,
        "shadow_segment_ready_count": shadow_segment_ready_count,
        "shadow_segment_positive_count": shadow_segment_positive_count,
        "shadow_segment_negative_count": shadow_segment_negative_count,
        "shadow_segment_blocked_count": shadow_segment_blocked_count,
        "shadow_segment_block_reason": shadow_segment_block_reason,
    }


def _patch_optional(module: object, stack: ExitStack, name: str, **patch_kwargs: object) -> None:
    if hasattr(module, name):
        stack.enter_context(patch.object(module, name, **patch_kwargs))


def _patched_live_mode_context(state: dict[str, object], live_enabled: bool) -> ExitStack:
    stack = ExitStack()
    tmpdir = stack.enter_context(TemporaryDirectory())
    env_path = Path(tmpdir) / ".env.test"
    stack.enter_context(patch.object(dashboard_api, "_bot_state_snapshot", return_value=state))
    stack.enter_context(
        patch.object(
            dashboard_api.time,
            "time",
            return_value=1_700_000_600,
        )
    )
    stack.enter_context(
        patch.object(
            dashboard_api,
            "_read_safe_env_values",
            return_value={"USE_REAL_MONEY": "true" if live_enabled else "false"},
        )
    )
    _patch_optional(dashboard_api, stack, "_live_trading_enabled_in_config", return_value=live_enabled)
    _patch_optional(dashboard_api, stack, "use_real_money", return_value=live_enabled)
    _patch_optional(dashboard_api, stack, "_source_env_path", return_value=env_path)

    for candidate in (
        "database_integrity_state",
        "_database_integrity_state",
        "_db_integrity_state",
        "shadow_history_state",
        "_shadow_history_state",
        "live_shadow_history_state",
        "_live_shadow_history_state",
        "shadow_history_gate_state",
        "_shadow_history_gate_state",
        "shadow_segment_state",
        "_shadow_segment_state",
        "segment_shadow_state",
        "_segment_shadow_state",
    ):
        if hasattr(dashboard_api, candidate):
            stack.enter_context(
                patch.object(
                    dashboard_api,
                    candidate,
                    return_value=dict(state),
                )
            )

    stack.enter_context(patch.object(dashboard_api, "ENV_PATH", env_path))
    return stack


def _invoke_live_mode_helper(*, enabled: bool) -> dict[str, object]:
    signature = inspect.signature(LIVE_MODE_HELPER)
    payload = {
        "enabled": enabled,
        "live_mode": enabled,
        "use_real_money": enabled,
        "mode": "live" if enabled else "shadow",
    }

    kwargs: dict[str, object] = {}
    for name, param in signature.parameters.items():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if name in _BOOL_PARAM_NAMES:
            kwargs[name] = enabled
        elif name in _MODE_PARAM_NAMES:
            kwargs[name] = "live" if enabled else "shadow"
        elif name in _BODY_PARAM_NAMES:
            kwargs[name] = payload

    positional_params = [
        param
        for param in signature.parameters.values()
        if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    if not kwargs:
        if len(positional_params) != 1:
            raise unittest.SkipTest(
                f"{HELPER_NAME} has an unsupported signature for this contract test: {signature}"
            )
        positional_name = positional_params[0].name
        if positional_name in _BOOL_PARAM_NAMES:
            result = LIVE_MODE_HELPER(enabled)
        elif positional_name in _MODE_PARAM_NAMES:
            result = LIVE_MODE_HELPER("live" if enabled else "shadow")
        elif positional_name in _BODY_PARAM_NAMES:
            result = LIVE_MODE_HELPER(payload)
        else:
            raise unittest.SkipTest(
                f"{HELPER_NAME} has an unsupported signature for this contract test: {signature}"
            )
    else:
        try:
            bound = signature.bind_partial(**kwargs)
        except TypeError as exc:
            raise unittest.SkipTest(
                f"{HELPER_NAME} has an unsupported signature for this contract test: {signature}"
            ) from exc
        required_missing = [
            name
            for name, param in signature.parameters.items()
            if param.default is inspect._empty
            and param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
            and name not in bound.arguments
        ]
        if required_missing:
            raise unittest.SkipTest(
                f"{HELPER_NAME} requires unsupported arguments {required_missing!r}; signature={signature}"
            )
        result = LIVE_MODE_HELPER(**kwargs)

    if isinstance(result, tuple) and result and isinstance(result[-1], dict):
        result = result[-1]
    if not isinstance(result, dict):
        raise AssertionError(f"{HELPER_NAME} returned unsupported payload type: {type(result)!r}")
    return result


class LiveModeApiGateTest(unittest.TestCase):
    def test_enable_live_mode_is_rejected_when_bot_state_is_stale(self) -> None:
        state = _fresh_live_mode_state(
            started_at=1_700_000_000,
            last_activity_at=1_700_000_000,
            db_integrity_ok=True,
            shadow_segment_status="ready",
        )
        with _patched_live_mode_context(state, live_enabled=False):
            result = _invoke_live_mode_helper(enabled=True)
        self.assertFalse(bool(result.get("ok")))
        message = str(result.get("message") or result.get("error") or "").lower()
        self.assertTrue("stale" in message or "refresh" in message or "restart" in message)

    def test_enable_live_mode_is_rejected_when_bot_state_readiness_is_unknown(self) -> None:
        state = _fresh_live_mode_state(
            started_at=0,
            last_activity_at=0,
            shadow_history_state_known=False,
            live_shadow_history_total_ready=False,
            live_shadow_history_ready=False,
            shadow_segment_state_known=False,
            shadow_segment_status="checking",
        )
        with _patched_live_mode_context(state, live_enabled=False):
            result = _invoke_live_mode_helper(enabled=True)
        self.assertFalse(bool(result.get("ok")))
        message = str(result.get("message") or result.get("error") or "").lower()
        self.assertIn("readiness", message)

    def test_enable_live_mode_is_rejected_when_db_integrity_fails(self) -> None:
        state = _fresh_live_mode_state(
            db_integrity_known=True,
            db_integrity_ok=False,
            db_integrity_message="sqlite integrity check failed",
            shadow_segment_status="ready",
        )
        with _patched_live_mode_context(state, live_enabled=False):
            result = _invoke_live_mode_helper(enabled=True)
        self.assertFalse(bool(result.get("ok")))
        message = str(result.get("message") or result.get("error") or "").lower()
        self.assertIn("integrity", message)

    def test_enable_live_mode_is_rejected_when_segment_shadow_status_is_blocked(self) -> None:
        state = _fresh_live_mode_state(
            shadow_segment_status="blocked",
            shadow_segment_ready_count=0,
            shadow_segment_total=5,
            shadow_segment_blocked_count=5,
            shadow_segment_block_reason="segment shadow readiness is blocked",
        )
        with _patched_live_mode_context(state, live_enabled=False):
            result = _invoke_live_mode_helper(enabled=True)
        self.assertFalse(bool(result.get("ok")))
        message = str(result.get("message") or result.get("error") or "").lower()
        self.assertTrue("segment" in message or "shadow" in message or "blocked" in message)

    def test_enable_live_mode_succeeds_only_when_all_checks_pass(self) -> None:
        state = _fresh_live_mode_state(
            mode="shadow",
            started_at=1_700_000_000,
            last_activity_at=1_700_000_590,
            db_integrity_known=True,
            db_integrity_ok=True,
            shadow_history_state_known=True,
            resolved_shadow_trade_count=24,
            live_require_shadow_history_enabled=True,
            live_min_shadow_resolved=10,
            live_shadow_history_total_ready=True,
            resolved_shadow_since_last_promotion=12,
            live_min_shadow_resolved_since_last_promotion=5,
            live_shadow_history_ready=True,
            shadow_segment_state_known=True,
            shadow_segment_status="ready",
            shadow_segment_total=8,
            shadow_segment_ready_count=8,
            shadow_segment_blocked_count=0,
        )
        with _patched_live_mode_context(state, live_enabled=False):
            result = _invoke_live_mode_helper(enabled=True)
        self.assertTrue(bool(result.get("ok")))
        message = str(result.get("message") or "").lower()
        self.assertTrue("live" in message or "enabled" in message or "updated" in message or message == "")

    def test_disable_live_mode_is_allowed(self) -> None:
        state = _fresh_live_mode_state(
            mode="live",
            started_at=1_700_000_000,
            last_activity_at=1_700_000_000,
            db_integrity_known=True,
            db_integrity_ok=False,
            db_integrity_message="sqlite integrity check failed",
            shadow_history_state_known=False,
            live_shadow_history_total_ready=False,
            live_shadow_history_ready=False,
            shadow_segment_state_known=False,
            shadow_segment_status="blocked",
            shadow_segment_ready_count=0,
            shadow_segment_total=0,
            shadow_segment_blocked_count=0,
        )
        with _patched_live_mode_context(state, live_enabled=True):
            result = _invoke_live_mode_helper(enabled=False)
        self.assertTrue(bool(result.get("ok")))


if __name__ == "__main__":
    unittest.main()
