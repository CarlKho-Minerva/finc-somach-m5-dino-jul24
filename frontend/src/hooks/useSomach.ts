import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { parseServerMessage } from "../protocol";
import type {
  ConnectionState,
  DashboardEvent,
  ParsedServerEvent,
  SignalBuffers,
  SomachController,
  TelemetryState,
} from "../types";

const MAX_BUFFER_SAMPLES = 6_000;
const CALIBRATION_SECONDS = 3;

const initialTelemetry: TelemetryState = {
  seq: 0,
  timestamp: null,
  rms: 0,
  threshold: 100,
  armed: false,
  jumpCount: 0,
  sampleRate: 0,
  source: "unknown",
  device: "Waiting for backend",
  leadsOff: false,
  clipping: false,
  calibration: { active: false, progress: 0, remaining: CALIBRATION_SECONDS },
  quartz: { available: false, trusted: false, lastCallMs: null },
  recording: { active: false, seconds: 0, samples: 0, markers: 0, path: null },
  model: {
    available: false,
    active: false,
    accuracy: null,
    balancedAccuracy: null,
    score: null,
    threshold: null,
    error: null,
  },
  refractoryMs: 250,
  latencyMs: null,
  lastPacketAt: null,
  lastJumpAt: null,
};

const initialConnection: ConnectionState = {
  status: "connecting",
  attempt: 0,
  nextRetryMs: null,
  lastConnectedAt: null,
  error: null,
};

function resolveWebSocketUrl(): string {
  const configured = import.meta.env.VITE_WS_URL?.trim();
  if (configured) {
    if (/^wss?:\/\//i.test(configured)) return configured;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const path = configured.startsWith("/") ? configured : `/${configured}`;
    return `${protocol}//${window.location.host}${path}`;
  }

  if (import.meta.env.DEV) {
    return `ws://${window.location.hostname || "localhost"}:8123/ws`;
  }
  return "ws://127.0.0.1:8123/ws";
}

function resolveApiPath(path: string): string {
  const configured = import.meta.env.VITE_API_BASE?.trim().replace(/\/$/, "");
  if (!configured && import.meta.env.PROD) {
    return `http://127.0.0.1:8123${path}`;
  }
  if (!configured) return path;
  if (configured.endsWith("/api") && path.startsWith("/api/")) {
    return `${configured}${path.slice(4)}`;
  }
  return `${configured}${path}`;
}

function pushCapped(target: number[], values: number[]): void {
  if (!values.length) return;
  if (values.length >= MAX_BUFFER_SAMPLES) {
    target.splice(0, target.length, ...values.slice(-MAX_BUFFER_SAMPLES));
    return;
  }
  target.push(...values);
  const excess = target.length - MAX_BUFFER_SAMPLES;
  if (excess > 0) target.splice(0, excess);
}

function epochLatency(timestamp: number | undefined): number | null {
  if (timestamp === undefined) return null;
  const epochMs = timestamp > 1_000_000_000_000 ? timestamp : timestamp * 1_000;
  if (epochMs < 1_500_000_000_000) return null;
  const latency = Date.now() - epochMs;
  return latency >= 0 && latency < 10_000 ? latency : null;
}

export function useSomach(): SomachController {
  const [telemetry, setTelemetry] = useState<TelemetryState>(initialTelemetry);
  const [connection, setConnection] = useState<ConnectionState>(initialConnection);
  const [lastEvent, setLastEvent] = useState<DashboardEvent | null>(null);
  const signals = useRef<SignalBuffers>({ raw: [], filtered: [], rms: [], revision: 0 });
  const socketRef = useRef<WebSocket | null>(null);
  const eventIdRef = useRef(0);
  const calibrationTimerRef = useRef<number | null>(null);
  const wsUrl = useMemo(resolveWebSocketUrl, []);

  const publishEvent = useCallback(
    (kind: DashboardEvent["kind"], message: string) => {
      eventIdRef.current += 1;
      setLastEvent({ id: eventIdRef.current, kind, message, at: Date.now() });
    },
    [],
  );

  const stopCalibrationTicker = useCallback(() => {
    if (calibrationTimerRef.current !== null) {
      window.clearInterval(calibrationTimerRef.current);
      calibrationTimerRef.current = null;
    }
  }, []);

  const startCalibrationTicker = useCallback(() => {
    stopCalibrationTicker();
    const startedAt = performance.now();
    setTelemetry((current) => ({
      ...current,
      calibration: { active: true, progress: 0, remaining: CALIBRATION_SECONDS },
    }));

    calibrationTimerRef.current = window.setInterval(() => {
      const elapsed = (performance.now() - startedAt) / 1_000;
      const progress = Math.min(1, elapsed / CALIBRATION_SECONDS);
      setTelemetry((current) => ({
        ...current,
        calibration: {
          active: progress < 1,
          progress: Math.max(current.calibration.progress, progress),
          remaining: Math.max(0, CALIBRATION_SECONDS - elapsed),
        },
      }));
      if (progress >= 1) stopCalibrationTicker();
    }, 50);
  }, [stopCalibrationTicker]);

  const applyParsedEvent = useCallback(
    (event: ParsedServerEvent | null) => {
      if (event === null) return;
      const now = Date.now();

      if (event.kind === "error") {
        publishEvent("error", event.message);
        return;
      }

      if (event.kind === "jump") {
        setTelemetry((current) => ({
          ...current,
          jumpCount: event.jumpCount ?? current.jumpCount + 1,
          rms: event.rms ?? current.rms,
          threshold: event.threshold ?? current.threshold,
          latencyMs: event.keyCallMs ?? current.latencyMs,
          quartz: {
            ...current.quartz,
            lastCallMs: event.keyCallMs ?? current.quartz.lastCallMs,
          },
          lastJumpAt: now,
          lastPacketAt: now,
        }));
        if (event.keyPosted === false) {
          publishEvent(
            "error",
            `JUMP decoded · SPACE blocked${event.keyError ? `: ${event.keyError}` : ""}`,
          );
        } else {
          publishEvent(
            "jump",
            event.keyPosted === true
              ? "JUMP impulse decoded · SPACE posted"
              : "JUMP impulse decoded",
          );
        }
        return;
      }

      const sampleSpan = Math.max(event.raw.length, event.filtered.length, 1);
      let rmsValues = event.rmsSeries;
      if (rmsValues.length === 1 && sampleSpan > 1) {
        rmsValues = Array.from({ length: sampleSpan }, () => rmsValues[0]);
      }
      pushCapped(signals.current.raw, event.raw);
      pushCapped(signals.current.filtered, event.filtered);
      pushCapped(signals.current.rms, rmsValues);
      if (event.raw.length || event.filtered.length || rmsValues.length) {
        signals.current.revision += 1;
      }

      if (event.patch.calibration?.active === false) stopCalibrationTicker();
      const measuredLatency =
        event.patch.latencyMs ?? epochLatency(event.patch.timestamp ?? undefined);
      setTelemetry((current) => {
        const recording = {
          ...current.recording,
          ...event.patch.recording,
        };
        // The backend intentionally clears the active recorder after an atomic
        // save. Keep the just-finished session totals visible until a new
        // recording starts instead of flashing back to 00:00 / zero markers.
        const preservingSavedTotals =
          event.patch.recording?.active === false &&
          current.recording.markers > 0 &&
          event.patch.recording.markers === 0 &&
          Boolean(event.patch.recording.path ?? current.recording.path);
        if (preservingSavedTotals) {
          recording.seconds = current.recording.seconds;
          recording.samples = current.recording.samples;
          recording.markers = current.recording.markers;
        }

        const model = {
          ...current.model,
          ...event.patch.model,
        };
        // Failed training is returned as HTTP 409 while the periodic model
        // snapshot still has error=null. Retain that actionable error until a
        // retry begins or a valid trained model arrives.
        if (
          current.model.error &&
          event.patch.model?.error === null &&
          !(event.patch.model.available ?? current.model.available)
        ) {
          model.error = current.model.error;
        }

        return {
          ...current,
          ...event.patch,
          calibration: {
            ...current.calibration,
            ...event.patch.calibration,
          },
          quartz: {
            ...current.quartz,
            ...event.patch.quartz,
          },
          recording,
          model,
          latencyMs: measuredLatency ?? current.latencyMs,
          lastPacketAt: now,
        };
      });
    },
    [publishEvent, stopCalibrationTicker],
  );

  const applyPayload = useCallback(
    (payload: unknown) => applyParsedEvent(parseServerMessage(payload)),
    [applyParsedEvent],
  );

  useEffect(() => {
    let disposed = false;
    let reconnectTimer: number | null = null;
    let attempt = 0;

    const connect = () => {
      if (disposed) return;
      attempt += 1;
      setConnection((current) => ({
        ...current,
        status: attempt === 1 ? "connecting" : "reconnecting",
        attempt,
        nextRetryMs: null,
        error: null,
      }));

      const socket = new WebSocket(wsUrl);
      socketRef.current = socket;

      socket.onopen = () => {
        if (disposed) return;
        attempt = 0;
        setConnection({
          status: "connected",
          attempt: 0,
          nextRetryMs: null,
          lastConnectedAt: Date.now(),
          error: null,
        });
      };

      socket.onmessage = (message) => {
        if (typeof message.data === "string") {
          applyPayload(message.data);
          return;
        }
        if (message.data instanceof Blob) {
          void message.data.text().then(applyPayload);
          return;
        }
        if (message.data instanceof ArrayBuffer) {
          applyPayload(new TextDecoder().decode(message.data));
        }
      };

      socket.onerror = () => {
        setConnection((current) => ({
          ...current,
          error: "Cannot reach the signal backend",
        }));
      };

      socket.onclose = (closeEvent) => {
        if (disposed) return;
        const retryAttempt = Math.max(1, attempt + 1);
        const baseDelay = Math.min(5_000, 450 * 1.7 ** (retryAttempt - 1));
        const retryDelay = Math.round(baseDelay + Math.random() * 180);
        setConnection((current) => ({
          ...current,
          status: "reconnecting",
          attempt: retryAttempt,
          nextRetryMs: retryDelay,
          error:
            closeEvent.code === 1000
              ? null
              : `Signal link closed (${closeEvent.code || "network"})`,
        }));
        reconnectTimer = window.setTimeout(connect, retryDelay);
      };
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socketRef.current?.close(1000, "Dashboard unmounted");
      socketRef.current = null;
    };
  }, [applyPayload, wsUrl]);

  useEffect(() => () => stopCalibrationTicker(), [stopCalibrationTicker]);

  const postJson = useCallback(
    async (path: string, body: Record<string, unknown> = {}) => {
      let response: Response;
      try {
        response = await fetch(resolveApiPath(path), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch {
        throw new Error("Backend is offline");
      }

      const contentType = response.headers.get("content-type") ?? "";
      const payload: unknown = contentType.includes("application/json")
        ? await response.json()
        : null;
      if (!response.ok) {
        const detail =
          payload && typeof payload === "object" && "detail" in payload
            ? String(payload.detail)
            : `Request failed (${response.status})`;
        throw new Error(detail);
      }
      if (payload !== null) applyPayload(payload);
    },
    [applyPayload],
  );

  const calibrate = useCallback(async () => {
    startCalibrationTicker();
    try {
      await postJson("/api/calibrate");
      publishEvent("info", "Capturing three seconds of passive baseline");
    } catch (error) {
      stopCalibrationTicker();
      setTelemetry((current) => ({
        ...current,
        calibration: { ...current.calibration, active: false },
      }));
      publishEvent("error", error instanceof Error ? error.message : "Calibration failed");
    }
  }, [postJson, publishEvent, startCalibrationTicker, stopCalibrationTicker]);

  const previewThreshold = useCallback((value: number) => {
    if (!Number.isFinite(value)) return;
    setTelemetry((current) => ({ ...current, threshold: Math.max(0, value) }));
  }, []);

  const setThreshold = useCallback(
    async (value: number) => {
      if (!Number.isFinite(value)) return;
      const next = Math.max(0, value);
      const previous = telemetry.threshold;
      previewThreshold(next);
      try {
        await postJson("/api/threshold", { value: next });
      } catch (error) {
        previewThreshold(previous);
        publishEvent("error", error instanceof Error ? error.message : "Threshold update failed");
      }
    },
    [postJson, previewThreshold, publishEvent, telemetry.threshold],
  );

  const setArmed = useCallback(
    async (armed: boolean) => {
      const previous = telemetry.armed;
      setTelemetry((current) => ({ ...current, armed }));
      try {
        await postJson("/api/armed", { armed });
        publishEvent("info", armed ? "Jump detection armed" : "Jump detection paused");
      } catch (error) {
        setTelemetry((current) => ({ ...current, armed: previous }));
        publishEvent("error", error instanceof Error ? error.message : "Arm update failed");
      }
    },
    [postJson, publishEvent, telemetry.armed],
  );

  const triggerMock = useCallback(async () => {
    try {
      await postJson("/api/mock/trigger");
    } catch (error) {
      publishEvent("error", error instanceof Error ? error.message : "Mock trigger failed");
    }
  }, [postJson, publishEvent]);

  const startRecording = useCallback(async () => {
    const previous = telemetry.recording;
    setTelemetry((current) => ({
      ...current,
      recording: {
        active: true,
        seconds: 0,
        samples: 0,
        markers: 0,
        path: null,
      },
    }));
    try {
      await postJson("/api/recording/start");
      publishEvent("info", "Local training session recording");
    } catch (error) {
      setTelemetry((current) => ({ ...current, recording: previous }));
      publishEvent("error", error instanceof Error ? error.message : "Recording failed");
    }
  }, [postJson, publishEvent, telemetry.recording]);

  const markRecording = useCallback(
    async (label: "jump" | "artifact") => {
      if (!telemetry.recording.active) {
        publishEvent("error", "Start a recording before adding markers");
        return;
      }
      const previousMarkers = telemetry.recording.markers;
      setTelemetry((current) => ({
        ...current,
        recording: {
          ...current.recording,
          markers: current.recording.markers + 1,
        },
      }));
      try {
        await postJson("/api/recording/mark", { label });
        if (label === "artifact") publishEvent("info", "Artifact excluded from training");
      } catch (error) {
        setTelemetry((current) => ({
          ...current,
          recording: { ...current.recording, markers: previousMarkers },
        }));
        publishEvent("error", error instanceof Error ? error.message : "Marker failed");
      }
    },
    [postJson, publishEvent, telemetry.recording.active, telemetry.recording.markers],
  );

  const stopRecording = useCallback(async () => {
    const wasActive = telemetry.recording.active;
    setTelemetry((current) => ({
      ...current,
      recording: { ...current.recording, active: false },
    }));
    try {
      await postJson("/api/recording/stop");
      publishEvent("info", "Training session saved locally");
    } catch (error) {
      setTelemetry((current) => ({
        ...current,
        recording: { ...current.recording, active: wasActive },
      }));
      publishEvent("error", error instanceof Error ? error.message : "Save failed");
    }
  }, [postJson, publishEvent, telemetry.recording.active]);

  const trainModel = useCallback(async () => {
    setTelemetry((current) => ({
      ...current,
      model: { ...current.model, error: null },
    }));
    try {
      publishEvent("info", "Training classifier locally on CPU");
      await postJson("/api/model/train");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Training failed";
      setTelemetry((current) => ({
        ...current,
        model: { ...current.model, error: message },
      }));
      publishEvent("error", message);
    }
  }, [postJson, publishEvent]);

  const setModelActive = useCallback(
    async (active: boolean) => {
      if (active && !telemetry.model.available) {
        publishEvent("error", "Train a valid model before activating it");
        return;
      }
      const previous = telemetry.model.active;
      setTelemetry((current) => ({
        ...current,
        model: { ...current.model, active },
      }));
      try {
        await postJson("/api/model/activate", { active });
        publishEvent("info", active ? "Learned model active" : "RMS trigger active");
      } catch (error) {
        setTelemetry((current) => ({
          ...current,
          model: { ...current.model, active: previous },
        }));
        publishEvent("error", error instanceof Error ? error.message : "Model toggle failed");
      }
    },
    [postJson, publishEvent, telemetry.model.active, telemetry.model.available],
  );

  return {
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
  };
}
