#!/usr/bin/env python3
"""
verify_forecasts.py — audit the forecast matrix before anything is modelled on it.

    python verify_forecasts.py
    python verify_forecasts.py --city new_york --lead 1

Four checks, in order of how badly they'd hurt if skipped:

1. COVERAGE      what fraction of expected hours arrived, per (city, model, lead)
2. LEAD SEMANTICS  does forecast error grow with lead time?  <-- the critical one
3. GAPS          missing hours, and whether they cluster
4. n_eff PREVIEW  early read on inter-model error correlation

Check 2 is the one that can silently invalidate everything. If `_previous_day7`
were actually a SHORTER lead than `_previous_day1`, every model would still fit,
every metric would still compute, and every conclusion would be backwards. The
test is simple: deviation from lead 0 must increase monotonically with lead.

CAVEAT ON CHECK 4
-----------------
It uses lead 0 (near-analysis) as pseudo-truth because CLI truth isn't joined
in yet. Each model's lead-0 field carries that model's own bias, so errors
measured this way are contaminated by shared model structure and will
UNDERSTATE the true spread. Treat the n_eff number as an order of magnitude,
not an estimate. The real one needs the CLI daily max.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

SRC = "data/processed/forecasts/hourly_temp.parquet"


def load(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"not found: {p}\nRun fetch_forecasts.py first.")
    df = pd.read_parquet(p)
    df["valid_time_utc"] = pd.to_datetime(df["valid_time_utc"], utc=True)
    return df


def check_coverage(df: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("1. COVERAGE — hours present per (city, model, lead)")
    print("=" * 72)
    span = df.valid_time_utc.max() - df.valid_time_utc.min()
    expected = int(span.total_seconds() // 3600) + 1
    print(f"window {df.valid_time_utc.min()} .. {df.valid_time_utc.max()}"
          f"   expected={expected}h\n")

    g = (df.groupby(["city", "model", "lead_day"])
           .valid_time_utc.nunique().rename("hours").reset_index())
    g["pct"] = (100 * g.hours / expected).round(1)

    piv = g.pivot_table(index=["city", "model"], columns="lead_day", values="pct")
    with pd.option_context("display.width", 160, "display.max_rows", 200):
        print(piv.fillna(0).round(0).astype(int).to_string())

    bad = g[(g.pct < 99.0)]
    if len(bad):
        print(f"\n  {len(bad)} (city,model,lead) combos below 99% complete:")
        for _, r in bad.head(15).iterrows():
            print(f"    {r.city:12} {r.model:22} d{int(r.lead_day)}  "
                  f"{int(r.hours):>4}/{expected}  ({r.pct}%)")
        if len(bad) > 15:
            print(f"    ... and {len(bad) - 15} more")


def check_lead_semantics(df: pd.DataFrame) -> bool:
    print("\n" + "=" * 72)
    print("2. LEAD SEMANTICS — mean |forecast - lead0|, by lead. MUST increase.")
    print("=" * 72)
    ok_all = True
    for city, sub in df.groupby("city"):
        p = sub.pivot_table(index=["model", "valid_time_utc"],
                            columns="lead_day", values="temp_f")
        if 0 not in p.columns:
            print(f"  {city}: no lead-0 series, cannot validate")
            ok_all = False
            continue
        dev = p.drop(columns=[0]).sub(p[0], axis=0).abs().mean().round(2)
        dev = dev.dropna()
        mono = dev.is_monotonic_increasing
        ok_all &= bool(mono)
        flag = "OK" if mono else "*** NOT MONOTONIC ***"
        print(f"\n  {city:12} {flag}")
        for lead, v in dev.items():
            print(f"    lead {int(lead)}  MAD vs lead0 = {v:>5.2f} F")
    if not ok_all:
        print("\n  A non-monotonic result means the _previous_dayN suffix does not")
        print("  mean what we assumed. STOP and re-check before modelling.")
    return ok_all


def check_gaps(df: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("3. GAPS — missing hours within each series")
    print("=" * 72)
    rows = []
    for (city, model, lead), sub in df.groupby(["city", "model", "lead_day"]):
        t = sub.valid_time_utc.sort_values()
        if len(t) < 2:
            continue
        d = t.diff().dropna()
        gaps = d[d > pd.Timedelta("1h")]
        if len(gaps):
            rows.append({"city": city, "model": model, "lead": int(lead),
                         "n_gaps": len(gaps),
                         "missing_h": int((gaps.sum().total_seconds() // 3600)
                                          - len(gaps)),
                         "largest_h": int(gaps.max().total_seconds() // 3600)})
    if not rows:
        print("  no gaps — every series is hourly-contiguous")
        return
    r = pd.DataFrame(rows).sort_values("missing_h", ascending=False)
    print(r.to_string(index=False))
    print("\n  Gaps matter for plateau width and peak hour: an hour missing near")
    print("  the daily max silently changes both. Interpolate deliberately or")
    print("  exclude the day — do not let a groupby().max() paper over it.")


def check_neff(df: pd.DataFrame, lead: int) -> None:
    print("\n" + "=" * 72)
    print(f"4. n_eff PREVIEW — inter-model error correlation at lead {lead}")
    print("=" * 72)
    print("  pseudo-truth = lead 0. Contaminated by shared model bias;")
    print("  this UNDERSTATES spread. Order of magnitude only.\n")

    for city, sub in df.groupby("city"):
        p = sub.pivot_table(index="valid_time_utc", columns=["model", "lead_day"],
                            values="temp_f")
        models = sorted({m for m, l in p.columns if l == lead}
                        & {m for m, l in p.columns if l == 0})
        if len(models) < 2:
            print(f"  {city}: fewer than 2 models at lead {lead}, skipping")
            continue
        err = pd.DataFrame({m: p[(m, lead)] - p[(m, 0)] for m in models}).dropna()
        if len(err) < 24:
            print(f"  {city}: only {len(err)} complete hours, skipping")
            continue

        C = err.corr()
        n = len(models)
        off = C.values[~np.eye(n, dtype=bool)]
        rho = float(np.mean(off))
        n_eff = n / (1 + (n - 1) * rho) if rho > -1 / (n - 1) else float("inf")

        print(f"  {city:12} n_models={n}  hours={len(err)}  "
              f"mean_rho={rho:.3f}  n_eff={n_eff:.2f}")
        print(f"    naive iid would claim precision {np.sqrt(n / n_eff):.1f}x "
              f"too high\n")
        with pd.option_context("display.width", 160):
            print(C.round(2).to_string())
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit the forecast matrix.")
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--city", help="restrict to one city")
    ap.add_argument("--lead", type=int, default=1, help="lead day for check 4")
    args = ap.parse_args()

    df = load(args.src)
    if args.city:
        df = df[df.city == args.city]
        if df.empty:
            raise SystemExit(f"no rows for city {args.city!r}")

    print(f"loaded {len(df):,} rows  "
          f"cities={sorted(df.city.unique())}  "
          f"models={df.model.nunique()}  leads={sorted(df.lead_day.unique())}")

    check_coverage(df)
    semantics_ok = check_lead_semantics(df)
    check_gaps(df)
    check_neff(df, args.lead)

    print("\n" + "=" * 72)
    print("VERDICT:", "matrix looks usable" if semantics_ok
          else "*** LEAD SEMANTICS FAILED — do not model on this ***")
    print("=" * 72)
    return 0 if semantics_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
