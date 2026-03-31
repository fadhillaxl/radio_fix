from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from optimized_transfer.fastpath_rf import ValidationCache, load_fastpath_rf_config


class FastPathRFTests(unittest.TestCase):
    """Menguji loader dan cache fast-path RF single-device."""

    def test_validation_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = ValidationCache(Path(temp_dir) / "cache.json")
            payload = {"hello": {"tx_gain_db": -10, "rx_gain_db": 35}}
            cache.save(payload)
            self.assertEqual(cache.load(), payload)

    def test_load_fastpath_config(self) -> None:
        hardware, fastpath = load_fastpath_rf_config("/Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml")
        self.assertGreaterEqual(hardware.frame_payload_bytes, 60000)
        self.assertEqual(fastpath.max_window_bytes, 1048576)


if __name__ == "__main__":
    unittest.main()
