"""Hikyuu data source implementation.

This implementation uses local hikyuu data.

Notes:
- Code format expected by callers is hikyuu market_code (e.g. 'sz000001').
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from collections import namedtuple
from typing import List, Optional, Union

import pandas as pd

from data_source.data_source import DataSourceBase

try:
    import hikyuu as hk
except Exception as exc:  # pragma: no cover
    hk = None
    _HK_IMPORT_ERROR = exc
else:
    _HK_IMPORT_ERROR = None

_HK_LOADED = False

logger = logging.getLogger(__name__)


def _get_config_path() -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = os.path.join(here, "config.ini")
    return cfg if os.path.exists(cfg) else None


def _ensure_hikyuu_loaded() -> None:
    global _HK_LOADED
    if _HK_LOADED:
        return
    if hk is None:
        raise RuntimeError(f"hikyuu is not available: {_HK_IMPORT_ERROR}")

    cfg = _get_config_path()
    if cfg and hasattr(hk, "hikyuu_init"):
        hk.hikyuu_init(cfg)
    elif cfg and hasattr(hk, "load_hikyuu"):
        try:
            hk.load_hikyuu(cfg)
        except TypeError:
            hk.load_hikyuu()
    elif hasattr(hk, "load_hikyuu"):
        hk.load_hikyuu()

    _HK_LOADED = True
def _to_date(d) -> Optional[_dt.date]:
    if d is None:
        return None
    if hasattr(d, "number"):
        val = d.number // 10000
        return _dt.datetime.strptime(str(val), "%Y%m%d").date()
    if isinstance(d, _dt.datetime):
        return d.date()
    if isinstance(d, _dt.date):
        return d

    s = str(d)
    if "-" in s:
        return _dt.datetime.strptime(s, "%Y-%m-%d").date()
    return _dt.datetime.strptime(s, "%Y%m%d").date()


def _to_hk_datetime(d) -> Optional["hk.Datetime"]:
    if d is None:
        return None
    date = _to_date(d)
    return hk.Datetime(int(date.strftime("%Y%m%d0000")))


def _to_hk_datetime_end(d) -> Optional["hk.Datetime"]:
    if d is None:
        return None
    date = _to_date(d) + _dt.timedelta(days=1)
    return hk.Datetime(int(date.strftime("%Y%m%d0000")))


def _kquery_for_dates(start_date=None, end_date=None, count=None):
    if count is not None and count > 0 and start_date is None and end_date is None:
        return hk.Query(-int(count))

    start_dt = _to_hk_datetime(start_date) if start_date else None
    end_dt = _to_hk_datetime_end(end_date) if end_date else None

    if start_dt and end_dt:
        return hk.Query(start_dt, end_dt)
    if start_dt:
        return hk.Query(start_dt, hk.Datetime(999912310000))
    if end_dt:
        return hk.Query(hk.Datetime(190001010000), end_dt)
    return hk.Query(-150)


def _map_fields(df: pd.DataFrame, fields: Optional[List[str]]) -> pd.DataFrame:
    if not fields:
        return df
    mapped = df.copy()
    if "amount" in mapped.columns and "money" in fields:
        mapped = mapped.rename(columns={"amount": "money"})
    return mapped


def _normalize_amount_unit(df: pd.DataFrame) -> pd.DataFrame:
    if "money" in df.columns:
        df["money"] = df["money"].astype(float) * 10000.0
    if "amount" in df.columns:
        df["amount"] = df["amount"].astype(float) * 10000.0
    return df


def _format_single_result(df: pd.DataFrame, fields: Optional[List[str]]) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "datetime" in out.columns:
        out = out.set_index("datetime")
    if fields:
        cols = [c for c in fields if c in out.columns]
        out = out[cols]
    return out


SecurityInfo = namedtuple(
    "SecurityInfo",
    ["display_name", "name", "start_date", "end_date", "type", "parent"],
)


def _map_stock_type(stock_type) -> str:
    mapping = {}
    if hk is not None and hasattr(hk, "constant"):
        for key in ("STOCKTYPE_A", "STOCKTYPE_B", "STOCKTYPE_INDEX", "STOCKTYPE_FUND","STOCKTYPE_ETF"):
            if hasattr(hk.constant, key):
                mapped = key.replace("STOCKTYPE_", "").lower()
                if mapped == "a":
                    mapped = "stock"
                mapping[getattr(hk.constant, key)] = mapped

    if isinstance(stock_type, str):
        mapped = stock_type.lower()
        if mapped in ("a", "stock", "stock_a"):
            return "stock"
        if mapped in ("index", "idx"):
            return "index"
        if mapped in ("fund", "etf", "lof"):
            return "fund"
        return mapped

    return mapping.get(stock_type, "unknown")


class HikyuuDataSource(DataSourceBase):
    """Hikyuu local data source implementation."""

    def __init__(self):
        """Initialize the data source and the StockManager instance."""
        _ensure_hikyuu_loaded()
        if hasattr(hk, "sm"):
            self.sm = hk.sm
        elif hasattr(hk, "StockManager"):
            # hk.StockManager().Instance() is the suggested way to get singleton
            self.sm = hk.StockManager().Instance()
        else:
            raise RuntimeError("Cannot access StockManager from hikyuu")
    # Notice: The date used here is of the hikyuu Datetime type.
    def get_security_info(self, security_code: str, date: Optional[str] = None):
        sm = self.sm
        hk_code = security_code
        stock = sm[hk_code]
        if not getattr(stock, "valid", False):
            logger.warning(f"Invalid security code: {security_code}")
            return None

        display_name = getattr(stock, "name", "")
        start_date = getattr(stock, "start_datetime", None)
        end_date = getattr(stock, "last_datetime", None)
        stock_type = _map_stock_type(getattr(stock, "type", None))

        return SecurityInfo(
            display_name=display_name,
            name=display_name,
            start_date=start_date,
            end_date=end_date,
            type=stock_type,
            parent=None,
        )

    def get_price(
        self,
        security_code: Union[str, List[str]],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        frequency: str = "day",
        fields: Optional[List[str]] = None,
        **kwargs,
    ) -> pd.DataFrame:
        # Accept JoinQuant-like naming: frequency 'day' / 'daily'.
        if isinstance(security_code, (str, bytes)):
            sec_list = [security_code]
            is_single = True
        else:
            sec_list = list(security_code)
            is_single = len(sec_list) == 1

        if fields is None:
            fields = ["open", "close", "high", "low", "volume", "money", "pre_close", "paused"]
        elif isinstance(fields, (str, bytes)):
            fields = [fields]

        count = kwargs.get("count", None)
        skip_paused = kwargs.get("skip_paused", False)
        panel = kwargs.get("panel", False)
        fill_paused = kwargs.get("fill_paused", False)

        fetch_count = None
        if count is not None and "pre_close" in fields and start_date is None and end_date is None:
            fetch_count = int(count) + 1

        sm = self.sm
        hk_codes = sec_list
        q = _kquery_for_dates(start_date=start_date, end_date=end_date, count=fetch_count or count)

        rows = []
        for hk_code in hk_codes:
            stock = sm[hk_code]
            if not getattr(stock, "valid", False):
                logger.warning(f"Invalid security code: {hk_code}")
                continue

            if hasattr(hk, "KQuery"):
                ktype = getattr(hk.KQuery, "DAY", None)
                recover_type = getattr(hk.KQuery, "NO_RECOVER", None)
                if ktype is not None:
                    kdata = stock.get_kdata(q, ktype=ktype, recover_type=recover_type)
                else:
                    kdata = stock.get_kdata(q)
            else:
                kdata = stock.get_kdata(q)

            df = kdata.to_df()
            if df.empty:
                logger.warning(f"No price data for {hk_code} in the given date range.")
                continue

            df = _map_fields(df, fields)
            df = _normalize_amount_unit(df)

            if "datetime" in df.columns:
                df["time"] = pd.to_datetime(df["datetime"])
            else:
                df["time"] = pd.NaT

            df["code"] = hk_code

            if "pre_close" in fields:
                if "close" in df.columns:
                    df = df.sort_values("time")
                    df["pre_close"] = df["close"].shift(1)

            if "paused" in fields and "paused" not in df.columns:
                df["paused"] = 0

            if "high_limit" in fields and "high_limit" not in df.columns:
                name = getattr(stock, "name", "") or ""
                is_st = "ST" in str(name).upper()
                limit_rate = 0.05 if is_st else 0.10
                base = df["pre_close"] if "pre_close" in df.columns else df["close"].shift(1)
                df["high_limit"] = base * (1.0 + limit_rate)

            rows.append(df)

        if not rows:
            return pd.DataFrame()

        merged = pd.concat(rows, ignore_index=True, sort=False)

        if end_date is not None:
            end_dt = _to_date(end_date)
            merged = merged[merged["time"].dt.date <= end_dt]
            if count is not None and int(count) > 0:
                merged = (
                    merged.sort_values(["code", "time"]).groupby("code").tail(int(count))
                )

        if skip_paused and "paused" in merged.columns:
            merged = merged[merged["paused"] != 1]

        if not fill_paused and "paused" in merged.columns:
            pass

        if is_single and not panel:
            single = merged[merged["code"] == hk_codes[0]]
            return _format_single_result(single, fields)

        keep_cols = ["time", "code"] + [c for c in fields if c in merged.columns]
        out = merged[keep_cols].sort_values(["code", "time"]).reset_index(drop=True)
        return out

    def get_all_codes(self, security_type: str = "stock", date: Optional[str] = None) -> List[str]:
        df = self.get_all_securities(types=security_type, date=date)
        return list(df.index)

    def get_all_securities(self, types: Optional[Union[str, List[str]]] = None, date: Optional[str] = None) -> pd.DataFrame:
        sm = self.sm
        if types is None:
            types = []
        if isinstance(types, (str, bytes)):
            types = [types]

        type_filter = set(t.lower() for t in types)
        _ = _to_date(date) if date else None

        rows = []
        for stock in sm:
            if not getattr(stock, "valid", False):
                continue

            stock_type = _map_stock_type(getattr(stock, "type", None))
            if type_filter and stock_type not in type_filter:
                continue

            rows.append(
                {
                    "code": getattr(stock, "market_code", ""),
                    "display_name": getattr(stock, "name", ""),
                    "name": getattr(stock, "name", ""),
                    "start_date": None,
                    "end_date": None,
                    "type": stock_type,
                }
            )

        if not rows:
            return pd.DataFrame(columns=["display_name", "name", "start_date", "end_date", "type"])

        df = pd.DataFrame(rows).set_index("code").sort_index()
        return df

    def get_trade_days(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, count: Optional[int] = None
    ) -> List[_dt.date]:
        sm = self.sm
        start_dt = _to_hk_datetime(start_date) if start_date else None
        end_dt = _to_hk_datetime(end_date) if end_date else None

        if start_dt and end_dt:
            q = hk.Query(start_dt, end_dt)
            calendar = sm.get_trading_calendar(q, "SH")
        elif start_dt and count:
            q = hk.Query(start_dt, hk.Datetime(999912310000))
            calendar = sm.get_trading_calendar(q, "SH")[: int(count)]
        elif end_dt and count:
            q = hk.Query(hk.Datetime(190001010000), end_dt)
            calendar = sm.get_trading_calendar(q, "SH")
            calendar = calendar[-int(count) :]
        elif start_dt or end_dt:
            q = hk.Query(start_dt or hk.Datetime(190001010000), end_dt or hk.Datetime(999912310000))
            calendar = sm.get_trading_calendar(q, "SH")
        else:
            calendar = sm.get_trading_calendar(hk.Query(-1000), "SH")

        out = []
        for dt in calendar:
            if hasattr(dt, "number"):
                val = dt.number // 10000
                out.append(_dt.datetime.strptime(str(val), "%Y%m%d").date())
            else:
                out.append(_to_date(dt))
        return out

    def get_fund_net_value(
        self,
        security_code: Union[str, List[str]],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        fields: Optional[Union[str, List[str]]] = None,
        **kwargs,
    ) -> pd.DataFrame:
        raise NotImplementedError("Hikyuu fund net value is not implemented in this project")

    def get_security_industry(self, security_code: str, date: Optional[str] = None) -> str:
        sm = self.sm
        stock = sm.get_stock(security_code)
        if not stock.valid:
            logger.warning(f"Invalid security code: {security_code}")
            return ""

        industry_list = stock.get_belong_to_block_list("行业板块")
        if industry_list:
            return industry_list[0].name
        return ""

    def get_market_cap(
        self,
        security_code: Union[str, List[str]],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        sm = self.sm
        if isinstance(security_code, (str, bytes)):
            sec_list = [security_code]
        else:
            sec_list = list(security_code)

        trade_days = self.get_trade_days(start_date=start_date, end_date=end_date)
        if not trade_days:
            return pd.DataFrame()

        rows = []
        for code in sec_list:
            stock = sm.get_stock(code)
            if not stock or not stock.valid:
                logger.warning(f"Invalid or non-existent stock code: {code}")
                continue

            for trade_date in trade_days:
                hk_date = _to_hk_datetime(trade_date)
                if not hk_date:
                    continue
                
                market_cap = stock.get_market_value(hk_date, hk.Query.DAY)
                rows.append(
                    {
                        "time": trade_date,
                        "code": code,
                        "market_cap": market_cap,
                    }
                )

        if not rows:
            return pd.DataFrame(columns=["time", "code", "market_cap"])

        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"])
        return df
