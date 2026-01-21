from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from qlib_bt.exports import dump_positions_csv
from qlib_bt.io import dt_to_ts, ensure_dir, instrument_display_name
from qlib_bt.reports import write_quantstats_report


@dataclass(frozen=True)
class BacktestResult:
    returns: pd.Series
    equity: pd.Series
    final_value: float
    max_drawdown: Optional[float]


def _is_tradable_in_bt(data) -> bool:
    try:
        v = float(data.volume[0])
    except Exception:
        return False
    return v > 0


def load_ohlcv_for_backtrader(
    qlib_features_fn,
    instruments: List[str],
    start_time: str,
    end_time: str,
) -> Dict[str, pd.DataFrame]:
    fields = ["$open", "$high", "$low", "$close", "$volume", "$money"]
    df = qlib_features_fn(instruments, fields, start_time=start_time, end_time=end_time, freq="day")
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
        for c in ("open", "high", "low", "close", "volume", "money"):
            sub[c] = pd.to_numeric(sub[c], errors="coerce")
        sub = sub.dropna(subset=["close"])
        out[inst] = sub

    return out


def filter_price_map_by_start(
    price_map: Dict[str, pd.DataFrame],
    start_time: str,
) -> Tuple[Dict[str, pd.DataFrame], List[Dict[str, object]]]:
    start_ts = pd.Timestamp(start_time).normalize()
    kept: Dict[str, pd.DataFrame] = {}
    dropped: List[Dict[str, object]] = []

    for inst, df in price_map.items():
        if df is None or df.empty:
            dropped.append({"instrument": inst, "reason": "empty"})
            continue
        first = pd.to_datetime(df.index.min()).normalize()
        if first <= start_ts:
            kept[inst] = df
        else:
            dropped.append({"instrument": inst, "reason": f"first_bar={str(first.date())}"})

    return kept, dropped


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
    stock_name_map: Optional[Dict[str, str]] = None,
    stop_loss: Optional[float] = None,
) -> BacktestResult:
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
            ("stop_loss", None),
        )

        def __init__(self):
            self._targets_by_day = self.p.targets_by_day or {}
            self._rebal_days = sorted(dt_to_ts(d) for d in self._targets_by_day.keys())
            self._positions_log: List[Dict[str, object]] = []
            self._stop_loss_events: List[Dict[str, object]] = []
            self._entry_price: Dict[str, float] = {}
            self._stop_loss: Optional[float] = None
            try:
                if self.p.stop_loss is not None:
                    self._stop_loss = float(self.p.stop_loss)
            except Exception:
                self._stop_loss = None
            if self._stop_loss is not None and self._stop_loss <= 0:
                self._stop_loss = None

        def notify_order(self, order):
            if order.status not in (order.Completed, order.Canceled, order.Margin, order.Rejected):
                return

            # Track entry price on completed BUYs for stop-loss.
            try:
                if order.status == order.Completed and order.isbuy():
                    inst = getattr(order.data, "_name", None)
                    if inst:
                        px = float(getattr(order.executed, "price", 0.0) or 0.0)
                        if px > 0:
                            self._entry_price[str(inst)] = px
            except Exception:
                pass

        def next(self):
            if not self.datas:
                return

            cur_dt = dt_to_ts(self.datas[0].datetime.date(0))
            cur_key = cur_dt

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

            # Stop-loss check: evaluate on close, execute next day by issuing order_target_percent(0).
            if self._stop_loss is not None and float(self._stop_loss) > 0:
                for d in self.datas:
                    inst = d._name
                    pos = self.getposition(d)
                    if pos.size <= 0:
                        continue
                    if not _is_tradable_in_bt(d):
                        continue
                    entry = self._entry_price.get(str(inst))
                    if entry is None or entry <= 0:
                        continue
                    price = float(d.close[0]) if len(d.close) else float("nan")
                    if not (price == price) or price <= 0:
                        continue
                    dd = (price / float(entry)) - 1.0
                    if dd <= -float(self._stop_loss):
                        self._stop_loss_events.append(
                            {
                                "date": str(cur_dt.date()),
                                "instrument": str(inst),
                                "entry_price": float(entry),
                                "close": float(price),
                                "drawdown": float(dd),
                                "stop_loss": float(self._stop_loss),
                            }
                        )
                        # Force liquidation; keep remaining targets logic (rebalance) for others.
                        self.order_target_percent(d, target=0.0)

            if cur_key not in self._targets_by_day:
                return

            targets = self._targets_by_day[cur_key]
            data_by_name = {d._name: d for d in self.datas}

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

            for inst, w in targets.items():
                d = data_by_name.get(inst)
                if d is None:
                    continue
                if not _is_tradable_in_bt(d):
                    continue
                self.order_target_percent(d, target=float(w))

        def stop(self):
            if not self.p.out_dir:
                return
            ensure_dir(self.p.out_dir)
            dump_positions_csv(self._positions_log, self.p.out_dir, stock_name_map=stock_name_map)

            if self._stop_loss_events:
                pd.DataFrame(self._stop_loss_events).to_csv(
                    os.path.join(self.p.out_dir, "stop_loss_events.csv"),
                    index=False,
                )

    cerebro = bt.Cerebro(stdstats=False)

    ensure_dir(out_dir)

    try:
        keys = list(targets_by_day.keys())
        keys_ts = [dt_to_ts(k) for k in keys]
        with open(
            os.path.join(out_dir, "targets_key_info.txt"),
            "w",
            encoding="utf-8",
            newline="\n",
        ) as f:
            f.write(f"keys={len(keys)}\n")
            f.write(f"type0={type(keys[0]).__name__ if keys else ''}\n")
            f.write(f"min={str(min(keys_ts).date()) if keys_ts else ''}\n")
            f.write(f"max={str(max(keys_ts).date()) if keys_ts else ''}\n")
            for i, k in enumerate(sorted(keys_ts)[:10]):
                f.write(f"sample[{i}]={str(k)}\n")
    except Exception:
        pass

    cerebro.broker.setcash(float(cash))
    cerebro.broker.addcommissioninfo(AShareLikeCommission())
    if slippage is not None and float(slippage) > 0:
        cerebro.broker.set_slippage_perc(perc=float(slippage))

    for inst, df in price_map.items():
        data = PandasOHLCV(dataname=df, fromdate=pd.Timestamp(start_time), todate=pd.Timestamp(end_time))
        cerebro.adddata(data, name=inst)

    cerebro.addstrategy(
        WeeklyRebalanceStrategy,
        targets_by_day=targets_by_day,
        out_dir=out_dir,
        stop_loss=stop_loss,
    )
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="timereturn")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")

    results = cerebro.run(runonce=True, stdstats=False)
    strat = results[0]

    tr = strat.analyzers.timereturn.get_analysis()
    s = pd.Series(tr)
    s.index = pd.to_datetime(s.index)

    returns = pd.to_numeric(s, errors="coerce").fillna(0.0)
    returns.to_csv(os.path.join(out_dir, "returns.csv"), header=["returns"])  # type: ignore

    try:
        tr_start = str(pd.to_datetime(returns.index.min()).date()) if not returns.empty else ""
        tr_end = str(pd.to_datetime(returns.index.max()).date()) if not returns.empty else ""
        with open(
            os.path.join(out_dir, "timereturn_info.txt"),
            "w",
            encoding="utf-8",
            newline="\n",
        ) as f:
            f.write(f"rows={int(len(returns))}\n")
            f.write(f"start={tr_start}\n")
            f.write(f"end={tr_end}\n")
    except Exception:
        pass

    equity = (1.0 + returns).cumprod() * float(cash)
    equity.to_csv(os.path.join(out_dir, "equity_curve.csv"), header=["equity"])  # type: ignore

    title = "ETF Weekly Factor Backtrader"
    if stock_name_map:
        title = f"{title} (names mapped)"
    write_quantstats_report(returns=returns, out_dir=out_dir, title=title)

    dd = strat.analyzers.drawdown.get_analysis()
    max_dd = dd.get("max", {}).get("drawdown", None)

    with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"start={start_time} end={end_time}\n")
        f.write(f"cash={cash}\n")
        f.write(f"commission={commission} slippage={slippage} stamp_duty={stamp_duty}\n")
        f.write(f"final_value={cerebro.broker.getvalue():.2f}\n")
        f.write(f"max_drawdown={max_dd}\n")

    return BacktestResult(
        returns=returns,
        equity=equity,
        final_value=float(cerebro.broker.getvalue()),
        max_drawdown=max_dd,
    )
