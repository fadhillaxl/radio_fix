import numpy as np
cimport numpy as cnp
cimport cython


@cython.boundscheck(False)
@cython.wraparound(False)
def rolling_correlation_cy(cnp.ndarray[cnp.double_t, ndim=1] reference, cnp.ndarray[cnp.double_t, ndim=1] samples):
    """Implementasi Cython untuk korelasi rolling sinyal real."""

    cdef Py_ssize_t ref_len = reference.shape[0]
    cdef Py_ssize_t sam_len = samples.shape[0]
    cdef Py_ssize_t out_len
    cdef Py_ssize_t i
    cdef Py_ssize_t j
    cdef double acc
    if ref_len == 0 or sam_len < ref_len:
        return np.zeros(0, dtype=np.float64)
    out_len = sam_len - ref_len + 1
    cdef cnp.ndarray[cnp.double_t, ndim=1] output = np.zeros(out_len, dtype=np.float64)
    for i in range(out_len):
        acc = 0.0
        for j in range(ref_len):
            acc += samples[i + j] * reference[ref_len - 1 - j]
        output[i] = abs(acc)
    return output


@cython.boundscheck(False)
@cython.wraparound(False)
def detect_preamble_offsets_cy(cnp.ndarray[cnp.double_t, ndim=1] reference, cnp.ndarray[cnp.double_t, ndim=1] samples, double threshold_ratio=0.72):
    """Mendeteksi offset preamble memakai backend korelasi Cython."""

    cdef cnp.ndarray[cnp.double_t, ndim=1] corr = rolling_correlation_cy(reference, samples)
    cdef list peaks = []
    cdef Py_ssize_t index
    cdef double threshold
    if corr.shape[0] == 0:
        return peaks
    threshold = float(corr.max()) * threshold_ratio
    for index in range(1, corr.shape[0] - 1):
        if corr[index] >= threshold and corr[index] >= corr[index - 1] and corr[index] > corr[index + 1]:
            peaks.append(index)
    return peaks
