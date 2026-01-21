from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from qlib_bt.io import dt_to_ts
from qlib_bt.qlib_data import qlib_features, to_qlib_inst


def compute_daily_returns_from_close(close: pd.Series) -> pd.Series:
    s = pd.to_numeric(close, errors="coerce").dropna().sort_index()
    if s.empty:
        return s
    r = s / s.shift(1) - 1.0
    return r.dropna()


def fetch_benchmark_returns(
    *,
    benchmark_insts: Sequence[str],
    start_time: str,
    end_time: str,
) -> Dict[str, pd.Series]:
    if not benchmark_insts:
        return {}

    insts = [to_qlib_inst(x) for x in benchmark_insts]

    try:
        close_frame = qlib_features(
            instruments=insts,
            fields=["$close"],
            start_time=start_time,
            end_time=end_time,
            freq="day",
        )
    except Exception:
        return {}

    if close_frame is None or close_frame.empty:
        return {}

    out: Dict[str, pd.Series] = {}
    for raw, inst in zip(benchmark_insts, insts):
        try:
            sub = close_frame.xs(inst, level="instrument")
        except KeyError:
            continue
        if sub is None or sub.empty or "$close" not in sub.columns:
            continue
        s = pd.Series(sub["$close"].copy())
        s.index = pd.to_datetime(s.index).tz_localize(None)
        out[str(raw)] = compute_daily_returns_from_close(s)
    return out


def fetch_benchmark_close(
    *,
    benchmark_inst: str,
    start_time: str,
    end_time: str,
) -> Optional[pd.Series]:
    if not benchmark_inst:
        return None

    inst = to_qlib_inst(benchmark_inst)
    try:
        close_frame = qlib_features(
            instruments=[inst],
            fields=["$close"],
            start_time=start_time,
            end_time=end_time,
            freq="day",
        )
    except Exception:
        return None

    if close_frame is None or close_frame.empty:
        return None

    try:
        sub = close_frame.xs(inst, level="instrument")
    except KeyError:
        return None

    if sub is None or sub.empty or "$close" not in sub.columns:
        return None

    s = pd.Series(sub["$close"].copy())
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s


def trend_scale_by_day(
    *,
    calendar: Sequence[pd.Timestamp],
    benchmark_close: pd.Series,
    ma_window: int,
    risk_off_scale: float,
) -> Dict[pd.Timestamp, float]:
    """Return {date -> exposure_scale} using close < MA rule."""

    if ma_window <= 1:
        ma_window = 1

    close = pd.to_numeric(benchmark_close, errors="coerce").dropna().sort_index()
    close.index = pd.to_datetime(close.index).tz_localize(None)

    ma = close.rolling(int(ma_window)).mean()

    out: Dict[pd.Timestamp, float] = {}
    for d in calendar:
        d = dt_to_ts(d)
        if d not in close.index or d not in ma.index or pd.isna(ma.loc[d]):
            continue
        out[d] = float(risk_off_scale) if float(close.loc[d]) < float(ma.loc[d]) else 1.0
    return out


def apply_exposure_scale(
    targets_by_day: Dict[pd.Timestamp, Dict[str, float]],
    scale_by_day: Dict[pd.Timestamp, float],
) -> Dict[pd.Timestamp, Dict[str, float]]:
    out: Dict[pd.Timestamp, Dict[str, float]] = {}
    for d, m in targets_by_day.items():
        s = float(scale_by_day.get(dt_to_ts(d), 1.0))
        if not m:
            continue
        if s <= 0:
            continue
        out[dt_to_ts(d)] = {k: float(v) * s for k, v in m.items() if float(v) > 0}
    return out


def export_trend_regime(
    *,
    scale_by_day: Dict[pd.Timestamp, float],
    benchmark_close: pd.Series,
    ma_window: int,
    out_dir: str,
) -> Optional[str]:
    if not scale_by_day:
        return None
    if benchmark_close is None:
        return None

    bm_close_s = pd.to_numeric(benchmark_close, errors="coerce").dropna().sort_index()
    if bm_close_s.empty:
        return None

    bm_close_s.index = pd.to_datetime(bm_close_s.index).tz_localize(None)
    bm_ret = (bm_close_s / bm_close_s.shift(1) - 1.0).dropna()
    bm_ma = bm_close_s.rolling(int(max(int(ma_window), 1))).mean()

    df_trend = pd.DataFrame(index=pd.to_datetime(sorted(scale_by_day.keys())))
    df_trend.index = df_trend.index.tz_localize(None)
    df_trend["exposure_scale"] = pd.Series(scale_by_day)
    df_trend["bm_close"] = bm_close_s.reindex(df_trend.index)
    df_trend["bm_ma"] = bm_ma.reindex(df_trend.index)
    df_trend["bm_return"] = bm_ret.reindex(df_trend.index)
    df_trend["risk_off"] = (pd.to_numeric(df_trend["exposure_scale"], errors="coerce") < 1.0).astype(int)

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "trend_regime.csv")
    df_trend.to_csv(path, index=True)
    return path


def compute_weekly_turnover_from_targets(targets_by_day: Dict[pd.Timestamp, Dict[str, float]]) -> float:
    """Approx turnover at rebalance dates: 0.5*sum(abs(w_t - w_{t-1}))."""

    if not targets_by_day:
        return 0.0
    days = sorted(targets_by_day.keys())

    prev: Dict[str, float] = {}
    vals: List[float] = []
    for d in days:
        cur = {k: float(v) for k, v in (targets_by_day.get(d) or {}).items()}
        keys = set(prev.keys()) | set(cur.keys())
        diff = 0.0
        for k in keys:
            diff += abs(float(cur.get(k, 0.0)) - float(prev.get(k, 0.0)))
        vals.append(0.5 * diff)
        prev = cur

    return float(pd.Series(vals).mean()) if vals else 0.0


def safe_float(x: object) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)  # type: ignore[arg-type]
        if v != v:
            return None
        return v
    except Exception:
        return None


def total_return(returns: pd.Series) -> Optional[float]:
    if returns is None or returns.empty:
        return None
    s = pd.to_numeric(returns, errors="coerce").dropna()
    if s.empty:
        return None
    return safe_float((1.0 + s).prod() - 1.0)


def ann_return(returns: pd.Series, periods_per_year: int = 252) -> Optional[float]:
    if returns is None or returns.empty:
        return None
    s = pd.to_numeric(returns, errors="coerce").dropna()
    if s.empty:
        return None
    total = (1.0 + s).prod()
    n = int(len(s))
    if n <= 0:
        return None
    return safe_float(total ** (float(periods_per_year) / float(n)) - 1.0)


def ann_vol(returns: pd.Series, periods_per_year: int = 252) -> Optional[float]:
    if returns is None or returns.empty:
        return None
    s = pd.to_numeric(returns, errors="coerce").dropna()
    if s.empty:
        return None
    return safe_float(float(s.std(ddof=0)) * (float(periods_per_year) ** 0.5))


def sharpe(returns: pd.Series, rf: float = 0.0, periods_per_year: int = 252) -> Optional[float]:
    if returns is None or returns.empty:
        return None
    s = pd.to_numeric(returns, errors="coerce").dropna()
    if s.empty:
        return None
    ex = s - float(rf) / float(periods_per_year)
    vol = float(ex.std(ddof=0))
    if vol <= 0:
        return None
    return safe_float(float(ex.mean()) / vol * (float(periods_per_year) ** 0.5))


def export_stop_loss_outcomes(
    *,
    price_map: Dict[str, pd.DataFrame],
    events_path: str,
    out_dir: str,
) -> Optional[str]:
    if not os.path.exists(events_path):
        return None

    df_ev = pd.read_csv(events_path)
    if df_ev is None or df_ev.empty:
        return None

    rows: List[Dict[str, object]] = []

    def _fwd_ret(inst: str, d: pd.Timestamp, n: int) -> Optional[float]:
        df_px = price_map.get(str(inst))
        if df_px is None or df_px.empty or "close" not in df_px.columns:
            return None
        px = pd.to_numeric(df_px["close"], errors="coerce").dropna()
        if px.empty:
            return None
        px.index = pd.to_datetime(px.index).tz_localize(None)
        d0 = pd.to_datetime(d).tz_localize(None).normalize()
        if d0 not in px.index:
            idx = px.index[px.index >= d0]
            if idx.empty:
                return None
            d0 = idx.min()
        pos = int(px.index.get_loc(d0))
        nxt = pos + int(n)
        if nxt >= len(px.index):
            return None
        p0 = float(px.iloc[pos])
        p1 = float(px.iloc[nxt])
        if p0 <= 0:
            return None
        return float(p1 / p0 - 1.0)

    for _i, r in df_ev.iterrows():
        inst = str(r.get("instrument", ""))
        dt = pd.to_datetime(r.get("date", None), errors="coerce")
        if not inst or pd.isna(dt):
            continue
        rows.append(
            {
                "date": str(pd.to_datetime(dt).date()),
                "instrument": inst,
                "fwd_ret_5d": _fwd_ret(inst, dt, 5),
                "fwd_ret_20d": _fwd_ret(inst, dt, 20),
            }
        )

    if not rows:
        return None

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "stop_loss_outcomes.csv")
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def build_strategy_metrics(
    *,
    out_dir: str,
    git_head: Optional[str],
    params: Dict[str, object],
    result_pre: Any,
    result_post: Any,
    rebalance_days: Sequence[pd.Timestamp],
    scale_by_day: Dict[pd.Timestamp, float],
    targets_pre: Dict[pd.Timestamp, Dict[str, float]],
    targets_post: Dict[pd.Timestamp, Dict[str, float]],
) -> Dict[str, object]:
    scale_rebal = [float(scale_by_day.get(dt_to_ts(d), 1.0)) for d in rebalance_days]

    metrics: Dict[str, object] = {
        "schema_version": 1,
        "kind": "strategy_metrics",
        "run": {
            "out_dir": str(out_dir),
            "git_head": git_head,
        },
        "params": params,
        "pre_risk": {
            "out_dir": "bt_pre_risk",
            "final_value": float(result_pre.final_value),
            "max_drawdown": safe_float(result_pre.max_drawdown),
            "total_return": total_return(result_pre.returns),
            "ann_return": ann_return(result_pre.returns),
            "ann_vol": ann_vol(result_pre.returns),
            "sharpe": sharpe(result_pre.returns),
        },
        "post_risk": {
            "out_dir": ".",
            "final_value": float(result_post.final_value),
            "max_drawdown": safe_float(result_post.max_drawdown),
            "total_return": total_return(result_post.returns),
            "ann_return": ann_return(result_post.returns),
            "ann_vol": ann_vol(result_post.returns),
            "sharpe": sharpe(result_post.returns),
        },
        "attribution": {
            "delta_final_value": safe_float(float(result_post.final_value) - float(result_pre.final_value)),
            "delta_total_return": safe_float(
                (total_return(result_post.returns) or 0.0)
                - (total_return(result_pre.returns) or 0.0)
            ),
            "delta_max_drawdown": safe_float(
                ((safe_float(result_post.max_drawdown) or 0.0) - (safe_float(result_pre.max_drawdown) or 0.0))
            ),
            "trend_exposure_scale_avg": safe_float(pd.Series(scale_rebal).mean()) if scale_rebal else None,
            "trend_exposure_scale_min": safe_float(pd.Series(scale_rebal).min()) if scale_rebal else None,
        },
        "turnover": {
            "weekly_avg_pre_risk": compute_weekly_turnover_from_targets(targets_pre),
            "weekly_avg_post_trend": compute_weekly_turnover_from_targets(targets_post),
        },
        "outputs": {
            "manifest": "run_manifest.json",
            "targets_pre_risk": "targets_pre_risk.csv",
            "targets_post_trend": "targets_post_trend.csv",
            "positions": "positions.csv",
            "returns": "returns.csv",
            "equity": "equity_curve.csv",
            "benchmark_returns": "benchmark_returns.csv",
            "trend_regime": "trend_regime.csv",
            "stop_loss_events": "stop_loss_events.csv",
            "stop_loss_outcomes": "stop_loss_outcomes.csv",
            "attribution_cross_section": os.path.join("attribution", "rebalance_cross_section.csv"),
            "attribution_summary": os.path.join("attribution", "pipeline_summary.csv"),
        },
    }

    return metrics
