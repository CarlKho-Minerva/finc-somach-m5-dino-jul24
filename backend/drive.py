#!/usr/bin/env python3
"""Guided, session-trained silent-speech controller for a browser driving game.

This experiment is intentionally separate from the proven one-channel Flappy
runtime.  It accepts either the original single-value serial stream or the
dual-AD8232 ``a,b`` stream, trains on the current wearer/session, rejects a
learned NOISE class, and posts bounded macOS arrow-key pulses.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from somach.directional import (
    RegularizedLDA,
    extract_features,
    parse_sample_line,
    select_max_energy_window,
    stratified_holdout_evaluate,
)
from somach.keypress import KeypressResult, QuartzKeyInjector
from somach.sources import HardwareUnavailable, detect_serial_port, parse_meta_line


COMMAND_LABELS = ("up", "down", "left", "right")
TRAINING_LABELS = (*COMMAND_LABELS, "noise")
NOISE_CUES = (
    "SWALLOW ONCE",
    "SAY HELLO OUT LOUD",
    "CLENCH YOUR JAW ONCE",
    "TURN YOUR HEAD SLIGHTLY",
    "RELAX AND DO NOTHING",
)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def at_least_four(value: str) -> int:
    parsed = int(value)
    if parsed < 4:
        raise argparse.ArgumentTypeError("must be at least 4")
    return parsed


def probability(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description=(
            "Train UP/DOWN/LEFT/RIGHT on the current wearer and control the "
            "frontmost macOS driving game with bounded arrow-key pulses."
        )
    )
    command.add_argument(
        "mode",
        nargs="?",
        choices=("train-play", "train", "play"),
        default="train-play",
    )
    command.add_argument("--channels", type=int, choices=(1, 2), default=2)
    command.add_argument("--serial-port")
    command.add_argument(
        "--baud",
        type=positive_int,
        help="defaults to 460800 for two channels and 115200 for one",
    )
    command.add_argument("--sample-rate", type=positive_int, default=1_000)
    command.add_argument(
        "--trials",
        type=at_least_four,
        default=8,
        help="guided trials per class; 8 means 40 total including NOISE",
    )
    command.add_argument(
        "--model",
        type=Path,
        default=Path("datasets/directional_drive_model.npz"),
    )
    command.add_argument("--seed", type=int, default=27)
    command.add_argument("--confidence", type=probability, default=0.58)
    command.add_argument("--margin", type=probability, default=0.10)
    command.add_argument("--energy-sigma", type=float, default=4.0)
    command.add_argument(
        "--energy-threshold",
        type=float,
        help="manual centered-ADC RMS gate; normally auto-calibrated",
    )
    command.add_argument(
        "--force",
        action="store_true",
        help="allow play after weak same-session holdout validation",
    )
    command.add_argument(
        "--no-keypress",
        action="store_true",
        help="show predictions without posting macOS arrow events",
    )
    return command


@dataclass(slots=True)
class Collection:
    windows: np.ndarray
    labels: np.ndarray
    features: np.ndarray
    dataset_path: Path


class SerialFrameReader:
    """Synchronous serial reader with the same ESP32 reset protections as SOMACH."""

    def __init__(
        self,
        *,
        channels: int,
        sample_rate_hz: int,
        baud_rate: int,
        serial_port: str | None,
    ):
        self.channels = channels
        self.sample_rate_hz = sample_rate_hz
        self.baud_rate = baud_rate
        self.port = detect_serial_port(serial_port)
        self.connection = None
        self.meta: dict[str, str] = {}
        self.invalid_lines = 0

    def __enter__(self) -> "SerialFrameReader":
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - dependency bootstrap
            raise HardwareUnavailable("pyserial is missing; run `uv sync` first") from exc

        connection = serial.Serial(
            port=None,
            baudrate=self.baud_rate,
            timeout=0.1,
            write_timeout=1.0,
            inter_byte_timeout=0.1,
            dsrdtr=False,
            rtscts=False,
        )
        connection.dtr = False
        connection.rts = False
        connection.port = self.port
        try:
            connection.open()
        except (OSError, serial.SerialException) as exc:
            connection.close()
            raise HardwareUnavailable(
                f"Could not open {self.port}: {exc}. Close every serial monitor."
            ) from exc

        # Do not parse ESP32 bootloader text or stale samples.  Disabling the
        # handshake modes/line states before open prevents the old reset loop.
        time.sleep(2.0)
        connection.reset_input_buffer()
        self.connection = connection
        return self

    def __exit__(self, *_: object) -> None:
        if self.connection is not None and self.connection.is_open:
            self.connection.close()

    def _contact_error(self) -> str | None:
        active = (
            "leads_off",
            "lo_plus",
            "lo_minus",
            "a_leads_off",
            "a_lo_plus",
            "a_lo_minus",
            "b_leads_off",
            "b_lo_plus",
            "b_lo_minus",
        )
        bad = [name for name in active if self.meta.get(name, "0").lower() in {"1", "true", "yes"}]
        return f"Electrode lead-off active: {', '.join(bad)}" if bad else None

    def assert_contact(self) -> None:
        error = self._contact_error()
        if error:
            raise RuntimeError(f"{error}. Press every pad firmly, then retry.")

    def read_frame(self, *, deadline: float) -> np.ndarray:
        if self.connection is None:
            raise RuntimeError("serial reader is not open")
        while time.monotonic() < deadline:
            payload = self.connection.readline()
            if not payload:
                continue
            line = payload.decode("ascii", errors="ignore").strip()
            if line.startswith("#META"):
                self.meta.update(parse_meta_line(line))
                self.assert_contact()
                continue
            frame = parse_sample_line(line, expected_channels=self.channels)
            if frame is not None:
                if np.any(frame < 0) or np.any(frame > 4_095):
                    raise ValueError(
                        "ADC samples must be in the ESP32 12-bit range 0..4095"
                    )
                return frame
            self.invalid_lines += 1
        expected = "one integer" if self.channels == 1 else "two CSV integers"
        raise RuntimeError(
            f"Timed out waiting for {expected} from {self.port} at {self.baud_rate} baud. "
            "Flash the matching firmware and close the serial monitor."
        )

    def read_frames(self, count: int) -> np.ndarray:
        if count <= 0:
            return np.empty((0, self.channels), dtype=np.float64)
        duration = count / self.sample_rate_hz
        deadline = time.monotonic() + max(4.0, duration * 2.5 + 1.0)
        frames = [self.read_frame(deadline=deadline) for _ in range(count)]
        result = np.asarray(frames, dtype=np.float64)
        if result.shape != (count, self.channels):
            raise RuntimeError(f"Unexpected serial frame shape {result.shape}")
        return result

    def read_seconds(self, seconds: float) -> np.ndarray:
        return self.read_frames(max(1, round(seconds * self.sample_rate_hz)))

    def warm_up(self) -> None:
        print("Checking stream and electrode contact...")
        self.read_seconds(1.2)
        self.assert_contact()
        if self.meta.get("rate_hz"):
            try:
                observed = float(self.meta["rate_hz"])
            except ValueError:
                observed = 0.0
            if observed and not 0.9 * self.sample_rate_hz <= observed <= 1.1 * self.sample_rate_hz:
                raise RuntimeError(
                    f"Firmware reports {observed:.1f} Hz; expected about {self.sample_rate_hz} Hz."
                )
        print(f"Live: {self.channels} channel(s) from {self.port} at {self.baud_rate} baud.")


def centered_energy(window: np.ndarray) -> float:
    values = np.asarray(window, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] == 0:
        raise ValueError("energy window must be a non-empty samples x channels array")
    centered = values - np.mean(values, axis=0, keepdims=True)
    return float(np.sqrt(np.mean(np.square(centered))))


def clipping_fraction(window: np.ndarray) -> float:
    values = np.asarray(window)
    return float(np.mean((values <= 4) | (values >= 4_091)))


def countdown(reader: SerialFrameReader, cue: str) -> np.ndarray:
    for number in (3, 2, 1):
        print(f"  {number}", flush=True)
        reader.read_seconds(0.38)
    print(f"\a>>> {cue} <<<", flush=True)
    return reader.read_seconds(0.90)


def collect_training(
    reader: SerialFrameReader,
    *,
    trials_per_class: int,
    sample_rate_hz: int,
    seed: int,
    model_path: Path,
) -> Collection:
    print(
        "\nTRAINING\n"
        "Silently articulate the requested direction with the same closed-mouth gesture each time.\n"
        "For NOISE, perform only the nuisance action shown. Detection stays off during training."
    )
    input("Press Return once you are comfortable and ready: ")
    reader.read_seconds(0.5)

    schedule = [label for label in TRAINING_LABELS for _ in range(trials_per_class)]
    random.Random(seed).shuffle(schedule)
    noise_index = 0
    windows: list[np.ndarray] = []
    labels: list[str] = []
    window_samples = round(sample_rate_hz * 0.350)

    for index, label in enumerate(schedule, start=1):
        if label == "noise":
            cue = f"NOISE: {NOISE_CUES[noise_index % len(NOISE_CUES)]}"
            noise_index += 1
        else:
            cue = f"SILENTLY ARTICULATE {label.upper()}"
        print(f"\n[{index:02d}/{len(schedule):02d}] Prepare — {cue}")
        reader.read_seconds(0.40)
        captured = countdown(reader, cue)
        if clipping_fraction(captured) > 0.01:
            raise RuntimeError(
                "ADC clipping exceeded 1% during a trial. Reseat/replace pads or adjust the sensor before collecting data."
            )
        window = select_max_energy_window(captured, window_samples=window_samples)
        windows.append(window)
        labels.append(label)
        reader.read_seconds(0.35)

    stacked = np.stack(windows)
    label_array = np.asarray(labels)
    features = np.vstack(
        [extract_features(window, sample_rate_hz=sample_rate_hz) for window in stacked]
    )

    model_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dataset_path = model_path.with_name(f"directional_trials_{stamp}.npz")
    np.savez_compressed(
        dataset_path,
        windows=stacked.astype(np.float32),
        labels=label_array,
        sample_rate_hz=np.asarray(sample_rate_hz),
        channels=np.asarray(reader.channels),
    )
    print(f"\nSaved {len(labels)} labeled trials to {dataset_path}")
    return Collection(stacked, label_array, features, dataset_path)


def train_model(
    collection: Collection,
    *,
    model_path: Path,
    seed: int,
) -> tuple[RegularizedLDA, dict]:
    channel_count = int(collection.windows.shape[2])
    metrics = stratified_holdout_evaluate(
        collection.features,
        collection.labels,
        channel_count=channel_count,
        test_fraction=0.25,
        random_state=seed,
    )
    model = RegularizedLDA().fit(
        collection.features,
        collection.labels,
        channel_count=channel_count,
    )
    model.save(model_path)
    metrics_path = model_path.with_suffix(".metrics.json")
    metrics_path.write_text(
        json.dumps(
            {
                **metrics,
                "dataset": str(collection.dataset_path),
                "model": str(model_path),
                "note": "Quick same-session stratified holdout; not cross-user validation.",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        "\nQUICK HOLDOUT\n"
        f"  balanced accuracy: {metrics['balanced_accuracy']:.1%}\n"
        f"  accuracy:          {metrics['accuracy']:.1%}\n"
        f"  samples held out:  {metrics['test_count']}\n"
        "This is only a same-session rehearsal metric."
    )
    print(
        "  class recall:      "
        + ", ".join(
            f"{label.upper()} {recall:.0%}"
            for label, recall in metrics["per_class_recall"].items()
        )
    )
    print(f"Saved model to {model_path}")
    return model, metrics


def calibrate_energy(
    reader: SerialFrameReader,
    *,
    sample_rate_hz: int,
    sigma: float,
    manual_threshold: float | None,
) -> tuple[float, float, float]:
    print("\nBASELINE: relax jaw and tongue for three seconds.")
    baseline = reader.read_seconds(3.0)
    window = round(sample_rate_hz * 0.350)
    stride = max(1, round(sample_rate_hz * 0.050))
    energies = np.asarray(
        [centered_energy(baseline[start : start + window]) for start in range(0, len(baseline) - window + 1, stride)]
    )
    if not len(energies):
        raise RuntimeError("Not enough baseline samples")
    mean = float(np.mean(energies))
    std = float(np.std(energies))
    automatic = max(mean + sigma * std, mean * 1.55, 1.0)
    threshold = automatic if manual_threshold is None else float(manual_threshold)
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("energy threshold must be a positive finite number")
    print(f"Rest energy μ={mean:.2f}, σ={std:.2f}; event gate={threshold:.2f}")
    return threshold, mean, std


def post_command(injector: QuartzKeyInjector, label: str) -> KeypressResult:
    if label == "up":
        return injector.pulse_direction("up", duration_ms=650)
    if label == "down":
        return injector.pulse_direction("down", duration_ms=650)
    if label in {"left", "right"}:
        forward = injector.pulse_direction("up", duration_ms=500)
        turn = injector.pulse_direction(label, duration_ms=430)
        if not forward.posted:
            return forward
        return turn
    return KeypressResult(False, None, f"unsupported command {label!r}")


def play(
    reader: SerialFrameReader,
    model: RegularizedLDA,
    *,
    sample_rate_hz: int,
    confidence: float,
    margin_threshold: float,
    energy_sigma: float,
    manual_energy_threshold: float | None,
    no_keypress: bool,
) -> None:
    threshold, _rest_mean, _rest_std = calibrate_energy(
        reader,
        sample_rate_hz=sample_rate_hz,
        sigma=energy_sigma,
        manual_threshold=manual_energy_threshold,
    )
    injector = QuartzKeyInjector(enabled=not no_keypress)
    if not no_keypress and not injector.refresh_trust(prompt=True):
        raise RuntimeError(
            "macOS Accessibility permission is required. Enable your terminal/Python in "
            "System Settings > Privacy & Security > Accessibility, then restart."
        )

    print(
        "\nPLAY MODE\n"
        "Open https://bruno-simon.com, click the game canvas, and keep the browser frontmost.\n"
        "UP/DOWN pulse their arrows; LEFT/RIGHT pulse forward+steering.\n"
        "Rejected or NOISE predictions post no key. Press Control-C to release every key and stop.\n"
        "You have four seconds to focus the game..."
    )
    reader.read_seconds(4.0)

    window_samples = round(sample_rate_hz * 0.350)
    pretrigger_samples = round(sample_rate_hz * 0.100)
    posttrigger_samples = round(sample_rate_hz * 0.300)
    rearm_samples = round(sample_rate_hz * 0.180)
    history: deque[np.ndarray] = deque(maxlen=window_samples)
    for frame in reader.read_frames(window_samples):
        history.append(frame)
    low_samples = rearm_samples
    armed = True

    try:
        while True:
            frame = reader.read_frame(deadline=time.monotonic() + 2.0)
            history.append(frame)
            if len(history) < window_samples:
                continue
            current = np.asarray(history)
            energy = centered_energy(current)
            if energy < threshold * 0.65:
                low_samples += 1
                if low_samples >= rearm_samples:
                    armed = True
                continue
            low_samples = 0
            if not armed or energy < threshold:
                continue

            armed = False
            pretrigger = current[-pretrigger_samples:]
            posttrigger = reader.read_frames(posttrigger_samples)
            event = np.concatenate((pretrigger, posttrigger), axis=0)
            window = select_max_energy_window(event, window_samples=window_samples)
            feature = extract_features(window, sample_rate_hz=sample_rate_hz)
            label, winning_probability, margin = model.predict_with_margin(feature)
            accepted = (
                label in COMMAND_LABELS
                and winning_probability >= confidence
                and margin >= margin_threshold
            )
            if accepted:
                result = post_command(injector, label)
                state = "POSTED" if result.posted else f"BLOCKED: {result.reason}"
            else:
                result = KeypressResult(False, None, "confidence/noise rejection")
                state = "REJECTED"
            print(
                f"{label.upper():>5}  p={winning_probability:.2f}  margin={margin:.2f}  "
                f"energy={centered_energy(window):.1f}  {state}",
                flush=True,
            )
            history.clear()
            for post_frame in posttrigger[-min(len(posttrigger), window_samples) :]:
                history.append(post_frame)
    finally:
        injector.release_all()


def validate_model_channels(model: RegularizedLDA, channels: int) -> None:
    expected = getattr(model, "channel_count", None)
    if expected is not None and int(expected) != channels:
        raise ValueError(
            f"Model was trained for {expected} channels but --channels is {channels}. Retrain or choose the matching mode."
        )
    if set(model.labels) != set(TRAINING_LABELS):
        raise ValueError(
            "Model labels do not match UP/DOWN/LEFT/RIGHT/NOISE; retrain with this controller."
        )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    baud = args.baud or (460_800 if args.channels == 2 else 115_200)
    try:
        with SerialFrameReader(
            channels=args.channels,
            sample_rate_hz=args.sample_rate,
            baud_rate=baud,
            serial_port=args.serial_port,
        ) as reader:
            reader.warm_up()
            metrics: dict | None = None
            if args.mode in {"train", "train-play"}:
                collection = collect_training(
                    reader,
                    trials_per_class=args.trials,
                    sample_rate_hz=args.sample_rate,
                    seed=args.seed,
                    model_path=args.model,
                )
                model, metrics = train_model(
                    collection,
                    model_path=args.model,
                    seed=args.seed,
                )
                # Record channel count beside the model even when an older model
                # implementation does not expose custom metadata in its NPZ.
                args.model.with_suffix(".channels").write_text(f"{args.channels}\n", encoding="utf-8")
            else:
                if not args.model.exists():
                    raise FileNotFoundError(f"No model at {args.model}; run train-play first")
                model = RegularizedLDA.load(args.model)
                sidecar = args.model.with_suffix(".channels")
                if sidecar.exists() and int(sidecar.read_text(encoding="utf-8").strip()) != args.channels:
                    raise ValueError("Saved model channel count does not match --channels")
            validate_model_channels(model, args.channels)

            if args.mode == "train":
                return 0
            weak_classes = (
                []
                if metrics is None
                else [
                    label
                    for label, recall in metrics["per_class_recall"].items()
                    if recall < 0.50
                ]
            )
            if (
                metrics is not None
                and (
                    metrics["balanced_accuracy"] < 0.60
                    or weak_classes
                )
                and not args.force
            ):
                weakness = (
                    " Weak held-out classes: " + ", ".join(weak_classes) + "."
                    if weak_classes
                    else ""
                )
                print(
                    "\nThe quick holdout did not pass the play gate; keys will remain disabled."
                    + weakness
                    + " "
                    "Collect cleaner trials or rerun with --force only for an explicitly experimental rehearsal.",
                    file=sys.stderr,
                )
                return 3
            play(
                reader,
                model,
                sample_rate_hz=args.sample_rate,
                confidence=args.confidence,
                margin_threshold=args.margin,
                energy_sigma=args.energy_sigma,
                manual_energy_threshold=args.energy_threshold,
                no_keypress=args.no_keypress,
            )
    except KeyboardInterrupt:
        print("\nStopped; all directional keys released.")
        return 130
    except (FileNotFoundError, HardwareUnavailable, RuntimeError, ValueError) as exc:
        print(f"\nSOMACH drive error: {exc}\n", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
