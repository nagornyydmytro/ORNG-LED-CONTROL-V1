"""Layer composition: preset → overlays → face → master → blackout."""

from __future__ import annotations

import math
from dataclasses import dataclass

from orng_led.config.models import FixtureKind, ShowConfig
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw, StageIntent

WHITE_HIT_DURATION_S = 0.18
STROBE_MAX_HZ = 4.0
STROBE_HOLD_TIMEOUT_S = 8.0


@dataclass
class OverlayState:
    white_hit_until: float | None = None
    strobe_held: bool = False
    strobe_started_at: float | None = None
    face_on: bool = False
    face_brightness: float = 0.55
    master_brightness: float = 1.0
    blackout: bool = False


def _strobe_active(overlays: OverlayState, now: float) -> bool:
    if not overlays.strobe_held or overlays.strobe_started_at is None:
        return False
    if now - overlays.strobe_started_at >= STROBE_HOLD_TIMEOUT_S:
        return False
    return True


def _strobe_gate(now: float) -> float:
    # 4 Hz square wave: on for half period.
    phase = math.floor(now * STROBE_MAX_HZ * 2) % 2
    return 1.0 if phase == 0 else 0.0


def apply_white_hit(stage: StageIntent, show: ShowConfig) -> StageIntent:
    white = Rgbw(r=1.0, g=1.0, b=1.0, w=1.0)
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.FACE_PAR:
            continue
        if fixture.kind is FixtureKind.PAR:
            stage.fixtures[fixture.id] = ParIntent(color=white, intensity=1.0)
        elif fixture.kind is FixtureKind.BAR:
            stage.fixtures[fixture.id] = BarIntent(segments=(1.0,) * 8, dimmer=1.0)
        elif fixture.kind is FixtureKind.BEAM:
            previous = stage.fixtures.get(fixture.id)
            pan = previous.pan if isinstance(previous, BeamIntent) else 0.5
            tilt = previous.tilt if isinstance(previous, BeamIntent) else 0.5
            stage.fixtures[fixture.id] = BeamIntent(
                pan=pan,
                tilt=tilt,
                dimmer=1.0,
                color=0.0,
                shutter_open=True,
            )
    return stage


def apply_strobe(stage: StageIntent, show: ShowConfig, now: float) -> StageIntent:
    gate = _strobe_gate(now)
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.FACE_PAR:
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
                strobe=0.85 if gate > 0 else 0.0,
            )
        elif isinstance(current, BeamIntent):
            stage.fixtures[fixture.id] = BeamIntent(
                pan=current.pan,
                tilt=current.tilt,
                dimmer=current.dimmer * gate,
                color=current.color,
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
                strobe=intent.strobe,
            )
        elif isinstance(intent, BeamIntent):
            stage.fixtures[fixture_id] = BeamIntent(
                pan=intent.pan,
                tilt=intent.tilt,
                dimmer=intent.dimmer * master,
                color=intent.color,
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

    if overlays.white_hit_until is not None and now < overlays.white_hit_until:
        stage = apply_white_hit(stage, show)

    if _strobe_active(overlays, now):
        stage = apply_strobe(stage, show, now)

    stage = apply_face(stage, show, overlays)
    stage = apply_master_brightness(stage, overlays.master_brightness)
    return stage
