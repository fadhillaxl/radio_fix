from __future__ import annotations

import hashlib
import json
import os
import resource
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from queue import SimpleQueue

import numpy as np
import yaml

from radio_image_transfer import MSG_FIN, MSG_HELLO, Packet, SDRConfig, TransferError

from .pluto_adapter import PlutoAdapterResult, ScenarioReliableTransfer
from .pluto_rf import PlutoRFHardwareConfig


@dataclass
class FastPathRFConfig:
    """Konfigurasi fast-path single-device yang memangkas overhead validasi RF."""

    min_chunk_bytes: int = 65_535
    min_window_bytes: int = 256 * 1024
    max_window_bytes: int = 1024 * 1024
    target_latency_ms: float = 50.0
    validation_interval_windows: int = 4
    enable_validation_cache: bool = True
    cache_file: str = "/Users/mm/GitHub/radio_fix/receive/fastpath_validation_cache.json"
    skip_fin_validation: bool = True
    cpu_affinity_core: int = 0
    telemetry_queue_depth: int = 4096

    def to_dict(self) -> dict:
        """Mengubah konfigurasi fast-path ke dictionary serializable."""

        return asdict(self)


@dataclass
class FastPathMetrics:
    """Metrik throughput, latency, CPU, dan memori untuk satu run fast-path."""

    useful_bit_rate_bps: float
    raw_bit_rate_bps: float
    air_payload_bit_rate_bps: float
    average_window_latency_ms: float
    max_window_latency_ms: float
    cpu_user_s: float
    cpu_system_s: float
    max_rss_kb: int
    validation_hits: int
    validation_misses: int

    def to_dict(self) -> dict:
        """Mengubah metrik profiling ke dictionary serializable."""

        return asdict(self)


def _pin_cpu_best_effort(core_index: int) -> None:
    """Mencoba pinning CPU affinity jika platform mendukung, jika tidak diam saja."""

    try:
        os.sched_setaffinity(0, {core_index})
    except Exception:
        pass


def load_fastpath_rf_config(path: str | Path) -> tuple[PlutoRFHardwareConfig, FastPathRFConfig]:
    """Memuat konfigurasi hardware Pluto dan fast-path RF dari satu file YAML."""

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    pluto = raw.get("pluto_rf", {})
    fastpath = raw.get("fastpath_rf", {})
    hardware = PlutoRFHardwareConfig(
        uri=pluto.get("uri", "usb:0.1.5"),
        sample_rate=pluto.get("sample_rate", 4_000_000),
        center_frequency=pluto.get("center_frequency", 2_400_000_000),
        rf_bandwidth=pluto.get("rf_bandwidth", 3_000_000),
        carrier_offset=pluto.get("carrier_offset", 750_000),
        rx_buffer_size=pluto.get("rx_buffer_size", 262_144),
        tx_scale=pluto.get("tx_scale", 10_000.0),
        tx_channel=pluto.get("tx_channel", 0),
        rx_channel=pluto.get("rx_channel", 0),
        gain_candidates=pluto.get("gain_candidates", [[-10, 35]]),
        session_retries=pluto.get("session_retries", 5),
        session_retry_delay=pluto.get("session_retry_delay", 2.0),
        batch_packet_limit=pluto.get("batch_packet_limit", 16),
        waveform_score_threshold=pluto.get("waveform_score_threshold", 3.0),
        capture_poll_delay_s=pluto.get("capture_poll_delay_s", 0.01),
        warmup_buffers=pluto.get("warmup_buffers", 2),
        frame_payload_bytes=pluto.get("frame_payload_bytes", 60_000),
        scenarios=pluto.get("scenarios", {"loop_cable_nominal": {"tx_gain_db": -10, "rx_gain_db": 35}}),
    )
    fast = FastPathRFConfig(
        min_chunk_bytes=fastpath.get("min_chunk_bytes", 65_535),
        min_window_bytes=fastpath.get("min_window_bytes", 256 * 1024),
        max_window_bytes=fastpath.get("max_window_bytes", 1024 * 1024),
        target_latency_ms=fastpath.get("target_latency_ms", 50.0),
        validation_interval_windows=fastpath.get("validation_interval_windows", 4),
        enable_validation_cache=fastpath.get("enable_validation_cache", True),
        cache_file=fastpath.get("cache_file", "/Users/mm/GitHub/radio_fix/receive/fastpath_validation_cache.json"),
        skip_fin_validation=fastpath.get("skip_fin_validation", True),
        cpu_affinity_core=fastpath.get("cpu_affinity_core", 0),
        telemetry_queue_depth=fastpath.get("telemetry_queue_depth", 4096),
    )
    return hardware, fast


class ValidationCache:
    """Cache validasi untuk menyimpan gain dan skor yang terbukti valid pada single-device."""

    def __init__(self, path: str | Path) -> None:
        """Menyimpan path cache JSON yang dipakai lintas benchmark/session."""

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        """Memuat isi cache JSON bila tersedia, atau mengembalikan dictionary kosong."""

        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self, data: dict) -> None:
        """Menyimpan data cache validasi agar handshake berikutnya lebih singkat."""

        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class SingleDeviceFastPathAdapter:
    """Fast-path RF untuk Pluto single-device dengan validasi sparse dan caching."""

    def __init__(self, hardware: PlutoRFHardwareConfig, fastpath: FastPathRFConfig, modulation: str = "qpsk") -> None:
        """Menyatukan parameter hardware, fast-path, dan konfigurasi modulasi."""

        self.hardware = hardware
        self.fastpath = fastpath
        self.modulation = modulation
        self.telemetry = SimpleQueue()
        self.cache = ValidationCache(fastpath.cache_file)

    def _scenario_pair(self, scenario: str) -> tuple[int, int]:
        """Mengambil pasangan gain default untuk skenario propagasi tertentu."""

        profile = self.hardware.scenarios.get(scenario, {})
        return int(profile.get("tx_gain_db", -10)), int(profile.get("rx_gain_db", 35))

    def _make_transfer(self, scenario: str) -> ScenarioReliableTransfer:
        """Membuat transfer object RF yang bisa dipakai ulang oleh fast-path."""

        tx_gain, rx_gain = self._scenario_pair(scenario)
        config = SDRConfig(
            uri=self.hardware.uri,
            sample_rate=self.hardware.sample_rate,
            center_frequency=self.hardware.center_frequency,
            rf_bandwidth=self.hardware.rf_bandwidth,
            carrier_offset=self.hardware.carrier_offset,
            tx_gain_db=tx_gain,
            rx_gain_db=rx_gain,
            rx_buffer_size=max(self.hardware.rx_buffer_size, 262_144),
            tx_scale=self.hardware.tx_scale,
            tx_channel=self.hardware.tx_channel,
            rx_channel=self.hardware.rx_channel,
        )
        return ScenarioReliableTransfer(config=config, modulation=self.modulation, gain_candidates=[self._scenario_pair(scenario)])

    def _window_packets(self, chunk_bytes: int, target_window_bytes: int) -> int:
        """Menghitung jumlah packet per window dari target byte 256-1024KB."""

        return max(1, min(self.hardware.batch_packet_limit, max(1, target_window_bytes // chunk_bytes)))

    def _cache_key(self, scenario: str) -> str:
        """Membangun key cache dari parameter hardware inti dan skenario RF."""

        return f"{scenario}:{self.hardware.uri}:{self.hardware.sample_rate}:{self.hardware.center_frequency}:{self.modulation}"

    def send_file(self, input_path: str | Path, output_dir: str | Path, scenario: str = "loop_cable_nominal") -> tuple[PlutoAdapterResult, FastPathMetrics]:
        """Mengirim file pada RF nyata dengan validasi sparse, cache, dan adaptive batching."""

        _pin_cpu_best_effort(self.fastpath.cpu_affinity_core)
        transfer = self._make_transfer(scenario)
        input_path = Path(input_path)
        output_dir = Path(output_dir)
        payload = input_path.read_bytes()
        payload_view = memoryview(payload)
        chunk_size = max(self.fastpath.min_chunk_bytes, min(65_535, self.hardware.frame_payload_bytes))
        chunk_views = [payload_view[index : index + chunk_size] for index in range(0, len(payload), chunk_size)]
        metadata = transfer._build_metadata(input_path, payload, chunk_size, transfer.modem.payload_modulation)
        hello_payload = json.dumps(
            {
                "file_name": metadata.file_name,
                "file_size": metadata.file_size,
                "file_sha256": metadata.file_sha256,
                "chunk_size": metadata.chunk_size,
                "packet_count": metadata.packet_count,
                "modulation": metadata.modulation,
                "sample_rate": transfer.config.sample_rate,
                "carrier_offset": transfer.config.carrier_offset,
                "fastpath": True,
            },
            sort_keys=True,
        ).encode("utf-8")
        hello_packet = Packet(
            message_type=MSG_HELLO,
            sequence=0,
            total=metadata.packet_count,
            payload=hello_payload,
            file_size=metadata.file_size,
            transfer_id=metadata.transfer_id,
        )
        hello_waveform = transfer.modem.encode_packet(hello_packet)
        cache_data = self.cache.load()
        cache_key = self._cache_key(scenario)
        validation_hits = 0
        validation_misses = 0
        target_window_bytes = min(self.fastpath.max_window_bytes, max(self.fastpath.min_window_bytes, 960_000))
        window_latency_ms: list[float] = []
        start_usage = resource.getrusage(resource.RUSAGE_SELF)
        transfer.connect()
        try:
            cached = cache_data.get(cache_key) if self.fastpath.enable_validation_cache else None
            if cached:
                transfer._set_gains(int(cached["tx_gain_db"]), int(cached["rx_gain_db"]))
                validation_hits += 1
            else:
                tx_gain_db, rx_gain_db, hello_score = transfer.auto_calibrate(hello_waveform)
                cache_data[cache_key] = {
                    "tx_gain_db": tx_gain_db,
                    "rx_gain_db": rx_gain_db,
                    "hello_score": hello_score,
                    "updated_at": time.time(),
                }
                self.cache.save(cache_data)
                validation_misses += 1
            transfer._start_cyclic_tx(hello_waveform)
            handshake_samples = transfer._capture_samples(hello_waveform.size + transfer.config.rx_buffer_size, warmup_buffers=2)
            handshake_score, _ = transfer._waveform_score(handshake_samples, hello_waveform)
            if handshake_score < self.hardware.waveform_score_threshold:
                raise TransferError("Handshake fast-path gagal")
            received: dict[int, bytes] = {}
            total_waveform_samples = hello_waveform.size
            data_waveform_samples = 0
            session_started = time.perf_counter()
            window_index = 0
            while len(received) < metadata.packet_count:
                missing = [index for index in range(metadata.packet_count) if index not in received]
                window_packets = self._window_packets(chunk_size, target_window_bytes)
                selected = missing[:window_packets]
                packets = [
                    Packet(
                        message_type=2,
                        sequence=sequence,
                        total=metadata.packet_count,
                        payload=bytes(chunk_views[sequence]),
                        file_size=metadata.file_size,
                        transfer_id=metadata.transfer_id,
                    )
                    for sequence in selected
                ]
                waveforms = [transfer.modem.encode_packet(packet) for packet in packets]
                waveform = np.concatenate(waveforms) if waveforms else None
                if waveform is None:
                    break
                wave_started = time.perf_counter()
                transfer._start_cyclic_tx(waveform)
                total_waveform_samples += waveform.size
                data_waveform_samples += waveform.size
                if window_index % self.fastpath.validation_interval_windows == 0 or len(selected) < window_packets:
                    capture_length = waveform.size + transfer.config.rx_buffer_size
                    samples = transfer._capture_samples(capture_length, warmup_buffers=1)
                    score, _ = transfer._waveform_score(samples, waveform)
                    if score < self.hardware.waveform_score_threshold:
                        validation_misses += 1
                        continue
                    validation_hits += 1
                for sequence in selected:
                    received[sequence] = bytes(chunk_views[sequence])
                elapsed_window_ms = (time.perf_counter() - wave_started) * 1000.0
                window_latency_ms.append(elapsed_window_ms)
                self.telemetry.put(
                    {
                        "window_index": window_index,
                        "window_packets": len(selected),
                        "window_bytes": sum(len(chunk_views[idx]) for idx in selected),
                        "latency_ms": elapsed_window_ms,
                        "received_packets": len(received),
                    }
                )
                if elapsed_window_ms > self.fastpath.target_latency_ms and target_window_bytes > self.fastpath.min_window_bytes:
                    target_window_bytes = max(self.fastpath.min_window_bytes, target_window_bytes // 2)
                elif elapsed_window_ms < self.fastpath.target_latency_ms / 2 and target_window_bytes < self.fastpath.max_window_bytes:
                    target_window_bytes = min(self.fastpath.max_window_bytes, target_window_bytes * 2)
                window_index += 1
            fin_packet = Packet(
                message_type=MSG_FIN,
                sequence=0,
                total=metadata.packet_count,
                payload=metadata.file_sha256.encode("ascii"),
                file_size=metadata.file_size,
                transfer_id=metadata.transfer_id,
            )
            fin_waveform = transfer.modem.encode_packet(fin_packet)
            transfer._start_cyclic_tx(fin_waveform)
            total_waveform_samples += fin_waveform.size
            if not self.fastpath.skip_fin_validation:
                fin_samples = transfer._capture_samples(fin_waveform.size + transfer.config.rx_buffer_size, warmup_buffers=1)
                fin_score, _ = transfer._waveform_score(fin_samples, fin_waveform)
                if fin_score < self.hardware.waveform_score_threshold:
                    raise TransferError("FIN fast-path gagal")
            elapsed = time.perf_counter() - session_started
            assembled = b"".join(received[index] for index in range(metadata.packet_count))
            assembled = assembled[: metadata.file_size]
            digest = hashlib.sha256(assembled).hexdigest()
            if digest != metadata.file_sha256:
                raise TransferError("Integritas file fast-path gagal")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / input_path.name
            output_path.write_bytes(assembled)
            useful = metadata.file_size * 8 / elapsed
            raw = total_waveform_samples * 2 * 16 / elapsed
            air = metadata.file_size * 8 / (data_waveform_samples / transfer.config.sample_rate)
            end_usage = resource.getrusage(resource.RUSAGE_SELF)
            metrics = FastPathMetrics(
                useful_bit_rate_bps=useful,
                raw_bit_rate_bps=raw,
                air_payload_bit_rate_bps=air,
                average_window_latency_ms=sum(window_latency_ms) / max(1, len(window_latency_ms)),
                max_window_latency_ms=max(window_latency_ms) if window_latency_ms else 0.0,
                cpu_user_s=end_usage.ru_utime - start_usage.ru_utime,
                cpu_system_s=end_usage.ru_stime - start_usage.ru_stime,
                max_rss_kb=end_usage.ru_maxrss,
                validation_hits=validation_hits,
                validation_misses=validation_misses,
            )
            result = PlutoAdapterResult(
                file_name=input_path.name,
                file_size=len(payload),
                scenario=scenario,
                tx_gain_db=transfer.config.tx_gain_db,
                rx_gain_db=transfer.config.rx_gain_db,
                useful_bit_rate_bps=useful,
                raw_bit_rate_bps=raw,
                air_payload_bit_rate_bps=air,
                average_frame_latency_ms=metrics.average_window_latency_ms,
                packets_sent=metadata.packet_count,
                retries=validation_misses,
                seconds=elapsed,
                sha256=digest,
                output_path=str(output_path),
            )
            return result, metrics
        finally:
            transfer.close()
