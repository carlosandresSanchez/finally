"use client";

import {
  AreaSeries,
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

export interface SeriesPoint {
  time: number; // unix seconds
  value: number;
}

interface Props {
  data: SeriesPoint[];
  color?: string;
  testId?: string;
}

/** Dedupe to strictly increasing whole seconds (Lightweight Charts requirement); last value wins. */
export function toChartData(points: SeriesPoint[]) {
  const out: { time: UTCTimestamp; value: number }[] = [];
  for (const p of points) {
    const t = Math.floor(p.time) as UTCTimestamp;
    const last = out[out.length - 1];
    if (last && last.time === t) last.value = p.value;
    else if (!last || t > last.time) out.push({ time: t, value: p.value });
  }
  return out;
}

/** Canvas area chart (TradingView Lightweight Charts) themed for the dark terminal. */
export function TimeSeriesChart({ data, color = "#209dd7", testId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8b949e",
        fontFamily: "ui-monospace, Menlo, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(48,54,61,0.4)" },
        horzLines: { color: "rgba(48,54,61,0.4)" },
      },
      rightPriceScale: { borderColor: "#30363d" },
      timeScale: { borderColor: "#30363d", timeVisible: true, secondsVisible: true },
      crosshair: { horzLine: { labelBackgroundColor: "#1c2230" }, vertLine: { labelBackgroundColor: "#1c2230" } },
    });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(AreaSeries, {
      lineColor: color,
      topColor: `${color}55`,
      bottomColor: `${color}05`,
      lineWidth: 2,
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [color]);

  useEffect(() => {
    seriesRef.current?.setData(toChartData(data));
  }, [data]);

  return <div ref={containerRef} className="h-full w-full" data-testid={testId} data-points={data.length} />;
}
