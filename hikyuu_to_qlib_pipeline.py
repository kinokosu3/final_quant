import argparse
import datetime as dt
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from data_source.data_source import get_data_source
from data_source.hikyuu_data_source import HikyuuDataSource


FIELDS_1D = ["open", "high", "low", "close", "volume", "money"]


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _as_date(d) -> Optional[dt.date]:
    if d is None:
        return None
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if hasattr(d, "number"):
        val = int(d.number) // 10000
        try:
            return dt.datetime.strptime(str(val), "%Y%m%d").date()
        except ValueError:
            return None
    s = str(d)
    if "-" in s:
        return dt.datetime.strptime(s, "%Y-%m-%d").date()
    return dt.datetime.strptime(s, "%Y%m%d").date()


def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    if s is None:
        return None
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def map_hikyuu_code_to_qlib(hk_code: str) -> Optional[str]:
    hk_code = (hk_code or "").strip()
    if len(hk_code) < 3:
        return None
    prefix = hk_code[:2].lower()
    if prefix not in ("sz", "sh"):
        return None
    digits = hk_code[2:]
    if not digits.isdigit() or len(digits) != 6:
        return None
    suffix = prefix.upper()
    return f"{digits}.{suffix}"


def map_qlib_code_to_hikyuu(qlib_code: str) -> Optional[str]:
    qlib_code = (qlib_code or "").strip()
    if "." not in qlib_code:
        return None
    digits, suffix = qlib_code.split(".", 1)
    suffix = suffix.upper()
    if suffix not in ("SZ", "SH"):
        return None
    if not digits.isdigit() or len(digits) != 6:
        return None
    return f"{suffix.lower()}{digits}"


@dataclass(frozen=True)
class Range:
    start: Optional[dt.date]
    end: Optional[dt.date]


def _clamp_to_range(d: Optional[dt.date], r: Range) -> Optional[dt.date]:
    if d is None:
        return None
    if r.start and d < r.start:
        return r.start
    if r.end and d > r.end:
        return r.end
    return d


def build_calendar(ds: HikyuuDataSource, out_path: str, r: Range, count: Optional[int]) -> List[dt.date]:
    if count is not None and count > 0 and r.start is None and r.end is None:
        days = ds.get_trade_days(count=count)
    else:
        days = ds.get_trade_days(start_date=r.start, end_date=r.end, count=count)

    days = sorted(set(days))
    _ensure_dir(os.path.dirname(out_path))
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for d in days:
            f.write(d.strftime("%Y-%m-%d"))
            f.write("\n")
    return days


def _derive_start_end_from_price(
    ds: HikyuuDataSource, hk_code: str, r: Range, count: Optional[int]
) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    df = ds.get_price(
        hk_code,
        start_date=r.start,
        end_date=r.end,
        fields=["close"],
        count=count,
        panel=False,
    )
    if df is None or df.empty:
        return None, None
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.to_datetime(idx)
    start = idx.min().date()
    end = idx.max().date()
    return start, end


def build_instruments(
    ds: HikyuuDataSource,
    out_path: str,
    hk_codes: Sequence[str],
    r: Range,
    count: Optional[int],
) -> List[str]:
    lines: List[str] = []
    qlib_codes: List[str] = []

    for hk_code in hk_codes:
        qlib_code = map_hikyuu_code_to_qlib(hk_code)
        if qlib_code is None:
            continue

        info = ds.get_security_info(hk_code)
        start = _as_date(getattr(info, "start_date", None)) if info else None
        end = _as_date(getattr(info, "end_date", None)) if info else None

        if start is None or end is None:
            p_start, p_end = _derive_start_end_from_price(ds, hk_code, r=r, count=count)
            start = start or p_start
            end = end or p_end

        start = _clamp_to_range(start, r)
        end = _clamp_to_range(end, r)

        if start is None or end is None:
            continue

        if start > end:
            continue

        lines.append(f"{qlib_code}\t{start.strftime('%Y-%m-%d')}\t{end.strftime('%Y-%m-%d')}")
        qlib_codes.append(qlib_code)

    _ensure_dir(os.path.dirname(out_path))
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for line in sorted(lines):
            f.write(line)
            f.write("\n")

    return qlib_codes


def _align_to_calendar(df: pd.DataFrame, calendar: Sequence[dt.date]) -> pd.DataFrame:
    cal = pd.to_datetime(pd.Series(list(calendar), name="date"))

    if "date" not in df.columns:
        raise ValueError("expected 'date' column before calendar alignment")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.drop_duplicates(subset=["date"], keep="last")
    df = df.set_index("date").sort_index()

    aligned = df.reindex(cal.values)
    aligned.index.name = "date"

    aligned = aligned.reset_index()
    aligned["date"] = aligned["date"].dt.strftime("%Y-%m-%d")
    return aligned


def build_source_parquet_for_one(
    ds: HikyuuDataSource,
    hk_code: str,
    qlib_code: str,
    calendar: Sequence[dt.date],
    out_path: str,
    r: Range,
    count: Optional[int],
) -> None:
    df = ds.get_price(
        hk_code,
        start_date=r.start,
        end_date=r.end,
        fields=FIELDS_1D,
        count=count,
        panel=False,
    )

    if df is None or df.empty:
        return

    df = df.reset_index().rename(columns={"datetime": "date"})
    if "date" not in df.columns:
        df = df.rename(columns={df.columns[0]: "date"})

    keep = ["date"] + [c for c in FIELDS_1D if c in df.columns]
    df = df[keep]

    for col in ("open", "high", "low", "close", "money"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    df = _align_to_calendar(df, calendar)

    _ensure_dir(os.path.dirname(out_path))
    try:
        df.to_parquet(out_path, index=False)
    except Exception as exc:
        raise RuntimeError(
            "failed to write parquet; install a parquet engine (pyarrow recommended)"
        ) from exc


def build_source_parquet(
    ds: HikyuuDataSource,
    qlib_dir: str,
    qlib_codes: Sequence[str],
    calendar: Sequence[dt.date],
    r: Range,
    count: Optional[int],
    limit: Optional[int],
) -> None:
    out_dir = os.path.join(qlib_dir, "source", "1d_nor")
    _ensure_dir(out_dir)

    n = 0
    for qlib_code in qlib_codes:
        if limit is not None and n >= limit:
            break
        hk_code = map_qlib_code_to_hikyuu(qlib_code)
        if hk_code is None:
            continue

        out_path = os.path.join(out_dir, f"{qlib_code}.parquet")
        build_source_parquet_for_one(
            ds,
            hk_code=hk_code,
            qlib_code=qlib_code,
            calendar=calendar,
            out_path=out_path,
            r=r,
            count=count,
        )
        n += 1


def run_qlib_dump_bin(qlib_dir: str, data_path: str) -> None:
    # The user has moved dump_bin.py to the project root.
    dump_bin_script = "dump_bin.py"
    if not os.path.exists(dump_bin_script):
        raise FileNotFoundError(
            f"'{dump_bin_script}' not found in the project root directory. "
            "Please ensure the file is in the correct location."
        )

    cmd = [
        sys.executable,
        dump_bin_script,
        "dump_all",
        "--data_path",
        data_path,
        "--qlib_dir",
        qlib_dir,
        "--freq",
        "day",
        "--date_field_name",
        "date",
        "--exclude_fields",
        "date",
        "--file_suffix",
        ".parquet",
    ]
    subprocess.check_call(cmd)


def _filter_hk_codes(hk_codes: Iterable[str], prefixes: Sequence[str]) -> List[str]:
    allowed = {p.lower() for p in prefixes}
    out: List[str] = []
    for c in hk_codes:
        c = (c or "").strip()
        if len(c) < 3:
            continue
        if c[:2].lower() not in allowed:
            continue
        out.append(c)
    return sorted(set(out))


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Build a minimal Qlib CN dump_bin-style dataset from local Hikyuu daily bars"
    )
    p.add_argument("--qlib_dir", default=os.path.join("qlib_data", "cn_hikyuu"))
    p.add_argument("--start_date", default=None, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--end_date", default=None, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--count", type=int, default=None, help="latest N trading days")
    p.add_argument("--prefixes", default="sz,sh", help="comma-separated: sz,sh")
    p.add_argument("--limit", type=int, default=None, help="limit instruments for quick runs")
    p.add_argument("--no_calendar", action="store_true")
    p.add_argument("--no_instruments", action="store_true")
    p.add_argument("--no_source", action="store_true")
    p.add_argument("--dump_bin", action="store_true")

    args = p.parse_args(list(argv) if argv is not None else None)

    r = Range(start=_parse_date(args.start_date), end=_parse_date(args.end_date))
    prefixes = [x.strip() for x in args.prefixes.split(",") if x.strip()]
    
    ds = get_data_source('hk')
    cal_path = os.path.join(args.qlib_dir, "calendars", "day.txt")
    inst_path = os.path.join(args.qlib_dir, "instruments", "all.txt")

    if args.no_calendar:
        calendar = ds.get_trade_days(start_date=r.start, end_date=r.end, count=args.count)
        calendar = sorted(set(calendar))
    else:
        calendar = build_calendar(ds, out_path=cal_path, r=r, count=args.count)

    hk_codes = ds.get_all_codes("etf")
    hk_codes = _filter_hk_codes(hk_codes, prefixes=prefixes)

    if args.no_instruments:
        qlib_codes = [c for c in (map_hikyuu_code_to_qlib(x) for x in hk_codes) if c is not None]
    else:
        qlib_codes = build_instruments(
            ds,
            out_path=inst_path,
            hk_codes=hk_codes,
            r=r,
            count=args.count,
        )

    if not args.no_source:
        build_source_parquet(
            ds,
            qlib_dir=args.qlib_dir,
            qlib_codes=qlib_codes,
            calendar=calendar,
            r=r,
            count=args.count,
            limit=args.limit,
        )

    if args.dump_bin:
        data_path = os.path.join(args.qlib_dir, "source", "1d_nor")
        run_qlib_dump_bin(qlib_dir=args.qlib_dir, data_path=data_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
