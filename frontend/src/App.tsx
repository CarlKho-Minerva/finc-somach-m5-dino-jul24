import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { flushSync } from "react-dom";
import { Oscilloscope } from "./components/Oscilloscope";
import { useSomach } from "./hooks/useSomach";

function PulseIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <path d="M2 17h6l3-10 6 19 4-15 3 6h6" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5 15 15 5M7 5h8v8" />
    </svg>
  );
}

function CalibrateIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="7" />
      <path d="M12 2v4m0 12v4M2 12h4m12 0h4" />
    </svg>
  );
}

function formatRms(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function formatThresholdInput(value: number): string {
  if (!Number.isFinite(value)) return "";
  return value >= 100 ? value.toFixed(0) : value.toFixed(1);
}

function formatLatency(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value < 1) return "<1 ms";
  return `${value.toFixed(value < 10 ? 1 : 0)} ms`;
}

function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  return `${Math.floor(safe / 60).toString().padStart(2, "0")}:${(safe % 60)
    .toString()
    .padStart(2, "0")}`;
}

function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return Math.round(value).toString();
}

function formatPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const percent = Math.abs(value) <= 1 ? value * 100 : value;
  return `${percent.toFixed(percent >= 99.95 ? 0 : 1)}%`;
}

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

export default function App() {
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
    triggerMock,
    startRecording,
    markRecording,
    stopRecording,
    trainModel,
    setModelActive,
  } = useSomach();
  const mockCooldownRef = useRef(0);
  const trialRunningRef = useRef(false);
  const [trialCue, setTrialCue] = useState<"3" | "2" | "1" | "JUMP NOW" | null>(null);
  const [learningBusy, setLearningBusy] = useState<"record" | "train" | null>(null);
  const [thresholdDraft, setThresholdDraft] = useState(() =>
    formatThresholdInput(telemetry.threshold),
  );
  const [thresholdEditing, setThresholdEditing] = useState(false);

  const isConnected = connection.status === "connected";
  const sourceName = telemetry.source.toLowerCase();
  const isMock = sourceName.includes("mock") || sourceName.includes("synthetic");
  const calibrationPercent = Math.round(telemetry.calibration.progress * 100);
  const quartzCallMs = telemetry.quartz.lastCallMs;
  const modelMetric = telemetry.model.balancedAccuracy ?? telemetry.model.accuracy;
  const modelValid = telemetry.model.available && !telemetry.model.error;
  const vuSegmentCount = 18;
  const vuActiveSegments = Math.min(
    vuSegmentCount,
    Math.max(0, Math.round((telemetry.rms / Math.max(telemetry.threshold, 1)) * vuSegmentCount)),
  );
  const modelScoreCopy = telemetry.model.score === null
    ? ""
    : ` · score ${telemetry.model.score.toFixed(2)}`;

  const runMockTrigger = useCallback(() => {
    const now = performance.now();
    if (now - mockCooldownRef.current < 450) return;
    mockCooldownRef.current = now;
    void triggerMock();
  }, [triggerMock]);

  useEffect(() => {
    if (!thresholdEditing) {
      setThresholdDraft(formatThresholdInput(telemetry.threshold));
    }
  }, [telemetry.threshold, thresholdEditing]);

  const runGuidedTrial = useCallback(async () => {
    if (trialRunningRef.current || !telemetry.recording.active) return;
    trialRunningRef.current = true;
    try {
      setTrialCue("3");
      await wait(650);
      setTrialCue("2");
      await wait(650);
      setTrialCue("1");
      await wait(650);
      flushSync(() => setTrialCue("JUMP NOW"));
      await new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => {
          void markRecording("jump").finally(resolve);
        });
      });
      await wait(650);
    } finally {
      setTrialCue(null);
      trialRunningRef.current = false;
    }
  }, [markRecording, telemetry.recording.active]);

  useEffect(() => {
    if (!isMock || !isConnected) return;
    const handleSpace = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.repeat) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        target.closest("button, input, textarea, select, [contenteditable='true']")
      ) {
        return;
      }
      event.preventDefault();
      runMockTrigger();
    };
    window.addEventListener("keydown", handleSpace);
    return () => window.removeEventListener("keydown", handleSpace);
  }, [isConnected, isMock, runMockTrigger]);

  useEffect(() => {
    if (!isConnected || !telemetry.recording.active) return;
    const handleTrialShortcut = (event: KeyboardEvent) => {
      if (event.code !== "KeyJ" || event.repeat) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        target.closest("button, input, textarea, select, [contenteditable='true']")
      ) {
        return;
      }
      event.preventDefault();
      void runGuidedTrial();
    };
    window.addEventListener("keydown", handleTrialShortcut);
    return () => window.removeEventListener("keydown", handleTrialShortcut);
  }, [isConnected, runGuidedTrial, telemetry.recording.active]);

  const toggleRecording = async () => {
    if (learningBusy !== null) return;
    setLearningBusy("record");
    if (telemetry.recording.active) await stopRecording();
    else await startRecording();
    setLearningBusy(null);
  };

  const handleTrain = async () => {
    if (learningBusy !== null) return;
    setLearningBusy("train");
    await trainModel();
    setLearningBusy(null);
  };

  const commitThreshold = async (value: number) => {
    if (!Number.isFinite(value) || value < 0) return;
    const rounded = Math.round(value * 10) / 10;
    setThresholdEditing(false);
    setThresholdDraft(formatThresholdInput(rounded));
    await setThreshold(rounded);
  };

  const handleThresholdSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (thresholdDraft.trim() === "") return;
    await commitThreshold(Number(thresholdDraft));
  };

  const nudgeThreshold = async (delta: number) => {
    await commitThreshold(Math.max(0, telemetry.threshold + delta));
  };

  const openGame = () => {
    const game = window.open(
      "https://flappybird.io/",
      "somach-flappy",
      "popup=yes,width=980,height=760,resizable=yes,scrollbars=yes",
    );
    game?.focus();
  };

  const sourceValue = isMock
    ? "Mock generator"
    : sourceName.includes("hardware") || sourceName.includes("serial")
      ? "ESP32 hardware"
      : telemetry.source === "unknown"
        ? "Waiting"
        : telemetry.source;
  const leadValue = !isConnected
    ? "Waiting"
    : telemetry.leadsOff
      ? "Lead off"
      : isMock
        ? "Simulated"
        : "Contact good";
  const quartzValue = !telemetry.quartz.available
    ? "Unavailable"
    : telemetry.quartz.trusted
      ? "Authorized"
      : "Permission needed";

  return (
    <div className="dashboard-shell">
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />

      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <PulseIcon />
          </span>
          <span className="brand-copy">
            <strong>SOMACH</strong>
            <span>Silent speech control</span>
          </span>
        </div>

        <div className="topbar-actions">
          <div
            className={`connection-pill connection-${connection.status}`}
            title={connection.error ?? wsUrl}
          >
            <span className="connection-beacon" />
            {isConnected
              ? "Signal live"
              : connection.status === "reconnecting"
                ? "Reconnecting"
                : "Connecting"}
          </div>
          <button className="button button-secondary open-game" type="button" onClick={openGame}>
            Open Flappy
            <ArrowIcon />
          </button>
        </div>
      </header>

      <main className="dashboard-content">
        <section className="status-strip" aria-label="System telemetry">
          <span className={`status-stream${isConnected ? " is-live" : ""}`}>
            <i />
            [ STATUS: {isConnected ? "LIVE" : "WAIT"} ]
          </span>
          <span>[ SRC: {sourceValue} ]</span>
          <span>[ RATE: {telemetry.sampleRate ? `${Math.round(telemetry.sampleRate)}HZ` : "---"} ]</span>
          <span>[ LEADS: {leadValue} ]</span>
          <span>[ QUARTZ: {quartzValue} ]</span>
          <span className="status-latency">
            [ POST: {formatLatency(quartzCallMs)} / LOCK: {telemetry.refractoryMs}MS ]
          </span>
        </section>

        {(telemetry.leadsOff || telemetry.clipping) && (
          <div className="signal-warning" role="alert">
            <span className="warning-icon">!</span>
            <div>
              <strong>{telemetry.leadsOff ? "Electrode contact lost" : "ADC signal clipping"}</strong>
              <span>
                {telemetry.leadsOff
                  ? "Press each hydrogel pad firmly, then recalibrate before arming."
                  : "Reduce sensor gain or reseat the electrodes before continuing."}
              </span>
            </div>
          </div>
        )}

        <div className="workspace-grid">
          <section className="panel scope-panel">
            <div className="panel-heading scope-heading">
              <div className="rms-hero">
                <strong>{formatRms(telemetry.rms)}</strong>
                <div>
                  <span className="eyebrow">[ RMS ENVELOPE / 150MS ]</span>
                  <h1>Submental sEMG</h1>
                </div>
                <div
                  className="vu-meter"
                  role="meter"
                  aria-label="RMS signal power"
                  aria-valuemin={0}
                  aria-valuemax={vuSegmentCount}
                  aria-valuenow={vuActiveSegments}
                >
                  {Array.from({ length: vuSegmentCount }, (_, index) => (
                    <i key={index} className={index < vuActiveSegments ? "is-active" : ""} />
                  ))}
                </div>
              </div>
              <div className="scope-readouts">
                <span className={`armed-badge${telemetry.armed ? " is-armed" : ""}`}>
                  <i /> {telemetry.armed ? "Armed" : "Paused"}
                </span>
              </div>
            </div>

            <div className="scope-stage">
              <div className="scope-tech-meta" aria-label="Signal acquisition settings">
                <span>[ GAIN: 1.0X ]</span>
                <span>[ RATE: {Math.round(telemetry.sampleRate || 1_000)}HZ ]</span>
                <span>[ BUFFER: 2.5S ]</span>
              </div>
              <Oscilloscope
                signals={signals}
                threshold={telemetry.threshold}
                rms={telemetry.rms}
                sampleRate={telemetry.sampleRate}
                armed={telemetry.armed}
                onThresholdPreview={previewThreshold}
                onThresholdCommit={(value) => void setThreshold(value)}
              />

              <form
                className="threshold-control"
                onSubmit={(event) => void handleThresholdSubmit(event)}
              >
                <label htmlFor="threshold-input">
                  <span>threshold</span>
                  <input
                    id="threshold-input"
                    type="number"
                    inputMode="decimal"
                    min="0"
                    step="0.5"
                    value={thresholdDraft}
                    disabled={!isConnected}
                    onBlur={() => setThresholdEditing(false)}
                    onChange={(event) => {
                      setThresholdEditing(true);
                      setThresholdDraft(event.target.value);
                    }}
                    onFocus={() => setThresholdEditing(true)}
                  />
                </label>
                <button type="button" disabled={!isConnected} onClick={() => void nudgeThreshold(-5)}>
                  −5
                </button>
                <button type="button" disabled={!isConnected} onClick={() => void nudgeThreshold(5)}>
                  +5
                </button>
                <button
                  type="submit"
                  disabled={
                    !isConnected ||
                    thresholdDraft.trim() === "" ||
                    !Number.isFinite(Number(thresholdDraft))
                  }
                >
                  Apply
                </button>
              </form>
            </div>

            <div className="scope-footer">
              <div className="legend" aria-label="Waveform legend">
                <span><i className="legend-raw" />Raw reference</span>
                <span><i className="legend-rms" />150 ms RMS</span>
                <span><i className="legend-threshold" />Trigger threshold</span>
              </div>
              <span className="drag-hint">drag the threshold line to tune</span>
            </div>

          </section>

          <aside className="control-column">
            <section
              className={`panel workflow-panel${telemetry.recording.active ? " is-recording" : ""}`}
              aria-label="SOMACH setup workflow"
            >
              <section className="workflow-step calibration-panel">
                <div className="workflow-heading">
                  <span className="workflow-index">[01]</span>
                  <div>
                    <span className="eyebrow">[ CALIBRATION ]</span>
                    <h2>Baseline zero</h2>
                  </div>
                </div>
                <p>
                  Relax for three seconds. SOMACH measures rest and sets
                  <span className="formula"> T = μ + 3.5σ</span>.
                </p>

                <div className="calibration-progress" aria-hidden={!telemetry.calibration.active}>
                  <div className="progress-meta">
                    <span>{telemetry.calibration.active ? "capturing rest" : "ready to calibrate"}</span>
                    <strong>
                      {telemetry.calibration.active
                        ? `${telemetry.calibration.remaining.toFixed(1)}s`
                        : `${calibrationPercent}%`}
                    </strong>
                  </div>
                  <div
                    className="progress-track"
                    role="progressbar"
                    aria-label="Calibration progress"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={calibrationPercent}
                  >
                    <span style={{ width: `${calibrationPercent}%` }} />
                  </div>
                </div>

                <button
                  className="button button-primary calibrate-button"
                  type="button"
                  disabled={!isConnected || telemetry.calibration.active}
                  onClick={() => void calibrate()}
                >
                  <CalibrateIcon />
                  {telemetry.calibration.active
                    ? "[ 01 // ZEROING — HOLD ]"
                    : "[ 01 // CALIBRATE BASELINE ]"}
                </button>
              </section>

              <section className="workflow-step learning-panel">
                <div className="workflow-heading">
                  <span className="workflow-index">[02]</span>
                  <div>
                    <span className="eyebrow">[ TRAINING ]</span>
                    <h2>JUMP model</h2>
                  </div>
                  <span className={`recording-chip${telemetry.recording.active ? " is-live" : ""}`}>
                    <i /> {telemetry.recording.active ? "[ REC ]" : "[ LOCAL CPU ]"}
                  </span>
                </div>

                <div className="learning-stats" aria-label="Recording statistics">
                  <div>
                    <span>elapsed</span>
                    <strong>{formatDuration(telemetry.recording.seconds)}</strong>
                  </div>
                  <div>
                    <span>markers</span>
                    <strong>{telemetry.recording.markers}</strong>
                  </div>
                  <div>
                    <span>samples</span>
                    <strong>{formatCount(telemetry.recording.samples)}</strong>
                  </div>
                </div>

                <div className="recording-actions">
                  <button
                    type="button"
                    className={`record-button${telemetry.recording.active ? " is-stop" : ""}`}
                    disabled={!isConnected || learningBusy !== null || trialCue !== null}
                    onClick={() => void toggleRecording()}
                  >
                    <span className="record-symbol" />
                    {learningBusy === "record"
                      ? "Working…"
                      : telemetry.recording.active
                        ? "[ 02 // STOP + SAVE ]"
                        : "[ 02 // START CAPTURE ]"}
                  </button>
                  <button
                    type="button"
                    className="artifact-button"
                    disabled={!isConnected || !telemetry.recording.active || trialCue !== null}
                    onClick={() => void markRecording("artifact")}
                    title="Label a cough, movement, swallow, or accidental spike"
                  >
                    Mark artifact
                  </button>
                </div>

                <button
                  type="button"
                  className="guided-trial"
                  disabled={!isConnected || !telemetry.recording.active || trialCue !== null}
                  onClick={() => void runGuidedTrial()}
                >
                  <span>
                    <strong>{trialCue ? `Get ready · ${trialCue}` : "Guided JUMP trial"}</strong>
                    <small>3–2–1 cue · marker lands at flash</small>
                  </span>
                  <kbd>J</kbd>
                </button>

                <div className="model-control">
                  <div className="model-summary">
                    <div>
                      <span className="model-name">Learned classifier</span>
                      <span
                        className={`model-health${modelValid ? " is-valid" : ""}`}
                        title={modelValid && telemetry.model.threshold !== null
                          ? `Decision threshold ${telemetry.model.threshold.toFixed(2)}`
                          : undefined}
                      >
                        {modelValid
                          ? `Balanced accuracy ${formatPercent(modelMetric)}${modelScoreCopy}`
                          : "Record trials, then train"}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="train-button"
                      disabled={!isConnected || telemetry.recording.active || learningBusy !== null}
                      onClick={() => void handleTrain()}
                    >
                      {learningBusy === "train" ? "Training…" : "Train"}
                    </button>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={telemetry.model.active}
                    className={`model-toggle${telemetry.model.active ? " is-on" : ""}`}
                    disabled={!isConnected || !modelValid || learningBusy !== null}
                    onClick={() => void setModelActive(!telemetry.model.active)}
                  >
                    <span>rms</span>
                    <span className="mini-switch"><i /></span>
                    <span>model</span>
                  </button>
                </div>
                {telemetry.model.error && (
                  <div className="model-error" role="alert">{telemetry.model.error}</div>
                )}
              </section>

              <section className="workflow-step jump-panel">
                <div className="workflow-heading">
                  <span className="workflow-index">[03]</span>
                  <div>
                    <span className="eyebrow">[ EXECUTION ]</span>
                    <h2>Detector</h2>
                  </div>
                  <span className="jump-status">[ {telemetry.armed ? "LIVE" : "PAUSED"} ]</span>
                </div>

                <div className="live-detector-row">
                  <div className="jump-count-wrap">
                    <span key={telemetry.jumpCount} className="jump-count">
                      {telemetry.jumpCount.toString().padStart(2, "0")}
                    </span>
                    <span className="jump-unit">accepted triggers</span>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={telemetry.armed}
                    className={`arm-control${telemetry.armed ? " is-on" : ""}`}
                    disabled={!isConnected || telemetry.calibration.active}
                    onClick={() => void setArmed(!telemetry.armed)}
                  >
                    <span className="arm-copy">
                      <strong>{telemetry.armed ? "Detection armed" : "Detection paused"}</strong>
                      <small>{telemetry.armed ? "JUMP posts SPACE" : "No keys posted"}</small>
                    </span>
                    <span className="switch-track"><i /></span>
                  </button>
                </div>

                <div className="live-actions">
                  <button className="button button-primary" type="button" onClick={openGame}>
                    [ 03 // Launch Flappy <ArrowIcon /> ]
                  </button>
                  {isMock && (
                    <button
                      className="button button-secondary"
                      type="button"
                      disabled={!isConnected}
                      onClick={runMockTrigger}
                    >
                      Simulate JUMP
                    </button>
                  )}
                </div>

                {!isMock && (
                  <div className="device-inline" title={telemetry.device}>
                    <span>hardware</span>
                    <code>{telemetry.device}</code>
                    <span>gpio 36</span>
                  </div>
                )}
              </section>
            </section>
          </aside>
        </div>

        <footer className="dashboard-footer">
          <span><i className="privacy-dot" />Local processing · signal never leaves this Mac</span>
          <span className="socket-address" title={wsUrl}>{wsUrl}</span>
        </footer>
      </main>

      {lastEvent && (
        <div key={lastEvent.id} className={`event-toast event-${lastEvent.kind}`} role="status">
          <span>{lastEvent.kind === "jump" ? "↑" : lastEvent.kind === "error" ? "!" : "i"}</span>
          {lastEvent.message}
        </div>
      )}

      {trialCue && (
        <div
          className={`trial-cue-overlay${trialCue === "JUMP NOW" ? " is-jump" : ""}`}
          role="alert"
          aria-live="assertive"
        >
          <div className="trial-cue-card">
            <span>{trialCue === "JUMP NOW" ? "SUBVOCALIZE" : "RELAX · GET READY"}</span>
            <strong key={trialCue}>{trialCue}</strong>
            <small>
              {trialCue === "JUMP NOW"
                ? "Make the rehearsed silent JUMP gesture"
                : "Keep your jaw and neck still"}
            </small>
          </div>
        </div>
      )}
    </div>
  );
}
