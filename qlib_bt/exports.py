from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import pandas as pd

from qlib_bt.io import dt_to_ts, ensure_dir, instrument_display_name


def dump_targets_csv(
    targets_by_day: Dict[pd.Timestamp, Dict[str, float]],
    out_dir: str,
    stock_name_map: Optional[Dict[str, str]] = None,
) -> None:
    rows: List[Dict[str, object]] = []
    for d, w_map in sorted(targets_by_day.items(), key=lambda x: x[0]):
        for inst, w in sorted(w_map.items()):
            rows.append(
                {
                    "date": str(dt_to_ts(d).date()),
                    "instrument": inst,
                    "instrument_name": instrument_display_name(inst, stock_name_map),
                    "target_weight": float(w),
                }
            )
    if not rows:
        return
    ensure_dir(out_dir)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "targets.csv"), index=False)


def dump_positions_csv(
    positions_log: List[Dict[str, object]],
    out_dir: str,
    stock_name_map: Optional[Dict[str, str]] = None,
) -> None:
    if not positions_log:
        return

    ensure_dir(out_dir)
    df_pos = pd.DataFrame(positions_log)
    if stock_name_map is not None and not df_pos.empty and "instrument" in df_pos.columns:
        df_pos["instrument_name"] = df_pos["instrument"].map(
            lambda x: instrument_display_name(str(x), stock_name_map)
        )
    df_pos.to_csv(os.path.join(out_dir, "positions.csv"), index=False)


