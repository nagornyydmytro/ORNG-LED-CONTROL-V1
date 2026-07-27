"""UDP Art-Net transport — real sockets only when explicitly allowed."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Protocol

from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.output.artnet import UDP_PORT_DEFAULT, build_artdmx_packet, next_sequence
from orng_led.output.contract import OutputError, TransportKind


class DatagramSocket(Protocol):
    def sendto(self, data: bytes, address: tuple[str, int]) -> int: ...

    def close(self) -> None: ...


@dataclass
class RecordingSocket:
    """Test double that never opens a real network socket."""

    sent: list[tuple[bytes, tuple[str, int]]] = field(default_factory=list)
    closed: bool = False

    def sendto(self, data: bytes, address: tuple[str, int]) -> int:
        if self.closed:
            raise OSError("RecordingSocket closed")
        self.sent.append((data, address))
        return len(data)

    def close(self) -> None:
        self.closed = True


@dataclass
class UdpArtNetTransport:
    """Art-Net UDP adapter.

    HOME default: ``allow_real_network=False``. Without an injected socket the
    transport refuses to create a real UDP socket, so unit tests cannot
    accidentally send packets to the LAN.
    """

    target_ip: str
    universe: int = 0
    udp_port: int = UDP_PORT_DEFAULT
    allow_real_network: bool = False
    socket: DatagramSocket | None = None
    sequence: int = 0
    packets_sent: int = 0
    last_packet: bytes | None = None
    _owns_socket: bool = False
    _closed: bool = False
    _last_frame: list[int] | None = None

    @property
    def kind(self) -> TransportKind:
        return TransportKind.ARTNET

    @property
    def last_frame(self) -> list[int] | None:
        return self._last_frame

    def _ensure_socket(self) -> DatagramSocket:
        if self.socket is not None:
            return self.socket
        if not self.allow_real_network:
            raise OutputError(
                "Real UDP Art-Net send is disabled. "
                "Inject a socket for tests or set allow_real_network=True explicitly."
            )
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket = sock
        self._owns_socket = True
        return sock

    def send_frame(self, frame: list[int]) -> None:
        if self._closed:
            raise OutputError("UdpArtNetTransport is closed")
        if len(frame) != DMX_UNIVERSE_SIZE:
            raise OutputError(f"Frame length must be {DMX_UNIVERSE_SIZE}")
        self.sequence = next_sequence(self.sequence)
        packet = build_artdmx_packet(
            frame,
            sequence=self.sequence,
            universe=self.universe,
        )
        sock = self._ensure_socket()
        sock.sendto(packet, (self.target_ip, self.udp_port))
        self.packets_sent += 1
        self.last_packet = packet
        self._last_frame = list(frame)

    def close(self) -> None:
        self._closed = True
        if self.socket is not None and self._owns_socket:
            self.socket.close()
        self.socket = None
        self._owns_socket = False
