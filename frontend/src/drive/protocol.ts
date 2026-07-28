import type {
  DriveAction,
  DriveChannelId,
  DriveChannelState,
  DriveParsedTelemetry,
} from "./types";

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function first(source: JsonRecord, keys: readonly string[]): unknown {
  for (const key of keys) {
    const value = source[key];
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
}

function number(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function boolean(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (value === 1 || value === "1" || value === "true") return true;
  if (value === 0 || value === "0" || value === "false") return false;
  return null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function numbers(value: unknown): number[] {
  if (Array.isArray(value)) {
    return value.map(number).filter((entry): entry is number => entry !== null);
  }
  const scalar = number(value);
  return scalar === null ? [] : [scalar];
}

function action(value: unknown): DriveAction | null {
  const normalized = text(value)?.toLowerCase();
  if (!normalized || normalized === "none" || normalized === "rest" || normalized === "stop") {
    return normalized ? "idle" : null;
  }
  if (normalized === "up" || normalized === "forward" || normalized === "ahead") {
    return "forward";
  }
  if (normalized === "left" || normalized === "turn_left") return "left";
  if (normalized === "right" || normalized === "turn_right") return "right";
  return null;
}

const channelAliases: Record<DriveChannelId, readonly string[]> = {
  a: ["a", "A", "channelA", "channel_a", "chA", "ch1", "mylohyoid"],
  b: ["b", "B", "channelB", "channel_b", "chB", "ch2", "masseter"],
};

function nestedChannel(payload: JsonRecord, id: DriveChannelId): JsonRecord {
  const channels = record(first(payload, ["channels", "channel", "signals"])) ?? {};
  for (const alias of channelAliases[id]) {
    const nested = record(channels[alias]) ?? record(payload[alias]);
    if (nested) return nested;
  }
  return {};
}

function flat(payload: JsonRecord, id: DriveChannelId, field: string): unknown {
  const suffixes = id === "a" ? ["A", "_a", "1"] : ["B", "_b", "2"];
  return first(payload, suffixes.map((suffix) => `${field}${suffix}`));
}

function channelPatch(payload: JsonRecord, id: DriveChannelId): Partial<DriveChannelState> {
  const nested = nestedChannel(payload, id);
  const patch: Partial<DriveChannelState> = {};
  const rms = number(first(nested, ["rms", "value", "envelope", "latestRms"]) ?? flat(payload, id, "rms"));
  const threshold = number(
    first(nested, ["threshold", "triggerThreshold", "thresholdRms"]) ?? flat(payload, id, "threshold"),
  );
  const leadOff = boolean(
    first(nested, ["leadOff", "leadsOff", "lead_off", "leads_off"]) ??
      flat(payload, id, "leadOff") ??
      flat(payload, id, "leadsOff"),
  );
  const clipping = boolean(
    first(nested, ["clipping", "clipped", "isClipping"]) ?? flat(payload, id, "clipping"),
  );
  if (rms !== null) patch.rms = rms;
  if (threshold !== null) patch.threshold = threshold;
  if (leadOff !== null) patch.leadOff = leadOff;
  if (clipping !== null) patch.clipping = clipping;
  return patch;
}

function channelSeries(payload: JsonRecord, id: DriveChannelId): number[] {
  const nested = nestedChannel(payload, id);
  const nestedSeries = first(nested, ["rmsSeries", "rms_series", "envelope", "samples", "rms"]);
  const flatSeries =
    flat(payload, id, "rmsSeries") ?? flat(payload, id, "rms_series") ?? flat(payload, id, "rms");
  const values = numbers(nestedSeries ?? flatSeries);
  if (values.length) return values;

  const rms = first(payload, ["rms", "envelope"]);
  if (Array.isArray(rms) && rms.length === 2 && rms.every((entry) => number(entry) !== null)) {
    const value = number(rms[id === "a" ? 0 : 1]);
    return value === null ? [] : [value];
  }
  const rmsRecord = record(rms);
  if (rmsRecord) {
    for (const alias of channelAliases[id]) {
      const valuesForAlias = numbers(rmsRecord[alias]);
      if (valuesForAlias.length) return valuesForAlias;
    }
  }
  return [];
}

/**
 * Parse both the documented dual-drive schema and conservative aliases. The
 * preferred wire format is:
 * `{type:"telemetry", channels:{a:{rms,threshold,rmsSeries,leadOff,clipping},
 * b:{...}}, armed, action, counts, calibration}`.
 */
export function parseDriveMessage(input: unknown): DriveParsedTelemetry | null {
  let decoded = input;
  if (typeof input === "string") {
    try {
      decoded = JSON.parse(input) as unknown;
    } catch {
      return null;
    }
  }
  const outer = record(decoded);
  if (!outer) return null;
  const data = record(outer.data) ?? record(outer.telemetry) ?? {};
  const payload = { ...outer, ...data };
  const patch: DriveParsedTelemetry["patch"] = {};

  const sequence = number(first(payload, ["sequence", "seq"]));
  const timestamp = number(first(payload, ["timestamp", "ts", "time"]));
  const sampleRate = number(first(payload, ["sampleRate", "sample_rate", "rateHz", "fs"]));
  const signalConnected = boolean(first(payload, ["connected", "signalConnected", "sourceConnected"]));
  const armed = boolean(first(payload, ["armed", "isArmed", "enabled"]));
  const calibrated = boolean(first(payload, ["calibrated", "isCalibrated"]));
  const source = text(first(payload, ["source", "mode", "inputSource"]));
  const device = text(first(payload, ["device", "port", "serialPort"]));
  const detected = action(first(payload, ["action", "lastAction", "detectedAction", "command"]));
  const arbitration = record(first(payload, ["arbitration", "arbiter"])) ?? {};
  const arbitrationState = text(
    first(payload, ["arbitrationState", "arbitration_state", "detectorState", "gateState", "state"]) ??
      first(arbitration, ["state", "gateState"]),
  );
  const waitingForRelease = boolean(
    first(payload, ["waitingForRelease", "waiting_for_release", "latched", "releaseRequired"]) ??
      first(arbitration, ["waitingRelease", "waitingForRelease", "latched"]),
  );
  const canArm = boolean(first(payload, ["canArm", "can_arm"]) ?? first(arbitration, ["canArm", "readyToArm"]));
  const qualityError = text(first(payload, ["qualityError", "quality_error", "signalError"]));
  const backendError = text(first(payload, ["error", "backendError"]));
  const keyPosted = boolean(first(payload, ["keyPosted", "key_posted", "posted"]));
  const keyError = text(first(payload, ["keyError", "key_error", "keypressError", "postError"]));
  const coincidenceMs = number(
    first(payload, ["coincidenceMs", "coincidence_ms", "bothWindowMs"]) ??
      first(arbitration, ["coincidenceMs", "coincidence_ms"]),
  );
  const forwardPulseMs = number(first(payload, ["forwardPulseMs", "forward_pulse_ms", "upPulseMs"]));
  const turnPulseMs = number(first(payload, ["turnPulseMs", "turn_pulse_ms", "steerPulseMs"]));
  if (sequence !== null) patch.sequence = sequence;
  if (timestamp !== null) patch.timestamp = timestamp;
  if (sampleRate !== null) patch.sampleRate = sampleRate;
  if (signalConnected !== null) patch.signalConnected = signalConnected;
  if (armed !== null) patch.armed = armed;
  if (calibrated !== null) patch.calibrated = calibrated;
  if (canArm !== null) patch.canArm = canArm;
  if ("qualityError" in payload || "quality_error" in payload || "signalError" in payload) {
    patch.qualityError = qualityError;
  }
  if ("error" in payload || "backendError" in payload) patch.backendError = backendError;
  if (source !== null) patch.source = source;
  if (device !== null) patch.device = device;
  if (detected !== null) patch.action = detected;
  if (arbitrationState !== null) patch.arbitrationState = arbitrationState;
  if (waitingForRelease !== null) patch.waitingForRelease = waitingForRelease;
  if (keyPosted !== null) {
    patch.lastKeyPosted = keyPosted;
    if (keyPosted) patch.lastKeyError = null;
  }
  if (keyError !== null) patch.lastKeyError = keyError;
  if (coincidenceMs !== null) patch.coincidenceMs = coincidenceMs;
  if (forwardPulseMs !== null) patch.forwardPulseMs = forwardPulseMs;
  if (turnPulseMs !== null) patch.turnPulseMs = turnPulseMs;

  const channels: DriveParsedTelemetry["patch"]["channels"] = {
    a: channelPatch(payload, "a"),
    b: channelPatch(payload, "b"),
  };
  if (Object.keys(channels.a ?? {}).length || Object.keys(channels.b ?? {}).length) {
    patch.channels = channels;
  }

  const countSource = record(first(payload, ["counts", "actionCounts", "counters"])) ?? {};
  const counts: NonNullable<DriveParsedTelemetry["patch"]["counts"]> = {};
  const forwardCount = number(first(countSource, ["forward", "up"]) ?? first(payload, ["forwardCount", "forward_count", "upCount"]));
  const leftCount = number(first(countSource, ["left"]) ?? first(payload, ["leftCount", "left_count"]));
  const rightCount = number(first(countSource, ["right"]) ?? first(payload, ["rightCount", "right_count"]));
  if (forwardCount !== null) counts.forward = forwardCount;
  if (leftCount !== null) counts.left = leftCount;
  if (rightCount !== null) counts.right = rightCount;
  if (Object.keys(counts).length) patch.counts = counts;

  const calibrationSource = record(first(payload, ["calibration", "calibrate"])) ?? {};
  const calibration: NonNullable<DriveParsedTelemetry["patch"]["calibration"]> = {};
  const calibrationActive = boolean(
    first(calibrationSource, ["active", "calibrating"]) ?? first(payload, ["calibrating", "calibrationActive"]),
  );
  const calibrationProgress = number(
    first(calibrationSource, ["progress", "fraction"]) ?? first(payload, ["calibrationProgress"]),
  );
  const calibrationRemaining = number(
    first(calibrationSource, ["remaining", "remainingSeconds"]) ?? first(payload, ["calibrationRemaining"]),
  );
  if (calibrationActive !== null) calibration.active = calibrationActive;
  if (calibrationProgress !== null) calibration.progress = Math.min(1, Math.max(0, calibrationProgress));
  if (calibrationRemaining !== null) calibration.remaining = Math.max(0, calibrationRemaining);
  if (Object.keys(calibration).length) patch.calibration = calibration;

  const eventType = text(outer.type)?.toLowerCase();
  const eventAction = eventType === "action" || eventType === "trigger" ? detected : null;
  const errorMessage =
    eventType === "command-error" || eventType === "error"
      ? text(first(outer, ["message", "detail", "error"])) ?? "Dual-drive backend error"
      : null;
  if (eventType === "error") {
    patch.signalConnected = false;
    patch.armed = false;
    patch.action = "idle";
    patch.backendError = errorMessage;
  }
  return {
    patch,
    series: { a: channelSeries(payload, "a"), b: channelSeries(payload, "b") },
    eventAction,
    errorMessage,
  };
}
