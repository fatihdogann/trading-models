"""ATR (Wilder). Expanding mean warm-up so early bars have a usable value."""

from __future__ import annotations

from tme.config import ATRConfig


class ATR:
    def __init__(self, cfg: ATRConfig):
        self.cfg = cfg
        self.values: list[float] = []
        self._tr_sum = 0.0
        self._prev_close: float | None = None

    def update(self, high: float, low: float, close: float) -> float:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        n = len(self.values) + 1
        if n <= self.cfg.period:
            self._tr_sum += tr
            atr = self._tr_sum / n
        else:
            atr = (self.values[-1] * (self.cfg.period - 1) + tr) / self.cfg.period
        self.values.append(atr)
        self._prev_close = close
        return atr

    def __getitem__(self, idx: int) -> float:
        return self.values[idx]
