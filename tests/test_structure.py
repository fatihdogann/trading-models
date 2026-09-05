from tme.config import StructureConfig, SwingConfig
from tme.core.structure import StructureTracker
from tme.core.swings import SwingDetector
from tme.types import Trend


def test_bos_then_mss_transition():
    highs = [1, 1, 1, 1, 6, 1, 1, 1, 1, 1]       # swing high 6 (pivot 4, confirm 6)
    lows = [2, 1, 0.5, 1, 2, 2, 2, 2, 2, 2]      # swing low 0.5 (pivot 2, confirm 4)
    closes = [1.5, 1, 1, 0.8, 0.3, 0.4, 2, 4, 7, 7]
    times = list(range(10))
    det = SwingDetector(SwingConfig(left=1, right=2), times)
    st = StructureTracker(StructureConfig(), times)
    events = []
    for i in range(10):
        for s in det.update(i, highs, lows):
            st.on_swing(s)
        events += st.update(i, closes[i])

    kinds = [(e.idx, e.kind, e.direction.value) for e in events]
    # bar 4: swing low 0.5 just confirmed and close 0.3 is below it -> initial
    # trend set to short, labeled BOS
    assert (4, "BOS", "short") in kinds
    # bar 8: close 7 breaks the confirmed swing high while short -> MSS long
    mss = [e for e in events if e.kind == "MSS"]
    assert len(mss) == 1
    assert (mss[0].idx, mss[0].direction.value) == (8, "long")
    # trend after the MSS is bullish; it was bearish between bars 4 and 7
    assert st.trend is Trend.BULL
    # the broken swing levels are consumed: rerunning bar 8 adds nothing
    assert st.update(9, closes[9]) == []
