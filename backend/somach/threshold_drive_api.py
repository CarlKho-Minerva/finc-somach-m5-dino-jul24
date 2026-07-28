"""FastAPI control surface for the independent dual-channel driving demo."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .threshold_drive import DualDriveRuntime, DualDriveSettings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Client:
    socket: WebSocket
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class _Hub:
    """Broadcast without allowing a frozen browser to stall acquisition."""

    def __init__(self):
        self.clients: dict[int, _Client] = {}

    async def add(self, socket: WebSocket) -> _Client:
        await socket.accept()
        client = _Client(socket)
        self.clients[id(socket)] = client
        return client

    def remove(self, socket: WebSocket) -> None:
        self.clients.pop(id(socket), None)

    async def send(self, client: _Client, payload: dict) -> None:
        async with client.lock:
            await client.socket.send_json(payload)

    async def broadcast(self, payload: dict) -> None:
        stale: list[int] = []
        for identifier, client in tuple(self.clients.items()):
            try:
                await asyncio.wait_for(self.send(client, payload), timeout=0.1)
            except Exception:  # browser disconnects surface several exception types
                stale.append(identifier)
        for identifier in stale:
            self.clients.pop(identifier, None)


class ArmedBody(BaseModel):
    armed: bool


class ThresholdBody(BaseModel):
    channel: Literal["a", "b"]
    value: float = Field(gt=0.0, le=4_095.0)


class MockActionBody(BaseModel):
    action: Literal["forward", "left", "right"]


def _conflict(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _apply_command(runtime: DualDriveRuntime, payload: dict) -> dict:
    """Apply the same command vocabulary over HTTP and WebSocket."""

    command = payload.get("command", payload.get("type"))
    if command == "calibrate":
        return runtime.begin_calibration()
    if command == "armed":
        armed = payload.get("armed")
        if not isinstance(armed, bool):
            raise ValueError("armed must be true or false")
        return runtime.set_armed(armed)
    if command == "threshold":
        channel = payload.get("channel")
        value = payload.get("value")
        if channel not in {"a", "b"}:
            raise ValueError("channel must be 'a' or 'b'")
        if isinstance(value, bool):
            raise ValueError("threshold value must be numeric")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("threshold value must be numeric") from exc
        return runtime.set_threshold(channel, numeric)
    if command in {"counter-reset", "reset-counter"}:
        return runtime.reset_counter()
    if command == "mock-trigger":
        action = payload.get("action")
        if action not in {"forward", "left", "right"}:
            raise ValueError("mock action must be forward, left, or right")
        return runtime.inject_mock(action)
    if command == "accessibility":
        return runtime.prompt_accessibility()
    if command in {"status", "get-status"}:
        return runtime.snapshot()
    raise ValueError(f"Unknown WebSocket command: {command!r}")


def create_dual_drive_app(
    settings: DualDriveSettings,
    *,
    runtime: DualDriveRuntime | None = None,
) -> FastAPI:
    settings.validate()
    hub = _Hub()
    engine = runtime or DualDriveRuntime(settings)
    engine.publisher = hub.broadcast

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(
        title="SOMACH Dual-Channel Threshold Drive API",
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
            "error": engine.last_error or engine.source.meta.error,
            "service": "dual-threshold-drive",
        }

    @app.get("/api/status")
    async def status() -> dict:
        return engine.snapshot()

    @app.post("/api/calibrate")
    async def calibrate() -> dict:
        try:
            return engine.begin_calibration()
        except ValueError as exc:
            raise _conflict(exc) from exc

    @app.post("/api/armed")
    async def armed(body: ArmedBody) -> dict:
        try:
            return engine.set_armed(body.armed)
        except ValueError as exc:
            raise _conflict(exc) from exc

    @app.post("/api/threshold")
    async def threshold(body: ThresholdBody) -> dict:
        try:
            return engine.set_threshold(body.channel, body.value)
        except ValueError as exc:
            raise _conflict(exc) from exc

    @app.post("/api/counter/reset")
    async def reset_counter() -> dict:
        return engine.reset_counter()

    @app.post("/api/mock/trigger")
    async def mock_trigger(body: MockActionBody) -> dict:
        try:
            return engine.inject_mock(body.action)
        except ValueError as exc:
            raise _conflict(exc) from exc

    @app.post("/api/accessibility/prompt")
    async def prompt_accessibility() -> dict:
        return engine.prompt_accessibility()

    @app.websocket("/ws")
    async def websocket_endpoint(socket: WebSocket) -> None:
        client = await hub.add(socket)
        try:
            await hub.send(client, engine.snapshot())
            while True:
                payload = await socket.receive_json()
                if not isinstance(payload, dict):
                    await hub.send(
                        client,
                        {"type": "command-error", "message": "Command must be an object"},
                    )
                    continue
                try:
                    response = _apply_command(engine, payload)
                except ValueError as exc:
                    response = {"type": "command-error", "message": str(exc)}
                await hub.send(client, response)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("Drive dashboard WebSocket disconnected", exc_info=True)
        finally:
            hub.remove(socket)

    return app
