"""Checks on the rules-wording survey for KXHIGHNY (spec 014, agreed tests).

Run: uv run python api_probe/kalshi/check_survey.py
Runs survey_rules_wording.py as a subprocess (about 11 live read-only requests) and checks
the templates it prints. Last days are not checked: the series keeps growing.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SURVEY = ROOT / "api_probe" / "kalshi" / "survey_rules_wording.py"
SUMMARY = re.compile(r"^markets: (\d+), skipped: (\d+), templates: (\d+), requests: (\d+)$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# (label, first day, must contain, must not contain)
FIRST_DAY_CHECKS = [
    ("2022-03-04 Daily Climate Report wording", "2022-03-04",
     ["Central Park, New York", "Daily Climate Report"], []),
    ("2025-03-05 Climatological Report wording", "2025-03-05",
     ["Central Park, New York", "Climatological Report (Daily)"], []),
    ("2026-08-14 Weather Company wording without (CLINYC)", "2026-08-14",
     ["New York City for", "The Weather Company"], ["(CLINYC)"]),
    ("2026-08-15 wording 'New York City(CLINYC)'", "2026-08-15",
     ["New York City(CLINYC)"], []),
    ("2026-08-16 wording 'New York City (CLINYC)'", "2026-08-16",
     ["New York City (CLINYC)"], []),
]

results = []


def report(label, problems):
    if problems:
        print(f"[FAIL] {label}: {'; '.join(problems)}")
        results.append(False)
    else:
        print(f"[PASS] {label}")
        results.append(True)


def run_survey():
    proc = subprocess.run(
        [sys.executable, str(SURVEY), "--series", "KXHIGHNY"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"survey exited with {proc.returncode}\n{proc.stderr}")
        sys.exit(1)
    return proc.stdout


def parse(output):
    """Return (templates, summary). templates is a list of (first, last, markets, text)."""
    templates = []
    summary = None
    for line in output.splitlines():
        if not line.strip():
            continue
        match = SUMMARY.match(line)
        if match:
            summary = tuple(int(g) for g in match.groups())
            continue
        parts = line.split("  ", 3)
        if len(parts) != 4 or not DAY.match(parts[0]) or not DAY.match(parts[1]):
            print(f"unparsable line: {line[:120]}")
            sys.exit(1)
        templates.append((parts[0], parts[1], int(parts[2]), parts[3]))
    return templates, summary


templates, summary = parse(run_survey())

report("output has template lines and a summary line",
       [] if templates and summary else ["missing templates or summary"])

for label, first_day, must_have, must_not_have in FIRST_DAY_CHECKS:
    candidates = [t for t in templates if t[0] == first_day]
    problems = []
    if not candidates:
        problems.append(f"no template with first day {first_day}")
    elif not any(all(p in t[3] for p in must_have) and not any(p in t[3] for p in must_not_have)
                 for t in candidates):
        problems.append(f"no template on {first_day} has {must_have} and lacks {must_not_have}")
    report(label, problems)

# Every template from 2022-03-04 on has both placeholders (ISO dates compare as text).
problems = [f"{t[0]}: {t[3][:80]}" for t in templates
            if t[0] >= "2022-03-04" and ("<DATE>" not in t[3] or "<STRIKE>" not in t[3])]
report("every template from 2022-03-04 on has <DATE> and <STRIKE>", problems)

# Extra: summary agrees with the lines, and nothing was skipped.
if summary:
    markets, skipped, count, _requests = summary
    problems = []
    if count != len(templates):
        problems.append(f"summary says {count} templates, printed {len(templates)}")
    if markets != sum(t[2] for t in templates):
        problems.append(f"summary says {markets} markets, lines add up to {sum(t[2] for t in templates)}")
    if skipped != 0:
        problems.append(f"{skipped} markets skipped")
    report("summary agrees with the template lines, no skips", problems)

# Extra: lines are sorted by first day.
firsts = [t[0] for t in templates]
report("templates are sorted by first day", [] if firsts == sorted(firsts) else ["not sorted"])

sys.exit(0 if all(results) else 1)
