import argparse
import os
import sys
from typing import Dict, Optional, Sequence

# When executing via `python scripts\...`, Python sets the import root to `scripts/`.
# Ensure the repo root (which contains `qlib_bt/`) is importable.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if os.environ.get("QLIB_BT_DEBUG_IMPORT_PATH"):
    sys.stderr.write(f"[qlib_bt] repo_root={_REPO_ROOT}\n")
    sys.stderr.write(f"[qlib_bt] sys.path[0:5]={sys.path[:5]}\n")

import pandas as pd

from qlib_bt.bt_engine import (
    filter_price_map_by_start,
    load_ohlcv_for_backtrader,
    run_backtrader,
)
from qlib_bt.exports import dump_targets_csv
from qlib_bt.factor_analysis_integration import export_factor_analysis_inputs
from qlib_bt.factors import build_minimal_factors
from qlib_bt.io import (
    dt_to_ts,
    iter_weekly_rebalance_days,
    read_calendar,
    read_instruments,
    read_stock_name_map,
)
from qlib_bt.qlib_data import init_qlib, qlib_features, to_qlib_inst
from qlib_bt.selection import (
    collect_union_universe,
    filter_and_renormalize_targets,
    select_targets_by_date,
)


def _normalize_targets_to_universe(
    targets_by_day: Dict[pd.Timestamp, Dict[str, float]],
    universe: Dict[str, pd.DataFrame],
) -> Dict[pd.Timestamp, Dict[str, float]]:
    targets_norm: Dict[pd.Timestamp, Dict[str, float]] = {}
    for d, m in targets_by_day.items():
        targets_norm[dt_to_ts(d)] = {k: float(v) for k, v in m.items() if k in universe}

    targets_norm = filter_and_renormalize_targets(targets_norm, set(universe.keys()))
    return targets_norm


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Weekly long-only ETF portfolio from Qlib factors -> Backtrader"
    )

    p.add_argument("--qlib_dir", default=os.path.join("qlib_data", "cn_hikyuu"))
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-12-31")

    p.add_argument("--top_n", type=int, default=10)
    p.add_argument("--min_money_20d", type=float, default=20000000.0)

    p.add_argument("--cash", type=float, default=1000000.0)
    p.add_argument("--commission", type=float, default=0.0003)
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--stamp_duty", type=float, default=0.001)

    p.add_argument("--out_dir", default=os.path.join("results", "etf_weekly"))
    p.add_argument("--stock_name_map", default="mapped_stocks.txt")

    p.add_argument(
        "--export_factor_analysis",
        action="store_true",
        help="Export factor_analysis inputs (signal/ret/meta) into out_dir/factor_analysis",
    )
    p.add_argument(
        "--run_factor_analysis",
        action="store_true",
        help="Run factor_analysis immediately after exporting inputs",
    )

    p.add_argument(
        "--allow_late_start_instruments",
        action="store_true",
        help="Allow instruments that start after --start (may delay backtest start in multi-data mode)",
    )

    args = p.parse_args(list(argv) if argv is not None else None)

    cal_path = os.path.join(args.qlib_dir, "calendars", "day.txt")
    inst_path = os.path.join(args.qlib_dir, "instruments", "filtered.txt")

    calendar = read_calendar(cal_path)
    if not calendar:
        raise RuntimeError("calendar is empty")

    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end)
    calendar = [d for d in calendar if start_ts <= d <= end_ts]
    if not calendar:
        raise RuntimeError("no calendar days in the requested range")

    rebalance_days = iter_weekly_rebalance_days(calendar)

    instruments_raw = read_instruments(inst_path)
    instruments = [to_qlib_inst(x) for x in instruments_raw]

    stock_name_map = read_stock_name_map(args.stock_name_map)

    init_qlib(args.qlib_dir)

    factors = build_minimal_factors()

    targets_by_day, factor_daily = select_targets_by_date(
        instruments=instruments,
        rebalance_days=rebalance_days,
        start_time=str(calendar[0].date()),
        end_time=str(calendar[-1].date()),
        top_n=args.top_n,
        min_money_20d=args.min_money_20d,
        factors=factors,
    )

    used_universe = collect_union_universe(targets_by_day)

    price_map = load_ohlcv_for_backtrader(
        qlib_features_fn=qlib_features,
        instruments=used_universe,
        start_time=str(calendar[0].date()),
        end_time=str(calendar[-1].date()),
    )

    if not args.allow_late_start_instruments:
        price_map, _dropped = filter_price_map_by_start(price_map, start_time=str(calendar[0].date()))

    targets_norm = _normalize_targets_to_universe(targets_by_day, price_map)

    dump_targets_csv(targets_norm, args.out_dir, stock_name_map=stock_name_map)

    if args.export_factor_analysis or args.run_factor_analysis:
        # Export per-factor signals so the single-factor analyzer can be run for each.
        # 3 base factors + 1 aggregated score = 4 signals.
        signal_defs = [(fc.name, fc.expr) for fc in factors] + [("score", "score")]

        fa_dir, _meta = export_factor_analysis_inputs(
            factor_daily=factor_daily,
            instruments=instruments,
            start_time=str(calendar[0].date()),
            end_time=str(calendar[-1].date()),
            calendar=calendar,
            rebalance_days=rebalance_days,
            out_dir=args.out_dir,
            signal_col="score",
            signal_defs=signal_defs,
            offset="close",
            bins=5,
        )

        if args.run_factor_analysis:
            import subprocess

            subprocess.check_call(
                [
                    sys.executable,
                    os.path.join("scripts", "factor_analysis_runner.py"),
                    "--fa_dir",
                    fa_dir,
                    "--run_all_signals",
                    "--write_validation",
                ]
            )

    run_backtrader(
        price_map=price_map,
        targets_by_day=targets_norm,
        start_time=str(calendar[0].date()),
        end_time=str(calendar[-1].date()),
        cash=args.cash,
        commission=args.commission,
        slippage=args.slippage,
        stamp_duty=args.stamp_duty,
        out_dir=args.out_dir,
        stock_name_map=stock_name_map,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
