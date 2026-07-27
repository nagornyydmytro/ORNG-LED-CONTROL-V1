"""Single-process application runtime: one engine, one output, WS fan-out."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from orng_led.api.schemas import (
    AppStateResponse,
    EngineState,
    OutputState,
)
from orng_led.config import load_show_config
from orng_led.config.models import ShowConfig
from orng_led.engine.clock import FRAME_DT
from orng_led.engine.engine import Engine, EngineSnapshot
from orng_led.output.contract import OutputError
from orng_led.output.controller import OutputController
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

    @classmethod
    def create(cls, *, autostart_loop: bool = True, show: ShowConfig | None = None) -> AppRuntime:
        loaded = show or load_show_config()
        engine = Engine(show=loaded)
        output = OutputController(engine=engine)
        return cls(
            show=loaded,
            engine=engine,
            output=output,
            autostart_loop=autostart_loop,
        )

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
        frame = snapshot.frame
        if self.output.transport_kind.value == "mock":
            self.output.publish(frame)
        elif self.output.armed:
            try:
                self.output.publish(frame)
            except OutputError:
                # Fault already recorded on controller; engine keeps running.
                pass
        self.sequence += 1
        return snapshot

    def build_state(self) -> AppStateResponse:
        snap = self.engine.render_at(self.engine.clock.time(), dt_s=0.0)
        out = self.output.status()
        frame = list(snap.frame)
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

    def on_ws_disconnect(self) -> None:
        # Losing the controlling socket must clear held actions, not stop the show.
        self.output.on_disconnect()

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
