"""
server.py — WebSocket bridge between backtester.html and the kbt engine.

Run it from the repo root (where the `kbt/` package and `data/` live):

    uv pip install websockets
    python server.py

Then open backtester.html in your browser. It connects to ws://localhost:8765,
receives the available cached date intervals, and streams a backtest when you
hit Run.

Protocol (client → server):
    {type:"calendar"}                          → ask for cached intervals
    {type:"save_strategy", filename, strategy} → persist a strategy script (b64)
    {type:"start", city, series, start_date, end_date,
                   strategy, strategy_filename} → run a backtest
    {type:"stop"}                              → cancel a running backtest

Protocol (server → client):
    {type:"calendar", cities:[{key, series, intervals:[{start,end,days}], n_days}]}
    {type:"saved", ok, path}
    {type:"bar", time, open, high, low, close, volume}
    {type:"order", ts, market, side, price, contracts, cum_pnl}
    {type:"pnl", label, value}
    {type:"log", ts, level, message}
    {type:"stats", n_trades, win_rate, max_dd, sharpe, total_pnl}
    {type:"progress", pct}
    {type:"done"} | {type:"error", message}

Strategy file convention
------------------------
The uploaded .py file must expose a strategy. Either:
    strategy = MyStrategy(...)          # a module-level instance named `strategy`
or define exactly one class with an `on_event` method (it is instantiated with
no args). Example minimal file:

    from kbt.strategies import HighestTempFade
    strategy = HighestTempFade(quantity=1)
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import sys
from datetime import date
from pathlib import Path

try:
    import websockets
except ImportError:
    sys.exit("Missing dependency. Run:  uv pip install websockets")

from kbt.engine import run_event
from kbt.ledger import Ledger
from kbt.metrics import compute_metrics
from kbt.run import build_event_days

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("server")

# --- config ---------------------------------------------------------------
HOST = "localhost"
PORT = 8765
DATA = Path("data")
STRAT_DIR = Path("strategies_user")
STRAT_DIR.mkdir(exist_ok=True)

CITIES = [
    {"key": "new_york",    "series": "KXHIGHNY"},
    {"key": "los_angeles", "series": "KXHIGHLAX"},
    {"key": "miami",       "series": "KXHIGHMIA"},
]


def detect_series(city_key: str, fallback: str | None = None) -> str | None:
    """Series for a city = the markets/*.json filename on disk, so the server
    adapts to whatever data is present instead of trusting a hardcoded ticker.
    If several files exist, the largest (the real series) wins. Falls back to the
    CITIES hint when the folder is empty/missing."""
    mdir = DATA / "raw" / "kalshi" / city_key / "markets"
    if mdir.is_dir():
        files = sorted(mdir.glob("*.json"), key=lambda p: p.stat().st_size, reverse=True)
        if files:
            return files[0].stem
    return fallback

# one cancel flag per connection
_running: dict[int, bool] = {}


# --------------------------------------------------------------------------
# Calendar: continuous cached intervals
# --------------------------------------------------------------------------

def _merge_intervals(dates: list[date]) -> list[dict]:
    """
    Group a sorted list of event-dates into maximal continuous runs.
    This is the queue/stack merge: walk dates, extend the current run while
    the next date is +1 day, otherwise close the run and start a new one.
    """
    out: list[dict] = []
    if not dates:
        return out
    start = prev = dates[0]
    for d in dates[1:]:
        if (d - prev).days == 1:
            prev = d
            continue
        out.append({"start": start.isoformat(), "end": prev.isoformat(),
                    "days": (prev - start).days + 1})
        start = prev = d
    out.append({"start": start.isoformat(), "end": prev.isoformat(),
                "days": (prev - start).days + 1})
    return out


def build_calendar() -> list[dict]:
    cities = []
    for c in CITIES:
        series = detect_series(c["key"], c.get("series"))
        try:
            days = build_event_days(DATA, c["key"], series) if series else []
        except FileNotFoundError:
            days = []
        except Exception:
            log.exception("build_event_days failed for %s/%s", c["key"], series)
            days = []
        dates = sorted({d.event_date_local for d in days})
        cities.append({
            "key": c["key"], "series": series,
            "intervals": _merge_intervals(dates),
            "n_days": len(dates),
        })
    return cities


# --------------------------------------------------------------------------
# Strategy loading
# --------------------------------------------------------------------------

def save_strategy_b64(filename: str, b64: str) -> Path:
    safe = Path(filename).name  # strip any path components
    if not safe.endswith(".py"):
        safe += ".py"
    path = STRAT_DIR / safe
    path.write_bytes(base64.b64decode(b64))
    return path


# location of the built-in package strategies (kbt/strategies/*.py)
from kbt.strategy_loader import (
    PKG_STRAT_DIR, PKG_SKIP as _PKG_SKIP,
    extract_strategy as _extract_strategy,
    load_from_path as load_strategy,
    load_builtin as _load_builtin,
)


def resolve_strategy(source: str, filename: str):
    """
    Turn a {source, filename} reference into a strategy instance.
      builtin → import kbt.strategies.<stem>  (handles relative imports)
      user    → load strategies_user/<filename> by path
    """
    stem = Path(filename).stem
    if source == "builtin":
        return _load_builtin(stem)
    return load_strategy(STRAT_DIR / Path(filename).name)


def list_strategies() -> list[dict]:
    """
    Scan both the built-in package dir and the user upload dir for .py files.
    Each item is validated by attempting to resolve it, so the UI can show
    which ones are runnable.
    """
    items: list[dict] = []
    seen: set[str] = set()

    def _add(path: Path, source: str):
        if path.name in _PKG_SKIP or path.name.startswith("_"):
            return
        key = f"{source}:{path.name}"
        if key in seen:
            return
        seen.add(key)
        entry = {"filename": path.name, "source": source, "display": path.stem,
                 "valid": True, "error": None, "strategy_name": None}
        try:
            strat = resolve_strategy(source, path.name)
            entry["strategy_name"] = getattr(strat, "name", path.stem)
        except Exception as e:
            entry["valid"] = False
            entry["error"] = str(e)
        items.append(entry)

    if PKG_STRAT_DIR.exists():
        for p in sorted(PKG_STRAT_DIR.glob("*.py")):
            _add(p, "builtin")
    if STRAT_DIR.exists():
        for p in sorted(STRAT_DIR.glob("*.py")):
            _add(p, "user")
    return items


# --------------------------------------------------------------------------
# Backtest streaming
# --------------------------------------------------------------------------

async def stream_backtest(ws, conn_id: int, msg: dict) -> None:
    city   = msg.get("city")
    series = msg.get("series")
    d0 = date.fromisoformat(msg["start_date"])
    d1 = date.fromisoformat(msg["end_date"])

    # resolve the strategy: prefer a library reference, fall back to inline upload
    ref = msg.get("strategy_ref")
    try:
        if ref:
            strategy = resolve_strategy(ref["source"], ref["filename"])
        else:
            path = save_strategy_b64(msg["strategy_filename"], msg["strategy"])
            strategy = load_strategy(path)
    except Exception as e:
        await send(ws, {"type": "error", "message": f"strategy load failed: {e}"})
        return

    # per-run quantity override from the UI (contracts per trade; default 1)
    try:
        qty = max(1, int(msg.get("qty", 1)))
    except (TypeError, ValueError):
        qty = 1
    if hasattr(strategy, "quantity"):
        strategy.quantity = qty

    await send(ws, {"type": "log", "ts": d0.isoformat(), "level": "info",
                    "message": f"Loaded {strategy.name}  (qty={qty})"})

    # event-days in range
    try:
        all_days = build_event_days(DATA, city, series)
    except FileNotFoundError:
        await send(ws, {"type": "error", "message": f"no data for {city}/{series}"})
        return
    days = [d for d in all_days if d0 <= d.event_date_local <= d1]
    days.sort(key=lambda d: d.event_date_local)
    if not days:
        await send(ws, {"type": "error", "message": "no event-days in selected range"})
        return

    ledger = Ledger()
    all_trades = []
    seen_bar_tickers: set[str] = set()
    cum_pnl = 0.0
    cum_fee = 0.0
    total = len(days)

    for i, day in enumerate(days):
        if not _running.get(conn_id):
            await send(ws, {"type": "log", "ts": "", "level": "warn", "message": "stopped"})
            break

        trades = run_event(strategy, day, ledger)
        all_trades.extend(trades)

        # stream bars for any newly-traded contract (so candles meet the orders)
        for t in trades:
            if t.ticker not in seen_bar_tickers:
                seen_bar_tickers.add(t.ticker)
                strike = next((s.threshold for s in day.specs if s.ticker == t.ticker), None)
                # announce the contract so the UI can list it in the selector
                await send(ws, {
                    "type": "contract",
                    "ticker": t.ticker,
                    "strike": strike,
                    "date": day.event_date_local.isoformat(),
                    "label": (f"{day.event_date_local.isoformat()}  "
                              f"{t.ticker.split('-')[-1]}"
                              + (f"  ({strike:g}\u00b0)" if strike is not None else "")),
                })
                for b in day.bars_by_ticker.get(t.ticker, []):
                    if b.yes_bid_close is None and b.yes_ask_close is None and b.close is None:
                        continue
                    mid = b.mid if b.mid is not None else (b.close or 0.0)
                    await send(ws, {
                        "type": "bar",
                        "ticker": t.ticker,
                        "time": int(b.ts.timestamp()),
                        "open": b.open if b.open is not None else mid,
                        "high": b.high if b.high is not None else mid,
                        "low":  b.low  if b.low  is not None else mid,
                        "close": b.close if b.close is not None else mid,
                        "volume": b.volume,
                    })
            # the order marker
            if t.pnl is not None:
                cum_pnl += t.pnl
            cum_fee += t.fee
            await send(ws, {
                "type": "order",
                "ts": int(t.ts.timestamp()),
                "ticker": t.ticker,
                "market": t.ticker,
                "side": t.action,
                "price": round(t.fill_price, 4),
                "contracts": t.quantity,
                "fee": round(t.fee, 4),
                "cum_fee": round(cum_fee, 4),
                "cum_pnl": round(cum_pnl, 4),
            })
            await send(ws, {
                "type": "log", "ts": t.ts.isoformat(), "level": "signal",
                "message": f"{t.action} {t.quantity} {t.ticker} @ {t.fill_price:.2f} "
                           f"({t.reason}) → {t.result or 'unsettled'}",
            })

        # equity point for the P&L tab — realized cumulative PnL AFTER settlement
        if ledger.realized_curve:
            _, eq = ledger.realized_curve[-1]
            await send(ws, {"type": "pnl",
                            "label": day.event_date_local.isoformat(),
                            "value": round(eq, 4)})
        # cumulative fees point (parallel series for the Fees tab)
        await send(ws, {"type": "fees",
                        "label": day.event_date_local.isoformat(),
                        "value": round(cum_fee, 4)})

        await send(ws, {"type": "progress", "pct": round(100 * (i + 1) / total, 1)})
        await asyncio.sleep(0)  # yield to the event loop

    # final stats
    m = compute_metrics(all_trades, ledger.equity_curve)
    await send(ws, {
        "type": "stats",
        "n_trades": m.n_trades,
        "win_rate": m.win_rate,
        "max_dd": m.max_drawdown,
        "sharpe": m.sharpe_per_trade,
        "total_pnl": m.total_pnl,
    })
    await send(ws, {"type": "done"})
    _running[conn_id] = False


# --------------------------------------------------------------------------
# Connection handling
# --------------------------------------------------------------------------

async def send(ws, obj: dict) -> None:
    await ws.send(json.dumps(obj))


async def handle(ws, path=None):
    # `path=None` keeps this compatible with BOTH old websockets (handler(ws, path))
    # and new websockets (handler(ws)). Without it, older versions crash on connect.
    conn_id = id(ws)
    _running[conn_id] = False
    log.info("client connected")
    # push the calendar + strategy library immediately
    try:
        cal = build_calendar()
        await send(ws, {"type": "calendar", "cities": cal})
        log.info("sent calendar: %s",
                 ", ".join(f"{c['key']}={c['n_days']}d" for c in cal))
    except Exception as e:
        log.exception("failed to build/send calendar")
        await send(ws, {"type": "error", "message": f"calendar failed: {e}"})
    try:
        strat_list = list_strategies()
        await send(ws, {"type": "strategies", "items": strat_list})
        log.info("sent strategies: %d", len(strat_list))
    except Exception:
        log.exception("failed to build/send strategies")

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            log.info("recv %s", t)

            if t == "calendar":
                await send(ws, {"type": "calendar", "cities": build_calendar()})

            elif t == "strategies":
                await send(ws, {"type": "strategies", "items": list_strategies()})

            elif t == "save_strategy":
                try:
                    p = save_strategy_b64(msg["filename"], msg["strategy"])
                    await send(ws, {"type": "saved", "ok": True, "path": str(p)})
                    log.info("saved strategy → %s", p.resolve())
                    # refresh the library so the new file appears in the sidebar
                    await send(ws, {"type": "strategies", "items": list_strategies()})
                except Exception as e:
                    log.exception("save_strategy failed")
                    await send(ws, {"type": "saved", "ok": False, "path": str(e)})

            elif t == "start":
                _running[conn_id] = True
                asyncio.create_task(stream_backtest(ws, conn_id, msg))

            elif t == "stop":
                _running[conn_id] = False

    except websockets.ConnectionClosed:
        pass
    finally:
        _running.pop(conn_id, None)
        log.info("client disconnected")


async def main() -> None:
    log.info("kbt server starting…")
    log.info("  cwd        : %s", Path.cwd())
    log.info("  data dir   : %s  (exists=%s)", DATA.resolve(), DATA.exists())
    log.info("  strategies : %s", STRAT_DIR.resolve())
    if not DATA.exists():
        log.warning("  !! data dir not found — run server.py from your repo root "
                    "(where ./data lives), or edit DATA in server.py")
    cal = build_calendar()
    for c in cal:
        log.info("  calendar   : %-12s %s  (%d days)", c["key"], c["series"], c["n_days"])
    log.info("listening on ws://%s:%d", HOST, PORT)
    async with websockets.serve(handle, HOST, PORT, max_size=8 * 1024 * 1024):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("bye")