# Diagram Kerja `radio_image_transfer.py`

Dokumen ini menjelaskan alur kerja utama kode pada [radio_image_transfer.py](file:///Users/mm/GitHub/radio_fix/radio_image_transfer.py).

## 1. Gambaran Besar Sistem

```mermaid
flowchart TD
    A[CLI main] --> B[SDRConfig]
    B --> C[ReliableImageTransfer]
    C --> D[connect ke Pluto melalui pyadi-iio]
    C --> E[transfer_file]
    E --> F[Bangun metadata file]
    F --> G[Buat packet HELLO]
    G --> H[OFDMModem encode_packet]
    H --> I[auto_calibrate]
    I --> J[Handshake HELLO]
    J --> K[Chunking file]
    K --> L[Kirim window paket DATA]
    L --> M[Deteksi paket diterima]
    M --> N{Semua chunk sudah ada?}
    N -- Belum --> L
    N -- Sudah --> O[Kirim packet FIN]
    O --> P[Reassembly file]
    P --> Q[Validasi SHA-256]
    Q --> R[Simpan file ke /receive]
    R --> S[Tulis transfer_report.json]
```

## 2. Diagram Struktur Kelas dan Fungsi

```mermaid
flowchart LR
    A[main] --> B[SDRConfig]
    A --> C[ReliableImageTransfer]
    C --> D[OFDMModem]

    D --> D1[bytes_to_bits]
    D --> D2[bits_to_bytes]
    D --> D3[encode_packet]
    D --> D4[decode_packet_at]
    D --> D5[find_candidates]

    C --> C1[connect]
    C --> C2[auto_calibrate]
    C --> C3[_transmit_window]
    C --> C4[transfer_file]
    C --> C5[_waveform_score]
    C --> C6[_capture_samples]
```

## 3. Alur Kerja `main()`

```mermaid
flowchart TD
    A[Parse argumen CLI] --> B[Buat SDRConfig]
    B --> C{Loop session_attempt}
    C --> D[Buat ReliableImageTransfer]
    D --> E[connect]
    E --> F[transfer_file]
    F --> G{Berhasil?}
    G -- Ya --> H[Tampilkan status ok dan statistik]
    G -- Tidak --> I[Tampilkan session_error]
    I --> J{Masih ada retry sesi?}
    J -- Ya --> K[Sleep session_retry_delay]
    K --> C
    J -- Tidak --> L[Tampilkan status error]
```

## 4. Alur Kerja `transfer_file()`

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

## 5. Alur Kerja `OFDMModem.encode_packet()`

```mermaid
flowchart TD
    A[Terima Packet] --> B[Hitung CRC32 payload]
    B --> C[Hitung digest SHA-256 pendek]
    C --> D[Bangun header biner]
    D --> E[CRC header]
    E --> F[Header ke bit]
    F --> G[Modulasi header dengan BPSK]
    G --> H[Payload ke bit]
    H --> I[Modulasi payload dengan QPSK atau BPSK]
    I --> J[Gabung guard + preamble + header + payload + guard]
    J --> K[Naikkan ke carrier offset]
    K --> L[Normalisasi amplitude]
    L --> M[Waveform kompleks siap TX]
```

## 6. Alur Kerja `OFDMModem.decode_packet_at()`

```mermaid
flowchart TD
    A[Ambil sampel dari kandidat preamble] --> B[Coba beberapa delta offset]
    B --> C[Coba beberapa varian downmix dan conjugate]
    C --> D[_decode_downmixed]
    D --> E[Estimasi channel dari training symbol]
    E --> F[Demodulasi header BPSK]
    F --> G[Validasi magic dan CRC header]
    G --> H[Demodulasi payload]
    H --> I[Validasi CRC payload]
    I --> J[Validasi digest payload]
    J --> K[Kembalikan PacketDecode]
```

## 7. Diagram QPSK

### 7.1 Mapping Bit ke Simbol QPSK

```mermaid
flowchart LR
    A[Bit pair 00] --> B[Simbol -1 - j]
    C[Bit pair 01] --> D[Simbol -1 + j]
    E[Bit pair 10] --> F[Simbol +1 - j]
    G[Bit pair 11] --> H[Simbol +1 + j]
```

### 7.2 Konstelasi QPSK

```mermaid
flowchart TD
    A[QPSK Constellation] --> B[I negatif, Q positif = 01]
    A --> C[I positif, Q positif = 11]
    A --> D[I negatif, Q negatif = 00]
    A --> E[I positif, Q negatif = 10]
```

Representasi bidang I/Q:

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

### 7.3 Alur QPSK di Dalam Kode

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

### 7.4 Letak Implementasi QPSK di Kode

- Mapper QPSK: [OFDMModem._qpsk_map](file:///Users/mm/GitHub/radio_fix/radio_image_transfer.py#L153-L161)
- Demapper QPSK: [OFDMModem._qpsk_demap](file:///Users/mm/GitHub/radio_fix/radio_image_transfer.py#L163-L168)
- Pemakaian saat modulasi: [OFDMModem._modulate_bits](file:///Users/mm/GitHub/radio_fix/radio_image_transfer.py#L170-L185)
- Pemakaian saat demodulasi: [OFDMModem._demodulate_symbols](file:///Users/mm/GitHub/radio_fix/radio_image_transfer.py#L187-L209)

## 8. Diagram Sequence Transfer

```mermaid
sequenceDiagram
    participant CLI as main
    participant TX as ReliableImageTransfer
    participant MODEM as OFDMModem
    participant SDR as PlutoSDR
    participant RX as Detektor RX Lokal

    CLI->>TX: connect()
    CLI->>TX: transfer_file()
    TX->>MODEM: encode_packet(HELLO)
    TX->>SDR: tx(HELLO waveform)
    TX->>SDR: rx()
    TX->>RX: _waveform_score(HELLO)
    RX-->>TX: skor handshake

    loop sampai semua chunk diterima
        TX->>MODEM: encode_packet(DATA window)
        TX->>SDR: tx(window waveform)
        TX->>SDR: rx()
        TX->>RX: score tiap packet
        RX-->>TX: daftar packet terdeteksi
    end

    TX->>MODEM: encode_packet(FIN)
    TX->>SDR: tx(FIN waveform)
    TX->>SDR: rx()
    TX->>RX: validasi FIN
    RX-->>TX: FIN valid
    TX-->>CLI: status ok + statistik
```

## 9. Ringkasan Peran Tiap Bagian

- `main()` mengatur argumen CLI dan retry sesi penuh.
- `ReliableImageTransfer` mengatur koneksi SDR, handshake, retransmission, reassembly, dan statistik transfer.
- `OFDMModem` menangani framing paket, modulasi, dan demodulasi.
- `bytes_to_bits()` dan `bits_to_bytes()` menjadi utilitas serialisasi bit.
- `transfer_report.json` menyimpan hasil akhir transfer.

## 10. File Terkait

- Implementasi utama: [radio_image_transfer.py](file:///Users/mm/GitHub/radio_fix/radio_image_transfer.py)
- Laporan transfer terakhir: [transfer_report.json](file:///Users/mm/GitHub/radio_fix/receive/transfer_report.json)
