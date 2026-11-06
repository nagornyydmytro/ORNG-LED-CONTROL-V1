"""Deterministic scene release bundles (JSON format shared with Cloudflare Worker)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orng_led.config.io import load_show_config_from_scene_dir
from orng_led.config.models import ConfigError

ALLOWED_ROOT_FILES = frozenset(
    {
        "scene.yaml",
        "app.yaml",
        "patch.yaml",
        "layout.yaml",
        "controls.yaml",
    }
)
ALLOWED_DIRS = frozenset({"profiles", "presets"})
REQUIRED_FILES = frozenset({"scene.yaml", "app.yaml", "patch.yaml", "layout.yaml"})
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_DECODED_BYTES = 8 * 1024 * 1024
MAX_FILE_COUNT = 200
_B64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


def is_allowed_scene_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    if not normalized or ".." in normalized.split("/") or "\0" in normalized:
        return False
    if normalized in ALLOWED_ROOT_FILES:
        return True
    parts = normalized.split("/")
    if len(parts) == 2 and parts[0] in ALLOWED_DIRS:
        return parts[1].endswith((".yaml", ".yml"))
    return False


def validate_bundle_paths(paths: list[str]) -> None:
    if len(paths) > MAX_FILE_COUNT:
        raise ConfigError(f"Bundle has too many files ({len(paths)} > {MAX_FILE_COUNT})")
    seen: set[str] = set()
    for path in paths:
        if not is_allowed_scene_path(path):
            raise ConfigError(f"Path not allowlisted: {path}")
        if path in seen:
            raise ConfigError(f"Duplicate path in bundle: {path}")
        seen.add(path)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strict_b64decode(text: str) -> bytes:
    cleaned = str(text).strip()
    if not cleaned or not _B64_RE.match(cleaned) or len(cleaned) % 4 != 0:
        raise ConfigError("Invalid base64 encoding in bundle")
    try:
        return base64.b64decode(cleaned, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError("Invalid base64 encoding in bundle") from exc


def collect_scene_files(scene_dir: Path) -> dict[str, bytes]:
    root = Path(scene_dir)
    if not root.is_dir():
        raise ConfigError(f"Not a scene directory: {root}")
    files: dict[str, bytes] = {}
    for name in sorted(ALLOWED_ROOT_FILES):
        path = root / name
        if path.is_symlink():
            raise ConfigError(f"Symlinks not allowed: {name}")
        if path.is_file():
            data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                raise ConfigError(f"File too large: {name}")
            files[name] = data
    for dirname in sorted(ALLOWED_DIRS):
        folder = root / dirname
        if not folder.is_dir():
            continue
        if folder.is_symlink():
            raise ConfigError(f"Symlinks not allowed: {dirname}")
        for child in sorted(folder.iterdir()):
            if not child.is_file():
                continue
            if child.is_symlink():
                raise ConfigError(f"Symlinks not allowed: {dirname}/{child.name}")
            rel = f"{dirname}/{child.name}"
            if not is_allowed_scene_path(rel):
                continue
            data = child.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                raise ConfigError(f"File too large: {rel}")
            files[rel] = data
    for required in REQUIRED_FILES:
        if required not in files:
            raise ConfigError(f"Scene bundle requires {required}")
    validate_bundle_paths(list(files))
    return files


def pack_scene_bundle(scene_dir: Path) -> tuple[bytes, dict[str, Any]]:
    """Pack a scene directory into the immutable JSON bundle format.

    Validates the scene via ``load_show_config_from_scene_dir`` before packing.
    """
    root = Path(scene_dir)
    load_show_config_from_scene_dir(root)
    files = collect_scene_files(root)
    entries = []
    file_hashes: dict[str, str] = {}
    for path in sorted(files):
        data = files[path]
        digest = _sha256_bytes(data)
        file_hashes[path] = digest
        entries.append(
            {
                "path": path,
                "sha256": digest,
                "data_b64": base64.b64encode(data).decode("ascii"),
            }
        )
    payload = {"format": "orng-scene-bundle-v1", "files": entries}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    raw = text.encode("utf-8")
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ConfigError(f"Bundle exceeds {MAX_BUNDLE_BYTES} bytes")
    content_hash = _sha256_text(text)
    manifest = {
        "schema": 1,
        "content_sha256": content_hash,
        "files": file_hashes,
        "size_bytes": len(raw),
    }
    return raw, manifest


def unpack_scene_bundle(raw: bytes) -> tuple[dict[str, bytes], str]:
    if len(raw) > MAX_BUNDLE_BYTES + 1024:
        raise ConfigError("Bundle too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError("Bundle is corrupt (not JSON)") from exc
    if payload.get("format") != "orng-scene-bundle-v1":
        raise ConfigError("Unrecognized bundle format")
    files_meta = payload.get("files")
    if not isinstance(files_meta, list):
        raise ConfigError("Bundle missing files list")
    if len(files_meta) > MAX_FILE_COUNT:
        raise ConfigError(f"Bundle has too many files ({len(files_meta)} > {MAX_FILE_COUNT})")

    files: dict[str, bytes] = {}
    total_decoded = 0
    for entry in files_meta:
        if not isinstance(entry, dict):
            raise ConfigError("Invalid bundle entry")
        path = str(entry.get("path", ""))
        if not is_allowed_scene_path(path):
            raise ConfigError(f"Path not allowlisted: {path}")
        if path in files:
            raise ConfigError(f"Duplicate path in bundle: {path}")
        data = _strict_b64decode(str(entry.get("data_b64", "")))
        if len(data) > MAX_FILE_BYTES:
            raise ConfigError(f"Decoded file too large: {path}")
        total_decoded += len(data)
        if total_decoded > MAX_TOTAL_DECODED_BYTES:
            raise ConfigError("Total decoded size exceeds limit")
        digest = _sha256_bytes(data)
        if digest != str(entry.get("sha256", "")):
            raise ConfigError(f"Hash mismatch for {path}")
        files[path] = data

    if len(files) != len(files_meta):
        raise ConfigError("File count mismatch after unpack")
    validate_bundle_paths(list(files))
    for required in REQUIRED_FILES:
        if required not in files:
            raise ConfigError(f"Scene bundle requires {required}")

    # Content hash = sha256 of canonical packed bytes (same as pack).
    content_hash = _sha256_bytes(raw)
    return files, content_hash


def validate_unpacked_release(
    files: dict[str, bytes],
    *,
    expected_scene_id: str | None = None,
    expected_content_hash: str | None = None,
    content_hash: str | None = None,
    app_version: str | None = None,
    min_app_version: str | None = None,
) -> None:
    """Structural checks after unpack, before staging.

    ``expected_content_hash`` is compared to ``content_hash`` (computed by unpack).
    ``min_app_version`` comes from release manifest/header (not guessed from scene.yaml alone).
    """
    from orng_led.cloud.ids import assert_safe_scene_id

    validate_bundle_paths(list(files))
    for required in REQUIRED_FILES:
        if required not in files:
            raise ConfigError(f"Missing required file: {required}")

    if expected_content_hash:
        actual = content_hash
        if not actual:
            raise ConfigError("content_hash missing for release validation")
        if actual != expected_content_hash:
            raise ConfigError(
                f"Content hash mismatch: expected {expected_content_hash}, got {actual}"
            )

    if expected_scene_id:
        assert_safe_scene_id(expected_scene_id)

    scene_text = files["scene.yaml"].decode("utf-8", errors="replace")
    import yaml

    try:
        scene_doc = yaml.safe_load(scene_text) or {}
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"Invalid scene.yaml: {exc}") from exc
    if not isinstance(scene_doc, dict):
        raise ConfigError("Invalid scene.yaml: expected mapping")

    if expected_scene_id:
        sid = str(scene_doc.get("scene_id") or scene_doc.get("id") or "")
        if not sid:
            raise ConfigError("scene.yaml missing scene_id")
        assert_safe_scene_id(sid)
        if sid != expected_scene_id:
            raise ConfigError(
                f"scene.yaml scene_id {sid!r} does not match release scene {expected_scene_id!r}"
            )

    effective_min = min_app_version
    if effective_min is None:
        effective_min = scene_doc.get("min_app_version")
    if effective_min is not None and app_version:
        if _version_tuple(str(effective_min)) > _version_tuple(str(app_version)):
            raise ConfigError(
                f"min_app_version {effective_min} requires newer app (have {app_version})"
            )


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in re.split(r"[^\d]+", text.strip()):
        if chunk.isdigit():
            parts.append(int(chunk))
    return tuple(parts) or (0,)


def write_bundle_to_dir(files: dict[str, bytes], dest: Path) -> None:
    """Write unpacked files into ``dest`` (must not exist or be empty)."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ConfigError(f"Destination not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    for path, data in files.items():
        if not is_allowed_scene_path(path):
            raise ConfigError(f"Path not allowlisted: {path}")
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise ConfigError(f"Refusing to overwrite: {path}")
        target.write_bytes(data)


BACKUP_SUFFIX = ".bak-cloud"
PREVIOUS_GOOD_SUFFIX = ".previous-good"
WRECKAGE_SUFFIX = ".apply-wreckage"
JOURNAL_SUFFIX = ".apply-journal"
SCENE_AUX_SUFFIXES = (BACKUP_SUFFIX, PREVIOUS_GOOD_SUFFIX, WRECKAGE_SUFFIX)


@dataclass(frozen=True)
class ApplyReleaseOutcome:
    """What is confirmed, not what the swap hoped to do."""

    status: str
    files_in_place: bool
    scene_loaded: bool
    restart_revision: str | None
    backup_preserved: bool
    error: str | None = None


class ApplyReleaseError(ConfigError):
    """Apply stopped. ``outcome.status`` is the verified disk/memory result."""

    def __init__(self, code: str, message: str, outcome: ApplyReleaseOutcome) -> None:
        super().__init__(message)
        self.code = code
        self.outcome = outcome


def scene_backup_dir(final_dir: Path) -> Path:
    return Path(final_dir).with_name(Path(final_dir).name + BACKUP_SUFFIX)


def scene_previous_good_dir(final_dir: Path) -> Path:
    return Path(final_dir).with_name(Path(final_dir).name + PREVIOUS_GOOD_SUFFIX)


def scene_journal_path(final_dir: Path) -> Path:
    return Path(final_dir).with_name(Path(final_dir).name + JOURNAL_SUFFIX)


def is_scene_aux_name(name: str) -> bool:
    cleaned = str(name)
    if cleaned.startswith("."):
        return True
    return cleaned.endswith(SCENE_AUX_SUFFIXES)


def is_scene_tree(path: Path) -> bool:
    root = Path(path)
    return (
        root.is_dir()
        and (root / "app.yaml").is_file()
        and (root / "patch.yaml").is_file()
        and (root / "scene.yaml").is_file()
    )


def scene_revision(path: Path) -> str | None:
    """Identity of files on disk. Missing tree → None."""
    root = Path(path)
    if not is_scene_tree(root):
        return None
    digest = hashlib.sha256()
    for relative in ("scene.yaml", "app.yaml", "patch.yaml", "layout.yaml"):
        digest.update(relative.encode())
        digest.update((root / relative).read_bytes())
    return digest.hexdigest()


def recover_interrupted_swap(final_dir: Path) -> str | None:
    """Put a working scene back at ``final_dir`` after a crash or failed rename.

    Never deletes the last working copy. Returns which tree became live
    (``final``, ``backup``, ``previous-good``) or ``None``.
    """
    final_dir = Path(final_dir)
    backup = scene_backup_dir(final_dir)
    previous = scene_previous_good_dir(final_dir)
    if is_scene_tree(final_dir):
        return "final"
    source = None
    origin = None
    if is_scene_tree(backup):
        source, origin = backup, "backup"
    elif is_scene_tree(previous):
        source, origin = previous, "previous-good"
    if source is None:
        return None
    if final_dir.exists():
        wreckage = final_dir.with_name(final_dir.name + WRECKAGE_SUFFIX)
        if wreckage.exists():
            _rm_tree(wreckage)
        final_dir.rename(wreckage)
    source.rename(final_dir)
    _clear_journal(final_dir)
    return origin


def _show_already_black(runtime: Any) -> bool:
    engine = getattr(runtime, "engine", None)
    overlays = getattr(engine, "overlays", None)
    return bool(getattr(overlays, "blackout", False))


def _force_safe(runtime: Any) -> None:
    engine = getattr(runtime, "engine", None)
    if engine is not None:
        try:
            engine.set_blackout(True)
        except Exception:  # noqa: BLE001
            pass
    output = getattr(runtime, "output", None)
    if output is None:
        return
    try:
        output.disarm()
    except Exception:  # noqa: BLE001
        output.armed = False


def _write_journal(final_dir: Path, phase: str) -> None:
    try:
        scene_journal_path(final_dir).write_text(phase, encoding="utf-8")
    except OSError:
        pass


def _clear_journal(final_dir: Path) -> None:
    path = scene_journal_path(final_dir)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _runtime_revision(runtime: Any) -> str | None:
    show = getattr(runtime, "show", None)
    patch = getattr(show, "patch", None)
    fixtures = getattr(patch, "fixtures", None)
    if not fixtures:
        return None
    items = [(str(item.id), int(item.start_address)) for item in fixtures]
    return json.dumps({"scene": getattr(runtime, "scene_id", ""), "patch": items}, sort_keys=True)


def _pointer_scene_id(runtime: Any) -> str | None:
    root = getattr(runtime, "config_root", None)
    if root is None:
        return None
    try:
        from orng_led.config.io import load_runtime_state

        return load_runtime_state(Path(root)).active_scene_id
    except (ConfigError, OSError, ValueError):
        return None


def _restart_revision(runtime: Any, scene_id: str, final_dir: Path) -> str | None:
    pointer = _pointer_scene_id(runtime)
    if pointer != scene_id:
        if pointer:
            root = getattr(runtime, "config_root", None)
            if root is not None:
                other = Path(root) / "scenes" / pointer
                return scene_revision(other)
        return None
    return scene_revision(final_dir)


def _file_revision_matches(path: Path, expected: str | None) -> bool:
    if not expected:
        return False
    return scene_revision(path) == expected


def _loaded_matches(runtime: Any, expected_files: dict[str, bytes]) -> bool:
    patch_text = expected_files.get("patch.yaml")
    if patch_text is None:
        return False
    show = getattr(runtime, "show", None)
    patch = getattr(show, "patch", None)
    fixtures = getattr(patch, "fixtures", None)
    if fixtures is None:
        return False
    try:
        import yaml

        doc = yaml.safe_load(patch_text.decode("utf-8")) or {}
        wanted = {
            str(item.get("id")): int(item.get("start_address"))
            for item in doc.get("fixtures") or []
        }
    except (ValueError, TypeError, AttributeError):
        return False
    have = {str(item.id): int(item.start_address) for item in fixtures}
    return have == wanted and str(getattr(runtime, "scene_id", "")) == Path(
        getattr(runtime, "config_dir", "")
    ).name


def _outcome(
    *,
    status: str,
    final_dir: Path,
    runtime: Any,
    expected: str | None,
    loaded: bool,
    backup: Path,
    error: str | None = None,
) -> ApplyReleaseOutcome:
    files_ok = _file_revision_matches(final_dir, expected) if expected else is_scene_tree(final_dir)
    return ApplyReleaseOutcome(
        status=status,
        files_in_place=files_ok,
        scene_loaded=loaded,
        restart_revision=_restart_revision(runtime, final_dir.name, final_dir),
        backup_preserved=is_scene_tree(backup) or is_scene_tree(scene_previous_good_dir(final_dir)),
        error=error,
    )


def _raise_apply(
    code: str,
    cause: BaseException,
    outcome: ApplyReleaseOutcome,
) -> None:
    raise ApplyReleaseError(code, f"Scene apply failed; {code}: {cause}", outcome) from cause


def apply_release_safely(
    *,
    staging_dir: Path,
    final_dir: Path,
    files: dict[str, bytes],
    runtime: Any,
    release_id: str | None = None,
    keep_previous_good: bool = True,
    fault: Callable[[str], None] | None = None,
) -> ApplyReleaseOutcome:
    """Safe apply: release holds → blackout → dark → disarm → stage → validate → swap.

    ``applied`` and ``rolled_back`` are used only after files, memory, and the
    restart pointer are checked. A failed restore keeps blackout, disarmed,
    and the last backup.
    """
    staging_dir = Path(staging_dir)
    final_dir = Path(final_dir)
    _ = release_id

    recover_interrupted_swap(final_dir)
    backup = scene_backup_dir(final_dir)
    previous_good = scene_previous_good_dir(final_dir)
    if staging_dir.exists() and (is_scene_tree(final_dir) or is_scene_tree(backup)):
        _rm_tree(staging_dir)
    elif staging_dir.exists():
        raise ConfigError(f"Staging dir already exists: {staging_dir}")

    write_bundle_to_dir(files, staging_dir)
    wanted = scene_revision(staging_dir)
    previous = scene_revision(final_dir)

    try:
        load_show_config_from_scene_dir(staging_dir)
        from orng_led.scenes.semantics import validate_scene_texts

        texts = {
            path.relative_to(staging_dir).as_posix(): path.read_text(encoding="utf-8")
            for path in staging_dir.rglob("*")
            if path.is_file() and path.suffix in {".yaml", ".yml"}
        }
        problems = validate_scene_texts(texts)
        if problems:
            raise ConfigError("; ".join(problems[:8]))
    except (ConfigError, OSError, ValueError) as exc:
        _rm_tree(staging_dir)
        raise ConfigError(f"Staged scene invalid: {exc}") from exc

    # Re-check immediately before any engine or output change. A live show
    # stays as it is; clearing blackout in this window aborts the swap.
    if not _show_already_black(runtime):
        _rm_tree(staging_dir)
        raise ConfigError("show_active")

    if getattr(runtime, "input_dispatcher", None) is not None:
        runtime.input_dispatcher.force_release_all()
    _force_safe(runtime)
    try:
        runtime.output._emit_zero_frames(count=3, raise_on_error=False)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass

    # Only drop an extra backup when the live tree is already a working copy.
    if is_scene_tree(final_dir) and backup.exists():
        _rm_tree(backup)

    swapped = False
    try:
        _write_journal(final_dir, "backing_up")
        if final_dir.exists():
            final_dir.rename(backup)
        if fault is not None:
            fault("after_backup")
        _write_journal(final_dir, "swapping")
        staging_dir.rename(final_dir)
        swapped = True
        if fault is not None:
            fault("after_swap")
        _write_journal(final_dir, "activating")
        runtime.activate_scene(final_dir.name)
        if fault is not None:
            fault("after_activate")
    except Exception as exc:
        _force_safe(runtime)
        if swapped and _file_revision_matches(final_dir, wanted):
            if staging_dir.exists() and is_scene_tree(backup):
                _rm_tree(staging_dir)
            outcome = ApplyReleaseOutcome(
                status="activate_failed",
                files_in_place=True,
                scene_loaded=_loaded_matches(runtime, files),
                restart_revision=_restart_revision(runtime, final_dir.name, final_dir),
                backup_preserved=is_scene_tree(backup) or is_scene_tree(previous_good),
                error=str(exc),
            )
            _raise_apply("activate_failed", exc, outcome)
        recover_interrupted_swap(final_dir)
        files_previous = _file_revision_matches(final_dir, previous)
        loaded_previous = _runtime_matches_revision(runtime, previous)
        if staging_dir.exists() and (is_scene_tree(final_dir) or is_scene_tree(backup)):
            _rm_tree(staging_dir)
        if files_previous and loaded_previous:
            outcome = _outcome(
                status="rolled_back",
                final_dir=final_dir,
                runtime=runtime,
                expected=previous,
                loaded=True,
                backup=backup,
                error=str(exc),
            )
            _raise_apply("rolled_back", exc, outcome)
        if files_previous:
            outcome = _outcome(
                status="files_restored",
                final_dir=final_dir,
                runtime=runtime,
                expected=previous,
                loaded=False,
                backup=backup,
                error=str(exc),
            )
            _raise_apply("files_restored", exc, outcome)
        outcome = _outcome(
            status="restore_failed",
            final_dir=final_dir,
            runtime=runtime,
            expected=previous,
            loaded=False,
            backup=backup,
            error=str(exc),
        )
        _raise_apply("restore_failed", exc, outcome)

    loaded = _loaded_matches(runtime, files)
    files_ok = _file_revision_matches(final_dir, wanted)
    pointer = _pointer_scene_id(runtime)
    persist_error = getattr(runtime, "scene_error", None)
    pointer_ok = pointer == final_dir.name and not persist_error
    restart = _restart_revision(runtime, final_dir.name, final_dir)
    backup_kept = is_scene_tree(backup) or is_scene_tree(previous_good)

    if files_ok and loaded and pointer_ok and restart == wanted:
        if keep_previous_good and backup.exists():
            if previous_good.exists():
                _rm_tree(previous_good)
            try:
                backup.rename(previous_good)
            except OSError:
                pass
        _clear_journal(final_dir)
        _force_safe(runtime)
        return ApplyReleaseOutcome(
            status="applied",
            files_in_place=True,
            scene_loaded=True,
            restart_revision=wanted,
            backup_preserved=is_scene_tree(previous_good) or is_scene_tree(backup),
        )

    _force_safe(runtime)
    if files_ok and loaded and not pointer_ok:
        outcome = ApplyReleaseOutcome(
            status="pointer_unconfirmed",
            files_in_place=True,
            scene_loaded=True,
            restart_revision=restart,
            backup_preserved=backup_kept,
            error=str(persist_error or "active scene pointer not written"),
        )
        raise ApplyReleaseError(
            "pointer_unconfirmed",
            "Scene apply failed; pointer_unconfirmed: active scene pointer not written",
            outcome,
        )
    if files_ok and not loaded:
        outcome = ApplyReleaseOutcome(
            status="activate_failed",
            files_in_place=True,
            scene_loaded=False,
            restart_revision=restart,
            backup_preserved=backup_kept,
            error="scene files swapped but engine was not replaced",
        )
        raise ApplyReleaseError(
            "activate_failed",
            "Scene apply failed; activate_failed: renamed scene was not loaded",
            outcome,
        )
    outcome = ApplyReleaseOutcome(
        status="unconfirmed",
        files_in_place=files_ok,
        scene_loaded=loaded,
        restart_revision=restart,
        backup_preserved=backup_kept,
        error="apply finished without a confirmed state",
    )
    raise ApplyReleaseError("unconfirmed", "Scene apply failed; unconfirmed", outcome)


def _runtime_matches_revision(runtime: Any, revision: str | None) -> bool:
    if not revision:
        return False
    config_dir = getattr(runtime, "config_dir", None)
    if config_dir is None:
        return False
    return scene_revision(Path(config_dir)) == revision and _runtime_revision(runtime) is not None


def _rm_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
