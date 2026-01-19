# Assessment: Hikyuu → Qlib minimal daily pipeline

## Conclusion

For a **minimal** Qlib dataset build (daily `open/high/low/close/volume/money` + calendar + instruments), this data source is **largely sufficient**, with a few gaps/assumptions you should account for in the pipeline layer.

Key capabilities are present via [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:200), [`HikyuuDataSource.get_trade_days()`](data_source/hikyuu_data_source.py:351), and code universe enumeration via [`HikyuuDataSource.get_all_securities()`](data_source/hikyuu_data_source.py:315) / [`HikyuuDataSource.get_all_codes()`](data_source/hikyuu_data_source.py:311).

## What you can build with current data source

- Daily bars: `open/high/low/close/volume/money` are available (defaults in [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:200))
- Calendar: trading days list is available (see [`HikyuuDataSource.get_trade_days()`](data_source/hikyuu_data_source.py:351))
- Instruments universe: list of symbols is available, index is hikyuu `market_code` like `sz000001` (see [`HikyuuDataSource.get_all_securities()`](data_source/hikyuu_data_source.py:315))

## Gaps / risks to handle in the pipeline

### 1) Qlib instrument naming vs hikyuu `market_code`

- Callers are expected to use hikyuu `market_code` (see module docstring in [`hikyuu_data_source.py`](data_source/hikyuu_data_source.py:1)).
- Qlib datasets commonly use `000001.SZ` / `SZ000001` or similar conventions.
- Recommendation: define a single mapping function in the pipeline layer, and ensure you apply it consistently for both the instrument list and per-instrument feature files.

### 2) Instruments start/end dates are not provided by universe enumeration

- [`HikyuuDataSource.get_all_securities()`](data_source/hikyuu_data_source.py:315) returns `start_date` and `end_date` as `None` (see row construction in [`HikyuuDataSource.get_all_securities()`](data_source/hikyuu_data_source.py:334)).
- However, per-instrument start/end exist via [`HikyuuDataSource.get_security_info()`](data_source/hikyuu_data_source.py:178), returning `start_datetime`/`last_datetime` from hikyuu.
- Recommendation: build `instruments` by calling [`HikyuuDataSource.get_security_info()`](data_source/hikyuu_data_source.py:178) for each code (or derive from first/last bar of [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:200)).

### 3) Money/amount unit conversion is an assumption

- The data source multiplies `money`/`amount` by `10000` (see [`_normalize_amount_unit()`](data_source/hikyuu_data_source.py:115)).
- This may be correct for certain upstream conventions, but it is an implicit assumption.
- Recommendation: validate this once against a known stock/day, then keep the conversion as a configurable option in the pipeline.

### 4) Paused/suspension handling is weak

- If `paused` is requested but missing, it is set to `0` for all rows (see [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:272)).
- Qlib pipelines typically need either:
  - dense calendar alignment with NaNs on missing days, or
  - explicit suspension flags
- Recommendation: in the pipeline, reindex each instrument to the calendar returned by [`HikyuuDataSource.get_trade_days()`](data_source/hikyuu_data_source.py:351) and decide fill policy.

### 5) End-date inclusiveness differs between price and calendar

- Price query uses an end-date + 1 day trick (see [`_to_hk_datetime_end()`](data_source/hikyuu_data_source.py:83) and usage in [`_kquery_for_dates()`](data_source/hikyuu_data_source.py:90)).
- Calendar query does not do the +1 adjustment (see [`HikyuuDataSource.get_trade_days()`](data_source/hikyuu_data_source.py:351)).
- Recommendation: treat `end_date` as inclusive at the pipeline API boundary, and implement a single inclusive filter step after fetch (similar to the existing filter in [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:289)).

### 6) Frequency is effectively day-only

- `frequency` is accepted but not used to select non-daily K-line types (see signature of [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:200)).
- For your current goal (daily only), this is fine.

## Minimal pipeline contract (what we should assume)

If we proceed with “daily raw price only”, the pipeline can rely on:

- Universe: from [`HikyuuDataSource.get_all_codes()`](data_source/hikyuu_data_source.py:311)
- Calendar: from [`HikyuuDataSource.get_trade_days()`](data_source/hikyuu_data_source.py:351)
- Bars: from [`HikyuuDataSource.get_price()`](data_source/hikyuu_data_source.py:200)

And it should explicitly implement:

- instrument code mapping
- instruments start/end derivation
- calendar alignment / missing-day handling
- money unit validation switch
