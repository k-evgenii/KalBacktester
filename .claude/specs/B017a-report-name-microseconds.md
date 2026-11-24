# B017a — Unique report file names for run_all

Track: B. Approved by Track A (lead) on 2026-11-24, as a follow-up to B017.

## Goal
Two `backtest.run_all` calls within the same second must not overwrite each other's JSON
report. On the B017 live run, the rerun overwrote the live run's report because the name had
one-second resolution.

## Not built
No counter, no lock and no existence check. Microseconds in the name are enough for a
single-user CLI. Nothing else in `run_all.py` changes.

## Files
- `backtest/run_all.py` (change only the line that builds the timestamp, currently line 171)

## Interfaces
Report path: `backtest/reports/run_all_<YYYYmmddTHHMMSS.ffffffZ>.json`, for example
`run_all_20261124T130611.482913Z.json`.

## Hard parts
```python
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
```

## Agreed tests
Add to `tests/backtest/check_run_all.py`. Keep every existing test as it is.
11. Two CLI calls with `--dry`, run back to back against the restored copy, write two different
    report files. Both exist afterwards, and each file name matches
    `run_all_\d{8}T\d{6}\.\d{6}Z\.json`.

## Test files
- `tests/backtest/check_run_all.py` (add test 11 only)

## Legacy reference
none

## Done when
`uv run python -m tests.backtest.check_run_all` all PASS, including the new test 11.

If anything here is unclear or looks wrong: stop and report, do not guess.
