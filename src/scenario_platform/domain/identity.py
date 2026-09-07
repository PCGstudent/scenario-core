"""Canonical semantic identity for model artifacts and datasets.

Architecture plan Section 8.2 / AGENTS.md invariant 29: identity is computed
from a canonical binary encoding of *decoded semantic values*, never from the
bytes of whatever transport format happens to store them. This module is
therefore a pure encode-and-hash layer with no knowledge of JSON, NPZ, CSV or
any other container -- ``serialization.py`` is where transport lives, and it
must never be consulted here.

Why hashing the stored files would be wrong (and is not done anywhere in this
module):

* ``.npz`` is a ZIP container. Its bytes carry per-entry timestamps, entry
  ordering and compression choices, so two saves of *identical* arrays
  produce *different* file bytes.
* JSON serialisation of floats is not canonical: ``repr(0.1)`` and a
  formatter that rounds to N digits can both losslessly round-trip through
  ``json.loads`` yet produce different byte sequences.
* Python's dict insertion order, pickle protocol version and NumPy's
  ``.npy``/``.npz`` format version are all implementation details that must
  not leak into a value meant to identify *content*.

The canonical encoding instead writes every semantic field through one of
four primitives -- a length-prefixed UTF-8 string, a big-endian IEEE-754
binary64, a single canonical boolean byte, or a named/typed/shaped array of
big-endian binary64 or binary int64 -- concatenates them in a fixed,
documented order, and hashes the result with SHA-256. Two encoders are built
from these primitives:

``compute_artifact_id``
    Over ``(family, threshold_set_version, policy_set_version, dataset_id,
    calibration_start, calibration_end, params, fitted_residuals,
    fitted_variances, diagnostics)`` -- the exact byte layout in
    ``canonical_artifact_bytes``'s docstring below, matching the
    architecture plan's worked example byte-for-byte.

``compute_dataset_id``
    Over ``(ticker, return_definition, index_ns, close)`` -- the decoded
    content of a fetched price series, independent of whether it is stored
    as CSV, Parquet, or anything else.

Every property this buys is a direct test in ``tests/test_artifact_identity.py``:
stability under re-serialisation, one-ULP sensitivity in every parameter and
every array element, insensitivity to storage-format bytes, and (for the
dataset scheme) insensitivity to which transport format re-encoded the same
values.

**What is deliberately covered, and what is deliberately not.** Every field
that can change *governance/policy behaviour* -- the seven fitted parameters,
both fitted state arrays, and every ``StructuralDiagnostics`` field (Section
8.6) -- is part of the hash: ``policies.py`` reads ``finite_third_moment`` /
``finite_fourth_moment`` directly, so a stored artifact whose diagnostics
silently disagreed with its own parameters would let a governance decision
diverge from the content ``artifact_id`` claims to represent, without
``verify_identity`` ever noticing. ``Provenance`` (``dataset_uri``,
``calibration_timestamp``, ``git_sha``, ``dependency_lock_hash``) is,
by contrast, deliberately *outside* canonical identity -- it records where
and when an artifact was produced, not what it asserts, and two artifacts
built from the same dataset at two different timestamps are legitimately the
*same* content with different provenance. ``model_version`` is likewise
outside identity: it is the human-assigned registry label, not the
machine-verified content hash. Documentation and tests must not claim
``artifact_id`` cryptographically protects these provenance-only fields --
only the identity-bearing, model-risk-relevant content listed above.
"""

from __future__ import annotations

import hashlib
import struct
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from xtra_takehome.challenger import GjrSkewTParams

if TYPE_CHECKING:
    # Deferred: artifacts.py imports this module, so a top-level import here
    # would be circular. Safe because `from __future__ import annotations`
    # (above) makes every annotation in this file a lazy string.
    from .artifacts import StructuralDiagnostics

#: Domain-separation tags, one per encoding scheme this module defines. The
#: trailing "v1" is the encoding's own version: a future change to the byte
#: layout below gets a new tag, so old and new artifact_id values can never
#: collide by accident (architecture plan Section 8.2, "domain-versioned").
ARTIFACT_SCHEMA = "scenario-core/artifact/v1"
DATASET_SCHEMA = "scenario-core/dataset/v1"

_ARTIFACT_IDENTITY_DOMAIN = (ARTIFACT_SCHEMA + "\n").encode("ascii")
_DATASET_IDENTITY_DOMAIN = (DATASET_SCHEMA + "\n").encode("ascii")

#: Fixed parameter order for GjrSkewTParams in the canonical encoding. This is
#: pinned as an explicit tuple here -- not derived from dataclass field order
#: -- because dataclass field order is an implementation detail of
#: ``xtra_takehome.challenger`` that this module must not be silently
#: sensitive to. Changing this tuple changes every artifact_id ever computed,
#: so it must never change without a new ``ARTIFACT_SCHEMA`` version.
PARAM_ORDER: tuple[str, ...] = ("mu", "omega", "alpha", "gamma", "beta", "eta", "lam")

#: Fixed field order for StructuralDiagnostics in the canonical encoding
#: (each field paired with its encoded kind: "f64" or "bool"), pinned here
#: for the same reason PARAM_ORDER is pinned above -- and covering every
#: field the dataclass has, deliberately, so a new StructuralDiagnostics
#: field can never be silently added to the artifact without also becoming
#: part of what artifact_id protects.
DIAGNOSTICS_ORDER: tuple[tuple[str, str], ...] = (
    ("effective_persistence", "f64"),
    ("fourth_moment_coefficient", "f64"),
    ("implied_unconditional_variance", "f64"),
    ("implied_return_tail_index", "f64"),
    ("hill_tail_index", "f64"),
    ("finite_second_moment", "bool"),
    ("finite_third_moment", "bool"),
    ("finite_fourth_moment", "bool"),
)


def _write_varint(buf: bytearray, n: int) -> None:
    """Append an unsigned LEB128 varint: 7 payload bits per byte, MSB = continuation."""
    if n < 0:
        raise ValueError("varint length must be non-negative")
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            buf.append(byte | 0x80)
        else:
            buf.append(byte)
            return


def _write_str(buf: bytearray, value: str) -> None:
    """Append a varint-length-prefixed UTF-8 string."""
    encoded = value.encode("utf-8")
    _write_varint(buf, len(encoded))
    buf.extend(encoded)


def _write_f64(buf: bytearray, value: float) -> None:
    """Append an IEEE-754 binary64, big-endian.

    Never a decimal string: this is what makes identity insensitive to
    Python's float ``repr`` and to JSON's formatting of floats. Two floats
    that compare unequal under ``==`` always encode to different bytes here,
    including a single-ULP difference, because IEEE-754 binary64 is an exact
    bit-for-bit representation with no rounding step in between.
    """
    buf.extend(struct.pack(">d", float(value)))


def _write_u64(buf: bytearray, value: int) -> None:
    """Append an unsigned 64-bit integer, big-endian."""
    buf.extend(struct.pack(">Q", int(value)))


def _write_bool(buf: bytearray, value: bool) -> None:
    """Append one explicit canonical byte: ``0x01`` for True, ``0x00`` for False.

    Never encoded as a length-prefixed string (``"true"``/``"false"``) or
    folded into ``_write_f64``/``_write_u64`` as 1.0/1 -- a dedicated
    single-byte primitive with no ambiguity about its own type or width.
    """
    if not isinstance(value, bool):
        raise TypeError(f"canonical boolean encoding requires bool, got {type(value)}")
    buf.append(0x01 if value else 0x00)


def _write_diagnostics(buf: bytearray, diagnostics: StructuralDiagnostics) -> None:
    """Append every ``StructuralDiagnostics`` field, in ``DIAGNOSTICS_ORDER``."""
    for name, kind in DIAGNOSTICS_ORDER:
        value = getattr(diagnostics, name)
        if kind == "f64":
            _write_f64(buf, value)
        else:
            _write_bool(buf, value)


def _write_array(buf: bytearray, name: str, array: npt.NDArray[Any], *, dtype: str) -> None:
    """Append name, dtype tag, shape, and big-endian raw bytes of a typed array.

    ``dtype`` must be ``"float64"`` or ``"int64"`` -- the only two element
    types this scheme encodes today (fitted-state arrays and a dataset's
    epoch-nanosecond index, respectively). The dtype is asserted rather than
    silently coerced: a caller passing the wrong dtype has a bug, and
    silently casting it would hide exactly the kind of shape/dtype mismatch
    invariant 29 requires identity to be sensitive to.

    Byte order is normalised to big-endian regardless of host order (numpy's
    ``astype`` with an explicit byte-order character performs a real byte
    swap on little-endian hosts, not a no-op reinterpretation), so identity
    does not depend on the machine that computed it. The array is also made
    C-contiguous first, so a transposed view of the same data cannot produce
    the same encoded bytes as the canonical (C-contiguous) layout -- shape
    and memory order are both part of what is hashed.
    """
    expected_dtype, be_code = {"float64": (np.float64, ">f8"), "int64": (np.int64, ">i8")}[
        dtype
    ]
    arr = np.asarray(array)
    if arr.dtype != expected_dtype:
        raise TypeError(
            f"canonical encoding of {name!r} requires dtype {dtype}, got {arr.dtype}"
        )
    _write_str(buf, name)
    buf.extend(dtype.encode("ascii"))
    _write_u64(buf, arr.ndim)
    for dim in arr.shape:
        _write_u64(buf, dim)
    contiguous = np.ascontiguousarray(arr)
    big_endian = contiguous.astype(be_code, copy=False)
    buf.extend(big_endian.tobytes())


def canonical_artifact_bytes(
    *,
    family: str,
    threshold_set_version: str,
    policy_set_version: str,
    dataset_id: str,
    calibration_start: str,
    calibration_end: str,
    params: GjrSkewTParams,
    fitted_residuals: npt.NDArray[np.float64],
    fitted_variances: npt.NDArray[np.float64],
    diagnostics: StructuralDiagnostics,
) -> bytes:
    """Build the canonical byte stream an artifact's identity is hashed from.

    Exact layout (architecture plan Section 8.2)::

        b"scenario-core/artifact/v1\\n"
        || varint-length-prefixed UTF-8  family
        || varint-length-prefixed UTF-8  threshold_set_version
        || varint-length-prefixed UTF-8  policy_set_version
        || varint-length-prefixed UTF-8  dataset_id
        || varint-length-prefixed UTF-8  calibration_start
        || varint-length-prefixed UTF-8  calibration_end
        || for each parameter in PARAM_ORDER (mu, omega, alpha, gamma, beta, eta, lam):
               big-endian binary64
        || for each array in (fitted_residuals, fitted_variances):
               varint-length-prefixed UTF-8 name
               || b"float64"
               || big-endian uint64 ndim
               || big-endian uint64 * ndim (one per dimension)
               || C-contiguous raw bytes, big-endian
        || for each field in DIAGNOSTICS_ORDER (effective_persistence,
               fourth_moment_coefficient, implied_unconditional_variance,
               implied_return_tail_index, hill_tail_index -- each a
               big-endian binary64; then finite_second_moment,
               finite_third_moment, finite_fourth_moment -- each one
               canonical byte, 0x01/0x00):
               the field's encoded value, per its kind

    This function has no side effects and performs no I/O; it is pure enough
    to call directly from a test with hand-built inputs.
    """
    buf = bytearray()
    buf.extend(_ARTIFACT_IDENTITY_DOMAIN)
    _write_str(buf, family)
    _write_str(buf, threshold_set_version)
    _write_str(buf, policy_set_version)
    _write_str(buf, dataset_id)
    _write_str(buf, calibration_start)
    _write_str(buf, calibration_end)
    for name in PARAM_ORDER:
        _write_f64(buf, getattr(params, name))
    _write_array(buf, "fitted_residuals", fitted_residuals, dtype="float64")
    _write_array(buf, "fitted_variances", fitted_variances, dtype="float64")
    _write_diagnostics(buf, diagnostics)
    return bytes(buf)


def compute_artifact_id(
    *,
    family: str,
    threshold_set_version: str,
    policy_set_version: str,
    dataset_id: str,
    calibration_start: str,
    calibration_end: str,
    params: GjrSkewTParams,
    fitted_residuals: npt.NDArray[np.float64],
    fitted_variances: npt.NDArray[np.float64],
    diagnostics: StructuralDiagnostics,
) -> str:
    """``"sha256:" + hex(SHA256(canonical_artifact_bytes(...)))``."""
    digest = hashlib.sha256(
        canonical_artifact_bytes(
            family=family,
            threshold_set_version=threshold_set_version,
            policy_set_version=policy_set_version,
            dataset_id=dataset_id,
            calibration_start=calibration_start,
            calibration_end=calibration_end,
            params=params,
            fitted_residuals=fitted_residuals,
            fitted_variances=fitted_variances,
            diagnostics=diagnostics,
        )
    ).hexdigest()
    return f"sha256:{digest}"


def canonical_dataset_bytes(
    *,
    ticker: str,
    return_definition: str,
    index_ns: npt.NDArray[np.int64],
    close: npt.NDArray[np.float64],
) -> bytes:
    """Build the canonical byte stream a dataset's identity is hashed from.

    Exact layout (architecture plan Section 8.2)::

        b"scenario-core/dataset/v1\\n"
        || varint-length-prefixed UTF-8  ticker
        || varint-length-prefixed UTF-8  return_definition
        || named/typed/shaped array "index_ns"  (int64,  big-endian)
        || named/typed/shaped array "close"     (float64, big-endian)

    ``index_ns`` is the price series' index as int64 epoch-nanoseconds (what
    a ``pandas.DatetimeIndex`` decodes to), not any particular timestamp
    string representation -- so identity does not depend on how a caller
    happened to print or serialise the dates. ``return_definition`` is
    carried as metadata describing how returns are derived from ``close``
    downstream; it is not applied here; the hashed data is the price series
    itself; the identity of a price series does not depend on which
    downstream transform a caller intends to apply to it.
    """
    buf = bytearray()
    buf.extend(_DATASET_IDENTITY_DOMAIN)
    _write_str(buf, ticker)
    _write_str(buf, return_definition)
    _write_array(buf, "index_ns", np.asarray(index_ns, dtype=np.int64), dtype="int64")
    _write_array(buf, "close", np.asarray(close, dtype=np.float64), dtype="float64")
    return bytes(buf)


def compute_dataset_id(
    *,
    ticker: str,
    return_definition: str,
    index_ns: npt.NDArray[np.int64],
    close: npt.NDArray[np.float64],
) -> str:
    """``"sha256:" + hex(SHA256(canonical_dataset_bytes(...)))``."""
    digest = hashlib.sha256(
        canonical_dataset_bytes(
            ticker=ticker,
            return_definition=return_definition,
            index_ns=index_ns,
            close=close,
        )
    ).hexdigest()
    return f"sha256:{digest}"
