"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChatPanel } from "@/components/ChatPanel";
import { Header } from "@/components/Header";
import { Heatmap } from "@/components/Heatmap";
import { PositionsTable } from "@/components/PositionsTable";
import { TimeSeriesChart } from "@/components/TimeSeriesChart";
import { TradeBar } from "@/components/TradeBar";
import { Watchlist } from "@/components/Watchlist";
import { api } from "@/lib/api";
import { fmtPct, fmtPrice, pnlClass } from "@/lib/format";
import { livePortfolio } from "@/lib/portfolio";
import type { ChatMessage, Portfolio, Snapshot } from "@/lib/types";
import { usePriceStream } from "@/lib/usePriceStream";

const HISTORY_REFRESH_MS = 30_000;

export default function Home() {
  const { prices, history, status } = usePriceStream();
  const [tickers, setTickers] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);

  const refreshWatchlist = useCallback(async () => {
    const list = await api.watchlist();
    setTickers(list.map((w) => w.ticker));
    setSelected((cur) => cur ?? list[0]?.ticker ?? null);
  }, []);

  const refreshPortfolio = useCallback(async () => {
    const [p, h] = await Promise.all([api.portfolio(), api.history()]);
    setPortfolio(p);
    setSnapshots(h);
  }, []);

  useEffect(() => {
    refreshWatchlist().catch(console.error);
    refreshPortfolio().catch(console.error);
    api.chatHistory().then(setMessages).catch(console.error);
    const id = setInterval(() => refreshPortfolio().catch(console.error), HISTORY_REFRESH_MS);
    return () => clearInterval(id);
  }, [refreshWatchlist, refreshPortfolio]);

  const live = useMemo(() => (portfolio ? livePortfolio(portfolio, prices) : null), [portfolio, prices]);

  const selectedSeries = useMemo(
    () => (selected ? (history[selected] ?? []).map((p) => ({ time: p.time, value: p.price })) : []),
    [history, selected],
  );

  const pnlSeries = useMemo(
    () => snapshots.map((s) => ({ time: Date.parse(s.recorded_at) / 1000, value: s.total_value })),
    [snapshots],
  );

  async function handleTrade(ticker: string, quantity: number, side: "buy" | "sell") {
    const { portfolio: p } = await api.trade(ticker, quantity, side);
    setPortfolio(p);
    api.history().then(setSnapshots).catch(console.error);
  }

  async function handleAdd(ticker: string) {
    const list = await api.addTicker(ticker);
    setTickers(list.map((w) => w.ticker));
  }

  async function handleRemove(ticker: string) {
    const list = await api.removeTicker(ticker);
    setTickers(list.map((w) => w.ticker));
    if (selected === ticker) setSelected(list[0]?.ticker ?? null);
  }

  async function handleChat(text: string) {
    const optimistic: ChatMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content: text,
      actions: null,
      created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, optimistic]);
    setChatLoading(true);
    try {
      const reply = await api.chat(text);
      setMessages((m) => [...m, reply]);
      if (reply.actions) {
        await Promise.all([refreshPortfolio(), refreshWatchlist()]);
      }
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          id: `err-${Date.now()}`,
          role: "assistant",
          content: `Error: ${(err as Error).message}`,
          actions: null,
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setChatLoading(false);
    }
  }

  const sel = selected ? prices[selected] : undefined;

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <Header
        totalValue={live?.total_value ?? null}
        cash={live?.cash_balance ?? null}
        pnl={live?.unrealized_pnl ?? null}
        status={status}
      />
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <div className="h-64 w-full shrink-0 md:h-auto md:w-80">
          <Watchlist
            tickers={tickers}
            prices={prices}
            history={history}
            selected={selected}
            onSelect={setSelected}
            onAdd={handleAdd}
            onRemove={handleRemove}
          />
        </div>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <section className="flex min-h-[220px] flex-[3] flex-col border-b border-border">
            <div className="flex items-baseline gap-3 px-3 py-2">
              <h2 className="panel-title">Chart</h2>
              <span className="text-base font-bold text-accent" data-testid="selected-ticker">
                {selected ?? "—"}
              </span>
              {sel && (
                <>
                  <span className="tabular-nums">{fmtPrice(sel.price)}</span>
                  <span className={`text-xs tabular-nums ${pnlClass(sel.day_change_percent)}`}>
                    {fmtPct(sel.day_change_percent)}
                  </span>
                </>
              )}
            </div>
            <div className="min-h-0 flex-1 px-1">
              <TimeSeriesChart data={selectedSeries} color="#209dd7" testId="main-chart" />
            </div>
          </section>

          <div className="flex min-h-[200px] flex-[2] flex-col border-b border-border lg:flex-row">
            <section className="flex min-h-[160px] flex-1 flex-col border-border lg:border-r">
              <h2 className="panel-title px-3 py-2">Portfolio Heatmap</h2>
              <div className="min-h-0 flex-1 px-3 pb-3">
                <Heatmap positions={live?.positions ?? []} onSelect={setSelected} />
              </div>
            </section>
            <section className="flex min-h-[160px] flex-1 flex-col">
              <h2 className="panel-title px-3 py-2">Portfolio Value</h2>
              <div className="min-h-0 flex-1 px-1">
                <TimeSeriesChart data={pnlSeries} color="#ecad0a" testId="pnl-chart" />
              </div>
            </section>
          </div>

          <section className="flex min-h-[140px] flex-[2] flex-col">
            <h2 className="panel-title px-3 py-2">Positions</h2>
            <div className="min-h-0 flex-1">
              <PositionsTable positions={live?.positions ?? []} onSelect={setSelected} />
            </div>
          </section>

          <TradeBar defaultTicker={selected} onTrade={handleTrade} />
        </main>

        <div className={`flex min-h-0 ${chatOpen ? "h-96 md:h-auto" : ""}`}>
          <ChatPanel
            messages={messages}
            loading={chatLoading}
            onSend={handleChat}
            open={chatOpen}
            onToggle={() => setChatOpen((o) => !o)}
          />
        </div>
      </div>
    </div>
  );
}
