# Pluto RF Reproduction

## Tujuan

Dokumen ini menjelaskan konfigurasi hardware dan parameter untuk mereplikasi hasil uji optimized_transfer langsung pada PlutoSDR/pyadi-iio.

## Hardware

- PlutoSDR/Pluto+ terdeteksi sebagai `usb:0.1.5`
- Koneksi uji dasar: loop cable eksternal TX1 → RX1
- Lingkungan awal yang dipakai:
  - sample rate: 4 MHz
  - RF bandwidth: 3 MHz
  - center frequency: 2.4 GHz
  - carrier offset baseband: 750 kHz

## File Konfigurasi

Gunakan [optimized_pluto_rf.yaml](file:///Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml).

Section utama:

- `uri`: alamat Pluto melalui pyadi-iio
- `sample_rate`, `center_frequency`, `rf_bandwidth`, `carrier_offset`
- `rx_buffer_size`, `tx_scale`
- `gain_candidates`: kandidat kalibrasi awal
- `batch_packet_limit`: jumlah frame RF yang digabung per batch cyclic
- `session_retries`: retry sesi jika link RF tidak stabil
- `scenarios`: profil kondisi channel RF

## Skenario Uji

- `loop_cable_nominal`
  - profil baseline loop cable
  - `tx_gain_db = -10`
  - `rx_gain_db = 35`
- `low_power_path`
  - mensimulasikan link lebih lemah
  - `tx_gain_db = -25`
  - `rx_gain_db = 20`
- `high_margin_path`
  - margin link lebih kuat
  - `tx_gain_db = -6`
  - `rx_gain_db = 45`

## Perintah Uji

Satu skenario:

```bash
PYTHONPATH=/Users/mm/GitHub/radio_fix python /Users/mm/GitHub/radio_fix/benchmarks/benchmark_pluto_rf_optimized.py \
  --config /Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml \
  --input /Users/mm/GitHub/radio_fix/input.jpg \
  --scenario loop_cable_nominal
```

Semua skenario:

```bash
PYTHONPATH=/Users/mm/GitHub/radio_fix python /Users/mm/GitHub/radio_fix/benchmarks/benchmark_pluto_rf_optimized.py \
  --config /Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml \
  --input /Users/mm/GitHub/radio_fix/input.jpg \
  --all-scenarios
```

## Metrik yang Direkam

- `throughput_bps`: throughput end-to-end file pada link RF nyata
- `mean_latency_ms`: rata-rata latensi frame dari waktu kirim hingga terdeteksi receiver thread
- `tx_gain_db`, `rx_gain_db`: gain final hasil kalibrasi
- `sha256`: integritas file hasil terima

## Catatan Replikasi

- Adapter hardware yang diverifikasi untuk pengujian lokal adalah `optimized_transfer.pluto_adapter`, karena satu Pluto USB lokal tidak stabil bila dipaksa membuka dua context hardware terpisah secara paralel.
- Jalur kontrol ACK/NACK bitmap tetap dipakai di arsitektur baru, tetapi untuk mode single-device loop cable sinkronisasi sender-receiver dikelola internal oleh adapter RF yang tervalidasi.
- Untuk pengujian dua-node nyata, frame ACK/NACK yang sama dapat dipindahkan ke uplink RF terpisah tanpa mengubah format `FrameCodec`.

## Hasil Validasi Terkini

- `input.jpg` pada `loop_cable_nominal`: throughput efektif sekitar `0.83 Mbps`, latensi rata-rata frame sekitar `545.8 ms`, digest hasil identik.
- `input.jpg` semua skenario:
  - `loop_cable_nominal`: `0.843 Mbps`
  - `low_power_path`: `0.853 Mbps`
  - `high_margin_path`: `0.891 Mbps`
- `NearDrop.app.zip` pada `loop_cable_nominal`: throughput efektif sekitar `1.10 Mbps`, airtime payload `5.19 Mbps`, digest hasil identik.
