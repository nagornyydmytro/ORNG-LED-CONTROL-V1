"""Mock / keyboard adapters and future GPIO boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from orng_led.input.contract import Edge, InputAction, InputEvent, InputSource
from orng_led.input.mapping import (
    DIGIT_PAD_SLOT,
    HOLD_RELEASE_CODES,
    KEYBOARD_CODE_MAP,
    button_to_event,
)


class InputAdapter(Protocol):
    """Common adapter boundary for keyboard/mock/GPIO sources."""

    source: InputSource

    def handle_raw(self, raw: dict) -> list[InputEvent]:
        """Convert a source-specific payload into zero or more InputEvents."""


@dataclass
class MockInputAdapter:
    """Programmatic button presses for tests and tooling."""

    source: InputSource = InputSource.MOCK

    def press_button(
        self,
        button_id: int,
        *,
        edge: Edge = "pulse",
        client_command_id: str | None = None,
    ) -> InputEvent:
        return button_to_event(
            button_id,
            source=self.source,
            edge=edge,
            client_command_id=client_command_id,
        )

    def handle_raw(self, raw: dict) -> list[InputEvent]:
        button_id = int(raw["button_id"])
        edge: Edge = raw.get("edge", "pulse")
        return [
            self.press_button(
                button_id,
                edge=edge,
                client_command_id=raw.get("client_command_id"),
            )
        ]


@dataclass
class KeyboardInputAdapter:
    """Maps browser KeyboardEvent.code payloads to pad / encoder actions."""

    source: InputSource = InputSource.KEYBOARD
    code_map: dict[str, tuple[InputAction, Edge]] = field(
        default_factory=lambda: dict(KEYBOARD_CODE_MAP)
    )

    def handle_raw(self, raw: dict) -> list[InputEvent]:
        code = str(raw.get("code", ""))
        event_type = str(raw.get("type", "keydown"))  # keydown | keyup
        repeat = bool(raw.get("repeat", False))
        client_command_id = raw.get("client_command_id")

        if event_type == "keyup" and code in HOLD_RELEASE_CODES:
            return [
                InputEvent(
                    action=HOLD_RELEASE_CODES[code],
                    source=self.source,
                    edge="release",
                    client_command_id=client_command_id,
                )
            ]

        if event_type != "keydown":
            return []

        mapped = self.code_map.get(code)
        if mapped is None:
            return []
        action, edge = mapped
        if edge == "press" and repeat:
            return []
        if edge == "pulse" and repeat:
            return []

        pad_slot = DIGIT_PAD_SLOT.get(code)
        preset_id = "NONE" if action is InputAction.SELECT_PRESET and code == "Digit0" else None
        return [
            InputEvent(
                action=action,
                source=self.source,
                pad_slot=pad_slot,
                preset_id=preset_id,
                edge=edge,
                client_command_id=client_command_id,
            )
        ]


@dataclass
class GpioInputAdapterStub:
    """Prepared boundary for a future Raspberry GPIO adapter.

    HOME PLAN does not load RPi.GPIO / gpiozero. This stub documents the
    interface and always returns no events.
    """

    source: InputSource = InputSource.GPIO
    implemented: bool = False
    notes: str = "GPIO adapter is out of HOME scope. Wire pin→button_id mapping here later."

    def handle_raw(self, raw: dict) -> list[InputEvent]:
        return []

    def poll(self) -> list[InputEvent]:
        return []
