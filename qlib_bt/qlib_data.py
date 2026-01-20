from typing import List, Sequence

import pandas as pd


def init_qlib(provider_uri: str) -> None:
    import qlib

    qlib.init(provider_uri=provider_uri, region="cn")


def qlib_features(
    instruments: Sequence[str],
    fields: Sequence[str],
    start_time: str,
    end_time: str,
    freq: str = "day",
) -> pd.DataFrame:
    """Fetch features via DatasetD.dataset to avoid qlib D.features signature issues."""
    from qlib.data.data import DatasetD

    return DatasetD.dataset(
        instruments,
        list(fields),
        start_time=start_time,
        end_time=end_time,
        freq=freq,
        inst_processors=[],
    )


def to_qlib_inst(inst: str) -> str:
    return (inst or "").strip().lower()


def validate_qlib_frame(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        raise RuntimeError("qlib returned empty frame")

    if not isinstance(df.index, pd.MultiIndex) or df.index.names != ["instrument", "datetime"]:
        raise RuntimeError("unexpected qlib index; expected MultiIndex ['instrument','datetime']")
