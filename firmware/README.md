# SOMACH ESP32 + AD8232 firmware

This firmware samples the already-wired AD8232 at 1,000 Hz and streams it to
the Mac over USB UART at 115200 baud. It intentionally contains no Wi-Fi code.

## Exact wiring

Power everything down before changing wires.

| AD8232 pin | ESP32 connection | Purpose |
| --- | --- | --- |
| `OUTPUT` | GPIO 36 (`ADC1_CH0`, `VP`) | 12-bit analog signal |
| `3.3V` | `3V3` | Sensor power; **never use 5 V** |
| `GND` | `GND` | Common low-voltage reference |
| `SDN` | GPIO 27 | Driven HIGH by firmware to enable the AD8232 |
| `LO-` | GPIO 35 | Negative-electrode lead-off status |
| `LO+` | GPIO 32 | Positive-electrode lead-off status |

Electrode cable colors are not standardized. Follow the sensor board's
`RA`/`LA`/`RL` markings and its cable documentation rather than guessing from
wire color. `RA` and `LA` are the differential inputs; `RL` is the driven
reference. The polarity of `RA` versus `LA` does not affect an RMS detector,
but confusing either one with `RL` does.

### Human-subject safety

This prototype is not a medical device. Get the wearer's consent, use intact
commercial electrodes on healthy skin, and stop immediately if there is any
discomfort or skin reaction. Do not cut the conductive gel, metal snap, or
electrode center.

When electrodes are attached to a person, power the ESP32 only from the
MacBook running on its internal battery. Disconnect the MacBook charger and
other mains-powered or earth-grounded peripherals first. USB is the data and
power connection here; it must not be treated as protective medical isolation
or as a way to "ground" a person. Do not attach this prototype to anyone if
any cable, board, electrode, or insulation is damaged or wet.

## Build and flash with PlatformIO

Install PlatformIO Core if it is not already installed:

```bash
python3 -m pip install --user platformio
```

Then, from this `firmware` directory:

```bash
pio device list
pio run
pio run --target upload --upload-port /dev/cu.usbserial-XXXXXXXX
```

Replace the example port with the ESP32 port shown by `pio device list`. A
CP210x board may instead appear as `/dev/cu.SLAB_USBtoUART`; a CH340 board may
appear as `/dev/cu.wchusbserial-*`.

To inspect the stream without asserting DTR/RTS:

```bash
pio device monitor --port /dev/cu.usbserial-XXXXXXXX --baud 115200
```

`platformio.ini` sets monitor DTR and RTS low. The production backend should
also open pySerial with flow control disabled, wait two seconds, and clear the
input buffer before parsing. The ESP32 ROM may print a boot banner during a
physical reset; the sketch itself emits no untagged diagnostic text.

The default PlatformIO board is `esp32dev`. If the board is a different classic
ESP32 development-board variant, change only the `board` value after checking
PlatformIO's board ID; do not change the six signal connections above.

## Serial protocol

Every acquired ADC sample is a decimal integer on its own line:

```text
1874
1881
1869
```

At 1,000 Hz, four digits plus newline consume about 50 kbit/s with UART 8-N-1,
well below 115.2 kbit/s. A 512-sample queue absorbs short host/USB stalls. If
the host stops draining serial long enough to fill it, sampling stays on its
deadline and `tx_drop_total` records output samples that could not be queued.

Once per second, one machine-readable line begins with `#META,`:

```text
#META,rate_hz=1000.00,samples=1000,sample_total=10000,missed_total=0,overrun_total=0,max_late_us=18,tx_drop_total=0,lo_plus=0,lo_minus=0,leads_off=0,clip_low=0,clip_high=0,queued=1
```

- `rate_hz` is the conversions actually acquired during the reporting interval.
- `missed_total` counts scheduled 1 ms slots skipped because execution was late.
- `overrun_total` counts occasions on which one or more slots were skipped.
- `max_late_us` is the worst deadline lateness in the latest interval.
- `tx_drop_total` is cumulative queue overflow, separate from ADC deadline loss.
- `lo_plus`, `lo_minus`, and `leads_off` are active-high AD8232 lead status.
- `clip_low` and `clip_high` count latest-interval readings within four ADC
  counts of either rail.

The host parser should accept `^[0-9]+$` as a sample, parse `#META,` separately,
and ignore anything else during initial connection/reset.

## Sampling behavior

GPIO 36 uses the classic ESP32's ADC1, 12-bit resolution, and `ADC_11db`
attenuation. The loop advances an absolute `micros()` deadline by exactly
1,000 microseconds per scheduled sample. Signed deadline comparisons remain
correct across the roughly 71-minute `micros()` rollover. If execution is more
than one period late, it takes one current sample and accounts for skipped
slots instead of emitting a misleading catch-up burst.
