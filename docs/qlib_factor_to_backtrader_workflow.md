# Qlib Factors -> Backtrader Weekly ETF Portfolio

This repo already contains a Qlib `dump_bin`-style dataset under [`qlib_data/cn_hikyuu`](qlib_data/cn_hikyuu:1).

This doc describes a minimal workflow:

1) load ETF daily OHLCV from Qlib
2) compute a small set of factor expressions (Qlib expression engine)
3) weekly rebalance into a long-only top-N ETF portfolio (equal-weight)
4) backtest with Backtrader (commission + slippage + stamp duty on sells)

## Data prerequisites

Your dataset layout should include:

- calendar: [`qlib_data/cn_hikyuu/calendars/day.txt`](qlib_data/cn_hikyuu/calendars/day.txt:1)
- instruments: [`qlib_data/cn_hikyuu/instruments/all.txt`](qlib_data/cn_hikyuu/instruments/all.txt:1)
- features bin files: [`qlib_data/cn_hikyuu/features`](qlib_data/cn_hikyuu/features:1)

Example feature files:

- [`qlib_data/cn_hikyuu/features/159001.sz/close.day.bin`](qlib_data/cn_hikyuu/features/159001.sz/close.day.bin:1)

## Why not use `D.features()`

In `qlib==0.9.7`, [`D.features()`](scripts/etf_weekly_factor_bt.py:93) tries a call signature that includes a positional `disk_cache` argument, and then falls back.
With local providers this can trigger the error:

- `LocalDatasetProvider.dataset() got multiple values for argument 'inst_processors'`

The script avoids that by calling [`DatasetD.dataset()`](scripts/etf_weekly_factor_bt.py:93) directly.

## Minimal factor set

The script uses 3 simple expressions over `$close`:

- momentum 20D: `($close / Ref($close, 20)) - 1`
- trend 5/20: `(Mean($close, 5) / Mean($close, 20)) - 1`
- short reversal 5D: `-1 * (($close / Ref($close, 5)) - 1)`

Then combines them into a weighted score.
See [`_build_minimal_factors()`](scripts/etf_weekly_factor_bt.py:65).

## Constraints / filters

At each weekly rebalance day:

- Universe is ETFs from [`all.txt`](qlib_data/cn_hikyuu/instruments/all.txt:1)
- Liquidity filter: `Mean($money, 20) >= 20,000,000`
- Tradability filter: `$volume > 0`
- Selection: top `N=10` by score
- Long-only, equal-weight

See [`_select_targets_by_date()`](scripts/etf_weekly_factor_bt.py:113).

If an ETF is not tradable (volume=0) on the rebalance day, the strategy skips placing orders for it (keeps existing position).
See [`_is_tradable_in_bt()`](scripts/etf_weekly_factor_bt.py:232) and [`WeeklyRebalanceStrategy.next()`](scripts/etf_weekly_factor_bt.py:308).

## Run a smoke test

```bash
python scripts/etf_weekly_factor_bt.py --start 2025-01-02 --end 2025-03-31 --out_dir results/etf_weekly_smoke
```

Outputs:

- equity curve: `results/.../equity_curve.csv`
- weekly target weights: `results/.../targets.csv`
- daily holdings snapshot: `results/.../positions.csv`

See [`main()`](scripts/etf_weekly_factor_bt.py:405).

## Full year backtest

```bash
python scripts/etf_weekly_factor_bt.py --start 2025-01-02 --end 2025-12-30 --out_dir results/etf_weekly_2025
```

## Key parameters

- `--top_n`: portfolio size
- `--min_money_20d`: liquidity threshold
- `--commission`: commission rate (percentage)
- `--slippage`: slippage rate (percentage)
- `--stamp_duty`: stamp duty applied on sells (percentage)

All are wired in [`argparse.ArgumentParser()`](scripts/etf_weekly_factor_bt.py:406).
