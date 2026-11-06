"""Dispatch InputEvent values into AppRuntime command methods."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from orng_led.api.schemas import AppStateResponse
from orng_led.input.contract import (
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    BRIGHTNESS_STEP,
    LIVE_FX_SPEED_STEP,
    InputAction,
    InputEvent,
    resolve_source_id,
)
from orng_led.input.debounce import EdgeDebouncer

if TYPE_CHECKING:
    from orng_led.api.runtime import AppRuntime


@dataclass
class DispatchResult:
    accepted: bool
    reason: str | None = None
    state: AppStateResponse | None = None
    idempotent_replay: bool = False


_HOLD_PRESS = {
    InputAction.STROBE_PRESS: "strobe",
    InputAction.SWEEP_PRESS: "sweep",
    InputAction.VERTICAL_SWEEP_PRESS: "vertical_sweep",
}
_HOLD_RELEASE = {
    InputAction.STROBE_RELEASE: "strobe",
    InputAction.SWEEP_RELEASE: "sweep",
    InputAction.VERTICAL_SWEEP_RELEASE: "vertical_sweep",
}
_HOLD_EFFECTS = ("strobe", "sweep", "vertical_sweep")
_LIVE_OVER_AUTO = frozenset(
    {
        InputAction.STROBE_PRESS,
        InputAction.SWEEP_PRESS,
        InputAction.VERTICAL_SWEEP_PRESS,
        InputAction.FACE_TOGGLE,
        InputAction.LIVE_FX_SPEED_UP,
        InputAction.LIVE_FX_SPEED_DOWN,
        InputAction.PROGRAM_SPEED_UP,
        InputAction.PROGRAM_SPEED_DOWN,
    }
)


@dataclass
class HoldRegistry:
    """Track which source_ids currently hold each live FX effect."""

    _holders: dict[str, set[str]] = field(
        default_factory=lambda: {name: set() for name in _HOLD_EFFECTS}
    )

    def clear(self) -> None:
        for holders in self._holders.values():
            holders.clear()
        self._tokens.clear()

    def holders(self, effect: str) -> set[str]:
        return set(self._holders.get(effect, set()))

    def is_held_by(self, effect: str, source_id: str) -> bool:
        return source_id in self._holders.get(effect, set())

    def any_held(self, effect: str) -> bool:
        return bool(self._holders.get(effect))

    _tokens: dict[tuple[str, str], str] = field(default_factory=dict)

    def add(self, effect: str, source_id: str, token: str | None = None) -> bool:
        """Add a holder. Returns True if this is a new holder for the effect.

        A second press from the same source replaces the token and does not
        start a second activation. The previous token can no longer release it.
        """
        holders = self._holders.setdefault(effect, set())
        if token:
            self._tokens[(effect, source_id)] = token
        if source_id in holders:
            return False
        holders.add(source_id)
        return True

    def remove(self, effect: str, source_id: str, token: str | None = None) -> bool | None:
        """Remove a holder.

        Returns True if the effect set became empty, False if other holders
        remain or this source was not holding, and None when ``token`` is a
        stale id that must not cancel the current hold.
        """
        current = self._tokens.get((effect, source_id))
        if token and current and current != token:
            return None
        holders = self._holders.setdefault(effect, set())
        if source_id not in holders:
            return False
        holders.discard(source_id)
        self._tokens.pop((effect, source_id), None)
        return len(holders) == 0

    def remove_source(self, source_id: str) -> list[str]:
        """Remove source from all effects; return effects that became empty."""
        cleared: list[str] = []
        for effect in _HOLD_EFFECTS:
            outcome = self.remove(effect, source_id)
            if outcome is True:
                cleared.append(effect)
        return cleared


@dataclass
class InputDispatcher:
    runtime: AppRuntime
    debouncer: EdgeDebouncer = field(default_factory=EdgeDebouncer)
    holds: HoldRegistry = field(default_factory=HoldRegistry)
    clock: Callable[[], float] = time.monotonic
    _auto_latched: set[str] = field(default_factory=set)

    def dispatch(self, event: InputEvent) -> DispatchResult:
        now = float(self.clock())
        source_id = resolve_source_id(event)
        key = self._debounce_key(event, source_id)
        if event.action in _HOLD_RELEASE:
            self._auto_latched.discard(key)
        elif key in self._auto_latched and event.action in _HOLD_PRESS:
            return DispatchResult(accepted=False, reason="auto_press_stale")
        if self._auto_blocks(event):
            return DispatchResult(accepted=False, reason="auto_keyboard_blocked")

        encoder_actions = {
            InputAction.PROGRAM_SPEED_UP,
            InputAction.PROGRAM_SPEED_DOWN,
            InputAction.ZOOM_UP,
            InputAction.ZOOM_DOWN,
            InputAction.LIVE_FX_SPEED_UP,
            InputAction.LIVE_FX_SPEED_DOWN,
        }

        if event.action in _HOLD_PRESS:
            if not self.debouncer.accept_press(key, now):
                effect = _HOLD_PRESS[event.action]
                current = self.holds._tokens.get((effect, source_id))  # noqa: SLF001
                if event.hold_token and current and event.hold_token != current:
                    self.holds.add(effect, source_id, event.hold_token)
                    return DispatchResult(accepted=True, state=self.runtime.build_state())
                return DispatchResult(accepted=False, reason=f"{event.action.value}_ignored_repeat")
            return self._apply_hold_press(event, source_id)
        if event.action in _HOLD_RELEASE:
            if not self.debouncer.accept_release(key, now):
                # Debounce thinks we are not held — still try registry cleanup for races.
                effect = _HOLD_RELEASE[event.action]
                if not self.holds.is_held_by(effect, source_id):
                    engine_held = self._engine_effect_held(effect)
                    if not engine_held:
                        return DispatchResult(
                            accepted=False, reason=f"{event.action.value}_ignored"
                        )
            return self._apply_hold_release(event, source_id)
        if event.action in encoder_actions:
            # Encoders tick faster than button debounce; keep a short floor only.
            last = self.debouncer._last_accept.get(key)  # noqa: SLF001
            if last is not None and now - last < 0.02:
                return DispatchResult(accepted=False, reason="encoder_debounced")
            self.debouncer._last_accept[key] = now  # noqa: SLF001
        else:
            if not self.debouncer.accept_pulse(key, now):
                return DispatchResult(accepted=False, reason="debounced")

        state, replay = self._apply(event)
        return DispatchResult(
            accepted=True,
            state=state,
            idempotent_replay=replay,
        )

    def _auto_active(self) -> bool:
        bpm = getattr(self.runtime, "bpm", None)
        if bpm is None:
            return False
        return bool(getattr(bpm, "enabled", False)) and getattr(bpm, "scope", "") == "auto"

    def _auto_blocks(self, event: InputEvent) -> bool:
        """Auto owns the look. Live strobe, sweeps and Face stay on top."""
        if not self._auto_active():
            return False
        if event.action is InputAction.BLACKOUT_TOGGLE or event.action in _HOLD_RELEASE:
            return False
        if event.action in _LIVE_OVER_AUTO:
            return False
        return True

    def latch_for_auto(self) -> None:
        """Preset holds end. Live strobe, sweeps and Face continue over Auto."""
        return

    def force_release_all(self) -> DispatchResult:
        """Clear every source hold (shutdown / scene switch)."""
        self.holds.clear()
        self.debouncer.clear()
        state, replay = self.runtime.apply_strobe("release", client_command_id=None)
        self.runtime.apply_sweep("release", client_command_id=None)
        return DispatchResult(accepted=True, state=state, idempotent_replay=replay)

    def force_release_source(self, source_id: str) -> DispatchResult:
        """Release holds owned by one source (WS disconnect / keyboard unplug)."""
        cleared = self.holds.remove_source(source_id)
        # Drop debounce keys for this source so a reconnect can press again.
        prefix = f":{source_id}"
        stale = [key for key in list(self.debouncer._held) if key.endswith(prefix)]  # noqa: SLF001
        for key in stale:
            self.debouncer._held.discard(key)  # noqa: SLF001
            self.debouncer._last_accept.pop(f"{key}:press", None)  # noqa: SLF001
            self.debouncer._last_accept.pop(f"{key}:release", None)  # noqa: SLF001
        state: AppStateResponse | None = None
        replay = False
        if "strobe" in cleared and not self.holds.any_held("strobe"):
            state, replay = self.runtime.apply_strobe("release", client_command_id=None)
        if ("sweep" in cleared or "vertical_sweep" in cleared) and not (
            self.holds.any_held("sweep") or self.holds.any_held("vertical_sweep")
        ):
            state, replay = self.runtime.apply_sweep("release", client_command_id=None)
        elif "sweep" in cleared and self.holds.any_held("vertical_sweep"):
            state, replay = self.runtime.apply_sweep(
                "press", mode="vertical", client_command_id=None
            )
        elif "vertical_sweep" in cleared and self.holds.any_held("sweep"):
            state, replay = self.runtime.apply_sweep(
                "press", mode="horizontal", client_command_id=None
            )
        if state is None:
            state = self.runtime.build_state()
        return DispatchResult(accepted=True, state=state, idempotent_replay=replay)

    def force_release_strobe(self) -> DispatchResult:
        """Back-compat alias used by older disconnect paths — clears all holds."""
        return self.force_release_all()

    def _engine_effect_held(self, effect: str) -> bool:
        if effect == "strobe":
            return bool(self.runtime.engine.overlays.strobe_held)
        return bool(self.runtime.engine.overlays.sweep_held)

    def _apply_hold_press(self, event: InputEvent, source_id: str) -> DispatchResult:
        effect = _HOLD_PRESS[event.action]
        if not self.holds.add(effect, source_id, event.hold_token):
            return DispatchResult(accepted=True, state=self.runtime.build_state())
        # First holder for this effect (or additional holder while already engaged).
        if len(self.holds.holders(effect)) == 1:
            state, replay = self._engage_effect(effect)
        else:
            state, replay = self.runtime.build_state(), False
        return DispatchResult(accepted=True, state=state, idempotent_replay=replay)

    def _apply_hold_release(self, event: InputEvent, source_id: str) -> DispatchResult:
        effect = _HOLD_RELEASE[event.action]
        if event.hold_token:
            current = self.holds._tokens.get((effect, source_id))  # noqa: SLF001
            if current and current != event.hold_token:
                return DispatchResult(accepted=False, reason="stale_hold")
        was_holding = self.holds.is_held_by(effect, source_id)
        became_empty = self.holds.remove(effect, source_id, event.hold_token)
        if became_empty is None:
            return DispatchResult(accepted=False, reason="stale_hold")
        if not was_holding:
            # The registry can miss a press. If nobody else is holding and the
            # light is still on, the release the operator just sent must stop it.
            if (
                effect == "strobe"
                and not self.holds.any_held("strobe")
                and self._engine_effect_held("strobe")
            ):
                state, replay = self.runtime.apply_strobe("release", client_command_id=None)
                return DispatchResult(accepted=True, state=state, idempotent_replay=replay)
            if (
                effect != "strobe"
                and not self.holds.any_held("sweep")
                and not self.holds.any_held("vertical_sweep")
                and self._engine_effect_held("sweep")
            ):
                state, replay = self.runtime.apply_sweep("release", client_command_id=None)
                return DispatchResult(accepted=True, state=state, idempotent_replay=replay)
            return DispatchResult(accepted=False, reason=f"{event.action.value}_ignored")
        if not became_empty:
            # Other sources still hold this effect.
            state = self.runtime.build_state()
            return DispatchResult(accepted=True, state=state, idempotent_replay=False)
        # This effect's set is empty — maybe another sweep flavour is still held.
        if effect == "strobe":
            state, replay = self.runtime.apply_strobe("release", client_command_id=None)
            return DispatchResult(accepted=True, state=state, idempotent_replay=replay)
        if self.holds.any_held("sweep"):
            state, replay = self.runtime.apply_sweep(
                "press", mode="horizontal", client_command_id=None
            )
            return DispatchResult(accepted=True, state=state, idempotent_replay=replay)
        if self.holds.any_held("vertical_sweep"):
            state, replay = self.runtime.apply_sweep(
                "press", mode="vertical", client_command_id=None
            )
            return DispatchResult(accepted=True, state=state, idempotent_replay=replay)
        state, replay = self.runtime.apply_sweep("release", client_command_id=None)
        return DispatchResult(accepted=True, state=state, idempotent_replay=replay)

    def _engage_effect(self, effect: str) -> tuple[AppStateResponse, bool]:
        if effect == "strobe":
            return self.runtime.apply_strobe("press", client_command_id=None)
        if effect == "vertical_sweep":
            return self.runtime.apply_sweep("press", mode="vertical", client_command_id=None)
        return self.runtime.apply_sweep("press", mode="horizontal", client_command_id=None)

    def _debounce_key(self, event: InputEvent, source_id: str) -> str:
        if event.action in (
            InputAction.STROBE_PRESS,
            InputAction.STROBE_RELEASE,
        ):
            return f"strobe:{source_id}"
        if event.action in (InputAction.SWEEP_PRESS, InputAction.SWEEP_RELEASE):
            return f"sweep:{source_id}"
        if event.action in (
            InputAction.VERTICAL_SWEEP_PRESS,
            InputAction.VERTICAL_SWEEP_RELEASE,
        ):
            return f"vertical_sweep:{source_id}"
        if event.button_id is not None:
            return f"button:{event.button_id}:{event.action.value}:{source_id}"
        return f"action:{event.action.value}:{event.preset_id or event.pad_slot or ''}:{source_id}"

    def _apply(self, event: InputEvent) -> tuple[AppStateResponse, bool]:
        cid = event.client_command_id
        if event.action is InputAction.SELECT_PRESET:
            if not event.preset_id:
                raise ValueError("select_preset requires preset_id")
            return self.runtime.apply_select_preset(event.preset_id, client_command_id=cid)
        if event.action is InputAction.SELECT_PAD_SLOT:
            if event.pad_slot is None:
                raise ValueError("select_pad_slot requires pad_slot")
            return self.runtime.apply_select_pad_slot(event.pad_slot, client_command_id=cid)
        if event.action is InputAction.FACE_TOGGLE:
            enabled = not self.runtime.engine.overlays.face_on
            return self.runtime.apply_face(enabled, client_command_id=cid)
        if event.action is InputAction.WHITE_HIT:
            return self.runtime.apply_white_hit(client_command_id=cid)
        if event.action is InputAction.STROBE_PRESS:
            return self.runtime.apply_strobe("press", client_command_id=cid)
        if event.action is InputAction.STROBE_RELEASE:
            return self.runtime.apply_strobe("release", client_command_id=cid)
        if event.action is InputAction.SWEEP_PRESS:
            return self.runtime.apply_sweep("press", mode="horizontal", client_command_id=cid)
        if event.action is InputAction.SWEEP_RELEASE:
            return self.runtime.apply_sweep("release", client_command_id=cid)
        if event.action is InputAction.VERTICAL_SWEEP_PRESS:
            return self.runtime.apply_sweep("press", mode="vertical", client_command_id=cid)
        if event.action is InputAction.VERTICAL_SWEEP_RELEASE:
            return self.runtime.apply_sweep("release", client_command_id=cid)
        if event.action is InputAction.BLACKOUT_TOGGLE:
            return self.runtime.apply_blackout(None, client_command_id=cid)
        if event.action is InputAction.BRIGHTNESS_DOWN:
            current = self.runtime.engine.overlays.master_brightness
            value = max(BRIGHTNESS_MIN, current - BRIGHTNESS_STEP)
            return self.runtime.apply_master_brightness(value, client_command_id=cid)
        if event.action is InputAction.BRIGHTNESS_UP:
            current = self.runtime.engine.overlays.master_brightness
            value = min(BRIGHTNESS_MAX, current + BRIGHTNESS_STEP)
            return self.runtime.apply_master_brightness(value, client_command_id=cid)
        if event.action is InputAction.ZOOM_DOWN:
            return self.runtime.apply_zoom(-1, client_command_id=cid)
        if event.action is InputAction.ZOOM_UP:
            return self.runtime.apply_zoom(1, client_command_id=cid)
        if event.action is InputAction.EPISODE_PREV:
            return self.runtime.apply_nudge_episode(-1, client_command_id=cid)
        if event.action is InputAction.EPISODE_NEXT:
            return self.runtime.apply_nudge_episode(1, client_command_id=cid)
        if event.action is InputAction.PROGRAM_SPEED_DOWN:
            from orng_led.input.contract import PROGRAM_SPEED_STEP

            return self.runtime.apply_nudge_program_speed(
                -PROGRAM_SPEED_STEP, client_command_id=cid
            )
        if event.action is InputAction.PROGRAM_SPEED_UP:
            from orng_led.input.contract import PROGRAM_SPEED_STEP

            return self.runtime.apply_nudge_program_speed(PROGRAM_SPEED_STEP, client_command_id=cid)
        if event.action is InputAction.LIVE_FX_SPEED_DOWN:
            return self.runtime.apply_nudge_live_fx_speed(
                -LIVE_FX_SPEED_STEP, client_command_id=cid
            )
        if event.action is InputAction.LIVE_FX_SPEED_UP:
            return self.runtime.apply_nudge_live_fx_speed(LIVE_FX_SPEED_STEP, client_command_id=cid)
        raise ValueError(f"Unsupported action {event.action}")
