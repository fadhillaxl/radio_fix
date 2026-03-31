# Fast-Path RF Profiling Report

## Ringkasan

Refactor fast-path single-device berhasil menaikkan throughput end-to-end RF nyata pada PlutoSDR loop cable sampai melewati target 2 Mbps untuk file besar, sambil tetap menjaga integritas SHA-256 hasil terima.

## Sebelum vs Sesudah

### File besar `NearDrop.app.zip`

- baseline adapter RF nyata:
  - throughput efektif sekitar `1.10 Mbps`
  - airtime payload sekitar `5.19 Mbps`
- fast-path single-device:
  - throughput efektif sekitar `2.20 Mbps`
  - airtime payload sekitar `5.19 Mbps`
  - durasi transfer sekitar `24.30 s`
  - paket/frame `102`
  - retry validasi `1`

### Traffic generator UDP/TCP 1KB–64KB

Sumber data: [single_device_fastpath_profile.json](file:///Users/mm/GitHub/radio_fix/perf_workspace/single_device_fastpath_profile.json)

- rata-rata baseline: `190282.57 bps`
- rata-rata fast-path: `325241.57 bps`
- speedup rata-rata: `1.71x`
- hasil terbaik fast-path: `838144.41 bps` pada payload `tcp 65536`

## CPU dan Memori

Run fast-path `NearDrop.app.zip` mencatat:

- `cpu_user_s`: `11.68141`
- `cpu_system_s`: `2.301148`
- `max_rss_kb`: `1790869504`

Catatan: pada macOS, nilai `ru_maxrss` yang dikembalikan sistem tidak selalu sebanding langsung dengan satuan KB Linux, sehingga angka ini disimpan sebagai nilai mentah untuk konsistensi profiling lintas run.

## Latensi

- target desain fast-path: `< 50 ms`
- hasil aktual `NearDrop.app.zip`:
  - rata-rata window latency: `785.31 ms`
  - maksimum window latency: `5895.74 ms`

Kesimpulan: throughput target sudah tercapai, tetapi target latensi 50 ms belum tercapai pada setup single-device saat ini.

## Stabilitas Smoke Test

Sumber data: [fastpath_endurance_smoke.json](file:///Users/mm/GitHub/radio_fix/receive/fastpath_endurance_smoke.json)

- 3 run berturut-turut selama sekitar 60 detik
- throughput tiap run:
  - `2.204 Mbps`
  - `2.289 Mbps`
  - `2.310 Mbps`
- hash hasil terima konsisten identik pada semua run

Dashboard hasil smoke test tersedia di [fastpath_endurance_dashboard.html](file:///Users/mm/GitHub/radio_fix/receive/fastpath_endurance_dashboard.html).

## Harness Lanjutan

- profiling payload 1KB–64KB: [profile_single_device_fastpath.py](file:///Users/mm/GitHub/radio_fix/benchmarks/profile_single_device_fastpath.py)
- endurance 30 menit/24 jam: [endurance_single_device_fastpath.py](file:///Users/mm/GitHub/radio_fix/benchmarks/endurance_single_device_fastpath.py)
- dashboard generator: [generate_performance_dashboard.py](file:///Users/mm/GitHub/radio_fix/tools/generate_performance_dashboard.py)

## Status

- target throughput end-to-end `> 2 Mbps`: **tercapai**
- target integritas file: **tercapai**
- target latensi `< 50 ms`: **belum tercapai**
- uji 30 menit dan 24 jam penuh: **harness tersedia, belum dijalankan penuh pada sesi ini**
