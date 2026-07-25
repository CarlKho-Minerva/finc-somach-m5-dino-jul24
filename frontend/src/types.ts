import type { MutableRefObject } from "react";

export type ConnectionStatus =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

export interface CalibrationState {
  active: boolean;
  progress: number;
  remaining: number;
}

export interface QuartzState {
  available: boolean;
  trusted: boolean;
  lastCallMs: number | null;
}

export interface RecordingState {
  active: boolean;
  seconds: number;
  samples: number;
  markers: number;
  path: string | null;
}

export interface ModelState {
  available: boolean;
  active: boolean;
  accuracy: number | null;
  balancedAccuracy: number | null;
  score: number | null;
  threshold: number | null;
  error: string | null;
}

export interface TelemetryState {
  seq: number;
  timestamp: number | null;
  rms: number;
  threshold: number;
  armed: boolean;
  jumpCount: number;
  sampleRate: number;
  source: string;
  device: string;
  leadsOff: boolean;
  clipping: boolean;
  calibration: CalibrationState;
  quartz: QuartzState;
  recording: RecordingState;
  model: ModelState;
  refractoryMs: number;
  latencyMs: number | null;
  lastPacketAt: number | null;
  lastJumpAt: number | null;
}

export interface SignalBuffers {
  raw: number[];
  filtered: number[];
  rms: number[];
  revision: number;
}

export interface ConnectionState {
  status: ConnectionStatus;
  attempt: number;
  nextRetryMs: number | null;
  lastConnectedAt: number | null;
  error: string | null;
}

export interface DashboardEvent {
  id: number;
  kind: "jump" | "error" | "info";
  message: string;
  at: number;
}

export interface SomachController {
  telemetry: TelemetryState;
  connection: ConnectionState;
  signals: MutableRefObject<SignalBuffers>;
  wsUrl: string;
  lastEvent: DashboardEvent | null;
  calibrate: () => Promise<void>;
  setArmed: (armed: boolean) => Promise<void>;
  setThreshold: (value: number) => Promise<void>;
  previewThreshold: (value: number) => void;
  triggerMock: () => Promise<void>;
  startRecording: () => Promise<void>;
  markRecording: (label: "jump" | "artifact") => Promise<void>;
  stopRecording: () => Promise<void>;
  trainModel: () => Promise<void>;
  setModelActive: (active: boolean) => Promise<void>;
}

export interface TelemetryMessage {
  type: "telemetry" | "status" | "snapshot";
  seq?: number;
  timestamp?: number;
  raw?: number[] | number;
  filtered?: number[] | number;
  rms?: number[] | number;
  threshold?: number;
  armed?: boolean;
  jumpCount?: number;
  sampleRate?: number;
  source?: string;
  device?: string;
  leadsOff?: boolean;
  clipping?: boolean;
  calibration?: Partial<CalibrationState>;
  quartz?: Partial<QuartzState>;
  recording?: Partial<RecordingState>;
  model?: Partial<ModelState>;
  refractoryMs?: number;
  latencyMs?: number;
}

export interface JumpMessage {
  type: "jump";
  jumpCount: number;
  rms?: number;
  threshold?: number;
  keyCallMs?: number;
  keyPosted?: boolean;
  keyError?: string | null;
}

export interface ErrorMessage {
  type: "error";
  message: string;
}

export type ServerMessage = TelemetryMessage | JumpMessage | ErrorMessage;

export interface TelemetryPatch
  extends Partial<
    Omit<
      TelemetryState,
      "calibration" | "quartz" | "recording" | "model" | "lastPacketAt" | "lastJumpAt"
    >
  > {
  calibration?: Partial<CalibrationState>;
  quartz?: Partial<QuartzState>;
  recording?: Partial<RecordingState>;
  model?: Partial<ModelState>;
}

export type ParsedServerEvent =
  | {
      kind: "telemetry";
      patch: TelemetryPatch;
      raw: number[];
      filtered: number[];
      rmsSeries: number[];
    }
  | {
      kind: "jump";
      jumpCount: number | null;
      rms: number | null;
      threshold: number | null;
      keyCallMs: number | null;
      keyPosted: boolean | null;
      keyError: string | null;
    }
  | { kind: "error"; message: string };
