"""Layer composition.

Priority, lowest first::

    preset → sweep hit → colour hit → white hit → strobe → drop
           → face → master → blackout

Blackout is not a layer here: the engine zeroes the *base* before composition,
so Live Effects still compose on top. Absolute physical stop is Disarm / Art-Net
gates — not Blackout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from orng_led.config.models import FixtureInstance, FixtureKind, ShowConfig
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw, StageIntent

WHITE_HIT_DURATION_S = 0.18
STROBE_MAX_HZ = 4.0
STROBE_HOLD_TIMEOUT_S = 8.0

# Quick live effects (canon §5.3 momentary rules).
DROP_MAX_DURATION_S = 1.2
COLOR_HIT_DURATION_S = 0.35
SWEEP_HIT_DURATION_S = 0.75
SWEEP_WIDTH = 0.22

WHITE = Rgbw(r=1.0, g=1.0, b=1.0, w=1.0)

# Live-effect identity for debug / UI (not tied to nonzero preset channels).
LIVE_EFFECT_META: dict[str, dict[str, object]] = {
    "white_hit": {
        "label": "White Hit",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "color_hit": {
        "label": "Color Hit",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "sweep_hit": {
        "label": "Sweep Hit",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "strobe": {
        "label": "Live Strobe",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "drop": {
        "label": "Drop",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "face": {
        "label": "DJ Face",
        "target_groups": ["face", "face_par"],
    },
}


@dataclass
class OverlayState:
    white_hit_until: float | None = None
    strobe_held: bool = False
    strobe_started_at: float | None = None
    face_on: bool = False
    face_brightness: float = 0.55
    master_brightness: float = 1.0
    blackout: bool = False
    # Quick live effects.
    drop_held: bool = False
    drop_started_at: float | None = None
    color_hit_until: float | None = None
    color_hit_color: Rgbw = field(default_factory=lambda: Rgbw(r=1.0, g=1.0, b=1.0))
    sweep_started_at: float | None = None
    sweep_color: Rgbw = field(default_factory=lambda: Rgbw(r=1.0, g=1.0, b=1.0))


def _strobe_active(overlays: OverlayState, now: float) -> bool:
    if not overlays.strobe_held or overlays.strobe_started_at is None:
        return False
    if now - overlays.strobe_started_at >= STROBE_HOLD_TIMEOUT_S:
        return False
    return True


def drop_active(overlays: OverlayState, now: float) -> bool:
    if not overlays.drop_held or overlays.drop_started_at is None:
        return False
    return now - overlays.drop_started_at < DROP_MAX_DURATION_S


def sweep_progress(overlays: OverlayState, now: float) -> float | None:
    if overlays.sweep_started_at is None:
        return None
    elapsed = now - overlays.sweep_started_at
    if elapsed < 0.0 or elapsed >= SWEEP_HIT_DURATION_S:
        return None
    return elapsed / SWEEP_HIT_DURATION_S


def _strobe_gate(now: float) -> float:
    # 4 Hz square wave: on for half period.
    phase = math.floor(now * STROBE_MAX_HZ * 2) % 2
    return 1.0 if phase == 0 else 0.0


def _is_rear(kind: FixtureKind) -> bool:
    return kind is not FixtureKind.FACE_PAR


def rear_fixture_ids(show: ShowConfig) -> list[str]:
    return [fx.id for fx in show.patch.fixtures if _is_rear(fx.kind)]


def face_fixture_ids(show: ShowConfig) -> list[str]:
    return [fx.id for fx in show.patch.fixtures if fx.kind is FixtureKind.FACE_PAR]


def _force_rear_look(
    stage: StageIntent,
    fixture: FixtureInstance,
    *,
    color: Rgbw,
    level: float,
    strobe: float = 0.0,
) -> None:
    """Independent full look for every rear fixture — never gated by the preset.

    Beam Live Effects must never touch Pan/Tilt: motion stays on the engine's
    last-valid pose. Only colour, dimmer and (when requested) strobe change.
    """
    level = max(0.0, min(1.0, level))
    if fixture.kind is FixtureKind.PAR:
        stage.fixtures[fixture.id] = ParIntent(color=color, intensity=level, strobe=strobe)
    elif fixture.kind is FixtureKind.BAR:
        segments = (level,) * 8 if level > 0 else (0.0,) * 8
        stage.fixtures[fixture.id] = BarIntent(
            segments=segments,
            dimmer=level,
            color=color,
            strobe=strobe,
            whole=False,
        )
    elif fixture.kind is FixtureKind.BEAM:
        stage.fixtures[fixture.id] = BeamIntent(
            pan=None,
            tilt=None,
            dimmer=level,
            color=color,
            shutter_open=level > 0.02,
            strobe=strobe,
        )


def apply_white_hit(stage: StageIntent, show: ShowConfig) -> StageIntent:
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        _force_rear_look(stage, fixture, color=WHITE, level=1.0)
    return stage


def apply_color_hit(stage: StageIntent, show: ShowConfig, color: Rgbw) -> StageIntent:
    """Short, strong RGB accent — full independent replace for every rear fixture."""
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        _force_rear_look(stage, fixture, color=color, level=1.0)
    return stage


def apply_sweep_hit(
    stage: StageIntent,
    show: ShowConfig,
    progress: float,
    color: Rgbw,
) -> StageIntent:
    """One fast pass across the semantic layout, left → right by stage x."""
    front = -SWEEP_WIDTH + progress * (1.0 + 2.0 * SWEEP_WIDTH)
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        placement = show.layout.placement_for(fixture.id)
        if placement is not None:
            position = placement.x
        else:
            order = fixture.spatial.order or 1
            position = (order - 1) / 3.0
        distance = abs(position - front)
        if distance >= SWEEP_WIDTH:
            continue
        boost = 1.0 - (distance / SWEEP_WIDTH)
        # Independent: wavefront forces light even if the preset left this fixture dark.
        _force_rear_look(stage, fixture, color=color, level=boost)
    return stage


def apply_drop(stage: StageIntent, show: ShowConfig) -> StageIntent:
    """Momentary kill of the stage look; the preset clock keeps running."""
    for fixture in show.patch.fixtures:
        if _is_rear(fixture.kind):
            stage.fixtures.pop(fixture.id, None)
    return stage


def apply_strobe(stage: StageIntent, show: ShowConfig, now: float) -> StageIntent:
    """Independent full-scene strobe — does not use the preset frame as a fixture mask."""
    gate = _strobe_gate(now)
    level = 1.0 if gate > 0 else 0.0
    strobe = 0.85 if gate > 0 else 0.0
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        _force_rear_look(stage, fixture, color=WHITE, level=level, strobe=strobe)
    return stage


def apply_face(stage: StageIntent, show: ShowConfig, overlays: OverlayState) -> StageIntent:
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.FACE_PAR:
            continue
        if overlays.face_on:
            stage.fixtures[fixture.id] = ParIntent(
                color=Rgbw(r=1.0, g=0.92, b=0.75, w=0.8),
                intensity=overlays.face_brightness,
            )
        else:
            stage.fixtures.pop(fixture.id, None)
    return stage


def apply_master_brightness(stage: StageIntent, master: float) -> StageIntent:
    """Master is applied in the renderer on DIMMER roles only — keep intents intact."""
    _ = master
    return stage


def active_live_effect_ids(overlays: OverlayState, now: float) -> list[str]:
    active: list[str] = []
    if sweep_progress(overlays, now) is not None:
        active.append("sweep_hit")
    if overlays.color_hit_until is not None and now < overlays.color_hit_until:
        active.append("color_hit")
    if overlays.white_hit_until is not None and now < overlays.white_hit_until:
        active.append("white_hit")
    if _strobe_active(overlays, now):
        active.append("strobe")
    if drop_active(overlays, now):
        active.append("drop")
    if overlays.face_on:
        active.append("face")
    return active


def target_fixture_ids_for_effect(effect_id: str, show: ShowConfig) -> list[str]:
    if effect_id == "face":
        return face_fixture_ids(show)
    if effect_id in LIVE_EFFECT_META:
        return rear_fixture_ids(show)
    return []


def compose_layers(
    base: StageIntent,
    show: ShowConfig,
    overlays: OverlayState,
    now: float,
) -> StageIntent:
    stage = StageIntent(fixtures=dict(base.fixtures))

    progress = sweep_progress(overlays, now)
    if progress is not None:
        stage = apply_sweep_hit(stage, show, progress, overlays.sweep_color)

    if overlays.color_hit_until is not None and now < overlays.color_hit_until:
        stage = apply_color_hit(stage, show, overlays.color_hit_color)

    if overlays.white_hit_until is not None and now < overlays.white_hit_until:
        stage = apply_white_hit(stage, show)

    if _strobe_active(overlays, now):
        stage = apply_strobe(stage, show, now)

    if drop_active(overlays, now):
        stage = apply_drop(stage, show)

    stage = apply_face(stage, show, overlays)
    stage = apply_master_brightness(stage, overlays.master_brightness)
    return stage
