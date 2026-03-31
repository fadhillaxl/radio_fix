from __future__ import annotations

from pathlib import Path

from setuptools import Extension, setup


def build_extensions():
    """Membangun extension Cython jika dependency tersedia saat instalasi."""

    try:
        import numpy
        from Cython.Build import cythonize

        extensions = [
            Extension(
                "optimized_transfer.correlation_cy",
                [str(Path("optimized_transfer") / "correlation_cy.pyx")],
                include_dirs=[numpy.get_include()],
            )
        ]
        return cythonize(extensions, language_level="3")
    except Exception:
        return []


setup(ext_modules=build_extensions())
