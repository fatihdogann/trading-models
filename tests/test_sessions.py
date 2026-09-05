from datetime import datetime, timezone

from tme.config import SessionConfig
from tme.core.sessions import SessionClock


def make_clock():
    return SessionClock(SessionConfig(), times=[])


def test_ny_session_dst_spring_forward():
    # 2025-03-09 is the US DST switch: 09:30 NY = 13:30 UTC (EDT, was 14:30 EST)
    clock = make_clock()
    ts = datetime(2025, 3, 9, 13, 30, tzinfo=timezone.utc)
    assert clock.in_session("ny_am", clock.to_local(ts)) is True
    ts_est = datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc)  # winter offset
    assert clock.in_session("ny_am", clock.to_local(ts_est)) is True
    # 30 minutes earlier in UTC is in-session in winter but not in summer
    assert clock.in_session("ny_am", clock.to_local(
        datetime(2025, 1, 15, 14, 0, tzinfo=timezone.utc))) is False
    assert clock.in_session("ny_am", clock.to_local(
        datetime(2025, 3, 9, 13, 0, tzinfo=timezone.utc))) is False


def test_asia_overnight_wrap():
    clock = make_clock()
    # asia 20:00-00:00 NY: 20:30 NY winter = 01:30 UTC next day
    ts = datetime(2025, 1, 16, 1, 30, tzinfo=timezone.utc)
    assert clock.in_session("asia", clock.to_local(ts)) is True
    ts2 = datetime(2025, 1, 16, 5, 0, tzinfo=timezone.utc)  # 00:00 NY -> out
    assert clock.in_session("asia", clock.to_local(ts2)) is False


def test_session_close_and_day_rollover():
    cfg = SessionConfig()
    # two bars spanning NY midnight in UTC (winter: EST = UTC-5)
    t1 = datetime(2025, 1, 15, 20, 59, tzinfo=timezone.utc)  # 15:59 NY
    t2 = datetime(2025, 1, 16, 5, 1, tzinfo=timezone.utc)    # 00:01 NY next day
    clock = SessionClock(cfg, times=[t1, t2])
    closed, rolled = clock.on_bar(0, t1, 100.0, 99.0)
    assert closed == [] and rolled == []
    clock.apply_bar(100.0, 99.0)
    closed, rolled = clock.on_bar(1, t2, 101.0, 100.5)
    day_rolls = [r for r in rolled if r.kind == "day"]
    assert len(day_rolls) == 1
    assert day_rolls[0].prev_high == 100.0 and day_rolls[0].prev_low == 99.0


def test_session_instance_closes_when_bars_leave():
    cfg = SessionConfig()
    t_in = datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc)   # 09:30 NY
    t_out = datetime(2025, 1, 15, 17, 1, tzinfo=timezone.utc)   # 12:01 NY
    clock = SessionClock(cfg, times=[t_in, t_out])
    clock.on_bar(0, t_in, 100.0, 99.5)
    clock.apply_bar(100.0, 99.5)
    closed, _ = clock.on_bar(1, t_out, 100.2, 100.0)
    names = [c.name for c in closed]
    assert "ny_am" in names and "ny" not in names
    assert clock.closed["ny_am"].high == 100.0
