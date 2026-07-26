import { useEffect, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  MutableRefObject,
  PointerEvent as ReactPointerEvent,
} from "react";
import type { SignalBuffers } from "../types";

interface OscilloscopeProps {
  signals: MutableRefObject<SignalBuffers>;
  threshold: number;
  rms: number;
  sampleRate: number;
  armed: boolean;
  onThresholdPreview: (value: number) => void;
  onThresholdCommit: (value: number) => void;
}

interface PlotMetrics {
  top: number;
  bottom: number;
  scaleMax: number;
  thresholdY: number;
}

const initialMetrics: PlotMetrics = {
  top: 20,
  bottom: 320,
  scaleMax: 200,
  thresholdY: 170,
};

function trace(
  context: CanvasRenderingContext2D,
  values: number[],
  left: number,
  right: number,
  mapY: (value: number) => number,
): void {
  if (values.length < 2) return;
  const span = Math.max(1, values.length - 1);
  context.beginPath();
  for (let index = 0; index < values.length; index += 1) {
    const x = left + (index / span) * (right - left);
    const y = mapY(values[index]);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  }
  context.stroke();
}

function formatThreshold(value: number): string {
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

export function Oscilloscope({
  signals,
  threshold,
  rms,
  sampleRate,
  armed,
  onThresholdPreview,
  onThresholdCommit,
}: OscilloscopeProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const metricsRef = useRef<PlotMetrics>(initialMetrics);
  const latestValueRef = useRef(threshold);
  const propsRef = useRef({ threshold, rms, sampleRate, armed });
  const draggingRef = useRef(false);
  const [dragging, setDragging] = useState(false);

  propsRef.current = { threshold, rms, sampleRate, armed };
  latestValueRef.current = threshold;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) return;

    let animationFrame = 0;
    let width = 0;
    let height = 0;
    let envelopeScale = 0;

    const resize = () => {
      const bounds = canvas.getBoundingClientRect();
      const nextWidth = Math.max(320, bounds.width);
      const nextHeight = Math.max(300, bounds.height);
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      if (
        canvas.width !== Math.round(nextWidth * ratio) ||
        canvas.height !== Math.round(nextHeight * ratio)
      ) {
        canvas.width = Math.round(nextWidth * ratio);
        canvas.height = Math.round(nextHeight * ratio);
      }
      width = nextWidth;
      height = nextHeight;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };

    const draw = () => {
      resize();
      const { threshold: liveThreshold, sampleRate: liveRate } = propsRef.current;
      const left = 18;
      const right = width - 18;
      const top = 20;
      const bottom = height - 34;
      const usableHeight = Math.max(100, bottom - top);
      const envelopeBottom = top + usableHeight * 0.76;
      const envelopeHeight = Math.max(80, envelopeBottom - top);
      const signalTop = envelopeBottom + 16;
      const signalBottom = bottom;
      const signalHeight = Math.max(24, signalBottom - signalTop);
      const signalCenter = signalTop + signalHeight * 0.5;

      context.fillStyle = "#faf9f6";
      context.fillRect(0, 0, width, height);

      const gridSize = 20;
      context.lineWidth = 1;
      context.strokeStyle = "#e8e8e8";
      context.beginPath();
      for (let x = 0.5; x <= width; x += gridSize) {
        context.moveTo(x, 0);
        context.lineTo(x, height);
      }
      for (let y = 0.5; y <= height; y += gridSize) {
        context.moveTo(0, y);
        context.lineTo(width, y);
      }
      context.stroke();

      context.strokeStyle = "rgba(0, 0, 0, .14)";
      context.beginPath();
      for (let x = gridSize * 5; x < width; x += gridSize * 5) {
        for (let y = gridSize * 5; y < height; y += gridSize * 5) {
          context.moveTo(x - 3, y + 0.5);
          context.lineTo(x + 3, y + 0.5);
          context.moveTo(x + 0.5, y - 3);
          context.lineTo(x + 0.5, y + 3);
        }
      }
      context.stroke();

      const visibleSamples = Math.max(500, Math.min(4_000, Math.round((liveRate || 1_000) * 2.5)));
      const raw = signals.current.raw.slice(-visibleSamples);
      const envelope = signals.current.rms.slice(-visibleSamples);

      let rawMean = 0;
      for (const sample of raw) rawMean += sample;
      rawMean = raw.length ? rawMean / raw.length : 0;

      let signalRange = 8;
      for (const sample of raw) signalRange = Math.max(signalRange, Math.abs(sample - rawMean));

      const signalAmplitude = signalHeight * 0.42;
      const signalY = (value: number, baseline = 0) =>
        signalCenter - ((value - baseline) / signalRange) * signalAmplitude;

      context.save();
      context.beginPath();
      context.rect(left, signalTop, right - left, signalHeight);
      context.clip();

      context.strokeStyle = "#e5e5e5";
      context.lineWidth = 1;
      context.beginPath();
      const tickStep = Math.max(1, Math.ceil(raw.length / 260));
      for (let index = 0; index < raw.length; index += tickStep) {
        const x = left + (index / Math.max(1, raw.length - 1)) * (right - left);
        context.moveTo(x, signalCenter);
        context.lineTo(x, signalY(raw[index], rawMean));
      }
      context.stroke();
      context.restore();

      let envelopeMax = 0;
      for (const value of envelope) envelopeMax = Math.max(envelopeMax, value);
      const desiredScale = Math.max(1, liveThreshold * 1.55, envelopeMax * 1.15);
      if (envelopeScale === 0) envelopeScale = desiredScale;
      if (!draggingRef.current) {
        const response = desiredScale > envelopeScale ? 0.18 : 0.018;
        envelopeScale += (desiredScale - envelopeScale) * response;
      }
      const scaleMax = Math.max(1, envelopeScale);
      const envelopeY = (value: number) =>
        envelopeBottom - (value / scaleMax) * envelopeHeight;

      context.save();
      context.beginPath();
      context.rect(left, top, right - left, envelopeHeight);
      context.clip();

      if (envelope.length > 1) {
        context.strokeStyle = "#ff2b00";
        context.lineWidth = 2.5;
        trace(context, envelope, left, right, envelopeY);
      }

      const thresholdY = envelopeY(liveThreshold);
      metricsRef.current = { top, bottom: envelopeBottom, scaleMax, thresholdY };
      context.setLineDash([]);
      context.strokeStyle = "#000000";
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(left, thresholdY);
      context.lineTo(right, thresholdY);
      context.stroke();
      context.setLineDash([]);
      context.restore();

      context.strokeStyle = "#e5e5e5";
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(left, envelopeBottom + 8);
      context.lineTo(right, envelopeBottom + 8);
      context.stroke();

      const thresholdLabel = `[ THRESHOLD: ${formatThreshold(liveThreshold)} ]`;
      context.font = "700 10px ui-monospace, SFMono-Regular, Menlo, monospace";
      const labelWidth = context.measureText(thresholdLabel).width + 16;
      const labelX = right - labelWidth;
      const labelY = Math.max(top + 1, Math.min(envelopeBottom - 24, thresholdY - 11));
      context.fillStyle = "#000000";
      context.fillRect(labelX, labelY, labelWidth, 22);
      context.fillStyle = "#ffffff";
      context.fillText(thresholdLabel, labelX + 8, labelY + 15);

      context.fillStyle = "rgba(115, 115, 115, .72)";
      context.font = "500 10px ui-monospace, SFMono-Regular, Menlo, monospace";
      context.fillText("[ RAW REFERENCE ]", left, signalTop + 10);
      context.fillText("[ BUFFER: 2.5S ]", left, height - 12);
      const rateText = `[ RATE: ${Math.round(liveRate || 1_000)}HZ ]`;
      context.fillText(rateText, right - context.measureText(rateText).width, height - 12);

      if (!raw.length && !envelope.length) {
        context.fillStyle = "rgba(115, 115, 115, .62)";
        context.font = "600 12px ui-monospace, SFMono-Regular, Menlo, monospace";
        context.textAlign = "center";
        context.fillText("WAITING FOR SIGNAL", width / 2, top + envelopeHeight * 0.55);
        context.textAlign = "start";
      }

      animationFrame = window.requestAnimationFrame(draw);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    draw();
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(animationFrame);
    };
  }, [signals]);

  const valueFromPointer = (event: ReactPointerEvent<HTMLCanvasElement>): number => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const y = event.clientY - bounds.top;
    const metrics = metricsRef.current;
    const fraction = (metrics.bottom - y) / Math.max(1, metrics.bottom - metrics.top);
    return Math.max(0, Math.min(metrics.scaleMax * 1.08, fraction * metrics.scaleMax));
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    draggingRef.current = true;
    setDragging(true);
    const value = valueFromPointer(event);
    latestValueRef.current = value;
    onThresholdPreview(value);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!dragging) return;
    const value = valueFromPointer(event);
    latestValueRef.current = value;
    onThresholdPreview(value);
  };

  const finishPointer = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!dragging) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    draggingRef.current = false;
    setDragging(false);
    onThresholdCommit(latestValueRef.current);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLCanvasElement>) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const direction = event.key === "ArrowUp" ? 1 : -1;
    const step = Math.max(0.25, threshold * 0.025) * (event.shiftKey ? 5 : 1);
    const value = Math.max(0, threshold + direction * step);
    onThresholdPreview(value);
    onThresholdCommit(value);
  };

  return (
    <div className={`oscilloscope-frame${dragging ? " is-dragging" : ""}`}>
      <canvas
        ref={canvasRef}
        className="oscilloscope"
        role="slider"
        tabIndex={0}
        aria-label="Live sEMG waveform. Drag vertically to adjust the trigger threshold."
        aria-valuemin={0}
        aria-valuemax={Math.ceil(Math.max(threshold * 1.65, rms * 1.2, 1))}
        aria-valuenow={Number(threshold.toFixed(2))}
        aria-valuetext={`Trigger threshold ${formatThreshold(threshold)} RMS`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishPointer}
        onPointerCancel={finishPointer}
        onKeyDown={handleKeyDown}
      />
      <span className="threshold-grip" style={{ top: metricsRef.current.thresholdY }} aria-hidden="true">
        <span />
      </span>
    </div>
  );
}
