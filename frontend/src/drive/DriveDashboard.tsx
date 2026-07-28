import { useEffect, useRef, useState, type FormEvent } from "react";
import { DriveTrace } from "./DriveTrace";
import { useDrive } from "./useDrive";
import type { DriveAction, DriveChannelId } from "./types";
import "./drive.css";

const channelCopy = {
  a: {
    name: "MYLOHYOID",
    muscle: "JUMP contraction",
    action: "FORWARD",
    duration: "1.0s",
    arrow: "↑",
  },
  b: {
    name: "LEFT MASSETER",
    muscle: "gentle left bite",
    action: "LEFT",
    duration: "0.2s",
    arrow: "←",
  },
} as const;

const hybridChannelB = {
  name: "LEFT INPUT / SIMULATED",
  muscle: "disclosed synthetic test pulse",
  action: "LEFT (MOCK)",
  duration: "0.2s",
  arrow: "←",
} as const;

function reading(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function ThresholdControl({
  channel,
  value,
  connected,
  onCommit,
}: {
  channel: DriveChannelId;
  value: number;
  connected: boolean;
  onCommit: (channel: DriveChannelId, value: number) => Promise<void>;
}) {
  const [draft, setDraft] = useState(reading(value));
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing) setDraft(reading(value));
  }, [editing, value]);

  const commit = async (next: number) => {
    if (!Number.isFinite(next)) return;
    const rounded = Math.min(4_095, Math.max(0.1, Math.round(next * 10) / 10));
    setEditing(false);
    setDraft(reading(rounded));
    await onCommit(channel, rounded);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void commit(Number(draft));
  };

  return (
    <form className="drive-threshold" onSubmit={submit}>
      <label>
        <span>T{channel.toUpperCase()}</span>
        <input
          aria-label={`Channel ${channel.toUpperCase()} threshold`}
          type="number"
          min="0.1"
          max="4095"
          step="0.5"
          value={draft}
          disabled={!connected}
          onFocus={() => setEditing(true)}
          onBlur={() => setEditing(false)}
          onChange={(event) => {
            setEditing(true);
            setDraft(event.target.value);
          }}
        />
      </label>
      <button type="button" disabled={!connected} onClick={() => void commit(Math.max(0.1, value - 5))}>
        −5
      </button>
      <button type="button" disabled={!connected} onClick={() => void commit(Math.min(4_095, value + 5))}>
        +5
      </button>
      <button type="submit" disabled={!connected || draft.trim() === ""}>
        SET
      </button>
    </form>
  );
}

function ActionGlyph({ action }: { action: DriveAction }) {
  const glyph = action === "forward" ? "↑" : action === "left" ? "←" : action === "right" ? "→" : "·";
  return (
    <div className={`drive-action-glyph action-${action}`} aria-live="polite">
      <span>{glyph}</span>
      <strong>{action === "idle" ? "READY" : action.toUpperCase()}</strong>
    </div>
  );
}

export default function DriveDashboard() {
  const {
    telemetry,
    connection,
    signals,
    wsUrl,
    lastEvent,
    calibrate,
    setArmed,
    setThreshold,
    previewThreshold,
    resetCounts,
    triggerMockLeft,
  } = useDrive();
  const [mockLeftCountdown, setMockLeftCountdown] = useState<number | null>(null);
  const mockLeftTimerRef = useRef<number | null>(null);
  const gameWindowRef = useRef<Window | null>(null);
  const socketConnected = connection.status === "connected";
  const connected = socketConnected && telemetry.signalConnected;
  const isHybrid = telemetry.source.toLowerCase().includes("hybrid");
  const unsafe =
    Boolean(telemetry.qualityError || telemetry.backendError) ||
    (isHybrid
      ? telemetry.channels.a.leadOff || telemetry.channels.a.clipping
      : Object.values(telemetry.channels).some((channel) => channel.leadOff || channel.clipping));
  const calibrationPercent = Math.round(telemetry.calibration.progress * 100);
  const source = telemetry.source === "waiting" ? "DUAL ESP32" : telemetry.source.toUpperCase();
  const state = telemetry.waitingForRelease
    ? "WAITING FOR RELEASE"
    : telemetry.arbitrationState.replaceAll("_", " ").toUpperCase();
  const displayedAction = isHybrid && telemetry.action === "right" ? "idle" : telemetry.action;

  useEffect(() => {
    return () => {
      if (mockLeftTimerRef.current !== null) window.clearInterval(mockLeftTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (isHybrid && telemetry.armed) return;
    if (mockLeftTimerRef.current !== null) {
      window.clearInterval(mockLeftTimerRef.current);
      mockLeftTimerRef.current = null;
      setMockLeftCountdown(null);
    }
  }, [isHybrid, telemetry.armed]);

  const openGame = () => {
    const current = gameWindowRef.current;
    if (current && !current.closed) {
      current.focus();
      return;
    }
    const game = window.open(
      "https://bruno-simon.com/",
      "somach-drive-game",
      "popup=yes,width=1180,height=820,resizable=yes,scrollbars=yes",
    );
    gameWindowRef.current = game;
    game?.focus();
  };

  const beginMockLeft = () => {
    if (!isHybrid || !connected || !telemetry.armed || mockLeftTimerRef.current !== null) return;
    openGame();
    let remaining = 2;
    setMockLeftCountdown(remaining);
    mockLeftTimerRef.current = window.setInterval(() => {
      remaining -= 1;
      if (remaining > 0) {
        setMockLeftCountdown(remaining);
        return;
      }
      if (mockLeftTimerRef.current !== null) window.clearInterval(mockLeftTimerRef.current);
      mockLeftTimerRef.current = null;
      setMockLeftCountdown(null);
      void triggerMockLeft();
    }, 1_000);
  };

  return (
    <div className="drive-dashboard">
      <header className="drive-header">
        <div className="drive-wordmark">
          <span>SOMACH</span>
          <strong>{isHybrid ? "HYBRID DRIVE INSTRUMENT" : "DUAL-CHANNEL DRIVE INSTRUMENT"}</strong>
          <small>{isHybrid ? "[ A: LIVE HARDWARE / B: SIMULATED ]" : "[ THRESHOLD ARBITRATION / NO CLASSIFIER ]"}</small>
        </div>
        <nav aria-label="Dashboard modes">
          <span title="Stop this launcher, then run make hardware for one-channel Flappy">FLAPPY / 1CH · SEPARATE</span>
          <span className="is-current">{isHybrid ? "DRIVE / HYBRID" : "DRIVE / 2CH"}</span>
          <button type="button" onClick={openGame}>OPEN CAR ↗</button>
        </nav>
      </header>

      <section className="drive-status-strip" aria-label="Acquisition status">
        <span className={connected ? "is-live" : ""}>
          <i /> [ STATUS: {connected ? "LIVE" : connection.status.toUpperCase()} ]
        </span>
        <span>[ SRC: {source} ]</span>
        <span>[ RATE: {telemetry.sampleRate ? `${Math.round(telemetry.sampleRate)}HZ` : "---"} ]</span>
        <span>{isHybrid ? "[ B: SIMULATED / RIGHT: OFF ]" : `[ COINCIDENCE: ${telemetry.coincidenceMs}MS ]`}</span>
        <span title={telemetry.device}>[ DEVICE: {telemetry.device} ]</span>
      </section>

      {isHybrid && (
        <section className="drive-hybrid-disclosure" role="status" aria-label="Hybrid demo disclosure">
          <strong>[ HYBRID DEMO — CH.A LIVE HARDWARE / CH.B SIMULATED ]</strong>
          <span>Second AD8232 is broken. FORWARD is real submental sEMG; LEFT is an explicitly triggered software test pulse. RIGHT is unavailable.</span>
        </section>
      )}

      {!connected && (
        <div className="drive-connection-warning" role="status">
          <strong>{socketConnected ? (isHybrid ? "LIVE SENSOR A OFFLINE" : "DUAL SENSOR OFFLINE") : "BACKEND NOT LIVE"}</strong>
          <span>
            {socketConnected
              ? telemetry.backendError || "Waiting for the paired A,B serial signal. Check the ESP32 and close other serial monitors."
              : `Start the dual-drive command, then leave this tab open. Listening at ${wsUrl}`}
          </span>
        </div>
      )}

      {unsafe && (
        <div className="drive-connection-warning is-danger" role="alert">
          <strong>SIGNAL INTERLOCK</strong>
          <span>{isHybrid ? "Live channel A has a lead-off or clipping fault. Reseat the submental sensor, then recalibrate." : "Lead-off or clipping detected. Disarm, reseat the indicated channel, then recalibrate."}</span>
        </div>
      )}

      <main className="drive-grid">
        <section className="drive-scope-bank">
          <div className="drive-bank-heading">
            <div>
              <span>{isHybrid ? "[ A: LIVE RMS / B: SIMULATED RMS ]" : "[ DUAL RMS / 150MS WINDOW ]"}</span>
              <h1>{isHybrid ? "Live + simulated gates" : "Live muscle gates"}</h1>
            </div>
            <div className={`drive-arm-state${telemetry.armed ? " is-armed" : ""}`}>
              <i /> {telemetry.armed ? "KEY OUTPUT ARMED" : "KEY OUTPUT PAUSED"}
            </div>
          </div>

          {(["a", "b"] as const).map((channel) => {
            const values = telemetry.channels[channel];
            const simulated = isHybrid && channel === "b";
            const copy = simulated ? hybridChannelB : channelCopy[channel];
            const ratio = Math.min(1, values.rms / Math.max(1, values.threshold));
            return (
              <article className={`drive-channel channel-${channel}${simulated ? " is-simulated" : ""}`} key={channel}>
                <div className="drive-channel-readout">
                  <div className="drive-channel-id">{simulated ? "SIM.B" : `CH.${channel.toUpperCase()}`}</div>
                  <div>
                    <span>[ {copy.name} ]</span>
                    <strong>{reading(values.rms)}</strong>
                    <small>{simulated ? "MOCK RMS" : "LIVE RMS"}</small>
                  </div>
                  <div className="drive-vu" aria-label={`${copy.name} signal level`} role="meter">
                    {Array.from({ length: 14 }, (_, index) => (
                      <i key={index} className={index < Math.round(ratio * 14) ? "is-on" : ""} />
                    ))}
                  </div>
                  <div className="drive-contact">
                    {simulated ? (
                      <>
                        <span>SOURCE MOCK</span>
                        <span>SENSOR B BYPASSED</span>
                      </>
                    ) : (
                      <>
                        <span className={values.leadOff ? "is-bad" : ""}>
                          LEAD {values.leadOff ? "OFF" : "OK"}
                        </span>
                        <span className={values.clipping ? "is-bad" : ""}>
                          ADC {values.clipping ? "CLIP" : "OK"}
                        </span>
                      </>
                    )}
                  </div>
                </div>
                <div className="drive-trace-wrap">
                  <DriveTrace
                    channel={channel}
                    signals={signals}
                    rms={values.rms}
                    threshold={values.threshold}
                    leadOff={values.leadOff}
                    clipping={values.clipping}
                    connected={connected}
                    simulated={simulated}
                    editable={!simulated}
                    onPreview={(value) => previewThreshold(channel, value)}
                    onCommit={(value) => void setThreshold(channel, value)}
                  />
                </div>
                <div className="drive-channel-footer">
                  <span>[ MAP: {copy.action} {copy.arrow} / {copy.duration} ]</span>
                  {simulated ? (
                    <span>[ SOFTWARE THRESHOLD / READ ONLY ]</span>
                  ) : (
                    <>
                      <span>DRAG DASHED LINE OR</span>
                      <ThresholdControl
                        channel={channel}
                        value={values.threshold}
                        connected={connected}
                        onCommit={setThreshold}
                      />
                    </>
                  )}
                </div>
              </article>
            );
          })}
        </section>

        <aside className="drive-control-rail">
          <section className="drive-module drive-live-module">
            <div className="drive-module-label">[ 00 // LIVE ARBITER ]</div>
            <ActionGlyph action={displayedAction} />
            <div className="drive-arbitration-state">
              <span>GATE STATE</span>
              <strong>{state || "IDLE"}</strong>
            </div>
            <div className={`drive-counts${isHybrid ? " is-hybrid" : ""}`}>
              <div><span>FORWARD</span><strong>{telemetry.counts.forward.toString().padStart(2, "0")}</strong></div>
              <div><span>LEFT</span><strong>{telemetry.counts.left.toString().padStart(2, "0")}</strong></div>
              {!isHybrid && <div><span>RIGHT</span><strong>{telemetry.counts.right.toString().padStart(2, "0")}</strong></div>}
            </div>
            <div className="drive-output-state">
              [ KEY POST: {telemetry.lastKeyPosted === null ? "---" : telemetry.lastKeyPosted ? "OK" : "BLOCKED"} ]
              {telemetry.lastKeyError ? <span>{telemetry.lastKeyError}</span> : null}
            </div>
            <button className="drive-text-button" type="button" disabled={!connected} onClick={() => void resetCounts()}>
              RESET COUNTERS
            </button>
          </section>

          <section className="drive-module">
            <div className="drive-module-label">[ 01 // BASELINE ]</div>
            <h2>{isHybrid ? "Relax the submental muscle" : "Relax both muscles"}</h2>
            <p>{isHybrid ? "Jaw loose, tongue resting, head still. Three seconds calibrates live channel A; channel B remains a disclosed synthetic test signal." : "Jaw loose, tongue resting, head still. Three seconds sets a separate threshold for each channel."}</p>
            <div className="drive-progress">
              <div>
                <span>{telemetry.calibration.active ? "CAPTURING REST" : "READY"}</span>
                <strong>
                  {telemetry.calibration.active
                    ? `${telemetry.calibration.remaining.toFixed(1)}S`
                    : `${calibrationPercent}%`}
                </strong>
              </div>
              <span><i style={{ width: `${calibrationPercent}%` }} /></span>
            </div>
            <button
              className="drive-primary-button"
              type="button"
              disabled={!connected || telemetry.calibration.active || unsafe}
              onClick={() => void calibrate()}
            >
              {telemetry.calibration.active ? "[ HOLD STILL — ZEROING ]" : "[ 01 // CALIBRATE 3S ]"}
            </button>
          </section>

          <section className="drive-module drive-mapping-module">
            <div className="drive-module-label">[ 02 // FIXED MAPPING ]</div>
            <h2>{isHybrid ? "Two demo actions. No classifier." : "Three commands. No training."}</h2>
            <ol>
              <li>
                <b>A</b><span><strong>MYLOHYOID ONLY → FORWARD</strong>Use the same JUMP contraction as Flappy. Fixed 1.0s pulse.</span>
              </li>
              <li>
                <b>B</b><span><strong>{isHybrid ? "SIMULATED TEST PULSE → LEFT" : "LEFT MASSETER ONLY → LEFT"}</strong>{isHybrid ? "Button-triggered mock input, visibly labeled throughout. Fixed 0.2s pulse." : "Brief, gentle left-side bite. Fixed 0.2s pulse."}</span>
              </li>
              {!isHybrid && (
                <li>
                  <b>A+B</b><span><strong>BOTH TOGETHER → RIGHT</strong>Start both within {telemetry.coincidenceMs}ms. Fixed 0.2s pulse. Coactivation wins.</span>
                </li>
              )}
            </ol>
            <div className="drive-no-reverse">{isHybrid ? "[ RIGHT: UNAVAILABLE / SENSOR B BROKEN ]" : "[ REVERSE: NOT MAPPED ]"}</div>
          </section>

          <section className="drive-module drive-execution-module">
            <div className="drive-module-label">[ 03 // EXECUTION ]</div>
            <button
              type="button"
              role="switch"
              aria-checked={telemetry.armed}
              className={`drive-arm-button${telemetry.armed ? " is-armed" : ""}`}
              disabled={
                !connected ||
                telemetry.calibration.active ||
                unsafe ||
                (!telemetry.armed && (!telemetry.calibrated || !telemetry.canArm))
              }
              onClick={() => void setArmed(!telemetry.armed)}
            >
              <span>{telemetry.armed ? "DISARM OUTPUT" : "ARM OUTPUT"}</span>
              <i><b /></i>
            </button>
            <button className="drive-primary-button" type="button" onClick={openGame}>
              [ 03 // LAUNCH CAR ↗ ]
            </button>
            {isHybrid && (
              <button
                className={`drive-mock-left-button${mockLeftCountdown !== null ? " is-counting" : ""}`}
                type="button"
                disabled={!connected || !telemetry.armed || telemetry.calibration.active || unsafe || mockLeftCountdown !== null}
                onClick={beginMockLeft}
              >
                {mockLeftCountdown === null
                  ? "[ MOCK B // FOCUS CAR + LEFT IN 2S ]"
                  : `[ LEFT IN ${mockLeftCountdown}S — FOCUS GAME ]`}
              </button>
            )}
            <p>{isHybrid ? "FORWARD remains real hardware. For the disclosed mock LEFT, click its button and focus the car canvas before the two-second countdown ends." : "Disarm before tuning. Re-arm, refocus the game canvas, then relax fully between commands so the gate can reset."}</p>
          </section>
        </aside>
      </main>

      <footer className="drive-footer">
        <span>[ LOCALHOST / RAW BIOSIGNAL NEVER LEAVES MAC ]</span>
        <span>{isHybrid ? `[ A LIVE→↑ ${telemetry.forwardPulseMs}MS / B MOCK→← ${telemetry.turnPulseMs}MS / RIGHT DISABLED ]` : `[ A→↑ ${telemetry.forwardPulseMs}MS / B→← ${telemetry.turnPulseMs}MS / A+B→RIGHT ${telemetry.turnPulseMs}MS ]`}</span>
        <span>{lastEvent ? `[ EVENT: ${lastEvent.message} ]` : "[ EVENT: NONE ]"}</span>
      </footer>
    </div>
  );
}
