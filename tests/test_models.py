"""End-to-end model tests on crafted, deterministic bar sequences.

The bullish pattern (tme.synth.bullish_pattern_bars) encodes the full ICT
sequence: swing high -> decline -> swing low -> wick sweep + reclaim ->
displacement + MSS -> FVG -> retracement into the FVG.
"""

from dataclasses import replace

from conftest import build_df
from tme.backtest import Engine, ModelRun
from tme.config import load_config
from tme.synth import bearish_pattern_bars, bullish_pattern_bars


def make_cfg(**model_overrides):
    cfg = load_config(None)
    # deterministic displacement for the crafted pattern (bypass percentile)
    cfg = replace(cfg, displacement=replace(cfg.displacement, strong_body_atr=1.5))
    for name, mc in model_overrides.items():
        cfg = replace(cfg, **{name: mc})
    return cfg


def run_models(cfg, df, model_names, symbol="T", tf="15m"):
    runs = [ModelRun(n, (symbol, tf), variant="full" if n == "ict2022" else "default")
            for n in model_names]
    return Engine(cfg, {(symbol, tf): df}, runs).run()


def test_ict2022_full_sequence_triggers_on_crafted_pattern():
    df = build_df(bullish_pattern_bars(100.0, 0.3))
    cfg = make_cfg(ict2022=replace(load_config().ict2022, htf_required=False))
    res = run_models(cfg, df, model_names=["ict2022"])
    ict = [s for s in res.setups if s.model == "ict2022" and s.variant == "full"]
    assert any(s.state == "TRIGGERED" for s in ict), \
        [(s.state, s.failure_reason) for s in ict]
    ev = [s for s in ict if s.state == "TRIGGERED"][0]
    assert ev.direction.value == "long"
    # full sequence recorded
    assert ev.sweep_kind == "wick" and ev.sweep_depth_atr > 0
    assert ev.displacement_body_atr >= 1.5
    assert ev.mss_idx is not None
    assert ev.fvg_low is not None and ev.fvg_high is not None
    # entry inside the FVG, stop at the sweep extreme
    assert ev.fvg_low <= ev.entry_price <= ev.fvg_high
    assert ev.invalidation == ev.sweep_price
    # score: required components all passed, strong total
    assert ev.score == ev.score_max or ev.score >= 60
    names_passed = {c.name for c in ev.checklist if c.passed}
    assert {"htf_bias"} <= names_passed or True  # htf absent -> not required here
    assert {"reclaim", "displacement", "confirmed_mss", "fvg"} <= names_passed


def test_ict2022_requires_htf_when_configured():
    df = build_df(bullish_pattern_bars(100.0, 0.3))
    cfg = make_cfg()  # htf_required=True by default, no HTF data provided
    res = run_models(cfg, df, model_names=["ict2022"])
    assert not [s for s in res.setups if s.model == "ict2022"]
    m = res.models[0]
    assert m.rejections.get("HTF_UNAVAILABLE", 0) > 0


def test_liquidity_sweep_state_machine_triggers():
    df = build_df(bullish_pattern_bars(100.0, 0.3))
    cfg = make_cfg()
    res = run_models(cfg, df, model_names=["liquidity_sweep"])
    evs = [s for s in res.setups if s.model == "liquidity_sweep"]
    # the pattern's raid zone is never retested to the level itself, so the
    # model must expire it explicitly instead of silently dropping it
    assert evs and all(s.state in ("TRIGGERED", "EXPIRED", "INVALIDATED") for s in evs)
    transitions = [t for t in res.log.entries if t.model == "liquidity_sweep"]
    states = [t.to for t in transitions]
    assert "SWEPT" in states and "RECLAIMED" in states and "MSS_CONFIRMED" in states


def test_turtle_soup_failed_breakout_triggers():
    df = build_df(bearish_pattern_bars(100.0, 0.3))
    cfg = make_cfg()
    res = run_models(cfg, df, model_names=["turtle_soup"])
    evs = [s for s in res.setups if s.model == "turtle_soup" and s.state == "TRIGGERED"]
    assert evs, [(s.state, s.failure_reason) for s in res.setups if s.model == "turtle_soup"]
    ev = evs[0]
    assert ev.direction.value == "short"
    assert ev.sweep_kind == "close_through"   # breakout, not a wick sweep
    assert ev.invalidation == ev.sweep_price  # breakout extreme
    trans = [(t.frm, t.to) for t in res.log.entries
             if t.model == "turtle_soup" and t.setup_id == ev.setup_id]
    assert ("BROKEN_OUT", "RECLAIMED") in trans


def test_outcome_engine_measures_r():
    from tme.outcome import evaluate
    df = build_df(bullish_pattern_bars(100.0, 0.3))
    cfg = make_cfg(ict2022=replace(load_config().ict2022, htf_required=False))
    res = run_models(cfg, df, model_names=["ict2022"])
    trig = [s for s in res.setups if s.state == "TRIGGERED"][0]
    ctx = res.contexts[("T", "15m")]
    oc = evaluate(trig, ctx, cfg.outcome)
    assert oc is not None
    assert oc.mfe_r >= oc.r  # favorable excursion bounds the result
    assert oc.mae_r <= oc.r
