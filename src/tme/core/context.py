"""MarketContext: per (symbol, timeframe) bar-by-bar orchestration.

Single source of truth for what is knowable at bar i. The step order is fixed
and deterministic:

  1. ATR (current bar)
  2. session clock (closes previous session instance, day/week rollovers)
  3. swing confirmation (pivot + right bars)
  4. structure update on close (BOS/MSS) using newly confirmed swings
  5. sweep lifecycle check against the bar (pools created in steps 2-4 included)
  6. FVG detection (bars i-2..i) + mitigation
  7. displacement evaluation
  8. session/day/week extremes updated with this bar (AFTER sweep checks)
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from tme.config import EngineConfig
from tme.core.atr import ATR
from tme.core.displacement import DisplacementDetector
from tme.core.fvg import FVGDetector
from tme.core.htf import HTFContext, HTFSnapshot
from tme.core.liquidity import LiquidityEngine
from tme.core.sessions import SessionClock
from tme.core.structure import StructureTracker
from tme.core.swings import SwingDetector


class MarketContext:
    def __init__(self, symbol: str, timeframe: str, df: pd.DataFrame, cfg: EngineConfig,
                 htf: Optional[HTFContext] = None):
        from tme.dataio import validate_ohlc

        self.symbol = symbol
        self.timeframe = timeframe
        self.cfg = cfg
        self.htf = htf
        validate_ohlc(df, name=f"{symbol} {timeframe}")
        self.times = list(df.index)
        self.opens = [float(v) for v in df["open"]]
        self.highs = [float(v) for v in df["high"]]
        self.lows = [float(v) for v in df["low"]]
        self.closes = [float(v) for v in df["close"]]
        self.n = len(self.closes)

        self.atr = ATR(cfg.atr)
        self.swings = SwingDetector(cfg.swings, self.times)
        self.structure = StructureTracker(cfg.structure, self.times)
        self.liquidity = LiquidityEngine(cfg.liquidity, self.times)
        self.displacement = DisplacementDetector(cfg.displacement, self.times)
        self.fvg = FVGDetector(cfg.fvg, self.times, self.opens, self.highs, self.lows, self.closes)
        self.clock = SessionClock(cfg.sessions, self.times)

        self.session_names: list[tuple[str, ...]] = [() for _ in range(self.n)]
        self.i = -1  # last processed bar

    def step(self, i: int) -> None:
        assert i == self.i + 1, "bars must be processed strictly in order"
        o, h, l, c = self.opens[i], self.highs[i], self.lows[i], self.closes[i]
        atr_now = self.atr.update(h, l, c)

        closed, rolled = self.clock.on_bar(i, self.times[i], h, l)
        for ev in closed:
            self.liquidity.on_session_closed(ev)
        for ev in rolled:
            self.liquidity.on_period_rolled(ev)

        for s in self.swings.update(i, self.highs, self.lows):
            self.structure.on_swing(s)
            self.liquidity.on_swing(s, self.atr.values[i - 1] if i > 0 else atr_now)

        self.structure.update(i, c)
        self.liquidity.update(i, o, h, l, c, atr_now)
        self.fvg.update(i, atr_now)
        self.displacement.update(i, o, h, l, c, atr_now)

        self.clock.apply_bar(h, l)
        for name in self.clock.active:
            self.liquidity.apply_session_bar(name, h, l)

        self.session_names[i] = tuple(self.clock.active.keys())
        self.i = i

    def htf_snapshot(self, i: int) -> Optional[HTFSnapshot]:
        if self.htf is None:
            return None
        return self.htf.asof(self.times[i])

    def session_at(self, i: int) -> str | None:
        names = self.session_names[i]
        return names[0] if names else None
