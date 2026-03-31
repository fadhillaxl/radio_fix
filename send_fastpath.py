from __future__ import annotations

import argparse
import json
from pathlib import Path

from optimized_transfer.fastpath_rf import SingleDeviceFastPathAdapter, load_fastpath_rf_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/Users/mm/GitHub/radio_fix/NearDrop.app.zip")
    parser.add_argument("--output-dir", default="/Users/mm/GitHub/radio_fix/receive")
    parser.add_argument("--config", default="/Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml")
    parser.add_argument("--scenario", default="loop_cable_nominal")
    parser.add_argument("--log-file", default="/Users/mm/GitHub/radio_fix/receive/fastpath_last_run.json")
    args = parser.parse_args()
    hardware, fast = load_fastpath_rf_config(args.config)
    adapter = SingleDeviceFastPathAdapter(hardware, fast)
    result, metrics = adapter.send_file(Path(args.input), Path(args.output_dir), args.scenario)
    payload = {"result": result.to_dict(), "metrics": metrics.to_dict()}
    Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
    Path(args.log_file).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
