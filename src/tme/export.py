"""Exports: DataFrame / JSONL / TradingView-friendly CSV.

The SetupEvent object is the single source of truth; these helpers are thin
projections for table display, alerting, journaling and ML datasets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from tme.types import Outcome, SetupEvent

_SCALAR_FIELDS = [
    "model", "variant", "direction", "symbol", "timeframe", "setup_id", "state",
    "start_time", "trigger_time",
    "liquidity_type", "liquidity_price",
    "sweep_time", "sweep_price", "sweep_kind", "sweep_depth_atr",
    "displacement_idx", "displacement_body_atr", "displacement_pct_rank",
    "mss_idx", "mss_time", "mss_price",
    "fvg_low", "fvg_high", "fvg_mid",
    "entry_zone_low", "entry_zone_high", "entry_price", "invalidation",
    "target_type", "target_price",
    "score", "score_max", "failure_reason",
    "session", "weekday", "htf_trend", "atr_regime",
]


def setups_to_dataframe(setups: list[SetupEvent]) -> pd.DataFrame:
    rows = []
    for ev in setups:
        row: dict[str, Any] = {}
        for f in _SCALAR_FIELDS:
            v = getattr(ev, f)
            if hasattr(v, "value"):
                v = v.value
            row[f] = v
        row["checklist"] = " | ".join(
            f"{'+' if c.passed else '-'}{c.name}" + (f"({c.detail})" if c.detail else "")
            for c in ev.checklist
        )
        for k, v in ev.meta.items():
            row[f"meta_{k}"] = v
        rows.append(row)
    return pd.DataFrame(rows)


def outcomes_to_dataframe(outcomes: list[Outcome]) -> pd.DataFrame:
    if not outcomes:
        return pd.DataFrame(columns=["setup_id", "r"])
    rows = [o.__dict__ for o in outcomes]
    df = pd.DataFrame(rows)
    for col in ("direction",):
        if col in df.columns:
            df[col] = df[col].astype(str)
    return df


def save_jsonl(setups: list[SetupEvent], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in setups:
            f.write(json.dumps(ev.to_dict(), default=str) + "\n")


def save_tradingview_csv(setups: list[SetupEvent], path: str | Path) -> None:
    """Rows a TradingView table/alert integration can consume directly."""
    rows = []
    for ev in setups:
        if ev.state != "TRIGGERED":
            continue
        rows.append({
            "time": ev.trigger_time,
            "symbol": ev.symbol,
            "timeframe": ev.timeframe,
            "model": ev.model,
            "variant": ev.variant,
            "direction": str(ev.direction),
            "score": ev.score,
            "entry": ev.entry_price,
            "stop": ev.invalidation,
            "target": ev.target_price,
            "liquidity_type": ev.liquidity_type,
        })
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
