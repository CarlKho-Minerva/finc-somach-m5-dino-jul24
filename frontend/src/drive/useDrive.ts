import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { parseDriveMessage } from "./protocol";
import type {
  DriveChannelId,
  DriveConnection,
  DriveController,
  DriveEvent,
  DriveSignals,
  DriveTelemetry,
} from "./types";

const MAX_POINTS = 360;
const CALIBRATION_SECONDS = 3;

const initialTelemetry: DriveTelemetry = {
  sequence: 0,
  timestamp: null,
  sampleRate: 0,
  source: "waiting",
  device: "Waiting for dual-drive backend",
  signalConnected: false,
  armed: false,
  calibrated: false,
  canArm: false,
  qualityError: null,
  backendError: null,
  action: "idle",
  lastActionAt: null,
  arbitrationState: "idle",
  waitingForRelease: false,
  lastKeyPosted: null,
  lastKeyError: null,
  channels: {
    a: { rms: 0, threshold: 50, leadOff: false, clipping: false },
    b: { rms: 0, threshold: 50, leadOff: false, clipping: false },
  },
  counts: { forward: 0, left: 0, right: 0 },
  calibration: { active: false, progress: 0, remaining: CALIBRATION_SECONDS },
  coincidenceMs: 80,
  forwardPulseMs: 1_000,
  turnPulseMs: 200,
  lastPacketAt: null,
};

const initialConnection: DriveConnection = {
  status: "connecting",
  error: null,
  attempt: 0,
};

function websocketUrl(): string {
  const configured = import.meta.env.VITE_DRIVE_WS_URL?.trim();
  if (configured) return configured;
  const hostname = window.location.hostname || "127.0.0.1";
  return `ws://${hostname}:8124/ws`;
}

function apiUrl(path: string): string {
  const configured = import.meta.env.VITE_DRIVE_API_BASE?.trim().replace(/\/$/, "");
  const hostname = window.location.hostname || "127.0.0.1";
  return `${configured || `http://${hostname}:8124`}${path}`;
}

function push(target: number[], values: number[]): void {
  if (!values.length) return;
  target.push(...values);
  if (target.length > MAX_POINTS) target.splice(0, target.length - MAX_POINTS);
}

export function useDrive(): DriveController {
  const [telemetry, setTelemetry] = useState<DriveTelemetry>(initialTelemetry);
  const [connection, setConnection] = useState<DriveConnection>(initialConnection);
  const [lastEvent, setLastEvent] = useState<DriveEvent | null>(null);
  const signals = useRef<DriveSignals>({ a: [], b: [], revision: 0 });
  const socketRef = useRef<WebSocket | null>(null);
  const eventIdRef = useRef(0);
  const wsUrl = useMemo(websocketUrl, []);

  const publish = useCallback((kind: DriveEvent["kind"], message: string) => {
    eventIdRef.current += 1;
    setLastEvent({ id: eventIdRef.current, kind, message });
  }, []);

  const sendControl = useCallback(
    async (command: string, payload: Record<string, unknown> = {}, fallbackPath?: string) => {
      // HTTP gives each button a definitive success/error response. The
      // WebSocket remains the low-latency telemetry channel.
      if (fallbackPath) {
        const response = await fetch(apiUrl(fallbackPath), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: Object.keys(payload).length ? JSON.stringify(payload) : undefined,
        });
        if (!response.ok) {
          let detail = `Control failed (${response.status})`;
          try {
            const body = (await response.json()) as { detail?: unknown };
            if (typeof body.detail === "string") detail = body.detail;
          } catch {
            // Keep the status-based fallback when the response is not JSON.
          }
          throw new Error(detail);
        }
        return;
      }
      const socket = socketRef.current;
      if (socket?.readyState !== WebSocket.OPEN) {
        throw new Error("Dual-drive backend is not connected");
      }
      socket.send(JSON.stringify({ command, ...payload }));
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | null = null;
    let attempt = 0;

    const connect = () => {
      if (cancelled) return;
      setConnection((current) => ({
        ...current,
        status: attempt ? "reconnecting" : "connecting",
        attempt,
      }));
      const socket = new WebSocket(wsUrl);
      socketRef.current = socket;

      socket.addEventListener("open", () => {
        attempt = 0;
        setConnection({ status: "connected", error: null, attempt: 0 });
      });

      socket.addEventListener("message", (message) => {
        const parsed = parseDriveMessage(message.data);
        if (!parsed) {
          publish("error", "Dual-drive backend sent malformed telemetry");
          return;
        }
        const now = Date.now();
        push(signals.current.a, parsed.series.a ?? []);
        push(signals.current.b, parsed.series.b ?? []);
        if ((parsed.series.a?.length ?? 0) || (parsed.series.b?.length ?? 0)) {
          signals.current.revision += 1;
        }
        setTelemetry((current) => ({
          ...current,
          ...parsed.patch,
          action: parsed.patch.action ?? current.action,
          lastActionAt: parsed.eventAction ? now : current.lastActionAt,
          channels: {
            a: { ...current.channels.a, ...parsed.patch.channels?.a },
            b: { ...current.channels.b, ...parsed.patch.channels?.b },
          },
          counts: { ...current.counts, ...parsed.patch.counts },
          calibration: { ...current.calibration, ...parsed.patch.calibration },
          lastPacketAt: now,
        }));
        if (parsed.errorMessage) publish("error", parsed.errorMessage);
        if (parsed.eventAction) {
          publish("action", `${parsed.eventAction.toUpperCase()} command accepted`);
        }
      });

      socket.addEventListener("error", () => {
        setConnection((current) => ({ ...current, error: `Cannot reach ${wsUrl}` }));
      });

      socket.addEventListener("close", () => {
        if (socketRef.current === socket) socketRef.current = null;
        if (cancelled) return;
        attempt += 1;
        setConnection({
          status: "reconnecting",
          error: "Dual-drive backend disconnected",
          attempt,
        });
        setTelemetry((current) => ({
          ...current,
          signalConnected: false,
          armed: false,
          action: "idle",
        }));
        const delay = Math.min(8_000, 500 * 2 ** Math.min(attempt, 4));
        retryTimer = window.setTimeout(connect, delay);
      });
    };

    connect();
    return () => {
      cancelled = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      const socket = socketRef.current;
      socketRef.current = null;
      socket?.close();
    };
  }, [publish, wsUrl]);

  const calibrate = useCallback(async () => {
    try {
      await sendControl("calibrate", {}, "/api/calibrate");
      setTelemetry((current) => ({
        ...current,
        armed: false,
        calibrated: false,
        canArm: false,
        calibration: { active: true, progress: 0, remaining: CALIBRATION_SECONDS },
      }));
      publish("info", "Three-second rest calibration started");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Calibration failed";
      publish("error", message);
    }
  }, [publish, sendControl]);

  const setArmed = useCallback(
    async (armed: boolean) => {
      try {
        await sendControl("armed", { armed }, "/api/armed");
        setTelemetry((current) => ({ ...current, armed }));
        publish("info", armed ? "Directional control armed" : "Directional control paused");
      } catch (error) {
        publish("error", error instanceof Error ? error.message : "Arm control failed");
      }
    },
    [publish, sendControl],
  );

  const setThreshold = useCallback(
    async (channel: DriveChannelId, value: number) => {
      const safeValue = Math.min(4_095, Math.max(0.1, Math.round(value * 10) / 10));
      try {
        await sendControl("threshold", { channel, value: safeValue }, "/api/threshold");
        setTelemetry((current) => ({
          ...current,
          channels: {
            ...current.channels,
            [channel]: { ...current.channels[channel], threshold: safeValue },
          },
        }));
      } catch (error) {
        publish("error", error instanceof Error ? error.message : "Threshold update failed");
      }
    },
    [publish, sendControl],
  );

  const previewThreshold = useCallback((channel: DriveChannelId, value: number) => {
    const safeValue = Math.min(4_095, Math.max(0.1, value));
    setTelemetry((current) => ({
      ...current,
      channels: {
        ...current.channels,
        [channel]: { ...current.channels[channel], threshold: safeValue },
      },
    }));
  }, []);

  const resetCounts = useCallback(async () => {
    try {
      await sendControl("reset-counter", {}, "/api/counter/reset");
      setTelemetry((current) => ({
        ...current,
        counts: { forward: 0, left: 0, right: 0 },
      }));
    } catch (error) {
      publish("error", error instanceof Error ? error.message : "Counter reset failed");
    }
  }, [publish, sendControl]);

  const triggerMockLeft = useCallback(async () => {
    try {
      await sendControl(
        "mock-trigger",
        { action: "left" },
        "/api/mock/trigger",
      );
      publish("info", "Disclosed simulated channel B pulse sent: LEFT");
    } catch (error) {
      publish("error", error instanceof Error ? error.message : "Mock LEFT trigger failed");
    }
  }, [publish, sendControl]);

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
    resetCounts,
    triggerMockLeft,
  };
}
