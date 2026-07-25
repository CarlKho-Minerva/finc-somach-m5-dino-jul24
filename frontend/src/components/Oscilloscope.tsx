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
      const { threshold: liveThreshold, sampleRate: liveRate, armed: isArmed } =
        propsRef.current;
      const left = 18;
      const right = width - 18;
      const top = 20;
      const bottom = height - 34;
      const usableHeight = Math.max(100, bottom - top);
      const center = top + usableHeight * 0.52;

      const background = context.createLinearGradient(0, 0, 0, height);
      background.addColorStop(0, "#091116");
      background.addColorStop(1, "#060b0e");
      context.fillStyle = background;
      context.fillRect(0, 0, width, height);

      context.lineWidth = 1;
      for (let line = 0; line <= 10; line += 1) {
        const x = left + ((right - left) * line) / 10;
        context.strokeStyle = line % 5 === 0 ? "rgba(94, 132, 140, .16)" : "rgba(94, 132, 140, .075)";
        context.beginPath();
        context.moveTo(x, top);
        context.lineTo(x, bottom);
        context.stroke();
      }
      for (let line = 0; line <= 6; line += 1) {
        const y = top + (usableHeight * line) / 6;
        context.strokeStyle = line === 3 ? "rgba(94, 132, 140, .18)" : "rgba(94, 132, 140, .075)";
        context.beginPath();
        context.moveTo(left, y);
        context.lineTo(right, y);
        context.stroke();
      }

      const visibleSamples = Math.max(500, Math.min(4_000, Math.round((liveRate || 1_000) * 2.5)));
      const raw = signals.current.raw.slice(-visibleSamples);
      const filtered = signals.current.filtered.slice(-visibleSamples);
      const envelope = signals.current.rms.slice(-visibleSamples);

      let rawMean = 0;
      for (const sample of raw) rawMean += sample;
      rawMean = raw.length ? rawMean / raw.length : 0;

      let signalRange = 8;
      for (const sample of raw) signalRange = Math.max(signalRange, Math.abs(sample - rawMean));
      for (const sample of filtered) signalRange = Math.max(signalRange, Math.abs(sample));

      const signalAmplitude = usableHeight * 0.3;
      const signalY = (value: number, baseline = 0) =>
        center - ((value - baseline) / signalRange) * signalAmplitude;

      context.save();
      context.beginPath();
      context.rect(left, top, right - left, usableHeight);
      context.clip();

      context.strokeStyle = "rgba(84, 178, 230, .32)";
      context.lineWidth = 1;
      trace(context, raw, left, right, (value) => signalY(value, rawMean));

      context.shadowBlur = 8;
      context.shadowColor = "rgba(82, 232, 197, .35)";
      context.strokeStyle = "rgba(82, 232, 197, .92)";
      context.lineWidth = 1.45;
      trace(context, filtered, left, right, (value) => signalY(value));
      context.shadowBlur = 0;

      let envelopeMax = 0;
      for (const value of envelope) envelopeMax = Math.max(envelopeMax, value);
      const desiredScale = Math.max(1, liveThreshold * 1.65, envelopeMax * 1.2);
      if (envelopeScale === 0) envelopeScale = desiredScale;
      if (!draggingRef.current) {
        const response = desiredScale > envelopeScale ? 0.18 : 0.018;
        envelopeScale += (desiredScale - envelopeScale) * response;
      }
      const scaleMax = Math.max(1, envelopeScale);
      const envelopeY = (value: number) => bottom - (value / scaleMax) * usableHeight;

      if (envelope.length > 1) {
        const span = Math.max(1, envelope.length - 1);
        context.beginPath();
        for (let index = 0; index < envelope.length; index += 1) {
          const x = left + (index / span) * (right - left);
          const y = envelopeY(envelope[index]);
          if (index === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        }
        context.lineTo(right, bottom);
        context.lineTo(left, bottom);
        context.closePath();
        const envelopeFill = context.createLinearGradient(0, top, 0, bottom);
        envelopeFill.addColorStop(0, "rgba(169, 117, 255, .24)");
        envelopeFill.addColorStop(1, "rgba(169, 117, 255, .015)");
        context.fillStyle = envelopeFill;
        context.fill();

        context.strokeStyle = "rgba(181, 136, 255, .9)";
        context.lineWidth = 2;
        context.shadowBlur = 7;
        context.shadowColor = "rgba(169, 117, 255, .35)";
        trace(context, envelope, left, right, envelopeY);
        context.shadowBlur = 0;
      }

      const thresholdY = envelopeY(liveThreshold);
      metricsRef.current = { top, bottom, scaleMax, thresholdY };
      context.setLineDash([7, 5]);
      context.strokeStyle = isArmed ? "rgba(255, 195, 92, .95)" : "rgba(144, 151, 153, .72)";
      context.lineWidth = 1.3;
      context.beginPath();
      context.moveTo(left, thresholdY);
      context.lineTo(right, thresholdY);
      context.stroke();
      context.setLineDash([]);
      context.restore();

      const thresholdLabel = `T  ${formatThreshold(liveThreshold)}`;
      context.font = "600 11px ui-monospace, SFMono-Regular, Menlo, monospace";
      const labelWidth = context.measureText(thresholdLabel).width + 16;
      const labelX = right - labelWidth;
      const labelY = Math.max(top + 1, Math.min(bottom - 24, thresholdY - 11));
      context.fillStyle = isArmed ? "rgba(255, 195, 92, .16)" : "rgba(150, 158, 160, .12)";
      context.fillRect(labelX, labelY, labelWidth, 22);
      context.fillStyle = isArmed ? "#ffd078" : "#a1aaac";
      context.fillText(thresholdLabel, labelX + 8, labelY + 15);

      context.fillStyle = "rgba(146, 169, 174, .65)";
      context.font = "500 10px ui-monospace, SFMono-Regular, Menlo, monospace";
      context.fillText(`2.5 s BUFFER`, left, height - 12);
      const rateText = `${Math.round(liveRate || 1_000)} Hz`;
      context.fillText(rateText, right - context.measureText(rateText).width, height - 12);

      if (!raw.length && !filtered.length) {
        context.fillStyle = "rgba(177, 197, 201, .5)";
        context.font = "600 12px ui-monospace, SFMono-Regular, Menlo, monospace";
        context.textAlign = "center";
        context.fillText("WAITING FOR SIGNAL", width / 2, center + 4);
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
