# Optimized Transfer API

## Tujuan

Modul ini memecah bottleneck arsitektur lama menjadi beberapa komponen terpisah agar throughput end-to-end dapat didorong ke atas 2 Mbps:

- korelasi kritis dipersiapkan untuk backend Cython
- receiver dipindahkan ke proses terisolasi
- framing dibuat ringan dan deterministic
- ACK/NACK dikirim sebagai bitmap bit-level
- pipeline sender berjalan continuous tanpa stop-and-check per window

## Modul

### `optimized_transfer.config`

- `LinkRuntimeConfig`: konfigurasi utama runtime baru
- `load_runtime_config(path)`: memuat YAML konfigurasi
- `save_runtime_config(path, config)`: menyimpan konfigurasi

Contoh:

```python
from optimized_transfer.config import load_runtime_config

config = load_runtime_config("/Users/mm/GitHub/radio_fix/configs/optimized_transfer_2mbps.yaml")
print(config.pipeline.max_inflight_frames)
```

### `optimized_transfer.bitmap`

- `AckBitmap(size)`: bitmap ACK/NACK kompak
- `set(index)`: menandai frame diterima
- `missing_indexes()`: daftar frame yang belum diterima
- `to_bytes()` / `from_bytes()`: serialisasi untuk frame ACK/NACK

Contoh:

```python
from optimized_transfer.bitmap import AckBitmap

bitmap = AckBitmap(128)
bitmap.set(0)
bitmap.set(9)
payload = bitmap.to_bytes()
restored = AckBitmap.from_bytes(128, payload)
```

### `optimized_transfer.framing`

- `FrameType`: `START`, `DATA`, `ACK`, `NACK`, `FIN`
- `FrameCodec.encode(frame)`: serialisasi frame ringan
- `FrameCodec.decode_stream(buffer)`: parsing streaming bytes
- `build_start`, `build_data`, `build_ack`, `build_nack`, `build_fin`

Contoh:

```python
from optimized_transfer.framing import FrameCodec

codec = FrameCodec()
encoded = codec.build_data(1, 42, b"hello")
frames, leftover = codec.decode_stream(encoded)
```

### `optimized_transfer.correlation`

- `rolling_correlation_numpy(reference, samples)`: fallback NumPy
- `detect_preamble_offsets(reference, samples, threshold_ratio)`: detektor offset
- `resolve_correlator_backend()`: memilih backend Cython atau NumPy

Contoh:

```python
import numpy as np
from optimized_transfer.correlation import resolve_correlator_backend

correlate, detect, backend = resolve_correlator_backend()
offsets = detect(np.array([1.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0, 1.0, 0.0]))
```

### `optimized_transfer.runtime`

- `InMemoryTransferLink.create(depth)`: membuat channel duplex benchmark
- `ReceiverProcess`: proses terisolasi untuk receive dan ACK
- `HighThroughputTransferEngine.send_file(input_path, output_dir)`: transfer file end-to-end
- `HighThroughputTransferEngine.run_synthetic_benchmark(workspace)`: benchmark sintetis throughput
- `TransferBenchmarkResult`: hasil benchmark serializable

Contoh:

```python
from pathlib import Path
from optimized_transfer.config import load_runtime_config
from optimized_transfer.runtime import HighThroughputTransferEngine

config = load_runtime_config("/Users/mm/GitHub/radio_fix/configs/optimized_transfer_2mbps.yaml")
engine = HighThroughputTransferEngine(config)
result = engine.send_file(Path("/Users/mm/GitHub/radio_fix/NearDrop.app.zip"), Path(config.target_output_dir))
print(result.end_to_end_bps)
```

### `optimized_transfer.pluto_adapter`

- `PlutoOptimizedAdapter`: adapter hardware nyata yang memakai jalur PlutoSDR terverifikasi
- `send_file(input_path, output_dir, scenario)`: transfer file langsung pada loop RF Pluto
- `run_scenarios(input_path, output_dir)`: menjalankan beberapa profil kondisi channel RF
- `load_pluto_adapter(path)`: memuat adapter dari YAML

Contoh:

```python
from optimized_transfer.pluto_adapter import load_pluto_adapter

adapter = load_pluto_adapter("/Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml")
result = adapter.send_file("/Users/mm/GitHub/radio_fix/input.jpg", "/Users/mm/GitHub/radio_fix/receive")
print(result.useful_bit_rate_bps)
```

### `optimized_transfer.fastpath_rf`

- `FastPathRFConfig`: konfigurasi fast-path single-device
- `SingleDeviceFastPathAdapter`: adapter RF nyata dengan sparse validation dan cache
- `load_fastpath_rf_config(path)`: memuat konfigurasi hardware + fast-path dari YAML

Contoh:

```python
from optimized_transfer.fastpath_rf import SingleDeviceFastPathAdapter, load_fastpath_rf_config

hardware, fastpath = load_fastpath_rf_config("/Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml")
adapter = SingleDeviceFastPathAdapter(hardware, fastpath)
result, metrics = adapter.send_file("/Users/mm/GitHub/radio_fix/NearDrop.app.zip", "/Users/mm/GitHub/radio_fix/receive")
print(result.useful_bit_rate_bps)
```

## Benchmark

Jalankan benchmark sintetis:

```bash
python /Users/mm/GitHub/radio_fix/benchmarks/benchmark_optimized_transfer.py
```

Jalankan benchmark file nyata:

```bash
python /Users/mm/GitHub/radio_fix/benchmarks/benchmark_optimized_transfer.py \
  --input /Users/mm/GitHub/radio_fix/NearDrop.app.zip
```

Jalankan benchmark RF nyata:

```bash
PYTHONPATH=/Users/mm/GitHub/radio_fix python /Users/mm/GitHub/radio_fix/benchmarks/benchmark_pluto_rf_optimized.py \
  --config /Users/mm/GitHub/radio_fix/configs/optimized_pluto_rf.yaml \
  --input /Users/mm/GitHub/radio_fix/input.jpg \
  --scenario loop_cable_nominal
```

## Pengujian

Jalankan unit test:

```bash
python -m unittest discover -s /Users/mm/GitHub/radio_fix/tests -p 'test_optimized_transfer.py'
```
