"""Layer composition.

Priority, lowest first::

    preset → sweep → colour hit → white hit → strobe → drop
           → face → master → blackout

Strobe and Sweep are live presets: while held they discard the pad preset's
light/colour entirely and keep only moving-head pan/tilt from the episode.

Blackout is not a layer here: the engine zeroes the *base* before composition,
so Live Effects still compose on top. Absolute physical stop is Disarm / Art-Net
gates — not Blackout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from orng_led.config.models import FixtureInstance, FixtureKind, ShowConfig
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw, StageIntent

WHITE_HIT_DURATION_S = 0.18
# Frame-locked live strobe (engine runs at 30 FPS). Max = one flash every 2 frames.
STROBE_MAX_HZ = 15.0
STROBE_MIN_HZ = 1.0
STROBE_ON_FRAMES = 1
# Compat alias: one engine frame of white.
STROBE_FLASH_ON_S = 1.0 / 30.0
# 0 = no wall-clock auto-release. Holds end on keyup / focus / disconnect so a
# live FX can span automatic episode boundaries while the key stays down.
STROBE_HOLD_TIMEOUT_S = 0.0

# Quick live effects (canon §5.3 momentary rules).
DROP_MAX_DURATION_S = 8.0
COLOR_HIT_DURATION_S = 0.35
SWEEP_HOLD_TIMEOUT_S = 0.0
SWEEP_WIDTH = 0.22
# Sweep cycle period at speed=0.5; faster speeds shorten the period.
SWEEP_PERIOD_SLOW_S = 0.8
SWEEP_PERIOD_FAST_S = 0.093

WHITE = Rgbw(r=1.0, g=1.0, b=1.0)

SweepMode = Literal["horizontal", "vertical"]

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
        "label": "Sweep",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "vertical_sweep": {
        "label": "Vertical Sweep",
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
    strobe_speed: float = 0.10
    # 0..1 position in the flash cycle — speed only changes how fast this advances.
    strobe_phase: float = 0.0
    # Back-compat alias for older tests (integer tick index ≈ phase × period).
    strobe_tick: int = 0
    face_on: bool = False
    face_brightness: float = 1.0
    master_brightness: float = 1.0
    blackout: bool = False
    # Quick live effects.
    drop_held: bool = False
    drop_started_at: float | None = None
    color_hit_until: float | None = None
    color_hit_color: Rgbw = field(default_factory=lambda: Rgbw(r=1.0, g=1.0, b=1.0))
    sweep_held: bool = False
    sweep_started_at: float | None = None
    sweep_mode: SweepMode = "horizontal"
    sweep_speed: float = 0.10
    # 0..1 wavefront progress — integrated so live speed changes do not restart.
    sweep_phase: float = 0.0
    sweep_color: Rgbw = field(default_factory=lambda: Rgbw(r=1.0, g=1.0, b=1.0))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _strobe_active(overlays: OverlayState, now: float) -> bool:
    if not overlays.strobe_held or overlays.strobe_started_at is None:
        return False
    if (
        STROBE_HOLD_TIMEOUT_S > 0.0
        and now - overlays.strobe_started_at >= STROBE_HOLD_TIMEOUT_S
    ):
        return False
    return overlays.strobe_speed > 0.0


def drop_active(overlays: OverlayState, now: float) -> bool:
    if not overlays.drop_held or overlays.drop_started_at is None:
        return False
    return now - overlays.drop_started_at < DROP_MAX_DURATION_S


def _sweep_active(overlays: OverlayState, now: float) -> bool:
    if not overlays.sweep_held or overlays.sweep_started_at is None:
        return False
    if (
        SWEEP_HOLD_TIMEOUT_S > 0.0
        and now - overlays.sweep_started_at >= SWEEP_HOLD_TIMEOUT_S
    ):
        return False
    return overlays.sweep_speed > 0.0


def _sweep_period_s(speed: float) -> float:
    speed = _clamp01(speed)
    return SWEEP_PERIOD_SLOW_S + (SWEEP_PERIOD_FAST_S - SWEEP_PERIOD_SLOW_S) * speed


def sweep_progress(overlays: OverlayState, now: float) -> float | None:
    """Return looping 0..1 progress while sweep is held, or None when inactive.

    Progress is integrated in ``advance_sweep_phase`` so changing speed mid-hold
    only alters the rate — it does not rewrite the wavefront position.

    At speed ≈ 1.0 the stage is fully filled (progress sentinel 1.0 with full look).
    """
    if not _sweep_active(overlays, now):
        return None
    speed = _clamp01(overlays.sweep_speed)
    if speed >= 0.999:
        return 1.0
    return float(overlays.sweep_phase) % 1.0


def advance_sweep_phase(overlays: OverlayState, dt_s: float) -> None:
    """Advance sweep wavefront by wall ``dt_s`` at the current speed."""
    if dt_s <= 0.0 or not overlays.sweep_held:
        return
    speed = _clamp01(overlays.sweep_speed)
    if speed >= 0.999:
        overlays.sweep_phase = 1.0
        return
    period = max(0.05, _sweep_period_s(speed))
    overlays.sweep_phase = (float(overlays.sweep_phase) + float(dt_s) / period) % 1.0


def _strobe_period_frames(speed: float) -> int:
    """How many engine frames between strobe flashes (inclusive of the ON frame)."""
    from orng_led.engine.clock import FPS

    speed = _clamp01(speed)
    if speed <= 0.0:
        return 0
    hz = max(STROBE_MIN_HZ, STROBE_MAX_HZ * speed)
    # At least 2 frames so every flash has a dark neighbour — no aliasing at 30 FPS.
    return max(2, int(round(FPS / hz)))


def _strobe_gate_phase(phase: float, speed: float) -> float:
    """Flash gate from 0..1 cycle phase (speed-independent position)."""
    period = _strobe_period_frames(speed)
    if period <= 0:
        return 0.0
    # ON for the first STROBE_ON_FRAMES / period of the cycle.
    return 1.0 if (float(phase) % 1.0) < (STROBE_ON_FRAMES / period) else 0.0


def advance_strobe_phase(overlays: OverlayState) -> None:
    """One engine-frame step of the strobe cycle at the current speed."""
    if not overlays.strobe_held:
        return
    period = _strobe_period_frames(overlays.strobe_speed)
    if period <= 0:
        return
    overlays.strobe_phase = (float(overlays.strobe_phase) + 1.0 / period) % 1.0
    overlays.strobe_tick = int(overlays.strobe_phase * period + 1e-9) % period


def _strobe_gate_tick(tick: int, speed: float) -> float:
    """Frame-locked flash gate from engine tick count (not wall elapsed).

    Exactly ``STROBE_ON_FRAMES`` ticks white, then dark until the next period.
    Using tick index (not wall time) keeps the rhythm steady when the loop
    jitters — Art-Net still gets one clear flash every N engine frames.
    """
    period = _strobe_period_frames(speed)
    if period <= 0:
        return 0.0
    return 1.0 if (max(0, int(tick)) % period) < STROBE_ON_FRAMES else 0.0


def _strobe_gate(elapsed_s: float, speed: float) -> float:
    """Compat helper: map elapsed seconds → tick index at engine FPS."""
    from orng_led.engine.clock import FPS

    tick = int(max(0.0, float(elapsed_s)) * FPS + 1e-9)
    return _strobe_gate_tick(tick, speed)


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
    pan: float | None = None,
    tilt: float | None = None,
    segments: tuple[float, ...] | None = None,
) -> None:
    """Independent full look for every rear fixture — never gated by the preset.

    Beam light overlays preserve existing pan/tilt from the episode look when
    callers omit axes, so heads keep following the preset trajectory.
    """
    level = max(0.0, min(1.0, level))
    if fixture.kind is FixtureKind.PAR:
        stage.fixtures[fixture.id] = ParIntent(color=color, intensity=level, strobe=strobe)
    elif fixture.kind is FixtureKind.BAR:
        if segments is None:
            segs = (level,) * 8 if level > 0 else (0.0,) * 8
        else:
            segs = tuple(max(0.0, min(1.0, s)) for s in segments[:8])
            if len(segs) < 8:
                segs = segs + (0.0,) * (8 - len(segs))
        dimmer = max(segs) if segs else level
        stage.fixtures[fixture.id] = BarIntent(
            segments=segs,
            dimmer=dimmer,
            color=color,
            strobe=strobe,
            whole=False,
        )
    elif fixture.kind is FixtureKind.BEAM:
        on = level > 0.02
        prev = stage.fixtures.get(fixture.id)
        if isinstance(prev, BeamIntent):
            if pan is None:
                pan = prev.pan
            if tilt is None:
                tilt = prev.tilt
        stage.fixtures[fixture.id] = BeamIntent(
            pan=pan,
            tilt=tilt,
            dimmer=1.0 if on else 0.0,
            color=color,
            shutter_open=on,
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


def _fixture_axis(show: ShowConfig, fixture: FixtureInstance, axis: str) -> float:
    placement = show.layout.placement_for(fixture.id)
    if placement is not None:
        return float(getattr(placement, axis))
    order = fixture.spatial.order or 1
    return (order - 1) / 3.0


def motion_only_stage(base: StageIntent, show: ShowConfig) -> StageIntent:
    """Strip preset light/colour — keep only beam pan/tilt for the episode path.

    Used while Strobe/Sweep are held so those FX act as a live preset.
    """
    stage = StageIntent()
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        prev = base.fixtures.get(fixture.id)
        if isinstance(prev, BeamIntent):
            stage.fixtures[fixture.id] = BeamIntent(
                pan=prev.pan,
                tilt=prev.tilt,
                dimmer=0.0,
                color=Rgbw(),
                shutter_open=False,
                strobe=0.0,
            )
        else:
            # No axes in this episode frame — renderer/advance hold last_valid.
            stage.fixtures[fixture.id] = BeamIntent(
                pan=None,
                tilt=None,
                dimmer=0.0,
                color=Rgbw(),
                shutter_open=False,
                strobe=0.0,
            )
    return stage


def _claim_rear_light(stage: StageIntent, show: ShowConfig, color: Rgbw) -> None:
    """Live FX owns rear light: kill leftover look, keep beam pan/tilt from stage."""
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        _force_rear_look(stage, fixture, color=color, level=0.0)


def apply_sweep_hit(
    stage: StageIntent,
    show: ShowConfig,
    progress: float,
    color: Rgbw,
) -> StageIntent:
    """Hold sweep across the semantic layout, left → right by stage x."""
    # FX > preset for light: clear rear look first, then paint the wavefront.
    _claim_rear_light(stage, show, color)
    if progress >= 0.999:
        for fixture in show.patch.fixtures:
            if not _is_rear(fixture.kind):
                continue
            _force_rear_look(stage, fixture, color=color, level=1.0)
        return stage

    front = -SWEEP_WIDTH + progress * (1.0 + 2.0 * SWEEP_WIDTH)
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        position = _fixture_axis(show, fixture, "x")
        distance = abs(position - front)
        if distance >= SWEEP_WIDTH:
            continue
        boost = 1.0 - (distance / SWEEP_WIDTH)
        _force_rear_look(stage, fixture, color=color, level=boost)
    return stage


# Vertical sweep (0 = stage bottom, 1 = top):
#   1) lower PARs fade brightness up/down (longer envelope), before bars
#   2) bars climb segment-by-segment
#   3) ceiling beams flash near the top
# PAR pulses: (peak_at, attack, release) — ~2× longer than the first fade pass;
# all finish before bar climb.
_VERTICAL_PAR_PULSE: dict[str, tuple[float, float, float]] = {
    "par_2": (0.18, 0.11, 0.14),
    "par_3": (0.18, 0.11, 0.14),
    "par_1": (0.42, 0.11, 0.16),
    "par_4": (0.42, 0.11, 0.16),
}
# Bars start only after the upper PAR fade-out has finished.
_VERTICAL_BAR_START = 0.62
# Beams sit on the ceiling — pulse while upper bar segments climb (not from t=0).
_VERTICAL_BEAM_BAND = 0.86
_VERTICAL_BAND_WIDTH = 0.16
_VERTICAL_BEAM_BAND_WIDTH = 0.22


def _band_level(progress: float, band: float, *, width: float = _VERTICAL_BAND_WIDTH) -> float:
    """Soft pulse around a height band as the rising front passes."""
    distance = abs(progress - band)
    if distance >= width:
        return 0.0
    return 1.0 - distance / width


def _brightness_fade(
    progress: float,
    peak_at: float,
    *,
    attack: float,
    release: float,
) -> float:
    """Analog brightness rise/fall (smoothstep) — not a hard on/off gate."""
    start = peak_at - attack
    end = peak_at + release
    if progress <= start or progress >= end:
        return 0.0
    if progress <= peak_at:
        t = (progress - start) / max(1e-9, attack)
    else:
        t = 1.0 - (progress - peak_at) / max(1e-9, release)
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def apply_vertical_sweep(
    stage: StageIntent,
    show: ShowConfig,
    progress: float,
    color: Rgbw,
) -> StageIntent:
    """Hold sweep bottom → top: PARs → bars → ceiling beams; beams keep episode axes."""
    # FX > preset for light: bars/PARs outside the front stay dark (not preset).
    _claim_rear_light(stage, show, color)
    full = progress >= 0.999
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue

        if fixture.kind is FixtureKind.BAR:
            if full:
                segments = (1.0,) * 8
            elif progress < _VERTICAL_BAR_START:
                segments = (0.0,) * 8
            else:
                # Segment 0 = bottom, 7 = top — climb after the lower PAR band.
                bar_progress = (progress - _VERTICAL_BAR_START) / (1.0 - _VERTICAL_BAR_START)
                head = bar_progress * 8.0
                segments_list: list[float] = []
                for index in range(8):
                    distance = abs(index + 0.5 - head)
                    # Wider trail so bars read clearly during a fast hold.
                    segments_list.append(max(0.0, 1.0 - distance / 2.4))
                segments = tuple(segments_list)
            level = max(segments) if segments else 0.0
            _force_rear_look(stage, fixture, color=color, level=level, segments=segments)
            continue

        if fixture.kind is FixtureKind.BEAM:
            # Light pulse at ceiling height; pan/tilt stay on the episode path.
            if full:
                level = 1.0
            else:
                layout_y = _fixture_axis(show, fixture, "y")
                # Layout y grows downward — invert so ceiling mounts are near 1.0.
                band = max(_VERTICAL_BEAM_BAND, min(0.95, 1.0 - layout_y))
                level = _band_level(progress, band, width=_VERTICAL_BEAM_BAND_WIDTH)
            _force_rear_look(stage, fixture, color=color, level=level)
            continue

        if fixture.kind is FixtureKind.PAR:
            if full:
                _force_rear_look(stage, fixture, color=color, level=1.0)
                continue
            pulse = _VERTICAL_PAR_PULSE.get(fixture.id)
            if pulse is None:
                # Unknown rear PAR: short fade just before the bar climb.
                layout_y = _fixture_axis(show, fixture, "y")
                peak = max(0.06, min(_VERTICAL_BAR_START - 0.06, 1.0 - layout_y))
                pulse = (peak, 0.05, 0.07)
            peak_at, attack, release = pulse
            boost = _brightness_fade(progress, peak_at, attack=attack, release=release)
            _force_rear_look(stage, fixture, color=color, level=boost)
            continue

    return stage


def apply_drop(stage: StageIntent, show: ShowConfig) -> StageIntent:
    """Momentary kill of the rear stage look; the preset clock keeps running."""
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        if fixture.kind is FixtureKind.PAR:
            stage.fixtures[fixture.id] = ParIntent(
                color=Rgbw(),
                intensity=0.0,
                strobe=0.0,
            )
        elif fixture.kind is FixtureKind.BAR:
            stage.fixtures[fixture.id] = BarIntent(
                segments=(0.0,) * 8,
                dimmer=0.0,
                color=Rgbw(),
                strobe=0.0,
                whole=False,
            )
        elif fixture.kind is FixtureKind.BEAM:
            stage.fixtures[fixture.id] = BeamIntent(
                pan=None,
                tilt=None,
                dimmer=0.0,
                color=Rgbw(),
                shutter_open=False,
                strobe=0.0,
            )
        else:
            stage.fixtures.pop(fixture.id, None)
    return stage


def apply_strobe(
    stage: StageIntent,
    show: ShowConfig,
    now: float,
    speed: float,
    overlays: OverlayState | None = None,
) -> StageIntent:
    """Independent full-scene white strobe. Speed only changes how fast phase advances."""
    del now  # Rhythm comes from strobe_phase, advanced once per engine tick.
    if overlays is not None:
        level = 1.0 if _strobe_gate_phase(overlays.strobe_phase, speed) > 0.0 else 0.0
    else:
        level = 1.0 if _strobe_gate_phase(0.0, speed) > 0.0 else 0.0
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        # Full-intensity white hit for the flash; no fixture strobe-channel duty.
        _force_rear_look(stage, fixture, color=WHITE, level=level, strobe=0.0)
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
    if _sweep_active(overlays, now):
        if overlays.sweep_mode == "vertical":
            active.append("vertical_sweep")
        else:
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
    progress = sweep_progress(overlays, now)
    strobe_on = _strobe_active(overlays, now)
    # Strobe / Sweep = live preset: ignore pad colours, keep head motion only.
    if strobe_on or progress is not None:
        stage = motion_only_stage(base, show)
    else:
        stage = StageIntent(fixtures=dict(base.fixtures))

    if progress is not None:
        if overlays.sweep_mode == "vertical":
            stage = apply_vertical_sweep(stage, show, progress, overlays.sweep_color)
        else:
            stage = apply_sweep_hit(stage, show, progress, overlays.sweep_color)

    if overlays.color_hit_until is not None and now < overlays.color_hit_until:
        stage = apply_color_hit(stage, show, overlays.color_hit_color)

    if overlays.white_hit_until is not None and now < overlays.white_hit_until:
        stage = apply_white_hit(stage, show)

    if strobe_on:
        stage = apply_strobe(stage, show, now, overlays.strobe_speed, overlays)

    if drop_active(overlays, now):
        stage = apply_drop(stage, show)

    stage = apply_face(stage, show, overlays)
    stage = apply_master_brightness(stage, overlays.master_brightness)
    return stage
