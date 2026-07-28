from __future__ import annotations

from pathlib import Path

import numpy as np

from drive import Collection, clipping_fraction, train_model
from somach.directional import feature_names


def test_train_model_uses_collection_channel_count_and_writes_artifacts(
    tmp_path: Path,
) -> None:
    generator = np.random.default_rng(19)
    labels = np.repeat(["up", "down", "left", "right", "noise"], 8)
    feature_count = len(feature_names(1))
    centers = np.zeros((5, feature_count))
    for index in range(5):
        centers[index, index] = 8.0
    features = np.vstack(
        [
            centers[index] + generator.normal(0.0, 0.15, feature_count)
            for index in range(5)
            for _ in range(8)
        ]
    )
    collection = Collection(
        windows=np.zeros((40, 350, 1), dtype=np.float32),
        labels=labels,
        features=features,
        dataset_path=tmp_path / "trials.npz",
    )
    model_path = tmp_path / "drive.npz"

    model, metrics = train_model(collection, model_path=model_path, seed=11)

    assert model.channel_count == 1
    assert metrics["balanced_accuracy"] == 1.0
    assert model_path.exists()
    assert model_path.with_suffix(".metrics.json").exists()


def test_clipping_fraction_counts_both_adc_rails() -> None:
    samples = np.asarray([[0.0, 2_000.0], [4_095.0, 2_100.0]])
    assert clipping_fraction(samples) == 0.5
