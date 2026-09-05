"""Turtle Soup: failed breakout / liquidity raid reversal.

Strict separation of sweep vs breakout:
  - sweep      = traded beyond the level, closed back inside (wick)
  - breakout   = CLOSED beyond the level (close-through)
Turtle soup trades the FAILED BREAKOUT: a close-through that is reclaimed
back inside within the window, confirmed by an opposing structure break
(MSS/BOS) or opposing displacement.

Sequence:
  level -> breakout (close-through) -> rejection -> reclaim
        -> reversal confirmation -> TRIGGERED
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from tme.config import TurtleSoupConfig
from tme.models.base import BaseModel
from tme.scoring import build_score
from tme.types import (
    Direction,
    DisplacementEvent,
    LiquidityType,
    StructureEvent,
    SweepEvent,
    SweepKind,
)


@dataclass
class _Cand:
    setup_id: str
    direction: Direction
    state: str
    start_idx: int
    start_time: Any
    sweep: SweepEvent
    breakout_extreme: float
    anchor_idx: int = -1
    confirm: Optional[StructureEvent | DisplacementEvent] = None


class TurtleSoupModel(BaseModel):
    name = "turtle_soup"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mc: TurtleSoupConfig = self.mc
        self._pool_kinds = frozenset(LiquidityType(k) for k in self.mc.pool_kinds)
        self.candidates: list[_Cand] = []

    def on_bar(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        out += self._spawn(i)
        out += self._advance(i)
        return out

    def _spawn(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        for ev in self.ctx.liquidity.sweeps:
            if ev.sweep_idx != i:
                continue
            if ev.pool.kind not in self._pool_kinds:
                continue
            allowed = {SweepKind.CLOSE_THROUGH}
            if self.mc.allow_wick_only:
                allowed.add(SweepKind.WICK)
            if ev.kind not in allowed:
                self.reject("WICK_SWEEP_NOT_BREAKOUT")
                continue
            if ev.confirmed_breakout:
                continue
            cand = _Cand(
                setup_id=self.next_id(),
                direction=ev.direction,      # swept a high -> SHORT, swept a low -> LONG
                state="BROKEN_OUT",
                start_idx=i,
                start_time=ev.sweep_time,
                sweep=ev,
                breakout_extreme=(self.ctx.highs[i] if ev.side.value == "high" else self.ctx.lows[i]),
            )
            self.log.add(cand.setup_id, self.name, "WATCHING_LEVEL", "BROKEN_OUT", i, ev.sweep_time,
                         f"closed through {ev.pool.kind.value} @ {ev.level:.5f}")
            self.candidates.append(cand)
        return out

    def _advance(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        for cand in list(self.candidates):
            ev = cand.sweep
            long = cand.direction is Direction.LONG
            # keep the extreme of the breakout phase up to date until reclaim
            if not ev.reclaimed:
                if ev.side.value == "high":
                    cand.breakout_extreme = max(cand.breakout_extreme, self.ctx.highs[i])
                else:
                    cand.breakout_extreme = min(cand.breakout_extreme, self.ctx.lows[i])

            if cand.state == "BROKEN_OUT":
                if ev.reclaimed:
                    cand.state = "RECLAIMED"
                    cand.anchor_idx = ev.reclaim_idx or i
                    self.log.add(cand.setup_id, self.name, "BROKEN_OUT", "RECLAIMED", i,
                                 self.ctx.times[i], "closed back inside the level")
                elif ev.confirmed_breakout:
                    out.append(self._invalidate(cand, i, "BREAKOUT_CONTINUED"))
                    continue
                elif i - ev.sweep_idx > self.mc.max_reclaim_bars:
                    out.append(self._invalidate(cand, i, "NO_RECLAIM"))
                    continue

            # continuation check before confirmation: a close beyond the
            # breakout extreme (in the breakout direction, i.e. against the
            # reversal) means the breakout was real, not a raid
            if cand.state == "RECLAIMED":
                broke_upside = cand.sweep.side.value == "high"
                continuation = (self.ctx.closes[i] > cand.breakout_extreme) if broke_upside \
                    else (self.ctx.closes[i] < cand.breakout_extreme)
                if continuation:
                    out.append(self._invalidate(cand, i, "CLOSE_BEYOND_BREAKOUT_EXTREME"))
                    continue
                lo = cand.anchor_idx
                hi = lo + self.mc.max_confirm_bars
                confirm = self.structure_between(cand.direction, lo - 1, hi)
                if confirm is None and self.mc.allow_displacement_confirm:
                    confirm = self.displacement_between(cand.direction, lo - 1, hi)
                if confirm is not None:
                    out.append(self._trigger(cand, confirm.idx, confirm))
                    continue
                if i > hi:
                    out.append(self._invalidate(cand, i, "NO_CONFIRMATION"))
        return out

    def _trigger(self, cand: _Cand, j: int, confirm):
        entry = self.ctx.closes[j]
        target = self.nearest_target(j, entry, cand.direction)
        cand.state = "TRIGGERED"
        self.candidates.remove(cand)
        self.log.add(cand.setup_id, self.name, "RECLAIMED", "TRIGGERED", j, self.ctx.times[j],
                     f"confirm at bar {j}")
        zone = (cand.sweep.level, cand.breakout_extreme) if cand.sweep.side.value == "high" \
            else (cand.breakout_extreme, cand.sweep.level)
        return self.make_event(
            cand, "TRIGGERED", j,
            score=self._score(cand, j, confirm),
            liquidity_type=cand.sweep.pool.kind.value,
            liquidity_price=cand.sweep.level,
            sweep_idx=cand.sweep.sweep_idx,
            sweep_time=cand.sweep.sweep_time,
            sweep_price=cand.breakout_extreme,
            sweep_kind=cand.sweep.kind.value,
            sweep_depth_atr=cand.sweep.depth_atr,
            displacement_idx=(confirm.idx if isinstance(confirm, DisplacementEvent) else None),
            displacement_body_atr=(confirm.body_atr if isinstance(confirm, DisplacementEvent) else None),
            mss_idx=(confirm.idx if isinstance(confirm, StructureEvent) else None),
            mss_time=(confirm.time if isinstance(confirm, StructureEvent) else None),
            mss_price=(confirm.level if isinstance(confirm, StructureEvent) else None),
            entry_zone_low=zone[0],
            entry_zone_high=zone[1],
            entry_price=entry,
            invalidation=cand.breakout_extreme,
            target_type=(target.kind.value if target else None),
            target_price=(target.price if target else None),
        )

    def _invalidate(self, cand: _Cand, i: int, reason: str):
        self.candidates.remove(cand)
        self.log.add(cand.setup_id, self.name, cand.state, "INVALIDATED", i, self.ctx.times[i], reason)
        return self.make_event(cand, "INVALIDATED", i, failure=reason)

    def _score(self, cand: _Cand, i: int, confirm):
        ext_kinds = {LiquidityType.PDH, LiquidityType.PDL, LiquidityType.PWH,
                     LiquidityType.PWL, LiquidityType.PREV_SESSION_HIGH,
                     LiquidityType.PREV_SESSION_LOW}
        quick = (cand.anchor_idx - cand.sweep.sweep_idx) <= max(1, self.mc.max_reclaim_bars // 2)
        components = {
            "failed_breakout": (True, f"{cand.sweep.pool.kind.value} close-through rejected"),
            "quick_reclaim": (quick, f"reclaimed in {cand.anchor_idx - cand.sweep.sweep_idx} bars"),
            "confirmed_reversal": (confirm is not None,
                                   type(confirm).__name__ if confirm else ""),
            "external_level": (cand.sweep.pool.kind in ext_kinds, cand.sweep.pool.kind.value),
            "session_context": (self.in_killzone(i), f"session={self.ctx.session_at(i)}"),
        }
        return build_score(components, self.mc.weights, self.mc.required)
