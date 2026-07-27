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
    IdentifyFixtureCommand,
    IdentifyGroupCommand,
    MasterBrightnessCommand,
    PreviewSpeedCommand,
    RawTesterCommand,
    RawTesterSetCommand,
    ReadyResponse,
    SaveAppConfigRequest,
    SaveLayoutRequest,
    SavePatchRequest,
    SaveProfileRequest,
    SelectPresetCommand,
    StrobeCommand,
    ValidatePatchRequest,
    WhiteHitCommand,
    dump_config_model,
)
from orng_led.config.models import ConfigError


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

    @router.post("/commands/preview-speed", response_model=CommandAck)
    async def preview_speed(body: PreviewSpeedCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_preview_speed(
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

    @router.post("/setup/validate-patch")
    def validate_patch(body: ValidatePatchRequest, request: Request) -> dict:
        runtime = get_runtime(request)
        try:
            return runtime.validate_patch_payload(body.patch, body.profiles)
        except ConfigError as exc:
            return {"ok": False, "errors": [str(exc)]}

    @router.put("/config/app", response_model=CommandAck)
    async def put_app(body: SaveAppConfigRequest, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.save_app_config(body.config)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.put("/config/patch", response_model=CommandAck)
    async def put_patch(body: SavePatchRequest, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.save_patch(body.patch)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.put("/config/profiles/{profile_id}", response_model=CommandAck)
    async def put_profile(
        profile_id: str, body: SaveProfileRequest, request: Request
    ) -> CommandAck:
        runtime = get_runtime(request)
        try:
            profile = body.profile
            if profile.get("id") and profile["id"] != profile_id:
                raise HTTPException(status_code=400, detail="profile id mismatch")
            profile = {**profile, "id": profile_id}
            state = runtime.save_profile(profile)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.put("/config/layout", response_model=CommandAck)
    async def put_layout(body: SaveLayoutRequest, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.save_layout(body.layout)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/reload", response_model=CommandAck)
    async def reload_config(request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.reload_from_disk()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.get("/setup/readiness")
    def readiness(request: Request) -> dict:
        return get_runtime(request).readiness_summary()

    @router.get("/setup/raw-tester")
    def raw_tester_status(request: Request) -> dict:
        return get_runtime(request).raw_tester.as_dict()

    @router.post("/setup/raw-tester/enter", response_model=CommandAck)
    async def raw_enter(body: RawTesterCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state = runtime.enter_raw_tester()
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/raw-tester/exit", response_model=CommandAck)
    async def raw_exit(body: RawTesterCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state = runtime.exit_raw_tester()
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/raw-tester/blackout", response_model=CommandAck)
    async def raw_blackout(body: RawTesterCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state = runtime.raw_tester_blackout()
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/raw-tester/set", response_model=CommandAck)
    async def raw_set(body: RawTesterSetCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        channels = None
        if body.channels:
            channels = {int(k): int(v) for k, v in body.channels.items()}
        try:
            state = runtime.raw_tester_set(
                channel=body.channel,
                value=body.value,
                channels=channels,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/identify-fixture", response_model=CommandAck)
    async def identify_fixture(body: IdentifyFixtureCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.identify_fixture(body.fixture_id, body.level)
        except (KeyError, ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/identify-group", response_model=CommandAck)
    async def identify_group(body: IdentifyGroupCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.identify_group(body.group, body.level)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    return router
