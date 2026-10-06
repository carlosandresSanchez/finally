import type { PricePoint } from "@/lib/types";

interface Props {
  points: PricePoint[];
  width?: number;
  height?: number;
}

/** Tiny SVG line; green if the series is up since page load, red if down. */
export function Sparkline({ points, width = 80, height = 24 }: Props) {
  if (points.length < 2) {
    return <svg width={width} height={height} aria-hidden data-testid="sparkline" />;
  }
  const prices = points.map((p) => p.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const step = width / (points.length - 1);
  const d = prices
    .map((p, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(height - 2 - ((p - min) / range) * (height - 4)).toFixed(1)}`)
    .join(" ");
  const up = prices[prices.length - 1] >= prices[0];
  return (
    <svg width={width} height={height} aria-hidden data-testid="sparkline">
      <path d={d} fill="none" stroke={up ? "var(--color-up)" : "var(--color-down)"} strokeWidth={1.25} />
    </svg>
  );
}
