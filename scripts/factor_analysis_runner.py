import argparse
import json
import os
import sys
from typing import Optional, Sequence

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

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

        if not args.no_ic:
            analyzer.ic_analysis()

        if not args.no_return:
            analyzer.return_analysis(weights_mode="equal", non_linear=False)

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
