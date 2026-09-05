"""PO3 (Power of Three): Accumulation -> Manipulation -> Distribution.

Accumulation: objectively measurable — the configured session instance must
compress into a range between min/max ATR multiples.
Manipulation: a raid of ONE side of the range (wick breach + acceptance back
inside, or a close-through that is reclaimed within the liquidity engine's
reclaim window).
Distribution: displacement in the opposite direction plus expansion — a close
beyond the opposite range boundary — inside the distribution window.

Both bullish and bearish PO3 supported; direction is decided by the
manipulation side, never assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from tme.config import PO3Config
from tme.models.base import BaseModel
from tme.scoring import build_score
from tme.types import Direction, DisplacementEvent


@dataclass
class _Cand:
    setup_id: str
    direction: Optional[Direction]
    state: str
    start_idx: int
    start_time: Any
    range_high: float
    range_low: float
    range_end_idx: int
    manip_extreme: float = float("nan")
    manip_side: str = ""
    manip_depth_atr: float = 0.0
    manip_idx: int = -1
    breach_low: Optional[float] = None
    breach_high: Optional[float] = None
    breached_idx: int = -1
    disp: Optional[DisplacementEvent] = None


class PO3Model(BaseModel):
    name = "po3"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mc: PO3Config = self.mc
        self.candidates: list[_Cand] = []
        self._seen_sessions: set[tuple[str, int]] = set()

    def on_bar(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        out += self._arm_from_session_close(i)
        out += self._advance(i)
        return out

    # ------------------------------------------------------------------ #

    def _arm_from_session_close(self, i: int) -> list[SetupEvent]:
        ev = self.ctx.clock.closed.get(self.mc.accumulation_session)
        if ev is None or (ev.name, ev.end_idx) in self._seen_sessions:
            return []
        self._seen_sessions.add((ev.name, ev.end_idx))
        atr = self.ctx.atr.values[ev.end_idx]
        if atr <= 0:
            self.reject("NO_ATR")
            return []
        rng = ev.high - ev.low
        ratio = rng / atr
        if not (self.mc.min_range_atr <= ratio <= self.mc.max_range_atr):
            self.reject(f"RANGE_NOT_COMPRESSED({ratio:.2f} ATR)")
            return []
        cand = _Cand(
            setup_id=self.next_id(),
            direction=None,
            state="RANGE_CONFIRMED",
            start_idx=ev.end_idx + 1,
            start_time=self.ctx.times[min(ev.end_idx + 1, self.ctx.n - 1)],
            range_high=ev.high,
            range_low=ev.low,
            range_end_idx=ev.end_idx,
        )
        self.log.add(cand.setup_id, self.name, "ACCUMULATION", "RANGE_CONFIRMED",
                     ev.end_idx, ev.end_time,
                     f"range {ev.low:.5f}-{ev.high:.5f} ({ratio:.2f} ATR)")
        self.candidates.append(cand)
        return []

    # ------------------------------------------------------------------ #

    def _advance(self, i: int) -> list[SetupEvent]:
        out: list[SetupEvent] = []
        for cand in list(self.candidates):
            if cand.state == "RANGE_CONFIRMED":
                res = self._manipulation(cand, i)
                if res is not None:
                    out.append(res)
                    continue
            if cand.state == "MANIPULATION":
                res = self._distribution(cand, i)
                if res is not None:
                    out.append(res)
        return out

    def _manipulation(self, cand: _Cand, i: int):
        """Watch both range sides. First accepted raid decides direction."""
        deadline = cand.range_end_idx + self.mc.manipulation_window_bars
        atr = self.ctx.atr.values[i]
        reclaim_limit = self.cfg.liquidity.max_reclaim_bars

        for j in range(cand.breached_idx + 1 if cand.breached_idx >= 0 else cand.range_end_idx + 1, i + 1):
            h, l, c = self.ctx.highs[j], self.ctx.lows[j], self.ctx.closes[j]
            # low side (bullish PO3)
            if l < cand.range_low:
                cand.breach_low = min(cand.breach_low, l) if cand.breach_low is not None else l
            if c < cand.range_low and cand.breached_idx < 0:
                cand.breached_idx = j
            # high side (bearish PO3)
            if h > cand.range_high:
                cand.breach_high = max(cand.breach_high, h) if cand.breach_high is not None else h
            if c > cand.range_high and cand.breached_idx < 0:
                cand.breached_idx = j

            # acceptance back inside after either kind of breach
            if cand.breach_low is not None and c > cand.range_low:
                return self._manipulation_confirmed(cand, j, Direction.LONG,
                                                    cand.breach_low, cand.range_low, atr)
            if cand.breach_high is not None and c < cand.range_high:
                return self._manipulation_confirmed(cand, j, Direction.SHORT,
                                                    cand.breach_high, cand.range_high, atr)

            # close-through held too long: real breakdown, not manipulation
            if cand.breached_idx >= 0 and j - cand.breached_idx > reclaim_limit:
                return self._invalidate(cand, j, "RANGE_BROKEN")

        if i > deadline and cand.manip_idx < 0:
            return self._invalidate(cand, i, "NO_MANIPULATION")
        return None

    def _manipulation_confirmed(self, cand: _Cand, j: int, direction: Direction,
                                extreme: float, level: float, atr: float):
        cand.direction = direction
        cand.manip_extreme = extreme
        cand.manip_side = "low" if direction is Direction.LONG else "high"
        cand.manip_depth_atr = abs(level - extreme) / atr if atr > 0 else 0.0
        cand.manip_idx = j
        cand.state = "MANIPULATION"
        self.log.add(cand.setup_id, self.name, "RANGE_CONFIRMED", "MANIPULATION", j,
                     self.ctx.times[j],
                     f"{cand.manip_side} raid, depth {cand.manip_depth_atr:.2f} ATR")
        return None

    def _distribution(self, cand: _Cand, i: int):
        deadline = cand.manip_idx + self.mc.distribution_window_bars
        for j in range(cand.manip_idx, min(i, deadline) + 1):
            disp = self.displacement_between(cand.direction, cand.manip_idx - 1, j)
            if disp is None:
                continue
            cand.disp = disp
            expansion = (self.ctx.closes[j] > cand.range_high
                         if cand.direction is Direction.LONG
                         else self.ctx.closes[j] < cand.range_low)
            if expansion:
                return self._trigger(cand, j)
        if i > deadline:
            return self._invalidate(cand, i, "NO_DISTRIBUTION")
        return None

    # ------------------------------------------------------------------ #

    def _trigger(self, cand: _Cand, j: int):
        entry = self.ctx.closes[j]
        atr = self.ctx.atr.values[j]
        target = self.nearest_target(j, entry, cand.direction)
        magnitude = ((entry - cand.range_high) if cand.direction is Direction.LONG
                     else (cand.range_low - entry)) / atr if atr > 0 else 0.0
        cand.state = "TRIGGERED"
        self.candidates.remove(cand)
        self.log.add(cand.setup_id, self.name, "MANIPULATION", "TRIGGERED", j, self.ctx.times[j], "")
        zone = (cand.manip_extreme, cand.range_high) if cand.direction is Direction.LONG \
            else (cand.range_low, cand.manip_extreme)
        return self.make_event(
            cand, "TRIGGERED", j,
            score=self._score(cand, j, magnitude),
            liquidity_type=f"po3_range_{cand.manip_side}",
            liquidity_price=(cand.range_low if cand.manip_side == "low" else cand.range_high),
            sweep_idx=cand.manip_idx,
            sweep_time=self.ctx.times[cand.manip_idx],
            sweep_price=cand.manip_extreme,
            sweep_kind="po3_manipulation",
            sweep_depth_atr=cand.manip_depth_atr,
            displacement_idx=(cand.disp.idx if cand.disp else None),
            displacement_body_atr=(cand.disp.body_atr if cand.disp else None),
            displacement_pct_rank=(cand.disp.body_pct_rank if cand.disp else None),
            entry_zone_low=zone[0],
            entry_zone_high=zone[1],
            entry_price=entry,
            invalidation=cand.manip_extreme,
            target_type=(target.kind.value if target else None),
            target_price=(target.price if target else None),
            meta={
                "accumulation_high": cand.range_high,
                "accumulation_low": cand.range_low,
                "manipulation_side": cand.manip_side,
                "manipulation_depth_atr": cand.manip_depth_atr,
                "distribution_magnitude_atr": magnitude,
            },
        )

    def _invalidate(self, cand: _Cand, i: int, reason: str):
        self.candidates.remove(cand)
        self.log.add(cand.setup_id, self.name, cand.state, "INVALIDATED", i, self.ctx.times[i], reason)
        return self.make_event(cand, "INVALIDATED", i, failure=reason)

    # ------------------------------------------------------------------ #

    def _score(self, cand: _Cand, i: int, magnitude: float):
        snap = self.ctx.htf_snapshot(i)
        htf_dir = snap.trend.as_direction() if snap else None
        components = {
            "compressed_range": (True, f"{cand.range_low:.5f}-{cand.range_high:.5f}"),
            "manipulation": (cand.manip_idx >= 0,
                             f"{cand.manip_side} raid {cand.manip_depth_atr:.2f} ATR"),
            "distribution": (cand.disp is not None,
                             f"{magnitude:+.2f} ATR beyond range" if cand.disp else ""),
            "session_context": (self.in_killzone(i), f"session={self.ctx.session_at(i)}"),
            "htf_alignment": (htf_dir is not None and htf_dir is cand.direction,
                              f"htf={htf_dir.value if htf_dir else 'none'}"),
        }
        return build_score(components, self.mc.weights, self.mc.required)
