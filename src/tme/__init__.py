"""Trading Model Engine (TME).

Deterministic, bar-by-bar, lookahead-free setup research engine.

Pipeline: primitives -> state-machine models -> confluence scoring -> events
-> outcome evaluation -> stats/ablation. Detection and backtest share the
exact same bar-by-bar logic.
"""

from tme.config import EngineConfig, load_config
from tme.types import (
    Direction,
    LiquidityType,
    SweepKind,
    SetupEvent,
)

__all__ = [
    "EngineConfig",
    "load_config",
    "Direction",
    "LiquidityType",
    "SweepKind",
    "SetupEvent",
]
__version__ = "0.1.0"
