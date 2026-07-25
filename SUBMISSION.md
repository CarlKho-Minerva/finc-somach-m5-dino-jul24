# Night Hack III submission copy

Use this as form-ready copy. Replace every bracketed field before submission.
The demo is hardware-first: no slides and no pitch deck.

## Form fields

**First name(s)**

Carl **[ADD OR REMOVE TEAMMATE FIRST NAMES]**

**Team name**

SOMACH **[CONFIRM]**

**Primary contact email**

**[CONFIRM OR REPLACE: carl@somach.life]**

**One-liner (41/50 characters)**

Subvocalized JUMP makes Chrome Dino jump.

**Short project description**

SOMACH is a local silent-articulation controller for Chrome Dino. An AD8232 on
the chin captures the voluntary muscle impulse of a closed-mouth JUMP gesture;
an ESP32 streams it at 1 kHz to a Mac, which filters and calibrates the signal,
then posts a native macOS Space event. A live dashboard shows the waveform,
personal threshold, lead contact, latency telemetry, and jump count. The signal
never leaves the laptop.

This is a working extension of Carl's prior low-cost sEMG capstone. Tonight's
work converts that research rig into a fast judge-onboarding hardware demo with
exact-pin firmware, a three-second personal zeroing flow, and direct game input.

**What we built during the five-hour hack**

- Adapted the prior AD8232/ESP32 capstone rig into exact-pin, single-channel
  1,000 Hz USB firmware with lead-off, clipping, timing, and drop telemetry.
- Built a causal 60 Hz notch, Butterworth bandpass, streaming RMS envelope, and
  a three-second personal threshold calibration (`mu + 3.5 sigma`).
- Added refractory and hysteretic trigger logic plus native Quartz Space events
  to control Chrome Dino without PyAutoGUI.
- Built a local FastAPI/WebSocket backend with both deterministic mock and live
  hardware modes.
- Built a dark React Canvas oscilloscope with draggable threshold, judge
  onboarding, hardware diagnostics, and a live jump counter.

**60-second demo video**

**[UPLOAD THE FINAL MP4, MAX 60 MB]**

**Demo link**

**[REPLACE WITH PUBLIC URL TO THE 10-SECOND HARDWARE CLIP OR MAIN VIDEO]**

**Live project / website URL**

**[REPLACE WITH PUBLIC PROJECT PAGE; DO NOT SUBMIT LOCALHOST]**

Supporting public project history: https://somach.vercel.app/editorial/jump.html

**Team photo**

**[UPLOAD A PHOTO SHOWING CARL/TEAM WITH THE ESP32 + AD8232 RIG]**

**GitHub repo**

https://github.com/CarlKho-Minerva/finc-somach-m5-dino-jul24

**Sponsor tools used tonight**

**[SELECT ONLY TOOLS ACTUALLY USED FROM THE FORM OPTIONS]**

## Exactly timed Loom script (59 seconds)

Record one continuous take. Layout: Dino on the left, SOMACH dashboard on the
right, and a webcam bubble with the chin/electrodes clearly visible. Do not show
slides.

| Time | Screen action | Spoken line |
| --- | --- | --- |
| `0:00-0:05` | Show the wired chin sensor, dashboard, and Dino together. | "This is SOMACH. A silent JUMP gesture controls Chrome Dino from my chin muscles." |
| `0:05-0:11` | Point briefly to the AD8232 and ESP32; keep live waveform visible. | "This low-cost AD8232 streams one thousand samples per second over USB to this Mac." |
| `0:11-0:17` | Pan over source, sample-rate, electrode, and Quartz status. | "Everything runs locally: signal filtering, calibration, detection, and the native Space key." |
| `0:17-0:23` | Click **Calibrate baseline**. Relax and stay silent for three seconds. | "A new wearer stays relaxed for three seconds. SOMACH learns their baseline and arms itself." |
| `0:23-0:27` | Click Dino last and place hands visibly away from the keyboard. | "My hands are off. Watch my chin, the waveform, and the dinosaur." |
| `0:27-0:41` | Silently articulate JUMP three separate times; pause long enough to show three distinct jumps. | Before the first gesture: "Three silent JUMPs." Then say nothing while demonstrating. |
| `0:41-0:49` | Show the increased jump counter and threshold crossings without stealing focus until the run is safe. | "Each muscle burst crossed my personal RMS threshold and posted one refractory-protected Space event." |
| `0:49-0:56` | Hold on the functioning game plus rig. | "Tonight we turned prior sEMG research into a working, judge-calibrated local game controller." |
| `0:56-0:59` | End on the chin electrodes and live game. | "It reads deliberate silent articulation, not thoughts." |

Rehearse the three gestures before recording. If any trigger misses, recalibrate
or move the threshold; do not explain a failed run inside the final 59 seconds.

## Ten-second hardware clip

Use this for the hardware-specific demo link if the form requests a separate
short clip.

| Time | Shot |
| --- | --- |
| `0:00-0:02` | Close-up of intact chin electrodes, AD8232, and ESP32 USB connection. |
| `0:02-0:04` | Dashboard showing hardware source, about 1,000 Hz, contact good, calibrated, and armed. |
| `0:04-0:09` | Hands off keyboard; silently articulate JUMP twice while Dino visibly jumps twice. |
| `0:09-0:10` | Hold on the dashboard jump counter at `02`. |

No music, cuts, title cards, or architecture slides are needed. The causal proof
is the synchronized chin movement, waveform burst, event count, and game jump.

## Judge-criteria proof points

| Criterion | What the demo proves |
| --- | --- |
| Progress tonight | Prior research hardware became exact-pin 1 kHz firmware, a local real-time pipeline, a judge-calibration UX, and a direct Dino controller. |
| Technical difficulty and execution | The system crosses analog biosensing, deterministic embedded sampling, causal DSP, asynchronous WebSockets, macOS permissions, and native input injection. |
| Core demo quality | Three-second onboarding, lead-off checks, a visible threshold, refractory protection, and a mock fallback make the central JUMP loop inspectable. |
| Originality and potential | It turns closed-mouth speech motor activity into a general local control primitive using roughly $40 consumer hardware. |

## Prior foundation: disclose, do not blur

Before this hack, Carl's capstone had already established the research path:

- Phone IMU control worked but was ergonomically poor.
- Pixel Watch IMU reached 94% binary performance but plateaued around 57% for
  multiple gestures; manual button labels replaced variable-latency voice labels.
- A single-channel forearm AD8232 study benchmarked 18 model configurations;
  the Random Forest reached 74.25% accuracy with 0.01 ms inference.
- The later roughly $40 two-channel silent-speech studies reported 48.9% +/-
  3.1% and 51.8% +/- 2.8% five-fold cross-validation on six classes, above the
  16.7% chance baseline; confidence gating reached 64.1% in one study.

Tonight's claim is narrower and more demoable: adapt those lessons into a new
single-channel, person-calibrated **JUMP event detector** that controls a game in
real time. Do not say the older multi-class studies or datasets were produced
during the five-hour hack.

## Honesty guardrails for live judging

- Say **silent articulation**, **subvocalized gesture**, or **covert articulatory
  production**. Do not call it mind reading or claim it decodes arbitrary inner
  speech.
- It detects one intentionally exaggerated command using a calibrated energy
  threshold. It is not tonight's trained speech classifier.
- Do not promise guaranteed sub-80 ms human-to-jump latency. The Quartz call and
  CPU processing may measure much faster, but the detector uses a trailing
  150 ms RMS window with a 20 ms stride.
- The common AD8232 ECG breakout may have an approximately 40 Hz analog low-pass
  filter. Do not claim a measured 250 Hz biological passband unless this board's
  hardware modification or frequency response is verified.
- Keep the Mac on internal battery with chargers and mains-powered peripherals
  disconnected while electrodes are on a person.
- If selected for the Top 10, bring up the same live hardware/software view.
  No slides, no deck, and no opening pitch: calibrate, focus Dino, demonstrate.

## Pre-submit checklist

- Replace every bracketed placeholder.
- Keep the main video at 59 seconds or shorter and under 60 MB.
- Test every public link in an incognito window without authentication.
- Do not use `localhost` as the submitted URL; hardware proof should be a public
  video link.
- Test the exact hardware, USB cable, battery-only setup, Accessibility grant,
  Chrome focus, calibration, and threshold immediately before recording.
- Keep the local mock mode ready as a diagnostic fallback, but lead with the
  physical chin-to-Dino loop.
