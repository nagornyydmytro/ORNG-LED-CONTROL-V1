"""Unit tests for config models, patch validation and YAML I/O."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from orng_led.config import (
    SCHEMA_VERSION,
    ConfigError,
    default_config_dir,
    global_channel,
    load_show_config,
    save_model,
    validate_patch,
)
from orng_led.config.io import atomic_write_yaml, load_app_config, parse_model
from orng_led.config.models import AppConfig, FixtureProfile, PatchDocument


def test_provisional_show_config_loads() -> None:
    show = load_show_config()
    # Preferred YAML transport may be mock (HOME default) or artnet (venue day).
    # Runtime still starts Mock regardless — covered by output/H001 tests.
    assert show.app.transport.value in {"mock", "artnet"}
    assert show.app.output_armed is False
    assert len(show.patch.fixtures) == 12
    assert len(show.profiles) == 4
    assert all(not profile.hardware_verified for profile in show.profiles.values())
    assert set(show.layout.fixtures) == {fx.id for fx in show.patch.fixtures}


def test_global_channel_formula() -> None:
    assert global_channel(1, 1) == 1
    assert global_channel(2, 1) == 2
    assert global_channel(2, 2) == 3
    assert global_channel(29, 1) == 29
    assert global_channel(164, 7) == 170


def test_provisional_patch_has_no_overlaps() -> None:
    show = load_show_config()
    validate_patch(show.patch, show.profiles)
    occupied: dict[int, str] = {}
    for fixture in show.patch.fixtures:
        profile = show.profiles[fixture.profile_id]
        for local in range(1, profile.footprint + 1):
            channel = global_channel(fixture.start_address, local)
            assert channel not in occupied
            occupied[channel] = fixture.id
    assert max(occupied) == 170
    assert min(occupied) == 1


def test_yaml_round_trip_app_config(tmp_path: Path) -> None:
    source = default_config_dir() / "app.yaml"
    app = load_app_config(source)
    target = tmp_path / "app-roundtrip.yaml"
    save_model(target, app)
    reloaded = load_app_config(target)
    assert reloaded.model_dump() == app.model_dump()


def test_yaml_round_trip_profile(tmp_path: Path) -> None:
    show = load_show_config()
    profile = show.profiles["par_7ch_provisional"]
    target = tmp_path / "par.yaml"
    save_model(target, profile)
    text = target.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    restored = parse_model(FixtureProfile, data, source=target)
    assert restored.model_dump() == profile.model_dump()


def test_overlap_is_rejected() -> None:
    show = load_show_config()
    fixtures = copy.deepcopy(show.patch.fixtures)
    fixtures[1].start_address = fixtures[0].start_address
    bad_patch = PatchDocument(schema_version=SCHEMA_VERSION, fixtures=fixtures)
    with pytest.raises(ConfigError, match="overlap"):
        validate_patch(bad_patch, show.profiles)


def test_out_of_range_is_rejected() -> None:
    show = load_show_config()
    fixtures = copy.deepcopy(show.patch.fixtures)
    fixtures[0].start_address = 510
    bad_patch = PatchDocument(schema_version=SCHEMA_VERSION, fixtures=fixtures)
    with pytest.raises(ConfigError, match="outside"):
        validate_patch(bad_patch, show.profiles)


def test_duplicate_fixture_id_is_rejected() -> None:
    show = load_show_config()
    fixtures = copy.deepcopy(show.patch.fixtures)
    fixtures[1].id = fixtures[0].id
    bad_patch = PatchDocument(schema_version=SCHEMA_VERSION, fixtures=fixtures)
    with pytest.raises(ConfigError, match="Duplicate fixture id"):
        validate_patch(bad_patch, show.profiles)


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad-app.yaml"
    atomic_write_yaml(
        path,
        {
            "schema_version": 999,
            "transport": "mock",
            "output_armed": False,
        },
    )
    with pytest.raises(ConfigError, match="Unsupported schema_version"):
        load_app_config(path)


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "extra.yaml"
    atomic_write_yaml(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "transport": "mock",
            "output_armed": False,
            "unexpected": True,
        },
    )
    with pytest.raises(ConfigError, match="Validation failed"):
        load_app_config(path)


def test_atomic_write_preserves_valid_file_on_invalid_followup(tmp_path: Path) -> None:
    path = tmp_path / "stable.yaml"
    app = AppConfig(schema_version=SCHEMA_VERSION)
    save_model(path, app)
    original = path.read_text(encoding="utf-8")

    with pytest.raises(ConfigError):
        parse_model(
            AppConfig,
            {"schema_version": SCHEMA_VERSION, "transport": "not-a-mode"},
            source="memory",
        )

    # Invalid parse must not rewrite the existing file.
    assert path.read_text(encoding="utf-8") == original


def test_utf8_path_and_cyrillic_content_smoke(tmp_path: Path) -> None:
    folder = tmp_path / "конфіг"
    folder.mkdir()
    path = folder / "налаштування.yaml"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "transport": "mock",
        "output_armed": False,
        "master_brightness": 0.5,
    }
    atomic_write_yaml(path, payload)
    loaded = load_app_config(path)
    assert loaded.master_brightness == 0.5
    assert "конфіг" in str(path)
    assert path.read_text(encoding="utf-8")


def test_invalid_edit_does_not_replace_last_good_yaml(tmp_path: Path) -> None:
    path = tmp_path / "patch.yaml"
    show = load_show_config()
    save_model(path, show.patch)
    good = path.read_text(encoding="utf-8")

    # Simulate a bad write attempt: write temp then refuse replace by validating first.
    bad = {"schema_version": SCHEMA_VERSION, "universe": 0, "fixtures": "nope"}
    with pytest.raises(ConfigError):
        parse_model(PatchDocument, bad, source="bad-edit")
    assert path.read_text(encoding="utf-8") == good
