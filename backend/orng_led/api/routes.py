"""HTTP routes for state, commands, and configuration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from orng_led import __version__
from orng_led.api.runtime import AppRuntime
from orng_led.api.schemas import (
    ActivateArtNetCommand,
    AppStateResponse,
    ArmOutputCommand,
    BlackoutCommand,
    ColorHitCommand,
    CommandAck,
    DeactivateArtNetCommand,
    DisarmOutputCommand,
    DropCommand,
    FaceCommand,
    FailsafeCommand,
    FixtureChannelTestCommand,
    FixtureChannelTestSetCommand,
    HealthResponse,
    IdentifyFixtureCommand,
    IdentifyGroupCommand,
    InputButtonRequest,
    InputDispatchResponse,
    InputKeyboardRequest,
    MasterBrightnessCommand,
    PresetCreateRequest,
    PresetDuplicateRequest,
    PresetPreviewRequest,
    PresetRenameRequest,
    PresetUpdateRequest,
    PreviewSpeedCommand,
    RawTesterCommand,
    RawTesterSetCommand,
    ReadyResponse,
    SaveAppConfigRequest,
    SaveLayoutRequest,
    SavePatchRequest,
    SaveProfileRequest,
    SeekEpisodeCommand,
    SelectPresetCommand,
    StrobeCommand,
    SweepHitCommand,
    ValidatePatchRequest,
    WhiteHitCommand,
    dump_config_model,
)
from orng_led.config.models import ConfigError
from orng_led.output.contract import OutputError


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

    @router.get("/stage/layout")
    def stage_layout(request: Request) -> dict:
        return get_runtime(request).stage_layout()

    @router.get("/presets/{preset_id}/preview-clip")
    def preview_clip(
        preset_id: str,
        request: Request,
        seconds: float = 6.0,
        fps: int = 12,
        start_s: float = 0.0,
    ) -> dict:
        runtime = get_runtime(request)
        try:
            return runtime.preview_clip(preset_id, seconds=seconds, fps=fps, start_s=start_s)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/presets")
    def presets(request: Request) -> dict:
        runtime = get_runtime(request)
        return {"presets": runtime.list_preset_summaries()}

    @router.get("/presets/{preset_id}")
    def get_preset(preset_id: str, request: Request) -> dict:
        runtime = get_runtime(request)
        try:
            return runtime.get_preset_document(preset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/presets", response_model=CommandAck)
    async def create_preset(body: PresetCreateRequest, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            if body.preset:
                state = runtime.create_preset(body.preset)
            elif body.id and body.label:
                state = runtime.create_default_custom(body.id, body.label)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Provide full preset document or id+label for a default custom preset",
                )
        except (ConfigError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.put("/presets/{preset_id}", response_model=CommandAck)
    async def put_preset(preset_id: str, body: PresetUpdateRequest, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.update_preset(preset_id, body.preset)
        except (ConfigError, KeyError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/presets/{preset_id}/rename", response_model=CommandAck)
    async def rename_preset(
        preset_id: str, body: PresetRenameRequest, request: Request
    ) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.rename_preset(preset_id, body.label)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/presets/{preset_id}/duplicate", response_model=CommandAck)
    async def duplicate_preset(
        preset_id: str, body: PresetDuplicateRequest, request: Request
    ) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.duplicate_preset(preset_id, body.new_id, body.label)
        except (ConfigError, KeyError) as exc:
            status = 404 if isinstance(exc, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.delete("/presets/{preset_id}", response_model=CommandAck)
    async def delete_preset(preset_id: str, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.delete_preset(preset_id)
        except (ConfigError, KeyError) as exc:
            status = 404 if isinstance(exc, KeyError) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/presets/{preset_id}/preview", response_model=CommandAck)
    async def preview_preset(
        preset_id: str, body: PresetPreviewRequest, request: Request
    ) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.preview_preset(preset_id, speed=body.speed)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

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

    @router.post("/commands/seek-episode", response_model=CommandAck)
    async def seek_episode(body: SeekEpisodeCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state, replay = runtime.apply_seek_episode(
                body.episode_index,
                client_command_id=body.client_command_id,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.get("/setup/channel-roles")
    def channel_roles(request: Request) -> dict:
        runtime = get_runtime(request)
        return {"roles": runtime.channel_role_catalog()}

    @router.post("/setup/fixture-channel-test/begin", response_model=CommandAck)
    async def fixture_test_begin(
        body: FixtureChannelTestCommand,
        request: Request,
    ) -> CommandAck:
        runtime = get_runtime(request)
        if not body.fixture_id:
            raise HTTPException(status_code=400, detail="fixture_id required")
        try:
            state = runtime.begin_fixture_channel_test(body.fixture_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/fixture-channel-test/set", response_model=CommandAck)
    async def fixture_test_set(
        body: FixtureChannelTestSetCommand,
        request: Request,
    ) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.set_fixture_local_channel(
                body.fixture_id,
                body.local,
                body.value,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/fixture-channel-test/reset", response_model=CommandAck)
    async def fixture_test_reset(
        body: FixtureChannelTestCommand,
        request: Request,
    ) -> CommandAck:
        runtime = get_runtime(request)
        if not body.fixture_id:
            raise HTTPException(status_code=400, detail="fixture_id required")
        try:
            state = runtime.reset_fixture_channel_test(body.fixture_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/fixture-channel-test/end", response_model=CommandAck)
    async def fixture_test_end(request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state = runtime.end_fixture_channel_test()
        await runtime.broadcast_state()
        return CommandAck(state=state)

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

    @router.post("/commands/drop", response_model=CommandAck)
    async def drop(body: DropCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_drop(
            body.action,
            client_command_id=body.client_command_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/color-hit", response_model=CommandAck)
    async def color_hit(body: ColorHitCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_color_hit(
            body.rgb(),
            client_command_id=body.client_command_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/sweep-hit", response_model=CommandAck)
    async def sweep_hit(body: SweepHitCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_sweep_hit(
            body.rgb(),
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

    @router.post("/output/activate-artnet", response_model=CommandAck)
    async def activate_artnet(body: ActivateArtNetCommand, request: Request) -> CommandAck:
        """Start Art-Net UDP with zero frames only. Requires explicit confirmation."""
        runtime = get_runtime(request)
        try:
            state, replay = runtime.activate_artnet_network(
                confirmed=body.confirmed,
                allow_real_udp=True,
                client_command_id=body.client_command_id,
            )
        except OutputError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/output/deactivate-artnet", response_model=CommandAck)
    async def deactivate_artnet(
        body: DeactivateArtNetCommand,
        request: Request,
    ) -> CommandAck:
        """Send zeros, close UDP, return runtime to Mock."""
        runtime = get_runtime(request)
        state, replay = runtime.deactivate_artnet_network(
            client_command_id=body.client_command_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.get("/output/activation-blockers")
    def activation_blockers(request: Request) -> dict:
        runtime = get_runtime(request)
        blockers = runtime.artnet_activation_blockers()
        return {"ok": len(blockers) == 0, "blockers": blockers}

    @router.post("/output/arm", response_model=CommandAck)
    async def arm_output(body: ArmOutputCommand, request: Request) -> CommandAck:
        """Arm Art-Net while Blackout stays on. Requires confirmed=true."""
        runtime = get_runtime(request)
        try:
            state, replay = runtime.arm_output(
                confirmed=body.confirmed,
                client_command_id=body.client_command_id,
            )
        except OutputError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/output/disarm", response_model=CommandAck)
    async def disarm_output(
        body: DisarmOutputCommand,
        request: Request,
    ) -> CommandAck:
        """Disarm: force Blackout + zeros; keep Art-Net UDP open if active."""
        runtime = get_runtime(request)
        try:
            state, replay = runtime.disarm_output(
                client_command_id=body.client_command_id,
            )
        except OutputError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.get("/output/arm-blockers")
    def arm_blockers(request: Request) -> dict:
        runtime = get_runtime(request)
        blockers = runtime.artnet_arm_blockers()
        return {"ok": len(blockers) == 0, "blockers": blockers}

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

    @router.get("/input/mapping")
    def input_mapping() -> dict:
        from orng_led.input.contract import (
            ALL_BUTTON_IDS,
            BRIGHTNESS_STEP,
            BUTTON_PRESET_IDS,
        )
        from orng_led.input.mapping import KEYBOARD_CODE_MAP

        return {
            "buttons": list(ALL_BUTTON_IDS),
            "presets": BUTTON_PRESET_IDS,
            "brightness_step": BRIGHTNESS_STEP,
            "keyboard": {
                code: {"button_id": button_id, "edge": edge}
                for code, (button_id, edge) in KEYBOARD_CODE_MAP.items()
            },
            "gpio": {
                "implemented": False,
                "detail": "Raspberry GPIO adapter is PENDING HARDWARE / future stage",
            },
        }

    @router.post("/input/button", response_model=InputDispatchResponse)
    async def input_button(body: InputButtonRequest, request: Request) -> InputDispatchResponse:
        from orng_led.input.contract import InputSource
        from orng_led.input.mapping import button_to_event

        runtime = get_runtime(request)
        if runtime.input_dispatcher is None:
            raise HTTPException(status_code=503, detail="Input dispatcher not ready")
        try:
            event = button_to_event(
                body.button_id,
                source=InputSource(body.source),
                edge=body.edge,
                client_command_id=body.client_command_id,
            )
            result = runtime.input_dispatcher.dispatch(event)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.accepted and result.state is not None and not result.idempotent_replay:
            await runtime.broadcast_state()
        state = result.state or runtime.build_state()
        return InputDispatchResponse(
            accepted=result.accepted,
            reason=result.reason,
            idempotent_replay=result.idempotent_replay,
            state=state,
        )

    @router.post("/input/keyboard", response_model=InputDispatchResponse)
    async def input_keyboard(body: InputKeyboardRequest, request: Request) -> InputDispatchResponse:
        from orng_led.input.adapters import KeyboardInputAdapter

        runtime = get_runtime(request)
        if runtime.input_dispatcher is None:
            raise HTTPException(status_code=503, detail="Input dispatcher not ready")
        adapter = KeyboardInputAdapter()
        events = adapter.handle_raw(body.model_dump())
        if not events:
            return InputDispatchResponse(
                accepted=False,
                reason="unmapped_or_repeat",
                state=runtime.build_state(),
            )
        result = runtime.input_dispatcher.dispatch(events[0])
        if result.accepted and result.state is not None and not result.idempotent_replay:
            await runtime.broadcast_state()
        state = result.state or runtime.build_state()
        return InputDispatchResponse(
            accepted=result.accepted,
            reason=result.reason,
            idempotent_replay=result.idempotent_replay,
            state=state,
        )

    return router
