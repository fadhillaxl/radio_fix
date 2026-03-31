from __future__ import annotations

import json
from pathlib import Path

from optimized_transfer.config import load_runtime_config
from optimized_transfer.runtime import HighThroughputTransferEngine


def main() -> int:
    """Contoh pemakaian engine baru untuk mengirim file dengan pipeline continuous."""

    config = load_runtime_config("/Users/mm/GitHub/radio_fix/configs/optimized_transfer_2mbps.yaml")
    engine = HighThroughputTransferEngine(config)
    result = engine.send_file(
        input_path=Path("/Users/mm/GitHub/radio_fix/NearDrop.app.zip"),
        output_dir=Path("/Users/mm/GitHub/radio_fix/receive"),
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
