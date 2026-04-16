from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from runtime_paths import SHADOW_EVIDENCE_EPOCH_FILE


def _default_epoch_state() -> dict[str, object]:
    return {
        "shadow_evidence_epoch_known": False,
        "shadow_evidence_epoch_started_at": 0,
        "shadow_evidence_epoch_source": "",
        "shadow_evidence_epoch_request_id": "",
        "shadow_evidence_epoch_message": "",
    }


def read_shadow_evidence_epoch(path: Path | None = None) -> dict[str, object]:
    target = Path(path) if path is not None else SHADOW_EVIDENCE_EPOCH_FILE
    if not target.exists():
        return _default_epoch_state()

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return _default_epoch_state()
    if not isinstance(payload, dict):
        return _default_epoch_state()

    started_at = max(int(payload.get("started_at") or 0), 0)
    source = str(payload.get("source") or "").strip().lower()
    request_id = str(payload.get("request_id") or "").strip()
    message = str(payload.get("message") or "").strip()
    known = started_at > 0 or bool(source or request_id or message)
    return {
        "shadow_evidence_epoch_known": known,
        "shadow_evidence_epoch_started_at": started_at,
        "shadow_evidence_epoch_source": source,
        "shadow_evidence_epoch_request_id": request_id,
        "shadow_evidence_epoch_message": message,
    }


def write_shadow_evidence_epoch(
    *,
    started_at: int | None = None,
    source: str = "shadow_reset",
    request_id: str = "",
    message: str = "",
    path: Path | None = None,
) -> dict[str, object]:
    target = Path(path) if path is not None else SHADOW_EVIDENCE_EPOCH_FILE
    payload: dict[str, Any] = {
        "started_at": max(int(started_at or time.time()), 0),
        "source": str(source or "").strip().lower(),
        "request_id": str(request_id or "").strip(),
        "message": str(message or "").strip(),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.name}.{int(time.time() * 1000)}.tmp")
    temp_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
    temp_path.replace(target)
    return read_shadow_evidence_epoch(target)
