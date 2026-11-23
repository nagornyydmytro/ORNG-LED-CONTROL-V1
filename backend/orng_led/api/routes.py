"""HTTP routes for state, commands, and configuration."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from orng_led import __version__
from orng_led.api.runtime import AppRuntime
from orng_led.api.schemas import (
    ActivateArtNetCommand,
    ActivateSceneRequest,
    AppStateResponse,
    ArmOutputCommand,
    BeamCalibrationBeginCommand,
    BeamCalibrationPositionCommand,
    BeamCalibrationSaveCommand,
    BeamCalibrationVisibleCommand,
    BlackoutCommand,
    BpmSyncCommand,
    NextLookCommand,
    ColorHitCommand,
    CommandAck,
    DeactivateArtNetCommand,
    DisarmOutputCommand,
    DropCommand,
    EditorEpisodePreviewRequest,
    EditorEpisodePreviewStopRequest,
    FaceCommand,
    FailsafeCommand,
    FixtureChannelTestCommand,
    FixtureChannelTestSetCommand,
    HealthResponse,
    IdentifyFixtureCommand,
    IdentifyGroupCommand,
    InputActionRequest,
    InputButtonRequest,
    InputDispatchResponse,
    InputKeyboardRequest,
    LiveFxSpeedCommand,
    MasterBrightnessCommand,
    HeadDeskCommand,
    PadPresetsCommand,
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
    SweepCommand,
    SweepHitCommand,
    ValidatePatchRequest,
    WhiteHitCommand,
    dump_config_model,
)
from orng_led.config.models import ConfigError
from orng_led.output.contract import OutputError
from orng_led.version import build_id as app_build_id


def _desk_role_name(runtime: AppRuntime) -> str:
    from orng_led.desktop.desk_role import desk_role

    return desk_role(runtime.config_root)


def _desk_instance_id(runtime: AppRuntime) -> str:
    from orng_led.desktop.desk_role import instance_id

    return instance_id(runtime.config_root)


def _reject_empty_preset(preset_id: str) -> None:
    """NONE is the built-in empty look. It is not a scene file."""
    if str(preset_id).strip().upper() == "NONE":
        raise HTTPException(
            status_code=409,
            detail="NONE — порожній пресет пульта. Його не відкривають, не змінюють і не видаляють.",
        )


def get_runtime(request: Request) -> AppRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime is not initialized")
    return runtime


def _draft_base(body: dict) -> int | None:
    value = body.get("base_version")
    return value if isinstance(value, int) else None


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        runtime = get_runtime(request)
        status = runtime.output.status()
        frontend = getattr(request.app.state, "frontend_dist_present", False)
        from orng_led.updater.mode import detect_arch, detect_install_mode, detect_platform

        venue = runtime.venue_ready_status()
        kb_connected: bool | None = None
        try:
            kb = runtime.linux_keyboard_status()
            kb_connected = bool(kb.get("connected"))
        except Exception:  # noqa: BLE001
            kb_connected = None
        return HealthResponse(
            service="orng-led-control",
            version=__version__,
            build_id=app_build_id(),
            transport=status.transport.value,
            output_armed=status.armed,
            artnet_network_enabled=status.network_allowed,
            frontend_dist_present=bool(frontend),
            ready=runtime.is_ready,
            frame_cycle_alive=runtime.frame_cycle_alive,
            scene_id=runtime.scene_id,
            scene_name=runtime.scene_name,
            blackout=bool(runtime.engine.overlays.blackout),
            install_mode=detect_install_mode().value,
            platform=detect_platform(),
            arch=detect_arch(),
            scene_error=runtime.scene_error,
            degraded=bool(runtime.scene_error) or venue.get("venue_ready_state") == "degraded",
            udp_active=bool(status.udp_active),
            output_policy=venue.get("output_policy"),
            venue_ready_state=venue.get("venue_ready_state"),
            venue_last_error=venue.get("venue_last_error"),
            ethernet_link=venue.get("ethernet_link"),
            artnet_route_valid=venue.get("artnet_route_valid"),
            keyboard_connected=kb_connected,
            startup_preset=venue.get("startup_preset"),
            instance_id=_desk_instance_id(runtime),
            desk_role=_desk_role_name(runtime),
            executor_online=getattr(runtime, "executor_online", None),
            frame_timing=runtime.frame_timing(),
        )

    @router.get("/audio/diagnostics")
    def audio_diagnostics(request: Request) -> dict[str, object]:
        """Local capture snapshot. Not on the desk, and not a copy of the audio."""
        from orng_led.audio.service import local_diagnostics_allowed

        host = request.client.host if request.client is not None else None
        if not local_diagnostics_allowed(host):
            raise HTTPException(status_code=403, detail="localhost only")
        return get_runtime(request).bpm.diagnostics()

    @router.get("/desk")
    def get_desk(request: Request) -> dict:
        runtime = get_runtime(request)
        return {
            "role": _desk_role_name(runtime),
            "instance_id": _desk_instance_id(runtime),
            "executor_online": getattr(runtime, "executor_online", None),
        }

    @router.post("/desk/role")
    async def set_desk(request: Request) -> dict:
        from orng_led.desktop.desk_role import set_desk_role

        runtime = get_runtime(request)
        body = await request.json()
        role = str(body.get("role") or "")
        try:
            saved = set_desk_role(runtime.config_root, role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if saved == "raspberry" and runtime.output.armed:
            runtime.disarm_output()
        return {
            "role": saved,
            "executor_online": getattr(runtime, "executor_online", None),
        }

    @router.get("/scenes")
    def list_scenes(request: Request) -> dict:
        runtime = get_runtime(request)
        return {"scenes": runtime.list_scenes(), "active": runtime.active_scene_info()}

    @router.get("/scenes/active")
    def active_scene(request: Request) -> dict:
        return get_runtime(request).active_scene_info()

    @router.post("/scenes/activate", response_model=CommandAck)
    async def activate_scene(body: ActivateSceneRequest, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state, replay = runtime.activate_scene(
                body.scene_id,
                client_command_id=body.client_command_id,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.get("/input/status")
    def input_status(request: Request) -> dict:
        runtime = get_runtime(request)
        return {"linux_keyboard": runtime.linux_keyboard_status()}

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

    @router.get("/auto/catalog")
    def auto_catalog() -> dict:
        from orng_led.audio.preview import catalog_payload

        return catalog_payload()

    @router.get("/auto/preview-clip")
    def auto_preview_clip(
        request: Request,
        look: str = "vertical_wave",
        cycle_beats: float = 4.0,
        palette: str = "orange",
        intensity: str = "full",
        bpm: float = 120.0,
        frames: int = 0,
    ) -> dict:
        """Canonical Auto preview. Isolated renderer, never the live executor."""
        from orng_led.audio.preview import preview_clip
        from orng_led.config.io import default_scene_dir

        runtime = get_runtime(request)
        if getattr(runtime, "show", None) is None:
            raise HTTPException(status_code=503, detail="Сцену ще не завантажено")
        try:
            return preview_clip(
                runtime.show,
                look,
                cycle_beats=cycle_beats,
                palette=palette,
                intensity=intensity,
                bpm=bpm,
                frames=frames,
                scene_id=getattr(runtime, "scene_id", None),
                scene_name=getattr(runtime, "scene_name", None),
                scene_dir=getattr(runtime, "scene_dir", None) or default_scene_dir(),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Невідома композиція {look}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/presets/{preset_id}/preview-clip")
    def preview_clip(
        preset_id: str,
        request: Request,
        seconds: float = 6.0,
        fps: int = 12,
        start_s: float = 0.0,
    ) -> dict:
        _reject_empty_preset(preset_id)
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
        _reject_empty_preset(preset_id)
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
        _reject_empty_preset(preset_id)
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
        _reject_empty_preset(preset_id)
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
        _reject_empty_preset(preset_id)
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
        _reject_empty_preset(preset_id)
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

    @router.post("/presets/editor-preview/start", response_model=CommandAck)
    async def editor_preview_start(
        body: EditorEpisodePreviewRequest, request: Request
    ) -> CommandAck:
        """Loop one draft episode through the real engine (no auto Art-Net/Arm)."""
        runtime = get_runtime(request)
        try:
            state = runtime.start_editor_episode_preview(
                preset_id=body.preset_id,
                preset_label=body.preset_label,
                episode_index=body.episode_index,
                episode=body.episode,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/presets/editor-preview/stop", response_model=CommandAck)
    async def editor_preview_stop(
        body: EditorEpisodePreviewStopRequest, request: Request
    ) -> CommandAck:
        runtime = get_runtime(request)
        state = runtime.stop_editor_episode_preview()
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

    @router.post("/setup/beam-calibration/begin", response_model=CommandAck)
    async def beam_cal_begin(
        body: BeamCalibrationBeginCommand,
        request: Request,
    ) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.begin_beam_calibration_test(
                body.fixture_id,
                confirmed=body.confirmed,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/beam-calibration/set", response_model=CommandAck)
    async def beam_cal_set(
        body: BeamCalibrationPositionCommand,
        request: Request,
    ) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.set_beam_calibration_position(pan=body.pan, tilt=body.tilt)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/beam-calibration/visible", response_model=CommandAck)
    async def beam_cal_visible(
        body: BeamCalibrationVisibleCommand,
        request: Request,
    ) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.set_beam_calibration_visible(
                enabled=body.enabled,
                confirmed=body.confirmed,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/beam-calibration/home", response_model=CommandAck)
    async def beam_cal_home(request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.beam_calibration_go_home()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/beam-calibration/end", response_model=CommandAck)
    async def beam_cal_end(request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state = runtime.end_beam_calibration_test()
        await runtime.broadcast_state()
        return CommandAck(state=state)

    @router.post("/setup/beam-calibration/save", response_model=CommandAck)
    async def beam_cal_save(
        body: BeamCalibrationSaveCommand,
        request: Request,
    ) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state = runtime.save_beam_calibration(body.fixture_id, body.spatial_update())
        except (KeyError, ValueError) as exc:
            from orng_led.config.models import ConfigError

            if isinstance(exc, ConfigError):
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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

    @router.post("/commands/sweep", response_model=CommandAck)
    async def sweep(body: SweepCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_sweep(
            body.action,
            mode=body.mode,
            color=body.rgb(),
            client_command_id=body.client_command_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/pad-presets", response_model=CommandAck)
    async def pad_presets(body: PadPresetsCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state, replay = runtime.apply_pad_presets(
                body.preset_ids,
                client_command_id=body.client_command_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/live-fx-speed", response_model=CommandAck)
    async def live_fx_speed(body: LiveFxSpeedCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_live_fx_speeds(
            strobe_speed=body.strobe_speed,
            sweep_speed=body.sweep_speed,
            persist=body.persist,
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

    @router.get("/output/connection")
    def output_connection(request: Request) -> dict:
        runtime = get_runtime(request)
        artnet = runtime.show.app.artnet
        return {
            "role": _desk_role_name(runtime),
            "saved_target_ip": artnet.target_ip,
            "universe": artnet.universe,
            "fps": artnet.fps,
            "hardware_verified": artnet.hardware_verified,
        }

    @router.post("/output/discover")
    def output_discover(request: Request) -> dict:
        """Recheck controllers. Does not arm output and does not choose among several."""
        from orng_led.output.artnet_discover import discover_nodes

        runtime = get_runtime(request)
        if _desk_role_name(runtime) != "local":
            raise HTTPException(
                status_code=409,
                detail="Пошук контролера доступний лише для виводу з цього комп'ютера.",
            )
        nodes = discover_nodes()
        saved = runtime.show.app.artnet.target_ip
        return {
            "nodes": nodes,
            "saved_target_ip": saved,
            "matches_saved": bool(saved and saved in nodes),
            "needs_choice": len(nodes) > 1,
        }

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

    @router.post("/commands/head-desk", response_model=CommandAck)
    async def head_desk(body: HeadDeskCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        runtime.apply_head_desk(
            enabled=body.enabled,
            light=body.light,
            scene=body.scene,
            color=body.color,
            x=body.x,
            y=body.y,
        )
        return CommandAck(state=runtime.build_state(reuse=True))

    @router.post("/commands/bpm-sync", response_model=CommandAck)
    async def bpm_sync(body: BpmSyncCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_bpm_sync(
            enabled=body.enabled,
            mode=body.mode,
            scope=body.scope,
            palette=body.palette,
            cast=body.cast,
            groups=body.groups,
            excluded=body.excluded,
            character=body.character,
            intensity=body.intensity,
            period=body.period,
            par_rhythm=body.par_rhythm,
            accent=body.accent,
            client_command_id=body.client_command_id,
            source_id=body.source_id,
            intent_ms=body.intent_ms,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/auto-next-look", response_model=CommandAck)
    async def auto_next_look(body: NextLookCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        try:
            state, replay = runtime.apply_next_auto_look(client_command_id=body.client_command_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        state, replay = runtime.apply_focus_loss(
            client_command_id=body.client_command_id,
            source_id=body.source_id,
        )
        if not replay:
            await runtime.broadcast_state()
        return CommandAck(state=state, idempotent_replay=replay)

    @router.post("/commands/visibility-hidden", response_model=CommandAck)
    async def visibility_hidden(body: FailsafeCommand, request: Request) -> CommandAck:
        runtime = get_runtime(request)
        state, replay = runtime.apply_visibility_hidden(
            client_command_id=body.client_command_id,
            source_id=body.source_id,
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
    def input_mapping(request: Request) -> dict:
        from orng_led.input.adapters import keyboard_dict_from_controls
        from orng_led.input.contract import (
            ALL_BUTTON_IDS,
            BRIGHTNESS_STEP,
            BUTTON_PAD_SLOTS,
        )

        runtime = get_runtime(request)
        return {
            "buttons": list(ALL_BUTTON_IDS),
            "pad_slots": BUTTON_PAD_SLOTS,
            "pad_presets": list(runtime.show.app.pad_presets),
            "brightness_step": BRIGHTNESS_STEP,
            "keyboard": keyboard_dict_from_controls(runtime.scene_controls),
            "gpio": {
                "implemented": False,
                "detail": "Raspberry GPIO adapter is PENDING HARDWARE / future stage",
            },
        }

    @router.post("/input/action", response_model=InputDispatchResponse)
    async def input_action(body: InputActionRequest, request: Request) -> InputDispatchResponse:
        """Dispatch a logical InputAction (source-scoped holds for UI Sweep etc.)."""
        from orng_led.input.contract import InputAction, InputEvent, InputSource

        runtime = get_runtime(request)
        if runtime.input_dispatcher is None:
            raise HTTPException(status_code=503, detail="Input dispatcher not ready")
        try:
            action = InputAction(body.action)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}") from exc
        event = InputEvent(
            action=action,
            source=InputSource(body.source),
            edge=body.edge,
            pad_slot=body.pad_slot,
            preset_id=body.preset_id,
            client_command_id=body.client_command_id,
            source_id=body.source_id,
        )
        try:
            result = runtime.input_dispatcher.dispatch(event)
        except ValueError as exc:
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
                source_id=body.source_id,
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
        from orng_led.input.adapters import adapter_for_profile

        if body.profile_id:
            try:
                adapter = adapter_for_profile(runtime.scene_controls, body.profile_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            adapter = KeyboardInputAdapter.from_controls(runtime.scene_controls)
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

    # --- Cloud control-plane (local UI helpers; Worker is separate) ---

    @router.get("/cloud/status")
    def cloud_status(request: Request) -> dict:
        from orng_led.cloud.agent import cloud_device_url, cloud_enabled
        from orng_led.cloud.credentials import credential_status, load_device_token
        from orng_led.cloud.metadata import load_cloud_metadata
        from orng_led.cloud.scene_bundle import pack_scene_bundle

        runtime = get_runtime(request)
        agent = getattr(runtime, "_cloud_agent", None)
        agent_status = agent.status if agent is not None else {  # type: ignore[attr-defined]
            "enabled": cloud_enabled(),
            "connected": False,
            "url_configured": bool(cloud_device_url()),
            "installation_id": None,
            "last_error": None,
            "has_token": bool(load_device_token()),
        }
        sync: dict = {"status": "unknown", "local_hash": None, "error": None}
        try:
            raw, manifest = pack_scene_bundle(runtime.config_dir)
            _ = raw
            meta = load_cloud_metadata()
            scene_meta = meta.scene(runtime.scene_id)
            local_hash = manifest["content_sha256"]
            sync = {
                "status": scene_meta.sync_status or "local",
                "local_hash": local_hash,
                "last_synced_hash": scene_meta.last_synced_hash,
                "last_synced_version": scene_meta.last_synced_version,
                "applied_version": scene_meta.applied_version,
                "applied_hash": scene_meta.applied_hash,
                "desired_version": scene_meta.desired_version,
                "desired_hash": scene_meta.desired_hash,
                "scene_id": runtime.scene_id,
                "error": scene_meta.last_sync_error,
                "diverged": bool(
                    local_hash
                    and scene_meta.last_synced_hash
                    and local_hash != scene_meta.last_synced_hash
                ),
            }
        except Exception as exc:  # noqa: BLE001
            sync = {"status": "error", "local_hash": None, "error": str(exc)}
        return {
            "agent": agent_status,
            "credential": credential_status(),
            "sync": sync,
            "admin_url": __import__("os").environ.get("ORNG_CLOUD_ADMIN_URL"),
            "device_url_configured": bool(cloud_device_url()),
            "message": (
                "Open light.orng.wtf to approve pairing; this host cannot self-approve "
                "in production."
            ),
        }

    @router.post("/cloud/pair/start")
    async def cloud_pair_start(request: Request) -> dict:
        import httpx

        from orng_led.cloud.pairing_crypto import (
            generate_client_challenge,
            generate_device_keypair,
            save_pairing_private_key,
        )
        from orng_led.cloud.sync_service import SceneSyncError, cloud_http_base

        runtime = get_runtime(request)
        try:
            http_base = cloud_http_base()
        except SceneSyncError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        challenge = generate_client_challenge()
        keypair = generate_device_keypair()
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                f"{http_base}/pair/start",
                json={
                    "client_challenge": challenge,
                    "public_key_jwk": keypair.public_jwk,
                },
            )
        if res.status_code >= 400:
            raise HTTPException(status_code=res.status_code, detail=res.text)
        data = res.json()
        pairing_id = str(data.get("pairing_id", ""))
        if pairing_id:
            save_pairing_private_key(pairing_id, keypair.private_pem, runtime.config_root)
        data["client_challenge"] = challenge
        data["approve_hint"] = "Open light.orng.wtf and confirm this pairing code."
        return data

    @router.post("/cloud/pair/complete")
    async def cloud_pair_complete(request: Request) -> dict:
        import os

        import httpx

        from orng_led.cloud.credentials import save_device_token
        from orng_led.cloud.pairing_crypto import (
            clear_pairing_private_key,
            decrypt_credential_envelope,
            load_pairing_private_key,
        )
        from orng_led.cloud.sync_service import SceneSyncError, cloud_http_base

        runtime = get_runtime(request)
        body = await request.json()
        pairing_id = str(body.get("pairing_id", ""))
        challenge = str(body.get("client_challenge", ""))
        try:
            http_base = cloud_http_base()
        except SceneSyncError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        private_pem = load_pairing_private_key(pairing_id, runtime.config_root)
        if not private_pem:
            raise HTTPException(
                status_code=400,
                detail="Missing pairing private key; call /cloud/pair/start on this host first",
            )
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                f"{http_base}/pair/complete",
                json={"pairing_id": pairing_id, "client_challenge": challenge},
            )
            if res.status_code >= 400:
                raise HTTPException(status_code=res.status_code, detail=res.text)
            data = res.json()
            envelope_b64 = str(data.get("credential_envelope_b64") or "")
            legacy_token = str(data.get("token") or "")
            token = ""
            if envelope_b64:
                try:
                    token = decrypt_credential_envelope(private_pem, envelope_b64)
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to decrypt credential envelope: {exc}",
                    ) from exc
            elif legacy_token and os.environ.get("ORNG_CLOUD_DEV_LEGACY_PAIR") == "1":
                token = legacy_token
            if not token:
                raise HTTPException(status_code=400, detail="No credential envelope from cloud")
            # Order: decrypt → save → consume → clear private key.
            # If save fails, envelope stays consumable for retry.
            save_device_token(token)
            if envelope_b64:
                consume = await client.post(
                    f"{http_base}/pair/consume",
                    json={"pairing_id": pairing_id, "client_challenge": challenge},
                )
                # Consume is idempotent after success; transient failure is OK —
                # token already saved and next complete/consume is safe.
                if consume.status_code >= 400 and consume.status_code != 410:
                    # Still clear local private key only after save succeeded.
                    pass
            clear_pairing_private_key(pairing_id, runtime.config_root)
        return {
            "installation_id": data.get("installation_id"),
            "token_saved": True,
        }

    @router.get("/scene-draft")
    def get_scene_draft(request: Request) -> dict:
        from orng_led.scenes.drafts import read_meta

        runtime = get_runtime(request)
        meta = read_meta(runtime.config_root, runtime.scene_id)
        return {"scene_id": runtime.scene_id, **meta}

    @router.get("/scene-draft/presets")
    def list_scene_draft_presets(request: Request) -> dict:
        from orng_led.scenes.drafts import load_overlay_presets

        runtime = get_runtime(request)
        docs = load_overlay_presets(Path(runtime.config_dir), runtime.config_root, runtime.scene_id)
        return {
            "presets": [
                {
                    "id": doc.id,
                    "label": doc.label,
                    "builtin": doc.builtin,
                    "hardware_tuned": doc.hardware_tuned,
                    "episode_count": len(doc.episodes),
                    "total_duration_s": doc.total_duration_s,
                }
                for doc in docs
            ]
        }

    @router.get("/scene-draft/presets/{preset_id}")
    def get_scene_draft_preset(preset_id: str, request: Request) -> dict:
        from orng_led.scenes.drafts import load_overlay_preset

        _reject_empty_preset(preset_id)
        runtime = get_runtime(request)
        try:
            doc = load_overlay_preset(
                Path(runtime.config_dir), runtime.config_root, runtime.scene_id, preset_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return doc.model_dump(mode="json")

    @router.post("/scene-draft/presets", response_model=CommandAck)
    async def create_scene_draft_preset(request: Request) -> CommandAck:
        from orng_led.scenes.drafts import write_draft_preset

        runtime = get_runtime(request)
        body = await request.json()
        preset_id = str(body.get("id") or "")
        label = str(body.get("label") or "")
        try:
            document = runtime.preset_store.default_custom_document(preset_id, label)
            write_draft_preset(
                runtime.config_root,
                runtime.scene_id,
                document,
                base_version=_draft_base(body),
            )
        except (ConfigError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.post("/scene-draft/presets/{preset_id}/rename", response_model=CommandAck)
    async def rename_scene_draft_preset(preset_id: str, request: Request) -> CommandAck:
        from orng_led.scenes.drafts import load_overlay_preset, write_draft_preset

        _reject_empty_preset(preset_id)
        runtime = get_runtime(request)
        body = await request.json()
        try:
            document = load_overlay_preset(
                Path(runtime.config_dir), runtime.config_root, runtime.scene_id, preset_id
            )
            document = document.model_copy(update={"label": str(body.get("label") or "")})
            write_draft_preset(
                runtime.config_root,
                runtime.scene_id,
                document,
                base_version=_draft_base(body),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ConfigError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.post("/scene-draft/presets/{preset_id}/duplicate", response_model=CommandAck)
    async def duplicate_scene_draft_preset(preset_id: str, request: Request) -> CommandAck:
        from orng_led.scenes.drafts import load_overlay_preset, write_draft_preset

        _reject_empty_preset(preset_id)
        runtime = get_runtime(request)
        body = await request.json()
        new_id = str(body.get("new_id") or "")
        try:
            try:
                load_overlay_preset(
                    Path(runtime.config_dir), runtime.config_root, runtime.scene_id, new_id
                )
            except KeyError:
                pass
            else:
                raise ConfigError(f"Preset {new_id!r} already exists")
            document = load_overlay_preset(
                Path(runtime.config_dir), runtime.config_root, runtime.scene_id, preset_id
            )
            update = {"id": new_id, "builtin": False}
            if body.get("label"):
                update["label"] = str(body["label"])
            document = document.model_copy(update=update)
            write_draft_preset(
                runtime.config_root,
                runtime.scene_id,
                document,
                base_version=_draft_base(body),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ConfigError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.delete("/scene-draft/presets/{preset_id}", response_model=CommandAck)
    async def delete_scene_draft_preset(preset_id: str, request: Request) -> CommandAck:
        from orng_led.scenes.drafts import load_overlay_preset, mark_deleted

        _reject_empty_preset(preset_id)
        runtime = get_runtime(request)
        body: dict = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                parsed = await request.json()
                if isinstance(parsed, dict):
                    body = parsed
            except Exception:  # noqa: BLE001
                body = {}
        try:
            load_overlay_preset(
                Path(runtime.config_dir), runtime.config_root, runtime.scene_id, preset_id
            )
            mark_deleted(
                runtime.config_root,
                runtime.scene_id,
                f"presets/{preset_id}.yaml",
                base_version=_draft_base(body),
            )
            from orng_led.scenes.drafts import retarget_pad_after_delete

            retarget_pad_after_delete(
                Path(runtime.config_dir),
                runtime.config_root,
                runtime.scene_id,
                preset_id,
                base_version=_draft_base(body),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.get("/scene-draft/profiles")
    def get_scene_draft_profiles(request: Request) -> dict:
        from orng_led.scenes.drafts import load_overlay_profiles

        runtime = get_runtime(request)
        return load_overlay_profiles(
            Path(runtime.config_dir), runtime.config_root, runtime.scene_id
        )

    @router.put("/scene-draft/app", response_model=CommandAck)
    async def put_scene_draft_app(body: SaveAppConfigRequest, request: Request) -> CommandAck:
        from orng_led.scenes.drafts import parse_app, stage_document

        runtime = get_runtime(request)
        try:
            stage_document(
                runtime.config_root,
                runtime.scene_id,
                "app.yaml",
                parse_app(body.config),
                base_version=body.base_version,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.put("/scene-draft/patch", response_model=CommandAck)
    async def put_scene_draft_patch(body: SavePatchRequest, request: Request) -> CommandAck:
        from orng_led.scenes.drafts import parse_patch, stage_document

        runtime = get_runtime(request)
        try:
            stage_document(
                runtime.config_root,
                runtime.scene_id,
                "patch.yaml",
                parse_patch(body.patch),
                base_version=body.base_version,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.put("/scene-draft/layout", response_model=CommandAck)
    async def put_scene_draft_layout(body: SaveLayoutRequest, request: Request) -> CommandAck:
        from orng_led.scenes.drafts import parse_layout, stage_document

        runtime = get_runtime(request)
        try:
            stage_document(
                runtime.config_root,
                runtime.scene_id,
                "layout.yaml",
                parse_layout(body.layout),
                base_version=body.base_version,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.put("/scene-draft/profiles/{profile_id}", response_model=CommandAck)
    async def put_scene_draft_profile(
        profile_id: str, body: SaveProfileRequest, request: Request
    ) -> CommandAck:
        from orng_led.scenes.drafts import parse_profile, stage_document

        runtime = get_runtime(request)
        try:
            stage_document(
                runtime.config_root,
                runtime.scene_id,
                f"profiles/{profile_id}.yaml",
                parse_profile(body.profile, profile_id),
                base_version=body.base_version,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.put("/scene-draft/presets/{preset_id}", response_model=CommandAck)
    async def put_scene_draft_preset(
        preset_id: str, request: Request
    ) -> CommandAck:
        from orng_led.presets.models import PresetDocument
        from orng_led.scenes.drafts import write_draft_preset

        _reject_empty_preset(preset_id)
        runtime = get_runtime(request)
        body = await request.json()
        raw = body.get("preset")
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="preset required")
        try:
            document = PresetDocument.model_validate({**raw, "id": preset_id})
            write_draft_preset(
                runtime.config_root,
                runtime.scene_id,
                document,
                base_version=_draft_base(body),
            )
        except (ConfigError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.post("/scene-draft/beam-calibration", response_model=CommandAck)
    async def post_scene_draft_beam(request: Request) -> CommandAck:
        """Store beam calibration in the draft patch. Does not move the live output."""
        from orng_led.config.models import SpatialPlacement
        from orng_led.scenes.drafts import load_draft_document, parse_patch, stage_document

        runtime = get_runtime(request)
        body = await request.json()
        fixture_id = str(body.get("fixture_id") or "")
        if not fixture_id:
            raise HTTPException(status_code=400, detail="fixture_id required")
        current = load_draft_document(runtime.config_root, runtime.scene_id, "patch.yaml")
        if current is None:
            current = runtime.show.patch.model_dump(mode="json")
        fixtures = current.get("fixtures")
        if not isinstance(fixtures, list):
            raise HTTPException(status_code=400, detail="patch has no fixtures")
        found = False
        next_fixtures = []
        skipped = {"fixture_id", "base_version"}
        update = {key: value for key, value in body.items() if key not in skipped}
        for fixture in fixtures:
            if not isinstance(fixture, dict) or fixture.get("id") != fixture_id:
                next_fixtures.append(fixture)
                continue
            spatial = fixture.get("spatial") if isinstance(fixture.get("spatial"), dict) else {}
            merged = {**spatial, **update}
            SpatialPlacement.model_validate(merged)
            next_fixtures.append({**fixture, "spatial": merged})
            found = True
        if not found:
            raise HTTPException(status_code=404, detail="fixture not in draft patch")
        try:
            stage_document(
                runtime.config_root,
                runtime.scene_id,
                "patch.yaml",
                parse_patch({**current, "fixtures": next_fixtures}),
                base_version=_draft_base(body),
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandAck(state=runtime.build_state())

    @router.get("/scene-draft/app")
    def get_scene_draft_app(request: Request) -> dict:
        from orng_led.scenes.drafts import load_draft_document

        runtime = get_runtime(request)
        draft = load_draft_document(runtime.config_root, runtime.scene_id, "app.yaml")
        if draft is not None:
            return draft
        return runtime.show.app.model_dump(mode="json")

    @router.get("/scene-draft/patch")
    def get_scene_draft_patch(request: Request) -> dict:
        from orng_led.scenes.drafts import load_draft_document

        runtime = get_runtime(request)
        draft = load_draft_document(runtime.config_root, runtime.scene_id, "patch.yaml")
        if draft is not None:
            return draft
        return runtime.show.patch.model_dump(mode="json")

    @router.get("/scene-draft/layout")
    def get_scene_draft_layout(request: Request) -> dict:
        from orng_led.scenes.drafts import load_draft_document

        runtime = get_runtime(request)
        draft = load_draft_document(runtime.config_root, runtime.scene_id, "layout.yaml")
        if draft is not None:
            return draft
        return runtime.show.layout.model_dump(mode="json")

    @router.post("/cloud/publish")
    async def cloud_publish(request: Request) -> dict:
        """Publish via device Bearer credential (desktop scopes), not Access email spoofing."""
        import base64
        import os

        import httpx

        from orng_led.cloud.credentials import load_device_token
        from orng_led.cloud.scene_bundle import pack_scene_bundle
        from orng_led.cloud.sync_service import SceneSyncError, cloud_http_base
        from orng_led.scenes.drafts import draft_fingerprint, overlay_scene_dir, read_meta

        runtime = get_runtime(request)
        body = await request.json()
        scene_id = str(body.get("scene_id") or runtime.scene_id)
        base_version = body.get("base_version", None)
        # The draft's base is the revision it was opened from. Looking up
        # MAX(version) here would let an old desktop draft overwrite newer work.
        stored_base = read_meta(runtime.config_root, scene_id).get("base_version")
        if stored_base is not None:
            if base_version is not None and int(base_version) != int(stored_base):
                raise HTTPException(status_code=409, detail="draft_base_mismatch")
            base_version = int(stored_base)
        if base_version is None:
            raise HTTPException(
                status_code=409,
                detail="base_version_required",
            )
        token = load_device_token()
        if not token:
            raise HTTPException(status_code=400, detail="No device token; complete pairing first")
        try:
            http_base = cloud_http_base()
        except SceneSyncError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        import tempfile

        from orng_led.scenes.drafts import draft_lock

        with draft_lock(scene_id):
            before = draft_fingerprint(runtime.config_root, scene_id)
            with tempfile.TemporaryDirectory(prefix="orng-publish-") as tmp:
                packed_dir = Path(tmp) / "scene"
                overlay_scene_dir(
                    Path(runtime.config_dir), runtime.config_root, scene_id, packed_dir
                )
                from orng_led.scenes.semantics import validate_scene_texts

                texts = {
                    path.relative_to(packed_dir).as_posix(): path.read_text(encoding="utf-8")
                    for path in packed_dir.rglob("*")
                    if path.is_file() and path.suffix in {".yaml", ".yml"}
                }
                problems = validate_scene_texts(texts)
                if problems:
                    raise HTTPException(status_code=400, detail="; ".join(problems[:8]))
                raw, _manifest = pack_scene_bundle(packed_dir)
            if draft_fingerprint(runtime.config_root, scene_id) != before:
                raise HTTPException(status_code=409, detail="draft_changed")
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
        }
        if os.environ.get("ORNG_CLOUD_DEV_BYPASS_ACCESS") == "1":
            email = os.environ.get("ORNG_CLOUD_DEV_ACCESS_EMAIL", "").strip()
            if email:
                headers["x-dev-access-email"] = email
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                f"{http_base}/device/scenes/{scene_id}/publish",
                headers=headers,
                json={
                    "name": runtime.scene_name,
                    "base_version": base_version,
                    "bundle_base64": base64.b64encode(raw).decode("ascii"),
                },
            )
        if res.status_code >= 400:
            raise HTTPException(status_code=res.status_code, detail=res.text)
        payload = res.json()
        from orng_led.scenes.drafts import discard_if_unchanged

        if before and discard_if_unchanged(runtime.config_root, scene_id, before):
            payload = {**payload, "draft_retained": True}
        return payload

    @router.post("/cloud/sync")
    async def cloud_sync(request: Request) -> dict:
        """Pull desired release via SceneSyncService (shared with device agent)."""
        from orng_led.cloud.sync_service import SceneSyncError, SceneSyncService

        runtime = get_runtime(request)
        body = await request.json()
        force = bool(body.get("force", False))
        blackout = bool(runtime.engine.overlays.blackout)
        armed = bool(getattr(runtime.output, "armed", False))
        try:
            return await SceneSyncService(runtime).sync_assigned_release(
                force=force,
                prefer_apply=force or blackout or not armed,
            )
        except SceneSyncError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    # --- Application updater (managed installs only) ---

    @router.get("/app/update/status")
    def app_update_status(request: Request) -> dict:
        from orng_led.updater.service import AppUpdateService

        return AppUpdateService(get_runtime(request)).status()

    @router.post("/app/update/check")
    async def app_update_check(request: Request) -> dict:
        from orng_led.updater.service import AppUpdateError, AppUpdateService

        try:
            return await AppUpdateService(get_runtime(request)).check_assigned()
        except AppUpdateError as exc:
            raise HTTPException(
                status_code=400, detail={"code": exc.code, "message": exc.message}
            ) from exc

    @router.post("/app/update/stage")
    async def app_update_stage(request: Request) -> dict:
        from orng_led.updater.service import AppUpdateError, AppUpdateService

        body = await request.json()
        try:
            return await AppUpdateService(get_runtime(request)).stage(
                version=body.get("version"),
                build_id_wanted=body.get("build_id"),
            )
        except AppUpdateError as exc:
            raise HTTPException(
                status_code=400, detail={"code": exc.code, "message": exc.message}
            ) from exc

    @router.post("/app/update/apply")
    async def app_update_apply(request: Request) -> dict:
        from orng_led.updater.service import AppUpdateError, AppUpdateService

        body = await request.json()
        try:
            # Returns restarting after safe lighting + apply-request; helper
            # performs pointer swap, health, and terminal update_result.
            # simulate=False in production — never fake instantaneous applied.
            return AppUpdateService(get_runtime(request)).apply(
                expected_version=body.get("version"),
                expected_build_id=body.get("build_id"),
                expected_content_sha256=body.get("content_sha256"),
                force_unsafe=bool(body.get("force")),
                command_id=body.get("command_id"),
                simulate=False,
            )
        except AppUpdateError as exc:
            raise HTTPException(
                status_code=400, detail={"code": exc.code, "message": exc.message}
            ) from exc

    @router.post("/app/update/cancel")
    def app_update_cancel(request: Request) -> dict:
        from orng_led.updater.service import AppUpdateService

        return AppUpdateService(get_runtime(request)).cancel_staged()

    @router.post("/app/update/rollback")
    async def app_update_rollback(request: Request) -> dict:
        from orng_led.updater.service import AppUpdateError, AppUpdateService

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        try:
            return AppUpdateService(get_runtime(request)).rollback(
                command_id=(body or {}).get("command_id"),
                simulate=False,
            )
        except AppUpdateError as exc:
            raise HTTPException(
                status_code=400, detail={"code": exc.code, "message": exc.message}
            ) from exc

    return router
