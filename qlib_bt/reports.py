from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from qlib_bt.io import ensure_dir


def write_quantstats_report(returns: pd.Series, out_dir: str, title: str) -> Optional[str]:
    """Generate quantstats HTML report if quantstats is available."""
    try:
        import quantstats as qs
    except Exception:
        return None

    if returns is None or returns.empty:
        return None

    s = returns.copy()
    s.index = pd.to_datetime(s.index)
    try:
        s.index = s.index.tz_localize(None)
    except Exception:
        pass
    s = pd.to_numeric(s, errors="coerce").dropna().sort_index()
    if s.empty:
        return None

    ensure_dir(out_dir)
    html_path = os.path.join(out_dir, "quantstats_report.html")

    try:
        qs.reports.html(s, output=html_path, title=title)
        return html_path
    except TypeError:
        try:
            html = qs.reports.html(s, title=title)
        except Exception:
            return None
        if isinstance(html, str) and html.strip():
            with open(html_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(html)
            return html_path

    return None
