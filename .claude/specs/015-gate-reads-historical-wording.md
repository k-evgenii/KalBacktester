# 015 — The QA gate and the market loader read every historical wording

Track: A ingestion. Status: APPROVED by the user 2026-11-24 (plan "Rules-text wording",
units 014–016). Track B was asked not to edit the gate files until this is merged
(`.claude/handoffs/track-b-wording-note.md`).

## Goal
Gate check 22 accepts any of the station's `rules_names` (unit 014) instead of only the CLI
code; gate check 23 reads which source the rules text names and checks it against the date; the
market loader stores that source per event. Then two older New York weeks are dumped and loaded,
proving that pre-switch markets now pass.

## Not built
- No numeric station check (unit 016), no weather for the new weeks (unit 016).
- No other series or stations; no new check ids.

## Files
The implementer may change only:
- `ingest/load_kxhighny_dump.py` — `TWC_FROM`, `settlement_source()`, source per event
- `ingest/qa_kxhighny_dump.py` — checks 22 and 23
- `api_probe/kalshi/dump_kxhighny.py` — optional `--out` folder (default unchanged)
- `.gitignore` — the dump folder pattern below
and create the dump folders listed under "Real runs" (git-ignored).

## Hard parts (use as written)

In `ingest/load_kxhighny_dump.py` (the gate already imports from this module, so the function
lives here and the gate imports it; no new module):
```python
TWC_FROM = date(2026, 8, 14)  # first event day Kalshi settles daily highs on The Weather Company

def settlement_source(rules):
    """The source a rules text names: 'The Weather Company' or 'National Weather Service'.
    None when it names neither or both."""
    twc = "The Weather Company" in rules
    nws = "National Weather Service" in rules
    if twc == nws:
        return None
    return "The Weather Company" if twc else "National Weather Service"
```
Loader: each event's `settlement_source` = `settlement_source(rules_primary)` of the event's
first market (sorted by ticker); if it is None the loader stops with an error (the gate runs
first and would already have blocked). Existing rows are untouched (`ON CONFLICT DO NOTHING`).

Gate check 22, "rules name the station":
```
SELECT s.station_id, s.cli_pil, s.rules_names FROM series se
  JOIN underlying u ON u.underlying_id = se.underlying_id
  JOIN station s    ON s.station_id = u.station_id
 WHERE se.ticker = 'KXHIGHNY'
no row               -> fail, detail unchanged ("station for KXHIGHNY unknown: ...")
rules_names is empty -> fail, detail "station <id> has no rules_names: fill them from the survey"
otherwise, per market (lead's correction, see below):
    codes = re.findall(r"\(CLI[A-Z]{3}\)", rules_primary)
    if codes:  a code is present -> every code found must equal "(" + cli_pil + ")"
               (s.cli_pil is selected alongside rules_names)
    else:      no code -> at least one of rules_names must occur (case-sensitive)
```
Gate check 23, renamed "rules name the settlement source for the date":
```
for each market:
    source   = settlement_source(rules_primary)
    expected = 'The Weather Company' if event_date(event_ticker) >= TWC_FROM else 'National Weather Service'
    source is None      -> problem "<ticker>: no single known source in rules"
    source != expected  -> problem "<ticker>: names <source>, event day <d> expects <expected>"
```
Both stay BLOCK and keep their ids; a missing `rules_primary` counts as an empty text.

Dump script: `--out` optional; when given, everything is written there instead of
`api_probe/kalshi/KXHIGHNY/`.

`.gitignore`: replace the line `api_probe/kalshi/KXHIGHNY/` with `api_probe/kalshi/KXHIGHNY*/`.

## Real runs (implementer, live API and live database; approved)
1. `uv run python api_probe/kalshi/dump_kxhighny.py --start 2026-06-01 --end 2026-06-07 --out api_probe/kalshi/KXHIGHNY_2026-06-01_2026-06-07`
2. `uv run python api_probe/kalshi/dump_kxhighny.py --start 2026-08-12 --end 2026-08-18 --out api_probe/kalshi/KXHIGHNY_2026-08-12_2026-08-18`
3. The gate on the November dump and on both new dumps: all three PASS.
4. Load both new dumps: `uv run python -m ingest.load_kxhighny_dump <folder>`; a second run of
   each inserts nothing.
If the gate blocks a real dump, stop and report the failing checks with examples: do not change
checks or data to make it pass.

## Agreed tests
`qa-tester` extends `tests/ingest/check_qa_kxhighny_dump.py` (all existing tests keep passing,
check ids unchanged) and writes `tests/ingest/check_wording.py`:
1. `settlement_source` on four strings: TWC only → TWC; NWS only → NWS; both → None; neither → None.
2. The gate PASSes on each of the three real dumps, with checks 22 and 23 `pass`.
3. Tampered copies (temporary folders, real dumps untouched):
   a. November copy, one market's rules with `(CLINYC)` and `New York City` replaced by
      `(CLIXYZ)` and `Gotham` → BLOCK, check 22;
   b. November copy, one market's `The Weather Company` replaced by `National Weather Service`
      → BLOCK, check 23 (wrong source for the date);
   c. November copy, one market's rules with ` National Weather Service` appended → BLOCK, 23
      (both sources);
   d. June copy, one market's `National Weather Service` replaced by `The Weather Company`
      → BLOCK, check 23.
4. In the live database after the loads: every event from 2026-06-01 to 2026-08-13 has
   `settlement_source = 'National Weather Service'`, every event from 2026-08-14 on has
   `'The Weather Company'` (the November week included); 21 events in total.
5. For every loaded event, the market with `result = 'yes'` is consistent with
   `expiration_value` in its dump (the strike rule), and each event has exactly 6 markets.

## Test files
- `tests/ingest/check_qa_kxhighny_dump.py`
- `tests/ingest/check_wording.py`
- `tests/ingest/check_load_kxhighny_dump.py` — **scope it to its dump** (lead's addition): its row
  counts and comparisons currently assume the database holds only the November week, so they
  break once more weeks are loaded. Count and compare only the events, markets and candles whose
  tickers appear in the November dump (`underlying` and `series` stay at 1 each). Nothing else in
  that file changes and no check is weakened.

## Spec correction (lead, 2026-11-24)
The first version passed a market when any accepted name occurred. Because `New York City` is
accepted (for the 14 Aug 2026 wording without a code), a text such as "New York City (CLIMDW)"
passed, and the existing gate test 17 (code replaced by `(CLIMDW)`) failed. Corrected: when a CLI
code is present it must be the station's own code; the name list applies only to texts without a
code. This is stricter than before, in line with the user's "keep the station check strict".
Existing test 17 must pass unchanged.

## Legacy reference
none

## Done when
- both real dumps exist; the gate PASSes on all three dumps
- `uv run python -m tests.ingest.check_qa_kxhighny_dump` and
  `uv run python -m tests.ingest.check_wording` exit 0
- `uv run python -m tests.ingest.check_load_kxhighny_dump` still exits 0

If anything here is unclear or looks wrong: stop and report, do not guess.
