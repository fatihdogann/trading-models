"""Synthetic pattern bars must be valid OHLC — they are used both by the
demo generator and by deterministic unit tests."""

from tme.synth import PATTERN_LEN, bearish_pattern_bars, bullish_pattern_bars, generate


def _assert_valid(rows, name):
    for k, (o, h, l, c) in enumerate(rows):
        assert h >= max(o, c) and h >= l, f"{name} bar {k}: high violates OHLC"
        assert l <= min(o, c) and l <= h, f"{name} bar {k}: low violates OHLC"


def test_pattern_bars_are_valid_ohlc():
    assert len(bullish_pattern_bars(100.0, 0.3)) == PATTERN_LEN
    _assert_valid(bullish_pattern_bars(100.0, 0.3), "bullish")
    _assert_valid(bullish_pattern_bars(2400.0, 1.5), "bullish-scaled")
    _assert_valid(bearish_pattern_bars(100.0, 0.3), "bearish")


def test_generated_series_is_valid_ohlc():
    df = generate(n_days=6, tf="15m", seed=11, inject=True)
    bad = (df["high"] < df[["open", "close", "low"]].max(axis=1)) | \
          (df["low"] > df["high"])
    assert not bad.any(), df[bad].head()
