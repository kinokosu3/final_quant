import argparse
import json
import os
import sys
from typing import Dict, Optional, Sequence, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from factor_analysis.group_back_testing import GroupBackTesting
from factor_analysis.signal_analyzer import SignalAnalyzer
from qlib_bt.factor_analysis_integration import (
    load_factor_analysis_meta,
    validate_factor_analysis_parquets,
)


def _chdir(path: str) -> None:
    if not path:
        return
    os.makedirs(path, exist_ok=True)
    os.chdir(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run single-factor analysis from exported parquets")

    p.add_argument(
        "--fa_dir",
        required=True,
        help="Directory containing signal*.parquet/ret.parquet/meta.json",
    )
    p.add_argument(
        "--signal_name",
        default=None,
        help="Run analysis for a specific signal name (uses signal_<name>.parquet)",
    )
    p.add_argument(
        "--run_all_signals",
        action="store_true",
        help="Run analysis for all signals listed in meta.json",
    )

    p.add_argument("--no_ic", action="store_true", help="Skip IC analysis")
    p.add_argument("--no_return", action="store_true", help="Skip layered return analysis")
    p.add_argument("--no_turnover", action="store_true", help="Skip turnover analysis")

    p.add_argument("--bins", type=int, default=None, help="Override bins (default from meta)")
    p.add_argument(
        "--write_validation",
        action="store_true",
        help="Write validation.json (row counts, null rates, duplicates)",
    )

    args = p.parse_args(list(argv) if argv is not None else None)

    fa_dir = os.path.abspath(args.fa_dir)

    # We may have extra keys in meta.json, so load raw JSON first.
    meta_path = os.path.join(fa_dir, "meta.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta_raw = json.load(f)

    meta = load_factor_analysis_meta(fa_dir)

    if args.write_validation:
        diag = validate_factor_analysis_parquets(fa_dir)
        with open(os.path.join(fa_dir, "validation.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(diag, f, ensure_ascii=False, indent=2)

    ret_path = os.path.join(fa_dir, "ret.parquet")

    def _safe_float(x) -> Optional[float]:
        try:
            if x is None:
                return None
            v = float(x)
            if v != v:
                return None
            return v
        except Exception:
            return None

    def _series_summary(s: pd.Series) -> Dict[str, Optional[float]]:
        s = pd.to_numeric(s, errors="coerce").dropna()
        if s.empty:
            return {"mean": None, "std": None, "ir": None}
        mean = float(s.mean())
        std = float(s.std(ddof=0))
        ir = mean / std if std > 0 else None
        return {"mean": mean, "std": std, "ir": _safe_float(ir)}

    def _run_one(sig_name: str, sig_file: str) -> None:
        sig_path = os.path.join(fa_dir, sig_file)
        sig = pd.read_parquet(sig_path)
        sig["datetime"] = pd.to_datetime(sig["datetime"]).dt.tz_localize(None)
        signal_wide = sig.pivot(index="datetime", columns="code", values="signal").sort_index()

        bins = int(args.bins) if args.bins is not None else int(meta.bins)

        analyzer = SignalAnalyzer(
            base_data_info={
                "signal_data": signal_wide,
                "ret_data": {
                    "path": ret_path,
                    "fields": ["close"],
                    "start": meta.start,
                    "end": meta.end,
                    "lag": 0,
                },
            },
            freq=int(meta.freq),
            offset=str(meta.offset),
            bins=bins,
        )

        out_subdir = os.path.join(fa_dir, f"signal={sig_name}")
        _chdir(out_subdir)

        metrics: Dict[str, object] = {
            "schema_version": 1,
            "signal": {"name": str(sig_name), "file": str(sig_file)},
            "meta": {
                "start": meta.start,
                "end": meta.end,
                "freq": int(meta.freq),
                "offset": str(meta.offset),
                "bins": int(bins),
            },
            "data": {
                "rows": int(len(sig)),
                "dates": int(sig["datetime"].dt.normalize().nunique()) if "datetime" in sig.columns else None,
                "codes": int(sig["code"].nunique()) if "code" in sig.columns else None,
                "null_rate": float(pd.to_numeric(sig["signal"], errors="coerce").isna().mean()) if "signal" in sig.columns else None,
            },
        }

        if not args.no_ic:
            analyzer.ic_analysis()
            # Recompute rank-IC numerically for metrics output.
            analyzer.get_signal()
            analyzer.get_ret()
            rank_ic = analyzer.signal_df.reindex(analyzer.ret_df.index).corrwith(
                analyzer.ret_df, method="spearman", axis=1
            )
            metrics["ic"] = {
                "rank_ic": _series_summary(rank_ic),
                "positive_rate": _safe_float((rank_ic > 0).mean()),
            }

        if not args.no_return:
            analyzer.return_analysis(weights_mode="equal", non_linear=False)
            # Numeric quantile spread summary
            analyzer.get_signal()
            analyzer.ret_1d = analyzer.__getattribute__("ret_data").to_dataframes()
            if "factor" in analyzer.ret_1d:
                analyzer.ret_1d = analyzer.ret_1d["close"] * analyzer.ret_1d["factor"]
            else:
                analyzer.ret_1d = analyzer.ret_1d["close"]
            analyzer.ret_1d = analyzer.ret_1d / analyzer.ret_1d.shift(1) - 1
            analyzer.ret_1d = analyzer.ret_1d.dropna(how="all")
            signal_df = analyzer.signal_df.iloc[analyzer.balance_idx]
            analyzer.cal_weights("equal", None)
            gb = GroupBackTesting(analyzer.ret_1d, signal_df, weights=analyzer.weights)
            group_ret = gb()
            if isinstance(group_ret, pd.DataFrame) and group_ret.shape[1] >= 2:
                top = group_ret.iloc[:, -1]
                bot = group_ret.iloc[:, 0]
                spread = top - bot
                metrics["quantile"] = {
                    "top_minus_bottom_daily": _series_summary(spread),
                    "top_daily": _series_summary(top),
                    "bottom_daily": _series_summary(bot),
                }

        if not args.no_turnover:
            analyzer.turnover_analysis(weights_mode="equal")
            # Numeric turnover (average per rebalance) can be derived from analyzer.weights
            try:
                analyzer.cal_weights("equal", None)
                if analyzer.weights is not None and len(analyzer.weights) > 0:
                    # Use top group turnover as a proxy for tradable portfolio turnover
                    w = analyzer.weights[-1].fillna(0)
                    # turnover per rebalance date = 0.5*sum(|Δw|)
                    t = (w.diff().abs().sum(axis=1) / 2.0).dropna()
                    metrics["turnover"] = {"top_group_avg": _safe_float(t.mean())}
            except Exception:
                pass

        with open(os.path.join(out_subdir, "metrics.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

    signals = meta_raw.get("signals") or []

    if args.run_all_signals:
        for s in signals:
            _run_one(str(s.get("name")), str(s.get("file")))
        return 0

    if args.signal_name:
        wanted = str(args.signal_name)
        for s in signals:
            if str(s.get("name")) == wanted:
                _run_one(wanted, str(s.get("file")))
                return 0
        raise SystemExit(f"signal_name not found in meta.json: {wanted}")

    # Backward compatible: run legacy signal.parquet in-place.
    _run_one("score", "signal.parquet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
