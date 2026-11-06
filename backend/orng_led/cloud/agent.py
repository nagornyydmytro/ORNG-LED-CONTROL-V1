"""Outbound WSS device agent.

Enable with:
  ORNG_CLOUD_DEVICE_URL=wss://device.light.orng.wtf/ws/device
  and a credential via ``orng_led.cloud.credentials``.

Auth: ``Authorization: Bearer <token>`` only — never put the token in the URL.
Reconnect uses exponential backoff with jitter. Dispatches allowlisted
commands into AppRuntime / InputDispatcher. ``sync_scene_release`` runs the
shared SceneSyncService and ACKs ``applied`` only after a real apply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from orng_led.api.runtime import CommandConflictError
from orng_led.cloud.credentials import credential_status, load_device_token
from orng_led.cloud.hold_lease import CloudHoldLeases, CloudLease
from orng_led.cloud.metadata import load_cloud_metadata
from orng_led.cloud.sync_service import SceneSyncError, SceneSyncService
from orng_led.input.contract import InputAction, InputEvent, InputSource
from orng_led.input.dispatcher import DispatchResult

if TYPE_CHECKING:
    from orng_led.api.runtime import AppRuntime

logger = logging.getLogger(__name__)


def bpm_sync_args(payload: dict[str, Any]) -> dict[str, Any]:
    """Musical fields are optional. Missing ``enabled`` must not turn sync off."""
    args: dict[str, Any] = {
        "mode": payload.get("mode") or None,
        "scope": payload.get("scope") or None,
        "palette": payload.get("palette") or None,
        "cast": payload.get("cast") or None,
        "character": payload.get("character") or None,
        "intensity": payload.get("intensity") or None,
        "period": payload.get("period"),
    }
    if "par_rhythm" in payload:
        args["par_rhythm"] = bool(payload.get("par_rhythm"))
    if "accent" in payload:
        try:
            args["accent"] = max(0.0, min(1.0, float(payload.get("accent"))))
        except (TypeError, ValueError):
            args["accent"] = 0.0
    if "enabled" in payload:
        args["enabled"] = bool(payload.get("enabled"))
    return args

PROTOCOL_VERSION = 1
ALLOWED = frozenset(
    {
        "request_state",
        "activate_scene",
        "sync_scene_release",
        "select_preset",
        "select_pad",
        "blackout",
        "arm",
        "disarm",
        "strobe_press",
        "strobe_release",
        "sweep_press",
        "sweep_release",
        "vertical_sweep_press",
        "vertical_sweep_release",
        "face_toggle",
        "face",
        "episode_nudge",
        "seek_episode",
        "speed",
        "set_program_speed",
        "bpm_sync",
        "auto_next_look",
        "set_live_fx_speed",
        "keyboard",
        "release_source",
        "hold_keepalive",
        "session_hello",
        "check_app_update",
        "stage_app_update",
        "apply_app_update",
        "cancel_staged_update",
        "rollback_app_update",
        # Phase 4 operational (fixed allowlist — no shell/reboot/apt)
        "request_diagnostics",
        "restart_runtime",
        "calibration_begin",
        "calibration_set",
        "calibration_visible",
        "calibration_home",
        "calibration_end",
    }
)
MOMENTARY_PRESS = frozenset(
    {"strobe_press", "sweep_press", "vertical_sweep_press", "face_toggle"}
)
# What a desk may drive. Update, restart and credentials are not in this set.
DESK_OPERATOR_COMMANDS = frozenset(
    {
        "select_preset",
        "select_pad",
        "blackout",
        "strobe_press",
        "strobe_release",
        "sweep_press",
        "sweep_release",
        "vertical_sweep_press",
        "vertical_sweep_release",
        "face_toggle",
        "face",
        "episode_nudge",
        "seek_episode",
        "speed",
        "set_program_speed",
        "bpm_sync",
        "auto_next_look",
        "set_live_fx_speed",
        "keyboard",
        "release_source",
        "hold_keepalive",
    }
)


@dataclass
class CloudOutcome:
    status: str = "applied"
    reason: str | None = None
    replay: bool = False


def _command_fingerprint(msg_type: str, payload: dict[str, Any]) -> str:
    body = {key: payload.get(key) for key in sorted(payload) if key != "command_id"}
    return json.dumps({"type": msg_type, "body": body}, sort_keys=True, default=str)


def cloud_enabled() -> bool:
    return bool(os.environ.get("ORNG_CLOUD_DEVICE_URL", "").strip())


def cloud_device_url() -> str:
    return os.environ.get("ORNG_CLOUD_DEVICE_URL", "").strip()


class CloudDeviceAgent:
    """Minimal asyncio WebSocket client for the Cloudflare device host."""

    def __init__(self, runtime: AppRuntime) -> None:
        self.runtime = runtime
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._source_prefix = "cloud:agent"
        self.installation_id: str | None = None
        self.connected = False
        self.last_error: str | None = None
        self._seen_commands: dict[str, str] = {}
        self._sync = SceneSyncService(runtime)
        self._calibration_until = 0.0
        self._leases = CloudHoldLeases()
        self._key_profile: dict[str, str] = {}

    @property
    def status(self) -> dict[str, Any]:
        # Never include token or URL with query credentials.
        cred = credential_status()
        return {
            "enabled": cloud_enabled(),
            "connected": self.connected,
            "url_configured": bool(cloud_device_url()),
            "installation_id": self.installation_id,
            "last_error": self.last_error,
            "has_token": bool(cred.get("has_token")),
            "token_storage": cred.get("storage"),
        }

    def start(self) -> None:
        if not cloud_enabled():
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="orng-cloud-agent")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self.connected = False

    async def _run_loop(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            token = load_device_token()
            url = cloud_device_url()
            if not token or not url:
                self.last_error = "missing token or ORNG_CLOUD_DEVICE_URL"
                await asyncio.sleep(5)
                continue
            try:
                # Complete pending rotation before connecting (may replace token).
                from orng_led.cloud.rotation import RotationError, complete_pending_rotation

                try:
                    rotated = await complete_pending_rotation()
                    if rotated:
                        token = load_device_token() or token
                except RotationError as exc:
                    # Keep going with current credential; retry rotation next loop.
                    self.last_error = f"rotation: {exc.code}"
                    logger.warning("cloud rotation deferred: %s", exc.code)

                await self._session(url, token)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._end_calibration_session()
                # Never log the token; strip any accidental query.
                safe = str(exc)
                if "token=" in safe.lower():
                    safe = "websocket error (credentials redacted)"
                self.last_error = safe
                self.connected = False
                logger.warning("cloud agent disconnected: %s", safe)
            delay = min(30.0, 0.5 * (2**attempt))
            jitter = delay * 0.2 * (random.random() * 2 - 1)
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(0.2, delay + jitter))
                break
            except TimeoutError:
                continue

    async def _session(self, url: str, token: str) -> None:
        try:
            import websockets  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "websockets package required for cloud agent "
                "(pip install 'orng-led-control[cloud]')"
            ) from exc

        # Strip any legacy ?token= from configured URL — never send credentials in query.
        clean_url = url.split("?", 1)[0]
        headers = {"Authorization": f"Bearer {token}"}
        async with websockets.connect(
            clean_url,
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
        ) as ws:
            self.connected = True
            self.last_error = None
            # Fresh session: do not replay momentary presses from prior connection.
            self._seen_commands.clear()
            await self._flush_pending_update_result(ws)
            heartbeat = asyncio.create_task(self._heartbeat(ws))
            try:
                async for raw in ws:
                    if self._stop.is_set():
                        break
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    await self._handle_message(ws, msg)
            finally:
                heartbeat.cancel()
                self.connected = False
                self._drop_all_cloud_holds()

    async def _heartbeat(self, ws: Any) -> None:
        while not self._stop.is_set():
            await self._heartbeat_once(ws)
            await asyncio.sleep(2)

    def _heartbeat_disk(self, scene_id: str) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        """Files, thermal and venue probe. Never on the frame loop."""
        meta = load_cloud_metadata()
        scene_meta = meta.scene(scene_id)
        from orng_led.raspberry.diagnostics import heartbeat_venue_fields
        from orng_led.updater.service import AppUpdateService

        upd = AppUpdateService(self.runtime).status()
        venue = heartbeat_venue_fields(self.runtime)
        return scene_meta, upd, venue

    def _heartbeat_message(self) -> str:
        """Full cloud snapshot. CPU and disk stay off the frame loop."""
        state = self.runtime.build_state(reuse=True)
        scene_meta, upd, venue = self._heartbeat_disk(state.scene_id)
        from orng_led.version import app_version, build_id, updater_protocol_version

        payload = {
                "uptime_s": venue.get("service_uptime_s"),
                "armed": bool(state.output.armed),
                "blackout": bool(state.engine.blackout),
                "transport": state.output.transport,
                "udp_active": bool(state.output.udp_active),
                "current_scene_id": state.scene_id,
                "scene_name": _scene_name(self.runtime, state.scene_id),
                "pad_presets": _pad_presets(self.runtime),
                "preset_id": state.engine.preset_id,
                "preset_time_s": state.engine.preset_time_s,
                "episode_index": state.engine.episode_index,
                "episode_time_s": state.engine.episode_time_s,
                "episode_count": state.engine.episode_count,
                "cycle_duration_s": state.engine.cycle_duration_s,
                "face_brightness": state.engine.face_brightness,
                "preview_speed": state.preview_speed,
                "bpm": state.bpm,
                "strobe_speed": state.engine.strobe_speed,
                "sweep_speed": state.engine.sweep_speed,
                "stage": state.simulator.model_dump(mode="json"),
                "face_on": bool(state.engine.face_on),
                "strobe_held": bool(state.engine.strobe_held),
                "sweep_active": bool(state.engine.sweep_active),
                "vertical_sweep_active": bool(state.engine.vertical_sweep_active),
                "current_release_version": scene_meta.applied_version,
                "current_content_hash": scene_meta.applied_hash,
                "last_synced_hash": scene_meta.last_synced_hash,
                "last_synced_version": scene_meta.last_synced_version,
                "desired_scene_version": scene_meta.desired_version,
                "desired_content_hash": scene_meta.desired_hash,
                "sync_status": scene_meta.sync_status,
                "app_version": app_version(),
                "build_id": build_id(),
                "platform": upd.get("platform"),
                "arch": upd.get("arch"),
                "install_mode": upd.get("install_mode"),
                "updater_protocol_version": updater_protocol_version(),
                "current_app_release": upd.get("current_release"),
                "desired_app_release": {
                    "version": upd.get("desired_version"),
                    "build_id": upd.get("desired_build_id"),
                },
                "update_state": upd.get("update_state"),
                "update_progress": upd.get("update_progress"),
                "last_update_error": upd.get("last_error"),
                "last_successful_update": {
                    "version": upd.get("last_successful_version"),
                },
                "rollback": upd.get("previous_release"),
                "staged_app_version": upd.get("staged_version"),
                "staged_build_id": upd.get("staged_build_id"),
                "capabilities": [
                    "scene_sync",
                    "momentary_holds",
                    "blackout",
                    "app_update",
                    "diagnostics",
                    "restart_runtime",
                    "bpm_sync",
                    "auto_director",
                ],
                "operator_commands": sorted(DESK_OPERATOR_COMMANDS),
                "last_error": state.output.last_error,
                "beam_calibration": state.beam_calibration,
                **venue,
        }
        return json.dumps(
            {
                "protocol_version": PROTOCOL_VERSION,
                "message_id": str(uuid.uuid4()),
                "type": "heartbeat",
                "device_id": self.installation_id or "unknown",
                "sent_at": _now(),
                "payload": payload,
            }
        )

    async def _heartbeat_once(self, ws: Any) -> None:
        self._expire_calibration_lease()
        # Expire before the send. A stuck socket must not keep a cloud hold alive.
        for lease in self._leases.expired():
            self._release_cloud_lease(lease)
        try:
            text = await asyncio.to_thread(self._heartbeat_message)
        except Exception:  # noqa: BLE001
            logger.debug("heartbeat build failed", exc_info=True)
            return
        try:
            await asyncio.wait_for(ws.send(text), timeout=1.5)
        except Exception:  # noqa: BLE001 — lease checks must not depend on a successful send
            logger.debug("heartbeat send failed", exc_info=True)

    async def _handle_message(self, ws: Any, msg: dict[str, Any]) -> None:
        msg_type = str(msg.get("type", ""))
        if msg_type not in ALLOWED and msg_type not in {"session_hello"}:
            await self._ack(ws, msg, "failed", "not_allowlisted", msg_type)
            return
        if msg_type == "session_hello":
            self.installation_id = str(msg.get("device_id") or self.installation_id)
            return

        payload = dict(msg.get("payload") or {})
        command_id = str(payload.get("command_id") or msg.get("message_id") or "")
        fingerprint = _command_fingerprint(msg_type, payload)
        if command_id and command_id in self._seen_commands and msg_type not in MOMENTARY_PRESS:
            if self._seen_commands[command_id] != fingerprint:
                await self._ack(ws, msg, "failed", "command_conflict", "command_id reused")
                return
            await self._ack(ws, msg, "applied", None, None, idempotent=True)
            return

        try:
            outcome = await self._dispatch(msg_type, payload, command_id=command_id)
            if outcome == "deferred" or (
                isinstance(outcome, CloudOutcome) and outcome.status == "deferred"
            ):
                # e.g. app update restarting — applied after reconnect + health.
                await self._ack(ws, msg, "accepted")
                return
            if isinstance(outcome, CloudOutcome) and outcome.status != "applied":
                await self._ack(ws, msg, outcome.status, outcome.reason, outcome.reason)
                return
            if command_id and msg_type not in MOMENTARY_PRESS:
                self._seen_commands[command_id] = fingerprint
            replay = isinstance(outcome, CloudOutcome) and outcome.replay
            await self._ack(ws, msg, "applied", None, None, idempotent=replay)
        except SceneSyncError as exc:
            await self._ack(ws, msg, "failed", exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            await self._ack(ws, msg, "failed", "apply_error", str(exc))

    async def _dispatch(
        self,
        msg_type: str,
        payload: dict[str, Any],
        *,
        command_id: str = "",
    ) -> CloudOutcome | str | None:
        source = str(payload.get("source") or self._source_prefix)
        if not source.startswith("cloud:"):
            source = f"cloud:{source}"

        if msg_type == "request_state":
            return None
        if msg_type == "release_source":
            self.runtime.input_dispatcher.force_release_source(source)
            self._leases.take_source(source)
            self._key_profile.pop(source, None)
            return None
        if msg_type == "hold_keepalive":
            token = str(payload.get("hold_id") or "")
            code = str(payload.get("code") or "")
            if token:
                self._leases.refresh(token, source)
            if code:
                self._leases.refresh_effect(source, f"key:{code}")
            return None
        if msg_type == "blackout":
            enabled = payload.get("enabled")
            if enabled is None:
                enabled = payload.get("on", True)
            try:
                _state, replay = self.runtime.apply_blackout(
                    bool(enabled),
                    client_command_id=command_id or None,
                )
            except CommandConflictError as exc:
                raise SceneSyncError(exc.code, str(exc)) from exc
            return CloudOutcome(replay=replay)
        if msg_type == "arm":
            self.runtime.arm_output()
            return None
        if msg_type == "disarm":
            self.runtime.disarm_output()
            return None
        if msg_type == "request_diagnostics":
            from orng_led.raspberry.diagnostics import build_venue_diagnostics

            # Result travels via update_result-style ack detail / next heartbeat.
            self.runtime._last_diagnostics = build_venue_diagnostics(self.runtime)  # noqa: SLF001
            return None
        if msg_type == "restart_runtime":
            # Safe lighting then request helper restart — never reboot the Pi OS.
            from orng_led.updater.service import AppUpdateService

            svc = AppUpdateService(self.runtime)
            try:
                svc._enter_safe_lighting(require_confirm=True)  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001
                raise SceneSyncError("safe_lighting_failed", str(exc)) from exc
            # Soft flag for helper/supervisor; API path can flush ack first.
            self.runtime._updater_shutdown_requested = True
            os.environ["ORNG_RESTART_REASON"] = "remote_restart_runtime"
            return "deferred"
        if msg_type == "activate_scene":
            scene_id = str(payload.get("scene_id", ""))
            cid = str(payload.get("command_id") or "") or None
            self.runtime.activate_scene(scene_id, client_command_id=cid)
            return None
        if msg_type == "sync_scene_release":
            scene_id = payload.get("scene_id")
            version = payload.get("version")
            # Respect device mode: desktop may return conflict/dirty without apply.
            # Only ACK applied/synced as success; otherwise failed so DO redelivers.
            # Apply only while the show is already black. Staging must not stop a live look.
            blackout = bool(self.runtime.engine.overlays.blackout)
            result = await self._sync.sync_assigned_release(
                force=False,
                scene_id=str(scene_id) if scene_id else None,
                version=int(version) if version is not None else None,
                command_id=command_id or None,
                auto_apply=None,
                prefer_apply=blackout,
            )
            status = str(result.get("status", ""))
            if status in {"synced", "applied"}:
                return None
            # Not applied yet. A failed ACK lets the command be delivered again
            # after blackout. It does not change the live look.
            raise SceneSyncError(
                status or "sync_failed",
                f"sync did not apply: {status}",
            )
        if msg_type in {
            "check_app_update",
            "stage_app_update",
            "apply_app_update",
            "cancel_staged_update",
            "rollback_app_update",
        }:
            return await self._dispatch_app_update(msg_type, payload, command_id=command_id)
        if msg_type == "select_preset":
            preset_id = str(payload.get("preset_id", ""))
            self.runtime.apply_select_preset(preset_id)
            return None
        if msg_type == "select_pad":
            slot = int(payload.get("pad_slot", payload.get("slot", 0)))
            self.runtime.apply_select_pad_slot(slot)
            return None
        if msg_type == "face_toggle":
            self._input(InputAction.FACE_TOGGLE, source)
            return None
        if msg_type == "face":
            brightness = payload.get("brightness")
            self.runtime.apply_face(
                bool(payload.get("enabled", True)),
                float(brightness) if brightness is not None else None,
                client_command_id=command_id or None,
            )
            return None
        if msg_type == "set_program_speed":
            self.runtime.apply_preview_speed(
                float(payload.get("value", 1)),
                client_command_id=command_id or None,
            )
            return None
        if msg_type == "bpm_sync":
            self.runtime.apply_bpm_sync(
                **bpm_sync_args(payload),
                client_command_id=command_id or None,
            )
            return None
        if msg_type == "auto_next_look":
            self.runtime.apply_next_auto_look(client_command_id=command_id or None)
            return None
        if msg_type == "episode_nudge":
            delta = int(payload.get("delta", 1))
            self.runtime.apply_nudge_episode(delta)
            return None
        if msg_type == "seek_episode":
            index = int(payload.get("episode_index", payload.get("index", 0)))
            self.runtime.apply_seek_episode(index, client_command_id=command_id or None)
            return None
        if msg_type == "speed":
            kind = str(payload.get("kind", "program"))
            delta = float(payload.get("delta", 0.25))
            if kind == "live_fx":
                self.runtime.apply_nudge_live_fx_speed(delta)
            else:
                self.runtime.apply_nudge_program_speed(delta)
            return None
        if msg_type == "set_live_fx_speed":
            strobe = payload.get("strobe_speed")
            sweep = payload.get("sweep_speed")
            self.runtime.apply_live_fx_speeds(
                strobe_speed=float(strobe) if strobe is not None else None,
                sweep_speed=float(sweep) if sweep is not None else None,
                persist=bool(payload.get("persist", False)),
                client_command_id=command_id or None,
            )
            return None
        if msg_type == "keyboard":
            from orng_led.input.adapters import PROFILE_QWERTY, adapter_for_profile

            if self.runtime.input_dispatcher is None:
                raise SceneSyncError("input_unavailable", "Input dispatcher not ready")
            code = str(payload.get("code") or "")
            event_type = str(payload.get("type") or "keydown")
            if not code or event_type not in {"keydown", "keyup"}:
                raise SceneSyncError("keyboard_invalid", "code and type are required")
            profile_id = str(payload.get("profile_id") or PROFILE_QWERTY)
            repeat = bool(payload.get("repeat"))
            if event_type == "keydown" and not repeat:
                previous = self._key_profile.get(source)
                if previous and previous != profile_id:
                    for old in self._leases.take_prefix(source, "key:"):
                        self._release_cloud_lease(old)
                self._key_profile[source] = profile_id
            elif event_type == "keyup":
                self._leases.release_token(
                    f"{source}:{profile_id}:{code}",
                    source=source,
                    effect=f"key:{code}",
                )
            try:
                adapter = adapter_for_profile(self.runtime.scene_controls, profile_id)
            except ValueError as exc:
                raise SceneSyncError("unknown_profile", str(exc)) from exc
            events = adapter.handle_raw(
                {
                    "code": code,
                    "type": event_type,
                    "repeat": bool(payload.get("repeat")),
                    "source_id": source,
                }
            )
            results = [self.runtime.input_dispatcher.dispatch(event) for event in events]
            if event_type == "keyup":
                return CloudOutcome()
            if event_type == "keydown" and not repeat:
                if any(result.accepted for result in results):
                    self._leases.arm(
                        source,
                        f"key:{code}",
                        f"{source}:{profile_id}:{code}",
                        profile_id=profile_id,
                    )
                    return CloudOutcome()
                reason = next((result.reason for result in results if result.reason), None)
                return CloudOutcome(status="rejected", reason=reason or "keyboard_rejected")
            return CloudOutcome()
        if msg_type in {
            "calibration_begin",
            "calibration_set",
            "calibration_visible",
            "calibration_home",
            "calibration_end",
        }:
            return self._dispatch_calibration(msg_type, payload)

        press_map = {
            "strobe_press": InputAction.STROBE_PRESS,
            "strobe_release": InputAction.STROBE_RELEASE,
            "sweep_press": InputAction.SWEEP_PRESS,
            "sweep_release": InputAction.SWEEP_RELEASE,
            "vertical_sweep_press": InputAction.VERTICAL_SWEEP_PRESS,
            "vertical_sweep_release": InputAction.VERTICAL_SWEEP_RELEASE,
        }
        if msg_type in press_map:
            effect = {
                "strobe_press": "strobe",
                "strobe_release": "strobe",
                "sweep_press": "sweep",
                "sweep_release": "sweep",
                "vertical_sweep_press": "vertical_sweep",
                "vertical_sweep_release": "vertical_sweep",
            }[msg_type]
            hold = payload.get("hold") if isinstance(payload.get("hold"), dict) else {}
            token = str(payload.get("hold_id") or hold.get("hold_id") or "")
            if msg_type.endswith("_press"):
                result = self._input(press_map[msg_type], source, token or None)
                if not result.accepted:
                    return CloudOutcome(status="rejected", reason=result.reason)
                if token:
                    self._leases.arm(source, effect, token)
                return CloudOutcome()
            # Release only the hold this finger started. An older id must not
            # stop a newer press of the same effect from this source. A matching
            # token on another source is a different press.
            current = self._leases.current_token(source, effect)
            if token and current and current != token:
                return CloudOutcome()
            if token:
                self._leases.release_token(token, source=source, effect=effect)
            self._input(press_map[msg_type], source, token or None)
            return CloudOutcome()

        raise ValueError(f"unhandled allowlisted command: {msg_type}")

    def _require_blackout_for_test(self) -> None:
        if not bool(self.runtime.engine.overlays.blackout):
            raise SceneSyncError(
                "unsafe_show",
                "Фізичний тест доступний лише під blackout, щоб не гасити шоу",
            )

    def _touch_calibration_lease(self) -> None:
        self._calibration_until = time.monotonic() + 20.0

    def _expire_calibration_lease(self) -> None:
        """Pi-local lease. Does not depend on a heartbeat reaching the Worker."""
        if self._calibration_until and time.monotonic() >= self._calibration_until:
            self._end_calibration_session()

    def _end_calibration_session(self) -> None:
        self._calibration_until = 0.0
        session = getattr(self.runtime, "beam_calibration", None)
        if session is not None and getattr(session, "active", False):
            self.runtime.end_beam_calibration_test()

    def _dispatch_calibration(self, msg_type: str, payload: dict[str, Any]) -> None:
        try:
            self._apply_calibration(msg_type, payload)
        except ValueError as exc:
            raise SceneSyncError("calibration_rejected", str(exc)) from exc

    def _apply_calibration(self, msg_type: str, payload: dict[str, Any]) -> None:
        self._expire_calibration_lease()
        owner = str(payload.get("source") or "") or None
        session_id = payload.get("session_id")
        session_id = str(session_id) if session_id else None
        if msg_type != "calibration_end":
            self._require_blackout_for_test()
        if msg_type == "calibration_begin":
            fixture_id = str(payload.get("fixture_id") or "")
            if not fixture_id:
                raise SceneSyncError("calibration_invalid", "fixture_id is required")
            self.runtime.begin_beam_calibration_test(
                fixture_id,
                confirmed=bool(payload.get("confirmed")),
                owner=owner,
            )
            self._touch_calibration_lease()
            return None
        if msg_type == "calibration_set":
            pan = payload.get("pan")
            tilt = payload.get("tilt")
            self.runtime.set_beam_calibration_position(
                pan=float(pan) if pan is not None else None,
                tilt=float(tilt) if tilt is not None else None,
                session_id=session_id,
                owner=owner,
            )
            self._touch_calibration_lease()
            return None
        if msg_type == "calibration_visible":
            self.runtime.set_beam_calibration_visible(
                enabled=bool(payload.get("enabled")),
                confirmed=bool(payload.get("confirmed")),
                session_id=session_id,
                owner=owner,
            )
            self._touch_calibration_lease()
            return None
        if msg_type == "calibration_home":
            self.runtime.beam_calibration_go_home(session_id=session_id, owner=owner)
            self._touch_calibration_lease()
            return None
        self._end_calibration_session()
        return None

    async def _dispatch_app_update(
        self,
        msg_type: str,
        payload: dict[str, Any],
        *,
        command_id: str,
    ) -> str | None:
        from orng_led.updater.service import AppUpdateError, AppUpdateService

        svc = AppUpdateService(self.runtime)
        try:
            if msg_type == "check_app_update":
                await svc.check_assigned()
                return None
            if msg_type == "stage_app_update":
                await svc.stage(
                    version=payload.get("version"),
                    build_id_wanted=payload.get("build_id"),
                    command_id=command_id or None,
                )
                return None
            if msg_type == "apply_app_update":
                force = bool(payload.get("force") or payload.get("force_unsafe"))
                # Never simulate in production agent — helper owns restart/health/ACK.
                result = svc.apply(
                    expected_version=str(payload.get("version") or ""),
                    expected_build_id=str(payload.get("build_id") or ""),
                    expected_content_sha256=str(payload.get("content_sha256") or "") or None,
                    force_unsafe=force,
                    command_id=command_id or None,
                    simulate=False,
                )
                status = str(result.get("status", ""))
                if status in {"restarting", "waiting_for_safe_state"}:
                    return "deferred"
                if status not in {"applied", "rolled_back"}:
                    raise AppUpdateError(status or "apply_failed", status)
                return None
            if msg_type == "cancel_staged_update":
                svc.cancel_staged()
                return None
            if msg_type == "rollback_app_update":
                # Production: helper owns pointer swap + process restart (like apply).
                result = svc.rollback(command_id=command_id or None, simulate=False)
                status = str(result.get("status", ""))
                if status in {"restarting", "rolling_back"}:
                    return "deferred"
                if status not in {"rolled_back", "failed"}:
                    raise AppUpdateError(status or "rollback_failed", status)
                return None
        except AppUpdateError as exc:
            raise SceneSyncError(exc.code, exc.message) from exc
        return None

    async def _flush_pending_update_result(self, ws: Any) -> None:
        """After reconnect, report deferred app-update outcome with original command_id.

        Pending result is cleared only after the send succeeds (server will 2xx /
        process the frame). Network failure keeps pending for retry.
        """
        from orng_led.updater.service import AppUpdateService

        svc = AppUpdateService(self.runtime)
        pending = svc.peek_pending_result()
        if not pending:
            return
        status = str(pending.get("status") or "")
        # Do not finalize while still restarting — helper has not finished.
        if status in {"restarting", "applying", "health_check"}:
            return
        command_id = str(pending.get("command_id") or "")
        if status == "rolled_back":
            ack_status = "rolled_back"
        elif status == "failed":
            ack_status = "failed"
        elif status == "applied":
            ack_status = "applied"
        else:
            return
        body = {
            "protocol_version": PROTOCOL_VERSION,
            "message_id": str(uuid.uuid4()),
            "type": "update_result",
            "device_id": self.installation_id or "unknown",
            "sent_at": _now(),
            "payload": {
                "command_id": command_id,
                "status": ack_status,
                "detail": pending,
            },
        }
        await ws.send(json.dumps(body))
        # Cleared only after successful send (frames are fire-and-forget on WS;
        # reconnects redeliver idempotently via command_id).
        svc.clear_pending_result()

    def _drop_all_cloud_holds(self) -> None:
        for lease in self._leases.take_all():
            self._release_cloud_lease(lease)
        self._key_profile.clear()

    def _release_cloud_lease(self, lease: CloudLease) -> None:
        if lease.effect.startswith("key:"):
            code = lease.effect[4:]
            self._dispatch_keyboard(
                source=lease.source,
                code=code,
                event_type="keyup",
                profile_id=lease.profile_id or "qwerty",
            )
            return
        action = {
            "strobe": InputAction.STROBE_RELEASE,
            "sweep": InputAction.SWEEP_RELEASE,
            "vertical_sweep": InputAction.VERTICAL_SWEEP_RELEASE,
        }.get(lease.effect)
        if action is None:
            return
        self._input(action, lease.source, lease.token)

    def _dispatch_keyboard(
        self,
        *,
        source: str,
        code: str,
        event_type: str,
        profile_id: str,
    ) -> None:
        from orng_led.input.adapters import adapter_for_profile

        if self.runtime.input_dispatcher is None or not code:
            return
        try:
            adapter = adapter_for_profile(self.runtime.scene_controls, profile_id)
        except ValueError:
            return
        events = adapter.handle_raw(
            {
                "code": code,
                "type": event_type,
                "repeat": False,
                "source_id": source,
            }
        )
        for event in events:
            self.runtime.input_dispatcher.dispatch(event)

    def _input(
        self,
        action: InputAction,
        source_id: str,
        token: str | None = None,
    ) -> DispatchResult:
        event = InputEvent(
            action=action,
            source=InputSource.UI,
            source_id=source_id,
            hold_token=token,
        )
        dispatcher = self.runtime.input_dispatcher
        if dispatcher is None:
            return DispatchResult(accepted=False, reason="input_unavailable")
        return dispatcher.dispatch(event)

    def _ack_snapshot(self) -> dict[str, Any]:
        engine = getattr(self.runtime, "engine", None)
        overlays = getattr(engine, "overlays", None)
        bpm = getattr(self.runtime, "bpm", None)
        return {
            "blackout": bool(getattr(overlays, "blackout", False)),
            "strobe_held": bool(getattr(overlays, "strobe_held", False)),
            "sweep_held": bool(getattr(overlays, "sweep_held", False)),
            "auto": bool(getattr(bpm, "enabled", False) and getattr(bpm, "scope", "") == "auto"),
        }

    async def _ack(
        self,
        ws: Any,
        msg: dict[str, Any],
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        *,
        idempotent: bool = False,
    ) -> None:
        payload_in = dict(msg.get("payload") or {})
        command_id = str(payload_in.get("command_id") or msg.get("message_id") or "")
        body = {
            "protocol_version": PROTOCOL_VERSION,
            "message_id": str(uuid.uuid4()),
            "type": "ack",
            "device_id": self.installation_id or str(msg.get("device_id") or "unknown"),
            "sent_at": _now(),
            "payload": {
                "command_id": command_id,
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
                "idempotent_replay": idempotent,
                **self._ack_snapshot(),
            },
        }
        await ws.send(json.dumps(body))


def _scene_name(runtime: Any, fallback: str | None) -> str | None:
    value = getattr(runtime, "scene_name", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _pad_presets(runtime: Any) -> list[str]:
    show = getattr(runtime, "show", None)
    app = getattr(show, "app", None) if show is not None else None
    pads = getattr(app, "pad_presets", None) if app is not None else None
    if not pads:
        return []
    return [str(x) for x in list(pads)[:9]]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def map_command_to_action(command_type: str) -> str | None:
    """Test helper: map protocol command → dispatcher concept."""
    mapping = {
        "strobe_press": "strobe_press",
        "strobe_release": "strobe_release",
        "sweep_press": "sweep_press",
        "sweep_release": "sweep_release",
        "vertical_sweep_press": "vertical_sweep_press",
        "vertical_sweep_release": "vertical_sweep_release",
        "face_toggle": "face_toggle",
        "select_preset": "select_preset",
        "select_pad": "select_pad_slot",
        "blackout": "blackout",
        "arm": "arm",
        "disarm": "disarm",
        "release_source": "release_source",
        "episode_nudge": "episode_nudge",
        "speed": "speed",
        "sync_scene_release": "sync_scene_release",
    }
    return mapping.get(command_type)
