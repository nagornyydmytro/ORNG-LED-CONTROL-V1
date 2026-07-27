"""Art-Net ArtDmx packet serializer (pure bytes, no network I/O)."""

from __future__ import annotations

from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.output.contract import OutputError

ARTNET_ID = b"Art-Net\x00"
OP_ART_DMX = 0x5000
PROTOCOL_VERSION = 14
ART_DMX_HEADER_SIZE = 18
UDP_PORT_DEFAULT = 6454


def _clamp_universe(universe: int) -> int:
    if universe < 0 or universe > 0x7FFF:
        raise OutputError(f"Art-Net universe out of range: {universe}")
    return universe


def next_sequence(current: int) -> int:
    """Art-Net sequence: 1..255, skipping 0 (0 means sequence disabled)."""
    nxt = (current % 255) + 1
    return nxt


def build_artdmx_packet(
    frame: list[int] | bytes,
    *,
    sequence: int = 1,
    physical: int = 0,
    universe: int = 0,
) -> bytes:
    """Serialize a 512-slot DMX frame into an ArtDmx UDP payload."""
    if isinstance(frame, list):
        if len(frame) != DMX_UNIVERSE_SIZE:
            raise OutputError(f"ArtDmx requires {DMX_UNIVERSE_SIZE} channels, got {len(frame)}")
        if any((not isinstance(v, int)) or v < 0 or v > 255 for v in frame):
            raise OutputError("ArtDmx frame values must be integers in 0..255")
        data = bytes(frame)
    else:
        if len(frame) != DMX_UNIVERSE_SIZE:
            raise OutputError(f"ArtDmx requires {DMX_UNIVERSE_SIZE} channels, got {len(frame)}")
        data = bytes(frame)

    if sequence < 0 or sequence > 255:
        raise OutputError(f"Invalid ArtDmx sequence: {sequence}")
    if physical < 0 or physical > 255:
        raise OutputError(f"Invalid ArtDmx physical: {physical}")

    universe = _clamp_universe(universe)
    length = DMX_UNIVERSE_SIZE

    packet = bytearray(ART_DMX_HEADER_SIZE + length)
    packet[0:8] = ARTNET_ID
    packet[8] = OP_ART_DMX & 0xFF  # little-endian opcode
    packet[9] = (OP_ART_DMX >> 8) & 0xFF
    packet[10] = (PROTOCOL_VERSION >> 8) & 0xFF
    packet[11] = PROTOCOL_VERSION & 0xFF
    packet[12] = sequence & 0xFF
    packet[13] = physical & 0xFF
    packet[14] = universe & 0xFF  # little-endian universe
    packet[15] = (universe >> 8) & 0xFF
    packet[16] = (length >> 8) & 0xFF  # big-endian length
    packet[17] = length & 0xFF
    packet[18:] = data
    return bytes(packet)


def parse_artdmx_header(packet: bytes) -> dict[str, int]:
    """Inspect an ArtDmx packet header (for tests / diagnostics)."""
    if len(packet) < ART_DMX_HEADER_SIZE:
        raise OutputError("Packet too short for ArtDmx header")
    if packet[0:8] != ARTNET_ID:
        raise OutputError("Not an Art-Net packet")
    opcode = packet[8] | (packet[9] << 8)
    if opcode != OP_ART_DMX:
        raise OutputError(f"Unexpected opcode {opcode:#06x}")
    length = (packet[16] << 8) | packet[17]
    return {
        "opcode": opcode,
        "protocol_version": (packet[10] << 8) | packet[11],
        "sequence": packet[12],
        "physical": packet[13],
        "universe": packet[14] | (packet[15] << 8),
        "length": length,
        "payload_size": len(packet) - ART_DMX_HEADER_SIZE,
    }
