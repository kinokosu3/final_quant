from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class ArtifactRef:
    relpath: str


@dataclass(frozen=True)
class RunArtifacts:
    out_dir: str

    manifest: ArtifactRef
    metrics: ArtifactRef

    returns: ArtifactRef
    equity: ArtifactRef
    positions: ArtifactRef

    targets_pre_risk: ArtifactRef
    targets_post_trend: ArtifactRef

    benchmark_returns: Optional[ArtifactRef]

    attribution_cross_section: Optional[ArtifactRef]
    attribution_summary: Optional[ArtifactRef]

    trend_regime: Optional[ArtifactRef]

    stop_loss_events: Optional[ArtifactRef]
    stop_loss_outcomes: Optional[ArtifactRef]

    factor_analysis_dir: Optional[ArtifactRef]


def _exists(path: str) -> bool:
    try:
        return os.path.exists(path)
    except Exception:
        return False


def _join(out_dir: str, rel: str) -> str:
    return os.path.join(out_dir, rel)


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


def enumerate_run_artifacts(out_dir: str) -> RunArtifacts:
    """Resolve the canonical artifact set for one backtest run folder.

    Required artifacts are always expected to exist (or preprocessing should fail-fast).
    Optional artifacts may be missing depending on configuration (e.g., no stop-loss events).
    """

    # Required (hard fail if missing)
    manifest = ArtifactRef("run_manifest.json")
    metrics = ArtifactRef("metrics.json")
    returns = ArtifactRef("returns.csv")
    equity = ArtifactRef("equity_curve.csv")
    positions = ArtifactRef("positions.csv")
    targets_pre = ArtifactRef("targets_pre_risk.csv")
    targets_post = ArtifactRef("targets_post_trend.csv")

    # Optional
    bm = ArtifactRef("benchmark_returns.csv")
    attrib_cross = ArtifactRef(os.path.join("attribution", "rebalance_cross_section.csv"))
    attrib_sum = ArtifactRef(os.path.join("attribution", "pipeline_summary.csv"))
    trend_regime = ArtifactRef("trend_regime.csv")
    sl_events = ArtifactRef("stop_loss_events.csv")
    sl_outcomes = ArtifactRef("stop_loss_outcomes.csv")
    fa_dir = ArtifactRef(os.path.join("factor_analysis"))

    return RunArtifacts(
        out_dir=str(out_dir),
        manifest=manifest,
        metrics=metrics,
        returns=returns,
        equity=equity,
        positions=positions,
        targets_pre_risk=targets_pre,
        targets_post_trend=targets_post,
        benchmark_returns=bm if _exists(_join(out_dir, bm.relpath)) else None,
        attribution_cross_section=attrib_cross if _exists(_join(out_dir, attrib_cross.relpath)) else None,
        attribution_summary=attrib_sum if _exists(_join(out_dir, attrib_sum.relpath)) else None,
        trend_regime=trend_regime if _exists(_join(out_dir, trend_regime.relpath)) else None,
        stop_loss_events=sl_events if _exists(_join(out_dir, sl_events.relpath)) else None,
        stop_loss_outcomes=sl_outcomes if _exists(_join(out_dir, sl_outcomes.relpath)) else None,
        factor_analysis_dir=fa_dir if _exists(_join(out_dir, fa_dir.relpath)) else None,
    )


def validate_run_artifacts(arts: RunArtifacts) -> Tuple[bool, List[str]]:
    missing: List[str] = []

    required = [
        arts.manifest.relpath,
        arts.metrics.relpath,
        arts.returns.relpath,
        arts.equity.relpath,
        arts.positions.relpath,
        arts.targets_pre_risk.relpath,
        arts.targets_post_trend.relpath,
    ]

    for rel in required:
        if not _exists(_join(arts.out_dir, rel)):
            missing.append(rel)

    return (len(missing) == 0, missing)
