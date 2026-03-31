from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import adi
import numpy as np


MAGIC = b"RFX1"
VERSION = 1
MSG_HELLO = 1
MSG_DATA = 2
MSG_FIN = 3


class TransferError(RuntimeError):
    pass


@dataclass
class SDRConfig:
    uri: str = "usb:0.1.5"
    sample_rate: int = 4_000_000
    center_frequency: int = 2_400_000_000
    rf_bandwidth: int = 3_000_000
    carrier_offset: int = 750_000
    tx_gain_db: int = -10
    rx_gain_db: int = 40
    rx_buffer_size: int = 262_144
    tx_scale: float = 10_000.0
    tx_channel: int = 0
    rx_channel: int = 0


@dataclass
class TransferMetadata:
    file_name: str
    file_size: int
    file_sha256: str
    chunk_size: int
    packet_count: int
    modulation: str
    transfer_id: int


@dataclass
class Packet:
    message_type: int
    sequence: int
    total: int
    payload: bytes
    file_size: int
    transfer_id: int


@dataclass
class PacketDecode:
    packet: Packet
    metric: float


@dataclass
class TransferStats:
    raw_bit_rate_bps: float
    useful_bit_rate_bps: float
    air_payload_bit_rate_bps: float
    packets_sent: int
    retries: int
    seconds: float


def bytes_to_bits(data: bytes) -> np.ndarray:
    if not data:
        return np.zeros(0, dtype=np.uint8)
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def bits_to_bytes(bits: np.ndarray, byte_length: int | None = None) -> bytes:
    if bits.size == 0:
        return b""
    rounded = int(math.ceil(bits.size / 8.0) * 8)
    if rounded != bits.size:
        bits = np.pad(bits, (0, rounded - bits.size))
    data = np.packbits(bits).tobytes()
    if byte_length is None:
        return data
    return data[:byte_length]


class OFDMModem:
    def __init__(self, config: SDRConfig, payload_modulation: str = "qpsk") -> None:
        if payload_modulation not in {"bpsk", "qpsk"}:
            raise ValueError("payload_modulation harus bpsk atau qpsk")
        self.config = config
        self.payload_modulation = payload_modulation
        self.fft_len = 64
        self.cp_len = 16
        self.guard_len = 32
        self.subcarriers = np.array(list(range(-26, 0)) + list(range(1, 27)), dtype=np.int32)
        self.bin_index = (self.subcarriers % self.fft_len).astype(np.int32)
        self.header_bytes = 32
        self.header_bits_per_symbol = self.subcarriers.size
        self.payload_bits_per_symbol = self.subcarriers.size * (2 if payload_modulation == "qpsk" else 1)
        self.header_symbol_count = int(math.ceil(self.header_bytes * 8 / self.header_bits_per_symbol))
        rng = np.random.default_rng(20260331)
        training_bits = rng.integers(0, 2, self.subcarriers.size, dtype=np.uint8)
        self.training_freq = (2 * training_bits - 1).astype(np.complex128)
        self.training_time = self._ifft_carriers(self.training_freq)
        self.preamble_baseband = np.concatenate(
            [self.training_time[-self.cp_len :], self.training_time, self.training_time]
        ).astype(np.complex128)
        n = np.arange(self.preamble_baseband.size, dtype=np.float64)
        self.preamble = self.preamble_baseband * np.exp(
            1j * 2 * np.pi * self.config.carrier_offset * n / self.config.sample_rate
        )

    @property
    def symbol_samples(self) -> int:
        return self.fft_len + self.cp_len

    @property
    def preamble_samples(self) -> int:
        return self.preamble_baseband.size

    def _ifft_carriers(self, carriers: np.ndarray) -> np.ndarray:
        freq = np.zeros(self.fft_len, dtype=np.complex128)
        freq[self.bin_index] = carriers
        return np.fft.ifft(freq) * np.sqrt(self.fft_len)

    def _fft_symbol(self, samples: np.ndarray) -> np.ndarray:
        return np.fft.fft(samples) / np.sqrt(self.fft_len)

    def _bpsk_map(self, bits: np.ndarray) -> np.ndarray:
        return (2 * bits.astype(np.float64) - 1).astype(np.complex128)

    def _bpsk_demap(self, symbols: np.ndarray) -> np.ndarray:
        return (symbols.real >= 0).astype(np.uint8)

    def _qpsk_map(self, bits: np.ndarray) -> np.ndarray:
        bits = bits.astype(np.uint8)
        if bits.size % 2:
            bits = np.pad(bits, (0, 1))
        pairs = bits.reshape(-1, 2)
        real = 2 * pairs[:, 0].astype(np.float64) - 1
        imag = 2 * pairs[:, 1].astype(np.float64) - 1
        return (real + 1j * imag) / np.sqrt(2.0)

    def _qpsk_demap(self, symbols: np.ndarray) -> np.ndarray:
        out = np.empty(symbols.size * 2, dtype=np.uint8)
        out[0::2] = (symbols.real >= 0).astype(np.uint8)
        out[1::2] = (symbols.imag >= 0).astype(np.uint8)
        return out

    def _modulate_bits(self, bits: np.ndarray, scheme: str, bits_per_symbol: int) -> tuple[np.ndarray, int]:
        pad_bits = (-bits.size) % bits_per_symbol
        if pad_bits:
            bits = np.pad(bits, (0, pad_bits))
        if scheme == "bpsk":
            carriers = self._bpsk_map(bits).reshape(-1, self.subcarriers.size)
        else:
            carriers = self._qpsk_map(bits).reshape(-1, self.subcarriers.size)
        time_symbols = []
        for carrier_row in carriers:
            symbol_td = self._ifft_carriers(carrier_row)
            time_symbols.append(np.concatenate([symbol_td[-self.cp_len :], symbol_td]))
        if not time_symbols:
            return np.zeros(0, dtype=np.complex128), 0
        return np.concatenate(time_symbols).astype(np.complex128), pad_bits

    def _demodulate_symbols(
        self,
        samples: np.ndarray,
        symbol_count: int,
        channel: np.ndarray,
        scheme: str,
    ) -> np.ndarray:
        bits = []
        for index in range(symbol_count):
            start = index * self.symbol_samples
            symbol = samples[start + self.cp_len : start + self.cp_len + self.fft_len]
            if symbol.size != self.fft_len:
                raise TransferError("Sampel OFDM tidak lengkap")
            freq = self._fft_symbol(symbol)
            equalized = freq[self.bin_index] / channel
            if scheme == "bpsk":
                bits.append(self._bpsk_demap(equalized))
            else:
                bits.append(self._qpsk_demap(equalized))
        if not bits:
            return np.zeros(0, dtype=np.uint8)
        return np.concatenate(bits).astype(np.uint8)

    def encode_packet(self, packet: Packet) -> np.ndarray:
        payload_crc = zlib.crc32(packet.payload) & 0xFFFFFFFF
        payload_digest = int.from_bytes(hashlib.sha256(packet.payload).digest()[:4], "big")
        header_without_crc = struct.pack(
            "!4sBBHHHIIIII",
            MAGIC,
            VERSION,
            packet.message_type,
            packet.sequence,
            packet.total,
            len(packet.payload),
            packet.transfer_id,
            packet.file_size,
            payload_crc,
            payload_digest,
            0,
        )
        header_crc = zlib.crc32(header_without_crc[:-4]) & 0xFFFFFFFF
        header = header_without_crc[:-4] + struct.pack("!I", header_crc)
        header_bits = bytes_to_bits(header)
        header_td, _ = self._modulate_bits(header_bits, "bpsk", self.header_bits_per_symbol)
        payload_bits = bytes_to_bits(packet.payload)
        payload_td, _ = self._modulate_bits(payload_bits, self.payload_modulation, self.payload_bits_per_symbol)
        baseband = np.concatenate(
            [
                np.zeros(self.guard_len, dtype=np.complex128),
                self.preamble_baseband,
                header_td,
                payload_td,
                np.zeros(self.guard_len, dtype=np.complex128),
            ]
        )
        n = np.arange(baseband.size, dtype=np.float64)
        rf = baseband * np.exp(1j * 2 * np.pi * self.config.carrier_offset * n / self.config.sample_rate)
        peak = np.max(np.abs(rf))
        if peak == 0:
            return rf.astype(np.complex64)
        scaled = rf * (self.config.tx_scale / peak)
        return scaled.astype(np.complex64)

    def packet_sample_length(self, payload_length: int) -> int:
        header_samples = self.header_symbol_count * self.symbol_samples
        payload_symbol_count = int(math.ceil(payload_length * 8 / self.payload_bits_per_symbol))
        payload_samples = payload_symbol_count * self.symbol_samples
        return self.guard_len + self.preamble_samples + header_samples + payload_samples + self.guard_len

    def _decode_downmixed(self, downmixed: np.ndarray) -> PacketDecode:
        training_1 = downmixed[self.cp_len : self.cp_len + self.fft_len]
        training_2 = downmixed[self.cp_len + self.fft_len : self.cp_len + 2 * self.fft_len]
        if training_1.size != self.fft_len or training_2.size != self.fft_len:
            raise TransferError("Preamble tidak lengkap")
        channel_1 = self._fft_symbol(training_1)[self.bin_index] / self.training_freq
        channel_2 = self._fft_symbol(training_2)[self.bin_index] / self.training_freq
        channel = (channel_1 + channel_2) / 2
        metric = float(np.mean(np.abs(channel)))
        payload_start = self.preamble_samples
        header_end = payload_start + self.header_symbol_count * self.symbol_samples
        header_bits = self._demodulate_symbols(
            downmixed[payload_start:header_end],
            self.header_symbol_count,
            channel,
            "bpsk",
        )
        header = bits_to_bytes(header_bits, self.header_bytes)
        unpacked = struct.unpack("!4sBBHHHIIIII", header)
        magic, version, message_type, sequence, total, payload_length, transfer_id, file_size, payload_crc, payload_digest, header_crc = unpacked
        if magic != MAGIC or version != VERSION:
            raise TransferError("Header magic tidak valid")
        if zlib.crc32(header[:-4]) & 0xFFFFFFFF != header_crc:
            raise TransferError("CRC header tidak valid")
        payload_symbol_count = int(math.ceil(payload_length * 8 / self.payload_bits_per_symbol))
        payload_end = header_end + payload_symbol_count * self.symbol_samples
        if downmixed.size < payload_end:
            raise TransferError("Payload tidak lengkap")
        payload_bits = self._demodulate_symbols(
            downmixed[header_end:payload_end],
            payload_symbol_count,
            channel,
            self.payload_modulation,
        )
        payload = bits_to_bytes(payload_bits, payload_length)
        if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc:
            raise TransferError("CRC payload tidak valid")
        if int.from_bytes(hashlib.sha256(payload).digest()[:4], "big") != payload_digest:
            raise TransferError("Digest payload tidak valid")
        packet = Packet(
            message_type=message_type,
            sequence=sequence,
            total=total,
            payload=payload,
            file_size=file_size,
            transfer_id=transfer_id,
        )
        return PacketDecode(packet=packet, metric=metric)

    def decode_packet_at(self, samples: np.ndarray, preamble_start: int) -> PacketDecode:
        if preamble_start < 0:
            raise TransferError("Offset paket negatif")
        base_required = preamble_start + self.preamble_samples + self.header_symbol_count * self.symbol_samples
        if samples.size < base_required:
            raise TransferError("Sampel header tidak lengkap")
        last_error: Exception | None = None
        for delta in range(-self.cp_len, self.cp_len + 1):
            start = preamble_start + delta
            if start < 0:
                continue
            n = np.arange(samples.size - start, dtype=np.float64)
            segment = samples[start:]
            variants = [
                segment * np.exp(-1j * 2 * np.pi * self.config.carrier_offset * n / self.config.sample_rate),
                segment * np.exp(1j * 2 * np.pi * self.config.carrier_offset * n / self.config.sample_rate),
                np.conj(segment) * np.exp(-1j * 2 * np.pi * self.config.carrier_offset * n / self.config.sample_rate),
                np.conj(segment) * np.exp(1j * 2 * np.pi * self.config.carrier_offset * n / self.config.sample_rate),
            ]
            for downmixed in variants:
                try:
                    return self._decode_downmixed(downmixed)
                except Exception as exc:
                    last_error = exc
        if last_error is not None:
            raise last_error
        raise TransferError("Dekode paket gagal")

    def find_candidates(self, samples: np.ndarray) -> list[int]:
        if samples.size < self.preamble.size:
            return []
        correlation = np.abs(np.convolve(samples, np.conj(self.preamble[::-1]), mode="valid"))
        if correlation.size == 0:
            return []
        mean_value = float(np.mean(correlation))
        max_value = float(np.max(correlation))
        threshold = max(mean_value * 6.0, max_value * 0.35)
        peaks: list[int] = []
        index = 1
        guard = max(self.preamble_samples // 2, 16)
        while index < correlation.size - 1:
            if correlation[index] >= threshold and correlation[index] >= correlation[index - 1] and correlation[index] > correlation[index + 1]:
                window_end = min(correlation.size, index + guard)
                local = index + int(np.argmax(correlation[index:window_end]))
                peaks.append(local)
                index = local + guard
            else:
                index += 1
        return peaks


class ReliableImageTransfer:
    def __init__(self, config: SDRConfig, modulation: str) -> None:
        self.config = config
        self.modem = OFDMModem(config=config, payload_modulation=modulation)
        self.sdr: adi.ad9361 | None = None

    def connect(self) -> None:
        sdr = adi.ad9361(uri=self.config.uri)
        sdr.sample_rate = int(self.config.sample_rate)
        sdr.tx_lo = int(self.config.center_frequency)
        sdr.rx_lo = int(self.config.center_frequency)
        sdr.tx_rf_bandwidth = int(self.config.rf_bandwidth)
        sdr.rx_rf_bandwidth = int(self.config.rf_bandwidth)
        sdr.tx_enabled_channels = [self.config.tx_channel]
        sdr.rx_enabled_channels = [self.config.rx_channel]
        sdr.gain_control_mode_chan0 = "manual"
        sdr.rx_hardwaregain_chan0 = int(self.config.rx_gain_db)
        sdr.tx_hardwaregain_chan0 = int(self.config.tx_gain_db)
        sdr.rx_buffer_size = int(self.config.rx_buffer_size)
        sdr.tx_cyclic_buffer = True
        self.sdr = sdr

    def close(self) -> None:
        if self.sdr is None:
            return
        try:
            self.sdr.tx_destroy_buffer()
        except Exception:
            pass
        try:
            self.sdr.rx_destroy_buffer()
        except Exception:
            pass
        self.sdr = None

    def _require_sdr(self) -> adi.ad9361:
        if self.sdr is None:
            raise TransferError("SDR belum terkoneksi")
        return self.sdr

    def _set_gains(self, tx_gain_db: int, rx_gain_db: int) -> None:
        sdr = self._require_sdr()
        self.config.tx_gain_db = tx_gain_db
        self.config.rx_gain_db = rx_gain_db
        sdr.tx_hardwaregain_chan0 = int(tx_gain_db)
        sdr.rx_hardwaregain_chan0 = int(rx_gain_db)

    def _start_cyclic_tx(self, waveform: np.ndarray) -> None:
        sdr = self._require_sdr()
        try:
            sdr.tx_destroy_buffer()
        except Exception:
            pass
        sdr.tx(waveform.astype(np.complex64))

    def _capture_samples(self, capture_samples: int, warmup_buffers: int = 1) -> np.ndarray:
        sdr = self._require_sdr()
        collected: list[np.ndarray] = []
        needed_buffers = max(1, int(math.ceil(capture_samples / self.config.rx_buffer_size)))
        for _ in range(warmup_buffers):
            sdr.rx()
        for _ in range(needed_buffers):
            collected.append(np.asarray(sdr.rx(), dtype=np.complex128))
        if not collected:
            return np.zeros(0, dtype=np.complex128)
        return np.concatenate(collected)

    def _decode_samples(self, samples: np.ndarray, transfer_id: int | None = None) -> list[PacketDecode]:
        found: dict[tuple[int, int, int], PacketDecode] = {}
        for candidate in self.modem.find_candidates(samples):
            try:
                decoded = self.modem.decode_packet_at(samples, candidate)
            except Exception:
                continue
            packet = decoded.packet
            if transfer_id is not None and packet.transfer_id != transfer_id:
                continue
            key = (packet.message_type, packet.sequence, zlib.crc32(packet.payload) & 0xFFFFFFFF)
            existing = found.get(key)
            if existing is None or decoded.metric > existing.metric:
                found[key] = decoded
        return list(found.values())

    def _waveform_score(self, samples: np.ndarray, waveform: np.ndarray) -> tuple[float, int]:
        best_score = 0.0
        best_index = 0
        refs = [waveform.astype(np.complex128), np.conj(waveform.astype(np.complex128))]
        for ref in refs:
            if samples.size < ref.size:
                continue
            nfft = 1 << int(math.ceil(math.log2(samples.size + ref.size - 1)))
            correlation_full = np.fft.ifft(
                np.fft.fft(samples, nfft) * np.fft.fft(np.conj(ref[::-1]), nfft)
            )
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

    def auto_calibrate(self, hello_waveform: np.ndarray) -> tuple[int, int, float]:
        candidates = [
            (-25, 20),
            (-20, 25),
            (-15, 30),
            (-10, 35),
            (-8, 40),
            (-6, 45),
            (-4, 50),
        ]
        best: tuple[float, tuple[int, int]] | None = None
        for tx_gain, rx_gain in candidates:
            self._set_gains(tx_gain, rx_gain)
            self._start_cyclic_tx(hello_waveform)
            capture_length = hello_waveform.size + self.config.rx_buffer_size
            samples = self._capture_samples(capture_length, warmup_buffers=2)
            score, _ = self._waveform_score(samples, hello_waveform)
            print(json.dumps({"stage": "calibration_probe", "tx_gain_db": tx_gain, "rx_gain_db": rx_gain, "score": score}))
            if best is None or score > best[0]:
                best = (score, (tx_gain, rx_gain))
        if best is None or best[0] < 3.0:
            raise TransferError("Kalibrasi link gagal. Coba periksa kabel loopback TX1 -> RX1 atau gain RF")
        self._set_gains(best[1][0], best[1][1])
        return best[1][0], best[1][1], best[0]

    def _transmit_window(self, packets: list[Packet], threshold_score: float) -> tuple[dict[int, bytes], int]:
        waveforms = {packet.sequence: self.modem.encode_packet(packet) for packet in packets}
        if not waveforms:
            return {}, 0
        waveform = np.concatenate(list(waveforms.values()))
        self._start_cyclic_tx(waveform)
        max_packet = max(item.size for item in waveforms.values())
        capture_length = waveform.size + max_packet + self.config.rx_buffer_size
        samples = self._capture_samples(capture_length, warmup_buffers=2)
        detected: dict[int, bytes] = {}
        for packet in packets:
            score, _ = self._waveform_score(samples, waveforms[packet.sequence])
            if score >= threshold_score:
                detected[packet.sequence] = packet.payload
        return detected, waveform.size

    def _build_metadata(self, input_path: Path, data: bytes, chunk_size: int, modulation: str) -> TransferMetadata:
        packet_count = int(math.ceil(len(data) / chunk_size))
        file_sha256 = hashlib.sha256(data).hexdigest()
        digest_prefix = int.from_bytes(hashlib.sha256(data).digest()[:4], "big")
        transfer_id = (digest_prefix ^ len(data) ^ packet_count ^ int(time.time())) & 0xFFFFFFFF
        return TransferMetadata(
            file_name=input_path.name,
            file_size=len(data),
            file_sha256=file_sha256,
            chunk_size=chunk_size,
            packet_count=packet_count,
            modulation=modulation,
            transfer_id=transfer_id,
        )

    def transfer_file(
        self,
        input_path: Path,
        output_dir: Path,
        chunk_size: int = 60_000,
        window_packets: int = 4,
        max_retries: int = 20,
    ) -> tuple[Path, TransferStats]:
        if chunk_size <= 0 or chunk_size > 65_535:
            raise TransferError("chunk_size harus berada pada rentang 1..65535")
        data = input_path.read_bytes()
        metadata = self._build_metadata(input_path, data, chunk_size, self.modem.payload_modulation)
        hello_payload = json.dumps(
            {
                "file_name": metadata.file_name,
                "file_size": metadata.file_size,
                "file_sha256": metadata.file_sha256,
                "chunk_size": metadata.chunk_size,
                "packet_count": metadata.packet_count,
                "modulation": metadata.modulation,
                "sample_rate": self.config.sample_rate,
                "carrier_offset": self.config.carrier_offset,
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
        hello_waveform = self.modem.encode_packet(hello_packet)
        print(json.dumps({"stage": "hello_waveform", "samples": int(hello_waveform.size), "peak": float(np.max(np.abs(hello_waveform)))}))
        selected_tx_gain, selected_rx_gain, hello_score = self.auto_calibrate(hello_waveform)
        print(json.dumps({"stage": "calibrated", "tx_gain_db": selected_tx_gain, "rx_gain_db": selected_rx_gain, "hello_score": hello_score}))
        self._start_cyclic_tx(hello_waveform)
        handshake_samples = self._capture_samples(hello_waveform.size + self.config.rx_buffer_size, warmup_buffers=2)
        handshake_score, _ = self._waveform_score(handshake_samples, hello_waveform)
        if handshake_score < max(3.0, hello_score * 0.75):
            raise TransferError("Handshake HELLO gagal dideteksi receiver")
        print(json.dumps({"stage": "handshake_hello", "score": handshake_score}))
        chunks = [
            data[index : index + metadata.chunk_size]
            for index in range(0, metadata.file_size, metadata.chunk_size)
        ]
        received: dict[int, bytes] = {}
        retries = 0
        total_waveform_samples = hello_waveform.size
        data_waveform_samples = 0
        start_time = time.perf_counter()
        while len(received) < metadata.packet_count:
            missing = [index for index in range(metadata.packet_count) if index not in received]
            selected = missing[:window_packets]
            window = [
                Packet(
                    message_type=MSG_DATA,
                    sequence=sequence,
                    total=metadata.packet_count,
                    payload=chunks[sequence],
                    file_size=metadata.file_size,
                    transfer_id=metadata.transfer_id,
                )
                for sequence in selected
            ]
            decoded, waveform_samples = self._transmit_window(window, threshold_score=max(3.0, hello_score * 0.7))
            total_waveform_samples += waveform_samples
            data_waveform_samples += waveform_samples
            progress_before = len(received)
            for sequence, payload in decoded.items():
                if sequence < metadata.packet_count:
                    received[sequence] = payload
            print(
                json.dumps(
                    {
                        "stage": "data_window",
                        "received_packets": len(received),
                        "total_packets": metadata.packet_count,
                        "window_packets": selected,
                        "detected_packets": sorted(decoded.keys()),
                    }
                )
            )
            if len(received) == progress_before:
                retries += 1
            else:
                retries = 0
            if retries > max_retries:
                raise TransferError("Paket hilang terus-menerus. Transfer dihentikan karena melebihi retry maksimum")
        fin_packet = Packet(
            message_type=MSG_FIN,
            sequence=0,
            total=metadata.packet_count,
            payload=metadata.file_sha256.encode("ascii"),
            file_size=metadata.file_size,
            transfer_id=metadata.transfer_id,
        )
        fin_waveform = self.modem.encode_packet(fin_packet)
        fin_score = 0.0
        for _ in range(5):
            self._start_cyclic_tx(fin_waveform)
            fin_samples = self._capture_samples(fin_waveform.size + self.config.rx_buffer_size, warmup_buffers=2)
            fin_score, _ = self._waveform_score(fin_samples, fin_waveform)
            if fin_score >= max(3.0, hello_score * 0.5):
                break
        if fin_score < max(3.0, hello_score * 0.5):
            raise TransferError("Handshake FIN gagal")
        print(json.dumps({"stage": "handshake_fin", "score": fin_score}))
        elapsed = time.perf_counter() - start_time
        assembled = b"".join(received[index] for index in range(metadata.packet_count))
        assembled = assembled[: metadata.file_size]
        digest = hashlib.sha256(assembled).hexdigest()
        if digest != metadata.file_sha256:
            raise TransferError("Integritas file gagal setelah reassembly")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / input_path.name
        output_path.write_bytes(assembled)
        useful_bit_rate_bps = metadata.file_size * 8 / elapsed
        raw_bit_rate_bps = total_waveform_samples * 2 * 16 / elapsed
        air_payload_bit_rate_bps = metadata.file_size * 8 / (data_waveform_samples / self.config.sample_rate)
        stats = TransferStats(
            raw_bit_rate_bps=raw_bit_rate_bps,
            useful_bit_rate_bps=useful_bit_rate_bps,
            air_payload_bit_rate_bps=air_payload_bit_rate_bps,
            packets_sent=metadata.packet_count,
            retries=retries,
            seconds=elapsed,
        )
        result = {
            "file_name": metadata.file_name,
            "file_size": metadata.file_size,
            "file_sha256": metadata.file_sha256,
            "packet_count": metadata.packet_count,
            "chunk_size": metadata.chunk_size,
            "modulation": metadata.modulation,
            "sample_rate": self.config.sample_rate,
            "carrier_offset": self.config.carrier_offset,
            "tx_gain_db": selected_tx_gain,
            "rx_gain_db": selected_rx_gain,
            "useful_bit_rate_bps": useful_bit_rate_bps,
            "raw_bit_rate_bps": raw_bit_rate_bps,
            "air_payload_bit_rate_bps": air_payload_bit_rate_bps,
            "seconds": elapsed,
        }
        (output_dir / "transfer_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return output_path, stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/Users/mm/GitHub/radio_fix/input.jpg")
    parser.add_argument("--output-dir", default="/Users/mm/GitHub/radio_fix/receive")
    parser.add_argument("--uri", default="usb:0.1.5")
    parser.add_argument("--modulation", choices=["bpsk", "qpsk"], default="qpsk")
    parser.add_argument("--sample-rate", type=int, default=4_000_000)
    parser.add_argument("--center-frequency", type=int, default=2_400_000_000)
    parser.add_argument("--rf-bandwidth", type=int, default=3_000_000)
    parser.add_argument("--carrier-offset", type=int, default=750_000)
    parser.add_argument("--chunk-size", type=int, default=60_000)
    parser.add_argument("--window-packets", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=20)
    parser.add_argument("--session-retries", type=int, default=5)
    parser.add_argument("--session-retry-delay", type=float, default=2.0)
    args = parser.parse_args()
    config = SDRConfig(
        uri=args.uri,
        sample_rate=args.sample_rate,
        center_frequency=args.center_frequency,
        rf_bandwidth=args.rf_bandwidth,
        carrier_offset=args.carrier_offset,
    )
    last_error: Exception | None = None
    for attempt in range(1, args.session_retries + 1):
        transfer = ReliableImageTransfer(config=config, modulation=args.modulation)
        try:
            print(json.dumps({"stage": "session_attempt", "attempt": attempt, "max_attempts": args.session_retries}))
            transfer.connect()
            output_path, stats = transfer.transfer_file(
                input_path=Path(args.input),
                output_dir=Path(args.output_dir),
                chunk_size=args.chunk_size,
                window_packets=args.window_packets,
                max_retries=args.max_retries,
            )
            print(json.dumps(
                {
                    "status": "ok",
                    "output": str(output_path),
                    "useful_bit_rate_bps": stats.useful_bit_rate_bps,
                    "raw_bit_rate_bps": stats.raw_bit_rate_bps,
                    "air_payload_bit_rate_bps": stats.air_payload_bit_rate_bps,
                    "packets_sent": stats.packets_sent,
                    "retries": stats.retries,
                    "seconds": stats.seconds,
                    "attempt": attempt,
                },
                indent=2,
            ))
            return 0
        except Exception as exc:
            last_error = exc
            print(json.dumps({"stage": "session_error", "attempt": attempt, "message": str(exc)}))
            if attempt < args.session_retries:
                time.sleep(args.session_retry_delay * attempt)
        finally:
            transfer.close()
    print(json.dumps({"status": "error", "message": str(last_error) if last_error else "Transfer gagal"}, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
