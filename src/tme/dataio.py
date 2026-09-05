"""CSV loading and validation for OHLCV data.

Convention: data is tz-aware UTC (naive timestamps are assumed UTC). All
session/DST handling happens downstream via the session clock.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED = ("open", "high", "low", "close")


def validate_ohlc(df: pd.DataFrame, name: str = "data") -> None:
    """Fail fast on corrupted bars: high must dominate open/close/low."""
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing OHLC columns {missing} in {name}")
    bad_high = df["high"] < df[["open", "close", "low"]].max(axis=1)
    bad_low = df["low"] > df[["open", "close", "high"]].min(axis=1)
    if bad_high.any() or bad_low.any():
        first = df.index[(bad_high | bad_low).argmax()]
        n = int((bad_high | bad_low).sum())
        raise ValueError(
            f"invalid OHLC rows (high<low violations) in {name}: "
            f"{n} bad bar(s), first at index {first}"
        )


def load_csv(path: str | Path, time_col: str | None = None, tz: str = "UTC") -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if time_col is None:
        for cand in ("time", "timestamp", "datetime", "date", "ts"):
            if cand in df.columns:
                time_col = cand
                break
    if time_col is None:
        raise ValueError(f"no time column found in {path}; pass time_col=")
    ts = pd.to_datetime(df[time_col], utc=False)
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(tz)
    df = df.set_index(pd.DatetimeIndex(ts).tz_convert("UTC"))
    df = df.drop(columns=[time_col])
    df = df[[c for c in (*REQUIRED, "volume") if c in df.columns]].astype(float)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    validate_ohlc(df, name=str(path))
    return df


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample to a higher timeframe (rule like '1h', '4h')."""
    out = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        **({"volume": "sum"} if "volume" in df.columns else {}),
    }).dropna(subset=["open", "high", "low", "close"])
    return out
