# Plan Implementasi Dual Pluto+ (TX Pluto1 → RX Pluto2)

## Tujuan

- Mengubah arsitektur dari single-device menjadi dual-device.
- Pluto1 berperan sebagai transmitter (TX1), Pluto2 berperan sebagai receiver (RX1).
- Transfer file harus reliabel dengan validasi integritas SHA-256 identik.

## Arsitektur Target

- TX Node (Pluto1): baca file, framing HELLO/DATA/FIN, kirim data RF, terima feedback ACK/NACK.
- RX Node (Pluto2): capture RF, decode packet, validasi CRC/SHA, kirim ACK/NACK bitmap, simpan file.
- Control channel host-level untuk ACK/NACK (UDP/TCP ringan), terpisah dari RF data path.

## Perubahan File (File-by-File)

### 1) Konfigurasi Dual Device

- File: [optimized_pluto_rf.yaml](file:///Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml)
- Tambah section `dual_pluto_rf`:
  - `tx_uri`, `rx_uri`
  - parameter RF TX/RX (gain, channel, buffer, sample rate, bandwidth, center frequency, carrier offset)
  - `ack_host`, `ack_port`, `ack_timeout_ms`
  - `window_packets`, `max_retries`, `session_retries`
- Pertahankan section existing agar backward-compatible.

### 2) Model Config dan Loader Dual

- File: [pluto_rf.py](file:///Users/mm/GitHub/radio_fix/optimized_transfer/pluto_rf.py)
- Tambah dataclass:
  - `DualPlutoHardwareConfig`
  - `DualTransferRuntimeConfig`
- Tambah loader `load_dual_pluto_rf_config(path)`.

### 3) Adapter TX/RX Terpisah

- File: [pluto_adapter.py](file:///Users/mm/GitHub/radio_fix/optimized_transfer/pluto_adapter.py)
- Tambah class:
  - `DualPlutoTxAdapter`
  - `DualPlutoRxAdapter`
- TX adapter:
  - build metadata, kirim HELLO
  - kirim DATA window
  - tunggu ACK/NACK bitmap
  - retransmit sequence yang hilang
- RX adapter:
  - capture dan decode packet
  - maintain `received_map`
  - kirim ACK/NACK berkala
  - validasi FIN + SHA lalu simpan file

### 4) Reuse Core Codec/Modem Stabil

- Referensi: [backup/radio_image_transfer.py](file:///Users/mm/GitHub/radio_fix/backup/radio_image_transfer.py)
- Gunakan format packet dan OFDM modem yang sudah terbukti.
- Ubah alur validasi:
  - mode lama: self-capture pada single-device
  - mode baru: decode/validasi dijalankan di RX murni.

### 5) Extend Fast-Path untuk Mode Dual

- File: [fastpath_rf.py](file:///Users/mm/GitHub/radio_fix/optimized_transfer/fastpath_rf.py)
- Tambah mode:
  - `single` (existing)
  - `dual_tx`
  - `dual_rx`
- Pertahankan output metrics kompatibel (`useful_bit_rate_bps`, retries, latency, memory/CPU fields bila tersedia).

### 6) CLI Subcommand untuk 2 Node

- File: [send_fastpath.py](file:///Users/mm/GitHub/radio_fix/send_fastpath.py)
- Tambah subcommand:
  - `start-rx`
  - `send-file`
- Pola eksekusi:
  - jalankan RX dulu di Pluto2
  - jalankan TX di Pluto1
- Simpan log JSON terpisah untuk TX dan RX.

### 7) Dokumentasi Workflow Dual Device

- File: [workflow.md](file:///Users/mm/GitHub/radio_fix/docs/workflow.md)
- Tambah:
  - setup fisik kabel Pluto1 TX1 → Pluto2 RX1
  - urutan startup RX lalu TX
  - sequence diagram dual-device + ACK/NACK control channel
  - troubleshooting URI/tuning gain/sinkronisasi.

### 8) Testing dan Verifikasi

- Tambah test:
  - `tests/test_dual_pluto_config.py`
  - `tests/test_dual_ack_nack_flow.py` (mock control channel)
- Verifikasi hardware:
  - transfer `input.jpg`
  - transfer file besar
  - checksum SHA-256 harus identik
  - catat throughput, retries, dan status handshake.

## Urutan Implementasi

1. Tambah config dual + loader dataclass.
2. Implement RX adapter (capture/decode/save) lebih dulu.
3. Implement TX adapter (send/retry berdasarkan ACK/NACK).
4. Integrasi CLI subcommand `start-rx` dan `send-file`.
5. Lengkapi metrics/logging dual-mode.
6. Update dokumentasi workflow.
7. Jalankan test unit + verifikasi end-to-end hardware.

## Kriteria Sukses

- RX menyimpan file hasil transfer ke direktori target.
- SHA-256 file TX dan RX identik.
- Retransmission berjalan saat ada packet loss.
- Log run memuat metrik throughput, retries, dan status handshake.
- Workflow dual Pluto terdokumentasi dan bisa direplikasi.
