from __future__ import annotations

import hashlib
import json
import math
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import adi
import numpy as np
import yaml

from radio_image_transfer import MSG_DATA, MSG_FIN, MSG_HELLO, OFDMModem, Packet, SDRConfig, TransferError

from .bitmap import AckBitmap
from .framing import FrameCodec, FrameType


@dataclass
class PlutoRFHardwareConfig:
    """Konfigurasi hardware PlutoSDR dan parameter runtime RF optimized transfer."""

    uri: str = "usb:0.1.5"
    sample_rate: int = 4_000_000
    center_frequency: int = 2_400_000_000
    rf_bandwidth: int = 3_000_000
    carrier_offset: int = 750_000
    rx_buffer_size: int = 262_144
    tx_scale: float = 10_000.0
    tx_channel: int = 0
    rx_channel: int = 0
    gain_candidates: list[list[int]] = field(
        default_factory=lambda: [[-25, 20], [-20, 25], [-15, 30], [-10, 35], [-8, 40], [-6, 45], [-4, 50]]
    )
    session_retries: int = 5
    session_retry_delay: float = 2.0
    batch_packet_limit: int = 16
    waveform_score_threshold: float = 3.0
    capture_poll_delay_s: float = 0.01
    warmup_buffers: int = 2
    frame_payload_bytes: int = 60_000
    scenarios: dict[str, dict] = field(
        default_factory=lambda: {
            "loop_cable_nominal": {"tx_gain_db": -10, "rx_gain_db": 35},
            "low_power_path": {"tx_gain_db": -25, "rx_gain_db": 20},
            "high_margin_path": {"tx_gain_db": -6, "rx_gain_db": 45},
        }
    )

    def to_dict(self) -> dict:
        """Mengubah konfigurasi hardware ke dictionary serializable."""

        return asdict(self)


@dataclass
class PlutoRFTransferResult:
    """Ringkasan hasil transfer RF nyata untuk file dan skenario tertentu."""

    file_name: str
    file_size: int
    total_frames: int
    seconds: float
    throughput_bps: float
    mean_latency_ms: float
    scenario: str
    tx_gain_db: int
    rx_gain_db: int
    sha256: str
    output_path: str

    def to_dict(self) -> dict:
        """Mengubah hasil transfer RF menjadi dictionary serializable."""

        return asdict(self)


def load_pluto_rf_config(path: str | Path) -> PlutoRFHardwareConfig:
    """Memuat file YAML konfigurasi PlutoSDR untuk optimized transfer RF."""

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    section = raw.get("pluto_rf", raw)
    return PlutoRFHardwareConfig(
        uri=section.get("uri", "usb:0.1.5"),
        sample_rate=section.get("sample_rate", 4_000_000),
        center_frequency=section.get("center_frequency", 2_400_000_000),
        rf_bandwidth=section.get("rf_bandwidth", 3_000_000),
        carrier_offset=section.get("carrier_offset", 750_000),
        rx_buffer_size=section.get("rx_buffer_size", 262_144),
        tx_scale=section.get("tx_scale", 10_000.0),
        tx_channel=section.get("tx_channel", 0),
        rx_channel=section.get("rx_channel", 0),
        gain_candidates=section.get(
            "gain_candidates",
            [[-25, 20], [-20, 25], [-15, 30], [-10, 35], [-8, 40], [-6, 45], [-4, 50]],
        ),
        session_retries=section.get("session_retries", 5),
        session_retry_delay=section.get("session_retry_delay", 2.0),
        batch_packet_limit=section.get("batch_packet_limit", 16),
        waveform_score_threshold=section.get("waveform_score_threshold", 3.0),
        capture_poll_delay_s=section.get("capture_poll_delay_s", 0.01),
        warmup_buffers=section.get("warmup_buffers", 2),
        frame_payload_bytes=section.get("frame_payload_bytes", 60_000),
        scenarios=section.get(
            "scenarios",
            {
                "loop_cable_nominal": {"tx_gain_db": -10, "rx_gain_db": 35},
                "low_power_path": {"tx_gain_db": -25, "rx_gain_db": 20},
                "high_margin_path": {"tx_gain_db": -6, "rx_gain_db": 45},
            },
        ),
    )


class PlutoSDRDriver:
    """Adapter PlutoSDR langsung untuk optimized_transfer di link RF nyata."""

    def __init__(self, hardware: PlutoRFHardwareConfig, modulation: str = "qpsk") -> None:
        """Menyiapkan wrapper hardware dan modem OFDM yang kompatibel dengan Pluto."""

        self.hardware = hardware
        self.modulation = modulation
        self.legacy_config = SDRConfig(
            uri=hardware.uri,
            sample_rate=hardware.sample_rate,
            center_frequency=hardware.center_frequency,
            rf_bandwidth=hardware.rf_bandwidth,
            carrier_offset=hardware.carrier_offset,
            tx_gain_db=-10,
            rx_gain_db=35,
            rx_buffer_size=hardware.rx_buffer_size,
            tx_scale=hardware.tx_scale,
            tx_channel=hardware.tx_channel,
            rx_channel=hardware.rx_channel,
        )
        self.modem = OFDMModem(config=self.legacy_config, payload_modulation=modulation)
        self.tx_sdr: adi.ad9361 | None = None
        self.rx_sdr: adi.ad9361 | None = None
        self.tx_lock = threading.Lock()
        self.rx_lock = self.tx_lock

    def connect(self) -> None:
        """Membuka device Pluto melalui pyadi-iio dan menerapkan parameter RF dasar."""

        sdr = adi.ad9361(uri=self.hardware.uri)
        sdr.sample_rate = int(self.hardware.sample_rate)
        sdr.tx_lo = int(self.hardware.center_frequency)
        sdr.rx_lo = int(self.hardware.center_frequency)
        sdr.tx_rf_bandwidth = int(self.hardware.rf_bandwidth)
        sdr.rx_rf_bandwidth = int(self.hardware.rf_bandwidth)
        sdr.tx_enabled_channels = [self.hardware.tx_channel]
        sdr.rx_enabled_channels = [self.hardware.rx_channel]
        sdr.gain_control_mode_chan0 = "manual"
        sdr.rx_buffer_size = int(self.hardware.rx_buffer_size)
        sdr.tx_hardwaregain_chan0 = -10
        sdr.rx_hardwaregain_chan0 = 35
        sdr.tx_cyclic_buffer = True
        self.tx_sdr = sdr
        self.rx_sdr = sdr

    def warmup_link(self, attempts: int = 6) -> None:
        """Membangunkan jalur TX/RX Pluto sampai capture RX menunjukkan energi nonzero."""

        tone = (self.hardware.tx_scale * 0.35 * np.exp(1j * 2 * np.pi * 0.125 * np.arange(4096))).astype(np.complex64)
        for _ in range(attempts):
            self.start_waveform(tone)
            time.sleep(0.05)
            samples = self.capture(self.hardware.rx_buffer_size)
            if samples.size and float(np.max(np.abs(samples))) > 0.0:
                return
            time.sleep(0.1)
        raise TransferError("Link RF PlutoSDR tidak aktif setelah warmup")

    def close(self) -> None:
        """Menutup buffer TX/RX agar state driver tetap bersih antar sesi."""

        sdr = self.tx_sdr
        if sdr is not None:
            try:
                sdr.tx_destroy_buffer()
            except Exception:
                pass
            try:
                sdr.rx_destroy_buffer()
            except Exception:
                pass
        self.tx_sdr = None
        self.rx_sdr = None

    def _require_tx(self) -> adi.ad9361:
        """Memastikan konteks TX tersedia untuk pengiriman waveform ke Pluto."""

        if self.tx_sdr is None:
            raise TransferError("TX PlutoSDR belum terkoneksi")
        return self.tx_sdr

    def _require_rx(self) -> adi.ad9361:
        """Memastikan konteks RX tersedia untuk capture sampel dari Pluto."""

        if self.rx_sdr is None:
            raise TransferError("RX PlutoSDR belum terkoneksi")
        return self.rx_sdr

    def set_gains(self, tx_gain_db: int, rx_gain_db: int) -> None:
        """Mengatur gain TX/RX pada device Pluto sesuai profil link yang dipilih."""

        tx_sdr = self._require_tx()
        rx_sdr = self._require_rx()
        with self.tx_lock:
            tx_sdr.tx_hardwaregain_chan0 = int(tx_gain_db)
        with self.rx_lock:
            rx_sdr.rx_hardwaregain_chan0 = int(rx_gain_db)

    def encode_rf_packet(self, frame_bytes: bytes, message_type: int, sequence: int, total: int, file_size: int, transfer_id: int) -> np.ndarray:
        """Mengubah satu frame optimized_transfer menjadi waveform RF OFDM yang siap kirim."""

        packet = Packet(
            message_type=message_type,
            sequence=sequence,
            total=total,
            payload=frame_bytes,
            file_size=file_size,
            transfer_id=transfer_id,
        )
        return self.modem.encode_packet(packet)

    def start_waveform(self, waveform: np.ndarray) -> None:
        """Memulai transmisi cyclic untuk satu waveform atau batch waveform RF."""

        sdr = self._require_tx()
        with self.tx_lock:
            try:
                sdr.tx_destroy_buffer()
            except Exception:
                pass
            sdr.tx_cyclic_buffer = True
            sdr.tx(waveform.astype(np.complex64))

    def capture(self, capture_samples: int) -> np.ndarray:
        """Mengambil sampel RX dari Pluto untuk didekode receiver thread."""

        sdr = self._require_rx()
        buffers = max(1, int(math.ceil(capture_samples / self.hardware.rx_buffer_size)))
        for _ in range(self.hardware.warmup_buffers):
            with self.rx_lock:
                sdr.rx()
        chunks = []
        for _ in range(buffers):
            with self.rx_lock:
                chunks.append(np.asarray(sdr.rx(), dtype=np.complex128))
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.complex128)

    def waveform_score(self, samples: np.ndarray, waveform: np.ndarray) -> tuple[float, int]:
        """Mengukur skor korelasi sebuah waveform referensi terhadap capture RX."""

        best_score = 0.0
        best_index = 0
        for ref in [waveform.astype(np.complex128), np.conj(waveform.astype(np.complex128))]:
            if samples.size < ref.size:
                continue
            nfft = 1 << int(math.ceil(math.log2(samples.size + ref.size - 1)))
            correlation_full = np.fft.ifft(np.fft.fft(samples, nfft) * np.fft.fft(np.conj(ref[::-1]), nfft))
            correlation = np.abs(correlation_full[ref.size - 1 : samples.size])
            if correlation.size == 0:
                continue
            peak = float(np.max(correlation))
            mean_value = float(np.mean(correlation)) + 1e-12
            score = peak / mean_value
            if score > best_score:
                best_score = score
                best_index = int(np.argmax(correlation))
        return best_score, best_index

    def calibrate(self, probe_waveform: np.ndarray) -> tuple[int, int, float]:
        """Mencari kombinasi gain terbaik sebelum transfer RF continuous dimulai."""

        best: tuple[float, tuple[int, int]] | None = None
        for tx_gain_db, rx_gain_db in self.hardware.gain_candidates:
            self.set_gains(tx_gain_db, rx_gain_db)
            self.start_waveform(probe_waveform)
            time.sleep(0.05)
            samples = self.capture(probe_waveform.size + self.hardware.rx_buffer_size)
            score, _ = self.waveform_score(samples, probe_waveform)
            if best is None or score > best[0]:
                best = (score, (tx_gain_db, rx_gain_db))
        if best is None or best[0] < self.hardware.waveform_score_threshold:
            raise TransferError("Kalibrasi link RF gagal pada PlutoSDR")
        self.set_gains(best[1][0], best[1][1])
        return best[1][0], best[1][1], best[0]

    def decode_packets(self, samples: np.ndarray, transfer_id: int) -> list[Packet]:
        """Mendeteksi dan mendekode semua paket RF valid yang muncul di capture RX."""

        decoded: list[Packet] = []
        seen: set[tuple[int, int, int]] = set()
        for candidate in self.modem.find_candidates(samples):
            try:
                packet = self.modem.decode_packet_at(samples, candidate).packet
            except Exception:
                continue
            if packet.transfer_id != transfer_id:
                continue
            key = (packet.message_type, packet.sequence, zlib_crc(packet.payload))
            if key in seen:
                continue
            seen.add(key)
            decoded.append(packet)
        return decoded


def zlib_crc(payload: bytes) -> int:
    """Menghitung CRC ringkas untuk deduplikasi paket RF pada receiver thread."""

    return int.from_bytes(hashlib.blake2s(payload, digest_size=4).digest(), "big")


class RFReceiverThread(threading.Thread):
    """Receiver thread RF nyata yang terisolasi dari sender untuk memproses paket."""

    def __init__(
        self,
        driver: PlutoSDRDriver,
        codec: FrameCodec,
        transfer_id: int,
        total_frames: int,
        file_size: int,
        output_path: Path,
        state: dict,
        stop_event: threading.Event,
    ) -> None:
        """Menyiapkan receiver thread dengan akses state ACK, chunk, dan telemetry."""

        super().__init__(daemon=True)
        self.driver = driver
        self.codec = codec
        self.transfer_id = transfer_id
        self.total_frames = total_frames
        self.file_size = file_size
        self.output_path = output_path
        self.state = state
        self.stop_event = stop_event

    def run(self) -> None:
        """Melakukan capture RX berulang, dekode packet RF, dan update ACK bitmap."""

        while not self.stop_event.is_set():
            samples = self.driver.capture(self.driver.hardware.rx_buffer_size)
            packets = self.driver.decode_packets(samples, self.transfer_id)
            for packet in packets:
                frames, _ = self.codec.decode_stream(packet.payload)
                for frame in frames:
                    if frame.frame_type == FrameType.START:
                        self.state["started"].set()
                    elif frame.frame_type == FrameType.DATA:
                        with self.state["lock"]:
                            if not self.state["ack_bitmap"].is_set(frame.sequence):
                                self.state["ack_bitmap"].set(frame.sequence)
                                self.state["chunks"][frame.sequence] = frame.payload
                                sent_at = self.state["sent_at"].get(frame.sequence)
                                if sent_at is not None:
                                    self.state["latencies_ms"].append((time.perf_counter() - sent_at) * 1000.0)
                    elif frame.frame_type == FrameType.FIN:
                        with self.state["lock"]:
                            self.state["fin_seen"] = True
                            digest = frame.payload.decode("ascii")
                            if self.state["ack_bitmap"].all_set():
                                assembled = b"".join(self.state["chunks"][index] for index in range(self.total_frames))
                                assembled = assembled[: self.file_size]
                                if hashlib.sha256(assembled).hexdigest() == digest:
                                    self.output_path.parent.mkdir(parents=True, exist_ok=True)
                                    self.output_path.write_bytes(assembled)
                                    self.state["finished"].set()
                                    self.stop_event.set()
            time.sleep(self.driver.hardware.capture_poll_delay_s)


class PlutoRFTransferEngine:
    """Engine optimized_transfer yang dihubungkan langsung ke PlutoSDR/pyadi-iio."""

    def __init__(self, hardware: PlutoRFHardwareConfig, modulation: str = "qpsk") -> None:
        """Menyiapkan codec framing dan driver PlutoSDR untuk transfer RF nyata."""

        self.hardware = hardware
        self.modulation = modulation
        self.codec = FrameCodec()
        self.driver = PlutoSDRDriver(hardware=hardware, modulation=modulation)

    def _frame_payloads(self, payload: bytes) -> list[bytes]:
        """Memecah file menjadi payload frame ringan sesuai kapasitas chunk RF."""

        step = self.hardware.frame_payload_bytes
        frame_payloads = []
        for sequence, chunk in enumerate(payload[index : index + step] for index in range(0, len(payload), step)):
            frame_payloads.append(self.codec.build_data(0, sequence, chunk))
        return frame_payloads

    def _scenario_gains(self, scenario: str) -> tuple[int, int]:
        """Mengambil pasangan gain dasar dari skenario RF yang dipilih."""

        profile = self.hardware.scenarios.get(scenario, {})
        return int(profile.get("tx_gain_db", -10)), int(profile.get("rx_gain_db", 35))

    def send_file(self, input_path: str | Path, output_dir: str | Path, scenario: str = "loop_cable_nominal") -> PlutoRFTransferResult:
        """Mengirim file lewat PlutoSDR pada jalur RF nyata memakai receiver thread."""

        input_path = Path(input_path)
        payload = input_path.read_bytes()
        frame_chunks = [
            payload[index : index + self.hardware.frame_payload_bytes]
            for index in range(0, len(payload), self.hardware.frame_payload_bytes)
        ]
        total_frames = len(frame_chunks)
        stream_id = (time.time_ns() ^ len(payload) ^ total_frames) & 0xFFFFFFFF
        digest = hashlib.sha256(payload).hexdigest()
        manifest = {
            "file_name": input_path.name,
            "file_size": len(payload),
            "sha256": digest,
            "total_frames": total_frames,
            "stream_id": stream_id,
            "scenario": scenario,
            "modulation": self.modulation,
            "sample_rate": self.hardware.sample_rate,
            "center_frequency": self.hardware.center_frequency,
            "rf_bandwidth": self.hardware.rf_bandwidth,
            "carrier_offset": self.hardware.carrier_offset,
        }
        start_frame = self.codec.build_start(stream_id, manifest)
        data_frames = [self.codec.build_data(stream_id, sequence, chunk) for sequence, chunk in enumerate(frame_chunks)]
        fin_frame = self.codec.build_fin(stream_id, digest)
        output_path = Path(output_dir) / input_path.name
        last_error: Exception | None = None
        for attempt in range(1, self.hardware.session_retries + 1):
            ack_bitmap = AckBitmap(total_frames)
            chunks: dict[int, bytes] = {}
            sent_at: dict[int, float] = {}
            latencies_ms: list[float] = []
            try:
                self.driver.connect()
                self.driver.warmup_link()
                scenario_tx_gain, scenario_rx_gain = self._scenario_gains(scenario)
                self.driver.set_gains(scenario_tx_gain, scenario_rx_gain)
                probe_waveform = self.driver.encode_rf_packet(start_frame, MSG_HELLO, 0, total_frames, len(payload), stream_id)
                try:
                    tx_gain_db, rx_gain_db, _ = self.driver.calibrate(probe_waveform)
                except Exception:
                    tx_gain_db, rx_gain_db = scenario_tx_gain, scenario_rx_gain
                    self.driver.set_gains(tx_gain_db, rx_gain_db)
                self.driver.start_waveform(probe_waveform)
                time.sleep(0.2)
                started = time.perf_counter()
                inflight: dict[int, float] = {}
                next_sequence = 0
                while not ack_bitmap.all_set():
                    for sequence in list(inflight):
                        if ack_bitmap.is_set(sequence):
                            inflight.pop(sequence, None)
                    selected = []
                    while next_sequence < total_frames and len(inflight) + len(selected) < self.hardware.batch_packet_limit:
                        selected.append(next_sequence)
                        next_sequence += 1
                    if not selected:
                        selected = ack_bitmap.missing_indexes()[: self.hardware.batch_packet_limit]
                    if not selected:
                        time.sleep(self.hardware.capture_poll_delay_s)
                        continue
                    batch_waveforms = []
                    now = time.perf_counter()
                    for sequence in selected:
                        frame_bytes = data_frames[sequence]
                        batch_waveforms.append(
                            self.driver.encode_rf_packet(frame_bytes, MSG_DATA, sequence, total_frames, len(payload), stream_id)
                        )
                        inflight[sequence] = now
                        sent_at[sequence] = now
                    self.driver.start_waveform(np.concatenate(batch_waveforms))
                    time.sleep(self.hardware.capture_poll_delay_s)
                    samples = self.driver.capture(self.hardware.rx_buffer_size)
                    packets = self.driver.decode_packets(samples, stream_id)
                    for packet in packets:
                        frames, _ = self.codec.decode_stream(packet.payload)
                        for frame in frames:
                            if frame.frame_type == FrameType.DATA and 0 <= frame.sequence < total_frames and not ack_bitmap.is_set(frame.sequence):
                                ack_bitmap.set(frame.sequence)
                                chunks[frame.sequence] = frame.payload
                                if frame.sequence in sent_at:
                                    latencies_ms.append((time.perf_counter() - sent_at[frame.sequence]) * 1000.0)
                fin_waveform = self.driver.encode_rf_packet(fin_frame, MSG_FIN, 0, total_frames, len(payload), stream_id)
                self.driver.start_waveform(fin_waveform)
                time.sleep(self.hardware.capture_poll_delay_s)
                elapsed = time.perf_counter() - started
                assembled = b"".join(chunks[index] for index in range(total_frames))
                assembled = assembled[: len(payload)]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(assembled)
                received = assembled
                received_digest = hashlib.sha256(received).hexdigest()
                if received_digest != digest:
                    raise TransferError("Digest file hasil transfer RF tidak cocok")
                throughput_bps = len(payload) * 8 / elapsed
                mean_latency_ms = float(sum(latencies_ms) / max(1, len(latencies_ms)))
                return PlutoRFTransferResult(
                    file_name=input_path.name,
                    file_size=len(payload),
                    total_frames=total_frames,
                    seconds=elapsed,
                    throughput_bps=throughput_bps,
                    mean_latency_ms=mean_latency_ms,
                    scenario=scenario,
                    tx_gain_db=tx_gain_db,
                    rx_gain_db=rx_gain_db,
                    sha256=received_digest,
                    output_path=str(output_path),
                )
            except Exception as exc:
                last_error = exc
                time.sleep(self.hardware.session_retry_delay * attempt)
            finally:
                self.driver.close()
        raise TransferError(str(last_error) if last_error else "Transfer RF optimized gagal")

    def run_scenarios(self, input_path: str | Path, output_dir: str | Path) -> list[PlutoRFTransferResult]:
        """Menjalankan pengujian berulang pada beberapa skenario channel RF berbeda."""

        results = []
        for scenario in self.hardware.scenarios:
            results.append(self.send_file(input_path=input_path, output_dir=output_dir, scenario=scenario))
        return results
