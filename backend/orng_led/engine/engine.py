"""Deterministic lighting engine with layered overlays."""

from __future__ import annotations

from dataclasses import dataclass, field

from orng_led.config.models import FixtureKind, ShowConfig
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.engine.beam import BeamMotionLimits, BeamMotionState, step_beam
from orng_led.engine.clock import FRAME_DT, FakeClock
from orng_led.engine.frame import assert_frame_bounds
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw, StageIntent
from orng_led.engine.layers import (
    COLOR_HIT_DURATION_S,
    DROP_MAX_DURATION_S,
    STROBE_HOLD_TIMEOUT_S,
    SWEEP_HIT_DURATION_S,
    WHITE_HIT_DURATION_S,
    OverlayState,
    compose_layers,
    drop_active,
    sweep_progress,
)
from orng_led.engine.presets import (
    CYCLE_DURATION_S,
    EPISODE_DURATION_S,
    NONE_PRESET_ID,
    BasePulsePreset,
    NonePresetProgram,
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
    episode_count: int
    cycle_duration_s: float
    blackout: bool
    face_on: bool
    strobe_held: bool
    white_hit_active: bool
    master_brightness: float
    frame: list[int]
    drop_active: bool = False
    color_hit_active: bool = False
    sweep_active: bool = False


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
        if preset_id == NONE_PRESET_ID:
            self.presets[NONE_PRESET_ID] = NonePresetProgram()
        if preset_id not in self.presets:
            raise KeyError(f"Unknown preset {preset_id!r}")
        self.active_preset_id = preset_id
        if reset_clock:
            self.preset_elapsed_s = 0.0

    def seek_episode(self, episode_index: int) -> None:
        """Jump to the start of ``episode_index``; auto-continue afterwards."""
        if self.active_preset_id == NONE_PRESET_ID:
            raise ValueError("Cannot seek episodes while «Без пресету» is selected")
        preset = self.active_preset
        if hasattr(preset, "episode_count"):
            count = max(1, int(preset.episode_count))
        elif hasattr(preset, "document"):
            count = max(1, len(preset.document.episodes))
        else:
            count = 10
        index = max(0, min(int(episode_index), count - 1))
        duration = EPISODE_DURATION_S
        if hasattr(preset, "document") and preset.document.episodes:
            # Use the selected episode's configured duration for offset of prior ones.
            elapsed = 0.0
            for i, episode in enumerate(preset.document.episodes):
                if i >= index:
                    break
                elapsed += float(episode.duration_s)
            self.preset_elapsed_s = elapsed
            return
        self.preset_elapsed_s = float(index) * duration

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

    # --- quick live effects -------------------------------------------------

    def current_look_color(self) -> Rgbw:
        """Average semantic colour of the running preset look."""
        base = self.active_preset.evaluate(self.preset_elapsed_s, self.show)
        reds: list[float] = []
        greens: list[float] = []
        blues: list[float] = []
        for intent in base.fixtures.values():
            if isinstance(intent, ParIntent | BarIntent | BeamIntent):
                reds.append(intent.color.r)
                greens.append(intent.color.g)
                blues.append(intent.color.b)
        if not reds:
            return Rgbw(r=1.0, g=1.0, b=1.0)
        count = float(len(reds))
        return Rgbw(r=sum(reds) / count, g=sum(greens) / count, b=sum(blues) / count)

    def contrast_color(self) -> Rgbw:
        """Complement of the running look, so a Colour Hit always reads as an accent."""
        look = self.current_look_color()
        peak = max(look.r, look.g, look.b, 0.001)
        complement = Rgbw(
            r=max(0.0, peak - look.r),
            g=max(0.0, peak - look.g),
            b=max(0.0, peak - look.b),
        )
        strongest = max(complement.r, complement.g, complement.b)
        if strongest < 0.25:
            # Near-white look: fall back to a cool accent instead of brand orange.
            return Rgbw(r=0.15, g=0.45, b=1.0)
        scale = 1.0 / strongest
        return Rgbw(
            r=min(1.0, complement.r * scale),
            g=min(1.0, complement.g * scale),
            b=min(1.0, complement.b * scale),
        )

    def drop_press(self) -> None:
        self.overlays.drop_held = True
        self.overlays.drop_started_at = self.clock.time()

    def drop_release(self) -> None:
        self.overlays.drop_held = False
        self.overlays.drop_started_at = None

    def trigger_color_hit(self, color: Rgbw | None = None) -> Rgbw:
        chosen = color or self.contrast_color()
        self.overlays.color_hit_color = chosen
        self.overlays.color_hit_until = self.clock.time() + COLOR_HIT_DURATION_S
        return chosen

    def trigger_sweep_hit(self, color: Rgbw | None = None) -> Rgbw:
        chosen = color or self.contrast_color()
        self.overlays.sweep_color = chosen
        self.overlays.sweep_started_at = self.clock.time()
        return chosen

    def release_momentary(self) -> None:
        """Release every held momentary control (failsafe path)."""
        self.strobe_release()
        self.drop_release()

    def on_control_disconnect(self) -> None:
        """UI/WebSocket disconnect must clear held momentary controls (canon §5.3)."""
        self.release_momentary()

    def on_focus_loss(self) -> None:
        self.release_momentary()

    def on_visibility_hidden(self) -> None:
        self.release_momentary()

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
        if self.overlays.color_hit_until is not None and now >= self.overlays.color_hit_until:
            self.overlays.color_hit_until = None
        if (
            self.overlays.sweep_started_at is not None
            and now - self.overlays.sweep_started_at >= SWEEP_HIT_DURATION_S
        ):
            self.overlays.sweep_started_at = None
        if (
            self.overlays.drop_held
            and self.overlays.drop_started_at is not None
            and now - self.overlays.drop_started_at >= DROP_MAX_DURATION_S
        ):
            self.drop_release()

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
        if self.active_preset_id == NONE_PRESET_ID:
            base = StageIntent()
        else:
            base = preset.evaluate(self.preset_elapsed_s, self.show)

        # Blackout zeroes the preset/base look only. Live Effects still compose
        # on top and can produce light while Blackout remains engaged.
        if self.overlays.blackout:
            base = StageIntent()

        composed = compose_layers(base, self.show, self.overlays, time_s)
        self._advance_beams(composed, dt_s)
        frame = render_stage(self.show, composed, self.beam_motion)

        assert_frame_bounds(frame)
        if self.active_preset_id == NONE_PRESET_ID:
            from orng_led.engine.presets import CyclePosition as _CP

            pos = _CP(0.0, 0, 0.0, 0.0)
            cycle_len = 0.0
            preset_time = 0.0
            episode_count = 0
        else:
            position_fn = getattr(preset, "cycle_position", None)
            if callable(position_fn):
                pos = position_fn(self.preset_elapsed_s)
                cycle_len = float(
                    getattr(preset, "total_duration_s", CYCLE_DURATION_S) or CYCLE_DURATION_S
                )
                preset_time = self.preset_elapsed_s % cycle_len if cycle_len else 0.0
                if hasattr(preset, "episode_count"):
                    episode_count = int(preset.episode_count)
                elif hasattr(preset, "document"):
                    episode_count = len(preset.document.episodes)
                else:
                    episode_count = 10
            else:
                pos = cycle_position(self.preset_elapsed_s)
                cycle_len = CYCLE_DURATION_S
                preset_time = self.preset_elapsed_s % CYCLE_DURATION_S
                episode_count = 10
        white_hit_active = (
            self.overlays.white_hit_until is not None and time_s < self.overlays.white_hit_until
        )
        return EngineSnapshot(
            time_s=time_s,
            preset_id=self.active_preset_id,
            preset_time_s=preset_time,
            episode_index=pos.episode_index,
            episode_time_s=pos.episode_time_s,
            episode_count=episode_count,
            cycle_duration_s=cycle_len,
            blackout=self.overlays.blackout,
            face_on=self.overlays.face_on,
            strobe_held=self.overlays.strobe_held,
            white_hit_active=white_hit_active,
            master_brightness=self.overlays.master_brightness,
            frame=frame,
            drop_active=drop_active(self.overlays, time_s),
            color_hit_active=(
                self.overlays.color_hit_until is not None and time_s < self.overlays.color_hit_until
            ),
            sweep_active=sweep_progress(self.overlays, time_s) is not None,
        )

    def tick(
        self,
        dt_s: float = FRAME_DT,
        *,
        wall_dt_s: float | None = None,
    ) -> EngineSnapshot:
        """Advance clocks and render.

        ``dt_s`` advances show/preset time (may be scaled by preview_speed).
        ``wall_dt_s`` advances the real safety clock used by White Hit / Strobe
        timeouts. When omitted, both clocks use ``dt_s``.
        """
        if dt_s < 0:
            raise ValueError("dt_s must be >= 0")
        wall = dt_s if wall_dt_s is None else wall_dt_s
        if wall < 0:
            raise ValueError("wall_dt_s must be >= 0")
        now = self.clock.advance(wall)
        # Preset clock advances except for «Без пресету».
        if self.active_preset_id != NONE_PRESET_ID:
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
