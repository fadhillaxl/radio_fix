from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AckBitmap:
    """Bitmap bit-level kompak untuk status ACK atau NACK setiap frame."""

    size: int
    _bits: bytearray = field(default_factory=bytearray)

    def __post_init__(self) -> None:
        """Mengalokasikan storage bytearray jika bitmap belum disediakan caller."""

        if not self._bits:
            self._bits = bytearray((self.size + 7) // 8)

    def set(self, index: int) -> None:
        """Menandai satu frame sebagai sudah diterima atau acknowledged."""

        if not 0 <= index < self.size:
            raise IndexError(index)
        self._bits[index // 8] |= 1 << (index % 8)

    def clear(self, index: int) -> None:
        """Menghapus tanda bit untuk kebutuhan bitmap NACK atau reset lokal."""

        if not 0 <= index < self.size:
            raise IndexError(index)
        self._bits[index // 8] &= ~(1 << (index % 8))

    def is_set(self, index: int) -> bool:
        """Membaca status sebuah frame dari bitmap."""

        if not 0 <= index < self.size:
            raise IndexError(index)
        return bool(self._bits[index // 8] & (1 << (index % 8)))

    def merge(self, other: "AckBitmap") -> None:
        """Menggabungkan dua bitmap dengan operasi OR untuk sinkronisasi ACK."""

        if self.size != other.size:
            raise ValueError("Ukuran bitmap tidak sama")
        for idx, value in enumerate(other._bits):
            self._bits[idx] |= value

    def invert(self) -> "AckBitmap":
        """Membuat bitmap komplemen untuk menghasilkan representasi NACK."""

        inverted = AckBitmap(self.size)
        for index in range(self.size):
            if not self.is_set(index):
                inverted.set(index)
        return inverted

    def count(self) -> int:
        """Menghitung jumlah frame yang sudah set di bitmap."""

        return sum(int(bit).bit_count() for bit in self._bits)

    def all_set(self) -> bool:
        """Menilai apakah semua frame sudah terkirim dan ter-ACK."""

        return self.count() == self.size

    def contiguous_prefix(self) -> int:
        """Mengembalikan panjang prefix ACK kontigu dari indeks nol."""

        for index in range(self.size):
            if not self.is_set(index):
                return index
        return self.size

    def missing_indexes(self) -> list[int]:
        """Menghasilkan daftar frame yang belum diterima berdasarkan bitmap."""

        return [index for index in range(self.size) if not self.is_set(index)]

    def to_bytes(self) -> bytes:
        """Men-serialize bitmap menjadi bytes padat untuk frame ACK/NACK."""

        return bytes(self._bits)

    @classmethod
    def from_bytes(cls, size: int, payload: bytes) -> "AckBitmap":
        """Membangun bitmap dari payload bytes yang dikirim receiver."""

        expected = (size + 7) // 8
        raw = bytearray(payload[:expected])
        if len(raw) < expected:
            raw.extend(b"\x00" * (expected - len(raw)))
        return cls(size=size, _bits=raw)
