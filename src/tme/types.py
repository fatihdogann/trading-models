"""Shared types: enums and event objects passed between primitives and models.

All events carry both a bar index and a timestamp. Bar indexes refer to the
bar that CLOSED making the information available (no lookahead).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional


class Side(StrEnum):
    HIGH = "high"
    LOW = "low"


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"

    @property
    def opposite(self) -> "Direction":
        return Direction.SHORT if self is Direction.LONG else Direction.LONG


class Trend(StrEnum):
    BULL = "bull"
    BEAR = "bear"
    NONE = "none"

    def as_direction(self) -> Optional["Direction"]:
        if self is Trend.BULL:
            return Direction.LONG
        if self is Trend.BEAR:
            return Direction.SHORT
        return None


class SweepKind(StrEnum):
    WICK = "wick"                    # traded beyond level, closed back inside
    CLOSE_THROUGH = "close_through"  # closed beyond level (breakout attempt)


class LiquidityType(StrEnum):
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
    EQH = "eqh"
    EQL = "eql"
    PDH = "pdh"
    PDL = "pdl"
    PWH = "pwh"
    PWL = "pwl"
    SESSION_HIGH = "session_high"          # live running session extreme
    SESSION_LOW = "session_low"
    PREV_SESSION_HIGH = "prev_session_high"
    PREV_SESSION_LOW = "prev_session_low"

    @property
    def side(self) -> Side:
        return Side.LOW if self.value.endswith("low") else Side.HIGH


EXTERNAL_LIQUIDITY: frozenset[LiquidityType] = frozenset({
    LiquidityType.PDH, LiquidityType.PDL,
    LiquidityType.PWH, LiquidityType.PWL,
    LiquidityType.PREV_SESSION_HIGH, LiquidityType.PREV_SESSION_LOW,
})


class FVGStatus(StrEnum):
    ACTIVE = "active"
    PARTIAL = "partial"
    FULL_FILLED = "full_filled"
    INVERTED = "inverted"


@dataclass(frozen=True)
class Swing:
    id: str
    side: Side
    price: float
    pivot_idx: int
    pivot_time: Any
    confirm_idx: int
    confirm_time: Any
    label: Optional[str] = None  # HH / HL / LH / LL vs previous same-side swing


@dataclass(frozen=True)
class StructureEvent:
    idx: int
    time: Any
    kind: str                  # "BOS" | "MSS"
    direction: Direction
    level: float
    swing: Swing


@dataclass
class LiquidityPool:
    id: str
    kind: LiquidityType
    side: Side
    price: float
    formed_idx: int
    formed_time: Any
    active: bool = True
    dynamic: bool = False       # live session extremes track current price
    group_id: Optional[str] = None  # equal-highs/lows cluster id


@dataclass
class SweepEvent:
    """Lifecycle of one liquidity raid against a pool.

    Mutates strictly forward in time: sweep -> (reclaim | confirmed_breakout).
    Models read the live object each bar; fields are only ever set, never unset.
    """

    id: str
    pool: LiquidityPool
    level: float
    side: Side
    formed_idx: int
    formed_time: Any
    sweep_idx: int
    sweep_time: Any
    kind: SweepKind
    depth: float                 # price distance beyond the level
    depth_atr: float
    bars_held: int               # bars the level survived before the sweep
    reclaimed: bool = False
    reclaim_idx: Optional[int] = None
    reclaim_time: Any = None
    reclaim_close: Optional[float] = None
    confirmed_breakout: bool = False
    breakout_idx: Optional[int] = None
    deep: bool = False           # depth_atr above model threshold (likely real breakout)

    @property
    def direction(self) -> Direction:
        """Direction implied for a reversal model: swept sell-side -> long."""
        return Direction.LONG if self.side is Side.LOW else Direction.SHORT


@dataclass(frozen=True)
class DisplacementEvent:
    idx: int
    time: Any
    direction: Direction
    body: float
    bar_range: float
    atr: float
    body_atr: float
    range_atr: float
    body_ratio: float
    body_pct_rank: float
    close_location: float


@dataclass
class FVG:
    id: str
    direction: Direction
    top: float
    bottom: float
    mid: float
    formed_idx: int
    formed_time: Any
    size: float
    size_atr: float
    status: FVGStatus = FVGStatus.ACTIVE
    first_touch_idx: Optional[int] = None
    first_touch_time: Any = None
    filled_idx: Optional[int] = None
    max_fill_ratio: float = 0.0

    @property
    def active(self) -> bool:
        return self.status in (FVGStatus.ACTIVE, FVGStatus.PARTIAL)


@dataclass(frozen=True)
class SessionClosed:
    name: str
    start_idx: int
    end_idx: int
    start_time: Any
    end_time: Any
    high: float
    low: float


@dataclass(frozen=True)
class PeriodRolled:
    kind: str  # "day" | "week"
    idx: int
    time: Any
    prev_high: float
    prev_low: float
    prev_label: str


@dataclass(frozen=True)
class ChecklistItem:
    name: str
    passed: bool
    weight: float
    required: bool = False
    detail: str = ""


@dataclass(frozen=True)
class ScoreResult:
    total: float
    max_total: float
    passed_required: bool
    missing_required: tuple[str, ...]
    items: tuple[ChecklistItem, ...]


@dataclass(frozen=True)
class Transition:
    setup_id: str
    model: str
    frm: str
    to: str
    idx: int
    time: Any
    note: str = ""


@dataclass
class SetupEvent:
    """Structured output of one detected (or rejected/invalidated) setup."""

    model: str
    variant: str
    direction: Direction
    symbol: str
    timeframe: str
    setup_id: str
    state: str                     # TRIGGERED / INVALIDATED / EXPIRED
    start_idx: int
    start_time: Any
    trigger_idx: Optional[int] = None
    trigger_time: Any = None
    # liquidity / sweep
    liquidity_type: Optional[str] = None
    liquidity_price: Optional[float] = None
    sweep_idx: Optional[int] = None
    sweep_time: Any = None
    sweep_price: Optional[float] = None
    sweep_kind: Optional[str] = None
    sweep_depth_atr: Optional[float] = None
    # sequence
    displacement_idx: Optional[int] = None
    displacement_body_atr: Optional[float] = None
    displacement_pct_rank: Optional[float] = None
    mss_idx: Optional[int] = None
    mss_time: Any = None
    mss_price: Optional[float] = None
    fvg_low: Optional[float] = None
    fvg_high: Optional[float] = None
    fvg_mid: Optional[float] = None
    # plan
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    entry_price: Optional[float] = None
    invalidation: Optional[float] = None
    target_type: Optional[str] = None
    target_price: Optional[float] = None
    # scoring / explain
    score: Optional[float] = None
    score_max: Optional[float] = None
    checklist: tuple[ChecklistItem, ...] = ()
    failure_reason: Optional[str] = None
    # context tags (filled by engine/outcome layer)
    session: Optional[str] = None
    weekday: Optional[str] = None
    htf_trend: Optional[str] = None
    atr_regime: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if k == "checklist":
                out[k] = [c.__dict__ for c in v]
            elif hasattr(v, "isoformat"):  # pd.Timestamp / datetime
                out[k] = v.isoformat()
            elif hasattr(v, "value"):  # StrEnum
                out[k] = v.value
            else:
                out[k] = v
        return out


@dataclass(frozen=True)
class Outcome:
    setup_id: str
    model: str
    variant: str
    direction: Direction
    entry: float
    stop: float
    target: float
    exit_price: float
    exit_reason: str          # TARGET / STOP / TIME
    r: float
    mae_r: float
    mfe_r: float
    bars_held: int
    t_target_bars: Optional[int]
    t_invalidation_bars: Optional[int]
    trigger_time: Any
    exit_time: Any
