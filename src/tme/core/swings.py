"""Confirmed swing highs/lows.

A pivot at bar p (with left/right window) is only *confirmed* at the close of
bar p + right. Before that it does not exist — no future pivot knowledge is
ever exposed. Labels (HH/HL/LH/LL) compare against the previous confirmed
swing of the same side.
"""

from __future__ import annotations

from tme.config import SwingConfig
from tme.types import Side, Swing


class SwingDetector:
    def __init__(self, cfg: SwingConfig, times):
        self.cfg = cfg
        self.times = times  # shared timestamp array (index -> time)
        self.swings: list[Swing] = []
        self._last_by_side: dict[Side, Swing | None] = {Side.HIGH: None, Side.LOW: None}
        self._seq = 0

    def update(self, i: int, highs: list[float], lows: list[float]) -> list[Swing]:
        """Confirm any pivot whose confirmation bar is `i`. Returns new swings."""
        out: list[Swing] = []
        p = i - self.cfg.right
        if p - self.cfg.left < 0:
            return out
        for side, series in ((Side.HIGH, highs), (Side.LOW, lows)):
            if self._is_pivot(p, series, side):
                swing = self._make(p, series[p], side, i)
                out.append(swing)
        return out

    def _is_pivot(self, p: int, series: list[float], side: Side) -> bool:
        left = range(p - self.cfg.left, p)
        right = range(p + 1, p + self.cfg.right + 1)
        v = series[p]
        if side is Side.HIGH:
            if any(series[j] >= v for j in right):   # strict on the right side
                return False
            return all(series[j] <= v for j in left)
        if any(series[j] <= v for j in right):
            return False
        return all(series[j] >= v for j in left)

    def _make(self, p: int, price: float, side: Side, confirm_idx: int) -> Swing:
        prev = self._last_by_side[side]
        if side is Side.HIGH:
            label = None if prev is None else ("HH" if price > prev.price else "LH")
        else:
            label = None if prev is None else ("HL" if price > prev.price else "LL")
        self._seq += 1
        swing = Swing(
            id=f"sw{self._seq}",
            side=side,
            price=price,
            pivot_idx=p,
            pivot_time=self.times[p],
            confirm_idx=confirm_idx,
            confirm_time=self.times[confirm_idx],
            label=label,
        )
        self._last_by_side[side] = swing
        self.swings.append(swing)
        return swing
