"""Single-process application runtime: one engine, one output, WS fan-out."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from orng_led.api.schemas import (
    AppStateResponse,
    EngineState,
    OutputState,
)
from orng_led.config import (
    collect_patch_errors,
    default_config_dir,
    load_show_config,
    save_model,
)
from orng_led.config.io import parse_model
from orng_led.config.models import (
    AppConfig,
    ChannelRole,
    ConfigError,
    FixtureKind,
    FixtureProfile,
    PatchDocument,
    ShowConfig,
    SpatialLayout,
    TransportMode,
)
from orng_led.config.validation import global_channel
from orng_led.engine.clock import FRAME_DT
from orng_led.engine.engine import Engine, EngineSnapshot
from orng_led.engine.intents import Rgbw
from orng_led.input.dispatcher import InputDispatcher
from orng_led.output.contract import OutputError
from orng_led.output.controller import OutputController
from orng_led.presets.models import PresetDocument
from orng_led.presets.store import PresetStore
from orng_led.setup.raw_tester import RawTesterSession
from orng_led.simulator.decode import decode_simulator_view

WsSender = Callable[[dict], Awaitable[None]]


@dataclass
class AppRuntime:
    """Process-wide lighting runtime.

    Reconnecting WebSocket clients attach to this same instance and never create
    a second engine.
    """

    show: ShowConfig
    engine: Engine
    output: OutputController
    sequence: int = 0
    _subscribers: set[WsSender] = field(default_factory=set)
    _seen_commands: OrderedDict[str, AppStateResponse] = field(default_factory=OrderedDict)
    _loop_task: asyncio.Task[None] | None = None
    _running: bool = False
    _shutting_down: bool = False
    _last_wall: float | None = None
    autostart_loop: bool = True
    max_idempotency_keys: int = 256
    preview_speed: float = 1.0
    raw_tester: RawTesterSession = field(default_factory=RawTesterSession)
    config_dir: Path = field(default_factory=default_config_dir)
    preset_store: PresetStore = field(default_factory=PresetStore)
    input_dispatcher: InputDispatcher | None = None

    @classmethod
    def create(
        cls,
        *,
        autostart_loop: bool = True,
        show: ShowConfig | None = None,
        config_dir: Path | None = None,
    ) -> AppRuntime:
        root = config_dir or default_config_dir()
        loaded = show or load_show_config(root)
        store = PresetStore.load(root / "presets")
        engine = Engine(show=loaded, presets=store.programs())
        # Canon §5.4: start Mock + blackout / zero safe frame.
        engine.set_blackout(True)
        output = OutputController(engine=engine)
        runtime = cls(
            show=loaded,
            engine=engine,
            output=output,
            autostart_loop=autostart_loop,
            config_dir=root,
            preset_store=store,
        )
        runtime.refresh_presets()
        runtime.input_dispatcher = InputDispatcher(runtime=runtime)
        return runtime

    def refresh_presets(self) -> None:
        from orng_led.engine.presets import NONE_PRESET_ID, NonePresetProgram

        programs = self.preset_store.programs()
        programs[NONE_PRESET_ID] = NonePresetProgram()
        previous = self.engine.active_preset_id
        self.engine.presets = programs
        if previous not in self.engine.presets:
            fallback = next(pid for pid in self.engine.presets if pid != NONE_PRESET_ID)
            self.engine.select_preset(fallback, reset_clock=True)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._shutting_down = False
        self._last_wall = time.monotonic()
        if self.autostart_loop:
            self._loop_task = asyncio.create_task(self._tick_loop(), name="orng-led-tick")

    @property
    def is_ready(self) -> bool:
        return self._running and not self._shutting_down

    async def stop(self) -> None:
        await self.shutdown()

    async def _tick_loop(self) -> None:
        try:
            while self._running and not self._shutting_down:
                now = time.monotonic()
                last = self._last_wall or now
                dt = max(0.0, min(0.25, now - last))
                self._last_wall = now
                if dt > 0:
                    self.tick(dt_s=dt)
                    await self.broadcast_state()
                await asyncio.sleep(FRAME_DT)
        except asyncio.CancelledError:
            raise

    def tick(self, dt_s: float = FRAME_DT) -> EngineSnapshot:
        # preview_speed advances show time only; wall clock drives safety timers.
        wall = max(0.0, dt_s)
        scaled = wall * self.preview_speed
        snapshot = self.engine.tick(dt_s=scaled, wall_dt_s=wall)
        self._publish_frame(self._published_frame(snapshot))
        self.sequence += 1
        return snapshot

    def _source_frame(self, snap: EngineSnapshot | None = None) -> list[int]:
        """Prepared source before wire gating (Raw session or engine output)."""
        if self.raw_tester.active:
            return list(self.raw_tester.frame)
        if snap is not None:
            return list(snap.frame)
        return list(self.engine.render_at(self.engine.clock.time(), dt_s=0.0).frame)

    def _source_owner(self, source: list[int]) -> str:
        if self.raw_tester.active:
            return "raw_tester"
        if any(int(value) for value in source):
            return "engine"
        return "none"

    def _published_frame(self, snap: EngineSnapshot | None = None) -> list[int]:
        """Frame fed into output before wire policy.

        Raw tester values are visible as source while Blackout holds the wire at
        zero via ``from_raw=True``. Engine frames already apply Blackout to the
        preset base and keep Live Effects.
        """
        if self.raw_tester.active:
            return list(self.raw_tester.frame)
        if snap is not None:
            return list(snap.frame)
        return list(self.engine.render_at(self.engine.clock.time(), dt_s=0.0).frame)

    def _wire_frame(self, snap: EngineSnapshot | None = None) -> list[int]:
        """Actual outbound frame after arm / Raw-under-Blackout wire policy."""
        return self.output.wire_frame(
            self._source_frame(snap),
            from_raw=self.raw_tester.active,
        )

    def _publish_frame(self, frame: list[int], *, from_raw: bool | None = None) -> None:
        raw = self.raw_tester.active if from_raw is None else from_raw
        try:
            self.output.publish(frame, from_raw=raw)
        except OutputError:
            pass

    def build_state(self) -> AppStateResponse:
        snap = self.engine.render_at(self.engine.clock.time(), dt_s=0.0)
        out = self.output.status()
        source = self._source_frame(snap)
        wire = self.output.wire_frame(source, from_raw=self.raw_tester.active)
        source_sum = int(sum(source))
        source_nonzero = int(sum(1 for value in source if value))
        wire_sum = int(sum(wire))
        wire_nonzero = int(sum(1 for value in wire if value))
        return AppStateResponse(
            engine=EngineState(
                preset_id=snap.preset_id,
                preset_time_s=snap.preset_time_s,
                episode_index=snap.episode_index,
                episode_time_s=snap.episode_time_s,
                episode_count=snap.episode_count,
                cycle_duration_s=snap.cycle_duration_s,
                blackout=snap.blackout,
                face_on=snap.face_on,
                face_brightness=self.engine.overlays.face_brightness,
                strobe_held=snap.strobe_held,
                white_hit_active=snap.white_hit_active,
                master_brightness=snap.master_brightness,
                time_s=snap.time_s,
                drop_active=snap.drop_active,
                color_hit_active=snap.color_hit_active,
                sweep_active=snap.sweep_active,
            ),
            output=OutputState(
                transport=out.transport.value,
                preferred_transport=self.show.app.transport.value,
                armed=out.armed,
                last_error=out.last_error,
                frames_sent=out.frames_sent,
                network_allowed=out.network_allowed,
                udp_active=out.udp_active,
                target_ip=out.target_ip,
                universe=out.universe,
                # frame_sum / nonzero_channels = wire (compat + topbar Σ).
                frame_sum=wire_sum,
                nonzero_channels=wire_nonzero,
                source_frame_sum=source_sum,
                source_nonzero_channels=source_nonzero,
                wire_frame_sum=wire_sum,
                wire_nonzero_channels=wire_nonzero,
                source_owner=self._source_owner(source),  # type: ignore[arg-type]
            ),
            presets=sorted(self.engine.presets.keys()),
            fixture_ids=[fx.id for fx in self.show.patch.fixtures],
            frame=wire,
            sequence=self.sequence,
            preview_speed=self.preview_speed,
            simulator=decode_simulator_view(self.show, wire),
            raw_tester=self.raw_tester.as_dict(),
        )

    def artnet_activation_blockers(self) -> list[str]:
        # Use wire frame so prepared Raw under Blackout does not block activation.
        frame = self._wire_frame()
        return self.output.activation_blockers(
            published_frame=frame,
            raw_tester_active=False,
        )

    def activate_artnet_network(
        self,
        *,
        confirmed: bool = False,
        allow_real_udp: bool = True,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Explicit confirmed Art-Net activation. Starts disarmed with zero frames.

        Production callers pass ``allow_real_udp=True``. Tests must pass
        ``allow_real_udp=False`` and inject a recording socket first.
        """
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if not confirmed:
            raise OutputError("Art-Net activation requires an explicit confirmation")

        # Keep Raw tester session intact — prepared source must survive activation.
        self.engine.set_blackout(True)
        # Ensure published frame is zero before the safety gate.
        self._publish_frame(self._published_frame())

        # Production: take target from YAML. Tests may pre-configure a loopback
        # target with an injected RecordingSocket — keep that address so venue
        # IP is never used in unit tests.
        if self.output._injected_socket is not None:
            if not self.output.target_ip:
                raise OutputError("Injected Art-Net socket requires a configured target_ip")
            self.output.configure_artnet(
                target_ip=self.output.target_ip,
                universe=self.output.universe,
                udp_port=self.output.udp_port,
                injected_socket=self.output._injected_socket,
            )
        elif self.show.app.artnet.target_ip:
            self.output.configure_artnet(
                target_ip=self.show.app.artnet.target_ip,
                universe=self.show.app.artnet.universe,
                udp_port=self.show.app.artnet.udp_port,
            )

        blockers = self.artnet_activation_blockers()
        if blockers:
            raise OutputError("Активацію Art-Net заблоковано: " + "; ".join(blockers))

        self.output.activate_artnet(
            explicit=True,
            confirmed=True,
            allow_real_udp=allow_real_udp,
        )
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def deactivate_artnet_network(
        self,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Send zeros, close UDP, return runtime to Mock + Blackout.

        Prepared Raw tester source is preserved across the return to Mock.
        """
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.output.deactivate_to_mock()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def artnet_arm_blockers(self) -> list[str]:
        wire = self._wire_frame()
        return self.output.arm_blockers(wire_frame=wire)

    def arm_output(
        self,
        *,
        confirmed: bool = False,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Explicit confirmed Arm. Does not activate Art-Net or clear Blackout."""
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if not confirmed:
            raise OutputError("Arm requires confirmed=true")
        wire = self._wire_frame()
        self.output.arm(explicit=True, confirmed=True, wire_frame=wire)
        # Blackout stays on; publish confirms wire remains zeros.
        self._publish_frame(self._source_frame())
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def disarm_output(
        self,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        """Disarm: force Blackout + zeros; keep Art-Net UDP open if active."""
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.output.disarm()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_preview_speed(
        self,
        value: float,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.preview_speed = max(1.0, min(120.0, float(value)))
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def enter_raw_tester(self) -> AppStateResponse:
        """Start Raw as prepared source. Does not activate Art-Net, Arm, or clear Blackout."""
        self.raw_tester.enter()
        self._publish_frame(self._source_frame())
        self.sequence += 1
        return self.build_state()

    def exit_raw_tester(self) -> AppStateResponse:
        """Explicit Raw exit: Blackout + zero frames first, then clear the source."""
        from orng_led.engine.frame import empty_frame
        from orng_led.output.contract import TransportKind

        self.engine.set_blackout(True)
        if self.output.transport_kind is TransportKind.ARTNET and self.output.udp_active:
            self.output._emit_zero_frames(count=3, raise_on_error=False)
        else:
            self._publish_frame(empty_frame())
        self.raw_tester.exit()
        self._publish_frame(empty_frame())
        self.sequence += 1
        return self.build_state()

    def raw_tester_blackout(self) -> AppStateResponse:
        if not self.raw_tester.active:
            self.enter_raw_tester()
        zeros = self.raw_tester.blackout()
        self._publish_frame(zeros if not self.engine.overlays.blackout else self._published_frame())
        self.sequence += 1
        return self.build_state()

    def raw_tester_set(
        self,
        *,
        channel: int | None = None,
        value: int | None = None,
        channels: dict[int, int] | None = None,
    ) -> AppStateResponse:
        if not self.raw_tester.active:
            raise RuntimeError("Raw tester is not active")
        if channels:
            self.raw_tester.set_channels(channels)
        elif channel is not None and value is not None:
            self.raw_tester.set_channel(channel, value)
        else:
            raise ValueError("Provide channel+value or channels map")
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def _identify_channels(self, fixture, profile, level: int) -> dict[int, int]:
        """Provisional Mock-visible identify values (never hardware_verified)."""
        channels: dict[int, int] = {}
        level = max(0, min(255, level))

        def set_role(role: ChannelRole, value: int) -> None:
            for ch in profile.channels:
                if ch.role is role:
                    channels[global_channel(fixture.start_address, ch.local)] = value

        # Prefer cyan when RGB roles exist. If only dimmer+red are mapped
        # (hardware-confirmed PAR), raise those so identify stays Mock-visible.
        set_role(ChannelRole.DIMMER, level)
        has_green = any(ch.role is ChannelRole.GREEN for ch in profile.channels)
        has_blue = any(ch.role is ChannelRole.BLUE for ch in profile.channels)
        if has_green and has_blue:
            set_role(ChannelRole.RED, 0)
            set_role(ChannelRole.GREEN, level)
            set_role(ChannelRole.BLUE, level)
        else:
            set_role(ChannelRole.RED, level)
        if fixture.kind in (FixtureKind.PAR, FixtureKind.FACE_PAR):
            set_role(ChannelRole.WHITE, 0)
        elif fixture.kind is FixtureKind.BAR:
            for ch in profile.channels:
                if ch.role is ChannelRole.SEGMENT:
                    channels[global_channel(fixture.start_address, ch.local)] = level
                elif ch.role in (ChannelRole.SEGMENT_COLOR, ChannelRole.WHOLE_COLOR):
                    table = ch.palette or {}
                    channels[global_channel(fixture.start_address, ch.local)] = int(
                        table.get("cyan", table.get("blue", 48))
                    )
        elif fixture.kind is FixtureKind.BEAM:
            set_role(ChannelRole.SHUTTER, 255)
            set_role(ChannelRole.COLOR, 0)
            # Controlled provisional home pose for Mock visibility.
            set_role(ChannelRole.PAN_COARSE, 128)
            set_role(ChannelRole.TILT_COARSE, 128)
        return channels

    def begin_fixture_channel_test(self, fixture_id: str) -> AppStateResponse:
        """Start Raw-backed single-fixture channel testing without touching Art-Net/Arm."""
        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            raise KeyError(f"Unknown fixture {fixture_id!r}")
        if not self.raw_tester.active:
            self.raw_tester.enter()
        else:
            self.raw_tester.blackout()
        self._publish_frame(self.raw_tester.frame, from_raw=True)
        self.sequence += 1
        return self.build_state()

    def set_fixture_local_channel(
        self,
        fixture_id: str,
        local: int,
        value: int,
    ) -> AppStateResponse:
        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            raise KeyError(f"Unknown fixture {fixture_id!r}")
        profile = self.show.profile_for(fixture)
        if local < 1 or local > profile.footprint:
            raise ValueError(f"Local channel {local} outside footprint {profile.footprint}")
        if not self.raw_tester.active:
            self.begin_fixture_channel_test(fixture_id)
        global_channel = fixture.start_address + local - 1
        self.raw_tester.set_channel(global_channel, value)
        self._publish_frame(self.raw_tester.frame, from_raw=True)
        self.sequence += 1
        return self.build_state()

    def reset_fixture_channel_test(self, fixture_id: str) -> AppStateResponse:
        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            raise KeyError(f"Unknown fixture {fixture_id!r}")
        if not self.raw_tester.active:
            self.begin_fixture_channel_test(fixture_id)
        profile = self.show.profile_for(fixture)
        for local in range(1, profile.footprint + 1):
            self.raw_tester.set_channel(fixture.start_address + local - 1, 0)
        self._publish_frame(self.raw_tester.frame, from_raw=True)
        self.sequence += 1
        return self.build_state()

    def end_fixture_channel_test(self) -> AppStateResponse:
        return self.exit_raw_tester()

    def channel_role_catalog(self) -> list[dict[str, str]]:
        from orng_led.config.models import ChannelRole

        labels = {
            ChannelRole.UNUSED: "Не використовується / завжди 0",
            ChannelRole.DIMMER: "Master Dimmer",
            ChannelRole.RED: "Red",
            ChannelRole.GREEN: "Green",
            ChannelRole.BLUE: "Blue",
            ChannelRole.WHITE: "White",
            ChannelRole.AMBER: "Amber",
            ChannelRole.UV: "UV",
            ChannelRole.STROBE: "Strobe",
            ChannelRole.STROBE_SPEED: "Strobe Speed",
            ChannelRole.PROGRAM: "Program / Effect",
            ChannelRole.EFFECT_SPEED: "Effect Speed",
            ChannelRole.DIRECTION_MODE: "Direction / Mode",
            ChannelRole.WHOLE_COLOR: "Whole Fixture Color / Palette",
            ChannelRole.SEGMENT_COLOR: "Segment Color",
            ChannelRole.SEGMENT: "Segment Level",
            ChannelRole.PAN_COARSE: "Pan",
            ChannelRole.PAN_FINE: "Pan Fine",
            ChannelRole.TILT_COARSE: "Tilt",
            ChannelRole.TILT_FINE: "Tilt Fine",
            ChannelRole.MOVEMENT_SPEED: "Movement Speed",
            ChannelRole.COLOR: "Color Wheel",
            ChannelRole.GOBO: "Gobo",
            ChannelRole.GOBO_ROTATION: "Gobo Rotation",
            ChannelRole.PRISM: "Prism",
            ChannelRole.PRISM_ROTATION: "Prism Rotation",
            ChannelRole.FOCUS: "Focus",
            ChannelRole.ZOOM: "Zoom",
            ChannelRole.RESET: "Reset",
            ChannelRole.FIXED: "Фіксоване значення",
            ChannelRole.SHUTTER: "Shutter",
            ChannelRole.MACRO: "Program / Effect (macro)",
            ChannelRole.SPEED: "Effect Speed (legacy)",
            ChannelRole.UNKNOWN: "Не використовується / завжди 0",
        }
        order = [
            ChannelRole.UNUSED,
            ChannelRole.DIMMER,
            ChannelRole.RED,
            ChannelRole.GREEN,
            ChannelRole.BLUE,
            ChannelRole.WHITE,
            ChannelRole.AMBER,
            ChannelRole.UV,
            ChannelRole.STROBE,
            ChannelRole.STROBE_SPEED,
            ChannelRole.PROGRAM,
            ChannelRole.EFFECT_SPEED,
            ChannelRole.DIRECTION_MODE,
            ChannelRole.WHOLE_COLOR,
            ChannelRole.SEGMENT_COLOR,
            ChannelRole.SEGMENT,
            ChannelRole.PAN_COARSE,
            ChannelRole.PAN_FINE,
            ChannelRole.TILT_COARSE,
            ChannelRole.TILT_FINE,
            ChannelRole.MOVEMENT_SPEED,
            ChannelRole.COLOR,
            ChannelRole.GOBO,
            ChannelRole.GOBO_ROTATION,
            ChannelRole.PRISM,
            ChannelRole.PRISM_ROTATION,
            ChannelRole.FOCUS,
            ChannelRole.ZOOM,
            ChannelRole.RESET,
            ChannelRole.FIXED,
            ChannelRole.SHUTTER,
        ]
        return [{"role": role.value, "label": labels[role]} for role in order]

    def identify_fixture(self, fixture_id: str, level: int = 200) -> AppStateResponse:
        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            raise KeyError(f"Unknown fixture {fixture_id!r}")
        profile = self.show.profile_for(fixture)
        if not self.raw_tester.active:
            self.enter_raw_tester()
        else:
            self.raw_tester.blackout()
        values = self._identify_channels(fixture, profile, level)
        if not values:
            raise ConfigError(f"Fixture {fixture_id!r} has no channels for identify")
        self.raw_tester.set_channels(values)
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def identify_group(self, group: str, level: int = 180) -> AppStateResponse:
        matches = [fx for fx in self.show.patch.fixtures if group in fx.groups]
        if not matches:
            raise KeyError(f"No fixtures in group {group!r}")
        if not self.raw_tester.active:
            self.enter_raw_tester()
        else:
            self.raw_tester.blackout()
        values: dict[int, int] = {}
        for fixture in matches:
            profile = self.show.profile_for(fixture)
            values.update(self._identify_channels(fixture, profile, level))
        self.raw_tester.set_channels(values)
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def validate_patch_payload(
        self,
        patch_data: dict,
        profiles_data: dict | None = None,
    ) -> dict:
        profiles = self.show.profiles
        if profiles_data:
            profiles = {
                pid: parse_model(FixtureProfile, pdata, source=f"profiles[{pid}]")
                for pid, pdata in profiles_data.items()
            }
        patch = parse_model(PatchDocument, patch_data, source="patch")
        errors = collect_patch_errors(patch, profiles)
        return {"ok": not errors, "errors": errors}

    def save_app_config(self, data: dict) -> AppStateResponse:
        app = parse_model(AppConfig, data, source="app.yaml")
        # HOME safety: never persist armed Art-Net or fake hardware verification.
        app = app.model_copy(
            update={
                "output_armed": False,
                "artnet": app.artnet.model_copy(update={"hardware_verified": False}),
            }
        )
        if app.transport is TransportMode.ARTNET and not app.artnet.target_ip:
            # Allow selecting Art-Net mode in YAML draft only with IP filled later;
            # keep preferred transport but do not enable real output.
            pass
        save_model(self.config_dir / "app.yaml", app)
        self.show = self.show.model_copy(update={"app": app})
        self.engine.show = self.show
        self.engine.overlays.master_brightness = app.master_brightness
        # Configure Art-Net settings in memory but stay on Mock unless already forced.
        if app.artnet.target_ip:
            self.output.configure_artnet(
                target_ip=app.artnet.target_ip,
                universe=app.artnet.universe,
                udp_port=app.artnet.udp_port,
            )
        # Never auto-arm; keep Mock publishing during HOME wizard.
        self.output.use_mock()
        return self.build_state()

    def save_patch(self, data: dict) -> AppStateResponse:
        patch = parse_model(PatchDocument, data, source="patch.yaml")
        errors = collect_patch_errors(patch, self.show.profiles)
        if errors:
            raise ConfigError("Patch validation failed:\n- " + "\n- ".join(errors))
        layout_ids = set(self.show.layout.fixtures)
        patch_ids = {fx.id for fx in patch.fixtures}
        if layout_ids != patch_ids:
            raise ConfigError(
                "layout.fixtures must list exactly the patch fixture ids. "
                f"missing={sorted(patch_ids - layout_ids)}, "
                f"extra={sorted(layout_ids - patch_ids)}."
            )
        save_model(self.config_dir / "patch.yaml", patch)
        self.show = self.show.model_copy(update={"patch": patch})
        self.engine.show = self.show
        return self.build_state()

    def save_profile(self, data: dict) -> AppStateResponse:
        profile = parse_model(FixtureProfile, data, source="profile")
        profile = profile.model_copy(update={"hardware_verified": False})
        path = self.config_dir / "profiles" / f"{profile.id}.yaml"
        save_model(path, profile)
        profiles = dict(self.show.profiles)
        profiles[profile.id] = profile
        self.show = self.show.model_copy(update={"profiles": profiles})
        self.engine.show = self.show
        return self.build_state()

    def save_layout(self, data: dict) -> AppStateResponse:
        layout = parse_model(SpatialLayout, data, source="layout.yaml")
        patch_ids = {fx.id for fx in self.show.patch.fixtures}
        if set(layout.fixtures) != patch_ids:
            raise ConfigError(
                "layout.fixtures must list exactly the patch fixture ids. "
                f"missing={sorted(patch_ids - set(layout.fixtures))}, "
                f"extra={sorted(set(layout.fixtures) - patch_ids)}."
            )
        save_model(self.config_dir / "layout.yaml", layout)
        self.show = self.show.model_copy(update={"layout": layout})
        self.engine.show = self.show
        return self.build_state()

    def reload_from_disk(self) -> AppStateResponse:
        if self.raw_tester.active:
            self.exit_raw_tester()
        loaded = load_show_config(self.config_dir)
        self.show = loaded
        self.engine.show = loaded
        self.engine.overlays.master_brightness = loaded.app.master_brightness
        self.output.use_mock()
        if loaded.app.artnet.target_ip:
            self.output.configure_artnet(
                target_ip=loaded.app.artnet.target_ip,
                universe=loaded.app.artnet.universe,
                udp_port=loaded.app.artnet.udp_port,
            )
        return self.build_state()

    def readiness_summary(self) -> dict:
        profiles = [
            {
                "id": profile.id,
                "label": profile.label,
                "hardware_verified": False,
                "badge": "Не перевірено на обладнанні",
            }
            for profile in self.show.profiles.values()
        ]
        out = self.output.status()
        source = self._source_frame()
        wire = self.output.wire_frame(source)
        return {
            "transport_preferred": self.show.app.transport.value,
            "runtime_transport": self.output.transport_kind.value,
            "output_armed": out.armed,
            "artnet_network_enabled": out.network_allowed,
            "udp_active": out.udp_active,
            "blackout": self.engine.overlays.blackout,
            "frame_sum": int(sum(wire)),
            "nonzero_channels": int(sum(1 for value in wire if value)),
            "source_frame_sum": int(sum(source)),
            "source_nonzero_channels": int(sum(1 for value in source if value)),
            "wire_frame_sum": int(sum(wire)),
            "wire_nonzero_channels": int(sum(1 for value in wire if value)),
            "source_owner": self._source_owner(source),
            "activation_blockers": self.artnet_activation_blockers(),
            "arm_blockers": self.artnet_arm_blockers(),
            "artnet_hardware_verified": False,
            "artnet_badge": "Не перевірено на обладнанні",
            "profiles": profiles,
            "fixture_count": len(self.show.patch.fixtures),
            "patch_ok": len(collect_patch_errors(self.show.patch, self.show.profiles)) == 0,
            "raw_tester_active": self.raw_tester.active,
            "notes": [
                "Збережений Art-Net у YAML не активує мережу автоматично.",
                "Реальний UDP починається лише після явного підтвердження кнопкою "
                "«Безпечно активувати Art-Net».",
                "Підготовлений Raw-кадр зберігається між кроками майстра і під час "
                "активації Art-Net / Arm.",
                "Arm вмикається окремо кнопкою «Увімкнути Arm» лише при Blackout "
                "і нульовому wire-кадрі.",
                "Поки armed=false або Blackout=true, на дріт ідуть лише нулі.",
            ],
        }

    def _remember(self, client_command_id: str | None, state: AppStateResponse) -> None:
        if not client_command_id:
            return
        self._seen_commands[client_command_id] = state
        self._seen_commands.move_to_end(client_command_id)
        while len(self._seen_commands) > self.max_idempotency_keys:
            self._seen_commands.popitem(last=False)

    def _idempotent(self, client_command_id: str | None) -> AppStateResponse | None:
        if client_command_id and client_command_id in self._seen_commands:
            return self._seen_commands[client_command_id]
        return None

    def apply_select_preset(
        self,
        preset_id: str,
        *,
        reset_clock: bool = True,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        from orng_led.engine.presets import NONE_PRESET_ID, NonePresetProgram

        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if preset_id == NONE_PRESET_ID:
            self.engine.presets[NONE_PRESET_ID] = NonePresetProgram()
        self.engine.select_preset(preset_id, reset_clock=reset_clock)
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_seek_episode(
        self,
        episode_index: int,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.engine.seek_episode(episode_index)
        # Publish immediately so the new episode is on the wire/source now.
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_white_hit(
        self, *, client_command_id: str | None = None
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.engine.trigger_white_hit()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_strobe(
        self,
        action: str,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if action == "press":
            self.engine.strobe_press()
        elif action == "release":
            self.engine.strobe_release()
        else:
            raise ValueError(f"Unknown strobe action: {action}")
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_drop(
        self,
        action: str,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if action == "press":
            self.engine.drop_press()
        elif action == "release":
            self.engine.drop_release()
        else:
            raise ValueError(f"Unknown drop action: {action}")
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_color_hit(
        self,
        color: tuple[float, float, float] | None = None,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        rgb = Rgbw(r=color[0], g=color[1], b=color[2]) if color else None
        self.engine.trigger_color_hit(rgb)
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_sweep_hit(
        self,
        color: tuple[float, float, float] | None = None,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        rgb = Rgbw(r=color[0], g=color[1], b=color[2]) if color else None
        self.engine.trigger_sweep_hit(rgb)
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def stage_layout(self) -> dict:
        """Static stage geometry for the simulator (no DMX values here)."""
        layout = self.show.layout
        kinds = {fx.id: fx.kind.value for fx in self.show.patch.fixtures}
        labels = {fx.id: fx.label for fx in self.show.patch.fixtures}
        groups = {fx.id: list(fx.groups) for fx in self.show.patch.fixtures}
        placements = []
        for placement in layout.placements:
            data = placement.model_dump(mode="json")
            data["label"] = labels.get(placement.fixture_id, placement.fixture_id)
            data["groups"] = groups.get(placement.fixture_id, [])
            data["kind"] = kinds.get(placement.fixture_id, data["kind"])
            placements.append(data)
        return {
            "description": layout.description,
            "placements": placements,
            "cable_chain": list(layout.cable_chain),
            "artnet_node": (
                layout.artnet_node.model_dump(mode="json") if layout.artnet_node else None
            ),
            "hardware_verified": False,
            "notes": "Provisional stage plan from the operator sketch. PENDING HARDWARE.",
        }

    def preview_clip(
        self,
        preset_id: str,
        *,
        seconds: float = 6.0,
        fps: int = 12,
        start_s: float = 0.0,
    ) -> dict:
        """Render a safe Mock preview clip with the real renderer, off-transport.

        The clip never touches the live engine, the output controller or any
        transport: it runs a throwaway engine over the same profiles/layout,
        so what the operator sees is exactly what the show renderer produces.
        """
        if preset_id not in self.preset_store.programs():
            raise KeyError(f"Unknown preset {preset_id!r}")
        fps = max(4, min(30, int(fps)))
        seconds = max(1.0, min(20.0, float(seconds)))
        programs = self.preset_store.programs()
        engine = Engine(show=self.show, presets=programs, active_preset_id=preset_id)
        engine.set_blackout(False)
        engine.overlays.master_brightness = 1.0
        dt = 1.0 / fps
        start_s = max(0.0, min(600.0, float(start_s)))
        for _ in range(int(start_s * fps)):
            engine.tick(dt_s=dt, wall_dt_s=dt)
        frames = []
        total = int(seconds * fps)
        for _ in range(total):
            snap = engine.tick(dt_s=dt, wall_dt_s=dt)
            view = decode_simulator_view(self.show, snap.frame)
            frames.append(view.model_dump(mode="json"))
        return {
            "preset_id": preset_id,
            "fps": fps,
            "seconds": seconds,
            "start_s": start_s,
            "transport": "none",
            "safe_mock_preview": True,
            "frames": frames,
        }

    def apply_blackout(
        self,
        enabled: bool | None = None,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        if enabled is None:
            self.engine.toggle_blackout()
        else:
            self.engine.set_blackout(enabled)
        # Republish immediately so Blackout zeros override raw tester.
        self._publish_frame(self._published_frame())
        self.sequence += 1
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_face(
        self,
        enabled: bool,
        brightness: float | None = None,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.engine.set_face(enabled, brightness)
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_master_brightness(
        self,
        value: float,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.engine.set_master_brightness(value)
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_focus_loss(
        self, *, client_command_id: str | None = None
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.output.on_focus_loss()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def apply_visibility_hidden(
        self,
        *,
        client_command_id: str | None = None,
    ) -> tuple[AppStateResponse, bool]:
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.output.on_visibility_hidden()
        state = self.build_state()
        self._remember(client_command_id, state)
        return state, False

    def list_preset_summaries(self) -> list[dict]:
        return [item.model_dump(mode="json") for item in self.preset_store.summaries()]

    def get_preset_document(self, preset_id: str) -> dict:
        return self.preset_store.get(preset_id).model_dump(mode="json")

    def create_preset(self, data: dict) -> AppStateResponse:
        document = PresetDocument.model_validate(data)
        document = document.model_copy(update={"hardware_tuned": False, "builtin": False})
        self.preset_store.create(document)
        self.refresh_presets()
        return self.build_state()

    def create_default_custom(self, preset_id: str, label: str) -> AppStateResponse:
        document = self.preset_store.default_custom_document(preset_id, label)
        self.preset_store.create(document)
        self.refresh_presets()
        return self.build_state()

    def update_preset(self, preset_id: str, data: dict) -> AppStateResponse:
        self.preset_store.update(preset_id, data)
        self.refresh_presets()
        return self.build_state()

    def rename_preset(self, preset_id: str, label: str) -> AppStateResponse:
        self.preset_store.rename(preset_id, label)
        self.refresh_presets()
        return self.build_state()

    def duplicate_preset(
        self, preset_id: str, new_id: str, new_label: str | None = None
    ) -> AppStateResponse:
        self.preset_store.duplicate(preset_id, new_id, new_label)
        self.refresh_presets()
        return self.build_state()

    def delete_preset(self, preset_id: str) -> AppStateResponse:
        self.preset_store.delete(preset_id)
        self.refresh_presets()
        return self.build_state()

    def preview_preset(self, preset_id: str, *, speed: float = 10.0) -> AppStateResponse:
        """Select preset and accelerate preview on Mock only (never arms Art-Net)."""
        self.output.use_mock()
        if self.raw_tester.active:
            self.exit_raw_tester()
        self.engine.select_preset(preset_id, reset_clock=True)
        # Preview is an explicit operator action: clear startup blackout so Mock is visible.
        self.engine.set_blackout(False)
        self.preview_speed = max(1.0, min(120.0, float(speed)))
        self._publish_frame(self._published_frame())
        self.sequence += 1
        return self.build_state()

    def on_ws_disconnect(self) -> None:
        # Losing the controlling socket must clear held actions, not stop the show.
        self.output.on_disconnect()
        if self.input_dispatcher is not None:
            self.input_dispatcher.debouncer.clear()

    def subscribe(self, sender: WsSender) -> None:
        self._subscribers.add(sender)

    def unsubscribe(self, sender: WsSender) -> None:
        self._subscribers.discard(sender)

    async def broadcast_state(self) -> None:
        if not self._subscribers:
            return
        payload = {"type": "state", "state": self.build_state().model_dump(mode="json")}
        dead: list[WsSender] = []
        for sender in list(self._subscribers):
            try:
                await sender(payload)
            except Exception:  # noqa: BLE001
                dead.append(sender)
        for sender in dead:
            self.unsubscribe(sender)

    async def shutdown(self) -> list[list[int]]:
        if self._shutting_down:
            return []
        self._shutting_down = True
        self._running = False
        if self.raw_tester.active:
            self.exit_raw_tester()
        task = self._loop_task
        self._loop_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        emitted = self.output.shutdown()
        self._subscribers.clear()
        return emitted
