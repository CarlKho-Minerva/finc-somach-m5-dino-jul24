from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from somach.api import create_app
from somach.config import Settings
from somach.runtime import SomachRuntime


def test_mock_control_surface() -> None:
    app = create_app(Settings(mode="mock", inject_keys=False))
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        threshold = client.post("/api/threshold", json={"value": 12.5})
        assert threshold.status_code == 200
        assert threshold.json()["threshold"] == 12.5

        armed = client.post("/api/armed", json={"armed": True})
        assert armed.status_code == 200
        assert armed.json()["armed"] is True

        jump = client.post("/api/mock/trigger")
        assert jump.status_code == 200
        assert jump.json()["accepted"] is True

        calibration = client.post("/api/calibrate")
        assert calibration.status_code == 200
        assert calibration.json()["calibration"]["active"] is True


def test_recording_and_model_api_contract(tmp_path: Path) -> None:
    settings = Settings(mode="mock", inject_keys=False)
    runtime = SomachRuntime(settings, dataset_dir=tmp_path)
    app = create_app(settings, runtime=runtime)
    with TestClient(app) as client:
        status = client.get("/api/status").json()
        assert status["recording"]["active"] is False
        assert status["model"]["trained"] is False
        assert status["detector"] == "rms"

        started = client.post("/api/recording/start")
        assert started.status_code == 200
        assert started.json()["recording"]["active"] is True

        marked = client.post("/api/recording/mark", json={"label": "jump"})
        assert marked.status_code == 200
        assert marked.json()["marker"]["label"] == "jump"

        recording_status = client.get("/api/recording/status")
        assert recording_status.status_code == 200
        assert recording_status.json()["jumpMarkers"] == 1

        stopped = client.post("/api/recording/stop")
        assert stopped.status_code == 200
        assert stopped.json()["recordingResult"]["saved"] is True
        assert stopped.json()["recordingResult"]["jumpMarkers"] == 1

        too_small = client.post("/api/model/train")
        assert too_small.status_code == 409
        assert "jump markers" in too_small.json()["detail"]

        activation = client.post("/api/model/activate", json={"active": True})
        assert activation.status_code == 409
