"""Timezone/DST-safe session clock.

Sessions are defined by HH:MM ranges in a configurable IANA timezone (default
America/New_York). Every bar timestamp is converted to that zone, so DST
transitions are handled per-date — no fixed UTC offsets anywhere.

Also tracks local-day and ISO-week aggregates (for PDH/PDL, PWH/PWL pools).
Aggregates only use bars up to and including the current one; the previous
period's extremes are emitted exactly once, at the first bar of the new
period.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from tme.config import SessionConfig
from tme.types import PeriodRolled, SessionClosed


@dataclass
class _Instance:
    name: str
    start_idx: int
    start_time: Any = None
    high: float = float("-inf")
    low: float = float("inf")


class SessionClock:
    def __init__(self, cfg: SessionConfig, times):
        self.cfg = cfg
        self.times = times
        self.tz = ZoneInfo(cfg.timezone)
        self._ranges: dict[str, tuple[time, time]] = {
            name: (self._parse(s), self._parse(e)) for name, (s, e) in cfg.sessions.items()
        }
        self.active: dict[str, _Instance] = {}
        self.closed: dict[str, SessionClosed] = {}
        self._local_date: Any = None
        self._iso_week: tuple[int, int] | None = None
        self._day_high = float("-inf")
        self._day_low = float("inf")
        self._week_high = float("-inf")
        self._week_low = float("inf")

    @staticmethod
    def _parse(hhmm: str) -> time:
        h, m = hhmm.split(":")
        return time(int(h), int(m))

    def to_local(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(self.tz)

    def in_session(self, name: str, local_dt: datetime) -> bool:
        start, end = self._ranges[name]
        m = local_dt.time()
        if start <= end:
            return start <= m < end
        return m >= start or m < end  # overnight wrap (e.g. asia 20:00-00:00)

    def sessions_at(self, ts: datetime) -> list[str]:
        local = self.to_local(ts)
        return [name for name in self._ranges if self.in_session(name, local)]

    def on_bar(self, idx: int, ts: datetime, high: float, low: float) -> tuple[list[SessionClosed], list[PeriodRolled]]:
        """Advance clock with bar idx. Extremes of the just-closed session/day/
        week reflect bars strictly before this one; the caller may then run
        sweep checks and finally call apply_bar()."""
        local = self.to_local(ts)
        closed: list[SessionClosed] = []
        rolled: list[PeriodRolled] = []

        for name in self._ranges:
            inside = self.in_session(name, local)
            inst = self.active.get(name)
            if inside and inst is None:
                self.active[name] = _Instance(name=name, start_idx=idx, start_time=ts)
            elif not inside and inst is not None:
                end_idx = max(idx - 1, inst.start_idx)
                ev = SessionClosed(
                    name=name,
                    start_idx=inst.start_idx,
                    end_idx=end_idx,
                    start_time=inst.start_time,
                    end_time=self.times[end_idx],
                    high=inst.high,
                    low=inst.low,
                )
                del self.active[name]
                self.closed[name] = ev
                closed.append(ev)

        if self._local_date is not None and local.date() != self._local_date:
            rolled.append(PeriodRolled("day", idx, ts, self._day_high, self._day_low, str(self._local_date)))
            self._day_high = float("-inf")
            self._day_low = float("inf")
        if self._iso_week is not None and local.date().isocalendar()[:2] != self._iso_week:
            rolled.append(PeriodRolled("week", idx, ts, self._week_high, self._week_low, f"ISO{self._iso_week}"))
            self._week_high = float("-inf")
            self._week_low = float("inf")

        self._local_date = local.date()
        self._iso_week = local.date().isocalendar()[:2]
        return closed, rolled

    def apply_bar(self, high: float, low: float) -> None:
        """Update session/day/week running extremes with the current bar.
        Call AFTER sweep checks so a bar is never swept by its own extreme."""
        for inst in self.active.values():
            inst.high = max(inst.high, high)
            inst.low = min(inst.low, low)
        self._day_high = max(self._day_high, high)
        self._day_low = min(self._day_low, low)
        self._week_high = max(self._week_high, high)
        self._week_low = min(self._week_low, low)
