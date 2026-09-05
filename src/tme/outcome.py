"""Outcome evaluation — deliberately separated from detection.

A triggered setup defines entry / invalidation / target; this module walks
the following bars and measures what happened. No position sizing, no
pyramiding: research-grade R statistics only.
"""

from __future__ import annotations

from tme.config import OutcomeConfig
from tme.types import Direction, Outcome, SetupEvent


def evaluate(setup: SetupEvent, ctx, cfg: OutcomeConfig) -> Outcome | None:
    if setup.state != "TRIGGERED" or setup.trigger_idx is None:
        return None
    if setup.entry_price is None or setup.invalidation is None:
        return None

    long = setup.direction is Direction.LONG
    entry = setup.entry_price
    stop = setup.invalidation
    risk = (entry - stop) if long else (stop - entry)
    if risk <= 0:
        return None
    target = setup.target_price
    if target is None:
        target = entry + risk * cfg.default_target_r if long else entry - risk * cfg.default_target_r
    # target must actually be beyond entry in the trade direction
    if (long and target <= entry) or ((not long) and target >= entry):
        target = entry + risk * cfg.default_target_r if long else entry - risk * cfg.default_target_r

    start = setup.trigger_idx + 1
    end = min(ctx.n, start + cfg.max_hold_bars)
    mae = 0.0
    mfe = 0.0
    t_target: int | None = None
    t_inval: int | None = None
    exit_price: float | None = None
    exit_reason = "TIME"
    exit_idx = end - 1

    for j in range(start, end):
        h, l = ctx.highs[j], ctx.lows[j]
        fav = (h - entry) if long else (entry - l)
        adv = (entry - l) if long else (h - entry)
        mfe = max(mfe, fav / risk)
        mae = min(mae, -adv / risk)
        hit_stop = l <= stop if long else h >= stop
        hit_target = h >= target if long else l <= target
        if hit_stop and hit_target and cfg.stop_first:
            hit_target = False
        if hit_target:
            t_target = j - setup.trigger_idx
            exit_price, exit_reason, exit_idx = target, "TARGET", j
            break
        if hit_stop:
            t_inval = j - setup.trigger_idx
            exit_price, exit_reason, exit_idx = stop, "STOP", j
            break

    if exit_price is None:
        if end - start < 1:
            return None
        exit_price = ctx.closes[end - 1]
        exit_idx = end - 1
    r = ((exit_price - entry) if long else (entry - exit_price)) / risk

    return Outcome(
        setup_id=setup.setup_id,
        model=setup.model,
        variant=setup.variant,
        direction=setup.direction,
        entry=entry,
        stop=stop,
        target=target,
        exit_price=exit_price,
        exit_reason=exit_reason,
        r=round(r, 4),
        mae_r=round(mae, 4),
        mfe_r=round(mfe, 4),
        bars_held=exit_idx - setup.trigger_idx,
        t_target_bars=t_target,
        t_invalidation_bars=t_inval,
        trigger_time=setup.trigger_time,
        exit_time=ctx.times[exit_idx],
    )
