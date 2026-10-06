import { describe, expect, it } from "vitest";
import { fmtPct, fmtSignedUsd, fmtUsd } from "@/lib/format";
import { livePortfolio } from "@/lib/portfolio";
import { squarify } from "@/lib/treemap";
import type { Portfolio, PriceUpdate } from "@/lib/types";
import { appendHistory } from "@/lib/usePriceStream";
import { toChartData } from "@/components/TimeSeriesChart";

const pu = (ticker: string, price: number, timestamp = 1): PriceUpdate => ({
  ticker,
  price,
  previous_price: price,
  timestamp,
  change: 0,
  change_percent: 0,
  direction: "flat",
  session_open: price,
  day_change: 0,
  day_change_percent: 0,
});

describe("format", () => {
  it("formats currency and percents", () => {
    expect(fmtUsd(1234.5)).toBe("$1,234.50");
    expect(fmtUsd(null)).toBe("—");
    expect(fmtPct(1.234)).toBe("+1.23%");
    expect(fmtPct(-0.5)).toBe("-0.50%");
    expect(fmtSignedUsd(-12)).toBe("-$12.00");
  });
});

describe("livePortfolio", () => {
  const base: Portfolio = {
    cash_balance: 1000,
    positions_value: 1000,
    total_value: 2000,
    unrealized_pnl: 0,
    unrealized_pnl_percent: 0,
    positions: [
      {
        ticker: "AAPL",
        quantity: 10,
        avg_cost: 100,
        current_price: 100,
        market_value: 1000,
        cost_basis: 1000,
        unrealized_pnl: 0,
        unrealized_pnl_percent: 0,
        weight: 50,
      },
    ],
  };

  it("revalues positions with streamed prices", () => {
    const live = livePortfolio(base, { AAPL: pu("AAPL", 110) });
    expect(live.total_value).toBe(2100);
    expect(live.unrealized_pnl).toBe(100);
    expect(live.positions[0].unrealized_pnl_percent).toBeCloseTo(10);
    expect(live.positions[0].weight).toBeCloseTo((1100 / 2100) * 100);
  });

  it("falls back to server price when no stream price", () => {
    expect(livePortfolio(base, {}).total_value).toBe(2000);
  });
});

describe("squarify", () => {
  it("fills the box with areas proportional to value", () => {
    const rects = squarify(
      [
        { value: 6, data: "a" },
        { value: 3, data: "b" },
        { value: 1, data: "c" },
      ],
      100,
      50,
    );
    expect(rects).toHaveLength(3);
    const area = (d: string) => {
      const r = rects.find((x) => x.data === d)!;
      return r.w * r.h;
    };
    expect(area("a")).toBeCloseTo(3000);
    expect(area("b")).toBeCloseTo(1500);
    expect(area("c")).toBeCloseTo(500);
    for (const r of rects) {
      expect(r.x + r.w).toBeLessThanOrEqual(100.0001);
      expect(r.y + r.h).toBeLessThanOrEqual(50.0001);
    }
  });

  it("ignores zero values and empty boxes", () => {
    expect(squarify([{ value: 0, data: 1 }], 10, 10)).toEqual([]);
    expect(squarify([{ value: 5, data: 1 }], 0, 10)).toEqual([]);
  });
});

describe("appendHistory", () => {
  it("appends only new ticks and caps length", () => {
    let h = appendHistory({}, { A: pu("A", 1, 1) });
    h = appendHistory(h, { A: pu("A", 1, 1) });
    expect(h.A).toHaveLength(1);
    h = appendHistory(h, { A: pu("A", 2, 2) }, 2);
    h = appendHistory(h, { A: pu("A", 3, 3) }, 2);
    expect(h.A.map((p) => p.price)).toEqual([2, 3]);
  });

  it("drops tickers no longer in the stream", () => {
    const h = appendHistory({ OLD: [{ time: 1, price: 1 }] }, { A: pu("A", 1) });
    expect(Object.keys(h)).toEqual(["A"]);
  });
});

describe("toChartData", () => {
  it("dedupes sub-second points to strictly increasing seconds", () => {
    const d = toChartData([
      { time: 10.1, value: 1 },
      { time: 10.6, value: 2 },
      { time: 11.2, value: 3 },
      { time: 9, value: 4 },
    ]);
    expect(d).toEqual([
      { time: 10, value: 2 },
      { time: 11, value: 3 },
    ]);
  });
});
