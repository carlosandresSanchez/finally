"use client";

import { useEffect, useRef, useState } from "react";
import { fmtPct } from "@/lib/format";
import { squarify } from "@/lib/treemap";
import type { Position } from "@/lib/types";

/** Green for profit, red for loss; intensity saturates at ±5%. */
export function pnlColor(pct: number): string {
  const t = Math.min(Math.abs(pct) / 5, 1);
  const alpha = 0.25 + 0.6 * t;
  return pct >= 0 ? `rgba(38, 166, 65, ${alpha})` : `rgba(248, 81, 73, ${alpha})`;
}

export function Heatmap({ positions, onSelect }: { positions: Position[]; onSelect: (t: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const rects = squarify(
    positions.map((p) => ({ value: p.market_value, data: p })),
    size.w,
    size.h,
  );

  return (
    <div ref={ref} className="relative h-full w-full" data-testid="heatmap">
      {positions.length === 0 && (
        <div className="flex h-full items-center justify-center text-xs text-muted">No positions yet</div>
      )}
      {rects.map(({ x, y, w, h, data: p }) => (
        <button
          key={p.ticker}
          data-testid={`heatmap-cell-${p.ticker}`}
          data-pnl={p.unrealized_pnl >= 0 ? "profit" : "loss"}
          onClick={() => onSelect(p.ticker)}
          title={`${p.ticker}: ${p.weight.toFixed(1)}% of portfolio, ${fmtPct(p.unrealized_pnl_percent)}`}
          className="absolute overflow-hidden border border-bg p-1 text-left transition-colors hover:brightness-125"
          style={{ left: x, top: y, width: w, height: h, backgroundColor: pnlColor(p.unrealized_pnl_percent) }}
        >
          {w > 40 && h > 24 && (
            <>
              <div className="text-xs font-bold">{p.ticker}</div>
              <div className="text-[10px] tabular-nums">{fmtPct(p.unrealized_pnl_percent)}</div>
            </>
          )}
        </button>
      ))}
    </div>
  );
}
