"""Mock / keyboard adapters and future GPIO boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from orng_led.input.contract import Edge, InputEvent, InputSource
from orng_led.input.mapping import (
    KEYBOARD_CODE_MAP,
    STROBE_RELEASE_CODES,
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
    """Maps browser KeyboardEvent.code payloads to the 16-button contract."""

    source: InputSource = InputSource.KEYBOARD
    code_map: dict[str, tuple[int, Edge]] = field(default_factory=lambda: dict(KEYBOARD_CODE_MAP))

    def handle_raw(self, raw: dict) -> list[InputEvent]:
        code = str(raw.get("code", ""))
        event_type = str(raw.get("type", "keydown"))  # keydown | keyup
        repeat = bool(raw.get("repeat", False))
        client_command_id = raw.get("client_command_id")

        if event_type == "keyup" and code in STROBE_RELEASE_CODES:
            return [
                button_to_event(
                    13,
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
        button_id, edge = mapped
        if edge == "press" and repeat:
            # Let dispatcher also ignore, but avoid emitting duplicate press events.
            return []
        if edge == "pulse" and repeat:
            return []
        return [
            button_to_event(
                button_id,
                source=self.source,
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
