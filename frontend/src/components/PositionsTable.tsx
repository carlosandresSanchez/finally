import { fmtPct, fmtPrice, fmtQty, fmtSignedUsd, pnlClass } from "@/lib/format";
import type { Position } from "@/lib/types";

export function PositionsTable({ positions, onSelect }: { positions: Position[]; onSelect: (t: string) => void }) {
  return (
    <div className="h-full overflow-auto" data-testid="positions-table">
      <table className="w-full text-xs tabular-nums">
        <thead className="sticky top-0 bg-panel text-[10px] uppercase text-muted">
          <tr className="border-b border-border">
            <th className="px-2 py-1 text-left">Ticker</th>
            <th className="px-2 py-1 text-right">Qty</th>
            <th className="px-2 py-1 text-right">Avg Cost</th>
            <th className="px-2 py-1 text-right">Price</th>
            <th className="px-2 py-1 text-right">Mkt Value</th>
            <th className="px-2 py-1 text-right">Unreal. P&amp;L</th>
            <th className="px-2 py-1 text-right">%</th>
          </tr>
        </thead>
        <tbody>
          {positions.length === 0 ? (
            <tr>
              <td colSpan={7} className="px-2 py-4 text-center text-muted">
                No open positions — use the trade bar or ask the AI.
              </td>
            </tr>
          ) : (
            positions.map((p) => (
              <tr
                key={p.ticker}
                data-testid={`position-row-${p.ticker}`}
                onClick={() => onSelect(p.ticker)}
                className="cursor-pointer border-b border-border/50 hover:bg-panel-2"
              >
                <td className="px-2 py-1 font-semibold">{p.ticker}</td>
                <td className="px-2 py-1 text-right" data-testid={`position-qty-${p.ticker}`}>
                  {fmtQty(p.quantity)}
                </td>
                <td className="px-2 py-1 text-right">{fmtPrice(p.avg_cost)}</td>
                <td className="px-2 py-1 text-right">{fmtPrice(p.current_price)}</td>
                <td className="px-2 py-1 text-right">{fmtPrice(p.market_value)}</td>
                <td className={`px-2 py-1 text-right ${pnlClass(p.unrealized_pnl)}`}>{fmtSignedUsd(p.unrealized_pnl)}</td>
                <td className={`px-2 py-1 text-right ${pnlClass(p.unrealized_pnl)}`}>{fmtPct(p.unrealized_pnl_percent)}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
