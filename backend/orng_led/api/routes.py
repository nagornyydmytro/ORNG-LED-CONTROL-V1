"""HTTP routes for state, commands, and configuration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from orng_led import __version__
from orng_led.api.runtime import AppRuntime
from orng_led.api.schemas import (
    AppStateResponse,
    BlackoutCommand,
    CommandAck,
    FaceCommand,
    FailsafeCommand,
    HealthResponse,
    MasterBrightnessCommand,
    ReadyResponse,
    SelectPresetCommand,
    StrobeCommand,
    WhiteHitCommand,
    dump_config_model,
)


def get_runtime(request: Request) -> AppRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime is not initialized")
    return runtime


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        runtime = get_runtime(request)
        status = runtime.output.status()
        frontend = getattr(request.app.state, "frontend_dist_present", False)
        return HealthResponse(
            service="orng-led-control",
            version=__version__,
            transport=status.transport.value,
            output_armed=status.armed,
            artnet_network_enabled=status.network_allowed,
            frontend_dist_present=bool(frontend),
            ready=runtime.is_ready,
        )

    @router.get("/ready", response_model=ReadyResponse)
    def ready(request: Request) -> ReadyResponse:
        runtime = get_runtime(request)
        ok = runtime.is_ready
        return ReadyResponse(
            ready=ok,
            engine=True,
            output=True,
            detail=None if ok else "Runtime is stopping or not started",
        )

    @router.get("/state", response_model=AppStateResponse)
    def state(request: Request) -> AppStateResponse:
        return get_runtime(request).build_state()

    @router.get("/config/app")
    def config_app(request: Request) -> dict:
        return dump_config_model(get_runtime(request).show.app)

    @router.get("/config/patch")
    def config_patch(request: Request) -> dict:
        return dump_config_model(get_runtime(request).show.patch)

    @router.get("/config/profiles")
    def config_profiles(request: Request) -> dict:
        runtime = get_runtime(request)
        return {
            profile_id: dump_config_model(profile)
            for profile_id, profile in runtime.show.profiles.items()
        }

    @router.get("/config/layout")
    def config_layout(request: Request) -> dict:
        return dump_config_model(get_runtime(request).show.layout)

    @router.get("/presets")
    def presets(request: Request) -> dict:
        runtime = get_runtime(request)
        items = []
        for preset_id, preset in sorted(runtime.engine.presets.items()):
            label = getattr(preset, "label", preset_id)
            items.append({"id": preset_id, "label": label})
        return {"presets": items}

    @router.post("/commands/select-preset", response_model=CommandAck)
    async def select_preset(body: SelectPresetCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state, replay = runtime.apply_select_preset(
                body.preset_id,
                reset_clock=body.reset_clock,
                client_command_id=body.client_command_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/white-hit", response_model=CommandAck)
    async def white_hit(body: WhiteHitCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_white_hit(client_command_id=body.client_command_id)
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/strobe", response_model=CommandAck)
    async def strobe(body: StrobeCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_strobe(
            body.action,
            client_command_id=body.client_command_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/blackout", response_model=CommandAck)
    async def blackout(body: BlackoutCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_blackout(
            body.enabled,
            client_command_id=body.client_command_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/face", response_model=CommandAck)
    async def face(body: FaceCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_face(
            body.enabled,
            body.brightness,
            client_command_id=body.client_command_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/master-brightness", response_model=CommandAck)
    async def master_brightness(body: MasterBrightnessCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_master_brightness(
            body.value,
            client_command_id=body.client_command_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/focus-loss", response_model=CommandAck)
    async def focus_loss(body: FailsafeCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_focus_loss(client_command_id=body.client_command_id)
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/visibility-hidden", response_model=CommandAck)
    async def visibility_hidden(body: FailsafeCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_visibility_hidden(
            client_command_id=body.client_command_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/shutdown", response_model=CommandAck)
    async def shutdown(request: Request) -> CommandAck:
        runtime = get_runtime(request)
        await runtime.shutdown()
        return CommandAck(state=runtime.build_state())

    return router
