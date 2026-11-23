#!/usr/bin/env python3
"""
build_dataset.py — join the forecast matrix to CLI settlement truth.

    data/processed/dataset/daily_long.parquet
        one row per (city, date, model, lead_day)
        forecast features + truth + errors.  Use for: skill by model,
        bias vector b, per-model diagnostics.

    data/processed/dataset/daily_wide.parquet
        one row per (city, date, lead_day)
        one column per model + ensemble summaries + truth.
        This is `m` from the inverse problem — the vector of model opinions
        for one day. Use for: the error covariance Sigma, n_eff, EMOS/BMA.

INPUTS
    data/processed/forecasts/hourly_temp.parquet   (fetch_forecasts.py)
    data/processed/weather/<city>.json             (fetch_weather.py)

WHAT IT COMPUTES PER (city, date, model, lead)
    fc_max_f      daily max of the hourly forecast, over the LOCAL calendar day
    fc_peak_hour  local hour of that max (float, 0-24)
    fc_w          plateau width: hours within PLATEAU_TOL_F of the max
    fc_a          asymmetry: T(peak+3h) - T(peak-3h). Positive = slow cooling.
    n_hours       completeness; days below MIN_HOURS are dropped, not patched

    e_T           T_cli - fc_max_f          (positive = forecast too cold)
    e_H           wrapped(H_cli - fc_peak_hour) in [-12, +12)

THREE THINGS TO KNOW BEFORE YOU TRUST THIS
------------------------------------------
1. CONSTANT LEAD, NOT CONSTANT ISSUE TIME.
   The previous-runs API gives a series where each HOUR was forecast N days
   ahead. So a daily max assembled from lead-1 hours blends issue times across
   a 24h span. That is correct for measuring model skill and error covariance
   -- which is what the density model needs -- but it is NOT "the forecast as
   it stood at 10:00 the day before". For decision-time backtesting you need
   the Single Runs API keyed by init time. Do not use this file to justify a
   fill.

2. LOCAL CALENDAR DAY.
   CLI reports the max over the local midnight-to-midnight day. Forecast
   timestamps are UTC. The conversion happens here; get it wrong and you
   silently compare different 24h windows.

3. e_T IS NOT PURE MODEL ERROR.
   It bundles model error WITH the grid-vs-station offset (the model forecasts
   a grid cell; CLI measures one instrument at one point). Separating them is
   the point of the bias vector b -- do not read the mean of e_T as "the model
   is bad".

    python build_dataset.py
    python build_dataset.py --cities new_york --min-hours 20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TZ = {
    "new_york":    "America/New_York",
    "los_angeles": "America/Los_Angeles",
    "miami":       "America/New_York",
}

PLATEAU_TOL_F = 1.0     # "within c degrees of the max" -> plateau width w
ASYM_OFFSET_H = 3       # hours either side of peak for the asymmetry measure
MIN_HOURS     = 20      # a day with fewer forecast hours is dropped

FC_SRC   = "data/processed/forecasts/hourly_temp.parquet"
TRUTH_DIR = "data/processed/weather"
OUT_DIR   = "data/processed/dataset"


# ---------------------------------------------------------------------------
# Truth
# ---------------------------------------------------------------------------

def load_truth(truth_dir: str, cities: list[str]) -> pd.DataFrame:
    """CLI daily max. BLOCKED days are dropped -- they are not truth."""
    rows = []
    for city in cities:
        p = Path(truth_dir) / f"{city}.json"
        if not p.exists():
            print(f"  ! no truth cache for {city}: {p}")
            continue
        for iso, rec in json.loads(p.read_text()).items():
            if rec.get("status") != "OK":
                continue
            h = np.nan
            if rec.get("max_time_utc"):
                t = pd.Timestamp(rec["max_time_utc"]).tz_convert(TZ[city])
                h = t.hour + t.minute / 60.0
            rows.append({
                "city": city,
                "date": pd.Timestamp(iso).date(),
                "T_cli": float(rec["max_f"]),
                "H_cli": h,
                "time_confidence": rec.get("time_confidence", "none"),
                "record_flag": bool(rec.get("record_flag", False)),
            })
    if not rows:
        raise SystemExit("no usable truth rows -- run fetch_weather.py first")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Forecast curve -> daily features
# ---------------------------------------------------------------------------

def curve_features(g: pd.DataFrame) -> pd.Series:
    """One local day of hourly forecast -> the shape summaries.

    g must be sorted by local time and carry columns [hour, temp_f].
    """
    t = g["temp_f"].to_numpy()
    h = g["hour"].to_numpy()
    i = int(np.argmax(t))
    tmax = float(t[i])
    peak = float(h[i])

    w = int(np.sum(t >= tmax - PLATEAU_TOL_F))

    def at(target_hour: float) -> float:
        if target_hour < h.min() or target_hour > h.max():
            return np.nan
        return float(np.interp(target_hour, h, t))

    before = at(peak - ASYM_OFFSET_H)
    after  = at(peak + ASYM_OFFSET_H)
    a = after - before if np.isfinite(before) and np.isfinite(after) else np.nan

    return pd.Series({
        "fc_max_f": tmax,
        "fc_peak_hour": peak,
        "fc_w": w,
        "fc_a": a,
        "fc_min_f": float(np.min(t)),
        "n_hours": int(len(t)),
    })


def daily_from_hourly(fc: pd.DataFrame, min_hours: int) -> pd.DataFrame:
    out = []
    for city, sub in fc.groupby("city", sort=False):
        tz = ZoneInfo(TZ[city])
        loc = sub.copy()
        lt = loc["valid_time_utc"].dt.tz_convert(tz)
        loc["date"] = lt.dt.date
        loc["hour"] = lt.dt.hour + lt.dt.minute / 60.0
        loc = loc.sort_values(["model", "lead_day", "date", "hour"])
        feats = (loc.groupby(["model", "lead_day", "date"], sort=False)
                    .apply(curve_features, include_groups=False)
                    .reset_index())
        feats.insert(0, "city", city)
        out.append(feats)
    df = pd.concat(out, ignore_index=True)

    n_before = len(df)
    df = df[df.n_hours >= min_hours].copy()
    dropped = n_before - len(df)
    if dropped:
        print(f"  dropped {dropped} (city,model,lead,date) days with "
              f"< {min_hours} forecast hours")
    return df


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def wrap_hours(x: pd.Series) -> pd.Series:
    """Signed circular difference into [-12, +12)."""
    return ((x + 12.0) % 24.0) - 12.0


def build_long(fc_daily: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    df = fc_daily.merge(truth, on=["city", "date"], how="inner")
    df["e_T"] = df["T_cli"] - df["fc_max_f"]
    df["e_H"] = wrap_hours(df["H_cli"] - df["fc_peak_hour"])
    return df.sort_values(["city", "date", "lead_day", "model"]).reset_index(drop=True)


def build_wide(long: pd.DataFrame) -> pd.DataFrame:
    """One row per (city, date, lead): the model-opinion vector + ensemble stats."""
    piv = long.pivot_table(index=["city", "date", "lead_day"],
                           columns="model", values="fc_max_f")
    piv.columns = [f"fc_{c}" for c in piv.columns]
    piv = piv.reset_index()

    cols = [c for c in piv.columns if c.startswith("fc_")]
    piv["ens_n"]    = piv[cols].notna().sum(axis=1)
    piv["ens_mean"] = piv[cols].mean(axis=1)
    piv["ens_sd"]   = piv[cols].std(axis=1, ddof=1)      # s_T
    piv["ens_min"]  = piv[cols].min(axis=1)
    piv["ens_max"]  = piv[cols].max(axis=1)
    piv["ens_range"] = piv["ens_max"] - piv["ens_min"]

    # peak-hour disagreement (s_H) and mean plateau width, across models
    aux = (long.groupby(["city", "date", "lead_day"])
               .agg(s_H=("fc_peak_hour", "std"),
                    w_mean=("fc_w", "mean"),
                    a_mean=("fc_a", "mean"),
                    T_cli=("T_cli", "first"),
                    H_cli=("H_cli", "first"))
               .reset_index())

    out = piv.merge(aux, on=["city", "date", "lead_day"], how="left")
    out["e_ens_mean"] = out["T_cli"] - out["ens_mean"]
    return out.sort_values(["city", "date", "lead_day"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(long: pd.DataFrame, wide: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("BIAS AND SKILL BY MODEL, lead 1  (e_T = T_cli - forecast)")
    print("=" * 72)
    print("positive bias = the forecast runs COLD vs the station\n")
    d1 = long[long.lead_day == 1]
    for city, sub in d1.groupby("city"):
        s = (sub.groupby("model")
                .agg(n=("e_T", "size"), bias=("e_T", "mean"),
                     sd=("e_T", "std"), mae=("e_T", lambda x: x.abs().mean()))
                .round(2).sort_values("mae"))
        print(f"  {city}")
        print(s.to_string().replace("\n", "\n  "))
        print()

    print("=" * 72)
    print("ENSEMBLE MEAN vs BEST SINGLE MODEL, by lead")
    print("=" * 72)
    print("if the blend does not beat the best member, the edge is not here\n")
    for city, sub in long.groupby("city"):
        best = (sub.groupby(["lead_day", "model"]).e_T
                   .apply(lambda x: x.abs().mean())
                   .groupby("lead_day").min().round(2))
        ens = (wide[wide.city == city].groupby("lead_day").e_ens_mean
                   .apply(lambda x: x.abs().mean()).round(2))
        cmp = pd.DataFrame({"best_single_mae": best, "ens_mean_mae": ens})
        cmp["ens_wins_by"] = (cmp.best_single_mae - cmp.ens_mean_mae).round(2)
        print(f"  {city}")
        print(cmp.to_string().replace("\n", "\n  "))
        print()

    print("=" * 72)
    print("PEAK-HOUR ERROR, lead 1  (e_H = H_cli - forecast peak, hours)")
    print("=" * 72)
    eh = (d1.groupby("city").e_H
            .agg(n="size", bias="mean", sd="std",
                 mad=lambda x: x.abs().mean()).round(2))
    print(eh.to_string())
    print("\nCLI peak time is Local STANDARD Time year-round -- in June that is")
    print("one hour off wall clock. weather.py converts it; if the bias here is")
    print("suspiciously close to -1.0 or +1.0, check that conversion first.")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Join forecasts to CLI truth.")
    ap.add_argument("--fc", default=FC_SRC)
    ap.add_argument("--truth-dir", default=TRUTH_DIR)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--cities", nargs="*", default=list(TZ))
    ap.add_argument("--min-hours", type=int, default=MIN_HOURS)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    src = Path(args.fc)
    if not src.exists():
        raise SystemExit(f"not found: {src}\nRun fetch_forecasts.py first.")
    fc = pd.read_parquet(src)
    fc["valid_time_utc"] = pd.to_datetime(fc["valid_time_utc"], utc=True)
    fc = fc[fc.city.isin(args.cities)]
    fc = fc[fc.lead_day > 0]        # lead 0 is near-analysis, NOT a forecast
    leads = sorted(int(x) for x in fc.lead_day.unique())
    print(f"forecasts: {len(fc):,} hourly rows, "
          f"{fc.model.nunique()} models, leads {leads}")

    truth = load_truth(args.truth_dir, args.cities)
    print(f"truth:     {len(truth)} city-days")

    fc_daily = daily_from_hourly(fc, args.min_hours)
    print(f"daily:     {len(fc_daily):,} (city,model,lead,date) rows")

    long = build_long(fc_daily, truth)
    wide = build_wide(long)
    print(f"joined:    {len(long):,} long / {len(wide):,} wide rows")
    print(f"           {long.date.nunique()} dates, "
          f"{long.city.nunique()} cities")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    long.to_parquet(out / "daily_long.parquet", index=False)
    wide.to_parquet(out / "daily_wide.parquet", index=False)
    print(f"\nwrote {out / 'daily_long.parquet'}")
    print(f"wrote {out / 'daily_wide.parquet'}")

    if not args.quiet:
        report(long, wide)
    return 0


if __name__ == "__main__":
    sys.exit(main())
