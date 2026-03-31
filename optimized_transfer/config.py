from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass
class CorrelatorConfig:
    """Konfigurasi migrasi korelator kritis ke jalur Cython dan fallback Python."""

    backend: str = "cython_preferred"
    fft_batch_size: int = 4096
    detector_threshold: float = 0.72
    overlap_samples: int = 512


@dataclass
class PipelineConfig:
    """Konfigurasi pipeline continuous untuk TX, RX, ACK, dan retransmisi."""

    frame_payload_bytes: int = 65535
    max_inflight_frames: int = 512
    ack_interval_frames: int = 32
    sender_poll_interval_s: float = 0.0005
    resend_timeout_s: float = 0.01
    nack_backoff_s: float = 0.001
    rx_queue_depth: int = 2048
    tx_queue_depth: int = 2048
    receiver_processes: int = 1


@dataclass
class ThroughputTarget:
    """Target performa yang dipakai benchmark dan validasi arsitektur baru."""

    minimum_end_to_end_bps: int = 2_000_000
    expected_air_payload_bps: int = 5_000_000
    synthetic_payload_bytes: int = 16 * 1024 * 1024


@dataclass
class AckProtocolConfig:
    """Konfigurasi protokol ACK/NACK bitmap yang ringkas dan hemat bandwidth."""

    use_bitmap_ack: bool = True
    full_bitmap_interval: int = 8
    nack_bitmap_after_fin: bool = True
    ack_frame_priority: int = 1


@dataclass
class LinkRuntimeConfig:
    """Konfigurasi runtime lengkap untuk arsitektur throughput tinggi."""

    name: str = "optimized-transfer-2mbps"
    modulation: str = "qpsk"
    sample_rate: int = 4_000_000
    center_frequency: int = 2_400_000_000
    rf_bandwidth: int = 3_000_000
    carrier_offset: int = 750_000
    tx_gain_db: int = -10
    rx_gain_db: int = 35
    target_output_dir: str = "/Users/mm/GitHub/radio_fix/receive"
    correlator: CorrelatorConfig = field(default_factory=CorrelatorConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    ack_protocol: AckProtocolConfig = field(default_factory=AckProtocolConfig)
    throughput_target: ThroughputTarget = field(default_factory=ThroughputTarget)

    def to_dict(self) -> dict:
        """Mengubah konfigurasi bertingkat ke dictionary serializable."""

        return asdict(self)


def _section(data: dict, key: str, cls):
    """Mengambil section YAML dan membangun dataclass bagian terkait."""

    return cls(**data.get(key, {}))


def load_runtime_config(path: str | Path) -> LinkRuntimeConfig:
    """Memuat file YAML konfigurasi runtime arsitektur optimized transfer."""

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return LinkRuntimeConfig(
        name=raw.get("name", "optimized-transfer-2mbps"),
        modulation=raw.get("modulation", "qpsk"),
        sample_rate=raw.get("sample_rate", 4_000_000),
        center_frequency=raw.get("center_frequency", 2_400_000_000),
        rf_bandwidth=raw.get("rf_bandwidth", 3_000_000),
        carrier_offset=raw.get("carrier_offset", 750_000),
        tx_gain_db=raw.get("tx_gain_db", -10),
        rx_gain_db=raw.get("rx_gain_db", 35),
        target_output_dir=raw.get("target_output_dir", "/Users/mm/GitHub/radio_fix/receive"),
        correlator=_section(raw, "correlator", CorrelatorConfig),
        pipeline=_section(raw, "pipeline", PipelineConfig),
        ack_protocol=_section(raw, "ack_protocol", AckProtocolConfig),
        throughput_target=_section(raw, "throughput_target", ThroughputTarget),
    )


def save_runtime_config(path: str | Path, config: LinkRuntimeConfig) -> None:
    """Menyimpan konfigurasi runtime ke file YAML agar mudah dibagikan ulang."""

    Path(path).write_text(yaml.safe_dump(config.to_dict(), sort_keys=False), encoding="utf-8")
