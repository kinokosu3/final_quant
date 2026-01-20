from __future__ import annotations

from typing import Dict, List, Sequence, Set, Tuple

import pandas as pd

from qlib_bt.factors import FactorConfig, build_minimal_factors
from qlib_bt.qlib_data import qlib_features, validate_qlib_frame


def select_targets_by_date(
    instruments: Sequence[str],
    rebalance_days: Sequence[pd.Timestamp],
    start_time: str,
    end_time: str,
    top_n: int,
    min_money_20d: float,
    factors: Sequence[FactorConfig] | None = None,
) -> Tuple[Dict[pd.Timestamp, Dict[str, float]], pd.DataFrame]:
    """Select weekly targets and also return the raw daily factor frame.

    Returns
    - targets_by_day: {rebalance_date -> {instrument -> weight}}
    - factor_daily: MultiIndex(instrument, datetime) with columns:
        factor expressions + auxiliary columns + 'score'

    Notes
    - Weight scheme is equal-weight among picked names.
    """

    factors = list(factors) if factors is not None else build_minimal_factors()

    factor_fields = [f.expr for f in factors]
    aux_fields = [
        "Mean($money, 20)",
        "$volume",
    ]

    fields = factor_fields + aux_fields
    df = qlib_features(instruments, fields, start_time=start_time, end_time=end_time, freq="day")
    validate_qlib_frame(df)

    df = df.copy()

    # Compute score for all rows (daily) so we can export.
    score = pd.Series(0.0, index=df.index)
    for fc in factors:
        s = pd.to_numeric(df[fc.expr], errors="coerce").fillna(0.0)
        score = score.add(s * float(fc.weight), fill_value=0.0)
    score = pd.to_numeric(score, errors="coerce").fillna(0.0)
    df["score"] = score

    targets: Dict[pd.Timestamp, Dict[str, float]] = {}

    for d in rebalance_days:
        try:
            cross = df.xs(d, level="datetime")
        except KeyError:
            continue
        if cross is None or cross.empty:
            continue

        money20 = cross[aux_fields[0]]
        vol = cross[aux_fields[1]]

        liquid = pd.to_numeric(money20, errors="coerce") >= float(min_money_20d)
        tradable = pd.to_numeric(vol, errors="coerce") > 0
        valid = liquid & tradable

        cross = cross[valid]
        if cross.empty:
            continue

        s_score = pd.to_numeric(cross["score"], errors="coerce").replace(
            [float("inf"), float("-inf")], pd.NA
        )
        s_score = s_score.dropna()
        if s_score.empty:
            continue

        picked = s_score.sort_values(ascending=False).head(int(top_n)).index.tolist()
        if not picked:
            continue

        w = 1.0 / float(len(picked))
        targets[d] = {inst: w for inst in picked}

    return targets, df


def collect_union_universe(targets_by_day: Dict[pd.Timestamp, Dict[str, float]]) -> List[str]:
    s: Set[str] = set()
    for m in targets_by_day.values():
        s.update(m.keys())
    return sorted(s)


def filter_and_renormalize_targets(
    targets_by_day: Dict[pd.Timestamp, Dict[str, float]],
    universe: Set[str],
) -> Dict[pd.Timestamp, Dict[str, float]]:
    out: Dict[pd.Timestamp, Dict[str, float]] = {}
    for d, m in targets_by_day.items():
        kept = {k: float(v) for k, v in m.items() if k in universe}
        if not kept:
            continue
        s = float(sum(kept.values()))
        if s > 0:
            kept = {k: float(v) / s for k, v in kept.items()}
        out[d] = kept
    return out
