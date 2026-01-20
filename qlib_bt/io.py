import datetime as dt
import os
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd


def parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def canonical_code(code: str) -> str:
    """Normalize instrument code to `159133.SZ` style for mapping."""
    s = (code or "").strip()
    if not s:
        return ""
    parts = s.split(".")
    if len(parts) != 2:
        return s.upper()
    sym, market = parts[0].strip(), parts[1].strip()
    if not sym:
        return s.upper()
    if not market:
        return sym.upper()
    return f"{sym}.{market.upper()}"


def read_stock_name_map(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path:
        return out
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "," not in line:
                continue
            code, name = line.split(",", 1)
            code = canonical_code(code)
            name = (name or "").strip()
            if not code or not name:
                continue
            out[code] = name
    return out


def instrument_display_name(inst: str, name_map: Optional[Dict[str, str]]) -> str:
    if not inst:
        return ""
    if not name_map:
        return inst
    key = canonical_code(inst)
    return name_map.get(key, inst)


def dt_to_ts(d) -> pd.Timestamp:
    if isinstance(d, pd.Timestamp):
        return d.normalize()
    return pd.Timestamp(d).normalize()


def read_calendar(path: str) -> List[pd.Timestamp]:
    out: List[pd.Timestamp] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(pd.Timestamp(line))
    return out


def read_instruments(path: str) -> List[str]:
    out: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            code = line.split("\t", 1)[0]
            out.append(code)
    return out


def iter_weekly_rebalance_days(calendar: Sequence[pd.Timestamp]) -> List[pd.Timestamp]:
    """Return the first trading day of each ISO week."""
    out: List[pd.Timestamp] = []
    last_key: Optional[Tuple[int, int]] = None
    for d in calendar:
        iso = d.isocalendar()
        key = (int(iso.year), int(iso.week))
        if key != last_key:
            out.append(d)
            last_key = key
    return out
