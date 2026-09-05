from tme.config import FVGConfig
from tme.core.fvg import FVGDetector
from tme.types import Direction, FVGStatus


def make_det(rows):
    times = list(range(len(rows)))
    opens = [r[0] for r in rows]
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]
    closes = [r[3] for r in rows]
    return FVGDetector(FVGConfig(min_size_atr=0.05), times, opens, highs, lows, closes)


def test_bullish_fvg_detection_and_full_fill():
    rows = [
        (100, 101, 99, 100),
        (100, 102, 100, 101.5),
        (101.5, 102.5, 101.2, 102),     # high[i-2]=101, next low must exceed
        (102, 102.6, 101.3, 102.4),     # low 101.3 > 101 -> bullish FVG at bar 2
        (102.4, 102.8, 100.5, 101),     # dips through: first touch + full fill
    ]
    det = make_det(rows)
    new = det.update(2, atr=1.0)
    assert len(new) == 1
    f = new[0]
    assert f.direction is Direction.LONG
    assert f.bottom == 101 and f.top == 101.2
    assert f.status is FVGStatus.ACTIVE
    det.update(3, atr=1.0)
    assert f.first_touch_idx is None    # untouched
    det.update(4, atr=1.0)
    assert f.first_touch_idx == 4
    assert f.status is FVGStatus.FULL_FILLED


def test_bearish_fvg_and_min_size_filter():
    rows = [
        (100, 100.5, 99, 99.5),
        (99.5, 100, 98, 98.5),
        (98.5, 98.8, 97.5, 98),        # low[i-2]=99 > high[i]=98.8 -> bearish FVG
    ]
    det = make_det(rows)
    new = det.update(2, atr=1.0)
    assert len(new) == 1
    assert new[0].direction is Direction.SHORT
    assert new[0].top == 99 and new[0].bottom == 98.8
    # size filter: gap 0.2 with min_size_atr=0.5 -> nothing
    det2_rows = rows[:2] + [(98.5, 98.95, 97.5, 98)]
    det2 = make_det(det2_rows)
    det2.cfg = FVGConfig(min_size_atr=0.5)
    assert det2.update(2, atr=1.0) == []
