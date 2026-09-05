from tme.config import SwingConfig
from tme.core.atr import ATR
from tme.core.swings import SwingDetector
from tme.types import Side


def test_pivot_confirms_after_right_bars():
    highs = [1, 2, 5, 2, 1, 1, 1, 1, 1, 1]
    lows = [0.5] * 10
    times = list(range(10))
    det = SwingDetector(SwingConfig(left=1, right=2), times)
    found = []
    for i in range(10):
        found += det.update(i, highs, lows)
    assert len(det.swings) == 1
    s = det.swings[0]
    assert s.side is Side.HIGH and s.price == 5
    assert s.pivot_idx == 2
    assert s.confirm_idx == 4  # pivot + right
    # the swing is invisible before its confirmation bar
    det2 = SwingDetector(SwingConfig(left=1, right=2), times)
    for i in range(4):
        det2.update(i, highs, lows)
    assert len(det2.swings) == 0


def test_labels_hh_hl_lh_ll():
    highs = [1, 1, 5, 1, 1, 1, 7, 1, 1, 1, 1]   # 5 then 7 -> HH
    lows = [3, 2, 3, 1, 2, 2, 2, 2, 2, 2, 2]    # 1 then ... -> LL
    times = list(range(11))
    det = SwingDetector(SwingConfig(left=1, right=2), times)
    for i in range(11):
        det.update(i, highs, lows)
    highs_sw = [s for s in det.swings if s.side is Side.HIGH]
    assert [s.label for s in highs_sw] == [None, "HH"]


def test_atr_expanding_warmup():
    from tme.config import ATRConfig
    atr = ATR(ATRConfig(period=3))
    a1 = atr.update(10, 8, 9)    # tr=2
    a2 = atr.update(11, 9, 10)   # tr=2
    a3 = atr.update(14, 10, 12)  # tr = max(4, |14-10|, |10-10|) = 4
    assert a1 == 2 and a2 == 2 and a3 == (2 + 2 + 4) / 3
    a4 = atr.update(13, 11, 11)  # tr = max(2, |13-12|, |11-12|) = 2 -> wilder
    assert abs(a4 - ((8 / 3) * 2 + 2) / 3) < 1e-9
