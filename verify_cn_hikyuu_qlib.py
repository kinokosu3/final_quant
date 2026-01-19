import argparse
import os
from typing import Optional, Sequence

import pandas as pd


def _pick_instruments(qlib_dir: str, limit: int) -> Sequence[str]:
    inst_path = os.path.join(qlib_dir, "instruments", "all.txt")
    if not os.path.exists(inst_path):
        raise FileNotFoundError(inst_path)

    out = []
    with open(inst_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            code = line.split("\t", 1)[0]
            out.append(code)
            if len(out) >= limit:
                break

    if not out:
        raise RuntimeError("no instruments found")
    return out


def _load_parquet(qlib_dir: str, inst: str) -> pd.DataFrame:
    p = os.path.join(qlib_dir, "source", "1d_nor", f"{inst}.parquet")
    return pd.read_parquet(p)


def _load_bin_series(qlib_dir: str, inst: str, field: str) -> pd.Series:
    import qlib
    from qlib.data import D

    qlib.init(provider_uri=qlib_dir, region="cn")

    cal = D.calendar(freq="day")
    cal = pd.to_datetime(pd.Series(cal))

    qlib_field = f"${field}"
    qlib_inst = inst.lower()
    df = D.features([qlib_inst], [qlib_field], freq="day")
    # df index: datetime x instrument
    s = df[qlib_field].xs(qlib_inst, level="instrument")

    out = pd.Series(index=cal, dtype=float)
    out.loc[s.index] = s.values
    out = out.rename(field)
    return out


def verify_one(qlib_dir: str, inst: str) -> None:
    src = _load_parquet(qlib_dir, inst)
    src = src.copy()
    src["date"] = pd.to_datetime(src["date"])
    src = src.set_index("date").sort_index()

    for field in ("close", "volume", "money"):
        s_bin = _load_bin_series(qlib_dir, inst, field)
        s_src = src[field] if field in src.columns else None

        if s_src is None:
            print(f"{inst} missing field in parquet: {field}")
            continue

        joined = pd.concat([s_src.rename("src"), s_bin.rename("bin")], axis=1)
        joined = joined.dropna(how="all")
        diff = (joined["src"] - joined["bin"]).abs()

        max_abs = float(diff.max()) if not diff.empty else float("nan")
        print(f"{inst} {field}: max_abs_diff={max_abs}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Smoke-test qlib_data/cn_hikyuu dataset")
    p.add_argument("--qlib_dir", default=os.path.join("qlib_data", "cn_hikyuu"))
    p.add_argument("--limit", type=int, default=3)
    args = p.parse_args(list(argv) if argv is not None else None)

    instruments = _pick_instruments(args.qlib_dir, args.limit)
    for inst in instruments:
        verify_one(args.qlib_dir, inst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
