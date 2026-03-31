from __future__ import annotations

import argparse
import json
from pathlib import Path

from optimized_transfer.fastpath_rf import SingleDeviceFastPathAdapter, load_fastpath_rf_config
from optimized_transfer.pluto_adapter import load_pluto_adapter
from tools.traffic_generator import write_payload_file


def main() -> int:
    """Membandingkan baseline vs fast-path pada payload UDP/TCP 1-64KB."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml")
    parser.add_argument("--workspace", default="/Users/mm/GitHub/radio_fix/perf_workspace")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    baseline = load_pluto_adapter(args.config)
    hardware, fastpath = load_fastpath_rf_config(args.config)
    fast_adapter = SingleDeviceFastPathAdapter(hardware, fastpath)
    sizes = [1024, 4 * 1024, 16 * 1024, 64 * 1024]
    report: list[dict] = []
    for mode in ["udp", "tcp"]:
        for size in sizes:
            payload_path = write_payload_file(workspace / f"{mode}_{size}.bin", mode, size)
            baseline_result = baseline.send_file(payload_path, workspace / "receive_baseline", scenario="loop_cable_nominal")
            fast_result, fast_metrics = fast_adapter.send_file(payload_path, workspace / "receive_fastpath", scenario="loop_cable_nominal")
            report.append(
                {
                    "mode": mode,
                    "size_bytes": size,
                    "baseline": baseline_result.to_dict(),
                    "fastpath": fast_result.to_dict(),
                    "fastpath_metrics": fast_metrics.to_dict(),
                }
            )
    output_path = workspace / "single_device_fastpath_profile.json"
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output_path), "entries": len(report)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
