# C09 — UI screen 4: weather against the market for one city and day

Track: C (UI lead). Fourth UI unit of the brief `.claude/specs/C04-ui-brief.md`.
STATUS: approved (overnight playbook: specs inside the queue count as user-approved).

## Goal
A read-only page per city and day, linked from the C08 day page, that puts the weather beside
the market: the official highs, the observed temperature through the climate day against the
day-ahead model forecasts, and a table of every model's forecast high at every lead day next to
the settled high and the winning bracket.

## Not built
No forecast skill statistics across days, no model selection, no HTMX, no maps, nothing written
to the database.

## Files
- `ui/weather.py` (new): the page and its chart
- `ui/serve.py` (edit: one route, nothing else)
- `ui/markets.py` (edit: a link "Weather →" on the C08 day page heading line, nothing else)
- `ui/static/app.css` (edit: append the rules below)

## Interfaces

Data only from Track A's `query.weather` and `query.market` (`from query import market as q,
weather as w`), connection first, lists of dicts:
- `w.observations(conn, event_ticker)` → `observed_at, temp_f` (temp_f may be NULL)
- `w.forecasts(conn, event_ticker, lead_day=None)` → `model, valid_time, lead_day, temp_f`
  (temp_f never NULL; ordered by model, lead_day, valid_time)
- `w.official(conn, event_ticker)` → `source, high_f, high_time_lst, low_f, issued_at`
- `q.event_days(conn, underlying_id)`, `q.ladder(conn, event_ticker)` (from C08)

Route: `/markets/<underlying_id>/<event_date>/weather` (same id and date rules as C08) →
`weather.weather_page(conn, underlying_id, event_date)`; no such event → 404.

```python
# ui/weather.py
def weather_page(conn, underlying_id: str, event_date: date) -> str | None
def temp_chart(observed: list[dict], forecasts: list[dict], settled_high) -> str
def forecast_highs(forecasts: list[dict]) -> dict[str, dict[int, Decimal]]   # model -> lead -> max temp_f
def winning_bracket(ladder: list[dict]) -> str     # bracket_label of the result 'yes' row, or "not settled"
```

Page (title "<city> · <date> · weather"; back link to the C08 day page), sections in order:
1. Heading line: event ticker (mono); "Settled high" = the `kalshi` official `high_f`, else the
   `nws_cli` one, else the missing mark, as `<n> °F`; "Observed max" = the maximum non-null
   observed `temp_f`, 1 decimal, as `<n> °F` (missing mark when none); "Winning bracket" =
   `winning_bracket`.
2. **Official values** table, one row per `official` row: Source (`kalshi` → "Kalshi
   settlement", `nws_cli` → "NWS climate report", other → the raw value, escaped), High °F,
   Time of high (`high_time_lst`, mono, missing mark when null), Low °F (missing mark when
   null), Issued (UTC `YYYY-MM-DD HH:MM`, mono). No rows → "No official values loaded."
3. **Temperature through the day**: `temp_chart(observations, forecasts at lead_day 1,
   settled_high)` then one sentence: "Observed temperature (blue) and each model's day-ahead
   forecast (grey); the dashed line is the settled high." No observations and no forecasts →
   "No weather loaded for this day."
4. **Forecast highs**: one table, rows = models (alphabetical); columns = Model, then Lead 0 …
   Lead 7. Cell = that model's
   maximum `temp_f` at that lead over the climate day, 1 decimal, right-aligned; missing mark
   when absent. A cell gets class `hit` when its displayed value (the maximum rounded half up
   to 1 decimal) rounds half up to a whole degree equal to the settled high: so `60.5` is a hit
   for 61 and `61.5` is not, whatever the unrounded value was (61.49 shows as `61.5`, no hit). One
   sentence under it: "Forecast high per model and lead day (0 = latest run); bold cells round
   to the settled high of <n> °F." (omit the second clause when there is no settled high).

## Hard parts

**forecast_highs** (as written):

```python
def forecast_highs(forecasts):
    """Maximum forecast temperature per model and lead day."""
    highs = {}
    for r in forecasts:
        by_lead = highs.setdefault(r["model"], {})
        if r["lead_day"] not in by_lead or r["temp_f"] > by_lead[r["lead_day"]]:
            by_lead[r["lead_day"]] = r["temp_f"]
    return highs
```

**temp_chart** (as written):

```python
def temp_chart(observed, forecasts, settled_high):
    """SVG: observed temperature line, one grey line per model forecast, dashed settled high."""
    width, height, pad_l, pad_r, pad_y = 640, 200, 40, 16, 12
    points = [(r["observed_at"], r["temp_f"]) for r in observed if r["temp_f"] is not None]
    points += [(r["valid_time"], r["temp_f"]) for r in forecasts]
    times = [t for t, _ in points]
    temps = [v for _, v in points] + ([settled_high] if settled_high is not None else [])
    t0, t1 = min(times), max(times)
    low, high = min(temps) - 2, max(temps) + 2
    span = (t1 - t0).total_seconds() or 1

    def x(t):
        return pad_l + (width - pad_l - pad_r) * (t - t0).total_seconds() / span

    def y(v):
        return pad_y + (height - 2 * pad_y) * float((high - v) / (high - low))

    def line(rows, time_key, css):
        pts = " ".join(f"{x(r[time_key]):.1f},{y(r['temp_f']):.1f}"
                       for r in rows if r["temp_f"] is not None)
        return f'<polyline class="{css}" points="{pts}"/>' if pts else ""

    by_model = {}
    for r in forecasts:
        by_model.setdefault(r["model"], []).append(r)
    parts = [line(rows, "valid_time", "t-forecast") for rows in by_model.values()]
    parts.append(line(observed, "observed_at", "t-observed"))
    if settled_high is not None:
        parts.append(f'<line class="t-settled" x1="{pad_l}" y1="{y(settled_high):.1f}" '
                     f'x2="{width - pad_r}" y2="{y(settled_high):.1f}"/>')
    axis = (f'<text class="t-axis" x="4" y="{y(high - 2) + 4:.1f}">{esc(f"{high - 2:.0f}")}°</text>'
            f'<text class="t-axis" x="4" y="{y(low + 2) + 4:.1f}">{esc(f"{low + 2:.0f}")}°</text>')
    return (f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Temperature through the climate day">{axis}{"".join(parts)}</svg>')
```

CSS to append (as written):

```css
.t-observed { fill: none; stroke: var(--accent); stroke-width: 2; }
.t-forecast { fill: none; stroke: var(--muted); stroke-width: 1; opacity: 0.6; }
.t-settled { stroke: var(--text); stroke-width: 1; stroke-dasharray: 4 3; }
.t-axis { fill: var(--muted); font: 11px var(--mono); }
td.hit { font-weight: 700; }
```

## Agreed tests

`tests/ui/check_weather_page.py`, house style, `PYTHONPATH=.`. Throwaway database
`kalbt_ui_weather_<pid>` (dropped first and at the end): `db/schema.sql`, `db/portfolio.sql`,
then a hand fixture: station `KNYC` (std_utc_offset_h −5), underlying `NYC_HIGH`, series
`KXHIGHNY`, event `EV-0601` on 2026-06-01 with three markets (less 60, between 60–61, greater
61; between settled yes); `official_daily` kalshi 61 and nws_cli 61 ('300 PM', low 54);
observations at 2026-06-01 06:00, 12:00, 18:00 UTC (55, 59, 61) and one with NULL temp at
20:00; forecasts for two models (`gfs_seamless`, `ecmwf_ifs025`) at lead 0 and 1, hourly 06:00
to 18:00 UTC, with known maxima (gfs lead 1 max 60.6, ecmwf lead 1 max 62.0, gfs lead 0 max
61.2, ecmwf lead 0 max 58.0). A second event `EV-0602` on 2026-06-02 with markets but no
weather and no official values. Server via `make_server(0, db)`.

1. `/markets/NYC_HIGH/2026-06-01/weather` → 200; heading shows `61 °F` and `60–61 °F`.
2. Official table: two rows, "Kalshi settlement" and "NWS climate report"; time `300 PM`; low
   `54` for NWS and the missing mark for Kalshi.
3. Chart: one `t-observed` polyline with 3 points (the NULL skipped), two `t-forecast`
   polylines (lead 1 only), one `t-settled` line; every point inside 0..640 × 0..200.
4. Forecast highs: rows `ecmwf_ifs025` then `gfs_seamless`; gfs lead 1 `60.6` and lead 0 `61.2`
   have class `hit` (both round to 61); ecmwf lead 1 `62.0` and lead 0 `58.0` do not; leads 2–7
   show the missing mark. The heading shows "Observed max" `61.0 °F`.
5. `forecast_highs` on the fixture rows equals the hand maxima above; on `[]` returns `{}`.
6. `winning_bracket`: ladder with a `yes` row → its label; none → `not settled`.
7. `/markets/NYC_HIGH/2026-06-02/weather` → 200 with "No official values loaded." and "No
   weather loaded for this day."; settled high shows the missing mark.
8. 404: `/markets/NYC_HIGH/2026-07-01/weather`, `/markets/XXX/2026-06-01/weather`.
9. The C08 day page `/markets/NYC_HIGH/2026-06-01` contains `href="/markets/NYC_HIGH/2026-06-01/weather"`.
10. C05, C06 and C08 suites still pass unchanged.
11. Live `kalbt` row counts in `run`, `order_intent`, `order_outcome` unchanged; by hand only
    (not asserted on counts): a server on live returns 200 for
    `/markets/NYC_HIGH/2026-11-22/weather` with 9 model rows in the forecast-highs table.

## Test files
- `tests/ui/check_weather_page.py` (new)

## Legacy reference
- `legacy/01_forecast_exploration.ipynb` (forecast vs observed exploration).
- Design (read-only): `/home/kondr/KalBacktester/.claude/agents/daylight-graphite.md` (grey out
  context series; one sentence per chart; tables for comparison with right-aligned numbers).

## Done when
- `PYTHONPATH=. uv run python tests/ui/check_weather_page.py` exits 0, only `[PASS]` lines.
- The other three UI suites still exit 0; `uvx ruff check ui/ tests/ui/` clean; clean-export
  run before READY.

Corrected after QA round 1: `hit` is decided from the displayed 1-decimal value, so the same
printed number is always styled the same way.

If anything here is unclear or looks wrong: stop and report, do not guess.
