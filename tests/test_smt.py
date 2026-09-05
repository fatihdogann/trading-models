from dataclasses import replace

from conftest import build_df
from tme.backtest import Engine, ModelRun
from tme.config import SwingConfig, load_config


A_ROWS = [  # lower lows
    (100.4, 100.8, 100.0, 100.2),
    (100.2, 100.4, 99.0, 99.2),
    (99.2, 99.4, 98.0, 98.2),    # swing low 98.0 (confirm 4)
    (98.2, 98.8, 98.2, 98.6),
    (98.6, 99.2, 98.4, 99.0),
    (99.0, 99.4, 98.6, 98.8),
    (98.8, 99.0, 97.2, 97.4),
    (97.4, 97.6, 96.2, 96.4),    # swing low 96.2 (confirm 9) -> LL
    (96.4, 96.8, 96.4, 96.6),
    (96.6, 97.0, 96.5, 96.8),
]

B_ROWS = [  # corresponding swing low is a HIGHER low
    (50.2, 50.6, 50.0, 50.4),
    (50.4, 50.8, 50.3, 50.6),
    (50.6, 51.0, 49.9, 50.8),    # swing low 49.9 (confirm 4)
    (50.8, 51.2, 50.5, 51.0),
    (51.0, 51.4, 50.8, 51.2),
    (51.2, 51.6, 50.9, 51.4),
    (51.4, 51.8, 51.4, 51.6),
    (51.6, 52.0, 51.3, 51.8),    # swing low 51.3 (confirm 9) -> HL
    (51.8, 52.2, 51.5, 52.0),
    (52.0, 52.4, 51.9, 52.2),
]


def make_cfg():
    cfg = load_config(None)
    return replace(cfg, swings=SwingConfig(left=1, right=2),
                   smt=replace(cfg.smt, primary="A", secondary="B",
                               timeframe="15m", min_divergence_atr=0.15,
                               correlation_window=10, max_swing_time_diff="1h"))


def test_smt_bullish_divergence_detected():
    cfg = make_cfg()
    data = {("A", "15m"): build_df(A_ROWS), ("B", "15m"): build_df(B_ROWS)}
    runs = [ModelRun("smt", ("A", "15m"), secondary=("B", "15m"))]
    res = Engine(cfg, data, runs).run()
    assert len(res.smt_events) == 1, res.smt_events
    div = res.smt_events[0]
    assert div.direction.value == "long"
    assert div.a_change < 0 and div.b_change > 0
    assert div.magnitude_atr > 0.15
    ev = [s for s in res.setups if s.model == "smt"][0]
    assert ev.state == "TRIGGERED" and ev.direction.value == "long"
    assert ev.score is not None and ev.checklist


def test_smt_no_divergence_when_assets_identical():
    cfg = make_cfg()
    data = {("A", "15m"): build_df(A_ROWS), ("B", "15m"): build_df(A_ROWS)}
    runs = [ModelRun("smt", ("A", "15m"), secondary=("B", "15m"))]
    res = Engine(cfg, data, runs).run()
    assert res.smt_events == []
