# Refactor: Qlib -> Backtrader -> QuantStats

This repo now splits the previous single-file script into small modules under `qlib_bt/`.

## What changed

- The previous implementation lived mostly in `scripts/etf_weekly_factor_bt.py`.
- Logic has been moved into reusable modules:
  - `qlib_bt/io.py`
  - `qlib_bt/qlib_data.py`
  - `qlib_bt/factors.py`
  - `qlib_bt/selection.py`
  - `qlib_bt/bt_engine.py`
  - `qlib_bt/reports.py`
  - `qlib_bt/exports.py`
- `scripts/etf_weekly_factor_bt.py` remains as the main entrypoint, now much thinner.

## Main pipeline usage

Run backtest only:

```bash
python scripts/etf_weekly_factor_bt.py --start 2025-01-02 --end 2025-03-31 --out_dir results/etf_weekly_smoke
```

This will write:

- `results/.../targets.csv`
- `results/.../positions.csv`
- `results/.../returns.csv`
- `results/.../equity_curve.csv`
- `results/.../quantstats_report.html` (if quantstats installed)

## Migration notes

- If you previously imported helpers from `scripts/etf_weekly_factor_bt.py`, switch to the new modules under `qlib_bt/`.
- The default factor set is still the 3-expression minimal set in `qlib_bt/factors.py`.
- `select_targets_by_date()` now also returns a daily factor frame.
