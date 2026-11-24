# C12 — Run metrics: trades, daily P&L, Sharpe, drawdown, VaR, event risk, exposure

Track: C accounting. Status: APPROVED (user, 2026-11-24, agreed in chat: Sharpe on daily equity changes
annualised by √365, plus per-trade Sharpe; historical VaR 95/99 on daily P&L plus worst-case loss per
event from bracket payoffs; user away, lead proceeds with the agreed plan).

## Goal
`portfolio/metrics.py` turns one run into a trade list (one row per market traded), day-end P&L points and
one dict of headline metrics, built on `equity_curve` (C11). Pure functions do the arithmetic so they can be
tested with hand numbers; two small functions read the database.

## Not built
- No table, view or migration; no UI (C13); no run comparison or sweeps.
- No expected shortfall / CVaR, no Sortino, no benchmark; one Sharpe convention per period only.

## Files
- `portfolio/metrics.py` (new)

## Interfaces
Money is Decimal dollars; ratios are float; timestamps tz-aware UTC.

```python
def trades(conn, run_id) -> list[dict]
```
One row per market the run has a ledger row in, ordered by `first_ts`, keys:
`market_ticker, event_ticker, event_date, underlying_id, first_ts` (first ledger ts), `last_ts`,
`n_fills` (ledger rows with kind 'fill'), `fees` (sum of fee), `pnl_net` (last `realized_cum` − fees),
`open` (bool: last `net_after <> 0`).

```python
def event_risk(conn, run_id) -> list[dict]
```
One row per event traded, ordered by `event_ticker`: `event_ticker, underlying_id, event_date,
max_worst_loss` (largest worst-case loss of the event's open positions after any ledger row), plus a
second return value — see Hard parts: the function returns `(rows, peak_capital_at_risk)`.

```python
def worst_loss(markets, nets, costs) -> Decimal
```
`markets`: every market ticker of one event; `nets`: {ticker: signed YES units, nonzero only};
`costs`: {ticker: cost basis}. Exactly one market of an event settles YES.

```python
def daily_points(curve) -> list[dict]
def metrics(curve, trade_rows, peak_capital_at_risk, max_event_worst_loss) -> dict
def run_metrics(conn, run_id) -> dict      # glue: equity_curve + trades + event_risk + metrics
```
CLI: `uv run python -m portfolio.metrics <run_id> [--db NAME]` prints `key value` lines of `run_metrics`.
SELECT only.

`metrics` returns exactly these keys (None where undefined, e.g. no data or zero spread):
| key | definition |
|---|---|
| `starting_cash`, `final_equity` | first / last curve row equity |
| `total_pnl`, `total_return` | final − starting; ÷ starting (float) |
| `max_drawdown`, `max_drawdown_pct`, `max_drawdown_at` | largest drop of hourly equity below its running peak (dollars, positive); ÷ that peak (float); ts of the trough |
| `n_days`, `sharpe_daily` | `len(daily_points(curve))` (the start point is not one of them); mean/stdev(sample) of daily returns × √365 |
| `var_95`, `var_99` | historical VaR of daily P&L in dollars (rule below), ≥ 0 |
| `n_trades`, `n_open` | rows of `trade_rows`; of them `open` |
| `win_rate`, `avg_win`, `avg_loss`, `profit_factor`, `expectancy`, `sharpe_per_trade` | over closed trades (`open` false) by `pnl_net`: wins > 0, losses < 0; profit_factor = sum wins / −sum losses (None if no losses); expectancy = mean; sharpe_per_trade = mean/stdev(sample), not annualised |
| `fees` | sum of trade fees |
| `max_cost_basis_open`, `max_open_markets`, `time_in_market` | max over curve rows; share of curve rows with open_markets > 0 (float) |
| `peak_capital_at_risk`, `max_event_worst_loss` | passed through |

## Hard parts (use as written)
```python
def daily_points(curve):
    """Start point, every 00:00 UTC mark after it, and the last row; each with the P&L since the
    previous point. 'day' is the UTC day that ended at that point."""
    if not curve:
        return []
    points = [curve[0]] + [r for r in curve[1:] if r["ts"].hour == 0]
    if points[-1] is not curve[-1]:
        points.append(curve[-1])
    out = []
    for prev, cur in zip(points, points[1:]):
        day = (cur["ts"] - timedelta(microseconds=1)).date() if cur["ts"].hour == 0 else cur["ts"].date()
        pnl = cur["equity"] - prev["equity"]
        out.append({"day": day, "ts": cur["ts"], "equity": cur["equity"], "pnl": pnl,
                    "ret": float(pnl / prev["equity"])})
    return out


def historical_var(pnls, level):
    """Loss not exceeded on `level` of days: the k-th worst day, k = floor((1 - level) * n), no interpolation."""
    ordered = sorted(pnls)
    k = math.floor((1 - level) * len(ordered) + 1e-9)
    return max(Decimal(0), -ordered[k])


def worst_loss(markets, nets, costs):
    payouts = []
    for winner in markets:
        pay = Decimal(0)
        for m, net in nets.items():
            if net > 0 and m == winner:
                pay += net
            elif net < 0 and m != winner:
                pay += -net
        payouts.append(pay)
    return sum(costs.values(), Decimal(0)) - min(payouts)


def event_risk(conn, run_id):
    # ledger rows in time order with each market's event; every market of those events
    # SELECT l.market_ticker, k.event_ticker, l.net_after, l.cost_basis_after
    #   FROM portfolio_ledger l JOIN market k ON k.ticker = l.market_ticker
    #   WHERE l.run_id = %s ORDER BY l.ts, l.kind, l.seq
    # SELECT k.ticker, k.event_ticker, e.underlying_id, e.event_date FROM market k
    #   JOIN event e ON e.ticker = k.event_ticker WHERE k.event_ticker = ANY(%s)
    nets, costs = {}, {}          # event -> {market: value}, only open markets
    current = {}                  # event -> its worst loss now
    worst = {}                    # event -> max worst loss seen
    peak = Decimal(0)
    for market, event, net, cost in ledger_rows:
        nets.setdefault(event, {}).pop(market, None)
        costs.setdefault(event, {}).pop(market, None)
        if net != 0:
            nets[event][market] = net
            costs[event][market] = cost
        current[event] = worst_loss(markets_of[event], nets[event], costs[event]) if nets[event] else Decimal(0)
        worst[event] = max(worst.get(event, Decimal(0)), current[event])
        peak = max(peak, sum(current.values(), Decimal(0)))
    rows = [{"event_ticker": e, "underlying_id": ..., "event_date": ..., "max_worst_loss": worst[e]}
            for e in sorted(worst)]
    return rows, peak
```
Max drawdown: walk the curve with `peak = max(peak, equity)`, `drop = peak - equity`; keep the largest drop,
its peak and its ts (first occurrence wins ties). `max_drawdown_pct = float(drop / peak)`; with no drop:
0, 0.0, None.
Sharpe: needs ≥ 2 daily points and stdev > 0, else None. Use `statistics.mean/stdev` on floats.
`trades`: one SQL over `portfolio_ledger` grouped by market (last row via `DISTINCT ON` or
`array_agg(... ORDER BY ts DESC, kind DESC, seq DESC)[1]`, matching the ledger's own order), joined to
market and event.

## Agreed tests (`tests/portfolio/check_metrics.py`, qa-tester)
Pure (no database), hand values:
1. `daily_points` on the curve (ts UTC, equity): 06-01 00:00 1000, 06-01 12:00 990, 06-02 00:00 1010,
   06-02 06:00 980, 06-03 00:00 1000, 06-03 05:00 1005 → 3 points: (day 06-01, pnl +10, ret 0.01),
   (06-02, −10, −0.0099010), (06-03, +5, 0.005).
2. `metrics` on that curve (open_markets 0,1,1,1,1,0; cost_basis_open 0,5,5,5,5,0) with trade pnl_net
   2.66, 4.32, −1.00 (closed, fees 0.09, 0.18, 0.05) and one open trade pnl 0 (fees 0.02), peak 7.75,
   max event 7.75: total_pnl 5, total_return 0.005, max_drawdown 30, max_drawdown_pct 0.029703 (30/1010),
   max_drawdown_at 06-02 06:00, n_days 3, sharpe_daily 3.13654 (±1e-4), var_95 10, var_99 10,
   n_trades 4, n_open 1, win_rate 0.666667, avg_win 3.49, avg_loss −1.00, profit_factor 6.98,
   expectancy 1.993333, sharpe_per_trade 0.732322 (±1e-5), fees 0.34, max_cost_basis_open 5,
   max_open_markets 1, time_in_market 0.666667.
3. `historical_var` on the 100 Decimal values pnl = i − 50 for i in 0..99: var_95 = 45 (k = 5,
   value −45), var_99 = 49 (k = 1).
4. `worst_loss(["A","B","C"], {"A": -10, "B": -10}, {"A": 5.00, "B": 6.00})` = 1.00 (min payout 10, cost 11).
5. `worst_loss(["A","B","C"], {"A": 5}, {"A": 2.25})` = 2.25; `{"A": 5, "B": -10}` with costs
   2.25 / 5.50 → 7.75 (if B wins: payout 0).
6. Empty curve: `daily_points([]) == []`; `metrics([], [], 0, 0)` returns every key without raising:
   counts (n_days, n_trades, n_open, max_open_markets) are 0, fees 0, peak_capital_at_risk and
   max_event_worst_loss 0 (passed through), every other key None.
Database (throwaway `kalbt_metrics_test_<pid>`, same fixture and hand run as C11 tests 1–9, built with
`load_fixture` + `hand_run` from `tests/recon/check_check_run.py`):
7. `trades`: 2 rows; T-M1 n_fills 1, fees 0.09, pnl_net 2.66, open False; T-M2 fees 0.18, pnl_net 4.32;
   both event EV-0601, underlying U1, event_date 2026-06-01.
8. `event_risk`: one row EV-0601 max_worst_loss 7.75; peak_capital_at_risk 7.75.
9. `run_metrics`: total_pnl 6.98, final_equity 1006.98, n_trades 2, win_rate 1.0, profit_factor None,
   fees 0.27, max_open_markets 2, max_cost_basis_open 7.75.
10. Live, read-only: `run_metrics(connect("kalbt"), 32)` in under 15 s; total_pnl equals run 32's last
    `portfolio_daily.net_pnl_cum`; sum of `trades` pnl_net equals the same; row counts of run,
    order_intent, order_outcome unchanged. CLI on 32 exits 0.

## Test files
- `tests/portfolio/check_metrics.py`

## Legacy reference
`legacy/kbt/metrics.py` (win rate, profit factor, expectancy, drawdown anchored at the start, per-trade
Sharpe). Not ported: float money, realized-only equity.

## Done when
- `PYTHONPATH=. uv run python tests/portfolio/check_metrics.py` exits 0
- `uv run python -m portfolio.metrics 32` prints the metrics
- lead runs `uvx ruff check portfolio/metrics.py tests/portfolio/check_metrics.py` clean

If anything here is unclear or looks wrong: stop and report, do not guess.
