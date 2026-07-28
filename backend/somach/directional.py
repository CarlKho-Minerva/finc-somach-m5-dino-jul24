"""Deterministic feature extraction and lightweight directional classification.

The live demo cannot infer four commands from amplitude alone.  This module
provides the small, dependency-light core needed to learn person-specific
commands from one or two synchronized sEMG channels.  It intentionally uses
only NumPy so training and inference do not depend on a GPU or scikit-learn.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_SAMPLE_RATE_HZ = 1_000
WINDOW_DURATION_SECONDS = 0.350
DEFAULT_WINDOW_SAMPLES = 350
MODEL_SCHEMA_VERSION = 1
_BANDS_HZ = ((20.0, 60.0), (60.0, 120.0), (120.0, 250.0))
_EPSILON = np.finfo(np.float64).eps


def parse_sample_line(
    line: str | bytes,
    expected_channels: int | None = None,
) -> np.ndarray | None:
    """Parse one firmware sample line.

    Accepted sample formats are ``"2048"`` and ``"2048,2051"``.  Empty lines
    and firmware metadata lines beginning with ``#META`` are ignored and
    return ``None``.  Malformed or non-finite samples raise ``ValueError`` so a
    wiring/firmware mismatch cannot silently contaminate a training session.
    """

    if expected_channels not in (None, 1, 2):
        raise ValueError("expected_channels must be 1, 2, or None")
    if isinstance(line, bytes):
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("sample line is not valid UTF-8") from exc
    elif isinstance(line, str):
        text = line
    else:
        raise TypeError("sample line must be str or bytes")

    text = text.strip()
    if not text or text.upper().startswith("#META"):
        return None

    fields = [field.strip() for field in text.split(",")]
    if len(fields) not in (1, 2) or any(not field for field in fields):
        raise ValueError("sample line must contain one value or two CSV values")
    if expected_channels is not None and len(fields) != expected_channels:
        raise ValueError(
            f"expected {expected_channels} channel(s), received {len(fields)}"
        )
    try:
        values = np.asarray([float(field) for field in fields], dtype=np.float64)
    except ValueError as exc:
        raise ValueError("sample values must be numeric") from exc
    if not np.all(np.isfinite(values)):
        raise ValueError("sample values must be finite")
    return values


def _sample_matrix(samples: np.ndarray | list[float], *, name: str) -> np.ndarray:
    try:
        values = np.asarray(samples, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric samples") from exc
    if values.ndim == 1:
        values = values[:, np.newaxis]
    elif values.ndim != 2:
        raise ValueError(f"{name} must have shape (samples,) or (samples, channels)")
    if values.shape[1] not in (1, 2):
        raise ValueError(f"{name} must contain one or two channels")
    if values.shape[0] == 0:
        raise ValueError(f"{name} cannot be empty")
    if not np.all(np.isfinite(values)):
        raise ValueError(f"{name} must contain only finite samples")
    return values


def _linear_detrend(values: np.ndarray) -> np.ndarray:
    """Remove a least-squares line from each channel without SciPy."""

    count = values.shape[0]
    if count < 2:
        return values - np.mean(values, axis=0, keepdims=True)
    time_axis = np.linspace(-1.0, 1.0, count, dtype=np.float64)
    centered = values - np.mean(values, axis=0, keepdims=True)
    denominator = float(np.dot(time_axis, time_axis))
    slopes = (time_axis @ centered) / denominator
    return centered - time_axis[:, np.newaxis] * slopes[np.newaxis, :]


def _zero_crossing_rate(values: np.ndarray) -> float:
    # A tiny dead band prevents ADC quantization around zero from adding many
    # meaningless crossings.  Crossings separated by dead-band samples still
    # count once.
    deviation = float(np.std(values))
    dead_band = max(1e-12, deviation * 0.02)
    signs = np.sign(values[np.abs(values) > dead_band])
    if signs.size < 2:
        return 0.0
    return float(np.count_nonzero(signs[1:] != signs[:-1]) / (values.size - 1))


def feature_names(channel_count: int) -> tuple[str, ...]:
    """Return the stable ordered feature schema for one or two channels."""

    if channel_count not in (1, 2):
        raise ValueError("channel_count must be 1 or 2")
    per_channel = (
        "rms",
        "mav",
        "std",
        "peak_abs",
        "waveform_length",
        "zero_crossing_rate",
        "crest_factor",
        "early_rms",
        "middle_rms",
        "late_rms",
        "log_band_energy_20_60",
        "log_band_energy_60_120",
        "log_band_energy_120_250",
        "band_fraction_20_60",
        "band_fraction_60_120",
        "band_fraction_120_250",
    )
    names = [
        f"ch{channel + 1}_{name}"
        for channel in range(channel_count)
        for name in per_channel
    ]
    if channel_count == 2:
        names.extend(
            (
                "cross_correlation_zero_lag",
                "cross_correlation_peak",
                "cross_correlation_peak_lag",
                "log_rms_ratio_ch1_ch2",
                "normalized_rms_difference",
                "differential_rms",
                "common_mode_rms",
            )
        )
    return tuple(names)


def extract_features(
    window: np.ndarray | list[float],
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
) -> np.ndarray:
    """Extract a stable feature vector from one 350 ms sEMG window.

    Samples are rows and channels are columns.  A one-dimensional input is a
    one-channel window.  Linear detrending makes the features insensitive to
    the ESP32 ADC midpoint and slow electrode drift.
    """

    if isinstance(sample_rate_hz, bool) or not isinstance(
        sample_rate_hz, (int, np.integer)
    ):
        raise ValueError("sample_rate_hz must be a positive integer")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be a positive integer")
    expected_samples = round(sample_rate_hz * WINDOW_DURATION_SECONDS)
    values = _sample_matrix(window, name="window")
    if values.shape[0] != expected_samples:
        raise ValueError(
            f"window must contain exactly {expected_samples} samples "
            f"(350 ms at {sample_rate_hz} Hz)"
        )

    detrended = _linear_detrend(values)
    count, channels = detrended.shape
    taper = np.hanning(count)
    frequencies = np.fft.rfftfreq(count, d=1.0 / sample_rate_hz)
    split_a = count // 3
    split_b = (2 * count) // 3
    features: list[float] = []

    rms_values: list[float] = []
    for channel in range(channels):
        signal = detrended[:, channel]
        squares = signal * signal
        rms = float(np.sqrt(np.mean(squares)))
        mav = float(np.mean(np.abs(signal)))
        standard_deviation = float(np.std(signal))
        peak = float(np.max(np.abs(signal)))
        waveform_length = float(np.mean(np.abs(np.diff(signal))))
        crest_factor = peak / max(rms, _EPSILON)
        temporal_rms = (
            float(np.sqrt(np.mean(squares[:split_a]))),
            float(np.sqrt(np.mean(squares[split_a:split_b]))),
            float(np.sqrt(np.mean(squares[split_b:]))),
        )

        spectrum = np.fft.rfft(signal * taper)
        # This normalization keeps energy in squared ADC-count units while
        # being stable across supported sample rates.
        power = (np.abs(spectrum) ** 2) / max(float(np.sum(taper * taper)), 1.0)
        band_energies: list[float] = []
        for low_hz, high_hz in _BANDS_HZ:
            mask = (frequencies >= low_hz) & (frequencies < high_hz)
            band_energies.append(float(np.sum(power[mask]) / count))
        total_band_energy = sum(band_energies)
        band_fractions = [
            energy / max(total_band_energy, _EPSILON)
            for energy in band_energies
        ]

        rms_values.append(rms)
        features.extend(
            (
                rms,
                mav,
                standard_deviation,
                peak,
                waveform_length,
                _zero_crossing_rate(signal),
                crest_factor,
                *temporal_rms,
                *(float(np.log1p(energy)) for energy in band_energies),
                *band_fractions,
            )
        )

    if channels == 2:
        first = detrended[:, 0]
        second = detrended[:, 1]
        normalization = max(rms_values[0] * rms_values[1] * count, _EPSILON)
        zero_lag = float(np.dot(first, second) / normalization)
        maximum_lag = min(round(sample_rate_hz * 0.025), count - 1)
        correlations: list[float] = []
        lags = range(-maximum_lag, maximum_lag + 1)
        for lag in lags:
            if lag < 0:
                left, right = first[-lag:], second[: count + lag]
            elif lag > 0:
                left, right = first[: count - lag], second[lag:]
            else:
                left, right = first, second
            denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
            correlations.append(
                float(np.dot(left, right) / denominator)
                if denominator > _EPSILON
                else 0.0
            )
        peak_index = int(np.argmax(np.abs(correlations)))
        peak_correlation = correlations[peak_index]
        peak_lag = tuple(lags)[peak_index] / max(maximum_lag, 1)
        first_rms, second_rms = rms_values
        features.extend(
            (
                zero_lag,
                peak_correlation,
                float(peak_lag),
                float(np.log((first_rms + _EPSILON) / (second_rms + _EPSILON))),
                (first_rms - second_rms)
                / max(first_rms + second_rms, _EPSILON),
                float(np.sqrt(np.mean((first - second) ** 2))),
                float(np.sqrt(np.mean(((first + second) * 0.5) ** 2))),
            )
        )

    result = np.asarray(features, dtype=np.float64)
    expected_feature_count = len(feature_names(channels))
    if result.shape != (expected_feature_count,) or not np.all(np.isfinite(result)):
        raise RuntimeError("feature extraction produced an invalid feature vector")
    return result


def select_max_energy_window(
    samples: np.ndarray | list[float],
    window_samples: int = DEFAULT_WINDOW_SAMPLES,
) -> np.ndarray:
    """Return the highest locally detrended-energy window from a cue capture.

    In two-channel captures each channel is normalized by its energy across the
    complete cue.  A high-gain channel therefore cannot hide a contraction on
    the other sensor.  Ties resolve to the earliest window deterministically.
    """

    if isinstance(window_samples, bool) or not isinstance(
        window_samples, (int, np.integer)
    ):
        raise ValueError("window_samples must be an integer")
    if window_samples < 4:
        raise ValueError("window_samples must be at least 4")
    original = np.asarray(samples)
    values = _sample_matrix(samples, name="samples")
    if values.shape[0] < window_samples:
        raise ValueError(
            f"samples must contain at least {window_samples} rows"
        )
    if values.shape[0] == window_samples:
        selected = values.copy()
    else:
        cue_energy = np.sqrt(np.mean(_linear_detrend(values) ** 2, axis=0))
        scales = np.maximum(cue_energy, 1e-9)
        best_start = 0
        best_energy = float("-inf")
        for start in range(values.shape[0] - window_samples + 1):
            candidate = _linear_detrend(values[start : start + window_samples])
            energy = float(np.mean((candidate / scales) ** 2))
            if energy > best_energy:
                best_start = start
                best_energy = energy
        selected = values[best_start : best_start + window_samples].copy()
    if original.ndim == 1:
        return selected[:, 0]
    return selected


def _label_array(labels: Any, expected_count: int | None = None) -> np.ndarray:
    raw = np.asarray(labels)
    if raw.ndim != 1:
        raise ValueError("labels must be one-dimensional")
    if expected_count is not None and raw.size != expected_count:
        raise ValueError("features and labels must contain the same number of rows")
    values: list[str] = []
    for label in raw.tolist():
        if not isinstance(label, (str, np.str_)):
            raise ValueError("labels must be non-empty strings")
        normalized = str(label).strip()
        if not normalized:
            raise ValueError("labels must be non-empty strings")
        values.append(normalized)
    return np.asarray(values, dtype=np.str_)


def _feature_matrix(features: Any, *, expected_columns: int | None = None) -> np.ndarray:
    try:
        values = np.asarray(features, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("features must be numeric") from exc
    if values.ndim != 2:
        raise ValueError("features must be a two-dimensional matrix")
    if values.shape[0] == 0 or values.shape[1] == 0:
        raise ValueError("features cannot be empty")
    if expected_columns is not None and values.shape[1] != expected_columns:
        raise ValueError(
            f"expected {expected_columns} features, received {values.shape[1]}"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError("features must contain only finite values")
    return values


class RegularizedLDA:
    """Multiclass linear discriminant analysis with covariance shrinkage."""

    def __init__(self, regularization: float = 0.20, ridge: float = 1e-6):
        if not np.isfinite(regularization) or not 0.0 <= regularization <= 1.0:
            raise ValueError("regularization must be between 0 and 1")
        if not np.isfinite(ridge) or ridge <= 0.0:
            raise ValueError("ridge must be positive")
        self.regularization = float(regularization)
        self.ridge = float(ridge)
        self.labels_: np.ndarray | None = None
        self.channel_count_: int | None = None
        self.feature_mean_: np.ndarray | None = None
        self.feature_scale_: np.ndarray | None = None
        self.class_means_: np.ndarray | None = None
        self.precision_: np.ndarray | None = None
        self.priors_: np.ndarray | None = None

    @property
    def fitted(self) -> bool:
        return self.labels_ is not None

    @property
    def channel_count(self) -> int:
        self._require_fitted()
        assert self.channel_count_ is not None
        return self.channel_count_

    @property
    def labels(self) -> tuple[str, ...]:
        self._require_fitted()
        assert self.labels_ is not None
        return tuple(str(label) for label in self.labels_)

    def _require_fitted(self) -> None:
        if not self.fitted:
            raise RuntimeError("classifier has not been fitted")

    def fit(
        self,
        features: Any,
        labels: Any,
        *,
        channel_count: int,
    ) -> RegularizedLDA:
        """Fit pooled-covariance multiclass LDA and return ``self``."""

        if channel_count not in (1, 2):
            raise ValueError("channel_count must be 1 or 2")
        matrix = _feature_matrix(features)
        label_values = _label_array(labels, matrix.shape[0])
        classes, inverse, counts = np.unique(
            label_values, return_inverse=True, return_counts=True
        )
        if classes.size < 2:
            raise ValueError("at least two distinct classes are required")
        if np.any(counts < 2):
            raise ValueError("each class requires at least two training examples")

        feature_mean = np.mean(matrix, axis=0)
        feature_scale = np.std(matrix, axis=0)
        feature_scale = np.where(feature_scale > 1e-12, feature_scale, 1.0)
        standardized = (matrix - feature_mean) / feature_scale
        class_means = np.vstack(
            [
                np.mean(standardized[inverse == index], axis=0)
                for index in range(classes.size)
            ]
        )
        residuals = standardized - class_means[inverse]
        degrees_of_freedom = matrix.shape[0] - classes.size
        covariance = (residuals.T @ residuals) / max(degrees_of_freedom, 1)
        diagonal = np.diag(np.diag(covariance))
        covariance = (
            (1.0 - self.regularization) * covariance
            + self.regularization * diagonal
        )
        average_variance = max(float(np.mean(np.diag(covariance))), 1e-12)
        covariance += np.eye(matrix.shape[1]) * self.ridge * average_variance
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        floor = max(average_variance * self.ridge, 1e-12)
        eigenvalues = np.maximum(eigenvalues, floor)
        precision = (eigenvectors / eigenvalues) @ eigenvectors.T

        self.labels_ = classes.astype(np.str_)
        self.channel_count_ = int(channel_count)
        self.feature_mean_ = feature_mean
        self.feature_scale_ = feature_scale
        self.class_means_ = class_means
        self.precision_ = precision
        self.priors_ = counts.astype(np.float64) / matrix.shape[0]
        return self

    def _scores(self, features: Any) -> tuple[np.ndarray, bool]:
        self._require_fitted()
        assert self.feature_mean_ is not None
        assert self.feature_scale_ is not None
        assert self.class_means_ is not None
        assert self.precision_ is not None
        assert self.priors_ is not None
        raw = np.asarray(features)
        single = raw.ndim == 1
        if single:
            raw = raw[np.newaxis, :]
        matrix = _feature_matrix(raw, expected_columns=self.feature_mean_.size)
        standardized = (matrix - self.feature_mean_) / self.feature_scale_
        weights = self.class_means_ @ self.precision_
        intercept = -0.5 * np.sum(weights * self.class_means_, axis=1)
        intercept += np.log(self.priors_)
        return standardized @ weights.T + intercept, single

    def predict_proba(self, features: Any) -> np.ndarray:
        """Return posterior probabilities; one-dimensional input stays 1-D."""

        scores, single = self._scores(features)
        scores -= np.max(scores, axis=1, keepdims=True)
        exponential = np.exp(scores)
        probabilities = exponential / np.sum(exponential, axis=1, keepdims=True)
        return probabilities[0] if single else probabilities

    def predict(self, features: Any) -> str | np.ndarray:
        """Return the most likely label for one vector or a matrix of vectors."""

        probabilities = self.predict_proba(features)
        assert self.labels_ is not None
        if probabilities.ndim == 1:
            return str(self.labels_[int(np.argmax(probabilities))])
        return self.labels_[np.argmax(probabilities, axis=1)].copy()

    def predict_with_margin(self, features: Any) -> tuple[str, float, float]:
        """Predict one vector as ``(label, probability, top-two margin)``."""

        raw = np.asarray(features)
        if raw.ndim == 2 and raw.shape[0] == 1:
            raw = raw[0]
        if raw.ndim != 1:
            raise ValueError("predict_with_margin expects one feature vector")
        probabilities = self.predict_proba(raw)
        assert self.labels_ is not None
        order = np.argsort(probabilities)
        winner = int(order[-1])
        probability = float(probabilities[winner])
        margin = probability - float(probabilities[int(order[-2])])
        return str(self.labels_[winner]), probability, margin

    def save(self, path: str | Path) -> Path:
        """Atomically save a fitted classifier to a non-pickle NPZ file."""

        self._require_fitted()
        assert self.labels_ is not None
        assert self.channel_count_ is not None
        assert self.feature_mean_ is not None
        assert self.feature_scale_ is not None
        assert self.class_means_ is not None
        assert self.precision_ is not None
        assert self.priors_ is not None
        destination = Path(path)
        if destination.suffix.lower() != ".npz":
            raise ValueError("classifier path must end in .npz")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                np.savez_compressed(
                    temporary,
                    schema_version=np.asarray(MODEL_SCHEMA_VERSION, dtype=np.int64),
                    regularization=np.asarray(self.regularization, dtype=np.float64),
                    ridge=np.asarray(self.ridge, dtype=np.float64),
                    channel_count=np.asarray(self.channel_count_, dtype=np.int64),
                    labels=self.labels_,
                    feature_mean=self.feature_mean_,
                    feature_scale=self.feature_scale_,
                    class_means=self.class_means_,
                    precision=self.precision_,
                    priors=self.priors_,
                )
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, destination)
        except Exception:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
            raise
        return destination

    @classmethod
    def load(cls, path: str | Path) -> RegularizedLDA:
        """Load and rigorously validate a classifier written by :meth:`save`."""

        source = Path(path)
        required = {
            "schema_version",
            "regularization",
            "ridge",
            "channel_count",
            "labels",
            "feature_mean",
            "feature_scale",
            "class_means",
            "precision",
            "priors",
        }
        try:
            with np.load(source, allow_pickle=False) as archive:
                missing = required.difference(archive.files)
                if missing:
                    raise ValueError(
                        "classifier file is missing: " + ", ".join(sorted(missing))
                    )
                schema_version = int(np.asarray(archive["schema_version"]).item())
                regularization = float(np.asarray(archive["regularization"]).item())
                ridge = float(np.asarray(archive["ridge"]).item())
                channel_count = int(np.asarray(archive["channel_count"]).item())
                labels = np.asarray(archive["labels"]).copy()
                feature_mean = np.asarray(archive["feature_mean"], dtype=np.float64).copy()
                feature_scale = np.asarray(archive["feature_scale"], dtype=np.float64).copy()
                class_means = np.asarray(archive["class_means"], dtype=np.float64).copy()
                precision = np.asarray(archive["precision"], dtype=np.float64).copy()
                priors = np.asarray(archive["priors"], dtype=np.float64).copy()
        except (OSError, ValueError, TypeError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("classifier file"):
                raise
            raise ValueError(f"could not read classifier file: {exc}") from exc

        if schema_version != MODEL_SCHEMA_VERSION:
            raise ValueError(f"unsupported classifier schema version {schema_version}")
        if channel_count not in (1, 2):
            raise ValueError("classifier contains an invalid channel count")
        if labels.ndim != 1 or labels.size < 2 or labels.dtype.kind not in "US":
            raise ValueError("classifier contains invalid labels")
        labels = _label_array(labels)
        if np.unique(labels).size != labels.size:
            raise ValueError("classifier labels must be unique")
        feature_count = feature_mean.size
        if feature_mean.ndim != 1 or feature_count == 0:
            raise ValueError("classifier contains an invalid feature mean")
        if feature_scale.shape != (feature_count,) or np.any(feature_scale <= 0):
            raise ValueError("classifier contains an invalid feature scale")
        if class_means.shape != (labels.size, feature_count):
            raise ValueError("classifier contains invalid class means")
        if precision.shape != (feature_count, feature_count):
            raise ValueError("classifier contains an invalid precision matrix")
        if priors.shape != (labels.size,) or np.any(priors <= 0):
            raise ValueError("classifier contains invalid priors")
        arrays = (feature_mean, feature_scale, class_means, precision, priors)
        if not all(np.all(np.isfinite(array)) for array in arrays):
            raise ValueError("classifier contains non-finite values")
        if not np.allclose(precision, precision.T, rtol=1e-7, atol=1e-9):
            raise ValueError("classifier precision matrix is not symmetric")
        if not np.isclose(float(np.sum(priors)), 1.0, rtol=1e-7, atol=1e-9):
            raise ValueError("classifier priors do not sum to one")

        model = cls(regularization=regularization, ridge=ridge)
        model.labels_ = labels
        model.channel_count_ = channel_count
        model.feature_mean_ = feature_mean
        model.feature_scale_ = feature_scale
        model.class_means_ = class_means
        model.precision_ = precision
        model.priors_ = priors
        return model


def stratified_holdout_evaluate(
    features: Any,
    labels: Any,
    *,
    channel_count: int,
    test_fraction: float = 0.25,
    random_state: int = 0,
    regularization: float = 0.20,
) -> dict[str, Any]:
    """Run one deterministic, class-stratified held-out evaluation."""

    if not np.isfinite(test_fraction) or not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between 0 and 1")
    if isinstance(random_state, bool) or not isinstance(
        random_state, (int, np.integer)
    ):
        raise ValueError("random_state must be an integer")
    matrix = _feature_matrix(features)
    label_values = _label_array(labels, matrix.shape[0])
    classes, counts = np.unique(label_values, return_counts=True)
    if classes.size < 2:
        raise ValueError("at least two distinct classes are required")
    if np.any(counts < 2):
        raise ValueError("each class requires at least two examples for holdout")

    generator = np.random.default_rng(int(random_state))
    train_indices: list[int] = []
    test_indices: list[int] = []
    for label, count in zip(classes, counts, strict=True):
        indices = np.flatnonzero(label_values == label)
        shuffled = generator.permutation(indices)
        test_count = max(1, int(np.floor(float(count) * test_fraction + 0.5)))
        test_count = min(test_count, int(count) - 1)
        test_indices.extend(int(index) for index in shuffled[:test_count])
        train_indices.extend(int(index) for index in shuffled[test_count:])
    train = np.asarray(sorted(train_indices), dtype=np.int64)
    test = np.asarray(sorted(test_indices), dtype=np.int64)

    model = RegularizedLDA(regularization=regularization).fit(
        matrix[train], label_values[train], channel_count=channel_count
    )
    probabilities = np.asarray(model.predict_proba(matrix[test]))
    predicted = np.asarray(model.predict(matrix[test]), dtype=np.str_)
    truth = label_values[test]
    confusion = np.zeros((classes.size, classes.size), dtype=np.int64)
    class_index = {str(label): index for index, label in enumerate(classes)}
    for actual, prediction in zip(truth, predicted, strict=True):
        confusion[class_index[str(actual)], class_index[str(prediction)]] += 1
    recalls = np.divide(
        np.diag(confusion),
        np.sum(confusion, axis=1),
        out=np.zeros(classes.size, dtype=np.float64),
        where=np.sum(confusion, axis=1) > 0,
    )
    sorted_probabilities = np.sort(probabilities, axis=1)
    margins = sorted_probabilities[:, -1] - sorted_probabilities[:, -2]
    accuracy = float(np.mean(predicted == truth))
    return {
        "accuracy": accuracy,
        "balanced_accuracy": float(np.mean(recalls)),
        "mean_margin": float(np.mean(margins)),
        "labels": [str(label) for label in classes],
        "per_class_recall": {
            str(label): float(recall)
            for label, recall in zip(classes, recalls, strict=True)
        },
        "confusion_matrix": confusion.tolist(),
        "train_count": int(train.size),
        "test_count": int(test.size),
        "test_indices": test.tolist(),
        "predictions": [str(label) for label in predicted],
    }
