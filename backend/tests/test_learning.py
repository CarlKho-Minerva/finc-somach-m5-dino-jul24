from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from somach.learning import ModelManager, SessionRecorder


def ingest(recorder: SessionRecorder, filtered: np.ndarray) -> None:
    raw = np.clip(np.rint(2_000 + filtered), 0, 4_095).astype(np.uint16)
    recorder.ingest(raw, filtered, time.perf_counter())


def make_training_session(dataset_dir: Path) -> Path:
    sample_rate = 1_000
    recorder = SessionRecorder(sample_rate, dataset_dir=dataset_dir)
    rng = np.random.default_rng(123)

    ingest(recorder, rng.normal(0, 2, 1_000))
    recorder.start()
    for trial in range(6):
        ingest(recorder, rng.normal(0, 2, 500))
        recorder.mark("jump")
        values = rng.normal(0, 2, 800)
        samples = np.arange(300) / sample_rate
        burst = (
            90 * np.sin(2 * np.pi * 83 * samples + trial * 0.1)
            + 55 * np.sin(2 * np.pi * 137 * samples)
        )
        values[50:350] += burst * np.sin(np.pi * samples / 0.3) ** 2
        ingest(recorder, values)
    result = recorder.stop()
    return Path(result["dataset"])


def test_recording_is_atomic_aligned_and_contains_markers(tmp_path: Path) -> None:
    path = make_training_session(tmp_path)
    assert path.exists()
    assert path.with_suffix(".json").exists()
    assert not path.with_suffix(".npz.tmp").exists()

    with np.load(path, allow_pickle=False) as dataset:
        assert dataset["raw"].shape == dataset["filtered"].shape
        assert dataset["raw"].shape == dataset["sample_index"].shape
        assert dataset["raw"].shape == dataset["unix_time"].shape
        assert dataset["marker_label"].tolist() == ["jump"] * 6
        assert np.all(np.diff(dataset["sample_index"]) == 1)


def test_stop_result_keeps_completed_session_counts(tmp_path: Path) -> None:
    recorder = SessionRecorder(1_000, dataset_dir=tmp_path)
    recorder.start()
    recorder.mark("jump")
    recorder.mark("artifact")
    ingest(recorder, np.zeros(400))

    result = recorder.stop()

    assert result["active"] is False
    assert result["sampleCount"] == 400
    assert result["markerCount"] == 2
    assert result["jumpMarkers"] == 1
    assert result["artifactMarkers"] == 1


def test_failed_save_retains_recording_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = SessionRecorder(1_000, dataset_dir=tmp_path)
    recorder.start()
    recorder.mark("jump")
    ingest(recorder, np.zeros(400))
    original_persist = SessionRecorder._persist

    def fail_once(*_args: object) -> tuple[Path, Path]:
        raise OSError("disk unavailable")

    monkeypatch.setattr(SessionRecorder, "_persist", fail_once)
    with pytest.raises(OSError, match="disk unavailable"):
        recorder.stop()

    failed = recorder.status()
    assert failed["active"] is True
    assert failed["saving"] is False
    assert failed["sampleCount"] == 400
    assert failed["jumpMarkers"] == 1

    monkeypatch.setattr(SessionRecorder, "_persist", original_persist)
    result = recorder.stop()
    assert result["saved"] is True
    assert result["sampleCount"] == 400
    assert result["jumpMarkers"] == 1


def test_grouped_classifier_reports_real_held_out_metrics(tmp_path: Path) -> None:
    make_training_session(tmp_path)
    manager = ModelManager(1_000, dataset_dir=tmp_path)

    state = manager.train()
    metrics = state["metrics"]
    assert state["trained"] is True
    assert state["active"] is False
    assert metrics["jumpGroups"] == 6
    assert metrics["heldOutGroups"] >= 2
    assert metrics["heldOutWindows"] > 0
    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert 0.0 <= metrics["balancedAccuracy"] <= 1.0

    manager.set_active(True)
    probability = manager.observe(np.full(300, 1.0))
    assert probability is not None
    assert 0.0 <= probability <= 1.0


def test_clipped_marker_windows_are_not_accepted_for_training(tmp_path: Path) -> None:
    recorder = SessionRecorder(1_000, dataset_dir=tmp_path)
    recorder.start()
    for _ in range(6):
        recorder.mark("jump")
        recorder.ingest(
            np.zeros(700, dtype=np.uint16),
            np.zeros(700, dtype=np.float32),
            time.perf_counter(),
        )
    recorder.stop()
    manager = ModelManager(1_000, dataset_dir=tmp_path)

    with pytest.raises(ValueError, match="usable jump markers"):
        manager.train()
