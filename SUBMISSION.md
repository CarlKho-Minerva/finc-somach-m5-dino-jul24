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

**One-liner (40/50 characters)**

Subvocalized JUMP controls Flappy Bird.

**Short project description**

SOMACH is a local silent-articulation controller for Flappy Bird. An AD8232 on
the chin captures the voluntary muscle impulse of a rehearsed closed-mouth JUMP
gesture; an ESP32 streams it at 1 kHz to a Mac, which filters and calibrates the
signal, then posts a native macOS Space event. A live dashboard shows the
waveform, personal threshold, lead contact, event telemetry, and jump count.
The signal never leaves the laptop.

This is a working extension of Carl's prior low-cost sEMG capstone. Tonight's
work converts that research rig into a fast judge-onboarding hardware demo with
exact-pin firmware, a three-second personal zeroing flow, and direct game input.

**What we built during the five-hour hack**

- Adapted the prior AD8232/ESP32 capstone rig into exact-pin, single-channel
  1,000 Hz USB firmware with lead-off, clipping, timing, and drop telemetry.
- Built a causal 60 Hz notch, Butterworth bandpass, streaming RMS envelope, and
  a three-second personal threshold calibration (`mu + 3.5 sigma`).
- Added refractory and hysteretic trigger logic plus native Quartz Space events
  to control Flappy Bird without PyAutoGUI.
- Built a local FastAPI/WebSocket backend with both deterministic mock and live
  hardware modes.
- Built a dark React Canvas oscilloscope plus an atomic local recorder, guided
  JUMP/artifact labels, held-out validation, and a safe RMS fallback when the
  one-session learned model did not pass its deployment bar.

## Verified tonight

- Recovered the correct old hardware route: AD8232 output on GPIO36 and shutdown
  enable on GPIO27; the attached-electrode stream then held `1,000 Hz` with
  `0%` live clipping.
- Captured `358,700` clean real samples (`358.7 seconds`) across three local
  sessions, including 8 cued JUMPs and 12 artifact labels.
- Measured the rest baseline at RMS `mu = 11.32`, `sigma = 8.73`, giving an
  automatic threshold of `41.88`; ordinary speech/swallow activity required a
  more conservative filming threshold of `65`.
- Tested the learned path honestly. Its held-out balanced accuracy was `60%`
  with `50%` JUMP recall, so MODEL stays off rather than being presented as a
  working decoder.
- Completed a controlled hardware rehearsal in which exactly three deliberate
  silent gestures produced exactly three detector events and three native Space
  posts. Narration or swallowing while armed can still false-trigger, so the
  final demo gates detection to the silent command window.

**60-second demo video**

Explainer video: https://www.loom.com/share/8c0837809c0b4ac1a595ac519f4e1025

**Demo link**

Live hardware demo: https://x.com/Carl_NotANerd/status/2080905802735661263

**Live project / website URL**

https://frontend-three-amber-34.vercel.app

Supporting public project history: https://somach.vercel.app/editorial/jump.html

**Team photo**

**[UPLOAD A PHOTO SHOWING CARL/TEAM WITH THE ESP32 + AD8232 RIG]**

Take a real landscape phone photo: Carl in frame, intact chin electrodes clearly
visible, ESP32 and AD8232 visible on the desk, and the SOMACH waveform or
Flappy Bird in the background. Brace the loose USB cable first, remove the Mac charger and
other mains-connected peripherals, and hide unrelated/private screen content.
The repository dashboard screenshot documents software state but does **not**
replace this required team photo. Do not use an AI-generated hardware photo.

**GitHub repo**

https://github.com/CarlKho-Minerva/finc-somach-m5-dino-jul24

**Sponsor tools used tonight**

**[SELECT ONLY TOOLS ACTUALLY USED FROM THE FORM OPTIONS]**

## Exactly timed Loom script (59 seconds)

Before recording, calibrate at rest, set the tested RMS threshold to `65`, select
**RMS**, reset the counter to `00`, and leave **Detection paused**. Record one
continuous take with Flappy Bird left, SOMACH right, and a webcam bubble clearly showing
the chin/electrodes. Do not show slides.

| Time | Screen action | Spoken line |
| --- | --- | --- |
| `0:00-0:06` | With detection **paused**, show the wired chin sensor, dashboard, and Flappy Bird. | "This is SOMACH. A deliberate silent JUMP gesture controls Flappy Bird from my chin muscles." |
| `0:06-0:12` | Point to the AD8232/ESP32 and the live `1,000 Hz` status. | "The low-cost sensor streams one thousand samples per second over USB, entirely locally." |
| `0:12-0:18` | Point to raw, filtered, RMS, and threshold traces. | "RMS turns the oscillating muscle signal into a stable burst-strength number for detection." |
| `0:18-0:23` | Show calibrated/contact/Quartz status; remain paused. Swallow now if needed. | "I zeroed my resting baseline. Next are exactly three silent commands, with my hands off." |
| `0:23-0:27` | In silence, arm detection, click Flappy Bird last, and move both hands visibly away. | _Say nothing._ |
| `0:27-0:42` | Stay silent. Perform three strong rehearsed tongue-up/back JUMP gestures about one second apart. | _Say nothing._ |
| `0:42-0:46` | Still silent, click the dashboard and switch detection back to **paused**. | _Say nothing._ |
| `0:46-0:54` | Show counter `03`, live rig, and Flappy Bird. | "Three gestures made three native Space events. The 250 millisecond lockout prevents double jumps." |
| `0:54-0:59` | End on the functioning rig and local-processing footer. | "It detects a voluntary speech-motor gesture, not thoughts." |

Speaking, swallowing, coughing, and jaw motion overlap this single-channel energy
signal. Never narrate while armed. If a rehearsal misses or adds a trigger,
re-zero the counter and retake instead of claiming unrestricted accuracy.

## Ten-second hardware clip

Use this for the hardware-specific demo link if the form requests a separate
short clip.

| Time | Shot |
| --- | --- |
| `0:00-0:02` | Silent close-up of intact chin electrodes, AD8232, and ESP32 USB connection; detection is paused. |
| `0:02-0:03` | In silence, arm detection and click Flappy Bird last. |
| `0:03-0:08` | Hands off keyboard; perform two rehearsed silent JUMP gestures while Flappy Bird visibly flaps twice. |
| `0:08-0:09` | Return to the dashboard and pause detection, still without speaking. |
| `0:09-0:10` | Hold on the dashboard jump counter at `02`. |

No narration, music, cuts, title cards, or architecture slides are needed. The
causal proof is the synchronized chin movement, waveform burst, event count,
and game jump.

## Judge-criteria proof points

| Criterion | What the demo proves |
| --- | --- |
| Progress tonight | Prior research hardware became exact-pin 1 kHz firmware, a local real-time pipeline, a judge-calibration UX, and a direct Flappy Bird controller. |
| Technical difficulty and execution | The system crosses analog biosensing, deterministic embedded sampling, causal DSP, asynchronous WebSockets, macOS permissions, and native input injection. |
| Core demo quality | Stable real 1 kHz input, 0% live clipping, visible contact/threshold state, and an exact 3-for-3 controlled rehearsal make the central JUMP loop inspectable. |
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
- Swallowing and ordinary speech produced overlapping RMS amplitudes. Do not
  claim zero false positives during unrestricted behavior; keep detection
  paused for narration and arm only for the silent command window.
- The one-session learned model reached `60%` held-out balanced accuracy and
  `50%` JUMP recall, failed the deployment bar, and remains inactive. Presenting
  that result is evidence of validation discipline, not a model-performance win.
- Do not promise guaranteed sub-80 ms human-to-jump latency. The Quartz call and
  CPU processing may measure much faster, but the detector uses a trailing
  150 ms RMS window with a 20 ms stride.
- The common AD8232 ECG breakout may have an approximately 40 Hz analog low-pass
  filter. Do not claim a measured 250 Hz biological passband unless this board's
  hardware modification or frequency response is verified.
- Keep the Mac on internal battery with chargers and mains-powered peripherals
  disconnected while electrodes are on a person.
- If selected for the Top 10, bring up the same live hardware/software view.
  No slides, no deck, and no opening pitch: calibrate, focus Flappy Bird, demonstrate.

## Pre-submit checklist

- Replace every bracketed placeholder.
- Keep the main video at 59 seconds or shorter and under 60 MB.
- Test every public link in an incognito window without authentication.
- Do not use `localhost` as the submitted URL; hardware proof should be a public
  video link.
- Test the exact hardware, USB cable, battery-only setup, Accessibility grant,
  Chrome/Flappy Bird focus, calibration, and threshold immediately before recording.
- Brace the loose USB cable. Select **RMS**, set the tested threshold to `65`,
  reset the counter to `00`, and keep detection paused until narration ends.
- After the silent commands, pause detection again before speaking or swallowing.
- Keep the local mock mode ready as a diagnostic fallback, but lead with the
  physical chin-to-Flappy loop.
