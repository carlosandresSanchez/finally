import { fmtSignedUsd, fmtUsd, pnlClass } from "@/lib/format";
import type { ConnectionStatus } from "@/lib/types";

const STATUS_COLOR: Record<ConnectionStatus, string> = {
  connected: "bg-up",
  reconnecting: "bg-accent",
  disconnected: "bg-down",
};

interface Props {
  totalValue: number | null;
  cash: number | null;
  pnl: number | null;
  status: ConnectionStatus;
}

export function Header({ totalValue, cash, pnl, status }: Props) {
  return (
    <header className="flex items-center justify-between border-b border-border bg-panel px-4 py-2">
      <div className="flex items-baseline gap-2">
        <span className="text-lg font-bold text-accent">FinAlly</span>
        <span className="hidden text-xs text-muted sm:inline">AI Trading Workstation</span>
      </div>
      <div className="flex items-center gap-6 text-sm">
        <Stat label="Portfolio" value={fmtUsd(totalValue)} testId="total-value" />
        <Stat label="Cash" value={fmtUsd(cash)} testId="cash-balance" />
        {pnl != null && (
          <Stat label="Unrealized" value={fmtSignedUsd(pnl)} className={pnlClass(pnl)} testId="total-pnl" />
        )}
        <div className="flex items-center gap-2" title={`Stream ${status}`}>
          <span
            data-testid="connection-status"
            data-status={status}
            className={`inline-block h-2.5 w-2.5 rounded-full ${STATUS_COLOR[status]}`}
          />
          <span className="text-xs capitalize text-muted">{status}</span>
        </div>
      </div>
    </header>
  );
}

function Stat({ label, value, className = "", testId }: { label: string; value: string; className?: string; testId: string }) {
  return (
    <div className="flex flex-col items-end leading-tight">
      <span className="text-[10px] uppercase tracking-wider text-muted">{label}</span>
      <span data-testid={testId} className={`font-semibold tabular-nums ${className}`}>
        {value}
      </span>
    </div>
  );
}
