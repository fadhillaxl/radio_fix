from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from optimized_transfer.pluto_rf import load_pluto_rf_config


class PlutoRFConfigTests(unittest.TestCase):
    """Menguji loader konfigurasi hardware Pluto untuk jalur RF nyata."""

    def test_load_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "pluto.yaml"
            path.write_text(
                "pluto_rf:\n"
                "  uri: usb:0.1.5\n"
                "  sample_rate: 4000000\n"
                "  scenarios:\n"
                "    loop_cable_nominal:\n"
                "      tx_gain_db: -10\n"
                "      rx_gain_db: 35\n",
                encoding="utf-8",
            )
            config = load_pluto_rf_config(path)
            self.assertEqual(config.uri, "usb:0.1.5")
            self.assertEqual(config.sample_rate, 4_000_000)
            self.assertIn("loop_cable_nominal", config.scenarios)


if __name__ == "__main__":
    unittest.main()
