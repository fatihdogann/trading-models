"""Aggregation: setup/outcome tables, summary stats, ablation comparison."""

from __future__ import annotations

import pandas as pd

from tme.backtest import BacktestResult
from tme.export import setups_to_dataframe, outcomes_to_dataframe

SCORE_BUCKETS = (
    (0, 50, "0-49"),
    (50, 75, "50-74"),
    (75, 90, "75-89"),
    (90, 101, "90+"),
)


def joined_dataframe(result: BacktestResult) -> pd.DataFrame:
    s = setups_to_dataframe(result.setups)
    o = outcomes_to_dataframe(result.outcomes)
    if s.empty:
        return pd.DataFrame()
    df = s.merge(o, on="setup_id", how="left", suffixes=("", "_oc"))
    return df


def summarize(df: pd.DataFrame, by: str | list[str] | None = None) -> pd.DataFrame:
    """Win rate / avg R / median R / expectancy / MAE / MFE / timing."""
    if df.empty:
        return pd.DataFrame()
    d = df[df["state"] == "TRIGGERED"].copy()
    if d.empty:
        return pd.DataFrame()
    if by is not None:
        keys = [by] if isinstance(by, str) else list(by)
    else:
        keys = ["model", "variant"]

    def agg(g: pd.DataFrame) -> pd.Series:
        has_out = g["r"].notna()
        r = g.loc[has_out, "r"]
        return pd.Series({
            "n_setups": len(g),
            "n_triggered": int(has_out.sum()),
            "win_rate": (r > 0).mean() if len(r) else float("nan"),
            "avg_r": r.mean() if len(r) else float("nan"),
            "median_r": r.median() if len(r) else float("nan"),
            "expectancy": r.mean() if len(r) else float("nan"),
            "avg_mae_r": g["mae_r"].mean(),
            "avg_mfe_r": g["mfe_r"].mean(),
            "med_t_target": g["t_target_bars"].median(),
            "med_t_invalidation": g["t_invalidation_bars"].median(),
            "avg_score": g["score"].mean(),
        })

    out = d.groupby(keys, dropna=False).apply(agg, include_groups=False).reset_index()
    return out


def score_bucket(score: float) -> str:
    for lo, hi, label in SCORE_BUCKETS:
        if lo <= score < hi:
            return label
    return "unknown"


def bucket_tables(result: BacktestResult) -> dict[str, pd.DataFrame]:
    """Research slices: session / weekday / direction / liquidity type /
    HTF bias / score bucket / volatility regime."""
    df = joined_dataframe(result)
    if df.empty:
        return {}
    df = df[df["state"] == "TRIGGERED"].copy()
    if df.empty:
        return {}
    df["score_bucket"] = df["score"].map(score_bucket)
    tables = {}
    for key in ("session", "weekday", "direction", "liquidity_type",
                "htf_trend", "score_bucket", "atr_regime"):
        if key in df.columns:
            tables[key] = summarize(df, by=["model", key])
    return tables


def ablation_table(result: BacktestResult) -> pd.DataFrame:
    """The core research question: does sweep-only edge improve when
    displacement / MSS / FVG / full-sequence conditions are added?"""
    df = joined_dataframe(result)
    if df.empty:
        return pd.DataFrame()
    d = df[(df["model"] == "ict2022") & (df["state"] == "TRIGGERED")]
    if d.empty:
        return pd.DataFrame()
    return summarize(d, by="variant")
