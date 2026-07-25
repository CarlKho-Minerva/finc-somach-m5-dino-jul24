import type { ParsedServerEvent, TelemetryPatch } from "./types";

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function first(record: JsonRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null) return record[key];
  }
  return undefined;
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function bool(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value !== 0;
  if (value === 1 || value === "1" || value === "true") return true;
  if (value === 0 || value === "0" || value === "false") return false;
  return null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function numberArray(value: unknown): number[] {
  if (Array.isArray(value)) {
    return value
      .map(finiteNumber)
      .filter((entry): entry is number => entry !== null);
  }
  const scalar = finiteNumber(value);
  return scalar === null ? [] : [scalar];
}

function leadOffValue(value: unknown): boolean | null {
  const direct = bool(value);
  if (direct !== null) return direct;
  if (Array.isArray(value)) return value.some((entry) => bool(entry) === true);
  if (isRecord(value)) {
    return Object.values(value).some((entry) => bool(entry) === true);
  }
  return null;
}

function withNumber(
  patch: TelemetryPatch,
  key: keyof TelemetryPatch,
  value: unknown,
): void {
  const parsed = finiteNumber(value);
  if (parsed !== null) Object.assign(patch, { [key]: parsed });
}

function withBoolean(
  patch: TelemetryPatch,
  key: keyof TelemetryPatch,
  value: unknown,
): void {
  const parsed = bool(value);
  if (parsed !== null) Object.assign(patch, { [key]: parsed });
}

function telemetryEvent(record: JsonRecord): ParsedServerEvent {
  const nested = isRecord(record.data) ? record.data : {};
  const payload: JsonRecord = { ...record, ...nested };
  const patch: TelemetryPatch = {};

  withNumber(patch, "seq", first(payload, "seq", "sequence"));
  withNumber(patch, "timestamp", first(payload, "timestamp", "ts", "time"));
  withNumber(patch, "threshold", first(payload, "threshold", "triggerThreshold"));
  withNumber(patch, "jumpCount", first(payload, "jumpCount", "jump_count", "jumps"));
  withNumber(patch, "sampleRate", first(payload, "sampleRate", "sample_rate", "fs"));
  withNumber(patch, "refractoryMs", first(payload, "refractoryMs", "refractory_ms"));
  withNumber(
    patch,
    "latencyMs",
    first(payload, "latencyMs", "latency_ms", "pipelineMs", "processingMs"),
  );
  withBoolean(patch, "armed", first(payload, "armed", "isArmed"));
  withBoolean(patch, "clipping", first(payload, "clipping", "isClipping"));

  const source = text(first(payload, "source", "mode", "inputSource"));
  const device = text(first(payload, "device", "port", "serialPort"));
  if (source !== null) patch.source = source;
  if (device !== null) patch.device = device;

  const leadsOff = leadOffValue(
    first(payload, "leadsOff", "leads_off", "leadOff", "lead_off"),
  );
  if (leadsOff !== null) patch.leadsOff = leadsOff;

  const calibrationValue = first(payload, "calibration", "calibrate");
  const calibration = isRecord(calibrationValue) ? calibrationValue : {};
  const calibrationPatch: NonNullable<TelemetryPatch["calibration"]> = {};
  const calibrationActive = bool(
    first(calibration, "active", "calibrating") ??
      first(payload, "calibrating", "calibrationActive"),
  );
  const calibrationProgress = finiteNumber(
    first(calibration, "progress", "fraction") ?? first(payload, "calibrationProgress"),
  );
  const calibrationRemaining = finiteNumber(
    first(calibration, "remaining", "remainingSeconds") ??
      first(payload, "calibrationRemaining"),
  );
  if (calibrationActive !== null) calibrationPatch.active = calibrationActive;
  if (calibrationProgress !== null) {
    calibrationPatch.progress = Math.min(1, Math.max(0, calibrationProgress));
  }
  if (calibrationRemaining !== null) {
    calibrationPatch.remaining = Math.max(0, calibrationRemaining);
  }
  if (Object.keys(calibrationPatch).length) patch.calibration = calibrationPatch;

  const quartzValue = first(payload, "quartz", "accessibility");
  const quartz = isRecord(quartzValue) ? quartzValue : {};
  const quartzPatch: NonNullable<TelemetryPatch["quartz"]> = {};
  const quartzAvailable = bool(
    first(quartz, "available") ?? first(payload, "quartzAvailable"),
  );
  const quartzTrusted = bool(
    first(quartz, "trusted", "authorized") ?? first(payload, "quartzTrusted"),
  );
  const lastCallMs = finiteNumber(
    first(quartz, "lastCallMs", "last_call_ms") ?? first(payload, "keyCallMs"),
  );
  if (quartzAvailable !== null) quartzPatch.available = quartzAvailable;
  if (quartzTrusted !== null) quartzPatch.trusted = quartzTrusted;
  if (lastCallMs !== null) quartzPatch.lastCallMs = lastCallMs;
  if (Object.keys(quartzPatch).length) patch.quartz = quartzPatch;

  const recordingValue = first(payload, "recording", "recorder");
  const recording = isRecord(recordingValue) ? recordingValue : {};
  const recordingPatch: NonNullable<TelemetryPatch["recording"]> = {};
  const recordingActive = bool(
    first(recording, "active", "recording") ?? first(payload, "recordingActive"),
  );
  const recordingSeconds = finiteNumber(
    first(recording, "seconds", "elapsed", "elapsedSeconds", "durationSeconds") ??
      first(payload, "recordingSeconds"),
  );
  const recordingSamples = finiteNumber(
    first(recording, "samples", "sampleCount") ?? first(payload, "recordingSamples"),
  );
  const markersValue =
    first(recording, "markers", "markerCount") ?? first(payload, "recordingMarkers");
  const recordingMarkers = Array.isArray(markersValue)
    ? markersValue.length
    : finiteNumber(markersValue);
  const recordingPath = text(
    first(recording, "path", "file", "filePath", "lastDataset", "dataset"),
  );
  if (recordingActive !== null) recordingPatch.active = recordingActive;
  if (recordingSeconds !== null) recordingPatch.seconds = Math.max(0, recordingSeconds);
  if (recordingSamples !== null) recordingPatch.samples = Math.max(0, recordingSamples);
  if (recordingMarkers !== null) recordingPatch.markers = Math.max(0, recordingMarkers);
  if (recordingPath !== null) recordingPatch.path = recordingPath;
  if (Object.keys(recordingPatch).length) patch.recording = recordingPatch;

  const modelValue = first(payload, "model", "classifier");
  const model = isRecord(modelValue) ? modelValue : {};
  const metricsValue = first(model, "metrics", "validationMetrics");
  const metrics = isRecord(metricsValue) ? metricsValue : {};
  const modelPatch: NonNullable<TelemetryPatch["model"]> = {};
  const modelAvailable = bool(
    first(model, "available", "trained", "valid") ?? first(payload, "modelAvailable"),
  );
  const modelActive = bool(
    first(model, "active", "enabled") ?? first(payload, "modelActive"),
  );
  const modelAccuracy = finiteNumber(first(model, "accuracy") ?? first(metrics, "accuracy"));
  const balancedAccuracy = finiteNumber(
    first(model, "balancedAccuracy", "balanced_accuracy", "balancedAcc") ??
      first(metrics, "balancedAccuracy", "balanced_accuracy", "balancedAcc"),
  );
  const modelScore = finiteNumber(
    first(model, "score", "probability", "f1", "f1Score") ??
      first(metrics, "score", "f1", "f1Score"),
  );
  const modelThreshold = finiteNumber(first(model, "threshold", "decisionThreshold"));
  if (modelAvailable !== null) modelPatch.available = modelAvailable;
  if (modelActive !== null) modelPatch.active = modelActive;
  if (modelAccuracy !== null) modelPatch.accuracy = modelAccuracy;
  if (balancedAccuracy !== null) modelPatch.balancedAccuracy = balancedAccuracy;
  if (modelScore !== null) modelPatch.score = modelScore;
  if (modelThreshold !== null) modelPatch.threshold = modelThreshold;
  if ("error" in model) modelPatch.error = text(model.error);
  if (Object.keys(modelPatch).length) patch.model = modelPatch;

  const raw = numberArray(first(payload, "raw", "samples", "adc"));
  const filtered = numberArray(first(payload, "filtered", "filteredSamples", "signal"));
  const rmsSeries = numberArray(first(payload, "rms", "envelope", "rmsEnvelope"));
  if (rmsSeries.length) patch.rms = rmsSeries[rmsSeries.length - 1];

  return { kind: "telemetry", patch, raw, filtered, rmsSeries };
}

export function parseServerMessage(value: unknown): ParsedServerEvent | null {
  let decoded = value;
  if (typeof value === "string") {
    try {
      decoded = JSON.parse(value) as unknown;
    } catch {
      return { kind: "error", message: "Backend sent malformed JSON" };
    }
  }
  if (!isRecord(decoded)) return null;

  const type = text(decoded.type)?.toLowerCase() ?? "snapshot";
  if (type === "error") {
    return {
      kind: "error",
      message: text(first(decoded, "message", "detail", "error")) ?? "Backend error",
    };
  }
  if (type === "jump" || type === "trigger") {
    return {
      kind: "jump",
      jumpCount: finiteNumber(first(decoded, "jumpCount", "jump_count", "jumps")),
      rms: finiteNumber(decoded.rms),
      threshold: finiteNumber(decoded.threshold),
      keyCallMs: finiteNumber(first(decoded, "keyCallMs", "key_call_ms", "latencyMs")),
      keyPosted: bool(first(decoded, "keyPosted", "key_posted", "posted")),
      keyError: text(first(decoded, "keyError", "key_error", "reason")),
    };
  }

  return telemetryEvent(decoded);
}
