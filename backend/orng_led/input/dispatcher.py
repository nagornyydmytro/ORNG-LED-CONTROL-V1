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
    InputAction,
    InputEvent,
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


@dataclass
class InputDispatcher:
    runtime: AppRuntime
    debouncer: EdgeDebouncer = field(default_factory=EdgeDebouncer)
    clock: Callable[[], float] = time.monotonic

    def dispatch(self, event: InputEvent) -> DispatchResult:
        now = float(self.clock())
        key = self._debounce_key(event)

        if event.action is InputAction.STROBE_PRESS:
            if not self.debouncer.accept_press(key, now):
                return DispatchResult(accepted=False, reason="strobe_press_ignored_repeat")
        elif event.action is InputAction.STROBE_RELEASE:
            if not self.debouncer.accept_release(key, now):
                return DispatchResult(accepted=False, reason="strobe_release_ignored")
        else:
            if not self.debouncer.accept_pulse(key, now):
                return DispatchResult(accepted=False, reason="debounced")

        state, replay = self._apply(event)
        return DispatchResult(
            accepted=True,
            state=state,
            idempotent_replay=replay,
        )

    def force_release_strobe(self) -> DispatchResult:
        """Safety path used on disconnect/focus loss — clears debounce hold."""
        self.debouncer.clear()
        state, replay = self.runtime.apply_strobe("release", client_command_id=None)
        return DispatchResult(accepted=True, state=state, idempotent_replay=replay)

    def _debounce_key(self, event: InputEvent) -> str:
        if event.action in (InputAction.STROBE_PRESS, InputAction.STROBE_RELEASE):
            return "strobe"
        if event.button_id is not None:
            return f"button:{event.button_id}:{event.action.value}"
        return f"action:{event.action.value}:{event.preset_id or ''}"

    def _apply(self, event: InputEvent) -> tuple[AppStateResponse, bool]:
        cid = event.client_command_id
        if event.action is InputAction.SELECT_PRESET:
            if not event.preset_id:
                raise ValueError("select_preset requires preset_id")
            return self.runtime.apply_select_preset(event.preset_id, client_command_id=cid)
        if event.action is InputAction.FACE_TOGGLE:
            enabled = not self.runtime.engine.overlays.face_on
            return self.runtime.apply_face(enabled, client_command_id=cid)
        if event.action is InputAction.WHITE_HIT:
            return self.runtime.apply_white_hit(client_command_id=cid)
        if event.action is InputAction.STROBE_PRESS:
            return self.runtime.apply_strobe("press", client_command_id=cid)
        if event.action is InputAction.STROBE_RELEASE:
            return self.runtime.apply_strobe("release", client_command_id=cid)
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
        raise ValueError(f"Unsupported action {event.action}")
