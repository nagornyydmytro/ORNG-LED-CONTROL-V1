"""Output controller: Mock-by-default, explicit Art-Net arm, shutdown zeros."""

from __future__ import annotations

from dataclasses import dataclass, field

from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.engine.engine import Engine
from orng_led.engine.frame import empty_frame
from orng_led.output.contract import OutputError, OutputTransport, TransportKind
from orng_led.output.mock import MockTransport
from orng_led.output.safety import FailsafeReason, release_held_controls
from orng_led.output.udp import DatagramSocket, UdpArtNetTransport

ZERO_FRAME_SHUTDOWN_COUNT = 3


@dataclass(frozen=True)
class OutputStatus:
    transport: TransportKind
    armed: bool
    last_error: str | None
    frames_sent: int
    network_allowed: bool
    target_ip: str | None
    universe: int


@dataclass
class OutputController:
    """Owns the active transport and enforces HOME output safety."""

    engine: Engine
    transport: OutputTransport = field(default_factory=MockTransport)
    armed: bool = False
    allow_real_network: bool = False
    target_ip: str | None = None
    universe: int = 0
    udp_port: int = 6454
    frames_sent: int = 0
    last_error: str | None = None
    _injected_socket: DatagramSocket | None = None

    def __post_init__(self) -> None:
        app = self.engine.show.app
        self.universe = app.artnet.universe
        self.udp_port = app.artnet.udp_port
        self.target_ip = app.artnet.target_ip
        # Canon: runtime always starts on Mock with output disarmed, regardless of
        # any provisional artnet fields in YAML.
        self.transport = MockTransport()
        self.armed = False
        self.allow_real_network = False

    @property
    def transport_kind(self) -> TransportKind:
        return self.transport.kind

    def status(self) -> OutputStatus:
        return OutputStatus(
            transport=self.transport_kind,
            armed=self.armed,
            last_error=self.last_error,
            frames_sent=self.frames_sent,
            network_allowed=self.allow_real_network,
            target_ip=self.target_ip,
            universe=self.universe,
        )

    def use_mock(self) -> None:
        self._close_transport()
        self.transport = MockTransport()
        self.armed = False
        self.last_error = None

    def configure_artnet(
        self,
        *,
        target_ip: str,
        universe: int = 0,
        udp_port: int = 6454,
        injected_socket: DatagramSocket | None = None,
    ) -> None:
        """Prepare Art-Net settings without arming or sending."""
        if not target_ip:
            raise OutputError("Art-Net target_ip is required")
        self.target_ip = target_ip
        self.universe = universe
        self.udp_port = udp_port
        self._injected_socket = injected_socket

    def switch_to_artnet(self, *, explicit: bool = False) -> None:
        """Switch active transport to Art-Net. Requires an explicit operator action."""
        if not explicit:
            raise OutputError("Art-Net transport cannot be enabled implicitly")
        if not self.target_ip:
            raise OutputError("Configure Art-Net target_ip before switching transport")
        self._close_transport()
        self.transport = UdpArtNetTransport(
            target_ip=self.target_ip,
            universe=self.universe,
            udp_port=self.udp_port,
            allow_real_network=self.allow_real_network,
            socket=self._injected_socket,
        )
        self.armed = False
        self.last_error = None

    def arm(self, *, explicit: bool = False) -> None:
        if not explicit:
            raise OutputError("Output arm requires an explicit action")
        if self.transport.kind is not TransportKind.ARTNET:
            raise OutputError("Only Art-Net transport can be armed for real output")
        if not self.target_ip:
            raise OutputError("Cannot arm Art-Net without target_ip")
        self.armed = True
        self.last_error = None

    def disarm(self) -> None:
        self.armed = False

    def publish(self, frame: list[int]) -> None:
        """Send a frame according to safety rules."""
        if len(frame) != DMX_UNIVERSE_SIZE:
            raise OutputError(f"Frame length must be {DMX_UNIVERSE_SIZE}")

        if self.transport.kind is TransportKind.ARTNET and not self.armed:
            raise OutputError("Art-Net output is not armed; refusing to send")

        try:
            self.transport.send_frame(frame)
            self.frames_sent += 1
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - capture as output fault
            self.last_error = str(exc)
            self.armed = False
            raise OutputError(f"Output send failed: {exc}") from exc

    def publish_engine_tick(self) -> list[int]:
        snapshot = self.engine.tick()
        frame = snapshot.frame
        if self.transport.kind is TransportKind.MOCK:
            self.publish(frame)
        elif self.armed:
            self.publish(frame)
        return frame

    def on_disconnect(self) -> dict[str, object]:
        return release_held_controls(self.engine, FailsafeReason.DISCONNECT)

    def on_focus_loss(self) -> dict[str, object]:
        return release_held_controls(self.engine, FailsafeReason.FOCUS_LOSS)

    def on_visibility_hidden(self) -> dict[str, object]:
        return release_held_controls(self.engine, FailsafeReason.VISIBILITY_HIDDEN)

    def shutdown(self, *, zero_count: int = ZERO_FRAME_SHUTDOWN_COUNT) -> list[list[int]]:
        """Release holds, emit zero frames, disarm, and return to Mock."""
        release_held_controls(self.engine, FailsafeReason.SHUTDOWN)
        self.engine.set_blackout(True)
        zeros = empty_frame()
        emitted: list[list[int]] = []

        for _ in range(max(1, zero_count)):
            if self.transport.kind is TransportKind.ARTNET and self.armed:
                try:
                    self.transport.send_frame(zeros)
                    self.frames_sent += 1
                except Exception as exc:  # noqa: BLE001
                    self.last_error = str(exc)
            elif self.transport.kind is TransportKind.MOCK:
                self.transport.send_frame(zeros)
                self.frames_sent += 1
            emitted.append(list(zeros))

        self.disarm()
        self.use_mock()
        return emitted

    def _close_transport(self) -> None:
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()


def create_output_controller(engine: Engine | None = None) -> OutputController:
    from orng_led.engine import create_engine

    return OutputController(engine=engine or create_engine())
