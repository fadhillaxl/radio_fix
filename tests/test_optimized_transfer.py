from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from optimized_transfer.bitmap import AckBitmap
from optimized_transfer.config import LinkRuntimeConfig, load_runtime_config, save_runtime_config
from optimized_transfer.framing import FrameCodec, FrameType
from optimized_transfer.runtime import HighThroughputTransferEngine


class AckBitmapTests(unittest.TestCase):
    """Menguji operasi bitmap ACK/NACK ringkas."""

    def test_bitmap_roundtrip(self) -> None:
        bitmap = AckBitmap(20)
        bitmap.set(0)
        bitmap.set(3)
        bitmap.set(19)
        restored = AckBitmap.from_bytes(20, bitmap.to_bytes())
        self.assertTrue(restored.is_set(0))
        self.assertTrue(restored.is_set(3))
        self.assertTrue(restored.is_set(19))
        self.assertEqual(restored.count(), 3)


class FrameCodecTests(unittest.TestCase):
    """Menguji framing ringan sebagai pengganti validasi capture besar."""

    def test_decode_stream_roundtrip(self) -> None:
        codec = FrameCodec()
        encoded = codec.build_data(7, 4, b"payload")
        frames, leftover = codec.decode_stream(encoded)
        self.assertEqual(leftover, b"")
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].frame_type, FrameType.DATA)
        self.assertEqual(frames[0].sequence, 4)
        self.assertEqual(frames[0].payload, b"payload")


class ConfigTests(unittest.TestCase):
    """Menguji file konfigurasi YAML untuk runtime optimized transfer."""

    def test_config_save_load(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.yaml"
            config = LinkRuntimeConfig()
            save_runtime_config(path, config)
            restored = load_runtime_config(path)
            self.assertEqual(restored.sample_rate, config.sample_rate)
            self.assertEqual(restored.pipeline.frame_payload_bytes, config.pipeline.frame_payload_bytes)


class RuntimeTests(unittest.TestCase):
    """Menguji engine end-to-end baru memakai link in-memory multi-process."""

    def test_small_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            input_path = base / "input.bin"
            input_path.write_bytes(np.arange(4096, dtype=np.uint8).tobytes())
            config = LinkRuntimeConfig(target_output_dir=str(base / "receive"))
            config.pipeline.frame_payload_bytes = 256
            config.pipeline.max_inflight_frames = 64
            config.pipeline.ack_interval_frames = 8
            config.throughput_target.minimum_end_to_end_bps = 1
            result = HighThroughputTransferEngine(config).send_file(input_path, base / "receive")
            self.assertTrue(result.meets_target)
            self.assertEqual((base / "receive" / "input.bin").read_bytes(), input_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
