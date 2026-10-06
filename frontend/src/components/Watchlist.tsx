"use client";

import { useEffect, useRef, useState } from "react";
import { fmtPct, fmtPrice, pnlClass } from "@/lib/format";
import type { PricePoint, PriceUpdate } from "@/lib/types";
import { Sparkline } from "./Sparkline";

interface Props {
  tickers: string[];
  prices: Record<string, PriceUpdate>;
  history: Record<string, PricePoint[]>;
  selected: string | null;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => Promise<void>;
  onRemove: (ticker: string) => Promise<void>;
}

export function Watchlist({ tickers, prices, history, selected, onSelect, onAdd, onRemove }: Props) {
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const ticker = input.trim().toUpperCase();
    if (!ticker) return;
    try {
      await onAdd(ticker);
      setInput("");
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <section className="flex h-full min-h-0 flex-col border-r border-border bg-panel" data-testid="watchlist">
      <div className="flex items-center justify-between px-3 py-2">
        <h2 className="panel-title">Watchlist</h2>
        <span className="text-[10px] text-muted">{tickers.length} symbols</span>
      </div>
      <form onSubmit={submit} className="flex gap-1 px-3 pb-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Add ticker"
          aria-label="Add ticker"
          data-testid="watchlist-input"
          className="min-w-0 flex-1 rounded border border-border bg-bg px-2 py-1 text-xs uppercase outline-none focus:border-primary"
        />
        <button
          type="submit"
          data-testid="watchlist-add"
          className="rounded bg-primary px-2 py-1 text-xs font-semibold text-white hover:opacity-90"
        >
          Add
        </button>
      </form>
      {error && <p className="px-3 pb-2 text-xs text-down">{error}</p>}
      <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-2 border-y border-border px-3 py-1 text-[10px] uppercase text-muted">
        <span>Symbol</span>
        <span className="text-right">Last</span>
        <span className="w-14 text-right">Chg%</span>
        <span className="w-[80px]" />
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto">
        {tickers.map((t) => (
          <WatchlistRow
            key={t}
            ticker={t}
            update={prices[t]}
            points={history[t] ?? []}
            selected={selected === t}
            onSelect={() => onSelect(t)}
            onRemove={() => onRemove(t)}
          />
        ))}
      </ul>
    </section>
  );
}

interface RowProps {
  ticker: string;
  update?: PriceUpdate;
  points: PricePoint[];
  selected: boolean;
  onSelect: () => void;
  onRemove: () => void;
}

export function WatchlistRow({ ticker, update, points, selected, onSelect, onRemove }: RowProps) {
  const flash = useFlash(update?.price, update?.direction);
  const dayPct = update?.day_change_percent ?? 0;
  return (
    <li
      data-testid={`watchlist-row-${ticker}`}
      onClick={onSelect}
      className={`group grid cursor-pointer grid-cols-[1fr_auto_auto_auto] items-center gap-x-2 border-b border-border/50 px-3 py-1.5 text-sm hover:bg-panel-2 ${
        selected ? "bg-panel-2 shadow-[inset_2px_0_0_var(--color-accent)]" : ""
      }`}
    >
      <span className="flex items-center gap-1 font-semibold">
        {ticker}
        <button
          aria-label={`Remove ${ticker}`}
          data-testid={`watchlist-remove-${ticker}`}
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className="invisible text-xs text-muted hover:text-down group-hover:visible"
        >
          ✕
        </button>
      </span>
      <span
        data-testid={`price-${ticker}`}
        className={`flash-cell rounded px-1 text-right tabular-nums ${flash ? `flash-${flash}` : ""}`}
      >
        {fmtPrice(update?.price)}
      </span>
      <span className={`w-14 text-right text-xs tabular-nums ${pnlClass(dayPct)}`}>{fmtPct(dayPct)}</span>
      <Sparkline points={points} />
    </li>
  );
}

/** Returns "up"/"down" briefly after a price change, then null (CSS fades the background). */
export function useFlash(price: number | undefined, direction: string | undefined, ms = 120) {
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const prev = useRef(price);
  useEffect(() => {
    if (price === undefined || prev.current === undefined || price === prev.current) {
      prev.current = price;
      return;
    }
    const dir = direction === "up" || direction === "down" ? direction : price > prev.current ? "up" : "down";
    prev.current = price;
    setFlash(dir);
    const id = setTimeout(() => setFlash(null), ms);
    return () => clearTimeout(id);
  }, [price, direction, ms]);
  return flash;
}
