"""Research runner.

Examples:
    python scripts/run_research.py --demo
    python scripts/run_research.py --csv data/BTCUSD_15m.csv --symbol BTCUSD --tf 15m \
        --htf-csv data/BTCUSD_1h.csv --htf-tf 1h
    python scripts/run_research.py --config configs/default.yaml --demo --out out/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from tme.backtest import Engine
from tme.config import EngineConfig, load_config
from tme.dataio import load_csv, resample
from tme.explain import format_rejections, format_setup
from tme.stats import ablation_table, bucket_tables, summarize, joined_dataframe
from tme.export import save_jsonl, save_tradingview_csv
from tme.synth import generate


def main() -> None:
    ap = argparse.ArgumentParser(description="Trading Model Engine research runner")
    ap.add_argument("--config", default=None, help="YAML config overriding defaults")
    ap.add_argument("--demo", action="store_true", help="run on synthetic data")
    ap.add_argument("--csv", default=None, help="LTF OHLC csv")
    ap.add_argument("--symbol", default="SYM")
    ap.add_argument("--tf", default="15m")
    ap.add_argument("--htf-csv", default=None, help="optional HTF OHLC csv")
    ap.add_argument("--htf-tf", default="1h")
    ap.add_argument("--htf-resample", action="store_true",
                    help="resample the HTF csv up to --htf-tf before use")
    ap.add_argument("--tz", default="UTC",
                    help="timezone of naive timestamps in the csv (e.g. "
                         "America/New_York if your export is in NY time)")
    ap.add_argument("--out", default="out", help="output directory")
    args = ap.parse_args()

    cfg: EngineConfig = load_config(args.config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.demo:
        data = {("DEMO", "15m"): generate(n_days=25, tf="15m", seed=11)}
        data[("DEMO", "1h")] = resample(data[("DEMO", "15m")], "1h")
    elif args.csv:
        data = {(args.symbol, args.tf): load_csv(args.csv, tz=args.tz)}
        if args.htf_csv:
            htf_df = load_csv(args.htf_csv, tz=args.tz)
            if args.htf_resample:
                htf_df = resample(htf_df, _pandas_rule(args.htf_tf))
            data[(args.symbol, args.htf_tf)] = htf_df
    else:
        ap.error("either --demo or --csv is required")
        return

    engine = Engine(cfg, data)
    result = engine.run()

    print(f"\n=== TME run: {len(result.setups)} setup events, "
          f"{len(result.outcomes)} triggered-with-outcome ===\n")

    df = joined_dataframe(result)
    if df.empty:
        print("No setups detected. Check thresholds in config.")
    else:
        print("--- summary by model/variant ---")
        print(summarize(df).to_string(index=False))
        abl = ablation_table(result)
        if not abl.empty:
            print("\n--- ICT 2022 ablation (does the sequence add edge?) ---")
            print(abl.to_string(index=False))
        tables = bucket_tables(result)
        for name, t in tables.items():
            if not t.empty:
                print(f"\n--- by {name} ---")
                print(t.to_string(index=False))

    print("\n--- rejections ---")
    print(format_rejections(result.models))

    triggered = [s for s in result.setups if s.state == "TRIGGERED"]
    print(f"\n--- sample explanations ({min(5, len(triggered))} of {len(triggered)} triggered) ---")
    for ev in triggered[:5]:
        print("\n" + format_setup(ev))

    save_jsonl(result.setups, out / "setups.jsonl")
    save_tradingview_csv(result.setups, out / "tradingview_signals.csv")
    if not df.empty:
        df.to_csv(out / "joined.csv", index=False)
    print(f"\nexports written to {out.resolve()}")


def _pandas_rule(tf: str) -> str:
    return {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
            "1h": "1h", "4h": "4h"}.get(tf, "1h")


if __name__ == "__main__":
    main()
