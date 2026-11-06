"""Mock / keyboard adapters and future GPIO boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from orng_led.input.contract import Edge, InputAction, InputEvent, InputSource
from orng_led.input.mapping import (
    HOLD_RELEASE_CODES,
    KEYBOARD_CODE_MAP,
    PAD_KEY_SLOT,
    button_to_event,
)

if TYPE_CHECKING:
    from orng_led.scenes.models import SceneControls


PROFILE_QWERTY = "qwerty"
PROFILE_CH57X = "ch57x"
CH57X_VID = "1189"
CH57X_PID = "8890"


def _id_forms(value: str) -> set[str]:
    text = value.strip().lower().removeprefix("0x")
    forms = {text}
    if text.isdigit():
        forms.add(f"{int(text, 10):04x}")
    try:
        forms.add(f"{int(text, 16):04x}")
    except ValueError:
        pass
    return forms


def _ids_match(left: str, right: str) -> bool:
    return bool(_id_forms(left) & _id_forms(right))


def _maps_from_bindings(
    bindings: list[Any],
) -> tuple[
    dict[str, tuple[InputAction, Edge]],
    dict[str, InputAction],
    dict[str, int],
    dict[str, str],
]:
    code_map: dict[str, tuple[InputAction, Edge]] = {}
    release_map: dict[str, InputAction] = {}
    pad_slots: dict[str, int] = {}
    preset_ids: dict[str, str] = {}
    for binding in bindings:
        if binding.edge == "release":
            release_map[binding.code] = binding.action
        else:
            code_map[binding.code] = (binding.action, binding.edge)
        if binding.pad_slot is not None:
            pad_slots[binding.code] = int(binding.pad_slot)
        if binding.preset_id is not None:
            preset_ids[binding.code] = str(binding.preset_id)
    return code_map, release_map, pad_slots, preset_ids


def adapter_from_bindings(bindings: list[Any]) -> KeyboardInputAdapter:
    code_map, release_map, pad_slots, preset_ids = _maps_from_bindings(bindings)
    return KeyboardInputAdapter(
        code_map=code_map or dict(KEYBOARD_CODE_MAP),
        release_map=release_map or dict(HOLD_RELEASE_CODES),
        pad_slots=pad_slots or dict(PAD_KEY_SLOT),
        preset_ids=preset_ids or {"KeyP": "NONE"},
    )


def qwerty_adapter() -> KeyboardInputAdapter:
    return KeyboardInputAdapter()


def known_profile_ids(controls: SceneControls | None) -> set[str]:
    ids = {PROFILE_QWERTY, PROFILE_CH57X}
    profiles = getattr(controls, "profiles", None) or {}
    if isinstance(profiles, dict):
        ids.update(str(key) for key in profiles)
    return ids


def adapter_for_profile(controls: SceneControls | None, profile_id: str) -> KeyboardInputAdapter:
    """Select a scene profile. Unknown ids raise; qwerty falls back to the built-in map."""
    if profile_id not in known_profile_ids(controls):
        raise ValueError(f"unknown_profile:{profile_id}")
    profiles = getattr(controls, "profiles", None) or {}
    profile = profiles.get(profile_id) if isinstance(profiles, dict) else None
    if profile is not None and getattr(profile, "keyboard", None):
        adapter = adapter_from_bindings(list(profile.keyboard))
    elif profile_id == PROFILE_QWERTY:
        adapter = qwerty_adapter()
    else:
        adapter = KeyboardInputAdapter.from_controls(controls)
    return adapter


def adapter_for_hid(
    controls: SceneControls | None,
    vendor: str | None,
    product: str | None,
) -> KeyboardInputAdapter:
    """Pi local keyboard: match USB identity to a scene profile. Otherwise QWERTY."""
    vid = (vendor or "").strip()
    pid = (product or "").strip()
    profiles = getattr(controls, "profiles", None) or {}
    if vid and pid and isinstance(profiles, dict):
        for profile in profiles.values():
            for match in getattr(profile, "match", ()) or ():
                if _ids_match(match.vid, vid) and _ids_match(match.pid, pid):
                    if profile.keyboard:
                        return adapter_from_bindings(list(profile.keyboard))
    if vid and pid and _ids_match(vid, CH57X_VID) and _ids_match(pid, CH57X_PID):
        return adapter_for_profile(controls, PROFILE_CH57X)
    return adapter_for_profile(controls, PROFILE_QWERTY)


def controls_to_code_map(
    controls: SceneControls | None,
) -> dict[str, tuple[InputAction, Edge]]:
    """Build press/pulse code map from scene controls (fallback: KEYBOARD_CODE_MAP)."""
    if controls is None or not getattr(controls, "keyboard", None):
        return dict(KEYBOARD_CODE_MAP)
    mapping: dict[str, tuple[InputAction, Edge]] = {}
    for binding in controls.keyboard:
        if binding.edge == "release":
            continue
        mapping[binding.code] = (binding.action, binding.edge)
    return mapping or dict(KEYBOARD_CODE_MAP)


def controls_to_release_map(
    controls: SceneControls | None,
) -> dict[str, InputAction]:
    if controls is None or not getattr(controls, "keyboard", None):
        return dict(HOLD_RELEASE_CODES)
    mapping: dict[str, InputAction] = {}
    for binding in controls.keyboard:
        if binding.edge != "release":
            continue
        mapping[binding.code] = binding.action
    return mapping or dict(HOLD_RELEASE_CODES)


def controls_to_pad_slots(controls: SceneControls | None) -> dict[str, int]:
    if controls is None or not getattr(controls, "keyboard", None):
        return dict(PAD_KEY_SLOT)
    mapping: dict[str, int] = {}
    for binding in controls.keyboard:
        if binding.pad_slot is not None:
            mapping[binding.code] = int(binding.pad_slot)
    return mapping or dict(PAD_KEY_SLOT)


def controls_to_preset_ids(controls: SceneControls | None) -> dict[str, str]:
    if controls is None or not getattr(controls, "keyboard", None):
        return {"KeyP": "NONE"}
    mapping: dict[str, str] = {}
    for binding in controls.keyboard:
        if binding.preset_id is not None:
            mapping[binding.code] = str(binding.preset_id)
    return mapping


def keyboard_dict_from_controls(controls: SceneControls | None) -> dict[str, dict[str, str]]:
    """API-facing keyboard map from active scene controls."""
    code_map = controls_to_code_map(controls)
    return {
        code: {"action": action.value, "edge": edge} for code, (action, edge) in code_map.items()
    }


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
        source_id: str | None = None,
    ) -> InputEvent:
        event = button_to_event(
            button_id,
            source=self.source,
            edge=edge,
            client_command_id=client_command_id,
        )
        if source_id is None:
            return event
        return InputEvent(
            action=event.action,
            source=event.source,
            button_id=event.button_id,
            preset_id=event.preset_id,
            pad_slot=event.pad_slot,
            edge=event.edge,
            client_command_id=event.client_command_id,
            source_id=source_id,
        )

    def handle_raw(self, raw: dict) -> list[InputEvent]:
        button_id = int(raw["button_id"])
        edge: Edge = raw.get("edge", "pulse")
        return [
            self.press_button(
                button_id,
                edge=edge,
                client_command_id=raw.get("client_command_id"),
                source_id=raw.get("source_id"),
            )
        ]


@dataclass
class KeyboardInputAdapter:
    """Maps browser KeyboardEvent.code payloads to pad / encoder actions."""

    source: InputSource = InputSource.KEYBOARD
    code_map: dict[str, tuple[InputAction, Edge]] = field(
        default_factory=lambda: dict(KEYBOARD_CODE_MAP)
    )
    release_map: dict[str, InputAction] = field(default_factory=lambda: dict(HOLD_RELEASE_CODES))
    pad_slots: dict[str, int] = field(default_factory=lambda: dict(PAD_KEY_SLOT))
    preset_ids: dict[str, str] = field(default_factory=lambda: {"KeyP": "NONE"})

    @classmethod
    def from_controls(cls, controls: Any) -> KeyboardInputAdapter:
        return cls(
            code_map=controls_to_code_map(controls),
            release_map=controls_to_release_map(controls),
            pad_slots=controls_to_pad_slots(controls),
            preset_ids=controls_to_preset_ids(controls),
        )

    def handle_raw(self, raw: dict) -> list[InputEvent]:
        code = str(raw.get("code", ""))
        event_type = str(raw.get("type", "keydown"))  # keydown | keyup
        repeat = bool(raw.get("repeat", False))
        client_command_id = raw.get("client_command_id")
        source_id = raw.get("source_id")

        if event_type == "keyup" and code in self.release_map:
            return [
                InputEvent(
                    action=self.release_map[code],
                    source=self.source,
                    edge="release",
                    client_command_id=client_command_id,
                    source_id=source_id,
                )
            ]

        if event_type != "keydown":
            return []

        mapped = self.code_map.get(code)
        if mapped is None:
            return []
        action, edge = mapped
        # Hold keys: ignore OS auto-repeat while already down.
        if edge == "press" and repeat:
            return []
        # Encoder / media keys often arrive as repeat=true after the first tick —
        # still accept them so a spinning volume/zoom knob keeps nudging.
        encoder_actions = {
            InputAction.PROGRAM_SPEED_UP,
            InputAction.PROGRAM_SPEED_DOWN,
            InputAction.ZOOM_UP,
            InputAction.ZOOM_DOWN,
            InputAction.LIVE_FX_SPEED_UP,
            InputAction.LIVE_FX_SPEED_DOWN,
            InputAction.EPISODE_PREV,
            InputAction.EPISODE_NEXT,
        }
        if edge == "pulse" and repeat and action not in encoder_actions:
            return []

        pad_slot = self.pad_slots.get(code)
        preset_id = self.preset_ids.get(code)
        if preset_id is None and action is InputAction.SELECT_PRESET and code == "KeyP":
            preset_id = "NONE"
        return [
            InputEvent(
                action=action,
                source=self.source,
                pad_slot=pad_slot,
                preset_id=preset_id,
                edge=edge,
                client_command_id=client_command_id,
                source_id=source_id,
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


__all__ = [
    "CH57X_PID",
    "CH57X_VID",
    "GpioInputAdapterStub",
    "InputAdapter",
    "KeyboardInputAdapter",
    "MockInputAdapter",
    "PROFILE_CH57X",
    "PROFILE_QWERTY",
    "adapter_for_hid",
    "adapter_for_profile",
    "controls_to_code_map",
    "controls_to_pad_slots",
    "controls_to_preset_ids",
    "controls_to_release_map",
    "keyboard_dict_from_controls",
    "known_profile_ids",
]
