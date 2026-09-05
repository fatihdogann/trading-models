"""Fair Value Gap (3-bar imbalance) detection and mitigation tracking.

Bullish FVG at bar i: low[i] > high[i-2]  (gap = low[i] - high[i-2]).
Bearish FVG at bar i: high[i] < low[i-2]  (gap = low[i-2] - high[i]).
Formed (knowable) at the close of bar i. Mitigation is evaluated on every
subsequent bar: first touch, fill ratio, full fill.
"""

from __future__ import annotations

from tme.config import FVGConfig
from tme.types import Direction, FVG, FVGStatus


class FVGDetector:
    def __init__(self, cfg: FVGConfig, times, opens, highs, lows, closes):
        self.cfg = cfg
        self.times = times
        self.opens = opens
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.fvgs: list[FVG] = []
        self._seq = 0

    def update(self, i: int, atr: float) -> list[FVG]:
        new = self._detect(i, atr)
        self._update_mitigation(i)
        return new

    def _detect(self, i: int, atr: float) -> list[FVG]:
        if i < 2 or atr <= 0:
            return []
        out: list[FVG] = []
        if self.lows[i] > self.highs[i - 2]:
            gap = self.lows[i] - self.highs[i - 2]
            if gap / atr >= self.cfg.min_size_atr:
                if (not self.cfg.require_mid_candle_direction) or self.closes[i - 1] > self.opens[i - 1]:
                    out.append(self._make(i, Direction.LONG, top=self.lows[i], bottom=self.highs[i - 2], gap=gap, atr=atr))
        if self.highs[i] < self.lows[i - 2]:
            gap = self.lows[i - 2] - self.highs[i]
            if gap / atr >= self.cfg.min_size_atr:
                if (not self.cfg.require_mid_candle_direction) or self.closes[i - 1] < self.opens[i - 1]:
                    out.append(self._make(i, Direction.SHORT, top=self.lows[i - 2], bottom=self.highs[i], gap=gap, atr=atr))
        return out

    def _make(self, i: int, direction: Direction, top: float, bottom: float, gap: float, atr: float) -> FVG:
        self._seq += 1
        f = FVG(
            id=f"fvg{self._seq}",
            direction=direction,
            top=top,
            bottom=bottom,
            mid=(top + bottom) / 2.0,
            formed_idx=i,
            formed_time=self.times[i],
            size=gap,
            size_atr=gap / atr,
        )
        self.fvgs.append(f)
        return f

    def _update_mitigation(self, i: int) -> None:
        for f in self.fvgs:
            if not f.active or f.formed_idx == i:
                continue
            hi, lo = self.highs[i], self.lows[i]
            if f.direction is Direction.LONG:
                if lo <= f.top:
                    if f.first_touch_idx is None:
                        f.first_touch_idx = i
                        f.first_touch_time = self.times[i]
                    ratio = min(1.0, (f.top - lo) / f.size) if f.size > 0 else 1.0
                    f.max_fill_ratio = max(f.max_fill_ratio, ratio)
                    if lo <= f.bottom:
                        f.status = FVGStatus.FULL_FILLED
                        f.filled_idx = i
                    elif f.status is FVGStatus.ACTIVE:
                        f.status = FVGStatus.PARTIAL
            else:
                if hi >= f.bottom:
                    if f.first_touch_idx is None:
                        f.first_touch_idx = i
                        f.first_touch_time = self.times[i]
                    ratio = min(1.0, (hi - f.bottom) / f.size) if f.size > 0 else 1.0
                    f.max_fill_ratio = max(f.max_fill_ratio, ratio)
                    if hi >= f.top:
                        f.status = FVGStatus.FULL_FILLED
                        f.filled_idx = i
                    elif f.status is FVGStatus.ACTIVE:
                        f.status = FVGStatus.PARTIAL
