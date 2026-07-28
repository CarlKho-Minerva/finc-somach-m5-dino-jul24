# Two-channel threshold drive

This mode controls [Bruno Simon's driving game](https://bruno-simon.com) with
two deliberate muscle contractions. It is separate from the proven
single-channel Flappy demo and does not train a classifier.

| Input | Placement and gesture | Output |
| --- | --- | --- |
| Sensor A only | Mylohyoid/submental; the same closed-mouth `JUMP` contraction used for Flappy | `UP` held for 1,000 ms |
| Sensor B only | Left masseter; one brief, gentle left-side jaw contraction | `LEFT` held for 200 ms |
| Sensor A+B | Hold both contractions so their RMS traces are above threshold at the same time | `RIGHT` held for 200 ms |

There is intentionally no reverse command. A+B must truly overlap; two
sequential gestures do not count as right. The detector waits up to 80 ms for
the second channel, then commits the single-channel command. After a command,
relax both muscles so the RMS envelope can fall below its release lines for
180 ms.

## Current demo: one live channel plus disclosed mock steering

If the second AD8232 is unavailable or broken, use the hybrid path instead of
pretending the second channel is live:

```bash
make drive-hybrid
```

Hybrid mode still reads the dual ESP32 stream, but it discards physical Channel
B before filtering, calibration, health checks, and arbitration. The available
demo actions are deliberately limited:

| Input | Output | Claim boundary |
| --- | --- | --- |
| Live Sensor A / mylohyoid contraction | `UP` for 1,000 ms | Real submental sEMG control |
| Dashboard **MOCK B / LEFT IN 2S** button | `LEFT` for 200 ms | Simulated software-path test |
| Right | Disabled | Not demonstrated without a second live sensor |

The dashboard permanently labels B as simulated and opens/focuses the car before
the two-second mock-left countdown. Keep A relaxed during that countdown. Never
describe the mock-left action as decoded muscle activity.

## Safety before touching wiring

This prototype is not a medical device and USB is not medical isolation.

1. Remove all electrodes from the body and unplug USB before moving wires or
   flashing firmware.
2. Power both AD8232 modules from ESP32 `3V3`, never `5V`.
3. While wearing electrodes, run the MacBook on its internal battery. Disconnect
   the charger, powered display/dock, oscilloscope, and other mains-connected
   equipment.
4. Each AD8232 retains its own complete `RA`/`LA`/`RL` cable. Never connect an
   `RL` body lead to ESP32 ground or a breadboard rail.
5. Use intact commercial pads on healthy skin. Stop for pain, tingling, warmth,
   or irritation. Do not place a differential pair across the chest.

## Exact NodeMCU-32S wiring

Keep the working Flappy module as Sensor A and add Sensor B:

| AD8232 pin | Sensor A — mylohyoid | Sensor B — left masseter |
| --- | --- | --- |
| `3.3V` | ESP32 `3V3` | ESP32 `3V3` |
| `GND` | ESP32 `GND` | ESP32 `GND` |
| `OUTPUT` / `SIG` | GPIO36 / board label `VP` | GPIO39 / board label `VN` |
| `SDN` | GPIO27 | GPIO26 |
| `LO+` | GPIO32 | GPIO33 |
| `LO-` | GPIO35 | GPIO34 |

GPIO36 and GPIO39 are the two analog inputs. `LO+` and `LO-` are digital contact
indicators, not signal channels. The firmware drives both `SDN` pins high. The
modules can share only their low-voltage `3V3` and `GND` power rails.

Place A exactly where the one-channel Flappy signal worked. Place B's
differential pair over the left masseter without overlapping A's pads. Follow
the cable's verified `RA`, `LA`, and `RL` labels because lead colors vary.

## Flash once for two channels

Skip this if the board already emits two values such as `2041,1978`. Otherwise,
with every electrode off the body:

```bash
pio device list
./scripts/flash-drive.sh /dev/cu.usbserial-XXXXXXXX
```

The dual firmware emits synchronized `A,B` samples at 1,000 paired frames/s and
460800 baud. Close PlatformIO/Arduino Serial Monitor afterward; only one process
can own the port.

## Start and play

From the repository root:

```bash
make drive
```

If auto-detection ever picks the wrong device, use the explicit launcher:

```bash
./scripts/start-drive-dashboard.sh --hardware \
  --serial-port /dev/cu.usbserial-XXXXXXXX \
  --prompt-accessibility
```

The command starts the backend at `http://127.0.0.1:8124` and opens
`http://127.0.0.1:3000/drive`.

1. Keep the jaw, tongue, and head relaxed; click **CALIBRATE 3S**.
2. Wait until both traces settle and **ARM OUTPUT** becomes available.
3. Arm, launch the car, and click the game canvas so it owns keyboard focus.
4. Hold A alone for forward, B alone for left, or both together for right.
5. Relax completely between commands. Press `Control-C` in the launcher terminal
   to stop both services and release any held key.

For hybrid mode, calibrate only while the live submental site is relaxed. The
synthetic B baseline calibrates automatically. Arm output, focus the car, and
use the same closed-mouth `JUMP` contraction as Flappy for real forward. To
show steering, click the clearly labeled mock-left button; it focuses the game
and posts the simulated left input after two seconds. Right remains unavailable.

macOS may require the launching Terminal under **System Settings → Privacy &
Security → Accessibility**. After granting access, restart that Terminal and
run `make drive` again.

## Tune the two thresholds

Each graph has its own draggable threshold and `-5`, `+5`, and numeric controls.
Changing either threshold disarms output; this is intentional. Re-arm and
refocus the game after tuning.

- False A/forward: raise `TA`.
- Missed A/forward: lower `TA` slightly.
- False B/left: raise `TB`.
- Missed B/left: lower `TB` slightly.
- Intended right becomes forward or left: the weaker channel did not cross;
  lower only that threshold or hold both contractions together a little longer.
- A-only or B-only becomes right: the other sensor is seeing cross-talk; raise
  that other threshold.

RMS is the recent signal power over a 150 ms window, so it stays elevated
briefly after the physical contraction ends. Normal swallowing can still raise
the mylohyoid channel; threshold mode cannot identify *why* a muscle activated.
Keep output disarmed while talking or setting up, then arm only for the control
demonstration.

## Mock check and switching back to Flappy

Run the complete dashboard with synthetic signals and no key output:

```bash
make drive-mock
```

The old learned five-class experiment remains available only as
`make drive-classifier`; it is not the user-facing drive path.

Flappy remains strictly one channel. To return after flashing the dual image,
stop the drive launcher, remove electrodes, flash the single-channel firmware,
then restart Flappy:

```bash
pio run --project-dir firmware -e esp32dev -t upload \
  --upload-port /dev/cu.usbserial-XXXXXXXX
make hardware
```
