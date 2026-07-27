"""DMX address helpers and patch validation."""

from __future__ import annotations

from collections.abc import Mapping

from orng_led.config.models import ConfigError, FixtureInstance, FixtureProfile, PatchDocument
from orng_led.config.schema import DMX_CHANNEL_MAX, DMX_CHANNEL_MIN


def global_channel(start_address: int, local_channel: int) -> int:
    """Map fixture-local channel to universe channel.

    Rule (canon §4): global = start_address + local_channel - 1
    """
    if local_channel < 1:
        raise ConfigError(f"local_channel must be >= 1 (got {local_channel}).")
    channel = start_address + local_channel - 1
    if channel < DMX_CHANNEL_MIN or channel > DMX_CHANNEL_MAX:
        raise ConfigError(
            f"Global channel {channel} is outside {DMX_CHANNEL_MIN}..{DMX_CHANNEL_MAX} "
            f"(start_address={start_address}, local_channel={local_channel})."
        )
    return channel


def footprint_range(start_address: int, footprint: int) -> tuple[int, int]:
    if footprint < 1:
        raise ConfigError(f"footprint must be >= 1 (got {footprint}).")
    first = start_address
    last = start_address + footprint - 1
    if first < DMX_CHANNEL_MIN or last > DMX_CHANNEL_MAX:
        raise ConfigError(
            f"Address range {first}..{last} is outside {DMX_CHANNEL_MIN}..{DMX_CHANNEL_MAX}."
        )
    return first, last


def collect_patch_errors(
    patch: PatchDocument,
    profiles: Mapping[str, FixtureProfile],
) -> list[str]:
    """Return patch validation errors without raising."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    occupied: dict[int, str] = {}

    if not patch.fixtures:
        errors.append("Patch contains no fixtures.")

    for fixture in patch.fixtures:
        if fixture.id in seen_ids:
            errors.append(f"Duplicate fixture id {fixture.id!r}.")
        seen_ids.add(fixture.id)

        profile = profiles.get(fixture.profile_id)
        if profile is None:
            errors.append(f"Fixture {fixture.id!r}: unknown profile_id {fixture.profile_id!r}.")
            continue

        if fixture.kind != profile.kind:
            errors.append(
                f"Fixture {fixture.id!r}: kind {fixture.kind.value!r} does not match "
                f"profile kind {profile.kind.value!r}."
            )

        try:
            first, last = footprint_range(fixture.start_address, profile.footprint)
        except ConfigError as exc:
            errors.append(f"Fixture {fixture.id!r}: {exc}")
            continue

        for channel in range(first, last + 1):
            owner = occupied.get(channel)
            if owner is not None:
                errors.append(f"Channel overlap at {channel}: {owner!r} and {fixture.id!r}.")
            else:
                occupied[channel] = fixture.id

    return errors


def validate_patch(
    patch: PatchDocument,
    profiles: Mapping[str, FixtureProfile],
) -> None:
    """Validate IDs, profile refs, footprints, ranges and overlaps."""
    errors = collect_patch_errors(patch, profiles)
    if errors:
        raise ConfigError("Patch validation failed:\n- " + "\n- ".join(errors))


def occupied_channels(
    fixtures: list[FixtureInstance],
    profiles: Mapping[str, FixtureProfile],
) -> dict[int, str]:
    validate_patch(PatchDocument(schema_version=1, fixtures=fixtures), profiles)
    result: dict[int, str] = {}
    for fixture in fixtures:
        profile = profiles[fixture.profile_id]
        first, last = footprint_range(fixture.start_address, profile.footprint)
        for channel in range(first, last + 1):
            result[channel] = fixture.id
    return result
