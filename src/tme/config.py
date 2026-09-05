"""Configuration: every threshold lives here, no magic numbers in logic.

Defaults are defined in code (dataclasses); an optional YAML file overrides
any subset of fields. Nested dataclasses are merged recursively.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

from tme.types import LiquidityType

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ATRConfig:
    period: int = 14


@dataclass(frozen=True)
class SwingConfig:
    left: int = 3
    right: int = 3


@dataclass(frozen=True)
class StructureConfig:
    use_close_break: bool = True  # close-based breaks only (wick breaks are sweeps)


@dataclass(frozen=True)
class LiquidityConfig:
    equal_tol_atr: float = 0.15
    max_reclaim_bars: int = 6          # close-through must reclaim within N bars
    breakout_confirm_bars: int = 3     # close-through held N bars => confirmed breakout
    enable_pd: bool = True             # previous day high/low pools
    enable_pw: bool = True             # previous week high/low pools
    enable_prev_session: bool = True
    enable_session_extremes: bool = False  # live running session extreme pools
    allow_resweep: bool = False


@dataclass(frozen=True)
class DisplacementConfig:
    min_body_atr: float = 1.2
    min_body_ratio: float = 0.60
    strong_body_atr: float = 1.8       # bypasses the percentile requirement
    min_pct_rank: float = 85.0
    pct_lookback: int = 100
    min_close_location: float = 0.70   # close must be near the extreme end


@dataclass(frozen=True)
class FVGConfig:
    min_size_atr: float = 0.10
    require_mid_candle_direction: bool = False


@dataclass(frozen=True)
class SessionConfig:
    timezone: str = "America/New_York"
    sessions: dict[str, tuple[str, str]] = field(default_factory=lambda: {
        "asia": ("20:00", "00:00"),
        "london": ("02:00", "05:00"),
        "ny_am": ("09:30", "12:00"),
        "ny": ("09:30", "16:00"),
    })
    kill_zones: tuple[str, ...] = ("london", "ny_am")


@dataclass(frozen=True)
class HTFConfig:
    swing: SwingConfig = field(default_factory=lambda: SwingConfig(left=2, right=2))
    eq_band: float = 0.02  # neutral band around dealing-range equilibrium


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepModelConfig:
    enabled: bool = True
    allow_close_through: bool = True
    max_depth_atr: float = 1.5         # deeper sweeps treated as likely real breakouts
    max_reclaim_to_displacement_bars: int = 8
    max_displacement_to_mss_bars: int = 8
    max_entry_wait_bars: int = 24
    pool_kinds: tuple[str, ...] = (
        "swing_high", "swing_low", "eqh", "eql", "pdh", "pdl",
        "prev_session_high", "prev_session_low",
    )
    weights: dict[str, float] = field(default_factory=lambda: {
        "clean_sweep": 25.0,
        "reclaim": 20.0,
        "strong_displacement": 20.0,
        "confirmed_mss": 20.0,
        "session_context": 10.0,
        "htf_alignment": 5.0,
    })
    required: tuple[str, ...] = ("reclaim", "confirmed_mss")


@dataclass(frozen=True)
class PO3Config:
    enabled: bool = True
    accumulation_session: str = "asia"
    max_range_atr: float = 3.5
    min_range_atr: float = 0.5
    manipulation_window_bars: int = 24
    distribution_window_bars: int = 24
    max_entry_wait_bars: int = 24
    weights: dict[str, float] = field(default_factory=lambda: {
        "compressed_range": 25.0,
        "manipulation": 25.0,
        "distribution": 30.0,
        "session_context": 10.0,
        "htf_alignment": 10.0,
    })
    required: tuple[str, ...] = ("manipulation", "distribution")


@dataclass(frozen=True)
class ICT2022Config:
    enabled: bool = True
    htf_required: bool = True
    max_sweep_to_reclaim_bars: int = 6
    max_reclaim_to_displacement_bars: int = 8
    max_displacement_to_mss_bars: int = 8
    max_mss_to_fvg_bars: int = 2       # FVG must form within the displacement leg
    max_entry_wait_bars: int = 48
    entry_mode: str = "fvg_touch"      # fvg_touch | fvg_ce
    min_fvg_size_atr: float = 0.15
    pool_kinds: tuple[str, ...] = (
        "swing_high", "swing_low", "eqh", "eql", "pdh", "pdl",
        "prev_session_high", "prev_session_low",
    )
    # Ablation variants: variant name -> trigger stage. Scoring checklist is
    # always computed in full; the variant only changes when the setup fires.
    variants: tuple[str, ...] = (
        "sweep_only", "sweep_mss", "sweep_disp", "sweep_mss_disp", "full",
    )
    run_variants: bool = True
    weights: dict[str, float] = field(default_factory=lambda: {
        "htf_bias": 15.0,
        "external_liquidity": 15.0,
        "clean_sweep": 20.0,
        "strong_displacement": 15.0,
        "confirmed_mss": 15.0,
        "fvg_quality": 10.0,
        "session_context": 5.0,
        "smt_confirmation": 5.0,
    })
    required: tuple[str, ...] = (
        "htf_bias", "reclaim", "displacement", "confirmed_mss", "fvg",
    )


@dataclass(frozen=True)
class TurtleSoupConfig:
    enabled: bool = True
    allow_wick_only: bool = False      # False: require a close-through breakout
    allow_displacement_confirm: bool = True  # MSS always accepted; displacement optional
    max_reclaim_bars: int = 5
    max_confirm_bars: int = 8
    max_entry_wait_bars: int = 24
    pool_kinds: tuple[str, ...] = (
        "pdh", "pdl", "prev_session_high", "prev_session_low",
        "swing_high", "swing_low",
    )
    weights: dict[str, float] = field(default_factory=lambda: {
        "failed_breakout": 30.0,
        "quick_reclaim": 20.0,
        "confirmed_reversal": 25.0,
        "external_level": 15.0,
        "session_context": 10.0,
    })
    required: tuple[str, ...] = ("reclaim", "confirmed_reversal")


@dataclass(frozen=True)
class SMTConfig:
    enabled: bool = False
    primary: str = ""
    secondary: str = ""
    timeframe: str = "15m"
    max_swing_time_diff: str = "3h"    # max |pivot_time difference| between assets
    min_divergence_atr: float = 0.15
    correlation_window: int = 100
    resolution_max_bars: int = 48
    weights: dict[str, float] = field(default_factory=lambda: {
        "divergence": 40.0,
        "tight_timing": 15.0,
        "correlation": 20.0,
        "magnitude": 15.0,
        "resolution": 10.0,
    })
    required: tuple[str, ...] = ("divergence",)


@dataclass(frozen=True)
class OutcomeConfig:
    max_hold_bars: int = 96
    default_target_r: float = 2.0
    stop_first: bool = True            # same-bar target+stop => stop wins
    atr_regime_window: int = 288
    atr_regime_q1: float = 0.33
    atr_regime_q2: float = 0.66


@dataclass(frozen=True)
class EngineConfig:
    atr: ATRConfig = field(default_factory=ATRConfig)
    swings: SwingConfig = field(default_factory=SwingConfig)
    structure: StructureConfig = field(default_factory=StructureConfig)
    liquidity: LiquidityConfig = field(default_factory=LiquidityConfig)
    displacement: DisplacementConfig = field(default_factory=DisplacementConfig)
    fvg: FVGConfig = field(default_factory=FVGConfig)
    sessions: SessionConfig = field(default_factory=SessionConfig)
    htf: HTFConfig = field(default_factory=HTFConfig)
    sweep_model: SweepModelConfig = field(default_factory=SweepModelConfig)
    po3: PO3Config = field(default_factory=PO3Config)
    ict2022: ICT2022Config = field(default_factory=ICT2022Config)
    turtle_soup: TurtleSoupConfig = field(default_factory=TurtleSoupConfig)
    smt: SMTConfig = field(default_factory=SMTConfig)
    outcome: OutcomeConfig = field(default_factory=OutcomeConfig)
    run_sweep_model: bool = True
    run_po3: bool = True
    run_ict2022: bool = True
    run_turtle_soup: bool = True
    run_smt: bool = False


# ---------------------------------------------------------------------------
# YAML loading (recursive dataclass merge)
# ---------------------------------------------------------------------------


def _build(dc_cls: type, data: dict[str, Any]) -> Any:
    """Instantiate dataclass `dc_cls` from a (partial) dict, recursing into
    nested dataclass fields; unknown keys are ignored."""
    import typing

    hints = typing.get_type_hints(dc_cls)
    kwargs: dict[str, Any] = {}
    known = {f.name for f in fields(dc_cls)}
    for key, value in (data or {}).items():
        if key not in known:
            continue
        t = hints[key]
        if is_dataclass(t) and isinstance(value, dict):
            kwargs[key] = _build(t, value)
        else:
            kwargs[key] = value
    return dc_cls(**kwargs)


def load_config(path: str | Path | None = None) -> EngineConfig:
    if path is None:
        return EngineConfig()
    import yaml  # optional dependency

    raw = yaml.safe_load(Path(path).read_text()) or {}
    return _build(EngineConfig, raw)


def _pool_kinds(names: tuple[str, ...]) -> frozenset[LiquidityType]:
    return frozenset(LiquidityType(n) for n in names)
