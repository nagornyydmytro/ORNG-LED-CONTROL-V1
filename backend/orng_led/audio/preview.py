"""Isolated Auto library preview. Canonical renderer, no live executor."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import OrderedDict
from pathlib import Path

from orng_led.audio.auto_show import AUTO_CYCLE_BEATS, build_auto_stage
from orng_led.audio.gallery import FEATURED_LOOKS, catalog as library_catalog
from orng_led.audio.library import BY_ID, COMPOSITIONS, LOOKS
from orng_led.audio.palettes import COLOR_IDS, known_palette
from orng_led.audio.settings import INTENSITY_IDS, intensity_of
from orng_led.config.io import default_scene_dir
from orng_led.config.models import ChannelRole, FixtureKind, ShowConfig
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

_GROUP_KIND = {
    "par": FixtureKind.PAR,
    "bar": FixtureKind.BAR,
    "beam": FixtureKind.BEAM,
}
_GROUP_TITLE = {
    "par": "PAR",
    "bar": "бари",
    "beam": "голови",
}
PREVIEW_NOTE = "Перегляд. Зал з цієї сторінки не керується."
DEFAULT_LOOK = "vertical_wave"
PREVIEW_SAMPLES_PER_BEAT = 12.0
PREVIEW_MIN_FRAMES = 16
PREVIEW_MAX_FRAMES = 72
PREVIEW_CLIP_CACHE_LIMIT = 4
_TRAVEL_CACHE: dict[str, object] = {}
MISMATCH_REASON = "Ця картина не збігається з поточною сценою."
EMPTY_FRAMES_REASON = "Кадрів немає."
CLIP_MISSING_REASON = "Цього варіанту немає серед зібраних кадрів."
_CLIP_CACHE: OrderedDict[str, list[dict[str, object]]] = OrderedDict()


def catalog_payload() -> dict[str, object]:
    payload = library_catalog()
    rows = list(payload["compositions"])
    payload["count"] = len(rows)
    payload["default_look"] = DEFAULT_LOOK if DEFAULT_LOOK in BY_ID else (rows[0]["id"] if rows else None)
    payload["intensities"] = list(INTENSITY_IDS)
    payload["note"] = PREVIEW_NOTE
    return payload


def _follow_preview_axes(
    show: ShowConfig,
    stage: object,
    motion: dict[str, BeamMotionState],
    dt_s: float,
) -> None:
    """Same speed limit the engine uses. Preview does not emit DMX."""
    from orng_led.engine.beam import BeamMotionLimits, step_beam
    from orng_led.engine.intents import BeamIntent

    fixtures = getattr(stage, "fixtures", {})
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        state = motion.get(fixture.id)
        if state is None:
            state = BeamMotionState.from_home(
                float(fixture.spatial.home_pan),
                float(fixture.spatial.home_tilt),
            )
            motion[fixture.id] = state
        intent = fixtures.get(fixture.id)
        if not isinstance(intent, BeamIntent) or intent.pan is None or intent.tilt is None:
            continue
        if dt_s <= 0.0:
            continue
        step_beam(
            state,
            float(intent.pan),
            float(intent.tilt),
            float(dt_s),
            BeamMotionLimits(
                max_pan_speed=float(fixture.spatial.max_pan_speed),
                max_tilt_speed=float(fixture.spatial.max_tilt_speed),
            ),
        )


def _pose(show: ShowConfig) -> dict[str, BeamMotionState]:
    pose: dict[str, BeamMotionState] = {}
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        pose[fixture.id] = BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
    return pose


def _present_kinds(show: ShowConfig) -> set[FixtureKind]:
    return {fixture.kind for fixture in show.patch.fixtures}


def _has_bar_segments(show: ShowConfig) -> bool:
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BAR:
            continue
        profile = show.profile_for(fixture)
        if any(channel.segment_index is not None for channel in profile.channels):
            return True
        if any(
            channel.role in {ChannelRole.SEGMENT, ChannelRole.SEGMENT_COLOR}
            for channel in profile.channels
        ):
            return True
    return False


def compatibility(show: ShowConfig, look: str) -> dict[str, object]:
    composition = BY_ID.get(look)
    if composition is None:
        return {
            "ok": False,
            "look": look,
            "reason": f"Невідома композиція {look!r}.",
        }
    present = _present_kinds(show)
    missing = [
        group
        for group in composition.groups
        if _GROUP_KIND.get(group) is not None and _GROUP_KIND[group] not in present
    ]
    if missing:
        titles = ", ".join(_GROUP_TITLE.get(group, group) for group in missing)
        needed = ", ".join(_GROUP_TITLE.get(group, group) for group in composition.groups)
        return {
            "ok": False,
            "look": look,
            "reason": (
                f"На цій сцені немає приладів: {titles}. "
                f"«{composition.title}» потребує {needed}. "
                "Композицію не замінено на wash."
            ),
        }
    if composition.needs_segments and not _has_bar_segments(show):
        return {
            "ok": False,
            "look": look,
            "reason": (
                f"«{composition.title}» малює сегменти барів, а на сцені немає бар із сегментами. "
                "Композицію не замінено на wash."
            ),
        }
    return {"ok": True, "look": look, "reason": None}


def library_version() -> str:
    return hashlib.sha256("\n".join(LOOKS).encode("utf-8")).hexdigest()


def pack_render_identity(clips: dict[str, dict[str, object]]) -> str:
    """Identity of dumped frames. A name list alone does not change when render changes."""
    hasher = hashlib.sha256()
    for key in sorted(clips):
        hasher.update(key.encode("utf-8"))
        hasher.update(b"\n")
        hasher.update(
            json.dumps(clips[key].get("frames"), ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        hasher.update(b"\n")
    return hasher.hexdigest()


def preview_source_text(scene_dir: Path) -> str:
    """Patch, layout and profiles. Presets do not change Auto preview frames."""
    root = Path(scene_dir)
    chunks: list[str] = []
    for name in ("patch.yaml", "layout.yaml"):
        path = root / name
        if not path.is_file():
            continue
        chunks.append(f"{name}\n{path.read_text(encoding='utf-8').replace('\r\n', '\n')}")
    profiles = root / "profiles"
    if profiles.is_dir():
        for path in sorted(profiles.glob("*.yaml")):
            rel = f"profiles/{path.name}"
            chunks.append(f"{rel}\n{path.read_text(encoding='utf-8').replace('\r\n', '\n')}")
    return "\n".join(chunks)


def preview_scene_hash(scene_dir: Path | None = None) -> str:
    return hashlib.sha256(preview_source_text(scene_dir or default_scene_dir()).encode("utf-8")).hexdigest()


def cycle_key(cycle: float) -> str:
    number = float(cycle)
    if number.is_integer():
        return str(int(number))
    return str(number)


def clip_key(look: str, cycle: float, palette: str, intensity: str) -> str:
    return f"{look}|{cycle_key(cycle)}|{palette}|{intensity}"


def _beam_pose(view: dict[str, object]) -> tuple[object, ...]:
    rows = []
    for item in view.get("beams") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            (
                str(item.get("id")),
                round(float(item.get("pan") or 0.0) * 255.0),
                round(float(item.get("tilt") or 0.0) * 255.0),
            )
        )
    return tuple(rows)


def phase_frame_count(cycle_beats: float) -> int:
    """Phase samples of one cycle. Tempo only changes how long that cycle lasts."""
    return max(
        PREVIEW_MIN_FRAMES,
        min(PREVIEW_MAX_FRAMES, int(round(float(cycle_beats) * PREVIEW_SAMPLES_PER_BEAT))),
    )


def _cycle(value: float) -> float:
    number = float(value)
    for choice in AUTO_CYCLE_BEATS:
        if abs(number - choice) <= 1e-9:
            return choice
    raise ValueError("Допустимий період: ½ / 1 / 2 / 4 / 8 часток")


def _palette_of(look: str, palette: str) -> str:
    composition = BY_ID[look]
    name = str(palette or "").strip() or "orange"
    if name in composition.palettes or known_palette(name) or name in COLOR_IDS:
        return name
    if "orange" in composition.palettes:
        return "orange"
    return composition.palettes[0]


def _intensity_of(name: str) -> tuple[str, float]:
    key = str(name or "full").strip() or "full"
    if key not in INTENSITY_IDS:
        key = "full"
    return key, intensity_of(key)


def render_preview_view(
    show: ShowConfig,
    look: str,
    *,
    beats: float,
    cycle_beats: float,
    palette: str,
    intensity: str = "full",
    rig: object | None = None,
    motion: dict[str, BeamMotionState] | None = None,
    dt_s: float = 0.0,
    beat_period_s: float | None = None,
    travel: object | None = None,
) -> dict[str, object]:
    """One SimulatorView. Axes advance through the engine integrator, then decode."""
    composition = BY_ID[look]
    _level_name, level = _intensity_of(intensity)
    colour = _palette_of(look, palette)
    stage = build_auto_stage(
        list(show.patch.fixtures),
        colour,
        "groove",
        0.0,
        level,
        look=look,
        beats=float(beats),
        cycle_beats=float(cycle_beats),
        placements=show.layout.placements,
        groups=composition.groups,
    )
    from orng_led.audio.head_motion import HeadRig, preview_heads

    stage = preview_heads(
        show,
        stage,
        beats=float(beats),
        section="groove",
        look=look,
        beat_period_s=60.0 / 96.0 if beat_period_s is None else float(beat_period_s),
        rig=rig if rig is not None else HeadRig(),
        travel=travel,  # type: ignore[arg-type]
        cycle_beats=float(cycle_beats),
    )
    pose = motion if motion is not None else {}
    _follow_preview_axes(show, stage, pose, dt_s)
    frame = render_stage(show, stage, pose)
    view = decode_simulator_view(show, frame)
    dumped = view.model_dump(mode="json")
    dumped["blackout_visual"] = False
    return dumped


def preview_clip(
    show: ShowConfig,
    look: str,
    *,
    cycle_beats: float = 4.0,
    palette: str = "orange",
    intensity: str = "full",
    bpm: float = 120.0,
    frames: int = 0,
    scene_id: str | None = None,
    scene_name: str | None = None,
    scene_dir: Path | None = None,
) -> dict[str, object]:
    if look not in BY_ID:
        raise KeyError(look)
    cycle = _cycle(cycle_beats)
    tempo = max(40.0, min(220.0, float(bpm)))
    auto = int(frames) <= 0
    count = phase_frame_count(cycle) if auto else max(8, min(PREVIEW_MAX_FRAMES, int(frames)))
    check = compatibility(show, look)
    from orng_led.audio.head_motion import HeadRig, motion_loop_beats, prepare_head_travel

    loop = motion_loop_beats(look, cycle)
    duration_s = loop * 60.0 / tempo
    payload = {
        "look": look,
        "title": BY_ID[look].title,
        "cycle_beats": cycle,
        "palette": _palette_of(look, palette),
        "intensity": _intensity_of(intensity)[0],
        "bpm": tempo,
        "frames_requested": count,
        "seconds": duration_s,
        "loop_beats": loop,
        "transport": "none",
        "playback": "renderer",
        "safe_mock_preview": True,
        "isolated": True,
        "source": "script",
        "note": PREVIEW_NOTE,
        "compatible": bool(check["ok"]),
        "reason": check["reason"],
        "scene_id": scene_id,
        "scene_name": scene_name,
        "scene_hash": preview_scene_hash(scene_dir),
        "library_version": library_version(),
        "library_count": len(COMPOSITIONS),
        "canonical": False,
        "frames": [],
    }
    if not check["ok"]:
        return payload
    from orng_led.audio.head_motion import travel_fingerprint
    from orng_led.presets.io import default_presets_dir, load_presets_dir

    directory = Path(scene_dir) / "presets" if scene_dir is not None else default_presets_dir()
    documents = load_presets_dir(directory) if directory.is_dir() else {}
    fingerprint = travel_fingerprint(show, documents, scene_id=scene_id or "preview")
    cache_key = "|".join(
        [
            str(payload["scene_hash"]),
            fingerprint,
            look,
            cycle_key(cycle),
            str(payload["palette"]),
            str(payload["intensity"]),
            str(count),
            f"{tempo:.3f}",
            f"{loop:.3f}",
        ]
    )
    cached = _CLIP_CACHE.get(cache_key)
    if cached is not None:
        _CLIP_CACHE.move_to_end(cache_key)
        payload["frames"] = cached
        payload["canonical"] = True
        return payload
    travel_key = f"{scene_id or 'preview'}|{payload['scene_hash']}|{fingerprint}"
    travel = _TRAVEL_CACHE.get(travel_key)
    if travel is None:
        travel = prepare_head_travel(
            show,
            documents,
            scene_id=scene_id or "preview",
            revision=str(payload["scene_hash"]),
        )
        _TRAVEL_CACHE[travel_key] = travel
    steps: list[dict[str, object]] = []
    clip_rig = HeadRig()
    clip_motion: dict[str, BeamMotionState] = {}
    period_s = 60.0 / tempo
    if "beam" in BY_ID[look].groups:
        step = loop / count
        beats = 0.0
        recorded: list[dict[str, object]] = []
        previous_pose: tuple[object, ...] | None = None
        for _lap in range(8):
            lap: list[dict[str, object]] = []
            start_pose: tuple[object, ...] | None = None
            for index in range(count):
                beats += step
                view = render_preview_view(
                    show,
                    look,
                    beats=beats,
                    cycle_beats=cycle,
                    palette=str(payload["palette"]),
                    intensity=str(payload["intensity"]),
                    rig=clip_rig,
                    motion=clip_motion,
                    dt_s=step * period_s,
                    beat_period_s=period_s,
                    travel=travel,
                )
                if index == 0:
                    start_pose = _beam_pose(view)
                lap.append(view)
            recorded = lap
            if previous_pose is not None and start_pose == previous_pose:
                break
            previous_pose = start_pose
        steps.extend(recorded)
    else:
        previous_beat: float | None = None
        for index in range(count):
            beats = (index / count) * cycle
            dt_s = 0.0 if previous_beat is None else max(0.0, beats - previous_beat) * period_s
            previous_beat = beats
            steps.append(
                render_preview_view(
                    show,
                    look,
                    beats=beats,
                    cycle_beats=cycle,
                    palette=str(payload["palette"]),
                    intensity=str(payload["intensity"]),
                    rig=clip_rig,
                    motion=clip_motion,
                    dt_s=dt_s,
                    beat_period_s=period_s,
                    travel=travel,
                )
            )
    if not steps:
        payload["reason"] = payload["reason"] or EMPTY_FRAMES_REASON
        return payload
    _CLIP_CACHE[cache_key] = steps
    _CLIP_CACHE.move_to_end(cache_key)
    while len(_CLIP_CACHE) > PREVIEW_CLIP_CACHE_LIMIT:
        _CLIP_CACHE.popitem(last=False)
    payload["frames"] = steps
    payload["canonical"] = True
    return payload


def _compact_frame(view: dict[str, object]) -> dict[str, object]:
    def _round(value: object) -> float:
        return round(float(value), 4)

    pars = []
    for item in view.get("pars") or []:
        row = item if isinstance(item, dict) else {}
        pars.append(
            {
                "id": row.get("id"),
                "i": _round(row.get("intensity", 0.0)),
                "r": _round(row.get("r", 0.0)),
                "g": _round(row.get("g", 0.0)),
                "b": _round(row.get("b", 0.0)),
            }
        )
    bars = []
    for item in view.get("bars") or []:
        row = item if isinstance(item, dict) else {}
        bars.append(
            {
                "id": row.get("id"),
                "d": _round(row.get("dimmer", 0.0)),
                "r": _round(row.get("r", 0.0)),
                "g": _round(row.get("g", 0.0)),
                "b": _round(row.get("b", 0.0)),
                "s": [_round(value) for value in (row.get("segments") or [0.0] * 8)],
                "sr": [_round(value) for value in (row.get("segment_r") or [0.0] * 8)],
                "sg": [_round(value) for value in (row.get("segment_g") or [0.0] * 8)],
                "sb": [_round(value) for value in (row.get("segment_b") or [0.0] * 8)],
            }
        )
    beams = []
    for item in view.get("beams") or []:
        row = item if isinstance(item, dict) else {}
        beams.append(
            {
                "id": row.get("id"),
                "d": _round(row.get("dimmer", 0.0)),
                "o": bool(row.get("shutter_open")),
                "r": _round(row.get("r", 0.0)),
                "g": _round(row.get("g", 0.0)),
                "b": _round(row.get("b", 0.0)),
                "t": _round(row.get("throw", 0.0)),
                "p": _round(row.get("pan", 0.5)),
                "ti": _round(row.get("tilt", 0.5)),
                "pp": _round(row.get("physical_pan", row.get("pan", 0.5))),
                "pt": _round(row.get("physical_tilt", row.get("tilt", 0.5))),
            }
        )
    return {"pars": pars, "bars": bars, "beams": beams, "n": int(view.get("nonzero_channels") or 0)}


def _wanted_preview_combos() -> list[tuple[str, float, str, str]]:
    wanted: list[tuple[str, float, str, str]] = []
    seen: set[tuple[str, float, str, str]] = set()

    def add(look: str, cycle: float, palette: str, intensity: str) -> None:
        item = (look, float(cycle), palette, intensity)
        if item in seen:
            return
        seen.add(item)
        wanted.append(item)

    featured = set(FEATURED_LOOKS)
    for look in LOOKS:
        composition = BY_ID[look]
        default_palette = "orange" if "orange" in composition.palettes else composition.palettes[0]
        for cycle in AUTO_CYCLE_BEATS:
            add(look, cycle, default_palette, "full")
        if look not in featured:
            continue
        for cycle in AUTO_CYCLE_BEATS:
            for palette in composition.palettes:
                for intensity in INTENSITY_IDS:
                    add(look, cycle, palette, intensity)
    return wanted


def dump_preview_pack(dest: Path | None = None) -> Path:
    """Canonical clips for the default scene. Cloud serves these; it does not re-render."""
    scene_dir = default_scene_dir()
    from orng_led.config.io import load_show_config

    show = load_show_config(scene_dir)
    clips: dict[str, dict[str, object]] = {}
    by_look: dict[str, dict[str, dict[str, object]]] = {}
    for look, cycle, palette, intensity in _wanted_preview_combos():
        clip = preview_clip(
            show,
            look,
            cycle_beats=cycle,
            palette=palette,
            intensity=intensity,
            bpm=120.0,
            frames=phase_frame_count(cycle),
            scene_dir=scene_dir,
        )
        if not clip.get("canonical"):
            continue
        key = clip_key(look, cycle, str(clip["palette"]), str(clip["intensity"]))
        row = {
            "look": look,
            "cycle_beats": cycle,
            "loop_beats": clip.get("loop_beats", cycle),
            "palette": clip["palette"],
            "intensity": clip["intensity"],
            "frames": [_compact_frame(frame) for frame in clip["frames"]],  # type: ignore[union-attr]
        }
        clips[key] = row
        by_look.setdefault(look, {})[key] = row
    scene_hash = preview_scene_hash(scene_dir)
    library = pack_render_identity(clips)
    cloud_scenes = Path(__file__).resolve().parents[3] / "cloud" / "src" / "scenes"
    given = Path(dest) if dest is not None else cloud_scenes / "autoPreviewClips"
    clips_dir = given if given.suffix != ".gz" else given.parent / "autoPreviewClips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    for old in clips_dir.glob("*.json.gz"):
        old.unlink()
    for look, look_clips in by_look.items():
        payload = {
            "scene_hash": scene_hash,
            "library_version": library,
            "clips": look_clips,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (clips_dir / f"{look}.json.gz").write_bytes(gzip.compress(raw, compresslevel=9))
    available = [
        {
            "look": row["look"],
            "cycle_beats": row["cycle_beats"],
            "palette": row["palette"],
            "intensity": row["intensity"],
        }
        for row in clips.values()
    ]
    meta = {
        "scene_hash": scene_hash,
        "library_version": library,
        "library_count": len(COMPOSITIONS),
        "clip_count": len(clips),
        "note": PREVIEW_NOTE,
        "looks": sorted(by_look),
        "available": available,
    }
    meta_path = clips_dir.parent / "autoPreviewPack.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    monolith = clips_dir.parent / "autoPreviewPack.json.gz"
    if monolith.is_file():
        monolith.unlink()
    assets_root = Path(__file__).resolve().parents[3] / "cloud" / "admin-dist"
    if assets_root.is_dir():
        assets_clips = assets_root / "autoPreviewClips"
        assets_clips.mkdir(parents=True, exist_ok=True)
        for old in assets_clips.glob("*.json.gz"):
            old.unlink()
        for path in clips_dir.glob("*.json.gz"):
            (assets_clips / path.name).write_bytes(path.read_bytes())
        (assets_root / "autoPreviewPack.meta.json").write_text(
            meta_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        stale = assets_root / "autoPreviewPack.json.gz"
        if stale.is_file():
            stale.unlink()
    return clips_dir
