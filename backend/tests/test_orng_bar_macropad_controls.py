"""Regression: ORNG BAR controls match captured CH57x macropad HID map."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "config" / "scenes" / "orng-bar"
CONTROLS = SCENE / "controls.yaml"
OBSERVED = SCENE / "macropad-observed.json"

# Requested action label → expected HID name from laptop capture.
EXPECTED_HID = {
    "Q": "r",
    "W": "i",
    "E": "c",
    "R": "e",
    "T": "u",
    "Y": "space",
    "U": "w",
    "I": "y",
    "O": "p",
    "Z": "q",
    "X": "t",
    "C": "o",
    "Space": "x",
    "Volume-": "volume_down",
    "Volume+": "volume_up",
    "Zoom-": "minus",
    "Zoom+": "equal",
    # Speed-encoder click (consumer Mute) → Face — not Escape/KeyZ.
    "Mute": "mute",
}

HID_TO_CODE = {
    "r": "KeyR",
    "i": "KeyI",
    "c": "KeyC",
    "e": "KeyE",
    "u": "KeyU",
    "space": "Space",
    "w": "KeyW",
    "y": "KeyY",
    "p": "KeyP",
    "q": "KeyQ",
    "t": "KeyT",
    "o": "KeyO",
    "x": "KeyX",
    "volume_down": "AudioVolumeDown",
    "volume_up": "AudioVolumeUp",
    "mute": "AudioVolumeMute",
    "minus": "Minus",
    "equal": "Equal",
}

# ORNG action for each requested label (scene semantics).
ACTION_FOR = {
    "Q": ("select_pad_slot", 0),
    "W": ("select_pad_slot", 1),
    "E": ("select_pad_slot", 2),
    "R": ("select_pad_slot", 3),
    "T": ("select_pad_slot", 4),
    "Y": ("select_pad_slot", 5),
    "U": ("select_pad_slot", 6),
    "I": ("select_pad_slot", 7),
    "O": ("select_pad_slot", 8),
    "Z": ("strobe_press", None),
    "X": ("sweep_press", None),
    "C": ("vertical_sweep_press", None),
    "Space": ("blackout_toggle", None),
    "Volume-": ("program_speed_down", None),
    "Volume+": ("program_speed_up", None),
    "Mute": ("face_toggle", None),
    "Zoom-": ("zoom_down", None),
    "Zoom+": ("zoom_up", None),
}


def test_observed_capture_file_matches_constants() -> None:
    data = json.loads(OBSERVED.read_text(encoding="utf-8"))
    by_action = {row["action"]: row for row in data["rows"]}
    for label, hid in EXPECTED_HID.items():
        if label == "Mute":
            # Mute/face click was not in the original laptop capture rows.
            continue
        assert label in by_action, label
        assert by_action[label]["hid"] == hid, (label, by_action[label]["hid"], hid)


def test_controls_yaml_binds_observed_hid_codes() -> None:
    doc = yaml.safe_load(CONTROLS.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    rows = doc["keyboard"]
    by_code: dict[str, list[dict]] = {}
    for row in rows:
        by_code.setdefault(row["code"], []).append(row)

    for label, hid in EXPECTED_HID.items():
        code = HID_TO_CODE[hid]
        assert code in by_code, (label, code)
        action, slot = ACTION_FOR[label]
        matches = [r for r in by_code[code] if r["action"] == action]
        assert matches, (label, code, action, by_code[code])
        if slot is not None:
            assert any(r.get("pad_slot") == slot for r in matches), (label, matches)
        if label == "C":
            rel = [r for r in by_code[code] if r["action"] == "vertical_sweep_release"]
            assert rel and rel[0].get("edge") == "release", rel
