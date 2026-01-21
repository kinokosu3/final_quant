import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Sequence

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
from qlib_bt.exports import dump_targets_csv, dump_targets_csv_named
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
from qlib_bt.risk import (
    apply_exposure_scale,
    build_strategy_metrics,
    compute_weekly_turnover_from_targets,
    export_stop_loss_outcomes,
    export_trend_regime,
    fetch_benchmark_close,
    fetch_benchmark_returns,
    trend_scale_by_day,
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


def _parse_comma_list(s: str) -> List[str]:
    if not s:
        return []
    out: List[str] = []
    for part in str(s).replace(";", ",").split(","):
        part = part.strip()
        if part:
            out.append(part)
    return out


def _get_git_head() -> Optional[str]:
    try:
        import subprocess

        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            stderr=subprocess.DEVNULL,
        )
        s = out.decode("utf-8", errors="ignore").strip()
        return s or None
    except Exception:
        return None


def _write_run_manifest(out_dir: str, obj: Dict[str, object]) -> None:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "run_manifest.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _export_rebalance_cross_section(
    *,
    factor_daily: pd.DataFrame,
    rebalance_days: Sequence[pd.Timestamp],
    targets_by_day: Dict[pd.Timestamp, Dict[str, float]],
    min_money_20d: float,
    out_dir: str,
) -> str:
    """Export one table for all rebalance dates: factor values + flags + ranks."""

    os.makedirs(out_dir, exist_ok=True)

    rows: List[pd.DataFrame] = []

    money_col = "Mean($money, 20)"
    vol_col = "$volume"

    for d in rebalance_days:
        d = dt_to_ts(d)
        try:
            cross = factor_daily.xs(d, level="datetime").copy()
        except KeyError:
            continue
        if cross is None or cross.empty:
            continue

        cross["date"] = str(d.date())
        cross["instrument"] = cross.index

        money20 = pd.to_numeric(cross.get(money_col), errors="coerce")
        vol = pd.to_numeric(cross.get(vol_col), errors="coerce")

        cross["flag_liquid"] = (money20 >= float(min_money_20d)).astype(int)
        cross["flag_tradable"] = (vol > 0).astype(int)
        cross["flag_valid"] = ((cross["flag_liquid"] == 1) & (cross["flag_tradable"] == 1)).astype(int)

        score = pd.to_numeric(cross.get("score"), errors="coerce")
        cross["score"] = score

        cross["rank_score"] = score.rank(ascending=False, method="first")
        picked = set((targets_by_day.get(d) or {}).keys())
        cross["flag_picked"] = cross["instrument"].map(lambda x: 1 if str(x) in picked else 0)

        cross = cross.reset_index(drop=True)
        rows.append(cross)

    if not rows:
        return ""

    df = pd.concat(rows, ignore_index=True, sort=False)
    path = os.path.join(out_dir, "rebalance_cross_section.csv")
    df.to_csv(path, index=False)
    return path


def _export_pipeline_summary(
    *,
    factor_daily: pd.DataFrame,
    rebalance_days: Sequence[pd.Timestamp],
    targets_pre: Dict[pd.Timestamp, Dict[str, float]],
    targets_post: Dict[pd.Timestamp, Dict[str, float]],
    min_money_20d: float,
    out_dir: str,
) -> str:
    os.makedirs(out_dir, exist_ok=True)

    money_col = "Mean($money, 20)"
    vol_col = "$volume"

    rows: List[Dict[str, object]] = []

    for d in rebalance_days:
        d = dt_to_ts(d)
        try:
            cross = factor_daily.xs(d, level="datetime")
        except KeyError:
            continue
        if cross is None or cross.empty:
            continue

        money20 = pd.to_numeric(cross.get(money_col), errors="coerce")
        vol = pd.to_numeric(cross.get(vol_col), errors="coerce")

        liquid = (money20 >= float(min_money_20d)).fillna(False)
        tradable = (vol > 0).fillna(False)
        valid = liquid & tradable

        picked_pre = set((targets_pre.get(d) or {}).keys())
        picked_post = set((targets_post.get(d) or {}).keys())

        universe_n = int(len(cross))
        liquid_n = int(liquid.sum())
        tradable_n = int(tradable.sum())
        eligible_n = int(valid.sum())

        rows.append(
            {
                "date": str(d.date()),
                "universe": universe_n,
                "liquid": liquid_n,
                "tradable": tradable_n,
                "eligible": eligible_n,
                "liquidity_bind_rate": float(1.0 - (float(liquid_n) / float(universe_n)))
                if universe_n > 0
                else None,
                "tradable_bind_rate": float(1.0 - (float(tradable_n) / float(universe_n)))
                if universe_n > 0
                else None,
                "selected_pre": int(len(picked_pre)),
                "selected_post": int(len(picked_post)),
                "exposure_scale": float(sum((targets_post.get(d) or {}).values())),
            }
        )

    if not rows:
        return ""

    path = os.path.join(out_dir, "pipeline_summary.csv")
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


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
        "--benchmarks",
        default="510300.SH,510500.SH",
        help="comma-separated benchmark instruments (qlib format)",
    )
    p.add_argument(
        "--trend_ma",
        type=int,
        default=20,
        help="benchmark trend filter MA window",
    )
    p.add_argument(
        "--trend_risk_off_scale",
        type=float,
        default=0.3,
        help="portfolio exposure scale when benchmark close < MA",
    )
    p.add_argument(
        "--stop_loss",
        type=float,
        default=0.10,
        help="stop-loss drawdown from entry (e.g. 0.10 means -10%%), executed next day",
    )
    p.add_argument(
        "--disable_risk",
        action="store_true",
        help="Disable trend filter and stop-loss risk controls",
    )

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

    risk_enabled = not bool(args.disable_risk)

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

    # Baseline (pre-risk) targets.
    dump_targets_csv_named(
        targets_norm,
        args.out_dir,
        filename="targets_pre_risk.csv",
        stock_name_map=stock_name_map,
    )

    # Benchmark returns (for later excess-return exports and trend filter).
    benchmark_insts = _parse_comma_list(str(args.benchmarks))
    bm_rets = fetch_benchmark_returns(
        benchmark_insts=benchmark_insts,
        start_time=str(calendar[0].date()),
        end_time=str(calendar[-1].date()),
    )

    # Trend filter uses the first benchmark as the regime proxy.
    scale_by_day: Dict[pd.Timestamp, float] = {}
    if bm_rets:
        primary = benchmark_insts[0] if benchmark_insts else list(bm_rets.keys())[0]
        bm_close_frame = qlib_features(
            instruments=[to_qlib_inst(primary)],
            fields=["$close"],
            start_time=str(calendar[0].date()),
            end_time=str(calendar[-1].date()),
            freq="day",
        )
        try:
            bm_close = bm_close_frame.xs(to_qlib_inst(primary), level="instrument")["$close"]
            bm_close.index = pd.to_datetime(bm_close.index).tz_localize(None)
            scale_by_day = _trend_scale_by_day(
                calendar=calendar,
                benchmark_close=bm_close,
                ma_window=int(args.trend_ma),
                risk_off_scale=float(args.trend_risk_off_scale),
            )
        except Exception:
            scale_by_day = {}

    targets_post_trend = _apply_exposure_scale(targets_norm, scale_by_day)

    # Persist daily trend regime information so an offline agent can attribute trend behavior.
    try:
        if scale_by_day and "bm_close" in locals() and bm_close is not None and not bm_close.empty:
            bm_close_s = pd.to_numeric(bm_close, errors="coerce").dropna().sort_index()
            bm_close_s.index = pd.to_datetime(bm_close_s.index).tz_localize(None)
            bm_ret = (bm_close_s / bm_close_s.shift(1) - 1.0).dropna()
            bm_ma = bm_close_s.rolling(int(max(int(args.trend_ma), 1))).mean()

            df_trend = pd.DataFrame(index=pd.to_datetime(sorted(scale_by_day.keys())))
            df_trend.index = df_trend.index.tz_localize(None)
            df_trend["exposure_scale"] = pd.Series(scale_by_day)
            df_trend["bm_close"] = bm_close_s.reindex(df_trend.index)
            df_trend["bm_ma"] = bm_ma.reindex(df_trend.index)
            df_trend["bm_return"] = bm_ret.reindex(df_trend.index)
            df_trend["risk_off"] = (pd.to_numeric(df_trend["exposure_scale"], errors="coerce") < 1.0).astype(int)
            df_trend.to_csv(os.path.join(args.out_dir, "trend_regime.csv"), index=True)
    except Exception:
        pass

    # Post-trend targets (still before stop-loss, which is implemented inside Backtrader).
    dump_targets_csv_named(
        targets_post_trend,
        args.out_dir,
        filename="targets_post_trend.csv",
        stock_name_map=stock_name_map,
    )

    # Keep legacy filename for backwards compatibility.
    dump_targets_csv(targets_post_trend, args.out_dir, stock_name_map=stock_name_map)

    # Attribution exports from the selection pipeline.
    attrib_dir = os.path.join(args.out_dir, "attribution")
    _export_rebalance_cross_section(
        factor_daily=factor_daily,
        rebalance_days=rebalance_days,
        targets_by_day=targets_norm,
        min_money_20d=float(args.min_money_20d),
        out_dir=attrib_dir,
    )
    _export_pipeline_summary(
        factor_daily=factor_daily,
        rebalance_days=rebalance_days,
        targets_pre=targets_norm,
        targets_post=targets_post_trend,
        min_money_20d=float(args.min_money_20d),
        out_dir=attrib_dir,
    )

    # v0 run manifest (ensures later changes are attributable).
    manifest: Dict[str, object] = {
        "schema_version": 1,
        "kind": "etf_weekly_factor_bt",
        "git_head": _get_git_head(),
        "params": vars(args),
        "dataset": {
            "qlib_dir": str(args.qlib_dir),
            "inst_path": str(inst_path),
            "cal_path": str(cal_path),
        },
        "universe_size": int(len(instruments)),
        "rebalance_days": int(len(rebalance_days)),
        "turnover_weekly_avg_pre_risk": _compute_weekly_turnover_from_targets(targets_norm),
        "turnover_weekly_avg_post_trend": _compute_weekly_turnover_from_targets(targets_post_trend),
        "benchmarks": benchmark_insts,
        "outputs": {
            "targets_pre_risk": "targets_pre_risk.csv",
            "targets_post_trend": "targets_post_trend.csv",
            "targets_legacy": "targets.csv",
            "attribution_cross_section": os.path.join("attribution", "rebalance_cross_section.csv"),
            "attribution_summary": os.path.join("attribution", "pipeline_summary.csv"),
            "returns": "returns.csv",
            "equity": "equity_curve.csv",
            "benchmark_returns": "benchmark_returns.csv",
        },
    }
    _write_run_manifest(args.out_dir, manifest)

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

    def _safe_float(x: object) -> Optional[float]:
        try:
            if x is None:
                return None
            v = float(x)  # type: ignore[arg-type]
            if v != v:
                return None
            return v
        except Exception:
            return None

    def _total_return(returns: pd.Series) -> Optional[float]:
        if returns is None or returns.empty:
            return None
        s = pd.to_numeric(returns, errors="coerce").dropna()
        if s.empty:
            return None
        return _safe_float((1.0 + s).prod() - 1.0)

    def _ann_return(returns: pd.Series, periods_per_year: int = 252) -> Optional[float]:
        if returns is None or returns.empty:
            return None
        s = pd.to_numeric(returns, errors="coerce").dropna()
        if s.empty:
            return None
        total = (1.0 + s).prod()
        n = int(len(s))
        if n <= 0:
            return None
        return _safe_float(total ** (float(periods_per_year) / float(n)) - 1.0)

    def _ann_vol(returns: pd.Series, periods_per_year: int = 252) -> Optional[float]:
        if returns is None or returns.empty:
            return None
        s = pd.to_numeric(returns, errors="coerce").dropna()
        if s.empty:
            return None
        return _safe_float(float(s.std(ddof=0)) * (float(periods_per_year) ** 0.5))

    def _sharpe(returns: pd.Series, rf: float = 0.0, periods_per_year: int = 252) -> Optional[float]:
        if returns is None or returns.empty:
            return None
        s = pd.to_numeric(returns, errors="coerce").dropna()
        if s.empty:
            return None
        ex = s - float(rf) / float(periods_per_year)
        vol = float(ex.std(ddof=0))
        if vol <= 0:
            return None
        return _safe_float(float(ex.mean()) / vol * (float(periods_per_year) ** 0.5))

    # Pre-risk run (no trend filter, no stop-loss) for attribution.
    pre_dir = os.path.join(args.out_dir, "bt_pre_risk")
    result_pre = run_backtrader(
        price_map=price_map,
        targets_by_day=targets_norm,
        start_time=str(calendar[0].date()),
        end_time=str(calendar[-1].date()),
        cash=args.cash,
        commission=args.commission,
        slippage=args.slippage,
        stamp_duty=args.stamp_duty,
        out_dir=pre_dir,
        stock_name_map=stock_name_map,
        stop_loss=None,
    )

    # Post-risk run (trend filter + stop-loss). Keep outputs in root out_dir.
    result_post = run_backtrader(
        price_map=price_map,
        targets_by_day=targets_post_trend,
        start_time=str(calendar[0].date()),
        end_time=str(calendar[-1].date()),
        cash=args.cash,
        commission=args.commission,
        slippage=args.slippage,
        stamp_duty=args.stamp_duty,
        out_dir=args.out_dir,
        stock_name_map=stock_name_map,
        stop_loss=float(args.stop_loss),
    )

    # Benchmark / excess-return exports aligned to strategy returns index.
    if result_post.returns is not None and not result_post.returns.empty and bm_rets:
        df_bm = pd.DataFrame(index=result_post.returns.index)
        df_bm["strategy"] = result_post.returns
        for k, s in bm_rets.items():
            df_bm[f"bm_{k}"] = s.reindex(df_bm.index)
        df_bm.to_csv(os.path.join(args.out_dir, "benchmark_returns.csv"), index=True)

        for k in list(bm_rets.keys()):
            if f"bm_{k}" not in df_bm.columns:
                continue
            excess = df_bm["strategy"].fillna(0.0) - df_bm[f"bm_{k}"].fillna(0.0)
            pd.DataFrame({"excess": excess}).to_csv(
                os.path.join(args.out_dir, f"excess_vs_{k}.csv"), index=True
            )

    # Stop-loss outcome exports (forward returns after stop-loss events).
    try:
        events_path = os.path.join(args.out_dir, "stop_loss_events.csv")
        if os.path.exists(events_path):
            df_ev = pd.read_csv(events_path)
            if df_ev is not None and not df_ev.empty:
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
                        # Use next available trading day after the event date.
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

                if rows:
                    pd.DataFrame(rows).to_csv(
                        os.path.join(args.out_dir, "stop_loss_outcomes.csv"),
                        index=False,
                    )
    except Exception:
        pass

    # Strategy metrics for LLM summarization (schema_version=1).
    scale_rebal = [float(scale_by_day.get(dt_to_ts(d), 1.0)) for d in rebalance_days]
    metrics: Dict[str, object] = {
        "schema_version": 1,
        "kind": "strategy_metrics",
        "run": {
            "out_dir": str(args.out_dir),
            "git_head": _get_git_head(),
        },
        "params": {
            "start": str(args.start),
            "end": str(args.end),
            "top_n": int(args.top_n),
            "min_money_20d": float(args.min_money_20d),
            "commission": float(args.commission),
            "slippage": float(args.slippage),
            "stamp_duty": float(args.stamp_duty),
            "benchmarks": benchmark_insts,
            "trend_ma": int(args.trend_ma),
            "trend_risk_off_scale": float(args.trend_risk_off_scale),
            "stop_loss": float(args.stop_loss),
        },
        "pre_risk": {
            "out_dir": "bt_pre_risk",
            "final_value": float(result_pre.final_value),
            "max_drawdown": _safe_float(result_pre.max_drawdown),
            "total_return": _total_return(result_pre.returns),
            "ann_return": _ann_return(result_pre.returns),
            "ann_vol": _ann_vol(result_pre.returns),
            "sharpe": _sharpe(result_pre.returns),
        },
        "post_risk": {
            "out_dir": ".",
            "final_value": float(result_post.final_value),
            "max_drawdown": _safe_float(result_post.max_drawdown),
            "total_return": _total_return(result_post.returns),
            "ann_return": _ann_return(result_post.returns),
            "ann_vol": _ann_vol(result_post.returns),
            "sharpe": _sharpe(result_post.returns),
        },
        "attribution": {
            "delta_final_value": _safe_float(float(result_post.final_value) - float(result_pre.final_value)),
            "delta_total_return": _safe_float((_total_return(result_post.returns) or 0.0) - (_total_return(result_pre.returns) or 0.0)),
            "delta_max_drawdown": _safe_float(((_safe_float(result_post.max_drawdown) or 0.0) - (_safe_float(result_pre.max_drawdown) or 0.0))),
            "trend_exposure_scale_avg": _safe_float(pd.Series(scale_rebal).mean()) if scale_rebal else None,
            "trend_exposure_scale_min": _safe_float(pd.Series(scale_rebal).min()) if scale_rebal else None,
        },
        "turnover": {
            "weekly_avg_pre_risk": _compute_weekly_turnover_from_targets(targets_norm),
            "weekly_avg_post_trend": _compute_weekly_turnover_from_targets(targets_post_trend),
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

    try:
        metrics_path = os.path.join(args.out_dir, "metrics.json")
        with open(metrics_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
