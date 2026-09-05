from tme.config import DisplacementConfig
from tme.core.displacement import DisplacementDetector


def make_det(**kw):
    cfg = DisplacementConfig(min_body_atr=1.0, min_body_ratio=0.6,
                             strong_body_atr=2.0, min_pct_rank=85.0,
                             min_close_location=0.7, **kw)
    return DisplacementDetector(cfg, times=list(range(500)))


def test_strong_body_displaces_regardless_of_rank():
    det = make_det()
    # warm-up with small bars
    for i in range(20):
        det.update(i, 100, 100.4, 99.6, 100.1, atr=1.0)
    ev = det.update(21, 100, 103.2, 100.0, 103.0, atr=1.0)  # body 3.0 ATR, ratio 0.94
    assert ev is not None and ev.direction.value == "long"
    assert ev.body_atr == 3.0


def test_weak_body_no_displacement():
    det = make_det()
    ev = det.update(0, 100, 100.4, 99.6, 100.3, atr=1.0)  # body 0.3
    assert ev is None


def test_big_body_bad_close_location_rejected():
    det = make_det()
    for i in range(20):
        det.update(i, 100, 100.4, 99.6, 100.1, atr=1.0)
    # big body but closes mid-range (doji-like rejection both ends)
    ev = det.update(21, 100, 103.5, 99.5, 100.0, atr=1.0)  # body 0 -> no
    assert ev is None
    ev = det.update(22, 101, 103.0, 100.9, 101.2, atr=1.0)  # body 0.2 -> too small
    assert ev is None
