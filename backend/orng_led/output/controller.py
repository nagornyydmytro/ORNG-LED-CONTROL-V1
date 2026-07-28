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
    udp_active: bool = False


@dataclass
class OutputController:
    """Owns the active transport and enforces HOME / H001 output safety."""

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

    @property
    def udp_active(self) -> bool:
        """True when the Art-Net UDP path is selected and able to send packets."""
        if self.transport.kind is not TransportKind.ARTNET:
            return False
        if getattr(self.transport, "_closed", False):
            return False
        if self.allow_real_network:
            return True
        # Tests inject a socket without opening a real network endpoint.
        return getattr(self.transport, "socket", None) is not None

    def status(self) -> OutputStatus:
        return OutputStatus(
            transport=self.transport_kind,
            armed=self.armed,
            last_error=self.last_error,
            frames_sent=self.frames_sent,
            network_allowed=self.allow_real_network,
            target_ip=self.target_ip,
            universe=self.universe,
            udp_active=self.udp_active,
        )

    def use_mock(self) -> None:
        self._close_transport()
        self.transport = MockTransport()
        self.armed = False
        self.allow_real_network = False
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

    def activation_blockers(
        self,
        *,
        published_frame: list[int],
        raw_tester_active: bool = False,
    ) -> list[str]:
        """Human-readable reasons why Art-Net must not be activated yet.

        A prepared Raw tester session is allowed: Blackout + wire policy keep
        the outbound frame at zeros. ``raw_tester_active`` is accepted for
        call-site compatibility and does not block activation.
        """
        del raw_tester_active  # prepared Raw source is allowed under Blackout
        blockers: list[str] = []
        if self.armed:
            blockers.append("Вивід уже armed — спочатку disarm / поверніть Mock")
        if not self.engine.overlays.blackout:
            blockers.append("Blackout має бути увімкнений")
        if any(int(value) for value in published_frame):
            blockers.append("Поточний кадр не нульовий (frame_sum > 0)")
        if not self.target_ip:
            blockers.append("Не задано Art-Net target_ip")
        return blockers

    def activate_artnet(
        self,
        *,
        explicit: bool = False,
        confirmed: bool = False,
        allow_real_udp: bool = False,
        zero_count: int = ZERO_FRAME_SHUTDOWN_COUNT,
    ) -> list[list[int]]:
        """Explicit confirmed switch to Art-Net, still disarmed, sending only zeros.

        ``allow_real_udp=True`` opens a real UDP socket. Tests must keep it
        ``False`` and inject a :class:`RecordingSocket` instead.
        """
        if not explicit:
            raise OutputError("Art-Net activation requires an explicit action")
        if not confirmed:
            raise OutputError("Art-Net activation requires an explicit confirmation")
        if not self.target_ip:
            raise OutputError("Configure Art-Net target_ip before activation")

        self.engine.set_blackout(True)
        self.armed = False
        if allow_real_udp:
            self.allow_real_network = True
        self.switch_to_artnet(explicit=True)
        try:
            emitted = self._emit_zero_frames(count=zero_count, raise_on_error=True)
        except Exception:
            self.allow_real_network = False
            self.use_mock()
            raise
        return emitted

    def deactivate_to_mock(self, *, zero_count: int = ZERO_FRAME_SHUTDOWN_COUNT) -> list[list[int]]:
        """Send zeros, close UDP, clear network permission, return to Mock."""
        self.engine.set_blackout(True)
        emitted = self._emit_zero_frames(count=zero_count, raise_on_error=False)
        self.armed = False
        self.allow_real_network = False
        self.use_mock()
        return emitted

    def arm_blockers(self, *, wire_frame: list[int]) -> list[str]:
        """Human-readable reasons why Arm must not be enabled yet."""
        blockers: list[str] = []
        if self.transport.kind is not TransportKind.ARTNET:
            blockers.append("Runtime має бути Art-Net (у Mock Arm неможливий)")
        if not self.udp_active:
            blockers.append("UDP / Art-Net неактивний")
        if not self.allow_real_network:
            blockers.append("artnet_network_enabled=false — спочатку безпечно активуйте Art-Net")
        if not self.engine.overlays.blackout:
            blockers.append("Blackout має бути увімкнений перед Arm")
        if any(int(value) for value in wire_frame):
            blockers.append("Фактичний wire-кадр не нульовий")
        if not self.target_ip:
            blockers.append("Не задано Art-Net target_ip")
        if self.armed:
            blockers.append("Вивід уже armed")
        return blockers

    def arm(
        self,
        *,
        explicit: bool = False,
        confirmed: bool = False,
        wire_frame: list[int] | None = None,
    ) -> None:
        """Arm Art-Net output. Requires Blackout and a fully zero wire frame.

        Does not activate Art-Net, clear Blackout, or alter the prepared source.
        """
        if not explicit:
            raise OutputError("Output arm requires an explicit action")
        if not confirmed:
            raise OutputError("Arm requires confirmed=true")
        outbound = wire_frame if wire_frame is not None else empty_frame()
        blockers = self.arm_blockers(wire_frame=outbound)
        if blockers:
            raise OutputError("Arm заблоковано: " + "; ".join(blockers))
        self.armed = True
        self.last_error = None

    def disarm(self) -> list[list[int]]:
        """Disarm while keeping Art-Net UDP open; force Blackout and zero frames.

        Unlike :meth:`deactivate_to_mock`, this does not close UDP or switch to Mock.
        """
        self.engine.set_blackout(True)
        self.armed = False
        if self.transport.kind is TransportKind.ARTNET and self.udp_active:
            return self._emit_zero_frames(
                count=ZERO_FRAME_SHUTDOWN_COUNT,
                raise_on_error=False,
            )
        return []

    def wire_frame(self, frame: list[int], *, from_raw: bool = False) -> list[int]:
        """Outbound safety: Art-Net nonzero only when armed.

        Blackout no longer blanks Live Effects at the wire: the engine zeroes
        the preset base and then composes Live FX on top. Raw-tester sources
        still stay dark while Blackout is on (``from_raw=True``).
        """
        if len(frame) != DMX_UNIVERSE_SIZE:
            raise OutputError(f"Frame length must be {DMX_UNIVERSE_SIZE}")
        if from_raw and self.engine.overlays.blackout:
            return empty_frame()
        if self.transport.kind is TransportKind.ARTNET and not self.armed:
            return empty_frame()
        return list(frame)

    def publish(self, frame: list[int], *, from_raw: bool = False) -> None:
        """Send a frame according to safety rules."""
        outbound = self.wire_frame(frame, from_raw=from_raw)

        try:
            self.transport.send_frame(outbound)
            self.frames_sent += 1
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - capture as output fault
            self.last_error = str(exc)
            self.armed = False
            raise OutputError(f"Output send failed: {exc}") from exc

    def publish_engine_tick(self) -> list[int]:
        snapshot = self.engine.tick()
        frame = snapshot.frame
        # Art-Net (armed or not) and Mock both go through publish so the wire
        # policy can force zeros while disarmed / blacked out.
        self.publish(frame)
        return self.wire_frame(frame)

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
        emitted = self._emit_zero_frames(count=zero_count, raise_on_error=False)
        self.armed = False
        self.allow_real_network = False
        self.use_mock()
        return emitted

    def _emit_zero_frames(
        self,
        *,
        count: int = ZERO_FRAME_SHUTDOWN_COUNT,
        raise_on_error: bool = False,
    ) -> list[list[int]]:
        zeros = empty_frame()
        emitted: list[list[int]] = []
        for _ in range(max(1, count)):
            try:
                if self.transport.kind is TransportKind.ARTNET:
                    # Bypass arm gate: safety zeros must leave even while disarmed.
                    self.transport.send_frame(zeros)
                    self.frames_sent += 1
                elif self.transport.kind is TransportKind.MOCK:
                    self.transport.send_frame(zeros)
                    self.frames_sent += 1
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                if raise_on_error:
                    raise OutputError(f"Failed to emit safety zero frame: {exc}") from exc
                break
            emitted.append(list(zeros))
        return emitted

    def _close_transport(self) -> None:
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()


def create_output_controller(engine: Engine | None = None) -> OutputController:
    from orng_led.engine import create_engine

    return OutputController(engine=engine or create_engine())
