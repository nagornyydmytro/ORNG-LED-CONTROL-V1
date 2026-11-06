"""Forward the desk's operator actions to the location executor.

The browser only talks to this computer. The device token stays here.
A failure does not switch the desk back to local Art-Net.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx
from websockets.asyncio.client import connect

from orng_led.cloud.credentials import load_device_token
from orng_led.cloud.sync_service import SceneSyncError, cloud_http_base
from orng_led.desktop.desk_role import desk_role

logger = logging.getLogger(__name__)

# Commands the Stage A venue agent already accepts. A heartbeat without
# capabilities is treated as this set, so a newer desk does not send
# seek/calibration/holds to a device that will reject them.
BASELINE_EXECUTOR_COMMANDS = frozenset(
    {
        "blackout",
        "select_preset",
        "select_pad",
        "strobe_press",
        "strobe_release",
        "sweep_press",
        "sweep_release",
        "vertical_sweep_press",
        "vertical_sweep_release",
        "face",
        "face_toggle",
        "set_live_fx_speed",
        "set_program_speed",
        "episode_nudge",
        "keyboard",
    }
)

EXECUTOR_CAPABILITIES = BASELINE_EXECUTOR_COMMANDS | frozenset(
    {
        "seek_episode",
        "release_source",
        "hold_keepalive",
        "auto_next_look",
        "calibration_begin",
        "calibration_set",
        "calibration_visible",
        "calibration_home",
        "calibration_end",
    }
)


# Show commands an older player already applied. A heartbeat that only lists
# feature flags does not unlock seek, calibration, or an unadvertised BPM.
_LEGACY_SHOW = BASELINE_EXECUTOR_COMMANDS - {"seek_episode"}
_FLAG_COMMANDS = {
    "blackout": frozenset({"blackout"}),
    "momentary_holds": frozenset(
        {
            "strobe_press",
            "strobe_release",
            "sweep_press",
            "sweep_release",
            "vertical_sweep_press",
            "vertical_sweep_release",
            "hold_keepalive",
            "release_source",
        }
    ),
    "bpm_sync": frozenset({"bpm_sync"}),
}


def executor_supports(command_type: str, view: dict | None) -> bool:
    if not isinstance(view, dict):
        return command_type in _LEGACY_SHOW
    named = view.get("operator_commands")
    if isinstance(named, list) and named:
        return command_type in {str(item) for item in named}
    flags = view.get("capabilities")
    if not isinstance(flags, list) or not flags:
        return command_type in _LEGACY_SHOW
    allowed = set(_LEGACY_SHOW)
    for flag in flags:
        allowed |= _FLAG_COMMANDS.get(str(flag), frozenset())
    return command_type in allowed


# Press, release and keyboard return when the session has the frame.
# A later device ack is recorded, but release is not queued behind that ack.
QUICK_COMMANDS = frozenset(
    {
        "strobe_press",
        "strobe_release",
        "sweep_press",
        "sweep_release",
        "vertical_sweep_press",
        "vertical_sweep_release",
        "keyboard",
        "hold_keepalive",
        "face_toggle",
        "release_source",
    }
)
APPLY_WAIT_S = 8.0
RESULT_WAIT_S = 2.5

# local window key → one server-minted socket. Not replayed after reconnect.
_links: dict[str, DeskLink] = {}
_link_guard = asyncio.Lock()


def desk_source_for(body: dict[str, Any]) -> str:
    raw = str(body.get("source_id") or "")
    if raw.startswith("cloud:desk:"):
        return raw
    if raw:
        return f"cloud:desk:{raw}"
    return "cloud:desk:window"


def settle_command_status(events: list[dict[str, Any]], command_id: str) -> str:
    """accepted is not a finished apply. Silence after the wait is unknown."""
    status = "unknown"
    for event in events:
        if str(event.get("command_id") or "") != command_id:
            continue
        value = str(event.get("status") or "")
        if value in {"applied", "failed", "unsupported"}:
            return value
        if value == "accepted":
            status = "accepted"
    return "unknown" if status == "accepted" else status


# One hold id per source and effect. Press and release must share it.
# A fresh client_command_id on release is not the hold the press armed, and the
# executor then keeps strobe/sweep running.
_LIVE_HOLD = {
    "strobe_press": "strobe",
    "strobe_release": "strobe",
    "sweep_press": "sweep",
    "sweep_release": "sweep",
    "vertical_sweep_press": "vertical_sweep",
    "vertical_sweep_release": "vertical_sweep",
}


# (source, effect) → hold id of the press that is still down.
_open_hold: dict[tuple[str, str], str] = {}


def _attach_hold(action: str, source: str, payload: dict[str, Any]) -> dict[str, Any]:
    effect = _LIVE_HOLD.get(action)
    if not effect:
        return payload
    explicit = str(payload.get("hold_id") or "")
    if action.endswith("_press"):
        hold_id = explicit or uuid.uuid4().hex
        _open_hold[(source, effect)] = hold_id
        payload["hold_id"] = hold_id
        return payload
    payload["hold_id"] = explicit or _open_hold.get((source, effect), "")
    return payload


def _with_command_id(body: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    command_id = str(body.get("client_command_id") or payload.get("command_id") or "")
    if command_id:
        payload["command_id"] = command_id
    return payload


_SHOW_ACTIONS = {
    "blackout",
    "select_preset",
    "select_pad",
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
    "keyboard",
    "release_source",
    "hold_keepalive",
    "auto_next_look",
}


class RemoteDeskError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def command_for_request(path: str, body: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Map a local operator request onto one executor command.

    ``block`` means the request would move this computer's output and must not run.
    """
    if path.startswith("/api/output/"):
        return ("block", {})
    if path in {"/api/commands/focus-loss", "/api/commands/visibility-hidden"}:
        return ("close_session", {})
    if path == "/api/commands/blackout":
        return ("blackout", {"enabled": bool(body.get("enabled", True))})
    if path == "/api/commands/select-preset":
        return ("select_preset", {"preset_id": body.get("preset_id")})
    if path == "/api/commands/seek-episode":
        return ("seek_episode", {"episode_index": body.get("episode_index")})
    if path == "/api/commands/live-fx-speed":
        payload = {}
        for key in ("strobe_speed", "sweep_speed"):
            if body.get(key) is not None:
                payload[key] = body[key]
        return ("set_live_fx_speed", payload)
    if path == "/api/commands/preview-speed":
        return ("set_program_speed", {"value": body.get("value")})
    if path == "/api/commands/bpm-sync":
        payload: dict[str, Any] = {"enabled": bool(body.get("enabled"))}
        if body.get("mode"):
            payload["mode"] = body.get("mode")
        if body.get("scope"):
            payload["scope"] = body.get("scope")
        if body.get("palette"):
            payload["palette"] = body.get("palette")
        if body.get("cast"):
            payload["cast"] = body.get("cast")
        if body.get("character"):
            payload["character"] = body.get("character")
        if body.get("intensity"):
            payload["intensity"] = body.get("intensity")
        if body.get("period") is not None:
            payload["period"] = body.get("period")
        return ("bpm_sync", payload)
    if path == "/api/commands/auto-next-look":
        return ("auto_next_look", {})
    if path == "/api/commands/face":
        return ("face", {"enabled": body.get("enabled"), "brightness": body.get("brightness")})
    if path == "/api/input/action":
        action = str(body.get("action") or "")
        source = f"cloud:desk:{body.get('source_id') or 'desk'}"
        if action == "blackout_toggle":
            return ("blackout_toggle", {"source": source})
        if action in {"episode_prev", "episode_next"}:
            return (
                "episode_nudge",
                {"delta": -1 if action == "episode_prev" else 1, "source": source},
            )
        if action == "face_toggle":
            return ("face_toggle", {"source": source})
        if action not in _SHOW_ACTIONS:
            return None
        return (
            action,
            _attach_hold(
                action,
                source,
                {
                    "edge": body.get("edge"),
                    "pad_slot": body.get("pad_slot"),
                    "preset_id": body.get("preset_id"),
                    "hold_id": body.get("hold_id"),
                    "source": source,
                },
            ),
        )
    if path == "/api/input/keyboard":
        return (
            "keyboard",
            {
                "code": body.get("code"),
                "type": body.get("type"),
                "repeat": bool(body.get("repeat")),
                "profile_id": body.get("profile_id"),
                "source": f"cloud:desk:{body.get('source_id') or 'desk'}",
            },
        )
    if path == "/api/input/button":
        from orng_led.input.contract import InputSource
        from orng_led.input.mapping import button_to_event

        event = button_to_event(
            int(body.get("button_id")),
            source=InputSource(str(body.get("source") or "ui")),
            edge=str(body.get("edge") or "pulse"),
            source_id=body.get("source_id"),
        )
        action = event.action.value
        source = f"cloud:desk:{body.get('source_id') or 'desk'}"
        if action == "blackout_toggle":
            return ("blackout_toggle", {"source": source})
        if action not in _SHOW_ACTIONS and action != "select_pad_slot":
            return None
        cloud_type = "select_pad" if action == "select_pad_slot" else action
        return (
            cloud_type,
            _attach_hold(
                action,
                source,
                {
                    "pad_slot": event.pad_slot,
                    "preset_id": event.preset_id,
                    "edge": event.edge,
                    "source": source,
                },
            ),
        )
    return None


async def open_executor_socket() -> Any:
    """Device-token control session. The worker mints cloud:desk:<uuid>."""
    token = load_device_token()
    if not token:
        raise RemoteDeskError(503, "Немає ключа виконавця. Локальний вивід не вмикався.")
    try:
        base = cloud_http_base()
    except SceneSyncError as exc:
        raise RemoteDeskError(503, "Виконавець недоступний. Режим не змінено.") from exc
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{ws_base}/device/executor/session"
    try:
        return await connect(
            url,
            additional_headers={"Authorization": f"Bearer {token}"},
            open_timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        raise RemoteDeskError(503, "Виконавець недоступний. Режим не змінено.") from exc


class DeskLink:
    """One control socket for one local window. Nothing is replayed on reconnect."""

    def __init__(self, local_key: str) -> None:
        self.local_key = local_key
        self.server_source: str | None = None
        self.acks: dict[str, str] = {}
        self._ws: Any = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._modes: dict[str, str] = {}
        self.alive = False

    async def start(self) -> None:
        self._ws = await open_executor_socket()
        self.alive = True
        self._task = asyncio.create_task(self._read())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=5)
        except TimeoutError as exc:
            await self.close()
            raise RemoteDeskError(
                503,
                "Сесія пульта не відкрилась. Локальний вивід не вмикався.",
            ) from exc
        if not self.server_source:
            await self.close()
            raise RemoteDeskError(503, "Сесія пульта без джерела. Локальний вивід не вмикався.")

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                self._on_message(json.loads(raw))
        except Exception:  # noqa: BLE001
            logger.debug("desk session closed", exc_info=True)
        finally:
            self.alive = False
            self._ready.clear()
            self._fail_open("unknown")

    def _on_message(self, msg: dict[str, Any]) -> None:
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        kind = str(msg.get("type") or "")
        if kind == "admin_hello":
            source = str(payload.get("source") or "")
            if source:
                self.server_source = source
            self._ready.set()
            return
        command_id = str(payload.get("command_id") or "")
        if kind == "command_result":
            status = str(payload.get("status") or "")
            if command_id:
                self.acks[command_id] = status
            mode = self._modes.get(command_id, "apply")
            if status in {"failed", "applied", "unsupported"} or (
                mode == "result" and status == "accepted"
            ):
                self._finish(command_id, status, payload)
            return
        if kind == "ack":
            status = str(payload.get("status") or "")
            if status == "accepted" or not command_id:
                return
            self.acks[command_id] = status
            if status in {"applied", "failed", "unsupported"}:
                self._finish(command_id, status, payload)
            return
        if kind == "error":
            if command_id:
                self.acks[command_id] = "failed"
                self._finish(command_id, "failed", payload)
            else:
                self._fail_open("failed")

    def _finish(self, command_id: str, status: str, payload: dict[str, Any]) -> None:
        fut = self._waiters.get(command_id)
        if fut is None or fut.done():
            return
        fut.set_result(
            {
                "ok": status == "applied",
                "status": status,
                "command_id": command_id,
                "error": payload.get("error") or payload.get("message"),
                "idempotent_replay": bool(payload.get("idempotent_replay")),
            }
        )

    def _fail_open(self, status: str) -> None:
        for command_id in list(self._waiters):
            self._finish(
                command_id,
                status,
                {"error": status},
            )

    async def command(
        self,
        command_type: str,
        payload: dict[str, Any],
        *,
        wait: str,
    ) -> dict[str, Any]:
        command_id = str(payload.get("command_id") or uuid.uuid4().hex)
        timeout = RESULT_WAIT_S if wait == "result" else APPLY_WAIT_S
        body = {
            key: value
            for key, value in payload.items()
            if key not in {"source", "command_id"}
        }
        body["command_id"] = command_id
        async with self._send_lock:
            existing = self._waiters.get(command_id)
            if existing is not None and not existing.done():
                fut: asyncio.Future[dict[str, Any]] | None = existing
                send = False
            else:
                fut = asyncio.get_running_loop().create_future()
                self._waiters[command_id] = fut
                self._modes[command_id] = wait
                send = True
                try:
                    await self._ws.send(json.dumps({"type": command_type, "payload": body}))
                except Exception as exc:  # noqa: BLE001
                    self.alive = False
                    self._finish(command_id, "unknown", {"error": "send_failed"})
                    raise RemoteDeskError(
                        503,
                        "Виконавець недоступний. Режим не змінено.",
                    ) from exc
        assert fut is not None
        if not send:
            try:
                return await asyncio.wait_for(asyncio.shield(fut), timeout)
            except TimeoutError:
                return {"ok": False, "status": "unknown", "command_id": command_id}
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            self._waiters.pop(command_id, None)
            return {"ok": False, "status": "unknown", "command_id": command_id}

    async def close(self) -> None:
        self.alive = False
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                logger.debug("desk session close failed", exc_info=True)
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._fail_open("unknown")


async def _link(local_key: str) -> DeskLink:
    async with _link_guard:
        current = _links.get(local_key)
        if current is not None and current.alive and current.server_source:
            return current
        if current is not None:
            await current.close()
        link = DeskLink(local_key)
        _links[local_key] = link
        try:
            await link.start()
        except Exception:
            _links.pop(local_key, None)
            await link.close()
            raise
        return link


async def close_desk_session(local_key: str, command_id: str) -> dict[str, Any]:
    """Release this window only, then drop the socket so old presses are not replayed."""
    link = _links.get(local_key)
    if link is None or not link.alive:
        return {"ok": False, "status": "closed", "command_id": command_id}
    try:
        result = await link.command(
            "release_source",
            {"command_id": command_id or uuid.uuid4().hex},
            wait="apply",
        )
    finally:
        await link.close()
        _links.pop(local_key, None)
    if result.get("status") != "applied":
        return {**result, "status": "closed", "ok": False}
    return result


async def forward_operator_command(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    local_key = str(payload.get("source") or "cloud:desk:window")
    command_id = str(payload.get("command_id") or "")
    if command_type == "close_session":
        return await close_desk_session(local_key, command_id)
    link = await _link(local_key)
    wait = "result" if command_type in QUICK_COMMANDS else "apply"
    return await link.command(command_type, payload, wait=wait)


async def watch_executor(runtime: Any) -> None:
    """Refresh the executor snapshot. Errors leave the saved role alone."""
    while not getattr(runtime, "_shutting_down", False):
        if desk_role(runtime.config_root) != "raspberry":
            runtime.executor_online = None
            await asyncio.sleep(2)
            continue
        try:
            runtime.executor_view = await fetch_executor_state()
            runtime.executor_online = bool((runtime.executor_view or {}).get("online"))
        except RemoteDeskError:
            runtime.executor_online = False
        except Exception:  # noqa: BLE001
            logger.debug("executor watch failed", exc_info=True)
            runtime.executor_online = False
        await asyncio.sleep(2)


async def fetch_executor_state() -> dict[str, Any]:
    token = load_device_token()
    if not token:
        raise RemoteDeskError(503, "Немає ключа виконавця")
    base = cloud_http_base()
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(
            f"{base}/device/executor/state",
            headers={"authorization": f"Bearer {token}"},
        )
    if response.status_code >= 400:
        raise RemoteDeskError(response.status_code, "Стан виконавця недоступний")
    data = response.json()
    return data if isinstance(data, dict) else {}
