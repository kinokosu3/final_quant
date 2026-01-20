from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from qlib_bt.io import dt_to_ts, ensure_dir
from qlib_bt.qlib_data import qlib_features, validate_qlib_frame


@dataclass(frozen=True)
class FactorAnalysisMeta:
    start: str
    end: str
    offset: str
    freq: int
    signal_col: str
    bins: int
    rebalance_days: List[str]
    rebalance_gaps: List[int]
    rebalance_gap_median: int


def _as_date_str_list(days: Iterable[pd.Timestamp]) -> List[str]:
    return [str(dt_to_ts(d).date()) for d in days]


def derive_median_rebalance_gap(calendar: Sequence[pd.Timestamp], rebalance_days: Sequence[pd.Timestamp]) -> Tuple[int, List[int]]:
    if not calendar or not rebalance_days:
        return 5, []

    cal_idx: Dict[pd.Timestamp, int] = {dt_to_ts(d): i for i, d in enumerate(calendar)}

    rb = [dt_to_ts(d) for d in rebalance_days]
    rb = [d for d in rb if d in cal_idx]
    rb = sorted(set(rb))

    gaps: List[int] = []
    for a, b in zip(rb[:-1], rb[1:]):
        gaps.append(max(1, int(cal_idx[b] - cal_idx[a])))

    if not gaps:
        return 5, []

    s = pd.Series(gaps)
    med = int(s.median())
    if med <= 0:
        med = 5
    return med, gaps


def _sanitize_signal_name(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return "signal"
    s = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "signal"


def _factor_daily_to_long_signal(
    factor_daily: pd.DataFrame,
    signal_col: str,
) -> pd.DataFrame:
    if factor_daily is None or factor_daily.empty:
        raise ValueError("factor_daily is empty")
    if signal_col not in factor_daily.columns:
        raise KeyError(f"signal_col '{signal_col}' not in factor_daily columns")

    df = factor_daily[[signal_col]].copy()
    df = df.rename(columns={signal_col: "signal"})
    df = df.reset_index()
    if "instrument" not in df.columns or "datetime" not in df.columns:
        raise ValueError("factor_daily must have MultiIndex ['instrument','datetime']")

    df = df.rename(columns={"instrument": "code"})
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)
    df = df[["datetime", "code", "signal"]]
    return df


def _close_to_long_ret(close_frame: pd.DataFrame) -> pd.DataFrame:
    validate_qlib_frame(close_frame)

    df = close_frame[["$close"]].copy()
    df = df.reset_index().rename(columns={"instrument": "code", "$close": "close"})
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)
    df = df[["datetime", "code", "close"]]
    return df


def export_factor_analysis_inputs(
    *,
    factor_daily: pd.DataFrame,
    instruments: Sequence[str],
    start_time: str,
    end_time: str,
    calendar: Sequence[pd.Timestamp],
    rebalance_days: Sequence[pd.Timestamp],
    out_dir: str,
    signal_col: str = "score",
    signal_defs: Optional[Sequence[Tuple[str, str]]] = None,
    offset: str = "close",
    bins: int = 5,
    include_universe_in_meta: bool = True,
) -> Tuple[str, FactorAnalysisMeta]:
    """Export parquet inputs for the factor_analysis framework.

    Writes to `{out_dir}/factor_analysis/`:
    - signal.parquet: datetime, code, signal
    - ret.parquet: datetime, code, close
    - meta.json: analysis config + rebalance diagnostics

    Returns
    - fa_dir: the output directory
    - meta: parsed metadata
    """

    fa_dir = os.path.join(out_dir, "factor_analysis")
    ensure_dir(fa_dir)

    freq, gaps = derive_median_rebalance_gap(calendar, rebalance_days)

    if signal_defs is None or len(signal_defs) == 0:
        # Backward compatible: export one signal based on `signal_col`.
        signal_defs = [("score", str(signal_col))]

    close_frame = qlib_features(
        instruments=instruments,
        fields=["$close"],
        start_time=start_time,
        end_time=end_time,
        freq="day",
    )
    close_df = _close_to_long_ret(close_frame)

    ret_path = os.path.join(fa_dir, "ret.parquet")
    close_df.to_parquet(ret_path, index=False)

    exported_signals: List[Dict[str, str]] = []

    # Export each requested signal into its own parquet.
    for raw_name, source_col in signal_defs:
        sig_name = _sanitize_signal_name(str(raw_name))
        df_sig = _factor_daily_to_long_signal(factor_daily, signal_col=str(source_col))

        sig_file = f"signal_{sig_name}.parquet"
        sig_path = os.path.join(fa_dir, sig_file)
        df_sig.to_parquet(sig_path, index=False)

        exported_signals.append(
            {
                "name": sig_name,
                "source_col": str(source_col),
                "file": sig_file,
            }
        )

        # Keep legacy `signal.parquet` for the main signal (default: score).
        if sig_name == _sanitize_signal_name(str(signal_col)) or sig_name == "score":
            legacy_path = os.path.join(fa_dir, "signal.parquet")
            df_sig.to_parquet(legacy_path, index=False)

    meta = FactorAnalysisMeta(
        start=str(start_time),
        end=str(end_time),
        offset=str(offset),
        freq=int(freq),
        signal_col=str(signal_col),
        bins=int(bins),
        rebalance_days=_as_date_str_list(rebalance_days),
        rebalance_gaps=[int(x) for x in gaps],
        rebalance_gap_median=int(freq),
    )

    meta_path = os.path.join(fa_dir, "meta.json")
    meta_obj = dict(meta.__dict__)
    meta_obj["signals"] = exported_signals

    if include_universe_in_meta:
        meta_obj["universe"] = sorted(set(map(str, instruments)))
        meta_obj["universe_size"] = int(len(meta_obj["universe"]))

    with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta_obj, f, ensure_ascii=False, indent=2)

    return fa_dir, meta


def load_factor_analysis_meta(fa_dir: str) -> FactorAnalysisMeta:
    meta_path = os.path.join(fa_dir, "meta.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        obj = json.load(f)

    # Ignore extra keys such as universe diagnostics.
    keep = {k: obj[k] for k in FactorAnalysisMeta.__dataclass_fields__.keys() if k in obj}
    return FactorAnalysisMeta(**keep)


def validate_factor_analysis_parquets(fa_dir: str) -> Dict[str, object]:
    signal_path = os.path.join(fa_dir, "signal.parquet")
    ret_path = os.path.join(fa_dir, "ret.parquet")

    s = pd.read_parquet(signal_path)
    r = pd.read_parquet(ret_path)

    def _basic(df: pd.DataFrame, name: str, value_col: str) -> Dict[str, object]:
        out: Dict[str, object] = {}
        out["rows"] = int(len(df))
        out["cols"] = list(df.columns)
        out["unique_dates"] = int(pd.to_datetime(df["datetime"]).dt.normalize().nunique())
        out["unique_codes"] = int(df["code"].nunique())
        out["null_rate"] = float(pd.to_numeric(df[value_col], errors="coerce").isna().mean())
        out["dup_pairs"] = int(df.duplicated(subset=["datetime", "code"]).sum())
        out["name"] = name
        return out

    return {
        "signal": _basic(s, "signal", "signal"),
        "ret": _basic(r, "ret", "close"),
    }
