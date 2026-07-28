import type { MutableRefObject } from "react";

export type DriveChannelId = "a" | "b";
export type DriveAction = "forward" | "left" | "right" | "idle";

export interface DriveChannelState {
  rms: number;
  threshold: number;
  leadOff: boolean;
  clipping: boolean;
}

export interface DriveCalibrationState {
  active: boolean;
  progress: number;
  remaining: number;
}

export interface DriveTelemetry {
  sequence: number;
  timestamp: number | null;
  sampleRate: number;
  source: string;
  device: string;
  signalConnected: boolean;
  armed: boolean;
  calibrated: boolean;
  canArm: boolean;
  qualityError: string | null;
  backendError: string | null;
  action: DriveAction;
  lastActionAt: number | null;
  arbitrationState: string;
  waitingForRelease: boolean;
  lastKeyPosted: boolean | null;
  lastKeyError: string | null;
  channels: Record<DriveChannelId, DriveChannelState>;
  counts: Record<Exclude<DriveAction, "idle">, number>;
  calibration: DriveCalibrationState;
  coincidenceMs: number;
  forwardPulseMs: number;
  turnPulseMs: number;
  lastPacketAt: number | null;
}

export interface DriveSignals {
  a: number[];
  b: number[];
  revision: number;
}

export interface DriveConnection {
  status: "connecting" | "connected" | "reconnecting" | "disconnected";
  error: string | null;
  attempt: number;
}

export interface DriveEvent {
  id: number;
  kind: "action" | "error" | "info";
  message: string;
}

export interface DriveController {
  telemetry: DriveTelemetry;
  connection: DriveConnection;
  signals: MutableRefObject<DriveSignals>;
  wsUrl: string;
  lastEvent: DriveEvent | null;
  calibrate: () => Promise<void>;
  setArmed: (armed: boolean) => Promise<void>;
  setThreshold: (channel: DriveChannelId, value: number) => Promise<void>;
  previewThreshold: (channel: DriveChannelId, value: number) => void;
  resetCounts: () => Promise<void>;
  triggerMockLeft: () => Promise<void>;
}

export interface DriveParsedTelemetry {
  patch: Partial<Omit<DriveTelemetry, "channels" | "counts" | "calibration">> & {
    channels?: Partial<Record<DriveChannelId, Partial<DriveChannelState>>>;
    counts?: Partial<DriveTelemetry["counts"]>;
    calibration?: Partial<DriveCalibrationState>;
  };
  series: Partial<Record<DriveChannelId, number[]>>;
  eventAction: DriveAction | null;
  errorMessage: string | null;
}
