"""Liquidity pools and sweep lifecycles.

Pool sources: confirmed swings, equal highs/lows clusters, previous day/week
high/low, previous session high/low and (optionally) live session extremes.

A sweep is not "a wick beyond a level": every raid becomes a lifecycle
    ACTIVE -> SWEPT (wick | close_through) -> RECLAIMED | CONFIRMED_BREAKOUT
and models read the lifecycle state as it evolves bar by bar.
"""

from __future__ import annotations

from tme.config import LiquidityConfig
from tme.types import (
    Direction,
    LiquidityPool,
    LiquidityType,
    PeriodRolled,
    SessionClosed,
    Side,
    SweepEvent,
    SweepKind,
    Swing,
)


class LiquidityEngine:
    def __init__(self, cfg: LiquidityConfig, times):
        self.cfg = cfg
        self.times = times
        self.pools: list[LiquidityPool] = []
        self.sweeps: list[SweepEvent] = []
        self._seq = 0
        self._open_sweeps: dict[str, SweepEvent] = {}  # pool_id -> lifecycle
        self._session_pool_ids: dict[str, str] = {}    # session name -> live pool id

    # ------------------------------------------------------------------ #
    # pool creation
    # ------------------------------------------------------------------ #

    def _add_pool(
        self,
        kind: LiquidityType,
        price: float,
        idx: int,
        group_id: str | None = None,
        dynamic: bool = False,
    ) -> LiquidityPool:
        self._seq += 1
        pool = LiquidityPool(
            id=f"pl{self._seq}",
            kind=kind,
            side=kind.side,
            price=price,
            formed_idx=idx,
            formed_time=self.times[idx],
            group_id=group_id,
            dynamic=dynamic,
        )
        self.pools.append(pool)
        return pool

    def on_swing(self, swing: Swing, atr: float) -> None:
        kind = LiquidityType.SWING_HIGH if swing.side is Side.HIGH else LiquidityType.SWING_LOW
        pool = self._add_pool(kind, swing.price, swing.confirm_idx)
        self._maybe_group_equal(pool, atr)

    def _maybe_group_equal(self, pool: LiquidityPool, atr: float) -> None:
        tol = self.cfg.equal_tol_atr * atr if atr > 0 else 0.0
        for other in self.pools:
            if other is pool or not other.active or other.dynamic:
                continue
            if other.side is not pool.side:
                continue
            if other.kind not in (LiquidityType.SWING_HIGH, LiquidityType.SWING_LOW,
                                  LiquidityType.EQH, LiquidityType.EQL):
                continue
            if abs(other.price - pool.price) <= tol and other.formed_idx < pool.formed_idx:
                eq_kind = LiquidityType.EQH if pool.side is Side.HIGH else LiquidityType.EQL
                group = other.group_id or other.id
                other.group_id = group
                level = max(pool.price, other.price) if pool.side is Side.HIGH else min(pool.price, other.price)
                eq_pool = self._add_pool(eq_kind, level, pool.formed_idx, group_id=group)
                return

    def on_period_rolled(self, ev: PeriodRolled) -> None:
        if not self.cfg.enable_pd and ev.kind == "day":
            return
        if not self.cfg.enable_pw and ev.kind == "week":
            return
        hi, lo = (LiquidityType.PDH, LiquidityType.PDL) if ev.kind == "day" \
            else (LiquidityType.PWH, LiquidityType.PWL)
        self._add_pool(hi, ev.prev_high, ev.idx)
        self._add_pool(lo, ev.prev_low, ev.idx)

    def on_session_closed(self, ev: SessionClosed) -> None:
        if not self.cfg.enable_prev_session:
            return
        # label relative to the following session instance
        self._add_pool(LiquidityType.PREV_SESSION_HIGH, ev.high, ev.end_idx + 1 if ev.end_idx + 1 < len(self.times) else ev.end_idx)
        self._add_pool(LiquidityType.PREV_SESSION_LOW, ev.low, ev.end_idx + 1 if ev.end_idx + 1 < len(self.times) else ev.end_idx)

    # ------------------------------------------------------------------ #
    # live session extremes
    # ------------------------------------------------------------------ #

    def open_session_instance(self, name: str, idx: int) -> None:
        if not self.cfg.enable_session_extremes:
            return
        for side, kind in ((Side.HIGH, LiquidityType.SESSION_HIGH), (Side.LOW, LiquidityType.SESSION_LOW)):
            pool = self._add_pool(kind, float("inf") if side is Side.HIGH else float("-inf"), idx, dynamic=True)
            self._session_pool_ids[f"{name}:{side.value}"] = pool.id

    def apply_session_bar(self, name: str, high: float, low: float) -> None:
        if not self.cfg.enable_session_extremes:
            return
        for side in (Side.HIGH, Side.LOW):
            pid = self._session_pool_ids.get(f"{name}:{side.value}")
            if pid is None:
                continue
            pool = self._by_id(pid)
            if pool is None or not pool.active:
                continue
            pool.price = max(pool.price, high) if side is Side.HIGH else min(pool.price, low)

    def _by_id(self, pool_id: str) -> LiquidityPool | None:
        for p in self.pools:
            if p.id == pool_id:
                return p
        return None

    # ------------------------------------------------------------------ #
    # sweep lifecycle
    # ------------------------------------------------------------------ #

    def update(self, i: int, o: float, h: float, l: float, c: float, atr: float) -> list[SweepEvent]:
        """Advance all sweep lifecycles with bar i. Returns lifecycles that
        changed state this bar (new sweeps, reclaims, confirmed breakouts)."""
        touched: list[SweepEvent] = []
        for pool in self.pools:
            sweep = self._open_sweeps.get(pool.id)
            if sweep is None:
                if not pool.active:
                    continue
                ev = self._check_sweep(pool, i, h, l, c, atr)
                if ev is not None:
                    touched.append(ev)
            elif sweep.kind is SweepKind.CLOSE_THROUGH and not sweep.reclaimed \
                    and not sweep.confirmed_breakout:
                if self._check_reclaim_or_breakout(pool, sweep, i, c):
                    touched.append(sweep)
        return touched

    def _check_sweep(self, pool: LiquidityPool, i: int, h: float, l: float, c: float, atr: float) -> SweepEvent | None:
        if pool.dynamic and pool.price in (float("inf"), float("-inf")):
            return None  # nothing tracked yet in this session instance
        broke_low = pool.side is Side.LOW and l < pool.price
        broke_high = pool.side is Side.HIGH and h > pool.price
        if not (broke_low or broke_high):
            return None
        extreme = l if broke_low else h
        kind = SweepKind.WICK if (c > pool.price if broke_low else c < pool.price) else SweepKind.CLOSE_THROUGH
        depth = (pool.price - extreme) if broke_low else (extreme - pool.price)
        self._seq += 1
        sweep = SweepEvent(
            id=f"swp{self._seq}",
            pool=pool,
            level=pool.price,
            side=pool.side,
            formed_idx=pool.formed_idx,
            formed_time=pool.formed_time,
            sweep_idx=i,
            sweep_time=self.times[i],
            kind=kind,
            depth=depth,
            depth_atr=depth / atr if atr > 0 else 0.0,
            bars_held=i - pool.formed_idx,
        )
        self.sweeps.append(sweep)
        self._open_sweeps[pool.id] = sweep
        if kind is SweepKind.WICK:
            # traded beyond the level and closed back inside: acceptance
            # already happened on the sweeping bar itself; lifecycle is final
            sweep.reclaimed = True
            sweep.reclaim_idx = i
            sweep.reclaim_time = self.times[i]
            sweep.reclaim_close = c
            if not self.cfg.allow_resweep:
                pool.active = False
        # CLOSE_THROUGH pools stay active until the lifecycle resolves
        # (reclaimed or confirmed breakout) so it can advance on later bars
        return sweep

    def _check_reclaim_or_breakout(self, pool: LiquidityPool, sweep: SweepEvent, i: int, c: float) -> bool:
        back_inside_low = pool.side is Side.LOW and c > pool.price
        back_inside_high = pool.side is Side.HIGH and c < pool.price
        if back_inside_low or back_inside_high:
            sweep.reclaimed = True
            sweep.reclaim_idx = i
            sweep.reclaim_time = self.times[i]
            sweep.reclaim_close = c
            if not self.cfg.allow_resweep:
                pool.active = False
            return True
        if i - sweep.sweep_idx >= self.cfg.breakout_confirm_bars:
            sweep.confirmed_breakout = True
            sweep.breakout_idx = i
            pool.active = False
            return True
        return False

    # ------------------------------------------------------------------ #
    # queries
    # ------------------------------------------------------------------ #

    def unswept_pools(self, side: Side | None = None, kinds=None) -> list[LiquidityPool]:
        out = []
        for p in self.pools:
            if not p.active or p.dynamic and p.price in (float("inf"), float("-inf")):
                continue
            if side is not None and p.side is not side:
                continue
            if kinds is not None and p.kind not in kinds:
                continue
            out.append(p)
        return out

    def nearest_unswept(self, side: Side, price: float, kinds=None) -> LiquidityPool | None:
        """Nearest active pool beyond `price` on the given side."""
        best: LiquidityPool | None = None
        for p in self.unswept_pools(side, kinds):
            if side is Side.HIGH and p.price <= price:
                continue
            if side is Side.LOW and p.price >= price:
                continue
            if best is None or abs(p.price - price) < abs(best.price - price):
                best = p
        return best
