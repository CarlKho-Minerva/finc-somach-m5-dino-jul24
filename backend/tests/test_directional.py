from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from somach.directional import (
    RegularizedLDA,
    extract_features,
    feature_names,
    parse_sample_line,
    select_max_energy_window,
    stratified_holdout_evaluate,
)


def test_sample_parser_accepts_one_and_two_channels_and_ignores_meta() -> None:
    assert parse_sample_line("#META,rate=1000") is None
    assert parse_sample_line(b"  #meta channels=2\r\n") is None
    assert parse_sample_line("  \n") is None
    np.testing.assert_array_equal(parse_sample_line("2048"), [2048.0])
    np.testing.assert_array_equal(
        parse_sample_line("2048, 2051", expected_channels=2),
        [2048.0, 2051.0],
    )

    with pytest.raises(ValueError, match="expected 2 channel"):
        parse_sample_line("2048", expected_channels=2)
    with pytest.raises(ValueError, match="finite"):
        parse_sample_line("nan")
    with pytest.raises(ValueError, match="one value or two"):
        parse_sample_line("1,2,3")


def test_features_are_finite_detrended_and_frequency_sensitive() -> None:
    sample_rate = 1_000
    time_axis = np.arange(350) / sample_rate
    low = 50.0 * np.sin(2 * np.pi * 40.0 * time_axis)
    low_with_offset_and_trend = low + 2_000.0 + np.linspace(-80.0, 100.0, 350)
    high = 50.0 * np.sin(2 * np.pi * 180.0 * time_axis)

    baseline_features = extract_features(low)
    drifted_features = extract_features(low_with_offset_and_trend)
    high_features = extract_features(high)
    assert baseline_features.shape == (len(feature_names(1)),)
    assert np.all(np.isfinite(baseline_features))
    np.testing.assert_allclose(baseline_features, drifted_features, rtol=1e-10, atol=1e-10)

    names = feature_names(1)
    low_band = names.index("ch1_log_band_energy_20_60")
    high_band = names.index("ch1_log_band_energy_120_250")
    assert baseline_features[low_band] > baseline_features[high_band]
    assert high_features[high_band] > high_features[low_band]


def test_two_channel_features_capture_spatial_relationship() -> None:
    time_axis = np.arange(350) / 1_000
    first = np.sin(2 * np.pi * 83 * time_axis)
    in_phase = extract_features(np.column_stack((first, first)))
    anti_phase = extract_features(np.column_stack((first, -first)))
    names = feature_names(2)

    assert in_phase.shape == (len(names),)
    correlation = names.index("cross_correlation_zero_lag")
    common = names.index("common_mode_rms")
    differential = names.index("differential_rms")
    assert in_phase[correlation] == pytest.approx(1.0, abs=1e-12)
    assert anti_phase[correlation] == pytest.approx(-1.0, abs=1e-12)
    assert in_phase[common] > anti_phase[common]
    assert anti_phase[differential] > in_phase[differential]


def test_max_energy_selector_finds_burst_and_preserves_shape() -> None:
    generator = np.random.default_rng(4)
    samples = generator.normal(0.0, 0.2, (1_200, 2))
    time_axis = np.arange(260) / 1_000
    burst = 20.0 * np.sin(2 * np.pi * 90 * time_axis)
    samples[610:870, 1] += burst

    selected = select_max_energy_window(samples)
    assert selected.shape == (350, 2)
    assert float(np.sqrt(np.mean(selected[:, 1] ** 2))) > 8.0

    one_channel = select_max_energy_window(samples[:, 1])
    assert one_channel.shape == (350,)


def _directional_dataset() -> tuple[np.ndarray, np.ndarray]:
    generator = np.random.default_rng(91)
    sample_rate = 1_000
    time_axis = np.arange(350) / sample_rate
    envelope = np.sin(np.pi * np.arange(350) / 349) ** 2
    specifications = {
        "up": (70.0, 90.0, 25.0, 1.0),
        "down": (145.0, 25.0, 90.0, 1.0),
        "left": (45.0, 75.0, 75.0, 1.0),
        "right": (205.0, 75.0, 75.0, -1.0),
    }
    features: list[np.ndarray] = []
    labels: list[str] = []
    for label, (frequency, first_gain, second_gain, phase_sign) in specifications.items():
        for trial in range(12):
            phase = generator.uniform(-0.15, 0.15)
            carrier = np.sin(2 * np.pi * frequency * time_axis + phase)
            first = first_gain * carrier * envelope
            second = phase_sign * second_gain * carrier * envelope
            window = np.column_stack((first, second))
            window += generator.normal(0.0, 2.0, window.shape)
            window += 2_000.0 + trial * 0.5
            features.append(extract_features(window))
            labels.append(label)
    return np.vstack(features), np.asarray(labels)


def test_multiclass_lda_predicts_probabilities_margin_and_round_trips(
    tmp_path: Path,
) -> None:
    features, labels = _directional_dataset()
    model = RegularizedLDA(regularization=0.25).fit(
        features, labels, channel_count=2
    )
    label, probability, margin = model.predict_with_margin(features[0])
    probabilities = model.predict_proba(features[0])

    assert label == labels[0]
    assert 0.0 <= margin <= probability <= 1.0
    assert probabilities.shape == (4,)
    assert float(np.sum(probabilities)) == pytest.approx(1.0)

    path = model.save(tmp_path / "directional.npz")
    restored = RegularizedLDA.load(path)
    assert restored.channel_count == 2
    assert restored.labels == model.labels
    np.testing.assert_allclose(
        restored.predict_proba(features[:5]), model.predict_proba(features[:5])
    )
    restored_label, restored_probability, restored_margin = (
        restored.predict_with_margin(features[0])
    )
    assert restored_label == label
    assert restored_probability == pytest.approx(probability)
    assert restored_margin == pytest.approx(margin)


def test_stratified_holdout_is_deterministic_and_actually_held_out() -> None:
    features, labels = _directional_dataset()
    first = stratified_holdout_evaluate(
        features, labels, channel_count=2, random_state=17
    )
    second = stratified_holdout_evaluate(
        features, labels, channel_count=2, random_state=17
    )

    assert first == second
    assert first["accuracy"] > 0.9
    assert first["balanced_accuracy"] > 0.9
    assert first["test_count"] == 12
    assert len(set(first["test_indices"])) == first["test_count"]
    assert np.asarray(first["confusion_matrix"]).shape == (4, 4)


def test_directional_validation_rejects_unsafe_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly 350"):
        extract_features(np.zeros(349))
    with pytest.raises(ValueError, match="finite"):
        extract_features(np.full(350, np.nan))
    with pytest.raises(ValueError, match="at least 350"):
        select_max_energy_window(np.zeros(200))

    model = RegularizedLDA()
    with pytest.raises(RuntimeError, match="not been fitted"):
        model.predict_with_margin(np.zeros(3))
    with pytest.raises(ValueError, match="at least two distinct"):
        model.fit(np.zeros((4, 3)), ["up"] * 4, channel_count=1)
    with pytest.raises(ValueError, match="two training"):
        model.fit(
            np.asarray([[0.0], [1.0], [2.0]]),
            ["up", "up", "down"],
            channel_count=1,
        )

    corrupt = tmp_path / "corrupt.npz"
    np.savez(corrupt, schema_version=np.asarray(1))
    with pytest.raises(ValueError, match="missing"):
        RegularizedLDA.load(corrupt)
