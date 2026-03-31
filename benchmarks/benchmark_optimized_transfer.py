from __future__ import annotations

import argparse
import json
from pathlib import Path

from optimized_transfer.config import load_runtime_config
from optimized_transfer.runtime import HighThroughputTransferEngine


def main() -> int:
    """Menjalankan benchmark sintetis atau file nyata untuk target throughput baru."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/Users/mm/GitHub/radio_fix/configs/optimized_transfer_2mbps.yaml")
    parser.add_argument("--input")
    parser.add_argument("--workspace", default="/Users/mm/GitHub/radio_fix/optimized_benchmark")
    args = parser.parse_args()
    config = load_runtime_config(args.config)
    engine = HighThroughputTransferEngine(config)
    if args.input:
        result = engine.send_file(Path(args.input), Path(config.target_output_dir))
    else:
        result = engine.run_synthetic_benchmark(Path(args.workspace))
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.meets_target else 1


if __name__ == "__main__":
    raise SystemExit(main())
