"""WebSocket realtime state channel."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from orng_led.api.runtime import AppRuntime
from orng_led.api.schemas import WsClientMessage
from orng_led.input.contract import normalize_web_source_id

logger = logging.getLogger(__name__)

# Outbound hello/error only. Inbound ACK, release and control are applied
# before this queue. A slow socket keeps the newest messages, not an open list.
_COMMAND_LIMIT = 32


class SocketPump:
    """One writer per socket.

    Operator commands, errors and hello stay in a bounded queue. State
    snapshots keep only the newest frame, so a slow phone cannot pile up a
    second of pictures or stall the Art-Net loop.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self._commands: asyncio.Queue[dict] = asyncio.Queue()
        self._latest: dict | None = None
        self._wake = asyncio.Event()
        self._closed = False
        self._task = asyncio.create_task(self._run(), name="orng-ws-pump")

    def offer_command(self, payload: dict) -> None:
        if self._closed:
            return
        while self._commands.qsize() >= _COMMAND_LIMIT:
            try:
                self._commands.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._commands.put_nowait(payload)
        self._wake.set()

    def offer_state(self, payload: dict) -> None:
        if self._closed:
            return
        self._latest = payload
        self._wake.set()

    async def close(self) -> None:
        self._closed = True
        self._wake.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while not self._closed:
            if self._commands.empty() and self._latest is None:
                self._wake.clear()
                if self._commands.empty() and self._latest is None:
                    await self._wake.wait()
                    continue
            while not self._commands.empty():
                payload = self._commands.get_nowait()
                await self.websocket.send_json(payload)
            state = self._latest
            self._latest = None
            if state is not None:
                await self.websocket.send_json(state)


def _resolve_connection_source_id(websocket: WebSocket) -> str:
    """Prefer client-provided tab ``web:<id>``; fall back for legacy clients."""
    raw = websocket.query_params.get("source_id")
    validated = normalize_web_source_id(raw)
    if validated is not None:
        return validated
    return f"web:{uuid.uuid4().hex}"


def build_ws_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        runtime: AppRuntime | None = getattr(websocket.app.state, "runtime", None)
        if runtime is None:
            await websocket.close(code=1013)
            return

        await websocket.accept()
        source_id = _resolve_connection_source_id(websocket)
        pump = SocketPump(websocket)
        runtime.attach_socket(pump)
        try:
            pump.offer_command(
                {
                    "type": "hello",
                    "source_id": source_id,
                    "state": runtime.build_state().model_dump(mode="json"),
                }
            )
            while True:
                raw = await websocket.receive_json()
                try:
                    message = WsClientMessage.model_validate(raw)
                except ValidationError as exc:
                    pump.offer_command({"type": "error", "detail": str(exc)})
                    continue

                if message.type in {"ping", "request_state"}:
                    pump.offer_command(
                        {
                            "type": "state",
                            "state": runtime.build_state().model_dump(mode="json"),
                        }
                    )
                elif message.type == "focus_loss":
                    runtime.apply_focus_loss(
                        client_command_id=message.client_command_id,
                        source_id=source_id,
                    )
                    runtime.request_broadcast()
                elif message.type == "visibility_hidden":
                    runtime.apply_visibility_hidden(
                        client_command_id=message.client_command_id,
                        source_id=source_id,
                    )
                    runtime.request_broadcast()
        except WebSocketDisconnect:
            runtime.on_ws_disconnect(source_id=source_id)
        except Exception:  # noqa: BLE001
            logger.exception("WebSocket handler failed")
            runtime.on_ws_disconnect(source_id=source_id)
        finally:
            runtime.detach_socket(pump)
            await pump.close()

    return router
