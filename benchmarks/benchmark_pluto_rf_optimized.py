from __future__ import annotations

import argparse
import json
from pathlib import Path

from optimized_transfer.pluto_adapter import load_pluto_adapter


def main() -> int:
    """Menjalankan benchmark optimized_transfer langsung pada link RF PlutoSDR."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml")
    parser.add_argument("--input", default="/Users/mm/GitHub/radio_fix/input.jpg")
    parser.add_argument("--output-dir", default="/Users/mm/GitHub/radio_fix/receive")
    parser.add_argument("--scenario")
    parser.add_argument("--all-scenarios", action="store_true")
    args = parser.parse_args()
    engine = load_pluto_adapter(args.config)
    if args.all_scenarios:
        results = [result.to_dict() for result in engine.run_scenarios(args.input, args.output_dir)]
        print(json.dumps(results, indent=2))
        return 0 if results else 1
    scenario = args.scenario or "loop_cable_nominal"
    result = engine.send_file(Path(args.input), Path(args.output_dir), scenario=scenario)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
