"""Backtest engine: bar-by-bar replay, detection == live logic.

The engine drives MarketContext.step() in order and lets models react — the
exact same code path a live detector would use. No vectorized shortcuts that
could diverge from live behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from tme.config import EngineConfig
from tme.core.context import MarketContext
from tme.core.htf import HTFContext
from tme.models import (
    ICT2022Model,
    LiquiditySweepModel,
    PO3Model,
    SMTModel,
    TurtleSoupModel,
)
from tme.models.base import BaseModel, DetectionLog
from tme.outcome import evaluate
from tme.types import Direction, Outcome, SetupEvent


@dataclass(frozen=True)
class ModelRun:
    model: str                      # liquidity_sweep | po3 | ict2022 | turtle_soup | smt
    primary: tuple[str, str]
    secondary: Optional[tuple[str, str]] = None
    htf: Optional[tuple[str, str]] = None
    variant: str = "default"


def default_runs(cfg: EngineConfig, keys: list[tuple[str, str]]) -> list[ModelRun]:
    """One run per enabled model; ICT 2022 additionally once per ablation
    variant. HTF context is attached to the finest timeframe available."""
    ltf_keys = sorted({k for k in keys}, key=lambda k: _tf_minutes(k[1]))
    if not ltf_keys:
        return []
    ltf = ltf_keys[0]
    htf = ltf_keys[-1] if len(ltf_keys) > 1 else None
    runs: list[ModelRun] = []
    if cfg.run_sweep_model:
        runs.append(ModelRun("liquidity_sweep", ltf, htf=htf))
    if cfg.run_po3:
        runs.append(ModelRun("po3", ltf, htf=htf))
    if cfg.run_ict2022:
        variants = cfg.ict2022.variants if cfg.ict2022.run_variants else ("full",)
        for v in variants:
            runs.append(ModelRun("ict2022", ltf, htf=htf, variant=v))
    if cfg.run_turtle_soup:
        runs.append(ModelRun("turtle_soup", ltf, htf=htf))
    if cfg.run_smt and cfg.smt.primary and cfg.smt.secondary:
        smt_tf = cfg.smt.timeframe
        runs.append(ModelRun("smt", (cfg.smt.primary, smt_tf),
                             secondary=(cfg.smt.secondary, smt_tf), htf=None))
    return runs


_TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60,
               "2h": 120, "4h": 240, "1d": 1440}


def _tf_minutes(tf: str) -> int:
    return _TF_MINUTES.get(tf, 0)


class BacktestResult:
    def __init__(self, setups: list[SetupEvent], outcomes: list[Outcome],
                 log: DetectionLog, contexts: dict, models: list[BaseModel]):
        self.setups = setups
        self.outcomes = outcomes
        self.log = log
        self.contexts = contexts
        self.models = models

    @property
    def smt_events(self):
        for m in self.models:
            if isinstance(m, SMTModel):
                return m.events
        return []


class Engine:
    def __init__(self, cfg: EngineConfig, data: dict[tuple[str, str], pd.DataFrame],
                 runs: Optional[list[ModelRun]] = None):
        self.cfg = cfg
        self.data = data
        self.runs = runs if runs is not None else default_runs(cfg, list(data.keys()))
        self.contexts: dict[tuple[str, str], MarketContext] = {}
        self.htf_contexts: dict[tuple[str, str], HTFContext] = {}
        self.log = DetectionLog()

    # ------------------------------------------------------------------ #

    def run(self) -> BacktestResult:
        self._build_contexts()
        all_setups: list[SetupEvent] = []
        all_models: list[BaseModel] = []
        smt_events: list = []

        by_primary: dict[tuple[str, str], list[ModelRun]] = {}
        for run in self.runs:
            by_primary.setdefault(run.primary, []).append(run)

        for key, group in by_primary.items():
            ctx = self.contexts[key]
            models = [self._build_model(run, ctx, smt_events) for run in group]
            all_models.extend(models)
            aux = {m: self.contexts[r.secondary] for m, r in zip(models, group) if r.secondary}
            for i in range(ctx.n):
                ctx.step(i)
                for model, bctx in aux.items():
                    while bctx.i + 1 < bctx.n and bctx.times[bctx.i + 1] <= ctx.times[i]:
                        bctx.step(bctx.i + 1)
                for model in models:
                    for ev in model.on_bar(i):
                        all_setups.append(ev)

        # volatility-regime tags on triggered setups
        for ev in all_setups:
            if ev.state == "TRIGGERED":
                ctx = self.contexts[(ev.symbol, ev.timeframe)]
                ev.atr_regime = self._regime(ctx, ev.trigger_idx)

        outcomes = []
        for ev in all_setups:
            oc = evaluate(ev, self.contexts[(ev.symbol, ev.timeframe)], self.cfg.outcome)
            if oc is not None:
                outcomes.append(oc)

        return BacktestResult(all_setups, outcomes, self.log, self.contexts, all_models)

    # ------------------------------------------------------------------ #

    def _build_contexts(self) -> None:
        needed = set()
        for run in self.runs:
            needed.add(run.primary)
            if run.secondary:
                needed.add(run.secondary)
            if run.htf:
                needed.add(run.htf)
        htf_keys = {run.htf for run in self.runs if run.htf}
        for key in sorted(needed, key=lambda k: _tf_minutes(k[1]), reverse=True):
            df = self.data[key]
            if key in htf_keys:
                self.htf_contexts[key] = HTFContext(self.cfg.htf, self.cfg.atr, df,
                                                    symbol=key[0], timeframe=key[1])
                self.htf_contexts[key].run()
            else:
                htf = None
                # attach the coarsest htf context that is coarser than this key
                for hk in sorted(htf_keys, key=lambda k: _tf_minutes(k[1])):
                    if _tf_minutes(hk[1]) > _tf_minutes(key[1]):
                        htf = self.htf_contexts.get(hk)
                        break
                self.contexts[key] = MarketContext(key[0], key[1], df, self.cfg, htf=htf)

    def _build_model(self, run: ModelRun, ctx: MarketContext, smt_events: list) -> BaseModel:
        if run.model == "liquidity_sweep":
            return LiquiditySweepModel(ctx, self.cfg, self.cfg.sweep_model, run.variant, self.log)
        if run.model == "po3":
            return PO3Model(ctx, self.cfg, self.cfg.po3, run.variant, self.log)
        if run.model == "ict2022":
            return ICT2022Model(ctx, self.cfg, self.cfg.ict2022, run.variant, self.log,
                                smt_events=smt_events)
        if run.model == "turtle_soup":
            return TurtleSoupModel(ctx, self.cfg, self.cfg.turtle_soup, run.variant, self.log)
        if run.model == "smt":
            model = SMTModel(ctx, self.contexts[run.secondary], self.cfg, self.cfg.smt,
                             run.variant, self.log)
            model.events = smt_events  # share events with scoring consumers
            return model
        raise ValueError(f"unknown model: {run.model}")

    # ------------------------------------------------------------------ #

    def _regime(self, ctx: MarketContext, idx: int) -> str:
        w = self.cfg.outcome.atr_regime_window
        lo = max(0, idx - w)
        vals = [ctx.atr.values[j] / ctx.closes[j] for j in range(lo, idx)]
        cur = ctx.atr.values[idx] / ctx.closes[idx]
        if not vals:
            return "unknown"
        vals.sort()
        rank = sum(1 for v in vals if v <= cur) / len(vals)
        if rank <= self.cfg.outcome.atr_regime_q1:
            return "low_vol"
        if rank <= self.cfg.outcome.atr_regime_q2:
            return "mid_vol"
        return "high_vol"
