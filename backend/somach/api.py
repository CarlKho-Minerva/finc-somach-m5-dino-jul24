"""FastAPI HTTP controls and the batched real-time WebSocket."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import Settings
from .runtime import SomachRuntime

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Client:
    websocket: WebSocket
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WebSocketHub:
    """Fan one 50 Hz telemetry stream out to all local dashboards."""

    def __init__(self):
        self._clients: dict[int, Client] = {}

    async def add(self, websocket: WebSocket) -> Client:
        await websocket.accept()
        client = Client(websocket)
        self._clients[id(websocket)] = client
        return client

    def remove(self, websocket: WebSocket) -> None:
        self._clients.pop(id(websocket), None)

    async def send(self, client: Client, payload: dict) -> None:
        async with client.lock:
            await client.websocket.send_json(payload)

    async def broadcast(self, payload: dict) -> None:
        stale: list[int] = []
        for client_id, client in tuple(self._clients.items()):
            try:
                # A frozen browser must not stall acquisition or key injection.
                await asyncio.wait_for(self.send(client, payload), timeout=0.1)
            # Socket implementations surface disconnects through several
            # backend-specific exception classes. Any send failure makes this
            # client stale; acquisition must continue for the other clients.
            except Exception:  # noqa: BLE001
                stale.append(client_id)
        for client_id in stale:
            self._clients.pop(client_id, None)


class ThresholdBody(BaseModel):
    value: float = Field(ge=0.0, le=4_095.0)


class ArmedBody(BaseModel):
    armed: bool


class RecordingMarkBody(BaseModel):
    label: Literal["jump", "artifact"]


class ModelActivateBody(BaseModel):
    active: bool


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def create_app(
    settings: Settings,
    *,
    runtime: SomachRuntime | None = None,
) -> FastAPI:
    hub = WebSocketHub()
    engine = runtime or SomachRuntime(settings)
    engine.publisher = hub.broadcast

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(
        title="SOMACH Silent Speech Dino API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.runtime = engine
    app.state.hub = hub
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": engine.source.meta.connected and engine.last_error is None,
            "mode": engine.settings.mode,
            "device": engine.source.meta.device,
            "error": engine.last_error,
        }

    @app.get("/api/status")
    async def status() -> dict:
        return engine.snapshot()

    @app.post("/api/calibrate")
    async def calibrate() -> dict:
        try:
            return engine.begin_calibration()
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @app.post("/api/threshold")
    async def threshold(body: ThresholdBody) -> dict:
        try:
            return engine.set_threshold(body.value)
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @app.post("/api/armed")
    async def armed(body: ArmedBody) -> dict:
        try:
            return engine.set_armed(body.armed)
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @app.post("/api/mock/trigger")
    async def mock_trigger() -> dict:
        try:
            return engine.inject_mock_jump()
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @app.post("/api/counter/reset")
    async def counter_reset() -> dict:
        return engine.reset_counter()

    @app.post("/api/accessibility/prompt")
    async def accessibility_prompt() -> dict:
        return engine.prompt_accessibility()

    @app.post("/api/recording/start")
    async def recording_start() -> dict:
        try:
            return engine.start_recording()
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @app.post("/api/recording/mark")
    async def recording_mark(body: RecordingMarkBody) -> dict:
        try:
            return engine.mark_recording(body.label)
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @app.post("/api/recording/stop")
    async def recording_stop() -> dict:
        try:
            return await asyncio.to_thread(engine.stop_recording)
        except (OSError, ValueError) as exc:
            raise _bad_request(exc) from exc

    @app.get("/api/recording/status")
    async def recording_status() -> dict:
        return engine.recording_status()

    @app.post("/api/model/train")
    async def model_train() -> dict:
        try:
            return await asyncio.to_thread(engine.train_model)
        except (OSError, ValueError) as exc:
            raise _bad_request(exc) from exc

    @app.post("/api/model/activate")
    async def model_activate(body: ModelActivateBody) -> dict:
        try:
            return engine.set_model_active(body.active)
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @app.websocket("/ws")
    async def websocket_stream(websocket: WebSocket) -> None:
        client = await hub.add(websocket)
        await hub.send(client, engine.snapshot())
        try:
            while True:
                message = await websocket.receive_json()
                command = message.get("type")
                try:
                    if command == "ping":
                        await hub.send(client, {"type": "pong"})
                    elif command == "calibrate":
                        engine.begin_calibration()
                    elif command == "threshold":
                        engine.set_threshold(float(message["value"]))
                    elif command == "armed":
                        engine.set_armed(bool(message["armed"]))
                    elif command in {"mock_trigger", "mock_jump"}:
                        engine.inject_mock_jump()
                    else:
                        await hub.send(
                            client,
                            {"type": "error", "message": "Unknown command"},
                        )
                except (KeyError, TypeError, ValueError) as exc:
                    await hub.send(
                        client,
                        {"type": "error", "message": str(exc)},
                    )
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("WebSocket client closed", exc_info=True)
        finally:
            hub.remove(websocket)

    return app
