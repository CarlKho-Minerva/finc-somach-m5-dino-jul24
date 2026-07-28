import { useEffect, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  MutableRefObject,
  PointerEvent as ReactPointerEvent,
} from "react";
import type { DriveChannelId, DriveSignals } from "./types";

interface DriveTraceProps {
  channel: DriveChannelId;
  signals: MutableRefObject<DriveSignals>;
  rms: number;
  threshold: number;
  leadOff: boolean;
  clipping: boolean;
  connected: boolean;
  simulated?: boolean;
  editable?: boolean;
  onPreview: (value: number) => void;
  onCommit: (value: number) => void;
}

interface PlotMetrics {
  top: number;
  bottom: number;
  scaleMax: number;
}

const DEFAULT_METRICS: PlotMetrics = { top: 20, bottom: 180, scaleMax: 100 };

function label(value: number): string {
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

export function DriveTrace({
  channel,
  signals,
  rms,
  threshold,
  leadOff,
  clipping,
  connected,
  simulated = false,
  editable = true,
  onPreview,
  onCommit,
}: DriveTraceProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const liveRef = useRef({ rms, threshold, leadOff, clipping, connected });
  const metricsRef = useRef<PlotMetrics>(DEFAULT_METRICS);
  const draggingRef = useRef(false);
  const commitValueRef = useRef(threshold);
  const [dragging, setDragging] = useState(false);

  const visibleThreshold = draggingRef.current ? commitValueRef.current : threshold;
  liveRef.current = { rms, threshold: visibleThreshold, leadOff, clipping, connected };
  if (!draggingRef.current) commitValueRef.current = threshold;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) return;
    let animationFrame = 0;
    let width = 0;
    let height = 0;
    let scale = 0;

    const resize = () => {
      const bounds = canvas.getBoundingClientRect();
      width = Math.max(280, bounds.width);
      height = Math.max(170, bounds.height);
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const pixelWidth = Math.round(width * ratio);
      const pixelHeight = Math.round(height * ratio);
      if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };

    const draw = () => {
      resize();
      const state = liveRef.current;
      const left = 14;
      const right = width - 14;
      const top = 18;
      const bottom = height - 24;
      context.fillStyle = "#faf9f6";
      context.fillRect(0, 0, width, height);

      context.beginPath();
      context.strokeStyle = "#e3e2de";
      context.lineWidth = 1;
      for (let x = 0.5; x < width; x += 16) {
        context.moveTo(x, 0);
        context.lineTo(x, height);
      }
      for (let y = 0.5; y < height; y += 16) {
        context.moveTo(0, y);
        context.lineTo(width, y);
      }
      context.stroke();

      context.beginPath();
      context.strokeStyle = "rgba(0,0,0,.16)";
      for (let x = 80; x < width; x += 80) {
        for (let y = 80; y < height; y += 80) {
          context.moveTo(x - 3, y + 0.5);
          context.lineTo(x + 3, y + 0.5);
          context.moveTo(x + 0.5, y - 3);
          context.lineTo(x + 0.5, y + 3);
        }
      }
      context.stroke();

      const values = signals.current[channel].slice(-300);
      let observedMax = state.rms;
      for (const value of values) observedMax = Math.max(observedMax, value);
      const desiredScale = Math.max(1, state.threshold * 1.55, observedMax * 1.16);
      if (!scale) scale = desiredScale;
      if (!draggingRef.current) {
        const response = desiredScale > scale ? 0.16 : 0.018;
        scale += (desiredScale - scale) * response;
      }
      const scaleMax = Math.max(1, scale);
      const y = (value: number) => bottom - (value / scaleMax) * (bottom - top);
      metricsRef.current = { top, bottom, scaleMax };

      context.save();
      context.beginPath();
      context.rect(left, top, right - left, bottom - top);
      context.clip();
      if (values.length > 1) {
        context.beginPath();
        values.forEach((value, index) => {
          const x = left + (index / Math.max(1, values.length - 1)) * (right - left);
          if (index === 0) context.moveTo(x, y(value));
          else context.lineTo(x, y(value));
        });
        context.lineWidth = channel === "a" ? 2.5 : 2;
        context.strokeStyle = channel === "a" ? "#ff2b00" : "#000000";
        context.stroke();
      }

      const thresholdY = y(state.threshold);
      context.beginPath();
      context.setLineDash([6, 4]);
      context.strokeStyle = "#000000";
      context.lineWidth = 1;
      context.moveTo(left, thresholdY);
      context.lineTo(right, thresholdY);
      context.stroke();
      context.setLineDash([]);
      context.restore();

      const thresholdText = `[ T${channel.toUpperCase()}: ${label(state.threshold)} ]`;
      context.font = "700 10px ui-monospace, SFMono-Regular, Menlo, monospace";
      const tagWidth = context.measureText(thresholdText).width + 14;
      const tagY = Math.max(top, Math.min(bottom - 20, thresholdY - 10));
      context.fillStyle = "#000000";
      context.fillRect(right - tagWidth, tagY, tagWidth, 20);
      context.fillStyle = "#ffffff";
      context.fillText(thresholdText, right - tagWidth + 7, tagY + 14);

      if (!state.connected || state.leadOff || state.clipping || !values.length) {
        const status = !state.connected
          ? "WAITING FOR BACKEND"
          : state.leadOff
            ? "LEAD OFF — RESEAT PADS"
            : state.clipping
              ? "ADC CLIPPING"
              : "WAITING FOR RMS";
        context.fillStyle = "rgba(0,0,0,.62)";
        context.font = "700 11px ui-monospace, SFMono-Regular, Menlo, monospace";
        context.textAlign = "center";
        context.fillText(status, width / 2, top + (bottom - top) / 2);
        context.textAlign = "start";
      }

      context.fillStyle = "rgba(0,0,0,.54)";
      context.font = "600 9px ui-monospace, SFMono-Regular, Menlo, monospace";
      context.fillText(simulated ? "[ SIMULATED RMS / 150MS ]" : "[ RMS / 150MS ]", left, height - 8);
      const scaleText = `[ MAX: ${label(scaleMax)} ]`;
      context.fillText(scaleText, right - context.measureText(scaleText).width, height - 8);

      animationFrame = window.requestAnimationFrame(draw);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    draw();
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(animationFrame);
    };
  }, [channel, signals, simulated]);

  const valueAtPointer = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const y = event.clientY - bounds.top;
    const metrics = metricsRef.current;
    const fraction = (metrics.bottom - y) / Math.max(1, metrics.bottom - metrics.top);
    return Math.min(4_095, Math.max(0.1, Math.min(metrics.scaleMax * 1.08, fraction * metrics.scaleMax)));
  };

  const startDrag = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (event.button !== 0 || !connected || !editable) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    draggingRef.current = true;
    setDragging(true);
    const value = valueAtPointer(event);
    commitValueRef.current = value;
    liveRef.current.threshold = value;
    onPreview(value);
  };

  const moveDrag = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!draggingRef.current) return;
    const value = valueAtPointer(event);
    commitValueRef.current = value;
    liveRef.current.threshold = value;
    onPreview(value);
  };

  const finishDrag = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!draggingRef.current) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    draggingRef.current = false;
    setDragging(false);
    onCommit(commitValueRef.current);
  };

  const keyAdjust = (event: ReactKeyboardEvent<HTMLCanvasElement>) => {
    if (!connected || !editable || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
    event.preventDefault();
    const direction = event.key === "ArrowUp" ? 1 : -1;
    const step = Math.max(0.5, threshold * 0.05) * (event.shiftKey ? 5 : 1);
    const value = Math.min(4_095, Math.max(0.1, threshold + direction * step));
    onPreview(value);
    onCommit(value);
  };

  return (
    <canvas
      ref={canvasRef}
      className={`drive-trace${dragging ? " is-dragging" : ""}${editable ? "" : " is-readonly"}`}
      role="slider"
      tabIndex={editable ? 0 : -1}
      aria-disabled={!editable}
      aria-label={`${simulated ? "Simulated" : "Live"} channel ${channel.toUpperCase()} RMS.${editable ? " Drag vertically to adjust its threshold." : " Read only."}`}
      aria-valuemin={0.1}
      aria-valuemax={4095}
      aria-valuenow={Number(threshold.toFixed(2))}
      onPointerDown={startDrag}
      onPointerMove={moveDrag}
      onPointerUp={finishDrag}
      onPointerCancel={finishDrag}
      onKeyDown={keyAdjust}
    />
  );
}
