import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tme.synth import bars_to_df  # noqa: E402


def build_df(rows, start="2025-01-06 00:00", tf="15min"):
    return bars_to_df(rows, start=start, tf=tf)
