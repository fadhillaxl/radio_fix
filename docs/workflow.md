# Diagram Kerja `send_fastpath` (Fast-Path RF)

Dokumen ini menjelaskan alur kerja utama kode dari TX hingga RX menyimpan file ke `/receive`, mengikuti gaya diagram pada [DIAGRAM_KERJA.md](file:///Users/mm/GitHub/radio_fix/backup/DIAGRAM_KERJA.md).

Arsitektur yang dijelaskan terdiri dari 2 lapisan:
- Lapisan runner + adapter fast-path: [send_fastpath.py](file:///Users/mm/GitHub/radio_fix/send_fastpath.py) dan [fastpath_rf.py](file:///Users/mm/GitHub/radio_fix/optimized_transfer/fastpath_rf.py)
- Lapisan transfer RF reliabel (HELLO/DATA/FIN, reassembly, SHA-256): [backup/radio_image_transfer.py](file:///Users/mm/GitHub/radio_fix/backup/radio_image_transfer.py)

## 1. Gambaran Besar Sistem

```mermaid
flowchart TD
    A["CLI: send_fastpath.py"] --> B["Load YAML config"]
    B --> C["SingleDeviceFastPathAdapter"]
    C --> D["ScenarioReliableTransfer\n(ReliableImageTransfer)"]
    D --> E["connect: configure Pluto via pyadi-iio"]
    D --> F["send_file -> transfer_file"]
    F --> G["Bangun metadata file"]
    G --> H["Buat packet HELLO"]
    H --> I["OFDMModem.encode_packet"]
    I --> J["auto_calibrate"]
    J --> K["Handshake HELLO"]
    K --> L["Chunking file"]
    L --> M["Kirim window paket DATA"]
    M --> N["Deteksi paket diterima"]
    N --> O{"Semua chunk sudah ada?"}
    O -- "Belum" --> M
    O -- "Sudah" --> P["Kirim packet FIN"]
    P --> Q["Reassembly file"]
    Q --> R["Validasi SHA-256"]
    R --> S["Simpan file ke /receive"]
    S --> T["Tulis log JSON + report"]
```

## 2. Diagram Struktur Modul, Kelas, dan Fungsi

```mermaid
flowchart LR
    A["send_fastpath.main"] --> B["load_fastpath_rf_config"]
    A --> C["SingleDeviceFastPathAdapter.send_file"]
    C --> D["ScenarioReliableTransfer (ReliableImageTransfer)"]
    D --> E["OFDMModem"]

    C --> C1["_make_transfer"]
    C --> C2["_scenario_pair"]
    C --> C3["_window_packets"]
    C --> C4["ValidationCache load & save"]

    D --> D1["connect"]
    D --> D2["auto_calibrate"]
    D --> D3["_capture_samples"]
    D --> D4["_waveform_score"]
    D --> D5["_transmit_window"]
    D --> D6["transfer_file"]

    E --> E1["encode_packet"]
    E --> E2["decode_packet_at"]
    E --> E3["find_candidates"]
    E --> E4["_qpsk_map & _qpsk_demap"]
```

## 3. Alur Kerja `send_fastpath.py` (Entry Point)

Referensi: [send_fastpath.py](file:///Users/mm/GitHub/radio_fix/send_fastpath.py)

```mermaid
flowchart TD
    A["Parse argumen CLI"] --> B["Load YAML config"]
    B --> C["Buat adapter fast-path"]
    C --> D["send_file(input, output_dir, scenario)"]
    D --> E["Print JSON result + metrics"]
    D --> F["Tulis fastpath_last_run.json"]
```

## 4. Alur Kerja `SingleDeviceFastPathAdapter.send_file()`

Referensi: [fastpath_rf.py](file:///Users/mm/GitHub/radio_fix/optimized_transfer/fastpath_rf.py)

```mermaid
flowchart TD
    A["Baca file input"] --> B["Split payload -> chunk_views"]
    B --> C["Build metadata + HELLO payload JSON"]
    C --> D["Encode HELLO waveform"]
    D --> E["Load cache validasi (opsional)"]
    E --> F["connect + auto_calibrate"]
    F --> G["Handshake HELLO"]
    G --> H["Loop windows: kirim DATA dan deteksi yang masuk"]
    H --> I["Validasi sparse + caching sesuai config"]
    I --> J{"Semua chunk diterima?"}
    J -- "Belum" --> H
    J -- "Sudah" --> K["FIN (opsional divalidasi)"]
    K --> L["Reassembly + SHA-256"]
    L --> M["Write output file ke /receive"]
    M --> N["Return (result, metrics)"]
```

## 5. Alur Kerja `ReliableImageTransfer.transfer_file()`

Referensi: [backup/radio_image_transfer.py](file:///Users/mm/GitHub/radio_fix/backup/radio_image_transfer.py)

```mermaid
flowchart TD
    A[Baca file input] --> B[Bangun metadata]
    B --> C[Bangun payload HELLO]
    C --> D[Encode HELLO jadi waveform]
    D --> E[auto_calibrate]
    E --> F[Transmit HELLO]
    F --> G[Capture RX]
    G --> H[Hitung handshake score]
    H --> I{HELLO valid?}
    I -- Tidak --> X[Raise error]
    I -- Ya --> J[Potong file jadi chunk]
    J --> K[Inisialisasi received dict]
    K --> L{Semua packet sudah diterima?}
    L -- Belum --> M[Pilih missing packet per window]
    M --> N[Encode seluruh packet di window]
    N --> O[Transmit waveform window]
    O --> P[Capture RX]
    P --> Q[Score per packet]
    Q --> R[Simpan packet yang terdeteksi]
    R --> S{Ada progres?}
    S -- Tidak --> T[Tambah retry counter]
    S -- Ya --> U[Reset retry counter]
    T --> V{Retry > max_retries?}
    V -- Ya --> X
    V -- Tidak --> L
    U --> L
    L -- Sudah --> W[Encode dan kirim FIN]
    W --> Y[Capture dan validasi FIN]
    Y --> Z[Reassembly file]
    Z --> AA[Validasi SHA-256]
    AA --> AB[Simpan file]
    AB --> AC[Hitung statistik transfer]
    AC --> AD[Tulis transfer_report.json]
```

## 6. Alur Kerja `OFDMModem.encode_packet()`

Referensi: [backup/radio_image_transfer.py](file:///Users/mm/GitHub/radio_fix/backup/radio_image_transfer.py)

```mermaid
flowchart TD
    A[Terima Packet] --> B[Hitung CRC32 payload]
    B --> C[Hitung digest SHA-256 pendek]
    C --> D[Bangun header biner]
    D --> E[CRC header]
    E --> F[Header ke bit]
    F --> G[Modulasi header dengan BPSK]
    G --> H[Payload ke bit]
    H --> I[Modulasi payload dengan QPSK/BPSK]
    I --> J[Gabung guard + preamble + header + payload + guard]
    J --> K[Naikkan ke carrier offset]
    K --> L[Normalisasi amplitude]
    L --> M[Waveform kompleks siap TX]
```

## 7. Alur Kerja `OFDMModem.decode_packet_at()`

Referensi: [backup/radio_image_transfer.py](file:///Users/mm/GitHub/radio_fix/backup/radio_image_transfer.py)

```mermaid
flowchart TD
    A[Ambil sampel dari kandidat preamble] --> B[Coba beberapa delta offset]
    B --> C[Coba varian downmix + conjugate]
    C --> D[_decode_downmixed]
    D --> E[Estimasi channel dari training symbol]
    E --> F[Demodulasi header BPSK]
    F --> G[Validasi magic dan CRC header]
    G --> H[Demodulasi payload]
    H --> I[Validasi CRC payload]
    I --> J[Validasi digest payload]
    J --> K[Kembalikan PacketDecode]
```

## 8. Diagram QPSK

### 8.1 Mapping Bit ke Simbol QPSK

```mermaid
flowchart LR
    A[Bit pair 00] --> B[Simbol -1 - j]
    C[Bit pair 01] --> D[Simbol -1 + j]
    E[Bit pair 10] --> F[Simbol +1 - j]
    G[Bit pair 11] --> H[Simbol +1 + j]
```

### 8.2 Konstelasi QPSK (bidang I/Q)

```text
                Q (imaginer)
                  ^
                  |
          01      |      11
        (-1,+1)   |    (+1,+1)
                  |
    --------------+--------------> I (real)
                  |
          00      |      10
        (-1,-1)   |    (+1,-1)
                  |
```

### 8.3 Alur QPSK di Dalam Kode

```mermaid
flowchart TD
    A[Payload bytes] --> B[bytes_to_bits]
    B --> C[Kelompokkan 2 bit]
    C --> D[_qpsk_map]
    D --> E[Carrier symbols kompleks]
    E --> F[_modulate_bits]
    F --> G[OFDM time symbols]
    G --> H[Waveform TX]
    H --> I[RX samples]
    I --> J[_demodulate_symbols]
    J --> K[_qpsk_demap]
    K --> L[Bit hasil demodulasi]
    L --> M[bits_to_bytes]
```

## 9. Diagram Sequence Transfer (TX → RX → Simpan File)

```mermaid
sequenceDiagram
    participant CLI as send_fastpath.py
    participant FP as SingleDeviceFastPathAdapter
    participant XFER as ReliableImageTransfer
    participant MODEM as OFDMModem
    participant SDR as PlutoSDR
    participant FS as /receive

    CLI->>FP: send_file()
    FP->>XFER: connect()
    FP->>MODEM: encode_packet(HELLO)
    FP->>SDR: tx(HELLO waveform, cyclic)
    FP->>SDR: rx() capture
    FP->>XFER: waveform_score(HELLO)

    loop sampai semua chunk diterima
        FP->>MODEM: encode_packet(DATA window)
        FP->>SDR: tx(window waveform, cyclic)
        FP->>SDR: rx() capture
        FP->>XFER: score/deteksi packet
    end

    FP->>MODEM: encode_packet(FIN)
    FP->>SDR: tx(FIN waveform, cyclic)
    FP->>SDR: rx() capture
    FP->>XFER: validasi FIN (opsional)
    FP->>FS: write output file
    FP->>FS: write log JSON
```

## 10. Ringkasan Peran Tiap Bagian

- `send_fastpath.py` mengatur CLI, membaca YAML, menjalankan adapter, dan menyimpan log JSON run terakhir.
- `SingleDeviceFastPathAdapter` mengatur batching/window adaptif, validasi sparse, dan cache validasi handshake untuk mengurangi overhead.
- `ReliableImageTransfer` mengatur koneksi SDR, handshake, retransmission, reassembly, dan statistik transfer.
- `OFDMModem` menangani framing paket, modulasi (BPSK/QPSK), demodulasi, dan validasi header/payload.
- Folder `/receive` menyimpan file hasil, log run, dan cache validasi.

## 11. File Terkait

- Runner: [send_fastpath.py](file:///Users/mm/GitHub/radio_fix/send_fastpath.py)
- Konfigurasi: [optimized_pluto_rf.yaml](file:///Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml)
- Fast-path: [fastpath_rf.py](file:///Users/mm/GitHub/radio_fix/optimized_transfer/fastpath_rf.py)
- Adapter pipeline stabil: [pluto_adapter.py](file:///Users/mm/GitHub/radio_fix/optimized_transfer/pluto_adapter.py)
- Core transfer RF reliabel: [backup/radio_image_transfer.py](file:///Users/mm/GitHub/radio_fix/backup/radio_image_transfer.py)
- Artefak output: [receive/](file:///Users/mm/GitHub/radio_fix/receive)

## 12. Cara Menjalankan (Praktis)

Deteksi device:

```bash
iio_info -s
```

Kirim gambar:

```bash
PYTHONPATH=/Users/mm/GitHub/radio_fix python /Users/mm/GitHub/radio_fix/send_fastpath.py \
  --input /Users/mm/GitHub/radio_fix/input.jpg \
  --output-dir /Users/mm/GitHub/radio_fix/receive \
  --scenario loop_cable_nominal
```

Validasi manual SHA-256:

```bash
shasum -a 256 /Users/mm/GitHub/radio_fix/input.jpg
shasum -a 256 /Users/mm/GitHub/radio_fix/receive/input.jpg
```

## 13. Output dan Artefak yang Dihasilkan

- File hasil terima: default di folder [receive/](file:///Users/mm/GitHub/radio_fix/receive)
- Log JSON run terakhir: [receive/fastpath_last_run.json](file:///Users/mm/GitHub/radio_fix/receive/fastpath_last_run.json)
- Cache validasi handshake: [receive/fastpath_validation_cache.json](file:///Users/mm/GitHub/radio_fix/receive/fastpath_validation_cache.json)
