from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class FactorConfig:
    name: str
    expr: str
    weight: float


def build_minimal_factors() -> List[FactorConfig]:
    """Default factor set.

    Expressions are evaluated by Qlib expression engine.
    """
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
