"""Scene checks shared with the cloud publisher.

A quoted or flow-style document has to fail the same way as block YAML.
"""

from __future__ import annotations

from typing import Any

import yaml

from orng_led.scenes.capabilities import scene_capability_problems

_SECRET_KEYS = {"password", "token", "secret", "private_key", "api_key", "credential"}


def _load(text: str) -> Any:
    loaded = yaml.safe_load(text) if text.strip() else None
    return loaded


def _is_int(value: Any) -> bool:
    return type(value) is int


def _walk_secrets(node: Any, errors: list[str], path: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            name = str(key)
            if name in _SECRET_KEYS and value not in (None, "", False):
                errors.append(f"{path}: секретне поле {name}")
            if name == "output_armed" and value is True:
                errors.append(f"{path}: output_armed не належить канонічній сцені")
            if name == "hardware_verified" and value is True:
                errors.append(f"{path}: hardware_verified не належить канонічній сцені")
            _walk_secrets(value, errors, f"{path}.{name}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk_secrets(value, errors, f"{path}[{index}]")


def _text(files: dict[str, str], path: str) -> str:
    return files.get(path) or ""


def validate_scene_texts(files: dict[str, str]) -> list[str]:
    errors: list[str] = []
    parsed: dict[str, Any] = {}
    for path, text in files.items():
        if not path.endswith((".yaml", ".yml")):
            continue
        try:
            parsed[path] = _load(text)
        except yaml.YAMLError as exc:
            errors.append(f"{path}: YAML {exc}")
            continue
        _walk_secrets(parsed[path], errors, path)

    preset_ids = {
        path.split("/", 1)[1].rsplit(".", 1)[0]
        for path in files
        if path.startswith("presets/") and path.endswith((".yaml", ".yml"))
    }
    app = parsed.get("app.yaml")
    if isinstance(app, dict):
        slots = app.get("pad_presets") or []
        if isinstance(slots, list):
            for slot in slots:
                if slot and slot != "NONE" and str(slot) not in preset_ids:
                    errors.append(f"слот пульта {slot} не має пресета")

    footprints: dict[str, int] = {}
    for path, doc in parsed.items():
        if not path.startswith("profiles/") or not isinstance(doc, dict):
            continue
        profile_id = path.split("/", 1)[1].rsplit(".", 1)[0]
        declared = doc.get("footprint")
        channels = doc.get("channels") if isinstance(doc.get("channels"), list) else []
        if declared is None:
            size = len(channels) or 1
        elif not _is_int(declared) or declared < 1:
            errors.append(f"{profile_id}: footprint має бути цілим")
            continue
        else:
            size = declared
        footprints[profile_id] = size

    patch = parsed.get("patch.yaml")
    fixtures = patch.get("fixtures") if isinstance(patch, dict) else None
    seen: set[str] = set()
    ranges: list[tuple[str, int, int]] = []
    if isinstance(fixtures, list):
        for fixture in fixtures:
            if not isinstance(fixture, dict) or not fixture.get("id"):
                errors.append("прилад без id")
                continue
            fixture_id = str(fixture["id"])
            if fixture_id in seen:
                errors.append(f"дубль приладу {fixture_id}")
            seen.add(fixture_id)
            profile_id = str(fixture.get("profile_id") or "")
            footprint = footprints.get(profile_id)
            if not profile_id or footprint is None:
                errors.append(f"{fixture_id}: невідомий профіль {profile_id or '?'}")
                continue
            start = fixture.get("start_address")
            if not _is_int(start):
                errors.append(f"{fixture_id}: адреса має бути цілим числом")
                continue
            end = start + footprint - 1
            if start < 1 or end > 512:
                errors.append(f"{fixture_id}: адреса {start} поза 1..512")
                continue
            ranges.append((fixture_id, start, end))
            spatial = fixture.get("spatial")
            if isinstance(spatial, dict):
                for key in ("pan_min", "pan_max", "tilt_min", "tilt_max", "home_pan", "home_tilt"):
                    value = spatial.get(key)
                    if value is None:
                        continue
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        errors.append(f"{fixture_id}: {key} має бути числом")
                    elif not 0 <= float(value) <= 1:
                        errors.append(f"{fixture_id}: {key} поза 0..1")
                if (
                    isinstance(spatial.get("pan_min"), (int, float))
                    and isinstance(spatial.get("pan_max"), (int, float))
                    and spatial["pan_min"] > spatial["pan_max"]
                ):
                    errors.append(f"{fixture_id}: pan_min більший за pan_max")
    ranges.sort(key=lambda item: item[1])
    for index in range(1, len(ranges)):
        prev = ranges[index - 1]
        cur = ranges[index]
        if cur[1] <= prev[2]:
            errors.append(f"{prev[0]} і {cur[0]} перетинаються")

    layout = parsed.get("layout.yaml")
    layout_ids: list[str] = []
    if isinstance(layout, dict):
        raw = layout.get("fixtures")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    layout_ids.append(item)
                elif isinstance(item, dict):
                    layout_ids.append(str(item.get("fixture_id") or item.get("id") or ""))
        elif isinstance(raw, dict):
            layout_ids.extend(str(key) for key in raw)
    for fixture_id in layout_ids:
        if fixture_id and fixture_id not in seen and fixtures is not None:
            errors.append(f"схема посилається на відсутній прилад {fixture_id}")

    for path, doc in parsed.items():
        if not path.startswith("presets/") or not isinstance(doc, dict):
            continue
        preset_id = path.split("/", 1)[1].rsplit(".", 1)[0]
        if doc.get("id") not in (None, preset_id):
            errors.append(f"{path}: id не збігається з ім'ям файлу")
        episodes = doc.get("episodes")
        if episodes is None:
            continue
        if not isinstance(episodes, list) or not episodes:
            errors.append(f"{preset_id}: епізоди мають бути списком")
            continue
        for episode in episodes:
            if not isinstance(episode, dict) or not isinstance(episode.get("duration_s"), (int, float)):
                errors.append(f"{preset_id}: епізод без duration_s")
                break
            if isinstance(episode.get("duration_s"), bool):
                errors.append(f"{preset_id}: duration_s має бути числом")
                break

    controls = parsed.get("controls.yaml")
    if isinstance(controls, dict):
        keyboard = controls.get("keyboard")
        if keyboard is not None and not isinstance(keyboard, list):
            errors.append("controls.yaml: keyboard має бути списком")
    errors.extend(scene_capability_problems(parsed))
    return errors
