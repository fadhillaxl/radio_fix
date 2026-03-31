from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from optimized_transfer.fastpath_rf import SingleDeviceFastPathAdapter, load_fastpath_rf_config


def main() -> int:
    """Menjalankan continuous streaming fast-path selama durasi tertentu dan merekam hasil."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml")
    parser.add_argument("--input", default="/Users/mm/GitHub/radio_fix/input.jpg")
    parser.add_argument("--output-dir", default="/Users/mm/GitHub/radio_fix/receive")
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--report", default="/Users/mm/GitHub/radio_fix/receive/fastpath_endurance_report.json")
    args = parser.parse_args()
    hardware, fastpath = load_fastpath_rf_config(args.config)
    adapter = SingleDeviceFastPathAdapter(hardware, fastpath)
    started = time.time()
    results = []
    while time.time() - started < args.duration_seconds:
        result, metrics = adapter.send_file(args.input, args.output_dir, scenario="loop_cable_nominal")
        results.append({"result": result.to_dict(), "metrics": metrics.to_dict(), "timestamp": time.time()})
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "runs": len(results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
