# Hikyuu -> Qlib CN dump_bin pipeline

This implements the plan in [`plans/hikyuu_to_qlib_plan.md`](plans/hikyuu_to_qlib_plan.md:1): generate a Qlib CN `dump_bin`-style directory at `qlib_data/cn_hikyuu` from local Hikyuu daily bars.

## Output layout

Root: `qlib_data/cn_hikyuu`

- calendar: `qlib_data/cn_hikyuu/calendars/day.txt`
- instruments: `qlib_data/cn_hikyuu/instruments/all.txt`
- normalized source (parquet): `qlib_data/cn_hikyuu/source/1d_nor/<instrument>.parquet`
- features (bin): `qlib_data/cn_hikyuu/features/<instrument>/*.day.bin`

Instrument code convention:

- hikyuu: `sz000001` / `sh600000`
- qlib: `000001.SZ` / `600000.SH`

## Build dataset

1) Build calendar + instruments + source parquet

```bash
python scripts/hikyuu_to_qlib_pipeline.py --start_date 2010-01-01 --end_date 2025-12-31
```

Quick run (limit instrument count):

```bash
python scripts/hikyuu_to_qlib_pipeline.py --count 250 --limit 50
```

2) Convert parquet -> `.day.bin` via Qlib

```bash
python scripts/hikyuu_to_qlib_pipeline.py --count 250 --limit 50 --dump_bin
```

Notes:

- Qlib entrypoint used: `python -m qlib.scripts.dump_bin dump_all`.
- Parquet writing requires an engine (recommended: `pyarrow`).

## Verify (smoke test)

After `--dump_bin` finishes:

```bash
python scripts/verify_cn_hikyuu_qlib.py --limit 3
```

It compares the parquet source vs Qlib-loaded bins (fields: `close`, `volume`, `money`).

## Important assumptions

- `bj` instruments are ignored by mapping in [`scripts/hikyuu_to_qlib_pipeline.py`](scripts/hikyuu_to_qlib_pipeline.py:1).
- Missing-day handling: reindex each instrument to the shared calendar and keep `NaN` values.
- `money` unit: Hikyuu data source currently multiplies by `10000` in [`_normalize_amount_unit()`](data_source/hikyuu_data_source.py:115). This pipeline keeps that behavior.
