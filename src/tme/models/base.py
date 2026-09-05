"""Model base: state-machine candidates, detection log, shared queries.

Every model consumes primitives from the MarketContext only — no model
re-implements swing/sweep/FVG logic. All state transitions (including
invalidations, with reasons) go to the DetectionLog for explainability.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from tme.config import EngineConfig
from tme.types import (
    Direction,
    DisplacementEvent,
    FVG,
    LiquidityPool,
    StructureEvent,
    SetupEvent,
    Side,
    Transition,
)


class DetectionLog:
    def __init__(self) -> None:
        self.entries: list[Transition] = []

    def add(self, setup_id: str, model: str, frm: str, to: str, idx: int, time: Any, note: str = "") -> None:
        self.entries.append(Transition(setup_id, model, frm, to, idx, time, note))


class BaseModel:
    name = "base"

    def __init__(self, ctx, cfg: EngineConfig, model_cfg, variant: str = "default",
                 log: DetectionLog | None = None, smt_events: list | None = None):
        self.ctx = ctx
        self.cfg = cfg
        self.mc = model_cfg
        self.variant = variant
        self.log = log if log is not None else DetectionLog()
        self.smt_events = smt_events if smt_events is not None else []
        self.emitted: list[SetupEvent] = []
        self.rejections: dict[str, int] = {}
        self._seq = 0

    def on_bar(self, i: int) -> list[SetupEvent]:
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def next_id(self) -> str:
        self._seq += 1
        return f"{self.name}#{self.variant}#{self._seq}"

    def reject(self, reason: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def structure_between(self, direction: Direction, lo: int, hi: int) -> Optional[StructureEvent]:
        """First structure event with idx in (lo, hi]."""
        for ev in reversed(self.ctx.structure.events):
            if ev.idx <= lo:
                break
            if ev.idx <= hi and ev.direction is direction:
                return ev
        return None

    def displacement_between(self, direction: Direction, lo: int, hi: int) -> Optional[DisplacementEvent]:
        for ev in reversed(self.ctx.displacement.events):
            if ev.idx <= lo:
                break
            if ev.idx <= hi and ev.direction is direction:
                return ev
        return None

    def fvgs_formed_between(self, direction: Direction, lo: int, hi: int) -> list[FVG]:
        return [
            f for f in self.ctx.fvg.fvgs
            if lo <= f.formed_idx <= hi and f.direction is direction
        ]

    def nearest_target(self, i: int, price: float, direction: Direction) -> Optional[LiquidityPool]:
        side = Side.HIGH if direction is Direction.LONG else Side.LOW
        return self.ctx.liquidity.nearest_unswept(side, price)

    def in_killzone(self, i: int) -> bool:
        names = self.ctx.session_names[i]
        return any(n in self.cfg.sessions.kill_zones for n in names)

    def htf_trend(self, i: int):
        snap = self.ctx.htf_snapshot(i)
        return snap.trend if snap else None

    def make_event(self, cand, state: str, i: int, failure: str | None = None,
                   score=None, **fields) -> SetupEvent:
        ev = SetupEvent(
            model=self.name,
            variant=self.variant,
            direction=cand.direction,
            symbol=self.ctx.symbol,
            timeframe=self.ctx.timeframe,
            setup_id=cand.setup_id,
            state=state,
            start_idx=cand.start_idx,
            start_time=cand.start_time,
            trigger_idx=i,
            trigger_time=self.ctx.times[i],
            failure_reason=failure,
            session=self.ctx.session_at(i),
            weekday=pd.Timestamp(self.ctx.times[i]).day_name(),
            htf_trend=(self.htf_trend(i).value if self.htf_trend(i) else None),
        )
        for k, v in fields.items():
            if v is not None:
                setattr(ev, k, v)
        if score is not None:
            ev.score = score.total
            ev.score_max = score.max_total
            ev.checklist = score.items
        self.emitted.append(ev)
        return ev
