"""Liquidity Sweep model.

Bullish sequence (bearish is symmetric):

  sell-side liquidity pool -> sweep (wick or shallow close-through)
  -> reclaim (acceptance back above the level) -> displacement
  -> MSS/BOS -> retracement into the raid zone -> TRIGGERED

State machine:
  SWEPT -> RECLAIMED -> DISPLACEMENT -> MSS_CONFIRMED -> TRIGGERED
  SWEPT or any stage -> INVALIDATED (NO_RECLAIM | NO_DISPLACEMENT | NO_MSS |
                       CLOSED_BELOW_SWEEP_LOW | ENTRY_TIMEOUT)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from tme.config import SweepModelConfig
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
    anchor_idx: int = -1            # reclaim bar
    disp: Optional[DisplacementEvent] = None
    mss: Optional[StructureEvent] = None


class LiquiditySweepModel(BaseModel):
    name = "liquidity_sweep"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mc: SweepModelConfig = self.mc
        self._pool_kinds = frozenset(LiquidityType(k) for k in self.mc.pool_kinds)
        self.candidates: list[_Cand] = []

    # ------------------------------------------------------------------ #

    def on_bar(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        out += self._spawn(i)
        out += self._advance(i)
        return out

    # ------------------------------------------------------------------ #

    def _spawn(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        for ev in self.ctx.liquidity.sweeps:
            if ev.sweep_idx != i:
                continue
            if ev.pool.kind not in self._pool_kinds:
                self.reject("POOL_KIND_NOT_WATCHED")
                continue
            if ev.depth_atr > self.mc.max_depth_atr:
                self.reject("SWEEP_TOO_DEEP")
                continue
            if ev.kind is SweepKind.CLOSE_THROUGH and not self.mc.allow_close_through:
                self.reject("CLOSE_THROUGH_NOT_ALLOWED")
                continue
            cand = _Cand(
                setup_id=self.next_id(),
                direction=ev.direction,
                state="SWEPT",
                start_idx=i,
                start_time=ev.sweep_time,
                sweep=ev,
            )
            self.log.add(cand.setup_id, self.name, "WAITING_LIQUIDITY", "SWEPT", i, ev.sweep_time,
                         f"{ev.pool.kind.value} @ {ev.level:.5f} depth {ev.depth_atr:.2f} ATR")
            self.candidates.append(cand)
            if ev.reclaimed:
                cand.state = "RECLAIMED"
                cand.anchor_idx = ev.reclaim_idx
                self.log.add(cand.setup_id, self.name, "SWEPT", "RECLAIMED", i, ev.sweep_time, "wick sweep closed back inside")
        return out

    def _advance(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        for cand in list(self.candidates):
            ev = cand.sweep
            # global invalidation: decisive close beyond the sweep extreme
            if cand.direction is Direction.LONG and self.ctx.closes[i] < ev.level - ev.depth:
                out.append(self._invalidate(cand, i, "CLOSED_BELOW_SWEEP_LOW"))
                continue
            if cand.direction is Direction.SHORT and self.ctx.closes[i] > ev.level + ev.depth:
                out.append(self._invalidate(cand, i, "CLOSED_ABOVE_SWEEP_HIGH"))
                continue

            if cand.state == "SWEPT":
                if ev.reclaimed:
                    cand.state = "RECLAIMED"
                    cand.anchor_idx = ev.reclaim_idx or i
                    self.log.add(cand.setup_id, self.name, "SWEPT", "RECLAIMED", i, self.ctx.times[i], "")
                elif ev.confirmed_breakout:
                    out.append(self._invalidate(cand, i, "NO_RECLAIM_BREAKOUT_CONTINUED"))
                    continue
                elif i - ev.sweep_idx > self.mc.max_reclaim_to_displacement_bars:
                    out.append(self._invalidate(cand, i, "NO_RECLAIM_TIMEOUT"))
                    continue

            if cand.state == "RECLAIMED":
                lo, hi = cand.anchor_idx, cand.anchor_idx + self.mc.max_reclaim_to_displacement_bars
                disp = self.displacement_between(cand.direction, lo, hi)
                if disp is not None:
                    cand.disp = disp
                    cand.state = "DISPLACEMENT"
                    self.log.add(cand.setup_id, self.name, "RECLAIMED", "DISPLACEMENT", disp.idx,
                                 disp.time, f"body {disp.body_atr:.2f} ATR")
                elif i > hi:
                    out.append(self._invalidate(cand, i, "NO_DISPLACEMENT"))
                    continue

            if cand.state == "DISPLACEMENT":
                lo = cand.disp.idx
                hi = lo + self.mc.max_displacement_to_mss_bars
                mss = self.structure_between(cand.direction, lo - 1, hi)  # include displacement bar close itself
                if mss is not None:
                    cand.mss = mss
                    cand.state = "MSS_CONFIRMED"
                    self.log.add(cand.setup_id, self.name, "DISPLACEMENT", "MSS_CONFIRMED", mss.idx,
                                 mss.time, f"{mss.kind} @ {mss.level:.5f}")
                elif i > hi:
                    out.append(self._invalidate(cand, i, "NO_MSS"))
                    continue

            if cand.state == "MSS_CONFIRMED":
                triggered = self._try_trigger(cand, i)
                if triggered is not None:
                    out.append(triggered)
                elif i - cand.mss.idx > self.mc.max_entry_wait_bars:
                    out.append(self._expire(cand, i, "ENTRY_TIMEOUT"))
        return out

    def _try_trigger(self, cand: _Cand, i: int):
        """Retracement into the raid zone: LONG needs low back at/below the
        swept level; SHORT needs high back at/above it."""
        ev = cand.sweep
        level = ev.level
        j = cand.mss.idx + 1
        while j <= i:
            o, h, l = self.ctx.opens[j], self.ctx.highs[j], self.ctx.lows[j]
            if cand.direction is Direction.LONG:
                if self.ctx.closes[j] < level - ev.depth:
                    return self._invalidate(cand, j, "CLOSED_BELOW_SWEEP_LOW")
                if l <= level:
                    return self._trigger(cand, j, entry=(o if o <= level else level))
            else:
                if self.ctx.closes[j] > level + ev.depth:
                    return self._invalidate(cand, j, "CLOSED_ABOVE_SWEEP_HIGH")
                if h >= level:
                    return self._trigger(cand, j, entry=(o if o >= level else level))
            j += 1
        return None

    def _trigger(self, cand: _Cand, j: int, entry: float):
        ev = cand.sweep
        sweep_extreme = ev.level - ev.depth if cand.direction is Direction.LONG else ev.level + ev.depth
        target = self.nearest_target(j, entry, cand.direction)
        cand.state = "TRIGGERED"
        self.candidates.remove(cand)
        self.log.add(cand.setup_id, self.name, "MSS_CONFIRMED", "TRIGGERED", j, self.ctx.times[j], "")
        zone_lo, zone_hi = (sweep_extreme, ev.level) if cand.direction is Direction.LONG else (ev.level, sweep_extreme)
        return self.make_event(
            cand, "TRIGGERED", j,
            score=self._score(cand, j),
            liquidity_type=ev.pool.kind.value,
            liquidity_price=ev.level,
            sweep_idx=ev.sweep_idx,
            sweep_time=ev.sweep_time,
            sweep_price=sweep_extreme,
            sweep_kind=ev.kind.value,
            sweep_depth_atr=ev.depth_atr,
            displacement_idx=cand.disp.idx,
            displacement_body_atr=cand.disp.body_atr,
            displacement_pct_rank=cand.disp.body_pct_rank,
            mss_idx=cand.mss.idx,
            mss_time=cand.mss.time,
            mss_price=cand.mss.level,
            entry_zone_low=zone_lo,
            entry_zone_high=zone_hi,
            entry_price=entry,
            invalidation=sweep_extreme,
            target_type=(target.kind.value if target else None),
            target_price=(target.price if target else None),
        )

    def _invalidate(self, cand: _Cand, i: int, reason: str):
        self.candidates.remove(cand)
        self.log.add(cand.setup_id, self.name, cand.state, "INVALIDATED", i, self.ctx.times[i], reason)
        return self.make_event(cand, "INVALIDATED", i, failure=reason)

    def _expire(self, cand: _Cand, i: int, reason: str):
        self.candidates.remove(cand)
        self.log.add(cand.setup_id, self.name, cand.state, "EXPIRED", i, self.ctx.times[i], reason)
        return self.make_event(cand, "EXPIRED", i, failure=reason)

    # ------------------------------------------------------------------ #

    def _score(self, cand: _Cand, i: int):
        ev = cand.sweep
        clean = ev.kind is SweepKind.WICK and ev.depth_atr <= 0.75 * self.mc.max_depth_atr
        components = {
            "clean_sweep": (
                clean,
                f"{ev.kind.value}, depth {ev.depth_atr:.2f} ATR",
            ),
            "reclaim": (True, f"reclaimed at bar {ev.reclaim_idx}"),
            "strong_displacement": (
                cand.disp is not None and cand.disp.body_atr >= self.cfg.displacement.strong_body_atr,
                f"body {cand.disp.body_atr:.2f} ATR" if cand.disp else "no displacement",
            ),
            "confirmed_mss": (cand.mss is not None,
                              f"{cand.mss.kind} @ {cand.mss.level:.5f}" if cand.mss else ""),
            "session_context": (self.in_killzone(i), f"session={self.ctx.session_at(i)}"),
            "htf_alignment": (
                self.htf_trend(i) is not None and self.htf_trend(i).as_direction() is cand.direction,
                f"htf={self.htf_trend(i).value if self.htf_trend(i) else 'none'}",
            ),
        }
        return build_score(components, self.mc.weights, self.mc.required)
