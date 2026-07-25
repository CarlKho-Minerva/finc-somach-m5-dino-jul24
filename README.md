# SOMACH M5 Max Dino Controller (July 2026)

## Hardware & System Port Map
- ESP32 Serial Interface: /dev/cu.usbserial-* or /dev/cu.usbmodem* (Baud: 115200)
- Python Backend API & WebSocket: http://localhost:8123
- Vite React UI Dashboard: http://localhost:3000
- Target App: Chrome Dino (https://chromedino.com or chrome://dino)

## Critical Capstone Lessons Applied
1. Grounding: ESP32 stays USB-connected to MacBook chassis to drain 60Hz ambient noise.
2. Serial Reset Mitigation: PySerial requires dsrdtr=False, rtscts=False to stop ESP32 boot-loops.
3. Judge Onboarding: 3-second auto-zeroing adapts instantly to any judge's skin impedance.
