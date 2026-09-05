"""Repaint / lookahead audit.

The strongest guarantee we can give: running the engine on a prefix of the
data must produce EXACTLY the same events (same bars, same states, same
scores) as running it on the full series — i.e. no future bar ever changes
the past. Plus structural assertions (swing confirmation offsets, monotone
lifecycle timestamps).
"""

from collections import namedtuple

from conftest import build_df
from tme.backtest import Engine, ModelRun
from tme.config import load_config
from tme.synth import generate

RUNS = [
    ModelRun("liquidity_sweep", ("D", "15m")),
    ModelRun("po3", ("D", "15m")),
    ModelRun("ict2022", ("D", "15m"), variant="full"),
    ModelRun("turtle_soup", ("D", "15m")),
]


def _run(df, k=None):
    data = {("D", "15m"): df if k is None else df.iloc[:k]}
    return Engine(load_config(None), data, RUNS).run()


def _log_signature(res, max_idx):
    return [
        (t.model, t.setup_id, t.frm, t.to, t.idx, t.note)
        for t in res.log.entries
        if t.idx < max_idx
    ]


def _setup_signature(res, max_idx):
    out = []
    for s in res.setups:
        if s.trigger_idx is not None and s.trigger_idx >= max_idx:
            continue
        if s.start_idx >= max_idx:
            continue
        d = s.to_dict()
        d.pop("meta", None)
        out.append(d)
    out.sort(key=lambda d: (d["model"], d["setup_id"]))
    return out


def test_prefix_run_reproduces_full_run_exactly():
    df = generate(n_days=6, tf="15m", seed=3, inject=True)
    k = int(len(df) * 0.6)
    full = _run(df)
    prefix = _run(df, k=k)
    assert _log_signature(full, k) == _log_signature(prefix, k)
    assert _setup_signature(full, k) == _setup_signature(prefix, k)
    # sanity: the audit itself must be non-trivial
    assert len(_log_signature(full, k)) > 50
    assert any(s["state"] == "TRIGGERED" for s in _setup_signature(full, k))


def test_swing_confirmation_offset_is_exact():
    from tme.config import SwingConfig
    from tme.core.swings import SwingDetector
    cfg = SwingConfig(left=3, right=3)
    times = list(range(200))
    det = SwingDetector(cfg, times)
    highs = [100 + (i * 7 % 13) for i in range(200)]
    lows = [90 - (i * 5 % 11) for i in range(200)]
    for i in range(200):
        det.update(i, highs, lows)
    assert det.swings
    for s in det.swings:
        assert s.confirm_idx == s.pivot_idx + cfg.right


def test_lifecycle_timestamps_are_monotone():
    df = generate(n_days=6, tf="15m", seed=3, inject=True)
    res = _run(df)
    for s in res.contexts[("D", "15m")].liquidity.sweeps:
        assert s.sweep_idx >= s.formed_idx
        if s.reclaimed:
            assert s.reclaim_idx >= s.sweep_idx
        if s.confirmed_breakout:
            assert s.breakout_idx > s.sweep_idx
    for f in res.contexts[("D", "15m")].fvg.fvgs:
        if f.first_touch_idx is not None:
            assert f.first_touch_idx >= f.formed_idx
        if f.filled_idx is not None:
            assert f.filled_idx >= f.formed_idx


def test_pdh_pool_only_exists_after_first_full_day():
    df = generate(n_days=6, tf="15m", seed=3, inject=True)
    res = _run(df)
    pools = res.contexts[("D", "15m")].liquidity.pools
    pdh = [p for p in pools if p.kind.value == "pdh"]
    assert pdh
    # data starts Mon 00:00 UTC = Sun 19:00 NY; the first local day completes
    # at Mon 00:00 NY = 05:00 UTC = bar 20, so no PDH pool can exist before
    assert min(p.formed_idx for p in pdh) >= 19
