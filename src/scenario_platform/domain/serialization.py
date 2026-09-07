"""Versioned local serialization for ``scenario_platform.domain.artifacts.ModelArtifact``.

Local ``Path``-based files only -- no S3, no adapters, no network. Two
files per artifact directory:

``artifact.json``
    Metadata: schema/model version, ``artifact_id``, family, fitted
    parameters, structural diagnostics, provenance, threshold/policy set
    versions.
``state.npz``
    The fitted state arrays (``fitted_residuals``, ``fitted_variances``),
    as float64.

**Critical, and worth repeating here even though ``identity.py`` already
says it:** these transport bytes do not define identity. ``artifact_id`` is
computed once, from decoded values, before this module ever runs, and this
module's only obligation is to round-trip those *values* faithfully -- not
to preserve any particular byte layout of the files it writes. A JSON
serialiser upgrade, or NumPy writing a different ``.npz`` internal layout
between versions, must never change what :func:`load_artifact` reconstructs.

Fail-closed loading (Section 8.3): :func:`load_artifact` always calls
``artifact.verify_identity()`` before returning, which recomputes
``artifact_id`` from the decoded content and raises
:class:`~scenario_platform.domain.artifacts.ArtifactIntegrityError` on any
mismatch. The stored id is read from ``artifact.json`` and used to
*construct* the object, but it is never the last word -- the recomputation
is.

This module deliberately does not pickle anything: pickle's format is tied
to a Python/protocol version and is not something an independent verifier
could re-derive identity from, which is exactly the property this whole
design exists to avoid depending on.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from xtra_takehome.challenger import GjrSkewTParams

from .artifacts import ModelArtifact, Provenance, StructuralDiagnostics

#: The transport container's own version tag -- separate from
#: ``identity.ARTIFACT_SCHEMA``, because a change to how the JSON/NPZ files
#: are laid out on disk (this module) and a change to how identity is
#: computed (identity.py) are independent concerns that may version
#: independently.
TRANSPORT_SCHEMA = "scenario-core/artifact-transport/v1"

METADATA_FILENAME = "artifact.json"
STATE_FILENAME = "state.npz"


class TransportSchemaError(ValueError):
    """Raised when a directory's ``artifact.json`` names an unsupported transport schema."""


def save_artifact(artifact: ModelArtifact, directory: Path | str) -> None:
    """Write a complete artifact to ``directory`` (created if needed).

    The ``.npz`` container's own bytes -- entry order, ZIP timestamps,
    compression choice -- are explicitly allowed to differ between two saves
    of identical semantic content; proving that is exactly what
    ``tests/test_artifact_identity.py``'s save-load-save test does.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    metadata = {
        "transport_schema": TRANSPORT_SCHEMA,
        "schema_version": artifact.schema_version,
        "model_version": artifact.model_version,
        "artifact_id": artifact.artifact_id,
        "family": artifact.family,
        "params": asdict(artifact.params),
        "diagnostics": asdict(artifact.diagnostics),
        "provenance": asdict(artifact.provenance),
        "threshold_set_version": artifact.threshold_set_version,
        "policy_set_version": artifact.policy_set_version,
    }
    (directory / METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    np.savez(
        directory / STATE_FILENAME,
        fitted_residuals=artifact.fitted_residuals,
        fitted_variances=artifact.fitted_variances,
    )


def load_artifact(directory: Path | str) -> ModelArtifact:
    """Read a complete artifact from ``directory``, verifying its identity.

    Raises :class:`~scenario_platform.domain.artifacts.ArtifactIntegrityError`
    if the recomputed ``artifact_id`` does not match the one stored in
    ``artifact.json`` -- see :meth:`ModelArtifact.verify_identity`, which
    this function always calls before returning. The stored id is never
    trusted on its own.
    """
    directory = Path(directory)
    metadata = json.loads((directory / METADATA_FILENAME).read_text(encoding="utf-8"))

    transport_schema = metadata.get("transport_schema")
    if transport_schema != TRANSPORT_SCHEMA:
        raise TransportSchemaError(
            f"unsupported transport schema {transport_schema!r} in "
            f"{directory / METADATA_FILENAME}; this reader supports "
            f"{TRANSPORT_SCHEMA!r}"
        )

    with np.load(directory / STATE_FILENAME) as npz:
        fitted_residuals = np.asarray(npz["fitted_residuals"], dtype=np.float64)
        fitted_variances = np.asarray(npz["fitted_variances"], dtype=np.float64)

    params = GjrSkewTParams(**metadata["params"])
    diagnostics = StructuralDiagnostics(**metadata["diagnostics"])
    provenance = Provenance(**metadata["provenance"])

    artifact = ModelArtifact(
        schema_version=metadata["schema_version"],
        model_version=metadata["model_version"],
        artifact_id=metadata["artifact_id"],
        family=metadata["family"],
        params=params,
        fitted_residuals=fitted_residuals,
        fitted_variances=fitted_variances,
        diagnostics=diagnostics,
        provenance=provenance,
        threshold_set_version=metadata["threshold_set_version"],
        policy_set_version=metadata["policy_set_version"],
    )
    artifact.verify_identity()
    return artifact
