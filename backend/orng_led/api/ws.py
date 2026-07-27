"""WebSocket realtime state channel."""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from orng_led.api.runtime import AppRuntime
from orng_led.api.schemas import WsClientMessage

logger = logging.getLogger(__name__)


def build_ws_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        runtime: AppRuntime | None = getattr(websocket.app.state, "runtime", None)
        if runtime is None:
            await websocket.close(code=1013)
            return

        await websocket.accept()

        async def sender(payload: dict) -> None:
            await websocket.send_json(payload)

        runtime.subscribe(sender)
        try:
            await sender(
                {
                    "type": "hello",
                    "state": runtime.build_state().model_dump(mode="json"),
                }
            )
            while True:
                raw = await websocket.receive_json()
                try:
                    message = WsClientMessage.model_validate(raw)
                except ValidationError as exc:
                    await sender({"type": "error", "detail": str(exc)})
                    continue

                if message.type == "ping":
                    await sender(
                        {
                            "type": "state",
                            "state": runtime.build_state().model_dump(mode="json"),
                        }
                    )
                elif message.type == "request_state":
                    await sender(
                        {
                            "type": "state",
                            "state": runtime.build_state().model_dump(mode="json"),
                        }
                    )
                elif message.type == "focus_loss":
                    runtime.apply_focus_loss(client_command_id=message.client_command_id)
                    await runtime.broadcast_state()
                elif message.type == "visibility_hidden":
                    runtime.apply_visibility_hidden(
                        client_command_id=message.client_command_id,
                    )
                    await runtime.broadcast_state()
        except WebSocketDisconnect:
            runtime.on_ws_disconnect()
        except Exception:  # noqa: BLE001
            logger.exception("WebSocket handler failed")
            runtime.on_ws_disconnect()
        finally:
            runtime.unsubscribe(sender)

    return router
