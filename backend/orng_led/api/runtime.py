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
    FixtureProfile,
    PatchDocument,
    ShowConfig,
    SpatialLayout,
    TransportMode,
)
from orng_led.config.validation import global_channel
from orng_led.engine.clock import FRAME_DT
from orng_led.engine.engine import Engine, EngineSnapshot
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
        output = OutputController(engine=engine)
        runtime = cls(
            show=loaded,
            engine=engine,
            output=output,
            autostart_loop=autostart_loop,
            config_dir=root,
            preset_store=store,
        )
        runtime.input_dispatcher = InputDispatcher(runtime=runtime)
        return runtime

    def refresh_presets(self) -> None:
        self.engine.presets = self.preset_store.programs()
        if self.engine.active_preset_id not in self.engine.presets:
            fallback = next(iter(self.engine.presets))
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
        # preview_speed advances show time faster for simulator review only.
        scaled = max(0.0, dt_s) * self.preview_speed
        snapshot = self.engine.tick(dt_s=scaled)
        frame = list(self.raw_tester.frame) if self.raw_tester.active else snapshot.frame
        self._publish_frame(frame)
        self.sequence += 1
        return snapshot

    def _publish_frame(self, frame: list[int]) -> None:
        if self.output.transport_kind.value == "mock":
            self.output.publish(frame)
        elif self.output.armed:
            try:
                self.output.publish(frame)
            except OutputError:
                # Fault already recorded on controller; engine keeps running.
                pass

    def build_state(self) -> AppStateResponse:
        snap = self.engine.render_at(self.engine.clock.time(), dt_s=0.0)
        out = self.output.status()
        frame = list(self.raw_tester.frame) if self.raw_tester.active else list(snap.frame)
        return AppStateResponse(
            engine=EngineState(
                preset_id=snap.preset_id,
                preset_time_s=snap.preset_time_s,
                episode_index=snap.episode_index,
                episode_time_s=snap.episode_time_s,
                blackout=snap.blackout,
                face_on=snap.face_on,
                face_brightness=self.engine.overlays.face_brightness,
                strobe_held=snap.strobe_held,
                white_hit_active=snap.white_hit_active,
                master_brightness=snap.master_brightness,
                time_s=snap.time_s,
            ),
            output=OutputState(
                transport=out.transport.value,
                armed=out.armed,
                last_error=out.last_error,
                frames_sent=out.frames_sent,
                network_allowed=out.network_allowed,
                target_ip=out.target_ip,
                universe=out.universe,
            ),
            presets=sorted(self.engine.presets.keys()),
            fixture_ids=[fx.id for fx in self.show.patch.fixtures],
            frame=frame,
            sequence=self.sequence,
            preview_speed=self.preview_speed,
            simulator=decode_simulator_view(self.show, frame),
            raw_tester=self.raw_tester.as_dict(),
        )

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
        # Raw tester is Mock-only and never arms Art-Net.
        self.output.use_mock()
        self.raw_tester.enter()
        self._publish_frame(self.raw_tester.frame)
        self.sequence += 1
        return self.build_state()

    def exit_raw_tester(self) -> AppStateResponse:
        zeros = self.raw_tester.exit()
        self._publish_frame(zeros)
        self.sequence += 1
        return self.build_state()

    def raw_tester_blackout(self) -> AppStateResponse:
        if not self.raw_tester.active:
            self.enter_raw_tester()
        zeros = self.raw_tester.blackout()
        self._publish_frame(zeros)
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
        self._publish_frame(self.raw_tester.frame)
        self.sequence += 1
        return self.build_state()

    def identify_fixture(self, fixture_id: str, level: int = 200) -> AppStateResponse:
        fixture = next((fx for fx in self.show.patch.fixtures if fx.id == fixture_id), None)
        if fixture is None:
            raise KeyError(f"Unknown fixture {fixture_id!r}")
        profile = self.show.profile_for(fixture)
        if not self.raw_tester.active:
            self.enter_raw_tester()
        else:
            self.raw_tester.blackout()
        dimmer = next((ch for ch in profile.channels if ch.role is ChannelRole.DIMMER), None)
        if dimmer is None:
            raise ConfigError(f"Fixture {fixture_id!r} has no dimmer channel for identify")
        channel = global_channel(fixture.start_address, dimmer.local)
        self.raw_tester.set_channel(channel, level)
        self._publish_frame(self.raw_tester.frame)
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
        for fixture in matches:
            profile = self.show.profile_for(fixture)
            dimmer = next((ch for ch in profile.channels if ch.role is ChannelRole.DIMMER), None)
            if dimmer is None:
                continue
            channel = global_channel(fixture.start_address, dimmer.local)
            self.raw_tester.set_channel(channel, level)
        self._publish_frame(self.raw_tester.frame)
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
        return {
            "transport_preferred": self.show.app.transport.value,
            "runtime_transport": self.output.transport_kind.value,
            "output_armed": False,
            "artnet_network_enabled": False,
            "artnet_hardware_verified": False,
            "artnet_badge": "Не перевірено на обладнанні",
            "profiles": profiles,
            "fixture_count": len(self.show.patch.fixtures),
            "patch_ok": len(collect_patch_errors(self.show.patch, self.show.profiles)) == 0,
            "raw_tester_active": self.raw_tester.active,
            "notes": [
                "Mock wizard path does not confirm hardware.",
                "Art-Net remains disarmed; real network send is blocked by default.",
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
        cached = self._idempotent(client_command_id)
        if cached is not None:
            return cached, True
        self.engine.select_preset(preset_id, reset_clock=reset_clock)
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
        self.preview_speed = max(1.0, min(120.0, float(speed)))
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
            self.raw_tester.exit()
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
