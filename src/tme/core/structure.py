"""Market structure: BOS and MSS/CHoCH from confirmed swings.

A structural break is a CLOSE beyond the most recent unbroken confirmed swing
level (wick breaks are liquidity sweeps, handled by the liquidity engine).
MSS requires the prior trend to be opposite; BOS continues the current trend.
The first break of a session sets the initial trend and is labeled BOS.
"""

from __future__ import annotations

from tme.config import StructureConfig
from tme.types import Direction, StructureEvent, Swing, Trend


class StructureTracker:
    def __init__(self, cfg: StructureConfig, times):
        self.cfg = cfg
        self.times = times
        self.trend = Trend.NONE
        self.events: list[StructureEvent] = []
        self._unbroken_highs: list[Swing] = []
        self._unbroken_lows: list[Swing] = []

    def on_swing(self, swing: Swing) -> None:
        if swing.side.value == "high":
            self._unbroken_highs.append(swing)
        else:
            self._unbroken_lows.append(swing)

    @property
    def last_unbroken_high(self) -> Swing | None:
        return self._unbroken_highs[-1] if self._unbroken_highs else None

    @property
    def last_unbroken_low(self) -> Swing | None:
        return self._unbroken_lows[-1] if self._unbroken_lows else None

    def update(self, i: int, close: float) -> list[StructureEvent]:
        if not self.cfg.use_close_break:
            raise NotImplementedError("only close-based breaks are supported")
        events: list[StructureEvent] = []
        events += self._check_side(i, close, Direction.LONG, self._unbroken_highs, Trend.BULL)
        events += self._check_side(i, close, Direction.SHORT, self._unbroken_lows, Trend.BEAR)
        return events

    def _check_side(
        self,
        i: int,
        close: float,
        direction: Direction,
        stack: list[Swing],
        new_trend: Trend,
    ) -> list[StructureEvent]:
        out: list[StructureEvent] = []
        while stack and (
            close > stack[-1].price if direction is Direction.LONG else close < stack[-1].price
        ):
            swing = stack.pop()
            opposite = (self.trend is Trend.BULL and new_trend is Trend.BEAR) or (
                self.trend is Trend.BEAR and new_trend is Trend.BULL
            )
            kind = "MSS" if opposite else "BOS"
            self.trend = new_trend
            ev = StructureEvent(
                idx=i,
                time=self.times[i],
                kind=kind,
                direction=direction,
                level=swing.price,
                swing=swing,
            )
            self.events.append(ev)
            out.append(ev)
        return out
