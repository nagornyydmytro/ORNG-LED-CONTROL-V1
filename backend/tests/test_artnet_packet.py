"""Art-Net ArtDmx serializer golden tests (no network)."""

from __future__ import annotations

from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.output import (
    ART_DMX_HEADER_SIZE,
    OP_ART_DMX,
    build_artdmx_packet,
    next_sequence,
    parse_artdmx_header,
)


def test_artdmx_packet_golden_layout() -> None:
    frame = [0] * DMX_UNIVERSE_SIZE
    frame[0] = 10
    frame[511] = 200
    packet = build_artdmx_packet(frame, sequence=7, physical=0, universe=0)

    assert packet[0:8] == b"Art-Net\x00"
    assert packet[8] | (packet[9] << 8) == OP_ART_DMX
    assert packet[10] == 0
    assert packet[11] == 14
    assert packet[12] == 7
    assert packet[13] == 0
    assert packet[14] == 0
    assert packet[15] == 0
    # Length is big-endian 512
    assert packet[16] == 0x02
    assert packet[17] == 0x00
    assert len(packet) == ART_DMX_HEADER_SIZE + DMX_UNIVERSE_SIZE
    assert packet[18] == 10
    assert packet[18 + 511] == 200

    header = parse_artdmx_header(packet)
    assert header["length"] == 512
    assert header["payload_size"] == 512
    assert header["sequence"] == 7
    assert header["universe"] == 0


def test_artdmx_universe_little_endian() -> None:
    frame = [0] * DMX_UNIVERSE_SIZE
    packet = build_artdmx_packet(frame, sequence=1, universe=0x0104)
    assert packet[14] == 0x04
    assert packet[15] == 0x01
    assert parse_artdmx_header(packet)["universe"] == 0x0104


def test_sequence_skips_zero() -> None:
    assert next_sequence(0) == 1
    assert next_sequence(1) == 2
    assert next_sequence(254) == 255
    assert next_sequence(255) == 1
