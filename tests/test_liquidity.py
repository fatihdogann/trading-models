from tme.config import LiquidityConfig
from tme.core.liquidity import LiquidityEngine
from tme.types import LiquidityType, Side, Swing


def make_engine(cfg=None):
    cfg = cfg or LiquidityConfig(max_reclaim_bars=3, breakout_confirm_bars=2)
    return LiquidityEngine(cfg, times=list(range(100)))


def test_wick_sweep_reclaims_immediately():
    eng = make_engine()
    swing = Swing("s1", Side.LOW, 100.0, 2, 2, 5, 5)
    eng.on_swing(swing, atr=1.0)
    evs = eng.update(6, 100, 100.5, 98.5, 100.2, atr=1.0)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.kind.value == "wick"
    assert ev.reclaimed and ev.reclaim_idx == 6
    assert ev.depth == 1.5 and ev.depth_atr == 1.5
    assert ev.bars_held == 1
    assert not ev.confirmed_breakout


def test_close_through_then_reclaim_then_dead():
    eng = make_engine()
    eng.on_swing(Swing("s1", Side.LOW, 100.0, 2, 2, 5, 5), atr=1.0)
    # bar 6 closes below the level: close-through, lifecycle pending
    evs = eng.update(6, 100, 100.5, 98.5, 99.5, atr=1.0)
    assert len(evs) == 1 and evs[0].kind.value == "close_through"
    assert not evs[0].reclaimed
    # bar 7 still below: pending, no new event
    assert eng.update(7, 100, 100.2, 98.0, 98.5, atr=1.0) == []
    # bar 8 closes back above: reclaimed
    evs = eng.update(8, 100, 100.3, 99.0, 100.4, atr=1.0)
    assert len(evs) == 1 and evs[0].reclaimed and evs[0].reclaim_idx == 8
    # pool consumed: no further sweeps of the same level
    assert eng.update(9, 100, 100.2, 97.0, 98.0, atr=1.0) == []


def test_close_through_confirmed_breakout_when_not_reclaimed():
    eng = make_engine()
    eng.on_swing(Swing("s1", Side.LOW, 100.0, 2, 2, 5, 5), atr=1.0)
    eng.update(6, 100, 100.5, 98.5, 99.5, atr=1.0)   # close-through
    eng.update(7, 100, 100.2, 98.0, 98.5, atr=1.0)   # 1 bar below
    evs = eng.update(8, 100, 100.2, 98.0, 98.5, atr=1.0)  # 2 bars: confirm
    assert len(evs) == 1 and evs[0].confirmed_breakout
    assert not evs[0].reclaimed


def test_equal_highs_grouped():
    eng = make_engine()
    eng.on_swing(Swing("s1", Side.HIGH, 105.0, 2, 2, 5, 5), atr=1.0)
    eng.on_swing(Swing("s2", Side.HIGH, 105.05, 8, 8, 11, 11), atr=1.0)
    kinds = [p.kind for p in eng.pools]
    assert LiquidityType.EQH in kinds
    eq = [p for p in eng.pools if p.kind is LiquidityType.EQH][0]
    assert eq.price == 105.05  # the higher of the cluster


def test_pd_pools_created_on_rollover():
    from tme.types import PeriodRolled
    eng = make_engine()
    eng.on_period_rolled(PeriodRolled("day", 10, 10, 110.0, 95.0, "2025-01-06"))
    kinds = {p.kind for p in eng.pools}
    assert LiquidityType.PDH in kinds and LiquidityType.PDL in kinds
    pdh = [p for p in eng.pools if p.kind is LiquidityType.PDH][0]
    assert pdh.price == 110.0 and pdh.side is Side.HIGH
