"""Measurable displacement — no subjective "strong candle".

A bar displaces when its body/range metrics clear configurable thresholds and
its body is large relative to the trailing distribution (percentile rank) or
absolutely strong (body/ATR above the strong threshold).
"""

from __future__ import annotations

from collections import deque

from tme.config import DisplacementConfig
from tme.types import Direction, DisplacementEvent


class DisplacementDetector:
    def __init__(self, cfg: DisplacementConfig, times):
        self.cfg = cfg
        self.times = times
        self.events: list[DisplacementEvent] = []
        self._body_atr_hist: deque[float] = deque(maxlen=cfg.pct_lookback)

    def update(self, i: int, o: float, h: float, l: float, c: float, atr: float) -> DisplacementEvent | None:
        rng = h - l
        if rng <= 0 or atr <= 0:
            self._body_atr_hist.append(0.0)
            return None
        body = abs(c - o)
        body_atr = body / atr
        self._body_atr_hist.append(body_atr)
        if c == o:
            return None
        body_ratio = body / rng
        close_location = (c - l) / rng if c > o else (h - c) / rng
        direction = Direction.LONG if c > o else Direction.SHORT
        hist = self._body_atr_hist
        pct_rank = sum(1 for v in hist if v <= body_atr) / len(hist) * 100.0

        passed = (
            body_atr >= self.cfg.min_body_atr
            and body_ratio >= self.cfg.min_body_ratio
            and close_location >= self.cfg.min_close_location
            and (body_atr >= self.cfg.strong_body_atr or pct_rank >= self.cfg.min_pct_rank)
        )
        if not passed:
            return None
        ev = DisplacementEvent(
            idx=i,
            time=self.times[i],
            direction=direction,
            body=body,
            bar_range=rng,
            atr=atr,
            body_atr=body_atr,
            range_atr=rng / atr,
            body_ratio=body_ratio,
            body_pct_rank=pct_rank,
            close_location=close_location,
        )
        self.events.append(ev)
        return ev
