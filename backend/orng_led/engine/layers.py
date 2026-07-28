"""Layer composition.

Priority, lowest first::

    preset → sweep hit → colour hit → white hit → strobe → drop
           → face → master → blackout

Blackout is not a layer here: the engine zeroes the frame after composition,
so nothing can ever out-rank it. Every timeout below is measured on the real
monotonic engine clock, never on preview-scaled show time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from orng_led.config.models import FixtureKind, ShowConfig
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


def apply_white_hit(stage: StageIntent, show: ShowConfig) -> StageIntent:
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        if fixture.kind is FixtureKind.PAR:
            stage.fixtures[fixture.id] = ParIntent(color=WHITE, intensity=1.0)
        elif fixture.kind is FixtureKind.BAR:
            stage.fixtures[fixture.id] = BarIntent(
                segments=(1.0,) * 8,
                dimmer=1.0,
                color=WHITE,
            )
        elif fixture.kind is FixtureKind.BEAM:
            previous = stage.fixtures.get(fixture.id)
            pan = previous.pan if isinstance(previous, BeamIntent) else 0.5
            tilt = previous.tilt if isinstance(previous, BeamIntent) else 0.5
            stage.fixtures[fixture.id] = BeamIntent(
                pan=pan,
                tilt=tilt,
                dimmer=1.0,
                color=WHITE,
                shutter_open=True,
            )
    return stage


def apply_color_hit(stage: StageIntent, show: ShowConfig, color: Rgbw) -> StageIntent:
    """Short, strong RGB accent over the running preset (never brand orange by default)."""
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        current = stage.fixtures.get(fixture.id)
        if fixture.kind is FixtureKind.PAR:
            stage.fixtures[fixture.id] = ParIntent(color=color, intensity=1.0)
        elif fixture.kind is FixtureKind.BAR:
            stage.fixtures[fixture.id] = BarIntent(
                segments=(1.0,) * 8,
                dimmer=1.0,
                color=color,
            )
        elif fixture.kind is FixtureKind.BEAM:
            pan = current.pan if isinstance(current, BeamIntent) else 0.5
            tilt = current.tilt if isinstance(current, BeamIntent) else 0.5
            stage.fixtures[fixture.id] = BeamIntent(
                pan=pan,
                tilt=tilt,
                dimmer=1.0,
                color=color,
                shutter_open=True,
            )
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
        current = stage.fixtures.get(fixture.id)
        if fixture.kind is FixtureKind.PAR:
            base_intensity = current.intensity if isinstance(current, ParIntent) else 0.0
            stage.fixtures[fixture.id] = ParIntent(
                color=color,
                intensity=max(base_intensity, boost),
            )
        elif fixture.kind is FixtureKind.BAR:
            base_dimmer = current.dimmer if isinstance(current, BarIntent) else 0.0
            stage.fixtures[fixture.id] = BarIntent(
                segments=(boost,) * 8,
                dimmer=max(base_dimmer, boost),
                color=color,
            )
        elif fixture.kind is FixtureKind.BEAM:
            pan = current.pan if isinstance(current, BeamIntent) else 0.5
            tilt = current.tilt if isinstance(current, BeamIntent) else 0.5
            base_dimmer = current.dimmer if isinstance(current, BeamIntent) else 0.0
            stage.fixtures[fixture.id] = BeamIntent(
                pan=pan,
                tilt=tilt,
                dimmer=max(base_dimmer, boost),
                color=color,
                shutter_open=True,
            )
    return stage


def apply_drop(stage: StageIntent, show: ShowConfig) -> StageIntent:
    """Momentary kill of the stage look; the preset clock keeps running."""
    for fixture in show.patch.fixtures:
        if _is_rear(fixture.kind):
            stage.fixtures.pop(fixture.id, None)
    return stage


def apply_strobe(stage: StageIntent, show: ShowConfig, now: float) -> StageIntent:
    gate = _strobe_gate(now)
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        current = stage.fixtures.get(fixture.id)
        if isinstance(current, ParIntent):
            stage.fixtures[fixture.id] = ParIntent(
                color=current.color,
                intensity=current.intensity * gate,
                strobe=0.85 if gate > 0 else 0.0,
            )
        elif isinstance(current, BarIntent):
            segments = tuple(level * gate for level in current.segments)
            stage.fixtures[fixture.id] = BarIntent(
                segments=segments,
                dimmer=current.dimmer * gate,
                color=current.color,
                strobe=0.85 if gate > 0 else 0.0,
            )
        elif isinstance(current, BeamIntent):
            stage.fixtures[fixture.id] = BeamIntent(
                pan=current.pan,
                tilt=current.tilt,
                dimmer=current.dimmer * gate,
                color=current.color,
                wheel=current.wheel,
                shutter_open=gate > 0,
                strobe=0.85 if gate > 0 else 0.0,
            )
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
    master = max(0.0, min(1.0, master))
    for fixture_id, intent in list(stage.fixtures.items()):
        if isinstance(intent, ParIntent):
            stage.fixtures[fixture_id] = ParIntent(
                color=intent.color,
                intensity=intent.intensity * master,
                strobe=intent.strobe,
            )
        elif isinstance(intent, BarIntent):
            stage.fixtures[fixture_id] = BarIntent(
                segments=intent.segments,
                dimmer=intent.dimmer * master,
                color=intent.color,
                strobe=intent.strobe,
            )
        elif isinstance(intent, BeamIntent):
            stage.fixtures[fixture_id] = BeamIntent(
                pan=intent.pan,
                tilt=intent.tilt,
                dimmer=intent.dimmer * master,
                color=intent.color,
                wheel=intent.wheel,
                shutter_open=intent.shutter_open,
                strobe=intent.strobe,
            )
    return stage


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
