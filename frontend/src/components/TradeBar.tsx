"use client";

import { useEffect, useState } from "react";

interface Props {
  defaultTicker: string | null;
  onTrade: (ticker: string, quantity: number, side: "buy" | "sell") => Promise<void>;
}

export function TradeBar({ defaultTicker, onTrade }: Props) {
  const [ticker, setTicker] = useState(defaultTicker ?? "");
  const [quantity, setQuantity] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (defaultTicker) setTicker(defaultTicker);
  }, [defaultTicker]);

  async function trade(side: "buy" | "sell") {
    const t = ticker.trim().toUpperCase();
    const q = Number(quantity);
    if (!t) return setMessage({ ok: false, text: "Enter a ticker" });
    if (!Number.isFinite(q) || q <= 0) return setMessage({ ok: false, text: "Enter a positive quantity" });
    setBusy(true);
    try {
      await onTrade(t, q, side);
      setMessage({ ok: true, text: `${side === "buy" ? "Bought" : "Sold"} ${q} ${t}` });
      setQuantity("");
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="flex flex-wrap items-center gap-2 border-t border-border bg-panel px-3 py-2"
      onSubmit={(e) => {
        e.preventDefault();
        trade("buy");
      }}
      data-testid="trade-bar"
    >
      <span className="panel-title mr-1">Trade</span>
      <input
        value={ticker}
        onChange={(e) => setTicker(e.target.value)}
        placeholder="Ticker"
        aria-label="Trade ticker"
        data-testid="trade-ticker"
        className="w-24 rounded border border-border bg-bg px-2 py-1 text-xs uppercase outline-none focus:border-primary"
      />
      <input
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        placeholder="Qty"
        inputMode="decimal"
        aria-label="Trade quantity"
        data-testid="trade-quantity"
        className="w-20 rounded border border-border bg-bg px-2 py-1 text-xs outline-none focus:border-primary"
      />
      <button
        type="button"
        disabled={busy}
        onClick={() => trade("buy")}
        data-testid="trade-buy"
        className="rounded bg-up px-3 py-1 text-xs font-bold text-white hover:opacity-90 disabled:opacity-50"
      >
        BUY
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => trade("sell")}
        data-testid="trade-sell"
        className="rounded bg-down px-3 py-1 text-xs font-bold text-white hover:opacity-90 disabled:opacity-50"
      >
        SELL
      </button>
      {message && (
        <span data-testid="trade-message" className={`text-xs ${message.ok ? "text-up" : "text-down"}`}>
          {message.text}
        </span>
      )}
    </form>
  );
}
