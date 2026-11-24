# 014 — Station names as data, and a rules-wording survey tool

Track: A ingestion. Status: APPROVED by the user 2026-11-24 (plan "Rules-text wording",
units 014–016).

## Goal
Store, per station, the phrases that identify it in Kalshi's rules text, so the QA gate (unit
015) can check every historical wording. Add a read-only script that lists every wording a
series has used, with the dates each covers, to fill those phrases for new stations.

## Not built
- No gate or loader change (unit 015), no numeric check (unit 016).
- No other stations: they get their names during the backfill, from the survey.
- The survey writes nothing to the database and nothing to disk except its printed output.

## Files
The implementer may create or change only:
- `db/schema.sql` — append at the end (below everything else)
- `db/seed_stations.sql` — add one `UPDATE` at the end
- `db/README.md` — one line in the "Weather tables" section about `rules_names`
- `api_probe/kalshi/survey_rules_wording.py` — new

## Hard parts (use as written)

`db/schema.sql`, appended:
```sql
-- ---------------------------------------------------------------------------
-- Station names in Kalshi's rules text (unit 014)
-- ---------------------------------------------------------------------------
ALTER TABLE station ADD COLUMN rules_names text[] NOT NULL DEFAULT '{}';
COMMENT ON COLUMN station.rules_names IS
    'Phrases that identify this station in Kalshi rules text, e.g. (CLINYC), Central Park, New York. '
    'A market''s rules_primary must contain at least one of them (case-sensitive). '
    'Filled by hand from the rules-wording survey.';
```

`db/seed_stations.sql`, appended (a rerun changes nothing):
```sql
UPDATE station
SET rules_names = ARRAY['(CLINYC)', 'Central Park, New York', 'New York City']
WHERE station_id = 'KNYC' AND rules_names = '{}';
```

`api_probe/kalshi/survey_rules_wording.py`:
- Run: `uv run python api_probe/kalshi/survey_rules_wording.py --series KXHIGHNY`.
- Standard library only; `User-Agent: kalbt-probe`; 0.6 s pause after each request; on 429 or
  5xx wait 10 s and retry, at most 3 times. Base URL `https://api.elections.kalshi.com/trade-api/v2`.
- Pages `GET /historical/markets?series_ticker=<s>&limit=1000&cursor=<c>` and then
  `GET /markets?series_ticker=<s>&limit=1000&cursor=<c>` until the cursor is empty. A market
  seen on both surfaces counts once (by ticker).
- Event day from the event ticker, part after the first `-`, parsed with `%y%b%d`
  (`KXHIGHNY-26NOV22` → 2026-11-22). Skip a market whose event ticker does not parse, and count
  the skips.
- Template of a rules text (strip dates, then the strike phrase only, so text after it such as
  "according to The Weather Company" stays visible):
```python
MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|"
          "December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
DATE = re.compile(r"\b(" + MONTHS + r")\.? \d{1,2}(st|nd|rd|th)?,? \d{4}")
STRIKE = re.compile(r",? ?is (strictly )?(greater|less|between|above|below)[^°]*°")  # strike phrase only, up to the degree sign

def template(rules):
    return STRIKE.sub(" <STRIKE>", DATE.sub("<DATE>", rules)).strip()
```
- Output: one line per distinct template, sorted by first event day:
  `<first day>  <last day>  <markets>  <template>`, then a final line
  `markets: <n>, skipped: <n>, templates: <n>, requests: <n>`.

## Agreed tests
`qa-tester` writes:
- **Schema** — extend `tests/db/test_weather_schema.sql` (run on a throwaway database as in
  `db/README.md`): a new station row gets `rules_names = '{}'`; `rules_names` cannot be NULL;
  test 6 (the seed) now also checks KNYC's `rules_names` equal the three phrases above, and a
  second run of the seed changes nothing. Update test 6's copy of the seed statements to match
  the new `db/seed_stations.sql`.
- **Survey** — `api_probe/kalshi/check_survey.py` runs the survey for KXHIGHNY as a subprocess
  (about 11 live requests; approved) and checks that templates exist whose **first** day and
  wording match:
  1. 2022-03-04, contains "Central Park, New York" and "Daily Climate Report";
  2. 2025-03-05, contains "Central Park, New York" and "Climatological Report (Daily)";
  3. 2026-08-14, contains "New York City for" and "The Weather Company", no "(CLINYC)";
  4. 2026-08-15, contains "New York City(CLINYC)";
  5. 2026-08-16, contains "New York City (CLINYC)";
  and that every template whose first day is 2022-03-04 or later contains `<DATE>` and
  `<STRIKE>` (one 2021 market writes its strike without a degree sign and is left unreduced on
  purpose: the survey shows odd wordings as they are). Last days are not checked (the series
  keeps growing).
- The lead applies the `ALTER` and the seed `UPDATE` to the live database and checks KNYC.

## Test files
- `tests/db/test_weather_schema.sql`
- `api_probe/kalshi/check_survey.py`

## Spec correction (lead, 2026-11-24)
The first version's STRIKE pattern removed everything from the strike phrase to the end of the
text, which also removed the source clause after it ("… fahrenheit according to The Weather
Company …"), so check 3 could not pass. Corrected above to cut only up to the degree sign.
The `<DATE>`/`<STRIKE>` check then applies only from 2022-03-04, because one 2021 market
(2021-09-10, "is strictly greater than 74,") has no degree sign.

## Legacy reference
none

## Done when
- schema tests on a throwaway database: `test_schema.sql` 63 PASS, `test_weather_schema.sql` all PASS
- `uv run python api_probe/kalshi/check_survey.py` exits 0
- live: `SELECT rules_names FROM station WHERE station_id = 'KNYC'` shows the three phrases

If anything here is unclear or looks wrong: stop and report, do not guess.
