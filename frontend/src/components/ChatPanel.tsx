"use client";

import { useEffect, useRef, useState } from "react";
import { fmtPrice, fmtQty } from "@/lib/format";
import type { ChatMessage } from "@/lib/types";

interface Props {
  messages: ChatMessage[];
  loading: boolean;
  onSend: (message: string) => void;
  open: boolean;
  onToggle: () => void;
}

export function ChatPanel({ messages, loading, onSend, open, onToggle }: Props) {
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages, loading]);

  if (!open) {
    return (
      <button
        onClick={onToggle}
        data-testid="chat-toggle"
        className="border-l border-border bg-panel px-1 text-xs text-accent [writing-mode:vertical-rl] hover:bg-panel-2"
      >
        ◀ AI ASSISTANT
      </button>
    );
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;
    onSend(text);
    setInput("");
  }

  return (
    <aside className="flex min-h-0 w-full flex-col border-l border-border bg-panel lg:w-96" data-testid="chat-panel">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <h2 className="panel-title">
          <span className="text-accent">●</span> FinAlly AI
        </h2>
        <button onClick={onToggle} data-testid="chat-toggle" className="text-xs text-muted hover:text-text">
          ▶ hide
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3 text-sm" data-testid="chat-messages">
        {messages.length === 0 && (
          <p className="text-xs text-muted">
            Ask about your portfolio, request analysis, or tell me to trade — e.g. &ldquo;Buy 5 AAPL&rdquo; or
            &ldquo;Add PYPL to my watchlist&rdquo;.
          </p>
        )}
        {messages.map((m) => (
          <Message key={m.id} message={m} />
        ))}
        {loading && (
          <div data-testid="chat-loading" className="flex items-center gap-1 text-xs text-muted">
            <span className="animate-pulse">●</span>
            <span className="animate-pulse [animation-delay:150ms]">●</span>
            <span className="animate-pulse [animation-delay:300ms]">●</span>
            <span className="ml-1">thinking</span>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <form onSubmit={submit} className="flex gap-2 border-t border-border p-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask FinAlly…"
          aria-label="Chat message"
          data-testid="chat-input"
          className="min-w-0 flex-1 rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-primary"
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          data-testid="chat-send"
          className="rounded bg-secondary px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </aside>
  );
}

function Message({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex flex-col ${isUser ? "items-end" : "items-start"}`} data-testid={`chat-message-${message.role}`}>
      <div
        className={`max-w-[90%] whitespace-pre-wrap rounded px-3 py-2 ${
          isUser ? "bg-primary/20 text-text" : "border border-border bg-panel-2"
        }`}
      >
        {message.content}
      </div>
      {message.actions && (
        <div className="mt-1 flex max-w-[90%] flex-col gap-1">
          {message.actions.trades.map((t, i) => (
            <div
              key={`t${i}`}
              data-testid="chat-trade"
              data-status={t.status}
              className={`rounded border px-2 py-1 text-xs ${
                t.status === "executed" ? "border-up/50 text-up" : "border-down/50 text-down"
              }`}
            >
              {t.status === "executed"
                ? `✓ ${t.side.toUpperCase()} ${fmtQty(t.quantity)} ${t.ticker} @ ${fmtPrice(t.price)}`
                : `✗ ${t.side.toUpperCase()} ${fmtQty(t.quantity)} ${t.ticker}: ${t.error}`}
            </div>
          ))}
          {message.actions.watchlist_changes.map((w, i) => (
            <div
              key={`w${i}`}
              data-testid="chat-watchlist-change"
              className={`rounded border px-2 py-1 text-xs ${
                w.status === "failed" ? "border-down/50 text-down" : "border-primary/50 text-primary"
              }`}
            >
              {w.status === "failed"
                ? `✗ ${w.action} ${w.ticker}: ${w.error}`
                : `${w.action === "add" ? "+" : "−"} ${w.ticker} ${w.action === "add" ? "added to" : "removed from"} watchlist`}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
