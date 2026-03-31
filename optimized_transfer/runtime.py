from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .bitmap import AckBitmap
from .config import LinkRuntimeConfig
from .framing import FrameCodec, FrameType


@dataclass
class TransferBenchmarkResult:
    """Hasil benchmark atau transfer end-to-end untuk arsitektur baru."""

    file_name: str
    file_size: int
    total_frames: int
    seconds: float
    end_to_end_bps: float
    target_bps: int
    meets_target: bool
    sha256: str
    output_path: str

    def to_dict(self) -> dict:
        """Mengubah hasil benchmark ke dictionary untuk serialisasi JSON."""

        return asdict(self)


@dataclass
class DuplexEndpoint:
    """Endpoint duplex berbasis multiprocessing queue untuk TX dan RX terisolasi."""

    tx_queue: mp.Queue
    rx_queue: mp.Queue

    def send(self, payload: bytes) -> None:
        """Mengirim bytes ke arah lawan pada channel in-memory."""

        self.tx_queue.put(payload)

    def recv(self, timeout: float = 0.01) -> bytes | None:
        """Menerima bytes dari sisi lawan dengan timeout singkat non-blocking."""

        try:
            return self.rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None


class InMemoryTransferLink:
    """Link duplex simulasi untuk memvalidasi arsitektur throughput tinggi."""

    def __init__(self, sender: DuplexEndpoint, receiver: DuplexEndpoint) -> None:
        """Menyimpan pasangan endpoint sender dan receiver dalam satu link."""

        self.sender = sender
        self.receiver = receiver

    @classmethod
    def create(cls, depth: int) -> "InMemoryTransferLink":
        """Membangun dua endpoint queue duplex dengan kapasitas buffer tertentu."""

        forward = mp.Queue(maxsize=depth)
        backward = mp.Queue(maxsize=depth)
        return cls(
            sender=DuplexEndpoint(tx_queue=forward, rx_queue=backward),
            receiver=DuplexEndpoint(tx_queue=backward, rx_queue=forward),
        )


@dataclass
class AckState:
    """State sinkronisasi ACK/NACK yang dibagi antara sender dan ack listener."""

    acked: AckBitmap
    missing: AckBitmap
    lock: threading.Lock = field(default_factory=threading.Lock)

    def merge_ack(self, bitmap: AckBitmap) -> None:
        """Menggabungkan bitmap ACK baru ke state sender secara thread-safe."""

        with self.lock:
            self.acked.merge(bitmap)
            self.missing = self.acked.invert()

    def set_missing(self, bitmap: AckBitmap) -> None:
        """Menyimpan bitmap NACK terbaru agar sender bisa retransmit selektif."""

        with self.lock:
            self.missing = bitmap

    def snapshot(self) -> tuple[AckBitmap, AckBitmap]:
        """Mengambil salinan state ACK dan NACK untuk loop streaming sender."""

        with self.lock:
            ack = AckBitmap.from_bytes(self.acked.size, self.acked.to_bytes())
            missing = AckBitmap.from_bytes(self.missing.size, self.missing.to_bytes())
        return ack, missing


class ReceiverProcess(mp.Process):
    """Receiver terisolasi di proses terpisah untuk menghilangkan bottleneck I/O sender."""

    def __init__(self, endpoint: DuplexEndpoint, output_dir: str | Path, config: LinkRuntimeConfig) -> None:
        """Menyiapkan proses receiver dengan endpoint duplex dan konfigurasi runtime."""

        super().__init__(daemon=True)
        self.endpoint = endpoint
        self.output_dir = str(output_dir)
        self.config = config

    def run(self) -> None:
        """Menjalankan loop penerimaan, framing, ACK bitmap, dan penulisan file."""

        codec = FrameCodec()
        buffer = b""
        manifest: dict | None = None
        received_bitmap: AckBitmap | None = None
        chunks: dict[int, bytes] = {}
        fin_digest: str | None = None
        last_ack_time = time.monotonic()
        frames_since_ack = 0
        while True:
            chunk = self.endpoint.recv(timeout=0.01)
            if chunk is not None:
                buffer += chunk
            frames, leftover = codec.decode_stream(buffer)
            buffer = leftover
            for frame in frames:
                if frame.frame_type == FrameType.START:
                    manifest = json.loads(frame.payload.decode("utf-8"))
                    received_bitmap = AckBitmap(manifest["total_frames"])
                    chunks.clear()
                    fin_digest = None
                    frames_since_ack = 0
                elif frame.frame_type == FrameType.DATA and manifest is not None and received_bitmap is not None:
                    if 0 <= frame.sequence < received_bitmap.size and not received_bitmap.is_set(frame.sequence):
                        received_bitmap.set(frame.sequence)
                        chunks[frame.sequence] = frame.payload
                        frames_since_ack += 1
                elif frame.frame_type == FrameType.FIN:
                    fin_digest = frame.payload.decode("ascii")
            if manifest is not None and received_bitmap is not None:
                now = time.monotonic()
                due_by_count = frames_since_ack >= self.config.pipeline.ack_interval_frames
                due_by_time = now - last_ack_time >= self.config.pipeline.resend_timeout_s
                if due_by_count or due_by_time:
                    self.endpoint.send(
                        codec.build_ack(
                            stream_id=manifest["stream_id"],
                            contiguous_prefix=received_bitmap.contiguous_prefix(),
                            bitmap_bytes=received_bitmap.to_bytes(),
                        )
                    )
                    last_ack_time = now
                    frames_since_ack = 0
                if fin_digest is not None:
                    if received_bitmap.all_set():
                        assembled = b"".join(chunks[index] for index in range(received_bitmap.size))
                        assembled = assembled[: manifest["file_size"]]
                        digest = hashlib.sha256(assembled).hexdigest()
                        if digest == fin_digest:
                            output_dir = Path(self.output_dir)
                            output_dir.mkdir(parents=True, exist_ok=True)
                            output_path = output_dir / manifest["file_name"]
                            output_path.write_bytes(assembled)
                            self.endpoint.send(
                                codec.build_ack(
                                    stream_id=manifest["stream_id"],
                                    contiguous_prefix=received_bitmap.size,
                                    bitmap_bytes=received_bitmap.to_bytes(),
                                )
                            )
                            return
                    else:
                        self.endpoint.send(
                            codec.build_nack(
                                stream_id=manifest["stream_id"],
                                contiguous_prefix=received_bitmap.contiguous_prefix(),
                                bitmap_bytes=received_bitmap.invert().to_bytes(),
                            )
                        )


class HighThroughputTransferEngine:
    """Engine refactor baru dengan pipeline streaming continuous dan ACK bitmap."""

    def __init__(self, config: LinkRuntimeConfig, link_factory=InMemoryTransferLink.create) -> None:
        """Menyuntikkan konfigurasi runtime dan pabrik link untuk benchmark atau integrasi."""

        self.config = config
        self.codec = FrameCodec()
        self.link_factory = link_factory

    def _build_manifest(self, input_path: Path, payload: bytes, total_frames: int, stream_id: int) -> dict:
        """Menyusun metadata sesi transfer yang dibawa frame START."""

        return {
            "file_name": input_path.name,
            "file_size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "total_frames": total_frames,
            "chunk_size": self.config.pipeline.frame_payload_bytes,
            "stream_id": stream_id,
            "sample_rate": self.config.sample_rate,
            "modulation": self.config.modulation,
        }

    def _chunk_payload(self, payload: bytes) -> list[bytes]:
        """Memecah file besar menjadi frame payload tetap untuk streaming sender."""

        step = self.config.pipeline.frame_payload_bytes
        return [payload[index : index + step] for index in range(0, len(payload), step)]

    def _ack_listener(self, endpoint: DuplexEndpoint, total_frames: int, ack_state: AckState, stop_event: threading.Event) -> None:
        """Menerima frame ACK/NACK di thread terpisah agar sender tetap terus mengalir."""

        buffer = b""
        while not stop_event.is_set():
            chunk = endpoint.recv(timeout=0.01)
            if chunk is None:
                continue
            buffer += chunk
            frames, leftover = self.codec.decode_stream(buffer)
            buffer = leftover
            for frame in frames:
                if frame.frame_type == FrameType.ACK:
                    ack_state.merge_ack(AckBitmap.from_bytes(total_frames, frame.payload))
                elif frame.frame_type == FrameType.NACK:
                    ack_state.set_missing(AckBitmap.from_bytes(total_frames, frame.payload))

    def send_file(self, input_path: str | Path, output_dir: str | Path | None = None) -> TransferBenchmarkResult:
        """Mengirim satu file melalui arsitektur baru dan memverifikasi hasil akhir."""

        input_path = Path(input_path)
        payload = input_path.read_bytes()
        output_dir = Path(output_dir or self.config.target_output_dir)
        frames = self._chunk_payload(payload)
        total_frames = len(frames)
        stream_id = (time.time_ns() ^ len(payload) ^ total_frames ^ os.getpid()) & 0xFFFFFFFF
        manifest = self._build_manifest(input_path, payload, total_frames, stream_id)
        digest = manifest["sha256"]
        link = self.link_factory(max(self.config.pipeline.rx_queue_depth, self.config.pipeline.tx_queue_depth))
        receiver = ReceiverProcess(link.receiver, output_dir, self.config)
        receiver.start()
        ack_state = AckState(acked=AckBitmap(total_frames), missing=AckBitmap(total_frames))
        stop_event = threading.Event()
        ack_thread = threading.Thread(
            target=self._ack_listener,
            args=(link.sender, total_frames, ack_state, stop_event),
            daemon=True,
        )
        ack_thread.start()
        link.sender.send(self.codec.build_start(stream_id, manifest))
        inflight: dict[int, float] = {}
        next_sequence = 0
        started = time.perf_counter()
        while True:
            acked, missing = ack_state.snapshot()
            if acked.all_set():
                break
            for sequence in list(inflight):
                if acked.is_set(sequence):
                    inflight.pop(sequence, None)
            while next_sequence < total_frames and len(inflight) < self.config.pipeline.max_inflight_frames:
                link.sender.send(self.codec.build_data(stream_id, next_sequence, frames[next_sequence]))
                inflight[next_sequence] = time.monotonic()
                next_sequence += 1
            now = time.monotonic()
            resend_candidates = [
                sequence
                for sequence in missing.missing_indexes()
                if sequence < next_sequence and (sequence not in inflight or now - inflight[sequence] >= self.config.pipeline.nack_backoff_s)
            ]
            if not resend_candidates:
                resend_candidates = [
                    sequence
                    for sequence, sent_at in inflight.items()
                    if now - sent_at >= self.config.pipeline.resend_timeout_s and not acked.is_set(sequence)
                ]
            for sequence in resend_candidates[: self.config.pipeline.max_inflight_frames]:
                link.sender.send(self.codec.build_data(stream_id, sequence, frames[sequence]))
                inflight[sequence] = now
            time.sleep(self.config.pipeline.sender_poll_interval_s)
        link.sender.send(self.codec.build_fin(stream_id, digest))
        receiver.join(timeout=5.0)
        stop_event.set()
        ack_thread.join(timeout=1.0)
        if receiver.is_alive():
            receiver.terminate()
            receiver.join(timeout=1.0)
            raise RuntimeError("Receiver process tidak selesai setelah FIN")
        elapsed = time.perf_counter() - started
        output_path = output_dir / input_path.name
        received = output_path.read_bytes()
        received_digest = hashlib.sha256(received).hexdigest()
        if received_digest != digest:
            raise RuntimeError("Digest file hasil transfer tidak cocok")
        end_to_end_bps = len(payload) * 8 / elapsed
        return TransferBenchmarkResult(
            file_name=input_path.name,
            file_size=len(payload),
            total_frames=total_frames,
            seconds=elapsed,
            end_to_end_bps=end_to_end_bps,
            target_bps=self.config.throughput_target.minimum_end_to_end_bps,
            meets_target=end_to_end_bps >= self.config.throughput_target.minimum_end_to_end_bps,
            sha256=received_digest,
            output_path=str(output_path),
        )

    def run_synthetic_benchmark(self, workspace: str | Path) -> TransferBenchmarkResult:
        """Menjalankan benchmark sintetis untuk memvalidasi target throughput 2 Mbps."""

        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        payload_size = self.config.throughput_target.synthetic_payload_bytes
        input_path = workspace / "synthetic_payload.bin"
        if not input_path.exists() or input_path.stat().st_size != payload_size:
            input_path.write_bytes(os.urandom(payload_size))
        return self.send_file(input_path=input_path, output_dir=workspace / "receive")
