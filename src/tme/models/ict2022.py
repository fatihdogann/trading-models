"""ICT 2022 model.

Core sequence (deterministic):

  HTF bias -> liquidity raid (sweep against bias) -> reclaim
  -> displacement in bias direction -> MSS (close-based structure break)
  -> FVG created by the displacement leg -> retracement into the FVG
  -> ENTRY (TRIGGERED) -> target = opposing liquidity

Every condition is a separate checklist component. Scoring is computed in
full for every trigger stage; a setup only fires at its variant's trigger
stage, and required conditions (full variant) gate the trigger regardless of
score.

Ablation variants (trigger stage):
  sweep_only      -> fires on reclaim
  sweep_disp      -> fires on displacement
  sweep_mss       -> fires on structure break
  sweep_mss_disp  -> fires on structure break (same stage, kept for naming)
  full            -> fires on FVG retracement touch
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from tme.config import ICT2022Config
from tme.models.base import BaseModel
from tme.scoring import build_score
from tme.types import (
    Direction,
    DisplacementEvent,
    FVG,
    LiquidityType,
    StructureEvent,
    SweepEvent,
    SweepKind,
)

VARIANT_TRIGGER = {
    "sweep_only": "RECLAIMED",
    "sweep_disp": "DISPLACEMENT",
    "sweep_mss": "MSS_CONFIRMED",
    "sweep_mss_disp": "MSS_CONFIRMED",
    "full": "ENTRY_ZONE",
}


@dataclass
class _Cand:
    setup_id: str
    direction: Direction
    state: str
    start_idx: int
    start_time: Any
    sweep: SweepEvent
    anchor_idx: int = -1
    disp: Optional[DisplacementEvent] = None
    mss: Optional[StructureEvent] = None
    fvg: Optional[FVG] = None
    htf_trend_str: str = "none"


class ICT2022Model(BaseModel):
    name = "ict2022"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mc: ICT2022Config = self.mc
        self._pool_kinds = frozenset(LiquidityType(k) for k in self.mc.pool_kinds)
        self._smt_window = pd.Timedelta(self.cfg.smt.max_swing_time_diff)
        self.candidates: list[_Cand] = []
        if self.variant != "full":
            self._required: tuple[str, ...] = ()
        else:
            self._required = tuple(self.mc.required)

    # ------------------------------------------------------------------ #

    def on_bar(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        out += self._spawn(i)
        out += self._advance(i)
        return out

    # ------------------------------------------------------------------ #

    def _spawn(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        snap = self.ctx.htf_snapshot(i)
        for ev in self.ctx.liquidity.sweeps:
            if ev.sweep_idx != i:
                continue
            if ev.pool.kind not in self._pool_kinds:
                self.reject("POOL_KIND_NOT_WATCHED")
                continue
            if self.mc.htf_required:
                if snap is None:
                    self.reject("HTF_UNAVAILABLE")
                    continue
                bias = snap.trend.as_direction()
                if bias is None or bias is not ev.direction:
                    self.reject("HTF_BIAS_MISMATCH")
                    continue
            if ev.confirmed_breakout:
                self.reject("BREAKOUT_CONTINUED")
                continue
            cand = _Cand(
                setup_id=self.next_id(),
                direction=ev.direction,
                state="SWEPT",
                start_idx=i,
                start_time=ev.sweep_time,
                sweep=ev,
                htf_trend_str=(snap.trend.value if snap else "none"),
            )
            self.log.add(cand.setup_id, self.name, "WAITING_RAID", "SWEPT", i, ev.sweep_time,
                         f"{ev.pool.kind.value} @ {ev.level:.5f}, htf={cand.htf_trend_str}")
            self.candidates.append(cand)
            if ev.reclaimed:
                cand.state = "RECLAIMED"
                cand.anchor_idx = ev.reclaim_idx or i
                self.log.add(cand.setup_id, self.name, "SWEPT", "RECLAIMED", i, ev.sweep_time, "")
        return out

    # ------------------------------------------------------------------ #

    def _advance(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        for cand in list(self.candidates):
            ev = cand.sweep
            sweep_extreme = ev.level - ev.depth if cand.direction is Direction.LONG else ev.level + ev.depth
            if cand.direction is Direction.LONG and self.ctx.closes[i] < sweep_extreme:
                out.append(self._invalidate(cand, i, "CLOSED_BELOW_SWEEP_LOW"))
                continue
            if cand.direction is Direction.SHORT and self.ctx.closes[i] > sweep_extreme:
                out.append(self._invalidate(cand, i, "CLOSED_ABOVE_SWEEP_HIGH"))
                continue

            if cand.state == "SWEPT":
                if ev.reclaimed:
                    cand.state = "RECLAIMED"
                    cand.anchor_idx = ev.reclaim_idx or i
                    self.log.add(cand.setup_id, self.name, "SWEPT", "RECLAIMED", i, self.ctx.times[i], "")
                elif ev.confirmed_breakout:
                    out.append(self._invalidate(cand, i, "NO_RECLAIM"))
                    continue
                elif i - ev.sweep_idx > self.mc.max_sweep_to_reclaim_bars:
                    out.append(self._invalidate(cand, i, "NO_RECLAIM"))
                    continue

            if cand.state == "RECLAIMED":
                if VARIANT_TRIGGER[self.variant] == "RECLAIMED":
                    out.append(self._trigger_stage(cand, cand.anchor_idx, stage="reclaim"))
                    continue
                lo = cand.anchor_idx
                hi = lo + self.mc.max_reclaim_to_displacement_bars
                disp = self.displacement_between(cand.direction, lo - 1, hi)  # reclaim bar close may displace
                if disp is not None:
                    cand.disp = disp
                    cand.state = "DISPLACEMENT"
                    self.log.add(cand.setup_id, self.name, "RECLAIMED", "DISPLACEMENT", disp.idx,
                                 disp.time, f"body {disp.body_atr:.2f} ATR")
                elif i > hi:
                    out.append(self._invalidate(cand, i, "NO_DISPLACEMENT"))
                    continue

            if cand.state == "DISPLACEMENT":
                if VARIANT_TRIGGER[self.variant] == "DISPLACEMENT":
                    out.append(self._trigger_stage(cand, cand.disp.idx, stage="displacement"))
                    continue
                lo = cand.disp.idx
                hi = lo + self.mc.max_displacement_to_mss_bars
                mss = self.structure_between(cand.direction, lo - 1, hi)
                if mss is not None:
                    cand.mss = mss
                    cand.state = "MSS_CONFIRMED"
                    self.log.add(cand.setup_id, self.name, "DISPLACEMENT", "MSS_CONFIRMED", mss.idx,
                                 mss.time, f"{mss.kind} @ {mss.level:.5f}")
                elif i > hi:
                    out.append(self._invalidate(cand, i, "NO_MSS"))
                    continue

            if cand.state == "MSS_CONFIRMED":
                if VARIANT_TRIGGER[self.variant] in ("MSS_CONFIRMED",):
                    out.append(self._trigger_stage(cand, cand.mss.idx, stage="mss"))
                    continue
                lo = cand.disp.idx
                hi = cand.mss.idx + self.mc.max_mss_to_fvg_bars
                fvs = [f for f in self.fvgs_formed_between(cand.direction, lo, hi)
                       if f.active and f.size_atr >= self.mc.min_fvg_size_atr]
                if fvs:
                    cand.fvg = max(fvs, key=lambda f: f.size)
                    cand.state = "ENTRY_ZONE"
                    self.log.add(cand.setup_id, self.name, "MSS_CONFIRMED", "ENTRY_ZONE",
                                 cand.fvg.formed_idx, cand.fvg.formed_time,
                                 f"fvg {cand.fvg.bottom:.5f}-{cand.fvg.top:.5f}")
                elif i > hi:
                    out.append(self._invalidate(cand, i, "NO_FVG"))
                    continue

            if cand.state == "ENTRY_ZONE":
                out += self._entry_wait(cand, i)
        return out

    # ------------------------------------------------------------------ #

    def _entry_wait(self, cand: _Cand, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        fvg = cand.fvg
        long = cand.direction is Direction.LONG
        entry_level = fvg.top if (long and self.mc.entry_mode == "fvg_touch") else \
            fvg.bottom if self.mc.entry_mode == "fvg_touch" else fvg.mid
        j0 = max(cand.mss.idx, fvg.formed_idx) + 1
        j = j0
        while j <= i:
            o, h, l = self.ctx.opens[j], self.ctx.highs[j], self.ctx.lows[j]
            sweep_extreme = cand.sweep.level - cand.sweep.depth if long else cand.sweep.level + cand.sweep.depth
            if long and self.ctx.closes[j] < sweep_extreme:
                return [self._invalidate(cand, j, "CLOSED_BELOW_SWEEP_LOW")]
            if (not long) and self.ctx.closes[j] > sweep_extreme:
                return [self._invalidate(cand, j, "CLOSED_ABOVE_SWEEP_HIGH")]
            if long and l <= entry_level:
                fill = o if o <= entry_level else entry_level
                return [self._trigger(cand, j, fill, entry_level)]
            if (not long) and h >= entry_level:
                fill = o if o >= entry_level else entry_level
                return [self._trigger(cand, j, fill, entry_level)]
            j += 1
        if i - j0 > self.mc.max_entry_wait_bars:
            out.append(self._expire(cand, i, "ENTRY_TIMEOUT"))
        return out

    def _trigger_stage(self, cand: _Cand, j: int, stage: str):
        entry = self.ctx.closes[j]
        return self._trigger(cand, j, entry, entry)

    def _trigger(self, cand: _Cand, j: int, entry: float, entry_level: float):
        ev = cand.sweep
        long = cand.direction is Direction.LONG
        sweep_extreme = ev.level - ev.depth if long else ev.level + ev.depth
        target = self.nearest_target(j, entry, cand.direction)
        cand.state = "TRIGGERED"
        self.candidates.remove(cand)
        self.log.add(cand.setup_id, self.name, cand.state, "TRIGGERED", j, self.ctx.times[j],
                     f"variant={self.variant}")
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
            displacement_idx=(cand.disp.idx if cand.disp else None),
            displacement_body_atr=(cand.disp.body_atr if cand.disp else None),
            displacement_pct_rank=(cand.disp.body_pct_rank if cand.disp else None),
            mss_idx=(cand.mss.idx if cand.mss else None),
            mss_time=(cand.mss.time if cand.mss else None),
            mss_price=(cand.mss.level if cand.mss else None),
            fvg_low=(cand.fvg.bottom if cand.fvg else None),
            fvg_high=(cand.fvg.top if cand.fvg else None),
            fvg_mid=(cand.fvg.mid if cand.fvg else None),
            entry_zone_low=(cand.fvg.bottom if cand.fvg else sweep_extreme),
            entry_zone_high=(cand.fvg.top if cand.fvg else ev.level),
            entry_price=entry,
            invalidation=sweep_extreme,
            target_type=(target.kind.value if target else None),
            target_price=(target.price if target else None),
            meta={"trigger_stage": VARIANT_TRIGGER[self.variant].lower(),
                  "htf_trend": cand.htf_trend_str},
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
        long = cand.direction is Direction.LONG
        fvg = cand.fvg
        smt_ok, smt_detail = False, "no smt events"
        for s in self.smt_events:
            if s.direction is cand.direction and abs(s.time - ev.sweep_time) <= self._smt_window:
                smt_ok = True
                smt_detail = f"smt {s.direction.value} at {s.time}"
                break
        ext_kinds = {LiquidityType.PDH, LiquidityType.PDL, LiquidityType.PWH,
                     LiquidityType.PWL, LiquidityType.PREV_SESSION_HIGH,
                     LiquidityType.PREV_SESSION_LOW}
        components = {
            "htf_bias": (cand.htf_trend_str != "none",
                         f"htf trend {cand.htf_trend_str}"),
            "external_liquidity": (
                ev.pool.kind in ext_kinds,
                f"{ev.pool.kind.value}",
            ),
            "clean_sweep": (
                ev.kind is SweepKind.WICK and ev.depth_atr <= 1.0,
                f"{ev.kind.value}, depth {ev.depth_atr:.2f} ATR",
            ),
        }
        components["reclaim"] = (True, f"bar {ev.reclaim_idx}")
        components["displacement"] = (
            cand.disp is not None,
            f"body {cand.disp.body_atr:.2f} ATR" if cand.disp else "not reached",
        )
        components["strong_displacement"] = (
            cand.disp is not None and cand.disp.body_atr >= self.cfg.displacement.strong_body_atr,
            f"{cand.disp.body_atr:.2f} ATR" if cand.disp else "not reached",
        )
        components["confirmed_mss"] = (
            cand.mss is not None,
            f"{cand.mss.kind} @ {cand.mss.level:.5f}" if cand.mss else "not reached",
        )
        components["fvg"] = (fvg is not None,
                             f"{fvg.bottom:.5f}-{fvg.top:.5f}" if fvg else "not reached")
        components["fvg_quality"] = (
            fvg is not None and fvg.size_atr >= 2.0 * self.mc.min_fvg_size_atr,
            f"{fvg.size_atr:.2f} ATR" if fvg else "not reached",
        )
        components["retracement"] = (True, "entry touched")
        components["session_context"] = (self.in_killzone(i), f"session={self.ctx.session_at(i)}")
        components["smt_confirmation"] = (smt_ok, smt_detail)
        return build_score(components, self.mc.weights, self._required)
