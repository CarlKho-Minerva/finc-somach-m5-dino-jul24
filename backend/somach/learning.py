"""Local labeled-session recording and deterministic single-channel learning."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy import signal

WINDOW_MS = 300
CUE_OFFSET_MS = 50
JITTER_MS = (-25, 0, 25)
BACKGROUND_EXCLUSION_BEFORE_MS = 250
BACKGROUND_EXCLUSION_AFTER_MS = 500
MODEL_THRESHOLD = 0.5
MIN_POSITIVE_GROUPS = 6
MIN_NEGATIVE_GROUPS = 6


@dataclass(slots=True, frozen=True)
class RecordedBlock:
    raw: np.ndarray
    filtered: np.ndarray
    sample_index: np.ndarray
    monotonic_time: np.ndarray
    unix_time: np.ndarray

    @property
    def size(self) -> int:
        return int(self.raw.size)

    def tail(self, count: int) -> RecordedBlock:
        start = max(0, self.size - count)
        return RecordedBlock(
            self.raw[start:].copy(),
            self.filtered[start:].copy(),
            self.sample_index[start:].copy(),
            self.monotonic_time[start:].copy(),
            self.unix_time[start:].copy(),
        )


@dataclass(slots=True)
class Marker:
    label: str
    sample_index: int
    monotonic_time: float
    unix_time: float

    def json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "sampleIndex": self.sample_index,
            "monotonicTime": self.monotonic_time,
            "unixTime": self.unix_time,
        }


@dataclass(slots=True)
class ActiveSession:
    session_id: str
    started_unix: float
    started_monotonic: float
    recording_start_index: int
    blocks: list[RecordedBlock]
    markers: list[Marker]
    sample_count: int


class SessionRecorder:
    """Capture synchronized raw/filtered data with an always-on prebuffer."""

    def __init__(
        self,
        sample_rate_hz: int,
        *,
        dataset_dir: str | Path = "datasets",
        prebuffer_seconds: float = 1.0,
    ):
        self.sample_rate_hz = sample_rate_hz
        self.dataset_dir = Path(dataset_dir)
        self.prebuffer_samples = max(1, round(prebuffer_seconds * sample_rate_hz))
        self._prebuffer: deque[RecordedBlock] = deque()
        self._prebuffer_count = 0
        self._next_sample_index = 0
        self._session: ActiveSession | None = None
        self._saving = False
        self._last_dataset: Path | None = None
        self._last_metadata: Path | None = None
        self._last_error: str | None = None
        self._wall_minus_monotonic = time.time() - time.perf_counter()
        self._lock = threading.RLock()

    def ingest(
        self,
        raw: np.ndarray,
        filtered: np.ndarray,
        captured_at: float,
    ) -> None:
        raw_values = np.asarray(raw, dtype=np.uint16)
        filtered_values = np.asarray(filtered, dtype=np.float32)
        if raw_values.ndim != 1 or filtered_values.ndim != 1:
            raise ValueError("recorded samples must be one-dimensional")
        if raw_values.size != filtered_values.size:
            raise ValueError("raw and filtered recording blocks must be aligned")
        if raw_values.size == 0:
            return

        with self._lock:
            count = int(raw_values.size)
            indices = np.arange(
                self._next_sample_index,
                self._next_sample_index + count,
                dtype=np.uint64,
            )
            offsets = np.arange(count - 1, -1, -1, dtype=np.float64)
            monotonic_times = captured_at - offsets / self.sample_rate_hz
            unix_times = monotonic_times + self._wall_minus_monotonic
            block = RecordedBlock(
                raw_values.copy(),
                filtered_values.copy(),
                indices,
                monotonic_times,
                unix_times,
            )
            self._next_sample_index += count

            if self._session is not None:
                self._session.blocks.append(block)
                self._session.sample_count += count

            self._prebuffer.append(block)
            self._prebuffer_count += count
            self._trim_prebuffer()

    def _trim_prebuffer(self) -> None:
        while self._prebuffer and self._prebuffer_count > self.prebuffer_samples:
            excess = self._prebuffer_count - self.prebuffer_samples
            first = self._prebuffer[0]
            if first.size <= excess:
                self._prebuffer.popleft()
                self._prebuffer_count -= first.size
            else:
                self._prebuffer[0] = first.tail(first.size - excess)
                self._prebuffer_count -= excess

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._saving:
                raise ValueError("Wait for the current recording to finish saving")
            if self._session is not None:
                raise ValueError("A labeled recording is already active")
            now_unix = time.time()
            now_monotonic = time.perf_counter()
            session_id = uuid.uuid4().hex[:12]
            self._session = ActiveSession(
                session_id=session_id,
                started_unix=now_unix,
                started_monotonic=now_monotonic,
                recording_start_index=self._next_sample_index,
                blocks=list(self._prebuffer),
                markers=[],
                sample_count=self._prebuffer_count,
            )
            self._last_error = None
            return self.status()

    def mark(self, label: str) -> dict[str, Any]:
        if label not in {"jump", "artifact"}:
            raise ValueError("label must be 'jump' or 'artifact'")
        with self._lock:
            if self._session is None:
                raise ValueError("Start a recording before adding a marker")
            marker = Marker(
                label=label,
                sample_index=self._next_sample_index,
                monotonic_time=time.perf_counter(),
                unix_time=time.time(),
            )
            self._session.markers.append(marker)
            return marker.json()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._saving:
                raise ValueError("The labeled recording is already being saved")
            if self._session is None:
                raise ValueError("No labeled recording is active")
            session = self._session
            self._session = None
            self._saving = True
        try:
            dataset_path, metadata_path = self._persist(session)
        except Exception as exc:
            with self._lock:
                # Keep the completed in-memory capture available for another
                # stop/save attempt. A transient full disk or permissions
                # error must not silently destroy the judge's labeled trials.
                self._session = session
                self._saving = False
                self._last_error = str(exc)
            raise
        with self._lock:
            self._saving = False
            self._last_dataset = dataset_path
            self._last_metadata = metadata_path
            self._last_error = None
            jump_markers = sum(
                marker.label == "jump" for marker in session.markers
            )
            artifact_markers = sum(
                marker.label == "artifact" for marker in session.markers
            )
            return {
                **self.status(),
                "saved": True,
                "sessionId": session.session_id,
                "sampleCount": session.sample_count,
                "durationSeconds": round(
                    session.sample_count / self.sample_rate_hz, 3
                ),
                "markerCount": len(session.markers),
                "jumpMarkers": jump_markers,
                "artifactMarkers": artifact_markers,
                "dataset": str(dataset_path),
                "metadata": str(metadata_path),
            }

    @staticmethod
    def _concatenate(
        blocks: list[RecordedBlock], attribute: str, dtype: np.dtype[Any]
    ) -> np.ndarray:
        if not blocks:
            return np.asarray([], dtype=dtype)
        return np.concatenate([getattr(block, attribute) for block in blocks]).astype(
            dtype, copy=False
        )

    def _persist(self, session: ActiveSession) -> tuple[Path, Path]:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(session.started_unix, tz=UTC).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        stem = f"somach_{stamp}_{session.session_id}"
        dataset_path = self.dataset_dir / f"{stem}.npz"
        metadata_path = self.dataset_dir / f"{stem}.json"

        raw = self._concatenate(session.blocks, "raw", np.dtype(np.uint16))
        filtered = self._concatenate(
            session.blocks, "filtered", np.dtype(np.float32)
        )
        sample_index = self._concatenate(
            session.blocks, "sample_index", np.dtype(np.uint64)
        )
        monotonic_time = self._concatenate(
            session.blocks, "monotonic_time", np.dtype(np.float64)
        )
        unix_time = self._concatenate(
            session.blocks, "unix_time", np.dtype(np.float64)
        )
        marker_indices = np.asarray(
            [marker.sample_index for marker in session.markers], dtype=np.uint64
        )
        marker_times = np.asarray(
            [marker.unix_time for marker in session.markers], dtype=np.float64
        )
        marker_labels = np.asarray(
            [marker.label for marker in session.markers], dtype="U16"
        )

        temporary_npz = dataset_path.with_suffix(".npz.tmp")
        with temporary_npz.open("wb") as handle:
            np.savez_compressed(
                handle,
                raw=raw,
                filtered=filtered,
                sample_index=sample_index,
                monotonic_time=monotonic_time,
                unix_time=unix_time,
                marker_sample_index=marker_indices,
                marker_unix_time=marker_times,
                marker_label=marker_labels,
                sample_rate_hz=np.asarray(self.sample_rate_hz, dtype=np.int32),
                recording_start_index=np.asarray(
                    session.recording_start_index, dtype=np.uint64
                ),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_npz, dataset_path)

        stopped_unix = time.time()
        metadata = {
            "schemaVersion": 1,
            "sessionId": session.session_id,
            "sampleRateHz": self.sample_rate_hz,
            "startedUnix": session.started_unix,
            "stoppedUnix": stopped_unix,
            "recordingStartIndex": session.recording_start_index,
            "sampleCount": int(raw.size),
            "firstSampleIndex": None if raw.size == 0 else int(sample_index[0]),
            "lastSampleIndex": None if raw.size == 0 else int(sample_index[-1]),
            "markerCount": len(session.markers),
            "markers": [marker.json() for marker in session.markers],
            "arrays": {
                "raw": "uint16 ADC counts",
                "filtered": "float32 causal filtered ADC counts",
                "sample_index": "uint64 runtime-global sample index",
                "monotonic_time": "float64 seconds from perf_counter clock",
                "unix_time": "float64 Unix seconds",
            },
            "windowing": {
                "windowMs": WINDOW_MS,
                "cueOffsetMs": CUE_OFFSET_MS,
                "jitterMs": list(JITTER_MS),
            },
        }
        temporary_json = metadata_path.with_suffix(".json.tmp")
        with temporary_json.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_json, metadata_path)
        return dataset_path, metadata_path

    def status(self) -> dict[str, Any]:
        with self._lock:
            session = self._session
            markers = [] if session is None else session.markers
            sample_count = 0 if session is None else session.sample_count
            duration = sample_count / self.sample_rate_hz
            recent_clip_fraction = self.recent_clip_fraction()
            return {
                "active": session is not None,
                "saving": self._saving,
                "sessionId": None if session is None else session.session_id,
                "sampleCount": sample_count,
                "durationSeconds": round(duration, 3),
                "markerCount": len(markers),
                "jumpMarkers": sum(marker.label == "jump" for marker in markers),
                "artifactMarkers": sum(
                    marker.label == "artifact" for marker in markers
                ),
                "prebufferSeconds": self.prebuffer_samples / self.sample_rate_hz,
                "recentClipFraction": round(recent_clip_fraction, 6),
                "lastDataset": None
                if self._last_dataset is None
                else str(self._last_dataset),
                "lastMetadata": None
                if self._last_metadata is None
                else str(self._last_metadata),
                "error": self._last_error,
            }

    def recent_clip_fraction(self, count: int = 500) -> float:
        """Return the near-rail fraction in the newest prebuffer samples."""

        with self._lock:
            remaining = max(1, count)
            pieces: list[np.ndarray] = []
            for block in reversed(self._prebuffer):
                take = min(remaining, block.size)
                pieces.append(block.raw[-take:])
                remaining -= take
                if remaining == 0:
                    break
            if not pieces:
                return 0.0
            values = np.concatenate(list(reversed(pieces)))
            clipped = (values <= 4) | (values >= 4_091)
            return float(np.mean(clipped))


FEATURE_NAMES = (
    "rms",
    "mean_absolute_value",
    "standard_deviation",
    "peak_absolute",
    "crest_factor",
    "waveform_length",
    "zero_crossing_rate",
    "slope_change_rate",
    "absolute_q50",
    "absolute_q75",
    "absolute_q90",
    "bandpower_20_60",
    "bandpower_60_120",
    "bandpower_120_250",
    "spectral_centroid",
)


def temporal_features(values: np.ndarray, sample_rate_hz: int) -> np.ndarray:
    """Extract scale-aware time/frequency features from one 300 ms channel."""

    window = np.asarray(values, dtype=np.float64)
    if window.ndim != 1 or window.size < 8:
        raise ValueError("feature window must be a one-dimensional signal")
    centered = window - float(np.mean(window))
    absolute = np.abs(centered)
    rms = float(np.sqrt(np.mean(centered * centered)))
    peak = float(np.max(absolute))
    differences = np.diff(centered)
    deadband = max(1e-9, rms * 0.05)
    signs = np.sign(centered)
    zero_crossings = np.count_nonzero(
        (signs[1:] != signs[:-1])
        & (absolute[1:] > deadband)
        & (absolute[:-1] > deadband)
    )
    difference_signs = np.sign(differences)
    slope_changes = np.count_nonzero(
        difference_signs[1:] != difference_signs[:-1]
    )

    frequencies, density = signal.periodogram(
        centered, fs=sample_rate_hz, scaling="spectrum"
    )
    total_power = float(np.sum(density)) + 1e-12

    def band_power(low: float, high: float) -> float:
        mask = (frequencies >= low) & (frequencies < high)
        return float(np.sum(density[mask]) / total_power)

    centroid = float(np.sum(frequencies * density) / total_power)
    quantiles = np.quantile(absolute, [0.5, 0.75, 0.9])
    return np.asarray(
        [
            rms,
            float(np.mean(absolute)),
            float(np.std(centered)),
            peak,
            peak / max(rms, 1e-9),
            float(np.mean(np.abs(differences))),
            zero_crossings / max(1, window.size - 1),
            slope_changes / max(1, differences.size - 1),
            *quantiles.tolist(),
            band_power(20.0, 60.0),
            band_power(60.0, 120.0),
            band_power(120.0, min(250.0, sample_rate_hz / 2)),
            centroid / (sample_rate_hz / 2),
        ],
        dtype=np.float64,
    )


@dataclass(slots=True, frozen=True)
class WindowExample:
    features: np.ndarray
    label: int
    group: str


@dataclass(slots=True, frozen=True)
class LDAModel:
    feature_mean: np.ndarray
    feature_scale: np.ndarray
    weights: np.ndarray
    intercept: float

    def predict_probability(self, features: np.ndarray) -> float:
        standardized = (features - self.feature_mean) / self.feature_scale
        logit = float(np.dot(standardized, self.weights) + self.intercept)
        logit = float(np.clip(logit, -40.0, 40.0))
        return float(1.0 / (1.0 + np.exp(-logit)))


def _fit_lda(features: np.ndarray, labels: np.ndarray) -> LDAModel:
    feature_mean = np.mean(features, axis=0)
    feature_scale = np.std(features, axis=0)
    feature_scale = np.where(feature_scale < 1e-8, 1.0, feature_scale)
    standardized = (features - feature_mean) / feature_scale
    class_zero = standardized[labels == 0]
    class_one = standardized[labels == 1]
    if class_zero.size == 0 or class_one.size == 0:
        raise ValueError("Both jump and non-jump training windows are required")
    mean_zero = np.mean(class_zero, axis=0)
    mean_one = np.mean(class_one, axis=0)
    centered_zero = class_zero - mean_zero
    centered_one = class_one - mean_one
    degrees = max(1, standardized.shape[0] - 2)
    covariance = (
        centered_zero.T @ centered_zero + centered_one.T @ centered_one
    ) / degrees
    diagonal = np.diag(np.diag(covariance))
    regularized = 0.8 * covariance + 0.2 * diagonal
    ridge = max(1e-6, float(np.trace(regularized)) / regularized.shape[0] * 1e-3)
    regularized += np.eye(regularized.shape[0]) * ridge
    weights = np.linalg.solve(regularized, mean_one - mean_zero)
    intercept = -0.5 * float(np.dot(mean_one + mean_zero, weights))
    return LDAModel(feature_mean, feature_scale, weights, intercept)


def _window_at(
    filtered: np.ndarray,
    sample_indices: np.ndarray,
    start_index: int,
    window_samples: int,
) -> np.ndarray | None:
    position = int(np.searchsorted(sample_indices, start_index))
    end = position + window_samples
    if position >= sample_indices.size or end > sample_indices.size:
        return None
    if int(sample_indices[position]) != start_index:
        return None
    expected_end = start_index + window_samples - 1
    if int(sample_indices[end - 1]) != expected_end:
        return None
    return filtered[position:end]


def _window_is_clipped(raw_window: np.ndarray) -> bool:
    clipped = (raw_window <= 4) | (raw_window >= 4_091)
    return float(np.mean(clipped)) > 0.01


def extract_examples(dataset_path: Path) -> tuple[list[WindowExample], dict[str, int]]:
    with np.load(dataset_path, allow_pickle=False) as dataset:
        raw = np.asarray(dataset["raw"], dtype=np.uint16)
        filtered = np.asarray(dataset["filtered"], dtype=np.float64)
        indices = np.asarray(dataset["sample_index"], dtype=np.uint64)
        marker_indices = np.asarray(dataset["marker_sample_index"], dtype=np.uint64)
        marker_labels = np.asarray(dataset["marker_label"], dtype=str)
        sample_rate = int(np.asarray(dataset["sample_rate_hz"]).item())

    window_samples = round(sample_rate * WINDOW_MS / 1_000)
    cue_offset = round(sample_rate * CUE_OFFSET_MS / 1_000)
    jitter = [round(sample_rate * value / 1_000) for value in JITTER_MS]
    session = dataset_path.stem
    examples: list[WindowExample] = []
    usable_jump_groups: set[str] = set()
    usable_negative_groups: set[str] = set()
    skipped_markers = 0

    for marker_number, (marker_index_value, marker_label) in enumerate(
        zip(marker_indices, marker_labels, strict=True)
    ):
        label = 1 if marker_label == "jump" else 0
        group = f"{session}:marker:{marker_number}:{marker_label}"
        group_examples = 0
        marker_index = int(marker_index_value)
        for shift in jitter:
            window = _window_at(
                filtered,
                indices,
                marker_index + cue_offset + shift,
                window_samples,
            )
            if window is None:
                continue
            raw_window = _window_at(
                raw,
                indices,
                marker_index + cue_offset + shift,
                window_samples,
            )
            if raw_window is None or _window_is_clipped(raw_window):
                continue
            examples.append(
                WindowExample(temporal_features(window, sample_rate), label, group)
            )
            group_examples += 1
        if group_examples == 0:
            skipped_markers += 1
        elif label == 1:
            usable_jump_groups.add(group)
        else:
            usable_negative_groups.add(group)

    if indices.size >= window_samples:
        first = int(indices[0])
        last_start = int(indices[-1]) - window_samples + 1
        stride = max(window_samples, round(sample_rate * 0.4))
        exclusion_before = round(
            sample_rate * BACKGROUND_EXCLUSION_BEFORE_MS / 1_000
        )
        exclusion_after = round(
            sample_rate * BACKGROUND_EXCLUSION_AFTER_MS / 1_000
        )
        background_limit = max(MIN_NEGATIVE_GROUPS, len(usable_jump_groups) * 2)
        for start in range(first, last_start + 1, stride):
            end = start + window_samples
            near_marker = any(
                start < int(marker) + exclusion_after
                and end > int(marker) - exclusion_before
                for marker in marker_indices
            )
            if near_marker:
                continue
            window = _window_at(filtered, indices, start, window_samples)
            if window is None:
                continue
            raw_window = _window_at(raw, indices, start, window_samples)
            if raw_window is None or _window_is_clipped(raw_window):
                continue
            group = f"{session}:background:{start}"
            examples.append(
                WindowExample(temporal_features(window, sample_rate), 0, group)
            )
            usable_negative_groups.add(group)
            if len(usable_negative_groups) >= background_limit:
                break

    return examples, {
        "jumpGroups": len(usable_jump_groups),
        "negativeGroups": len(usable_negative_groups),
        "skippedMarkers": skipped_markers,
    }


def _grouped_split(
    examples: list[WindowExample], seed: int = 42
) -> tuple[np.ndarray, np.ndarray]:
    groups_by_label: dict[int, list[str]] = {0: [], 1: []}
    for label in (0, 1):
        groups_by_label[label] = sorted(
            {example.group for example in examples if example.label == label}
        )
    rng = np.random.default_rng(seed)
    test_groups: set[str] = set()
    for label in (0, 1):
        groups = groups_by_label[label]
        test_count = max(1, round(len(groups) * 0.25))
        selected = rng.choice(len(groups), size=test_count, replace=False)
        test_groups.update(groups[int(index)] for index in selected)
    test_mask = np.asarray(
        [example.group in test_groups for example in examples], dtype=bool
    )
    return ~test_mask, test_mask


def _metrics(labels: np.ndarray, probabilities: np.ndarray) -> dict[str, Any]:
    predictions = probabilities >= MODEL_THRESHOLD
    positives = labels == 1
    negatives = ~positives
    true_positive = int(np.count_nonzero(predictions & positives))
    false_positive = int(np.count_nonzero(predictions & negatives))
    true_negative = int(np.count_nonzero(~predictions & negatives))
    false_negative = int(np.count_nonzero(~predictions & positives))
    recall = true_positive / max(1, true_positive + false_negative)
    specificity = true_negative / max(1, true_negative + false_positive)
    precision = true_positive / max(1, true_positive + false_positive)
    return {
        "accuracy": (true_positive + true_negative) / max(1, labels.size),
        "balancedAccuracy": (recall + specificity) / 2,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": 2 * precision * recall / max(1e-12, precision + recall),
        "truePositive": true_positive,
        "falsePositive": false_positive,
        "trueNegative": true_negative,
        "falseNegative": false_negative,
        "heldOutWindows": int(labels.size),
    }


class ModelManager:
    """Train, validate, and optionally run the regularized LDA detector."""

    def __init__(self, sample_rate_hz: int, dataset_dir: str | Path = "datasets"):
        self.sample_rate_hz = sample_rate_hz
        self.dataset_dir = Path(dataset_dir)
        self.window_samples = round(sample_rate_hz * WINDOW_MS / 1_000)
        self.threshold = MODEL_THRESHOLD
        self.active = False
        self.model: LDAModel | None = None
        self.probability: float | None = None
        self.metrics: dict[str, Any] | None = None
        self.trained_at: float | None = None
        self.error: str | None = None
        self._window: deque[float] = deque(maxlen=self.window_samples)
        self._lock = threading.RLock()

    def observe(self, filtered: np.ndarray) -> float | None:
        with self._lock:
            self._window.extend(np.asarray(filtered, dtype=np.float64).tolist())
            model = self.model
            if model is None or len(self._window) < self.window_samples:
                self.probability = None
                return None
            try:
                features = temporal_features(
                    np.fromiter(self._window, dtype=np.float64), self.sample_rate_hz
                )
                self.probability = model.predict_probability(features)
                self.error = None
            except (FloatingPointError, ValueError, np.linalg.LinAlgError) as exc:
                self.probability = None
                self.error = f"Live model scoring failed: {exc}"
            return self.probability

    def train(self) -> dict[str, Any]:
        paths = sorted(self.dataset_dir.glob("somach_*.npz"))
        if not paths:
            raise ValueError(
                f"No labeled .npz sessions found in {self.dataset_dir}"
            )
        examples: list[WindowExample] = []
        jump_groups = 0
        negative_groups = 0
        skipped_markers = 0
        used_paths: list[str] = []
        for path in paths:
            session_examples, summary = extract_examples(path)
            if session_examples:
                examples.extend(session_examples)
                used_paths.append(str(path))
                jump_groups += summary["jumpGroups"]
                negative_groups += summary["negativeGroups"]
                skipped_markers += summary["skippedMarkers"]
        positive_group_names = {
            example.group for example in examples if example.label == 1
        }
        negative_group_names = {
            example.group for example in examples if example.label == 0
        }
        if len(positive_group_names) < MIN_POSITIVE_GROUPS:
            raise ValueError(
                "Need at least "
                f"{MIN_POSITIVE_GROUPS} usable jump markers with 300 ms of data "
                f"after each cue; found {len(positive_group_names)}"
            )
        if len(negative_group_names) < MIN_NEGATIVE_GROUPS:
            raise ValueError(
                "Need at least "
                f"{MIN_NEGATIVE_GROUPS} artifact/background trial groups; found "
                f"{len(negative_group_names)}. Record longer rest gaps or mark "
                "artifacts."
            )

        features = np.vstack([example.features for example in examples])
        labels = np.asarray([example.label for example in examples], dtype=np.int8)
        train_mask, test_mask = _grouped_split(examples)
        train_groups = {examples[index].group for index in np.flatnonzero(train_mask)}
        test_groups = {examples[index].group for index in np.flatnonzero(test_mask)}
        if train_groups & test_groups:
            raise RuntimeError("Grouped validation leaked a trial into both splits")
        model = _fit_lda(features[train_mask], labels[train_mask])
        probabilities = np.asarray(
            [model.predict_probability(row) for row in features[test_mask]],
            dtype=np.float64,
        )
        metrics = {
            **_metrics(labels[test_mask], probabilities),
            "trainWindows": int(np.count_nonzero(train_mask)),
            "trainGroups": len(train_groups),
            "heldOutGroups": len(test_groups),
            "jumpGroups": len(positive_group_names),
            "negativeGroups": len(negative_group_names),
            "skippedMarkers": skipped_markers,
            "sessions": used_paths,
        }
        with self._lock:
            self.model = model
            self.metrics = metrics
            self.trained_at = time.time()
            self.active = False
            self.probability = None
            self.error = None
            self._window.clear()
            return self.status()

    def set_active(self, active: bool) -> dict[str, Any]:
        with self._lock:
            if active and self.model is None:
                raise ValueError("Train a session model before activating it")
            self.active = bool(active)
            self.probability = None
            self._window.clear()
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "trained": self.model is not None,
                "active": self.active,
                "probability": None
                if self.probability is None
                else round(self.probability, 6),
                "threshold": self.threshold,
                "windowMs": WINDOW_MS,
                "featureCount": len(FEATURE_NAMES),
                "features": list(FEATURE_NAMES),
                "trainedAt": self.trained_at,
                "metrics": self.metrics,
                "validation": "deterministic held-out trial groups",
                "error": self.error,
            }
