"""Deterministic lighting engine with layered overlays."""

from __future__ import annotations

from dataclasses import dataclass, field

from orng_led.config.models import FixtureKind, ShowConfig
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.engine.beam import BeamMotionLimits, BeamMotionState, step_beam
from orng_led.engine.clock import FRAME_DT, FakeClock
from orng_led.engine.frame import assert_frame_bounds, empty_frame
from orng_led.engine.intents import BeamIntent, StageIntent
from orng_led.engine.layers import (
    STROBE_HOLD_TIMEOUT_S,
    WHITE_HIT_DURATION_S,
    OverlayState,
    compose_layers,
)
from orng_led.engine.presets import (
    CYCLE_DURATION_S,
    EPISODE_DURATION_S,
    BasePulsePreset,
    PresetProgram,
    cycle_position,
)
from orng_led.engine.renderer import render_stage


@dataclass
class EngineSnapshot:
    time_s: float
    preset_id: str
    preset_time_s: float
    episode_index: int
    episode_time_s: float
    blackout: bool
    face_on: bool
    strobe_held: bool
    white_hit_active: bool
    master_brightness: float
    frame: list[int]


@dataclass
class Engine:
    show: ShowConfig
    clock: FakeClock = field(default_factory=FakeClock)
    presets: dict[str, PresetProgram] = field(default_factory=dict)
    active_preset_id: str = "P05"
    preset_elapsed_s: float = 0.0
    overlays: OverlayState = field(default_factory=OverlayState)
    beam_motion: dict[str, BeamMotionState] = field(default_factory=dict)
    beam_limits: BeamMotionLimits = field(default_factory=BeamMotionLimits)

    def __post_init__(self) -> None:
        if not self.presets:
            base = BasePulsePreset()
            self.presets = {base.id: base}
            self.active_preset_id = base.id
        for fixture in self.show.patch.fixtures:
            if fixture.kind is FixtureKind.BEAM and fixture.id not in self.beam_motion:
                self.beam_motion[fixture.id] = BeamMotionState()
        self.overlays.master_brightness = self.show.app.master_brightness

    @property
    def active_preset(self) -> PresetProgram:
        try:
            return self.presets[self.active_preset_id]
        except KeyError as exc:
            raise KeyError(f"Unknown preset {self.active_preset_id!r}") from exc

    def select_preset(self, preset_id: str, *, reset_clock: bool = True) -> None:
        if preset_id not in self.presets:
            raise KeyError(f"Unknown preset {preset_id!r}")
        self.active_preset_id = preset_id
        if reset_clock:
            self.preset_elapsed_s = 0.0

    def trigger_white_hit(self) -> None:
        now = self.clock.time()
        self.overlays.white_hit_until = now + WHITE_HIT_DURATION_S

    def strobe_press(self) -> None:
        now = self.clock.time()
        self.overlays.strobe_held = True
        self.overlays.strobe_started_at = now

    def strobe_release(self) -> None:
        self.overlays.strobe_held = False
        self.overlays.strobe_started_at = None

    def on_control_disconnect(self) -> None:
        """UI/WebSocket disconnect must clear held Strobe (canon §5.3)."""
        self.strobe_release()

    def on_focus_loss(self) -> None:
        self.strobe_release()

    def on_visibility_hidden(self) -> None:
        self.strobe_release()

    def set_face(self, enabled: bool, brightness: float | None = None) -> None:
        self.overlays.face_on = enabled
        if brightness is not None:
            self.overlays.face_brightness = max(0.0, min(1.0, brightness))

    def set_master_brightness(self, value: float) -> None:
        self.overlays.master_brightness = max(0.0, min(1.0, value))

    def toggle_blackout(self) -> bool:
        self.overlays.blackout = not self.overlays.blackout
        return self.overlays.blackout

    def set_blackout(self, enabled: bool) -> None:
        self.overlays.blackout = enabled

    def _expire_overlays(self, now: float) -> None:
        if self.overlays.white_hit_until is not None and now >= self.overlays.white_hit_until:
            self.overlays.white_hit_until = None
        if (
            self.overlays.strobe_held
            and self.overlays.strobe_started_at is not None
            and now - self.overlays.strobe_started_at >= STROBE_HOLD_TIMEOUT_S
        ):
            self.strobe_release()

    def _advance_beams(self, stage: StageIntent, dt_s: float) -> None:
        for fixture_id, intent in stage.fixtures.items():
            if not isinstance(intent, BeamIntent):
                continue
            state = self.beam_motion.setdefault(fixture_id, BeamMotionState())
            step_beam(state, intent.pan, intent.tilt, dt_s, self.beam_limits)

    def render_at(self, time_s: float, *, dt_s: float = 0.0) -> EngineSnapshot:
        """Render a deterministic frame for an absolute clock time."""
        self._expire_overlays(time_s)
        preset = self.active_preset
        base = preset.evaluate(self.preset_elapsed_s, self.show)
        composed = compose_layers(base, self.show, self.overlays, time_s)
        self._advance_beams(composed, dt_s)

        if self.overlays.blackout:
            frame = empty_frame()
        else:
            frame = render_stage(self.show, composed, self.beam_motion)

        assert_frame_bounds(frame)
        position_fn = getattr(preset, "cycle_position", None)
        if callable(position_fn):
            pos = position_fn(self.preset_elapsed_s)
            cycle_len = getattr(preset, "total_duration_s", CYCLE_DURATION_S) or CYCLE_DURATION_S
            preset_time = self.preset_elapsed_s % cycle_len
        else:
            pos = cycle_position(self.preset_elapsed_s)
            preset_time = self.preset_elapsed_s % CYCLE_DURATION_S
        white_hit_active = (
            self.overlays.white_hit_until is not None and time_s < self.overlays.white_hit_until
        )
        return EngineSnapshot(
            time_s=time_s,
            preset_id=self.active_preset_id,
            preset_time_s=preset_time,
            episode_index=pos.episode_index,
            episode_time_s=pos.episode_time_s,
            blackout=self.overlays.blackout,
            face_on=self.overlays.face_on,
            strobe_held=self.overlays.strobe_held,
            white_hit_active=white_hit_active,
            master_brightness=self.overlays.master_brightness,
            frame=frame,
        )

    def tick(self, dt_s: float = FRAME_DT) -> EngineSnapshot:
        """Advance virtual clock by one frame (default 1/30 s) and render."""
        if dt_s < 0:
            raise ValueError("dt_s must be >= 0")
        now = self.clock.advance(dt_s)
        # Preset clock always advances, including during blackout.
        self.preset_elapsed_s += dt_s
        return self.render_at(now, dt_s=dt_s)

    def frame_at_preset_time(self, preset_time_s: float) -> list[int]:
        """Pure helper: same preset time ⇒ same base+layers frame (no beam step)."""
        saved_elapsed = self.preset_elapsed_s
        saved_beams = {
            key: BeamMotionState(pan=state.pan, tilt=state.tilt)
            for key, state in self.beam_motion.items()
        }
        try:
            self.preset_elapsed_s = preset_time_s
            # Snap beams to targets for pure deterministic comparison of color layers.
            base = self.active_preset.evaluate(preset_time_s, self.show)
            for fixture_id, intent in base.fixtures.items():
                if isinstance(intent, BeamIntent):
                    self.beam_motion[fixture_id] = BeamMotionState(
                        pan=intent.pan,
                        tilt=intent.tilt,
                    )
            return self.render_at(self.clock.time(), dt_s=0.0).frame
        finally:
            self.preset_elapsed_s = saved_elapsed
            self.beam_motion = saved_beams


def create_engine(show: ShowConfig | None = None) -> Engine:
    from orng_led.config import load_show_config

    return Engine(show=show or load_show_config())


__all__ = [
    "CYCLE_DURATION_S",
    "DMX_UNIVERSE_SIZE",
    "EPISODE_DURATION_S",
    "Engine",
    "EngineSnapshot",
    "create_engine",
]
