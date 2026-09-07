"""Session-wide pytest configuration.

Thread-pinning env vars are set here, at import time, before pytest collects
any test module -- not in a fixture, which runs too late. NumPy/SciPy's
OpenBLAS reads ``OMP_NUM_THREADS``/``OPENBLAS_NUM_THREADS``/``MKL_NUM_THREADS``
only once, when the shared library is first loaded (``dlopen``) as a side
effect of the first ``import numpy``/``import scipy`` anywhere in the
process; setting them later (even via ``monkeypatch.setenv`` inside a test,
before the worker's own thread-contract check runs) has no effect once some
earlier-collected test module has already imported numpy -- confirmed
directly: a fixture-level ``monkeypatch.setenv`` did not change what
``threadpoolctl.threadpool_info()`` reported.

``conftest.py`` is imported before any test module is collected, so this is
the earliest point in the pytest process where setting these env vars can
still influence the first numpy import -- matching what the production
container's Dockerfile ``ENV`` directives already guarantee (set before
Python itself even starts). ``setdefault`` rather than a hard overwrite: a
developer or CI environment that already pins these (or deliberately sets
them to something else to test a violation end-to-end) is respected, not
silently clobbered.
"""

from __future__ import annotations

import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "1")
