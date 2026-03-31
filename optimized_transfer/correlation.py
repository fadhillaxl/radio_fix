from __future__ import annotations

import numpy as np


def rolling_correlation_numpy(reference: np.ndarray, samples: np.ndarray) -> np.ndarray:
    """Menghitung korelasi rolling berbasis NumPy sebagai fallback non-Cython."""

    reference = np.asarray(reference, dtype=np.float64)
    samples = np.asarray(samples, dtype=np.float64)
    if reference.size == 0 or samples.size < reference.size:
        return np.zeros(0, dtype=np.float64)
    nfft = 1 << int(np.ceil(np.log2(reference.size + samples.size - 1)))
    freq_a = np.fft.rfft(samples, nfft)
    freq_b = np.fft.rfft(reference[::-1], nfft)
    corr = np.fft.irfft(freq_a * freq_b, nfft)
    return np.abs(corr[reference.size - 1 : samples.size]).astype(np.float64)


def detect_preamble_offsets(reference: np.ndarray, samples: np.ndarray, threshold_ratio: float = 0.72) -> list[int]:
    """Mendeteksi kandidat offset preamble dari skor korelasi rolling."""

    correlation = rolling_correlation_numpy(reference, samples)
    if correlation.size == 0:
        return []
    threshold = float(np.max(correlation)) * threshold_ratio
    peaks: list[int] = []
    for index in range(1, correlation.size - 1):
        if correlation[index] >= threshold and correlation[index] >= correlation[index - 1] and correlation[index] > correlation[index + 1]:
            peaks.append(index)
    return peaks


def resolve_correlator_backend():
    """Memilih backend Cython jika extension tersedia, jika tidak pakai NumPy."""

    try:
        from .correlation_cy import detect_preamble_offsets_cy, rolling_correlation_cy

        return rolling_correlation_cy, detect_preamble_offsets_cy, "cython"
    except Exception:
        return rolling_correlation_numpy, detect_preamble_offsets, "numpy"
