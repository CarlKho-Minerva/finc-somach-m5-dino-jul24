# Dual AD8232 drive firmware

This optional NodeMCU-32S image streams two synchronized ADC1 readings as
`a,b\n` at 1,000 paired frames per second and 460800 baud. It does not alter the
proven single-channel build.

## Exact wiring

Unplug USB power while moving wires. Both modules may share the ESP32's **3V3**
and **GND** power rails.

| AD8232 pin | Sensor A | Sensor B |
| --- | --- | --- |
| `3.3V` | ESP32 `3V3` | ESP32 `3V3` |
| `GND` | ESP32 `GND` | ESP32 `GND` |
| `OUTPUT` | GPIO36 / `VP` | GPIO39 / `VN` |
| `SDN` | GPIO27 | GPIO26 |
| `LO+` | GPIO32 | GPIO33 |
| `LO-` | GPIO35 | GPIO34 |

Each AD8232 must retain its own complete three-electrode cable and reference
electrode. **Never connect the two `RL` electrode leads to each other, to the
ESP32 ground, or to a breadboard rail.** Do not place electrodes across the
chest. Sensor A and B should observe two nearby but distinct speech-muscle
regions; record placement photos because even a 1 cm change can alter the data.

This is a prototype, not a medical device. Only wear electrodes while the Mac
is running on battery with its charger, powered dock/display, and mains-powered
test equipment disconnected. Use intact skin and stop immediately for pain,
tingling, heat, or irritation.
Do not modify or bypass the module's electrode-input or RLD current-limiting
resistors. Do not use a module whose protective component path is unknown or
damaged; the AD8232 datasheet requires RLD fault current to remain below 10 µA.

## Build, flash, and inspect

From the repository root:

```bash
cd firmware
pio run -e dual_ad8232
pio run -e dual_ad8232 -t upload
pio device monitor -b 460800 --dtr 0 --rts 0
```

Expected sample lines look like `2041,1978`. Once per second a `#META` line
reports both modules' lead-off states, clipping, actual paired rate, missed
deadlines, acquisition time, queue depth, and dropped frames.

To restore/flash the unchanged single-channel firmware:

```bash
pio run -e esp32dev -t upload
pio device monitor -b 115200 --dtr 0 --rts 0
```
