from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from qlib_bt.llm_agent.run_loader import (
    RunArtifacts,
    enumerate_run_artifacts,
    load_csv,
    load_json,
    validate_run_artifacts,
)


@dataclass(frozen=True)
class ContextPackResult:
    context_pack: Dict[str, Any]
    warnings: List[str]


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:
            return None
        return v
    except Exception:
        return None


def _mean_num(s: pd.Series) -> Optional[float]:
    try:
        x = pd.to_numeric(s, errors="coerce").dropna()
        if x.empty:
            return None
        return float(x.mean())
    except Exception:
        return None


def _ann_return_from_daily_returns(s: pd.Series, periods_per_year: int = 252) -> Optional[float]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return None
    total = float((1.0 + s).prod())
    n = int(len(s))
    if n <= 0:
        return None
    return _safe_float(total ** (float(periods_per_year) / float(n)) - 1.0)


def _load_factor_signals(fa_dir: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    try:
        for name in os.listdir(fa_dir):
            if not name.startswith("signal="):
                continue
            sub = os.path.join(fa_dir, name)
            metrics_path = os.path.join(sub, "metrics.json")
            if not os.path.exists(metrics_path):
                continue

            m = load_json(metrics_path)
            sig_name = (m.get("signal") or {}).get("name") or name.replace("signal=", "")

            ic_ir = None
            ic_mean = None
            try:
                rank_ic = ((m.get("ic") or {}).get("rank_ic") or {})
                ic_ir = _safe_float(rank_ic.get("ir"))
                ic_mean = _safe_float(rank_ic.get("mean"))
            except Exception:
                pass

            spread_ir = None
            try:
                spread = ((m.get("quantile") or {}).get("top_minus_bottom_daily") or {})
                spread_ir = _safe_float(spread.get("ir"))
            except Exception:
                pass

            turnover = None
            try:
                turnover = _safe_float(((m.get("turnover") or {}).get("top_group_avg")))
            except Exception:
                pass

            out.append(
                {
                    "name": str(sig_name),
                    "ic_ir": ic_ir,
                    "ic_mean": ic_mean,
                    "spread_ir": spread_ir,
                    "turnover": turnover,
                }
            )
    except Exception:
        return out

    out.sort(key=lambda r: (r.get("ic_ir") is None, -(r.get("ic_ir") or 0.0)))
    return out


def _merge_stop_loss_samples(
    *,
    out_dir: str,
    events_rel: Optional[str],
    outcomes_rel: Optional[str],
    limit: int = 10,
) -> Tuple[int, List[Dict[str, Any]]]:
    if not events_rel:
        return 0, []

    try:
        df_ev = pd.read_csv(os.path.join(out_dir, events_rel))
    except Exception:
        return 0, []

    if df_ev is None or df_ev.empty:
        return 0, []

    df_ev = df_ev.copy()
    if "date" in df_ev.columns:
        df_ev["date"] = pd.to_datetime(df_ev["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    df_out = None
    if outcomes_rel and os.path.exists(os.path.join(out_dir, outcomes_rel)):
        try:
            df_out = pd.read_csv(os.path.join(out_dir, outcomes_rel))
            df_out = df_out.copy()
            if "date" in df_out.columns:
                df_out["date"] = pd.to_datetime(df_out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        except Exception:
            df_out = None

    if df_out is not None and not df_out.empty:
        df = df_ev.merge(df_out, how="left", on=["date", "instrument"], suffixes=("", ""))
    else:
        df = df_ev

    count = int(len(df))

    if "drawdown" in df.columns:
        dd = pd.to_numeric(df["drawdown"], errors="coerce")
        df = df.assign(_abs_dd=dd.abs()).sort_values("_abs_dd", ascending=False)

    sample: List[Dict[str, Any]] = []
    for _, r in df.head(int(limit)).iterrows():
        sample.append(
            {
                "date": str(r.get("date", "")),
                "instrument": str(r.get("instrument", "")),
                "drawdown": _safe_float(r.get("drawdown")),
                "fwd_ret_5d": _safe_float(r.get("fwd_ret_5d")),
                "fwd_ret_20d": _safe_float(r.get("fwd_ret_20d")),
            }
        )

    return count, sample


def build_context_pack(out_dir: str) -> ContextPackResult:
    arts: RunArtifacts = enumerate_run_artifacts(out_dir)
    ok, missing = validate_run_artifacts(arts)

    warnings: List[str] = []
    if not ok:
        raise FileNotFoundError(f"missing required artifacts: {missing}")

    manifest = load_json(os.path.join(out_dir, arts.manifest.relpath))
    metrics = load_json(os.path.join(out_dir, arts.metrics.relpath))

    start = None
    end = None
    try:
        params = manifest.get("params") or {}
        start = str(params.get("start")) if params.get("start") is not None else None
        end = str(params.get("end")) if params.get("end") is not None else None
    except Exception:
        pass

    if not start or not end:
        try:
            params2 = metrics.get("params") or {}
            start = start or str(params2.get("start"))
            end = end or str(params2.get("end"))
        except Exception:
            pass

    if not start or not end:
        warnings.append("run date range missing from manifest/metrics")
        start = start or ""
        end = end or ""

    # Strategy metrics (prefer metrics.json computed by the backtest)
    pre = (metrics.get("pre_risk") or {})
    post = (metrics.get("post_risk") or {})
    attrib = (metrics.get("attribution") or {})

    strategy_pre = {
        "ann_return": _safe_float(pre.get("ann_return")),
        "ann_vol": _safe_float(pre.get("ann_vol")),
        "sharpe": _safe_float(pre.get("sharpe")),
        "max_drawdown": _safe_float(pre.get("max_drawdown")),
    }
    strategy_post = {
        "ann_return": _safe_float(post.get("ann_return")),
        "ann_vol": _safe_float(post.get("ann_vol")),
        "sharpe": _safe_float(post.get("sharpe")),
        "max_drawdown": _safe_float(post.get("max_drawdown")),
    }

    delta = {
        "delta_ann_return": _safe_float(attrib.get("delta_ann_return")),
        "delta_max_drawdown": _safe_float(attrib.get("delta_max_drawdown")),
    }

    # Benchmarks
    bench_list: List[str] = []
    try:
        bench_list = list((metrics.get("params") or {}).get("benchmarks") or [])
    except Exception:
        bench_list = []

    excess_ann: Dict[str, Dict[str, Optional[float]]] = {}
    for b in bench_list:
        ann = None
        ex_path = os.path.join(out_dir, f"excess_vs_{b}.csv")
        if os.path.exists(ex_path):
            try:
                df_ex = pd.read_csv(ex_path)
                if "excess" in df_ex.columns:
                    s = pd.Series(df_ex["excess"])
                    ann = _ann_return_from_daily_returns(s)
            except Exception:
                ann = None
        excess_ann[str(b)] = {"ann_return": ann}

    # Pipeline summary
    pipe_cov = {"universe_mean": None, "eligible_mean": None}
    pipe_filters = {"liquidity_bind_rate": None, "tradable_bind_rate": None}

    if arts.attribution_summary is not None:
        try:
            df_ps = load_csv(os.path.join(out_dir, arts.attribution_summary.relpath))
            if df_ps is not None and not df_ps.empty:
                if "universe" in df_ps.columns:
                    pipe_cov["universe_mean"] = _mean_num(df_ps["universe"])
                if "eligible" in df_ps.columns:
                    pipe_cov["eligible_mean"] = _mean_num(df_ps["eligible"])

                if "liquidity_bind_rate" in df_ps.columns:
                    pipe_filters["liquidity_bind_rate"] = _mean_num(df_ps["liquidity_bind_rate"])
                if "tradable_bind_rate" in df_ps.columns:
                    pipe_filters["tradable_bind_rate"] = _mean_num(df_ps["tradable_bind_rate"])
        except Exception:
            warnings.append("failed to parse attribution/pipeline_summary.csv")

    turnover = (metrics.get("turnover") or {})

    # Trend trigger rate
    trigger_rate = None
    if arts.trend_regime is not None:
        try:
            df_tr = load_csv(os.path.join(out_dir, arts.trend_regime.relpath))
            if df_tr is not None and not df_tr.empty and "risk_off" in df_tr.columns:
                trigger_rate = _safe_float(pd.to_numeric(df_tr["risk_off"], errors="coerce").mean())
        except Exception:
            warnings.append("failed to parse trend_regime.csv")

    # Stop-loss events
    sl_count, sl_sample = _merge_stop_loss_samples(
        out_dir=out_dir,
        events_rel=arts.stop_loss_events.relpath if arts.stop_loss_events is not None else None,
        outcomes_rel=arts.stop_loss_outcomes.relpath if arts.stop_loss_outcomes is not None else None,
        limit=10,
    )

    # Factor signals
    factor_signals: List[Dict[str, Any]] = []
    if arts.factor_analysis_dir is not None:
        fa_dir = os.path.join(out_dir, arts.factor_analysis_dir.relpath)
        factor_signals = _load_factor_signals(fa_dir)

    ctx: Dict[str, Any] = {
        "schema_version": 1,
        "run": {
            "out_dir": str(out_dir).replace("\\", "/"),
            "git_head": metrics.get("run", {}).get("git_head") if isinstance(metrics.get("run"), dict) else None,
            "start": str(start),
            "end": str(end),
        },
        "strategy": {
            "pre_risk": strategy_pre,
            "post_risk": strategy_post,
            "delta": delta,
        },
        "benchmarks": {
            "list": bench_list,
            "excess": excess_ann,
        },
        "pipeline": {
            "coverage": pipe_cov,
            "filters": pipe_filters,
            "turnover": {
                "weekly_avg_pre_risk": _safe_float(turnover.get("weekly_avg_pre_risk")),
                "weekly_avg_post_trend": _safe_float(turnover.get("weekly_avg_post_trend")),
            },
        },
        "risk": {
            "trend": {
                "ma": int((metrics.get("params") or {}).get("trend_ma") or 0),
                "risk_off_scale": _safe_float((metrics.get("params") or {}).get("trend_risk_off_scale")) or 0.0,
                "trigger_rate": trigger_rate,
            },
            "stop_loss": {
                "threshold": _safe_float((metrics.get("params") or {}).get("stop_loss")),
                "events": {"count": int(sl_count), "sample": sl_sample},
            },
        },
        "factors": {
            "signals": factor_signals,
        },
        "artifacts": {
            "metrics": os.path.join(out_dir, arts.metrics.relpath).replace("\\", "/"),
            "manifest": os.path.join(out_dir, arts.manifest.relpath).replace("\\", "/"),
        },
    }

    return ContextPackResult(context_pack=ctx, warnings=warnings)


def write_context_pack(out_dir: str, path: Optional[str] = None) -> str:
    res = build_context_pack(out_dir)

    out_path = path or os.path.join(out_dir, "llm_context_pack.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res.context_pack, f, ensure_ascii=False, indent=2)
    return out_path
