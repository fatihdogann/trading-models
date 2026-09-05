"""Human-readable explanations for setups — no black-box logic."""

from __future__ import annotations

from tme.types import SetupEvent, Transition


def format_setup(ev: SetupEvent, max_items: int = 12) -> str:
    lines = [
        f"{ev.model.upper().replace('_', ' ')} {str(ev.direction).upper()}"
        f"  [{ev.symbol} {ev.timeframe}]  variant={ev.variant}"
    ]
    if ev.state == "TRIGGERED":
        lines.append(f"Score: {ev.score:.0f}/{ev.score_max:.0f}")
    else:
        lines.append(f"State: {ev.state}" + (f" ({ev.failure_reason})" if ev.failure_reason else ""))
    for c in ev.checklist[:max_items]:
        mark = "✓" if c.passed else "✗"
        suffix = f"  ({c.required and 'required' or 'optional'})" if not c.passed else ""
        detail = f" — {c.detail}" if c.detail else ""
        req = "*" if c.required else ""
        lines.append(f"  {mark} {c.name}{req}{detail}{suffix}")
    if ev.state == "TRIGGERED" and ev.entry_price is not None:
        lines.append(
            f"  entry {ev.entry_price:.5f} | stop {ev.invalidation:.5f} | "
            f"target {ev.target_price if ev.target_price is not None else float('nan'):.5f}"
        )
    return "\n".join(lines)


def format_rejections(models) -> str:
    lines = []
    for m in models:
        if m.rejections:
            total = sum(m.rejections.values())
            parts = ", ".join(f"{k}={v}" for k, v in sorted(m.rejections.items()))
            lines.append(f"{m.name}#{m.variant}: {total} rejected ({parts})")
    return "\n".join(lines) if lines else "no rejections"


def format_transitions(log_entries: list[Transition], setup_id: str) -> str:
    rows = [t for t in log_entries if t.setup_id == setup_id]
    lines = [f"{t.idx} {t.time}  {t.frm} -> {t.to}" + (f"  [{t.note}]" if t.note else "")
             for t in rows]
    return "\n".join(lines)
