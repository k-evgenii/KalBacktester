# Kalshi market data debugging

`plot_kxhighny_week.ipynb` plots the KXHIGHNY week (7 events, 6 markets each) straight from the Postgres tables `event`, `market` and `candle`:
per-day bid/ask step charts with volume, bar density, spreads, a sum-of-mids sanity check, and last quote vs result.
Open it in VS Code or run `uv run jupyter lab` from the repo root. It needs the `kalbt-recon-db` container running.
