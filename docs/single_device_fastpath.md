# Single Device Fast-Path

## Tujuan

Fast-path ini mengoptimalkan jalur RF nyata untuk Pluto single-device dengan memangkas overhead handshake dan validasi yang berulang.

## Teknik yang Diimplementasikan

- validasi sparse per beberapa window, bukan setiap window
- cache gain dan hasil validasi handshake agar sesi berikutnya lebih singkat
- adaptive window size pada rentang 256KB–1024KB
- chunk minimum 64KB
- zero-copy chunking memakai `memoryview`
- telemetry queue berbasis `SimpleQueue`
- CPU affinity pinning best-effort untuk proses kritis

## File Utama

- [fastpath_rf.py](file:///Users/mm/GitHub/radio_fix/optimized_transfer/fastpath_rf.py)
- [optimized_pluto_rf.yaml](file:///Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml)
- [profile_single_device_fastpath.py](file:///Users/mm/GitHub/radio_fix/benchmarks/profile_single_device_fastpath.py)
- [endurance_single_device_fastpath.py](file:///Users/mm/GitHub/radio_fix/benchmarks/endurance_single_device_fastpath.py)
- [traffic_generator.py](file:///Users/mm/GitHub/radio_fix/tools/traffic_generator.py)
- [generate_performance_dashboard.py](file:///Users/mm/GitHub/radio_fix/tools/generate_performance_dashboard.py)

## Benchmark Sebelum/Sesudah

Gunakan:

```bash
PYTHONPATH=/Users/mm/GitHub/radio_fix python /Users/mm/GitHub/radio_fix/benchmarks/profile_single_device_fastpath.py
```

Script tersebut akan:

- membuat traffic benchmark bergaya UDP/TCP dengan payload 1KB, 4KB, 16KB, 64KB
- menjalankan baseline adapter RF nyata
- menjalankan fast-path adapter RF nyata
- merekam throughput, latensi window, CPU time, dan RSS memori

## Dashboard

Setelah profiling:

```bash
python /Users/mm/GitHub/radio_fix/tools/generate_performance_dashboard.py \
  --input /Users/mm/GitHub/radio_fix/perf_workspace/single_device_fastpath_profile.json \
  --output /Users/mm/GitHub/radio_fix/perf_workspace/performance_dashboard.html
```

## Endurance

30 menit:

```bash
PYTHONPATH=/Users/mm/GitHub/radio_fix python /Users/mm/GitHub/radio_fix/benchmarks/endurance_single_device_fastpath.py \
  --duration-seconds 1800
```

24 jam:

```bash
PYTHONPATH=/Users/mm/GitHub/radio_fix python /Users/mm/GitHub/radio_fix/benchmarks/endurance_single_device_fastpath.py \
  --duration-seconds 86400
```

## Status Verifikasi Saat Ini

- Fast-path source, profiling, endurance harness, dan dashboard generator sudah tersedia.
- Verifikasi sesi ini fokus pada refactor kode, unit test, dan benchmark RF nyata berdurasi pendek.
- Uji 30 menit dan 24 jam penuh disiapkan sebagai harness, tetapi tidak dijalankan penuh di sesi ini.
