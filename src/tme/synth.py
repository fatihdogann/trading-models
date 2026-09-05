"""Synthetic OHLC generation for research validation.

Base series: seeded random walk with mild session volatility profile. On top
of it we inject scripted micro-sequences (sweep -> reclaim -> displacement ->
FVG -> retrace) so the demo pipeline has real setups to find. The SAME
pattern bars are used in unit tests for deterministic assertions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PATTERN_LEN = 17


def bullish_pattern_bars(p: float, u: float) -> list[tuple[float, float, float, float]]:
    """A clean bullish raid sequence around price p with unit u (~ATR scale).

    b3 swing high (confirmed b6) -> decline -> b7 swing low (confirmed b10)
    -> b11 wick sweep + reclaim -> b12 displacement + MSS/BOS
    -> b14 FVG -> b15 retrace into FVG (entry).
    """
    return [
        (p - 1.2 * u, p - 0.6 * u, p - 1.8 * u, p - 1.4 * u),   # b0
        (p - 1.4 * u, p - 0.8 * u, p - 2.0 * u, p - 1.8 * u),   # b1
        (p - 1.8 * u, p - 1.0 * u, p - 2.2 * u, p - 1.2 * u),   # b2
        (p - 1.2 * u, p - 0.5 * u, p - 1.6 * u, p - 0.8 * u),   # b3 swing high
        (p - 1.0 * u, p - 1.0 * u, p - 2.0 * u, p - 1.8 * u),   # b4
        (p - 1.8 * u, p - 1.2 * u, p - 3.0 * u, p - 2.8 * u),   # b5
        (p - 2.8 * u, p - 1.4 * u, p - 3.6 * u, p - 3.2 * u),   # b6 (high confirmed)
        (p - 3.2 * u, p - 2.6 * u, p - 4.5 * u, p - 4.0 * u),   # b7 swing low
        (p - 4.0 * u, p - 3.0 * u, p - 4.3 * u, p - 3.4 * u),   # b8
        (p - 3.4 * u, p - 2.8 * u, p - 4.2 * u, p - 3.6 * u),   # b9
        (p - 3.6 * u, p - 3.0 * u, p - 4.1 * u, p - 3.3 * u),   # b10 (low confirmed)
        (p - 3.3 * u, p - 3.1 * u, p - 6.0 * u, p - 3.2 * u),   # b11 SWEEP (wick) + reclaim
        (p - 3.2 * u, p + 0.5 * u, p - 3.4 * u, p + 0.2 * u),   # b12 displacement + MSS
        (p + 0.3 * u, p + 1.0 * u, p + 0.25 * u, p + 0.8 * u),  # b13 FVG forms (top p+0.25u)
        (p + 1.0 * u, p + 1.6 * u, p + 0.45 * u, p + 1.4 * u),  # b14 continuation
        (p + 1.4 * u, p + 1.5 * u, p + 0.1 * u, p + 0.5 * u),   # b15 retrace into FVG -> entry
        (p + 0.5 * u, p + 3.0 * u, p + 0.4 * u, p + 2.8 * u),   # b16 expansion
    ]


def bearish_pattern_bars(p: float, u: float) -> list[tuple[float, float, float, float]]:
    """Mirror of the bullish sequence, but the raid CLOSES through the high
    (close-through breakout) and is reclaimed back below -> failed breakout
    (turtle soup) plus a full bearish continuation sequence."""
    bars = []
    for o, h, l, c in bullish_pattern_bars(p, u):
        bars.append((2 * p - o, 2 * p - l, 2 * p - h, 2 * p - c))
    o, h, l, c = bars[11]
    # force close-through of the mirrored high level (p + 4.5u)
    bars[11] = (o, h, l, p + 5.0 * u)
    return bars


def bars_to_df(rows, start="2025-01-06 00:00", tf="15min") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(rows), freq=tf, tz="UTC")
    return pd.DataFrame(
        [dict(open=o, high=h, low=l, close=c) for o, h, l, c in rows], index=idx
    )


def generate(n_days: int = 20, tf: str = "15m", seed: int = 7,
             start_price: float = 4500.0, inject: bool = True) -> pd.DataFrame:
    step = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}[tf]
    freq = f"{step}min"
    n = int(n_days * 1440 / step)
    idx = pd.date_range(start="2025-01-06 00:00", periods=n, freq=freq, tz="UTC")
    rng = np.random.default_rng(seed)
    u = start_price * 0.0012  # unit ~ target ATR scale

    # mild session-dependence: bigger bars during london/ny hours (UTC)
    hours = idx.hour
    vol = np.where((hours >= 7) & (hours <= 20), 1.0, 0.55)
    rets = rng.normal(0, 0.35 * u, n) * vol
    close = start_price + np.cumsum(rets)
    open_ = np.empty(n)
    open_[0] = start_price
    open_[1:] = close[:-1]
    wick = np.abs(rng.normal(0, 0.22 * u, n)) * vol
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick

    o, h, l, c = open_, high, low, close

    if inject:
        # every ~2 days, alternate bullish raid and bearish failed breakout
        spacing = max(PATTERN_LEN + 8, int(1.8 * 1440 / step))
        j = int(1.2 * 1440 / step)
        flip = False
        while j + PATTERN_LEN < n - 60:
            p = float(c[j - 1])
            bars = bearish_pattern_bars(p, u) if flip else bullish_pattern_bars(p, u)
            for k, (bo, bh, bl, bc) in enumerate(bars):
                o[j + k], h[j + k], l[j + k], c[j + k] = bo, bh, bl, bc
            # bridge the boundary: open of the next walk bar continues from pattern close
            if j + PATTERN_LEN < n:
                o[j + PATTERN_LEN] = c[j + PATTERN_LEN - 1]
                h[j + PATTERN_LEN] = max(o[j + PATTERN_LEN], h[j + PATTERN_LEN])
                l[j + PATTERN_LEN] = min(o[j + PATTERN_LEN], l[j + PATTERN_LEN])
            j += spacing
            flip = not flip

    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)
    df["volume"] = np.abs(rng.normal(1e3, 2e2, n))
    return df
