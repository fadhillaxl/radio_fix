from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum


class FrameType(IntEnum):
    """Jenis frame ringan untuk pipeline streaming continuous."""

    START = 1
    DATA = 2
    ACK = 3
    NACK = 4
    FIN = 5


@dataclass(frozen=True)
class Frame:
    """Representasi satu frame protokol ringan optimized transfer."""

    frame_type: FrameType
    stream_id: int
    sequence: int
    ack_base: int
    payload: bytes


class FrameCodec:
    """Codec framing ringan pengganti validasi waveform correlation berat."""

    MAGIC = 0x52465832
    VERSION = 1
    HEADER = struct.Struct("!IBBIIIHI")

    def encode(self, frame: Frame) -> bytes:
        """Menyandikan frame logis menjadi bytes yang siap dipindahkan pipeline."""

        payload_length = len(frame.payload)
        header_without_crc = self.HEADER.pack(
            self.MAGIC,
            self.VERSION,
            int(frame.frame_type),
            frame.stream_id,
            frame.sequence,
            frame.ack_base,
            payload_length,
            0,
        )
        checksum = zlib.crc32(header_without_crc[:-4] + frame.payload) & 0xFFFFFFFF
        header = self.HEADER.pack(
            self.MAGIC,
            self.VERSION,
            int(frame.frame_type),
            frame.stream_id,
            frame.sequence,
            frame.ack_base,
            payload_length,
            checksum,
        )
        return header + frame.payload

    def decode_stream(self, data: bytes) -> tuple[list[Frame], bytes]:
        """Mengurai stream bytes bertahap menjadi frame-frame valid dan sisa buffer."""

        frames: list[Frame] = []
        offset = 0
        while offset + self.HEADER.size <= len(data):
            magic = struct.unpack_from("!I", data, offset)[0]
            if magic != self.MAGIC:
                offset += 1
                continue
            header = self.HEADER.unpack_from(data, offset)
            _, version, frame_type, stream_id, sequence, ack_base, payload_length, checksum = header
            frame_end = offset + self.HEADER.size + payload_length
            if version != self.VERSION:
                offset += 1
                continue
            if frame_end > len(data):
                break
            payload = data[offset + self.HEADER.size : frame_end]
            expected = zlib.crc32(data[offset : offset + self.HEADER.size - 4] + payload) & 0xFFFFFFFF
            if checksum != expected:
                offset += 1
                continue
            frames.append(
                Frame(
                    frame_type=FrameType(frame_type),
                    stream_id=stream_id,
                    sequence=sequence,
                    ack_base=ack_base,
                    payload=payload,
                )
            )
            offset = frame_end
        return frames, data[offset:]

    def build_start(self, stream_id: int, manifest: dict) -> bytes:
        """Membangun frame START yang membawa metadata sesi transfer."""

        return self.encode(
            Frame(
                frame_type=FrameType.START,
                stream_id=stream_id,
                sequence=0,
                ack_base=0,
                payload=json.dumps(manifest, sort_keys=True).encode("utf-8"),
            )
        )

    def build_data(self, stream_id: int, sequence: int, payload: bytes) -> bytes:
        """Membangun frame DATA untuk satu chunk payload file."""

        return self.encode(
            Frame(
                frame_type=FrameType.DATA,
                stream_id=stream_id,
                sequence=sequence,
                ack_base=0,
                payload=payload,
            )
        )

    def build_ack(self, stream_id: int, contiguous_prefix: int, bitmap_bytes: bytes) -> bytes:
        """Membangun frame ACK yang membawa bitmap penerimaan receiver."""

        return self.encode(
            Frame(
                frame_type=FrameType.ACK,
                stream_id=stream_id,
                sequence=contiguous_prefix,
                ack_base=0,
                payload=bitmap_bytes,
            )
        )

    def build_nack(self, stream_id: int, contiguous_prefix: int, bitmap_bytes: bytes) -> bytes:
        """Membangun frame NACK bitmap untuk memberitahu frame yang masih hilang."""

        return self.encode(
            Frame(
                frame_type=FrameType.NACK,
                stream_id=stream_id,
                sequence=contiguous_prefix,
                ack_base=0,
                payload=bitmap_bytes,
            )
        )

    def build_fin(self, stream_id: int, digest_hex: str) -> bytes:
        """Membangun frame FIN yang menutup sesi dan membawa digest final."""

        return self.encode(
            Frame(
                frame_type=FrameType.FIN,
                stream_id=stream_id,
                sequence=0,
                ack_base=0,
                payload=digest_hex.encode("ascii"),
            )
        )
