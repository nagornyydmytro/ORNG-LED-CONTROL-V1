"""What the current patch and profiles can actually show.

Kind is not a channel. A bar without segment channels cannot run a segment
effect, and an empty group never stands in for another group.
"""

from __future__ import annotations

from typing import Any

from orng_led.presets.models import GROUP_CHOICES

TYPE_TAGS = frozenset({"par", "bar", "beam", "face"})
SIDE_TAGS = frozenset({"left", "right"})
RING_TAGS = frozenset({"inner", "outer"})
PATTERN_EFFECTS = frozenset(
    {
        "chase",
        "wave",
        "mirror_sweep",
        "glow_wave",
        "glow_rise",
        "glow_line",
        "glow_pinch",
        "glow_eq",
    }
)
_COLOR_ROLES = frozenset(
    {"red", "green", "blue", "white", "amber", "color", "whole_color", "segment_color"}
)
_SEGMENT_ROLES = frozenset({"segment", "segment_color"})


def matches_groups(fixture_groups: list[str], episode_groups: list[str]) -> bool:
    """Categories intersect. Tags inside one category are alternatives.

    ``outer+beam`` is an outer beam. ``left+right`` is either side.
    ``par+bar`` is a PAR or a bar. ``all_rear`` is any non-face rear fixture.
    """
    if not episode_groups:
        return False
    fx = set(fixture_groups)
    if "all_rear" in episode_groups and len(episode_groups) == 1:
        return bool(fx & {"all_rear", "rear", "par", "bar", "beam"}) and "face" not in fx
    wanted_types = set(episode_groups) & TYPE_TAGS
    wanted_sides = set(episode_groups) & SIDE_TAGS
    wanted_rings = set(episode_groups) & RING_TAGS
    if not wanted_types and not wanted_sides and not wanted_rings:
        if "all_rear" in episode_groups:
            return bool(fx & {"all_rear", "rear", "par", "bar", "beam"}) and "face" not in fx
        return False
    if "face" in fx and "face" not in wanted_types:
        return False
    if wanted_types and not (fx & wanted_types):
        return False
    if wanted_sides and not (fx & wanted_sides):
        return False
    if wanted_rings and not (fx & wanted_rings):
        return False
    return True


def _role(channel: Any) -> str:
    role = channel.get("role") if isinstance(channel, dict) else getattr(channel, "role", "")
    return str(getattr(role, "value", role) or "")


def _index(channel: Any) -> int | None:
    raw = channel.get("segment_index") if isinstance(channel, dict) else getattr(channel, "segment_index", None)
    if type(raw) is int and raw >= 1:
        return raw
    return None


def _channels(profile: Any) -> list[Any]:
    if profile is None:
        return []
    raw = profile.get("channels") if isinstance(profile, dict) else getattr(profile, "channels", None)
    return list(raw) if isinstance(raw, list) else []


def _segment_count(profile: Any) -> int:
    raw = profile.get("segment_count") if isinstance(profile, dict) else getattr(profile, "segment_count", 0)
    if type(raw) is int and raw > 0:
        return raw
    return 0


def segments_described(profile: Any) -> bool:
    count = _segment_count(profile)
    if count < 1:
        return False
    found = {_index(channel) for channel in _channels(profile)}
    found.discard(None)
    return all(index in found for index in range(1, count + 1))


def profile_problems(profile_id: str, profile: Any) -> list[str]:
    count = _segment_count(profile)
    if count < 1:
        return []
    holes = not segments_described(profile)
    missing_index = any(
        _role(channel) in _SEGMENT_ROLES and _index(channel) is None for channel in _channels(profile)
    )
    if holes or missing_index:
        return [f"{profile_id}: сегменти описані неповністю"]
    return []


def _roles(profile: Any) -> set[str]:
    return {_role(channel) for channel in _channels(profile)}


def look_supported(profile: Any, kind: str, effect: str) -> bool:
    """A patterned bar needs its segment map. Anything else needs a channel the effect can use."""
    roles = _roles(profile)
    if kind == "bar" and effect in PATTERN_EFFECTS:
        return segments_described(profile)
    has_color = bool(roles & _COLOR_ROLES)
    has_dimmer = "dimmer" in roles
    has_axes = "pan_coarse" in roles and "tilt_coarse" in roles
    if kind == "beam":
        return has_color or has_dimmer or has_axes
    if kind == "bar":
        return has_color or has_dimmer or segments_described(profile)
    if kind == "par" or kind == "face_par":
        return has_color or has_dimmer
    return has_color or has_dimmer


def _fixture_groups(fixture: Any) -> list[str]:
    raw = fixture.get("groups") if isinstance(fixture, dict) else getattr(fixture, "groups", None)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _fixture_kind(fixture: Any) -> str:
    raw = fixture.get("kind") if isinstance(fixture, dict) else getattr(fixture, "kind", "")
    return str(getattr(raw, "value", raw) or "")


def _fixture_id(fixture: Any) -> str:
    raw = fixture.get("id") if isinstance(fixture, dict) else getattr(fixture, "id", "")
    return str(raw or "")


def _profile_id(fixture: Any) -> str:
    raw = fixture.get("profile_id") if isinstance(fixture, dict) else getattr(fixture, "profile_id", "")
    return str(raw or "")


def group_problem(label: str, groups: list[str], fixtures: list[Any]) -> str | None:
    if not groups:
        return f"{label}: дія не називає групу"
    unknown = [item for item in groups if item not in GROUP_CHOICES]
    if unknown:
        return f"{label}: невідома група {unknown[0]}"
    matched = [
        fixture
        for fixture in fixtures
        if _fixture_kind(fixture) != "face_par" and matches_groups(_fixture_groups(fixture), groups)
    ]
    if matched:
        return None
    return f"{label}: група {'+'.join(groups)} не має приладів"


def look_problem(label: str, fixture: Any, profile: Any, effect: str) -> str | None:
    kind = _fixture_kind(fixture)
    if kind == "bar" and effect in PATTERN_EFFECTS and not segments_described(profile):
        return f"{label}: {_fixture_id(fixture)}: ефект {effect} потребує сегменти"
    if not look_supported(profile, kind, effect):
        return f"{label}: {_fixture_id(fixture)}: ефект {effect} не має каналу"
    return None


def _looks(episode: Any) -> list[tuple[list[str], str, str]]:
    if not isinstance(episode, dict):
        return []
    actions = episode.get("actions")
    if isinstance(actions, list) and actions:
        found: list[tuple[list[str], str, str]] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            groups = action.get("groups") if isinstance(action.get("groups"), list) else episode.get("groups")
            effect = action.get("effect") or episode.get("effect") or "pulse"
            ident = str(action.get("id") or episode.get("id") or "дія")
            found.append(([str(item) for item in groups] if isinstance(groups, list) else [], str(effect), ident))
        return found
    groups = episode.get("groups")
    return [(
        [str(item) for item in groups] if isinstance(groups, list) else [],
        str(episode.get("effect") or "pulse"),
        str(episode.get("id") or "епізод"),
    )]


def preset_problems(preset_id: str, document: Any, fixtures: list[Any], profiles: dict[str, Any]) -> list[str]:
    if not isinstance(document, dict):
        return []
    episodes = document.get("episodes")
    if not isinstance(episodes, list):
        return []
    errors: list[str] = []
    for episode in episodes:
        for groups, effect, ident in _looks(episode):
            label = f"{preset_id}/{ident}"
            problem = group_problem(label, groups, fixtures)
            if problem:
                errors.append(problem)
                continue
            for fixture in fixtures:
                if _fixture_kind(fixture) == "face_par":
                    continue
                if not matches_groups(_fixture_groups(fixture), groups):
                    continue
                profile = profiles.get(_profile_id(fixture))
                issue = look_problem(label, fixture, profile, effect)
                if issue:
                    errors.append(issue)
    return errors


def scene_capability_problems(parsed: dict[str, Any]) -> list[str]:
    """Problems that survive a successful YAML load."""
    errors: list[str] = []
    profiles: dict[str, Any] = {}
    for path, doc in parsed.items():
        if not str(path).startswith("profiles/") or not isinstance(doc, dict):
            continue
        profile_id = str(path).split("/", 1)[1].rsplit(".", 1)[0]
        profiles[profile_id] = doc
        errors.extend(profile_problems(profile_id, doc))
    patch = parsed.get("patch.yaml")
    fixtures = patch.get("fixtures") if isinstance(patch, dict) else None
    if not isinstance(fixtures, list):
        fixtures = []
    for path, doc in parsed.items():
        if not str(path).startswith("presets/") or not isinstance(doc, dict):
            continue
        preset_id = str(path).split("/", 1)[1].rsplit(".", 1)[0]
        errors.extend(preset_problems(preset_id, doc, fixtures, profiles))
    return errors
