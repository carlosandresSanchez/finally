import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/ChatPanel";
import { Header } from "@/components/Header";
import { pnlColor } from "@/components/Heatmap";
import { PositionsTable } from "@/components/PositionsTable";
import { TradeBar } from "@/components/TradeBar";
import { Watchlist, WatchlistRow } from "@/components/Watchlist";
import type { ChatMessage, Position, PriceUpdate } from "@/lib/types";

const pu = (price: number, direction: PriceUpdate["direction"] = "flat"): PriceUpdate => ({
  ticker: "AAPL",
  price,
  previous_price: price,
  timestamp: 1,
  change: 0,
  change_percent: 0,
  direction,
  session_open: 100,
  day_change: price - 100,
  day_change_percent: price - 100,
});

describe("WatchlistRow price flash", () => {
  it("flashes green on uptick then clears", () => {
    vi.useFakeTimers();
    const props = { ticker: "AAPL", points: [], selected: false, onSelect: () => {}, onRemove: () => {} };
    const { rerender } = render(
      <ul>
        <WatchlistRow {...props} update={pu(100)} />
      </ul>,
    );
    const cell = screen.getByTestId("price-AAPL");
    expect(cell).not.toHaveClass("flash-up");

    rerender(
      <ul>
        <WatchlistRow {...props} update={pu(101, "up")} />
      </ul>,
    );
    expect(cell).toHaveClass("flash-up");
    act(() => vi.advanceTimersByTime(200));
    expect(cell).not.toHaveClass("flash-up");

    rerender(
      <ul>
        <WatchlistRow {...props} update={pu(99, "down")} />
      </ul>,
    );
    expect(cell).toHaveClass("flash-down");
    vi.useRealTimers();
  });
});

describe("Watchlist", () => {
  it("adds and removes tickers", async () => {
    const onAdd = vi.fn().mockResolvedValue(undefined);
    const onRemove = vi.fn().mockResolvedValue(undefined);
    render(
      <Watchlist
        tickers={["AAPL"]}
        prices={{ AAPL: pu(101) }}
        history={{}}
        selected={null}
        onSelect={() => {}}
        onAdd={onAdd}
        onRemove={onRemove}
      />,
    );
    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("101.00");
    await userEvent.type(screen.getByTestId("watchlist-input"), "pypl");
    await userEvent.click(screen.getByTestId("watchlist-add"));
    expect(onAdd).toHaveBeenCalledWith("PYPL");
    fireEvent.click(screen.getByTestId("watchlist-remove-AAPL"));
    expect(onRemove).toHaveBeenCalled();
  });

  it("shows add errors", async () => {
    const onAdd = vi.fn().mockRejectedValue(new Error("PYPL is already on the watchlist"));
    render(
      <Watchlist tickers={[]} prices={{}} history={{}} selected={null} onSelect={() => {}} onAdd={onAdd} onRemove={vi.fn()} />,
    );
    await userEvent.type(screen.getByTestId("watchlist-input"), "PYPL");
    await userEvent.click(screen.getByTestId("watchlist-add"));
    expect(await screen.findByText("PYPL is already on the watchlist")).toBeInTheDocument();
  });
});

describe("Header", () => {
  it("shows values and connection status", () => {
    render(<Header totalValue={10500} cash={9000} pnl={-25} status="reconnecting" />);
    expect(screen.getByTestId("total-value")).toHaveTextContent("$10,500.00");
    expect(screen.getByTestId("cash-balance")).toHaveTextContent("$9,000.00");
    expect(screen.getByTestId("total-pnl")).toHaveClass("text-down");
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "reconnecting");
  });
});

describe("PositionsTable", () => {
  const pos: Position = {
    ticker: "TSLA",
    quantity: 2.5,
    avg_cost: 200,
    current_price: 220,
    market_value: 550,
    cost_basis: 500,
    unrealized_pnl: 50,
    unrealized_pnl_percent: 10,
    weight: 5,
  };

  it("renders positions with P&L", () => {
    render(<PositionsTable positions={[pos]} onSelect={() => {}} />);
    expect(screen.getByTestId("position-qty-TSLA")).toHaveTextContent("2.5");
    expect(screen.getByText("+$50.00")).toHaveClass("text-up");
    expect(screen.getByText("+10.00%")).toBeInTheDocument();
  });

  it("renders empty state", () => {
    render(<PositionsTable positions={[]} onSelect={() => {}} />);
    expect(screen.getByText(/No open positions/)).toBeInTheDocument();
  });
});

describe("pnlColor", () => {
  it("is green for gains and red for losses", () => {
    expect(pnlColor(3)).toMatch(/^rgba\(38, 166, 65/);
    expect(pnlColor(-3)).toMatch(/^rgba\(248, 81, 73/);
  });
});

describe("TradeBar", () => {
  it("submits buy and sell orders", async () => {
    const onTrade = vi.fn().mockResolvedValue(undefined);
    render(<TradeBar defaultTicker="AAPL" onTrade={onTrade} />);
    await userEvent.type(screen.getByTestId("trade-quantity"), "3");
    await userEvent.click(screen.getByTestId("trade-buy"));
    expect(onTrade).toHaveBeenCalledWith("AAPL", 3, "buy");
    expect(await screen.findByTestId("trade-message")).toHaveTextContent("Bought 3 AAPL");
  });

  it("validates quantity and surfaces API errors", async () => {
    const onTrade = vi.fn().mockRejectedValue(new Error("Insufficient cash"));
    render(<TradeBar defaultTicker="AAPL" onTrade={onTrade} />);
    await userEvent.click(screen.getByTestId("trade-sell"));
    expect(screen.getByTestId("trade-message")).toHaveTextContent("positive quantity");
    await userEvent.type(screen.getByTestId("trade-quantity"), "1");
    await userEvent.click(screen.getByTestId("trade-sell"));
    await waitFor(() => expect(screen.getByTestId("trade-message")).toHaveTextContent("Insufficient cash"));
  });
});

describe("ChatPanel", () => {
  const reply: ChatMessage = {
    id: "1",
    role: "assistant",
    content: "Done",
    created_at: "",
    actions: {
      trades: [
        { ticker: "AAPL", side: "buy", quantity: 2, price: 190, status: "executed" },
        { ticker: "TSLA", side: "sell", quantity: 1, status: "failed", error: "Insufficient shares" },
      ],
      watchlist_changes: [{ ticker: "PYPL", action: "add", status: "ok" }],
    },
  };

  it("renders messages with inline action confirmations", () => {
    render(<ChatPanel messages={[reply]} loading={false} onSend={() => {}} open onToggle={() => {}} />);
    const trades = screen.getAllByTestId("chat-trade");
    expect(trades[0]).toHaveTextContent("BUY 2 AAPL @ 190.00");
    expect(trades[1]).toHaveAttribute("data-status", "failed");
    expect(screen.getByTestId("chat-watchlist-change")).toHaveTextContent("PYPL added to watchlist");
  });

  it("shows loading indicator and sends messages", async () => {
    const onSend = vi.fn();
    const { rerender } = render(<ChatPanel messages={[]} loading={false} onSend={onSend} open onToggle={() => {}} />);
    await userEvent.type(screen.getByTestId("chat-input"), "hello");
    await userEvent.click(screen.getByTestId("chat-send"));
    expect(onSend).toHaveBeenCalledWith("hello");
    rerender(<ChatPanel messages={[]} loading onSend={onSend} open onToggle={() => {}} />);
    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
  });

  it("collapses", () => {
    render(<ChatPanel messages={[]} loading={false} onSend={() => {}} open={false} onToggle={() => {}} />);
    expect(screen.queryByTestId("chat-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-toggle")).toBeInTheDocument();
  });
});
