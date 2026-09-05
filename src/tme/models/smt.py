"""SMT Divergence (cross-asset correlation model).

Compares CORRESPONDING CONFIRMED swings between two assets — never raw
candle highs/lows. Asset A makes a lower low while corresponding-swing Asset
B holds a higher low (bullish SMT), or A makes a higher high while B does
not (bearish SMT).

"Corresponding" = B's most recent confirmed same-side swing whose pivot time
is within `max_swing_time_diff` of A's pivot and which was already confirmed
(by close time) when A's swing confirmed — no lookahead.

Also computes: divergence magnitude (ATR-normalized), swing timing
difference, rolling return correlation context, and resolution (an opposing
structure shift on A validating the divergence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import Any, Optional

import pandas as pd

from tme.config import SMTConfig
from tme.models.base import BaseModel
from tme.scoring import build_score
from tme.types import Direction, Side, Swing


@dataclass
class SMTDivergence:
    id: str
    direction: Direction
    side: Side
    idx: int
    time: Any
    a_swing: Swing
    b_swing: Swing
    a_change: float
    b_change: float
    magnitude_atr: float
    timing_diff: pd.Timedelta
    correlation: Optional[float]
    resolution: Optional[str] = None
    resolved_idx: Optional[int] = None
    resolved_time: Any = None


@dataclass
class _Pending:
    div: SMTDivergence
    deadline_idx: int


class SMTModel(BaseModel):
    name = "smt"

    def __init__(self, ctx_a, ctx_b, *args, **kwargs):
        super().__init__(ctx_a, *args, **kwargs)
        self.mc: SMTConfig = self.mc
        self.ctx_b = ctx_b
        self.events: list[SMTDivergence] = []
        self._pending: list[_Pending] = []
        self._cursor = 0  # next A swing index to process
        self._tol = pd.Timedelta(self.mc.max_swing_time_diff)

    def on_bar(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        out += self._detect_new_swings(i)
        out += self._track_resolution(i)
        return out

    # ------------------------------------------------------------------ #

    def _detect_new_swings(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        a_swings = self.ctx.swings.swings
        while self._cursor < len(a_swings) and a_swings[self._cursor].confirm_idx <= i:
            s = a_swings[self._cursor]
            self._cursor += 1
            if s.confirm_idx != i:
                continue  # processed in an earlier step already
            prev_same = [w for w in a_swings if w.side is s.side and w.confirm_idx < s.confirm_idx]
            if not prev_same:
                continue
            a_prev = prev_same[-1]
            b_pair = self._corresponding_b(s)
            if b_pair is None:
                self.reject("NO_CORRESPONDING_B_SWING")
                continue
            b, b_prev = b_pair
            atr_a = self.ctx.atr.values[i]
            if atr_a <= 0:
                continue
            a_change = s.price - a_prev.price
            b_change = b.price - b_prev.price
            if s.side is Side.LOW:
                direction = Direction.LONG
                magnitude = (b_change - a_change) / atr_a
                extends = a_change < 0
            else:
                direction = Direction.SHORT
                magnitude = (a_change - b_change) / atr_a
                extends = a_change > 0
            if not extends or magnitude < self.mc.min_divergence_atr:
                self.reject("NO_DIVERGENCE")
                continue
            corr = self._correlation(i)
            div = SMTDivergence(
                id=self.next_id(),
                direction=direction,
                side=s.side,
                idx=i,
                time=s.confirm_time,
                a_swing=s,
                b_swing=b,
                a_change=a_change,
                b_change=b_change,
                magnitude_atr=magnitude,
                timing_diff=abs(s.pivot_time - b.pivot_time),
                correlation=corr,
            )
            self.events.append(div)
            self._pending.append(_Pending(div, i + self.mc.resolution_max_bars))
            self.log.add(div.id, self.name, "SCANNING", "DIVERGENCE", i, s.confirm_time,
                         f"{direction.value}: A {a_change:+.5f} vs B {b_change:+.5f} "
                         f"({magnitude:.2f} ATR)")
            ev = self.make_event(
                _SMTView(div), "TRIGGERED", i,
                score=self._score(div),
                meta={
                    "a_swing_price": s.price,
                    "b_swing_price": b.price,
                    "a_change": a_change,
                    "b_change": b_change,
                    "correlation": corr,
                },
            )
            out.append(ev)
        return out

    def _corresponding_b(self, s: Swing) -> Optional[tuple[Swing, Swing]]:
        b_same = [w for w in self.ctx_b.swings.swings if w.side is s.side]
        best = None
        for w in b_same:
            if w.confirm_time > s.confirm_time:
                continue  # not knowable when A's swing confirmed
            diff = abs(w.pivot_time - s.pivot_time)
            if diff > self._tol:
                continue
            if best is None or diff < abs(best.pivot_time - s.pivot_time):
                best = w
        if best is None:
            return None
        earlier = [w for w in b_same if w.confirm_idx < best.confirm_idx]
        if not earlier:
            return None
        return best, earlier[-1]

    def _correlation(self, i: int) -> Optional[float]:
        n = self.mc.correlation_window
        lo = max(1, i - n + 1)
        ra: list[float] = []
        rb: list[float] = []
        b_times = self.ctx_b.times
        b_closes = self.ctx_b.closes
        import bisect

        for j in range(lo, i + 1):
            t = self.ctx.times[j]
            k = bisect.bisect_right(b_times, t) - 1
            if k < 1:
                continue
            ra.append(self.ctx.closes[j] / self.ctx.closes[j - 1] - 1.0)
            rb.append(b_closes[k] / b_closes[k - 1] - 1.0)
        if len(ra) < 10:
            return None
        ma = sum(ra) / len(ra)
        mb = sum(rb) / len(rb)
        cov = sum((a - ma) * (b - mb) for a, b in zip(ra, rb))
        va = sum((a - ma) ** 2 for a in ra)
        vb = sum((b - mb) ** 2 for b in rb)
        if va <= 0 or vb <= 0:
            return None
        return cov / sqrt(va * vb)

    def _track_resolution(self, i: int) -> list[SetupEvent]:
        for p in list(self._pending):
            if p.div.resolution is not None:
                self._pending.remove(p)
                continue
            for ev in reversed(self.ctx.structure.events):
                if ev.idx <= p.div.idx:
                    break
                if ev.direction is p.div.direction:
                    p.div.resolution = "structure_shift"
                    p.div.resolved_idx = ev.idx
                    p.div.resolved_time = ev.time
                    self.log.add(p.div.id, self.name, "DIVERGENCE", "RESOLVED", ev.idx,
                                 ev.time, f"{ev.kind} {ev.direction.value}")
                    break
            if p.div.resolution is None and i > p.deadline_idx:
                p.div.resolution = "unresolved"
                self.log.add(p.div.id, self.name, "DIVERGENCE", "UNRESOLVED", i,
                             self.ctx.times[i], "")
        return []

    def _score(self, div: SMTDivergence):
        components = {
            "divergence": (True, f"{div.magnitude_atr:.2f} ATR"),
            "tight_timing": (div.timing_diff <= self._tol / 2,
                             f"{div.timing_diff}"),
            "correlation": (div.correlation is not None and div.correlation >= 0.6,
                            f"{div.correlation:.2f}" if div.correlation is not None else "n/a"),
            "magnitude": (div.magnitude_atr >= 2.0 * self.mc.min_divergence_atr,
                          f"{div.magnitude_atr:.2f} ATR"),
            "resolution": (div.resolution == "structure_shift",
                           div.resolution or "pending"),
        }
        return build_score(components, self.mc.weights, self.mc.required)


class _SMTView:
    """Minimal candidate interface for BaseModel.make_event."""

    def __init__(self, div: SMTDivergence):
        self.setup_id = div.id
        self.direction = div.direction
        self.start_idx = div.idx
        self.start_time = div.time
