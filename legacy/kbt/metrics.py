"""
metrics.py — strategy evaluation from the trade list + equity curve.

Pure functions over settled Trade objects. Unsettled trades (result is None)
are excluded from PnL stats and counted separately so you always know how much
of the sample actually resolved.

All headline numbers are REALIZED and self-consistent:
  - total_pnl   = sum of settled trade PnL
  - final_equity = starting equity (0) + total_pnl   (so it always equals total_pnl)
  - max_drawdown = worst peak-to-trough on the realized equity curve, anchored at
    the starting equity of 0 so an opening loss is counted (it was not before).

An optional mark-to-market `equity_curve` (intraday marks from the ledger) can be
passed; when present it is used to report an additional intraday drawdown
(`max_drawdown_intraday`) but never overrides the realized headline numbers.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime

from .schemas import Trade


@dataclass
class Metrics:
    n_trades:              int
    n_settled:             int
    n_unsettled:           int
    total_pnl:             float
    win_rate:              float
    avg_win:               float
    avg_loss:              float
    profit_factor:         float
    expectancy:            float
    max_drawdown:          float
    max_drawdown_intraday: float
    sharpe_per_trade:      float
    final_equity:          float

    def as_dict(self) -> dict:
        d = asdict(self)
        # JSON-safe: inf/nan are not valid JSON and break browser JSON.parse.
        for k, v in d.items():
            if isinstance(v, float) and not math.isfinite(v):
                d[k] = None
        return d


def _max_drawdown(equity: list[float]) -> float:
    """Worst trough below the running peak. `equity` must already include the
    starting point (e.g. 0.0) so a drop from the start is captured."""
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return mdd


def _cum(xs: list[float]) -> list[float]:
    """Cumulative sum, anchored at the starting equity of 0.0 so the first move
    is measured from the start, not from the first trade's own PnL."""
    out, run = [0.0], 0.0
    for x in xs:
        run += x
        out.append(run)
    return out


def compute_metrics(
    trades: list[Trade],
    equity_curve: list[tuple[datetime, float]] | None = None,
) -> Metrics:
    settled = [t for t in trades if t.pnl is not None]
    settled.sort(key=lambda t: t.ts)            # realized curve in time order
    pnls = [t.pnl for t in settled]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total = sum(pnls)
    win_rate = len(wins) / len(settled) if settled else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    gross_win = sum(wins)
    gross_loss = -sum(losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)
    expectancy = total / len(settled) if settled else 0.0

    if len(pnls) > 1:
        mean = total / len(pnls)
        var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    # realized headline drawdown — anchored at 0 so an opening loss counts
    realized_dd = _max_drawdown(_cum(pnls))

    # optional intraday (mark-to-market) drawdown from the ledger curve
    mtm_vals = [v for _, v in (equity_curve or [])]
    intraday_dd = _max_drawdown(mtm_vals) if mtm_vals else realized_dd

    # final equity is realized, so it always reconciles with total_pnl
    final_equity = total

    return Metrics(
        n_trades=len(trades),
        n_settled=len(settled),
        n_unsettled=len(trades) - len(settled),
        total_pnl=round(total, 4),
        win_rate=round(win_rate, 4),
        avg_win=round(avg_win, 4),
        avg_loss=round(avg_loss, 4),
        profit_factor=round(profit_factor, 4) if math.isfinite(profit_factor) else profit_factor,
        expectancy=round(expectancy, 4),
        max_drawdown=round(realized_dd, 4),
        max_drawdown_intraday=round(intraday_dd, 4),
        sharpe_per_trade=round(sharpe, 4),
        final_equity=round(final_equity, 4),
    )


def format_report(m: Metrics, strategy_name: str) -> str:
    pf = m.profit_factor
    pf_str = "inf" if pf == math.inf else f"{pf:.2f}"
    lines = [
        "=" * 52,
        f"  {strategy_name}",
        "=" * 52,
        f"  Trades              {m.n_trades}",
        f"  Settled / unsettled {m.n_settled} / {m.n_unsettled}",
        f"  Total PnL           ${m.total_pnl:+.2f}",
        f"  Final equity        ${m.final_equity:+.2f}",
        f"  Win rate            {m.win_rate*100:.1f}%",
        f"  Avg win / loss      ${m.avg_win:+.2f} / ${m.avg_loss:+.2f}",
        f"  Profit factor       {pf_str}",
        f"  Expectancy/trade    ${m.expectancy:+.2f}",
        f"  Max drawdown        ${m.max_drawdown:.2f}",
        f"  Max DD (intraday)   ${m.max_drawdown_intraday:.2f}",
        f"  Sharpe (per-trade)  {m.sharpe_per_trade:.2f}",
        "=" * 52,
    ]
    return "\n".join(lines)