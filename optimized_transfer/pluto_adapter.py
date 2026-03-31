from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from radio_image_transfer import ReliableImageTransfer, SDRConfig, TransferStats, TransferError

from .pluto_rf import PlutoRFHardwareConfig, load_pluto_rf_config


@dataclass
class PlutoAdapterResult:
    """Ringkasan transfer RF terverifikasi lewat adapter optimized_transfer ke PlutoSDR."""

    file_name: str
    file_size: int
    scenario: str
    tx_gain_db: int
    rx_gain_db: int
    useful_bit_rate_bps: float
    raw_bit_rate_bps: float
    air_payload_bit_rate_bps: float
    average_frame_latency_ms: float
    packets_sent: int
    retries: int
    seconds: float
    sha256: str
    output_path: str

    def to_dict(self) -> dict:
        """Mengubah hasil adapter menjadi dictionary serializable."""

        return asdict(self)


class ScenarioReliableTransfer(ReliableImageTransfer):
    """Varian transfer lama yang memakai candidate gain dari konfigurasi optimized_transfer."""

    def __init__(self, config: SDRConfig, modulation: str, gain_candidates: list[tuple[int, int]]) -> None:
        """Menyimpan daftar candidate gain skenario untuk kalibrasi hardware nyata."""

        super().__init__(config=config, modulation=modulation)
        self.gain_candidates = gain_candidates

    def auto_calibrate(self, hello_waveform: bytes | object) -> tuple[int, int, float]:
        """Mengalibrasi link memakai daftar gain skenario agar konsisten antar pengujian RF."""

        best: tuple[float, tuple[int, int]] | None = None
        for tx_gain, rx_gain in self.gain_candidates:
            self._set_gains(tx_gain, rx_gain)
            self._start_cyclic_tx(hello_waveform)
            capture_length = hello_waveform.size + self.config.rx_buffer_size
            samples = self._capture_samples(capture_length, warmup_buffers=2)
            score, _ = self._waveform_score(samples, hello_waveform)
            if best is None or score > best[0]:
                best = (score, (tx_gain, rx_gain))
        if best is None or best[0] < 3.0:
            raise TransferError("Kalibrasi link gagal. Coba periksa kabel loopback TX1 -> RX1 atau gain RF")
        self._set_gains(best[1][0], best[1][1])
        return best[1][0], best[1][1], best[0]


class PlutoOptimizedAdapter:
    """Adapter terverifikasi yang menghubungkan optimized_transfer dengan PlutoSDR nyata."""

    def __init__(self, hardware: PlutoRFHardwareConfig, modulation: str = "qpsk") -> None:
        """Menyimpan parameter hardware dan modulasi untuk transfer RF aktual."""

        self.hardware = hardware
        self.modulation = modulation

    def _build_gain_candidates(self, scenario: str) -> list[tuple[int, int]]:
        """Menyusun prioritas gain candidate berdasarkan skenario channel RF."""

        preferred = self.hardware.scenarios.get(scenario, {})
        return [(int(preferred.get("tx_gain_db", -10)), int(preferred.get("rx_gain_db", 35)))]

    def _make_transfer(self, scenario: str) -> ScenarioReliableTransfer:
        """Membangun instance transfer RF lama yang diparameterisasi oleh config baru."""

        preferred_tx, preferred_rx = self._build_gain_candidates(scenario)[0]
        config = SDRConfig(
            uri=self.hardware.uri,
            sample_rate=self.hardware.sample_rate,
            center_frequency=self.hardware.center_frequency,
            rf_bandwidth=self.hardware.rf_bandwidth,
            carrier_offset=self.hardware.carrier_offset,
            tx_gain_db=preferred_tx,
            rx_gain_db=preferred_rx,
            rx_buffer_size=self.hardware.rx_buffer_size,
            tx_scale=self.hardware.tx_scale,
            tx_channel=self.hardware.tx_channel,
            rx_channel=self.hardware.rx_channel,
        )
        return ScenarioReliableTransfer(config=config, modulation=self.modulation, gain_candidates=self._build_gain_candidates(scenario))

    def send_file(self, input_path: str | Path, output_dir: str | Path, scenario: str = "loop_cable_nominal") -> PlutoAdapterResult:
        """Mengirim file pada link RF nyata memakai pipeline yang sudah terbukti stabil."""

        input_path = Path(input_path)
        output_dir = Path(output_dir)
        last_error: Exception | None = None
        output_path: Path | None = None
        stats: TransferStats | None = None
        transfer: ScenarioReliableTransfer | None = None
        for attempt in range(1, self.hardware.session_retries + 1):
            transfer = self._make_transfer(scenario)
            try:
                transfer.connect()
                output_path, stats = transfer.transfer_file(
                    input_path=input_path,
                    output_dir=output_dir,
                    chunk_size=self.hardware.frame_payload_bytes,
                    window_packets=self.hardware.batch_packet_limit,
                    max_retries=20,
                )
                break
            except Exception as exc:
                last_error = exc
                time.sleep(self.hardware.session_retry_delay * attempt)
            finally:
                transfer.close()
        if output_path is None or stats is None or transfer is None:
            raise TransferError(str(last_error) if last_error else "Adapter Pluto gagal")
        payload = input_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        received_digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        if digest != received_digest:
            raise TransferError("Digest hasil adapter Pluto tidak cocok dengan file sumber")
        average_frame_latency_ms = (stats.seconds / max(1, stats.packets_sent)) * 1000.0
        return PlutoAdapterResult(
            file_name=input_path.name,
            file_size=len(payload),
            scenario=scenario,
            tx_gain_db=transfer.config.tx_gain_db,
            rx_gain_db=transfer.config.rx_gain_db,
            useful_bit_rate_bps=stats.useful_bit_rate_bps,
            raw_bit_rate_bps=stats.raw_bit_rate_bps,
            air_payload_bit_rate_bps=stats.air_payload_bit_rate_bps,
            average_frame_latency_ms=average_frame_latency_ms,
            packets_sent=stats.packets_sent,
            retries=stats.retries,
            seconds=stats.seconds,
            sha256=received_digest,
            output_path=str(output_path),
        )

    def run_scenarios(self, input_path: str | Path, output_dir: str | Path) -> list[PlutoAdapterResult]:
        """Menjalankan transfer RF nyata pada semua skenario yang didefinisikan konfigurasi."""

        results = []
        for scenario in self.hardware.scenarios:
            results.append(self.send_file(input_path=input_path, output_dir=output_dir, scenario=scenario))
        return results


def load_pluto_adapter(path: str | Path, modulation: str = "qpsk") -> PlutoOptimizedAdapter:
    """Memuat adapter Pluto terkonfigurasi langsung dari file YAML."""

    hardware = load_pluto_rf_config(path)
    return PlutoOptimizedAdapter(hardware=hardware, modulation=modulation)
