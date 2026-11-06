"""Update state machine persisted atomically outside release directories."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from orng_led.updater.paths import ManagedPaths, resolve_managed_paths, write_pointer_atomic


class UpdateState(StrEnum):
    IDLE = "idle"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    STAGED = "staged"
    WAITING_FOR_SAFE_STATE = "waiting_for_safe_state"
    APPLYING = "applying"
    RESTARTING = "restarting"
    HEALTH_CHECK = "health_check"
    HEALTHY = "healthy"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass
class UpdateStateRecord:
    state: str = UpdateState.IDLE.value
    progress: float = 0.0
    desired_version: str | None = None
    desired_build_id: str | None = None
    staged_version: str | None = None
    staged_build_id: str | None = None
    staged_path: str | None = None
    staged_content_sha256: str | None = None
    command_id: str | None = None
    last_error: str | None = None
    last_successful_version: str | None = None
    last_successful_build_id: str | None = None
    rollback_from: str | None = None
    pending_result: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_update_state(paths: ManagedPaths | None = None) -> UpdateStateRecord:
    paths = paths or resolve_managed_paths()
    path = paths.update_state
    if not path.is_file():
        return UpdateStateRecord()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UpdateStateRecord()
    if not isinstance(raw, dict):
        return UpdateStateRecord()
    from dataclasses import fields

    known = {f.name for f in fields(UpdateStateRecord)}
    kwargs = {k: v for k, v in raw.items() if k in known}
    return UpdateStateRecord(**kwargs)


def save_update_state(record: UpdateStateRecord, paths: ManagedPaths | None = None) -> Path:
    paths = paths or resolve_managed_paths()
    write_pointer_atomic(paths.update_state, record.to_dict())
    return paths.update_state


def transition(
    record: UpdateStateRecord,
    new_state: UpdateState | str,
    *,
    paths: ManagedPaths | None = None,
    progress: float | None = None,
    error: str | None = None,
    **fields: Any,
) -> UpdateStateRecord:
    record.state = str(new_state.value if isinstance(new_state, UpdateState) else new_state)
    if progress is not None:
        record.progress = float(progress)
    if error is not None:
        record.last_error = error
    for key, value in fields.items():
        if hasattr(record, key):
            setattr(record, key, value)
    save_update_state(record, paths)
    return record
