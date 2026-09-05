"""HTF context: trend, dealing range, premium/discount, external liquidity.

Runs the SAME primitive implementations (swings, structure, ATR) on a higher
timeframe and takes a snapshot after every HTF bar close. LTF models query
`asof(t)`: the latest snapshot whose bar-close time <= t — never future data.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Optional

from tme.config import ATRConfig, HTFConfig
from tme.core.atr import ATR
from tme.core.structure import StructureTracker
from tme.core.swings import SwingDetector
from tme.types import Side, Trend


@dataclass(frozen=True)
class HTFSnapshot:
    time: Any
    trend: Trend
    dr_high: Optional[float]
    dr_low: Optional[float]
    eq: Optional[float]
    last_high_price: Optional[float]
    last_low_price: Optional[float]
    unbroken_high_prices: tuple[float, ...]
    unbroken_low_prices: tuple[float, ...]

    def zone(self, price: float, eq_band: float) -> str:
        """premium / discount / equilibrium relative to the dealing range."""
        if self.dr_high is None or self.dr_low is None:
            return "unknown"
        span = self.dr_high - self.dr_low
        if span <= 0:
            return "unknown"
        pos = (price - self.dr_low) / span
        if pos > 0.5 + eq_band:
            return "premium"
        if pos < 0.5 - eq_band:
            return "discount"
        return "equilibrium"

    def nearest_external(self, price: float) -> tuple[Optional[float], Optional[float]]:
        """(nearest unbroken high above price, nearest unbroken low below)."""
        hi = min((p for p in self.unbroken_high_prices if p > price), default=None)
        lo = max((p for p in self.unbroken_low_prices if p < price), default=None)
        return hi, lo


class HTFContext:
    def __init__(self, cfg: HTFConfig, atr_cfg: ATRConfig, df, symbol: str, timeframe: str):
        self.symbol = symbol
        self.timeframe = timeframe
        self.times = list(df.index)
        self.opens = [float(v) for v in df["open"]]
        self.highs = [float(v) for v in df["high"]]
        self.lows = [float(v) for v in df["low"]]
        self.closes = [float(v) for v in df["close"]]
        self.atr = ATR(atr_cfg)
        self.swings = SwingDetector(cfg.swing, self.times)
        self.structure = StructureTracker(
            type("S", (), {"use_close_break": True})(), self.times
        )
        self.snapshots: list[HTFSnapshot] = []
        self._close_times = list(self.times)

    def step(self, i: int) -> HTFSnapshot | None:
        o, h, l, c = self.opens[i], self.highs[i], self.lows[i], self.closes[i]
        self.atr.update(h, l, c)
        for s in self.swings.update(i, self.highs, self.lows):
            self.structure.on_swing(s)
        self.structure.update(i, c)
        snap = self._snapshot(i)
        self.snapshots.append(snap)
        return snap

    def run(self) -> None:
        for i in range(len(self.closes)):
            self.step(i)

    def _snapshot(self, i: int) -> HTFSnapshot:
        highs = [s for s in self.swings.swings if s.side is Side.HIGH]
        lows = [s for s in self.swings.swings if s.side is Side.LOW]
        last_high = highs[-1].price if highs else None
        last_low = lows[-1].price if lows else None
        dr_high = max(last_high, last_low) if (last_high is not None and last_low is not None) else last_high
        dr_low = min(last_high, last_low) if (last_high is not None and last_low is not None) else last_low
        return HTFSnapshot(
            time=self.times[i],
            trend=self.structure.trend,
            dr_high=dr_high,
            dr_low=dr_low,
            eq=(dr_high + dr_low) / 2.0 if (dr_high is not None and dr_low is not None) else None,
            last_high_price=last_high,
            last_low_price=last_low,
            unbroken_high_prices=tuple(s.price for s in self.structure._unbroken_highs),
            unbroken_low_prices=tuple(s.price for s in self.structure._unbroken_lows),
        )

    def asof(self, t: Any) -> HTFSnapshot | None:
        idx = bisect_right(self._close_times, t) - 1
        if idx < 0:
            return None
        return self.snapshots[idx]
