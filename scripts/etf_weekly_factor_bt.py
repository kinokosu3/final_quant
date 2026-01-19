import argparse
import datetime as dt
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd


def _parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _read_calendar(path: str) -> List[pd.Timestamp]:
    out: List[pd.Timestamp] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(pd.Timestamp(line))
    return out


def _read_instruments(path: str) -> List[str]:
    out: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            code = line.split("\t", 1)[0]
            out.append(code)
    return out


def _to_qlib_inst(inst: str) -> str:
    return (inst or "").strip().lower()


def _iter_weekly_rebalance_days(calendar: Sequence[pd.Timestamp]) -> List[pd.Timestamp]:
    # Use the first trading day of each ISO week.
    out: List[pd.Timestamp] = []
    last_key: Optional[Tuple[int, int]] = None
    for d in calendar:
        iso = d.isocalendar()
        key = (int(iso.year), int(iso.week))
        if key != last_key:
            out.append(d)
            last_key = key
    return out


@dataclass(frozen=True)
class FactorConfig:
    name: str
    expr: str
    weight: float


def _build_minimal_factors() -> List[FactorConfig]:
    # Keep operators minimal: Ref + Mean + arithmetic.
    # Note: Qlib expects field strings like '$close' and expression strings like 'Ref($close, 20)'.
    return [
        FactorConfig(
            name="mom20",
            expr="($close / Ref($close, 20)) - 1",
            weight=0.6,
        ),
        FactorConfig(
            name="trend_5_20",
            expr="(Mean($close, 5) / Mean($close, 20)) - 1",
            weight=0.4,
        ),
        FactorConfig(
            name="rev5",
            expr="-1 * (($close / Ref($close, 5)) - 1)",
            weight=0.2,
        ),
    ]


def _init_qlib(provider_uri: str) -> None:
    import qlib

    qlib.init(provider_uri=provider_uri, region="cn")


def _qlib_features(
    instruments: Sequence[str],
    fields: Sequence[str],
    start_time: str,
    end_time: str,
    freq: str = "day",
) -> pd.DataFrame:
    # Avoid D.features() to bypass disk_cache positional arg incompatibilities.
    from qlib.data.data import DatasetD

    return DatasetD.dataset(
        instruments,
        list(fields),
        start_time=start_time,
        end_time=end_time,
        freq=freq,
        inst_processors=[],
    )


def _select_targets_by_date(
    instruments: Sequence[str],
    rebalance_days: Sequence[pd.Timestamp],
    start_time: str,
    end_time: str,
    top_n: int,
    min_money_20d: float,
) -> Dict[pd.Timestamp, Dict[str, float]]:
    factors = _build_minimal_factors()

    factor_fields = [f.expr for f in factors]
    aux_fields = [
        "Mean($money, 20)",
        "$volume",
    ]

    fields = factor_fields + aux_fields

    df = _qlib_features(instruments, fields, start_time=start_time, end_time=end_time, freq="day")
    if df is None or df.empty:
        raise RuntimeError("qlib returned empty factor frame")

    # MultiIndex expected: instrument, datetime
    if not isinstance(df.index, pd.MultiIndex) or df.index.names != ["instrument", "datetime"]:
        raise RuntimeError("unexpected qlib index; expected MultiIndex ['instrument','datetime']")

    targets: Dict[pd.Timestamp, Dict[str, float]] = {}

    for d in rebalance_days:
        try:
            cross = df.xs(d, level="datetime")
        except KeyError:
            continue

        if cross is None or cross.empty:
            continue

        # columns in cross are exactly `fields`.
        cross = cross.copy()

        money20 = cross[aux_fields[0]]
        vol = cross[aux_fields[1]]

        liquid = money20 >= float(min_money_20d)
        tradable = vol > 0

        valid = liquid & tradable
        cross = cross[valid]
        if cross.empty:
            continue

        score = pd.Series(0.0, index=cross.index)
        for fc in factors:
            s = cross[fc.expr]
            # Minimal robustness: drop inf.
            s = s.replace([pd.NA, pd.NaT], pd.NA)
            s = pd.to_numeric(s, errors="coerce")
            score = score.add(s.fillna(0.0) * float(fc.weight), fill_value=0.0)

        score = score.replace([float("inf"), float("-inf")], pd.NA).dropna()
        if score.empty:
            continue

        picked = score.sort_values(ascending=False).head(int(top_n)).index.tolist()
        if not picked:
            continue

        w = 1.0 / float(len(picked))
        targets[d] = {inst: w for inst in picked}

    return targets


def _collect_union_universe(targets_by_day: Dict[pd.Timestamp, Dict[str, float]]) -> List[str]:
    s: Set[str] = set()
    for m in targets_by_day.values():
        s.update(m.keys())
    return sorted(s)


def _dump_targets(targets_by_day: Dict[pd.Timestamp, Dict[str, float]], out_dir: str) -> None:
    rows: List[Dict[str, object]] = []
    for d, w_map in sorted(targets_by_day.items(), key=lambda x: x[0]):
        for inst, w in sorted(w_map.items()):
            rows.append({"date": str(_dt_to_ts(d).date()), "instrument": inst, "target_weight": float(w)})
    if not rows:
        return
    _ensure_dir(out_dir)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "targets.csv"), index=False)


def _load_ohlcv_for_backtrader(
    instruments: Sequence[str],
    start_time: str,
    end_time: str,
) -> Dict[str, pd.DataFrame]:
    fields = ["$open", "$high", "$low", "$close", "$volume", "$money"]
    df = _qlib_features(instruments, fields, start_time=start_time, end_time=end_time, freq="day")
    if df is None or df.empty:
        raise RuntimeError("qlib returned empty OHLCV frame")

    out: Dict[str, pd.DataFrame] = {}
    for inst in instruments:
        if inst not in df.index.get_level_values("instrument"):
            continue
        sub = df.xs(inst, level="instrument")
        sub = sub.copy()
        sub.index = pd.to_datetime(sub.index)
        sub = sub.sort_index()
        sub.columns = ["open", "high", "low", "close", "volume", "money"]
        # Backtrader expects float columns.
        for c in ("open", "high", "low", "close", "volume", "money"):
            sub[c] = pd.to_numeric(sub[c], errors="coerce")
        sub = sub.dropna(subset=["close"])
        out[inst] = sub

    return out


def _is_tradable_in_bt(data) -> bool:
    try:
        v = float(data.volume[0])
    except Exception:
        return False
    return v > 0


def _dt_to_ts(d) -> pd.Timestamp:
    if isinstance(d, pd.Timestamp):
        return d.normalize()
    return pd.Timestamp(d).normalize()


def run_backtrader(
    price_map: Dict[str, pd.DataFrame],
    targets_by_day: Dict[pd.Timestamp, Dict[str, float]],
    start_time: str,
    end_time: str,
    cash: float,
    commission: float,
    slippage: float,
    stamp_duty: float,
    out_dir: str,
) -> None:
    import backtrader as bt

    class PandasOHLCV(bt.feeds.PandasData):
        lines = ("money",)
        params = (
            ("datetime", None),
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("close", "close"),
            ("volume", "volume"),
            ("openinterest", None),
            ("money", "money"),
        )

    class AShareLikeCommission(bt.CommInfoBase):
        params = (
            ("commission", commission),
            ("stamp_duty", stamp_duty),
            ("stocklike", True),
            ("commtype", bt.CommInfoBase.COMM_PERC),
            ("percabs", True),
        )

        def _getcommission(self, size, price, pseudoexec):
            value = abs(size) * price
            c = value * float(self.p.commission)
            if size < 0:
                c += value * float(self.p.stamp_duty)
            return c

    class WeeklyRebalanceStrategy(bt.Strategy):
        params = (
            ("targets_by_day", None),
            ("out_dir", None),
        )

        def __init__(self):
            self._targets_by_day = self.p.targets_by_day or {}
            self._rebal_days = sorted(_dt_to_ts(d) for d in self._targets_by_day.keys())
            self._positions_log: List[Dict[str, object]] = []

        def notify_order(self, order):
            # Keep hook in case we want to log trades later.
            if order.status not in (order.Completed, order.Canceled, order.Margin, order.Rejected):
                return

        def next(self):
            if not self.datas:
                return

            cur_dt = _dt_to_ts(self.datas[0].datetime.date(0))

            # Daily positions snapshot.
            value = float(self.broker.getvalue())
            for d in self.datas:
                inst = d._name
                pos = self.getposition(d)
                if pos.size == 0:
                    continue
                price = float(d.close[0]) if len(d.close) else float("nan")
                pos_value = float(pos.size) * price
                self._positions_log.append(
                    {
                        "date": str(cur_dt.date()),
                        "instrument": inst,
                        "size": float(pos.size),
                        "close": price,
                        "position_value": pos_value,
                        "portfolio_value": value,
                    }
                )

            if cur_dt not in self._targets_by_day:
                return

            targets = self._targets_by_day[cur_dt]

            # Sell dropped names first.
            for d in self.datas:
                inst = d._name
                pos = self.getposition(d)
                if pos.size == 0:
                    continue
                if inst in targets:
                    continue
                if not _is_tradable_in_bt(d):
                    continue
                self.order_target_percent(d, target=0.0)

            # Buy/adjust selected names.
            for d in self.datas:
                inst = d._name
                if inst not in targets:
                    continue
                if not _is_tradable_in_bt(d):
                    continue
                self.order_target_percent(d, target=float(targets[inst]))

        def stop(self):
            if not self._positions_log:
                return
            if not self.p.out_dir:
                return
            _ensure_dir(self.p.out_dir)
            pd.DataFrame(self._positions_log).to_csv(os.path.join(self.p.out_dir, "positions.csv"), index=False)

    cerebro = bt.Cerebro(stdstats=False)

    cerebro.broker.setcash(float(cash))
    cerebro.broker.addcommissioninfo(AShareLikeCommission())
    if slippage is not None and float(slippage) > 0:
        cerebro.broker.set_slippage_perc(perc=float(slippage))

    # Feeds
    for inst, df in price_map.items():
        data = PandasOHLCV(dataname=df, fromdate=pd.Timestamp(start_time), todate=pd.Timestamp(end_time))
        cerebro.adddata(data, name=inst)

    cerebro.addstrategy(WeeklyRebalanceStrategy, targets_by_day=targets_by_day, out_dir=out_dir)

    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="timereturn")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")

    results = cerebro.run(runonce=True, stdstats=False)
    strat = results[0]

    _ensure_dir(out_dir)

    # Save equity curve as cumulative from TimeReturn
    tr = strat.analyzers.timereturn.get_analysis()
    s = pd.Series(tr)
    s.index = pd.to_datetime(s.index)
    equity = (1.0 + s).cumprod() * float(cash)
    equity.to_csv(os.path.join(out_dir, "equity_curve.csv"), header=["equity"])  # type: ignore

    dd = strat.analyzers.drawdown.get_analysis()

    with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"start={start_time} end={end_time}\n")
        f.write(f"cash={cash}\n")
        f.write(f"commission={commission} slippage={slippage} stamp_duty={stamp_duty}\n")
        f.write(f"final_value={cerebro.broker.getvalue():.2f}\n")
        f.write(f"max_drawdown={dd.get('max', {}).get('drawdown', None)}\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Weekly long-only ETF portfolio from Qlib factors -> Backtrader")
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

    args = p.parse_args(list(argv) if argv is not None else None)

    cal_path = os.path.join(args.qlib_dir, "calendars", "day.txt")
    inst_path = os.path.join(args.qlib_dir, "instruments", "all.txt")

    calendar = _read_calendar(cal_path)
    if not calendar:
        raise RuntimeError("calendar is empty")

    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end)

    calendar = [d for d in calendar if start_ts <= d <= end_ts]
    if not calendar:
        raise RuntimeError("no calendar days in the requested range")

    rebalance_days = _iter_weekly_rebalance_days(calendar)

    instruments_raw = _read_instruments(inst_path)
    instruments = [_to_qlib_inst(x) for x in instruments_raw]

    _init_qlib(args.qlib_dir)

    targets_by_day = _select_targets_by_date(
        instruments=instruments,
        rebalance_days=rebalance_days,
        start_time=str(calendar[0].date()),
        end_time=str(calendar[-1].date()),
        top_n=args.top_n,
        min_money_20d=args.min_money_20d,
    )

    used_universe = _collect_union_universe(targets_by_day)

    price_map = _load_ohlcv_for_backtrader(
        instruments=used_universe,
        start_time=str(calendar[0].date()),
        end_time=str(calendar[-1].date()),
    )

    # Normalize keys to match BT data names.
    targets_norm: Dict[pd.Timestamp, Dict[str, float]] = {}
    for d, m in targets_by_day.items():
        targets_norm[_dt_to_ts(d)] = {k: float(v) for k, v in m.items() if k in price_map}

    _dump_targets(targets_norm, args.out_dir)

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
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
