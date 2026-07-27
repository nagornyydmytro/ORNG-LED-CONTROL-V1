"""DMX output adapters: Mock (default) and Art-Net."""

from orng_led.output.artnet import (
    ART_DMX_HEADER_SIZE,
    OP_ART_DMX,
    UDP_PORT_DEFAULT,
    build_artdmx_packet,
    next_sequence,
    parse_artdmx_header,
)
from orng_led.output.contract import OutputError, TransportKind
from orng_led.output.controller import OutputController, OutputStatus, create_output_controller
from orng_led.output.mock import MockTransport
from orng_led.output.safety import FailsafeReason, release_held_controls
from orng_led.output.udp import RecordingSocket, UdpArtNetTransport

__all__ = [
    "ART_DMX_HEADER_SIZE",
    "OP_ART_DMX",
    "UDP_PORT_DEFAULT",
    "FailsafeReason",
    "MockTransport",
    "OutputController",
    "OutputError",
    "OutputStatus",
    "RecordingSocket",
    "TransportKind",
    "UdpArtNetTransport",
    "build_artdmx_packet",
    "create_output_controller",
    "next_sequence",
    "parse_artdmx_header",
    "release_held_controls",
]
