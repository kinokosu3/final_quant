# Plan: Build Hikyuu → Qlib CN dump_bin pipeline

## Goal

Build a reproducible pipeline that converts local Hikyuu daily bars into an **official Qlib CN dump_bin-style directory** rooted at [`qlib_data/cn_hikyuu`](qlib_data/cn_hikyuu:1), with:

- calendar: [`qlib_data/cn_hikyuu/calendars/day.txt`](qlib_data/cn_hikyuu/calendars/day.txt:1)
- instruments: [`qlib_data/cn_hikyuu/instruments/all.txt`](qlib_data/cn_hikyuu/instruments/all.txt:1)
- features: [`qlib_data/cn_hikyuu/features/<instrument>/*.day.bin`](qlib_data/cn_hikyuu/features:1)

Daily field set: `open,high,low,close,volume,money` (no adj/factor, no paused).

Instrument code convention: `000001.SZ` / `600000.SH`.

## Inputs and available capabilities (from current data source)

- Universe enumeration: [`HikyuuDataSource.get_all_securities()`](data_source/hikyuu_data_source.py:315) and [`HikyuuDataSource.get_all_codes()`](data_source/hikyuu_data_source.py:311)
- Daily bars: [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:200)
- Trading calendar: [`HikyuuDataSource.get_trade_days()`](data_source/hikyuu_data_source.py:351)
- Start/end dates per security: [`HikyuuDataSource.get_security_info()`](data_source/hikyuu_data_source.py:178)

## Key design decisions (confirmed)

1) Output layout: official Qlib CN dump_bin style (calendars/instruments/features).

2) Mapping: Hikyuu market_code like `sz000001` and `sh600000` maps to:

- `sz000001` → `000001.SZ`
- `sh600000` → `600000.SH`

Exclusion: ignore `bj` instruments.

3) Missing-day policy: align every instrument to [`calendars/day.txt`](qlib_data/cn_hikyuu/calendars/day.txt:1), write `NaN` for missing days.

4) Dependency: you already have `qlib` installed, so we can use Qlib’s official dumper (see `scripts/dump_bin.py dump_all` usage in Qlib docs).

## Proposed pipeline stages

### Stage A — Build calendar

- Use [`HikyuuDataSource.get_trade_days()`](data_source/hikyuu_data_source.py:351) with a configurable date range.
- Write sorted dates (YYYY-MM-DD, one per line) into [`qlib_data/cn_hikyuu/calendars/day.txt`](qlib_data/cn_hikyuu/calendars/day.txt:1).

### Stage B — Build instruments universe

- Use [`HikyuuDataSource.get_all_securities()`](data_source/hikyuu_data_source.py:315) then filter by prefix `sz` and `sh` only.
- For each code, determine `start_date/end_date` via [`HikyuuDataSource.get_security_info()`](data_source/hikyuu_data_source.py:178) (fallback: first/last available bar from [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:200)).
- Map codes to Qlib format and write lines like:

```
000001.SZ	2010-01-01	2025-12-31
```

into [`qlib_data/cn_hikyuu/instruments/all.txt`](qlib_data/cn_hikyuu/instruments/all.txt:1).

### Stage C — Extract & normalize daily bars (intermediate source layer)

- For each instrument (or batched), call [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:200) with fields `open,high,low,close,volume,money`.
- Normalize schema to Qlib dump expectations:
  - date column name: `date`
  - float columns: `open,high,low,close,money`
  - volume column: `volume`
- Align to calendar (left-join / reindex by date) and keep NaNs on missing days.
- Write one file per instrument into:

- [`qlib_data/cn_hikyuu/source/1d_nor/<instrument>.parquet`](qlib_data/cn_hikyuu/source/1d_nor:1)

with columns: `date,open,high,low,close,volume,money`.

Note: the data source currently multiplies `money` by `10000` (see [`_normalize_amount_unit()`](data_source/hikyuu_data_source.py:115)); we’ll keep it but add a spot-check step.

### Stage D — Convert to Qlib `.day.bin`

Run Qlib’s dump command against the normalized directory:

- `python -m qlib.scripts.dump_bin dump_all --data_path qlib_data/cn_hikyuu/source/1d_nor --qlib_dir qlib_data/cn_hikyuu --freq day --date_field_name date --exclude_fields date --file_suffix .parquet`

If the entrypoint differs in your installed package, fallback to importing and calling the dump module directly in a small runner.

### Stage E — Verification (smoke test)

- Pick 1–3 instruments, compare:
  - source parquet rows vs loaded Qlib feature arrays
  - spot-check a date’s `close`/`volume`/`money`
- Confirm feature files exist:
  - [`qlib_data/cn_hikyuu/features/<instrument>/close.day.bin`](qlib_data/cn_hikyuu/features:1)

## Config knobs (pipeline CLI)

- `--start_date` / `--end_date` (inclusive), or `--count` (latest N trading days)
- `--types` or prefix filters (default: `sz` and `sh`)
- `--include_fields` fixed to `open,high,low,close,volume,money` for v1
- `--workers` for parallel per-instrument extraction

## Open risks (explicit)

- Qlib dump entrypoint naming may differ depending on installation; we’ll verify the correct module path during implementation.
- `money` unit scaling must be validated once against a known day.

## Definition of done

- `qlib_data/cn_hikyuu` contains `calendars/`, `instruments/`, `features/` and Qlib can load daily features for at least one instrument.
