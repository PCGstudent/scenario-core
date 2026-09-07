"""The worker's exit-code / error_class contract (architecture plan Section 6.3, 8.3).

Step 4g of the request flow (Section 6.1): "non-zero exit codes are a small,
documented enumeration mapped to error_class." This module is that
enumeration -- the single place a worker failure becomes a stable, testable
exit code, so a caller (a shell script today; the `ClassifyFailure` Lambda
once Phase 3's Step Functions workflow exists) never has to parse free-text
output to know what happened.

Two different kinds of error_class live in the architecture plan, and this
module only owns one of them:

**Deterministic, in-process (this module's job).** A failure the worker
*itself* detects and can exit cleanly from: bad input, an artifact that
cannot be trusted, or an unexpected internal error. These are exactly
"container exits non-zero" in Section 6.3's table -- always ``FAILED``,
never retried, because re-running reproduces the same failure.

**Infrastructure-classified (NOT this module's job, and not reachable from
Phase 2).** ``RESOURCE`` (OOM, ``exitCode = 137`` from a SIGKILL the process
never gets to handle), ``TIMEOUT`` (``States.Timeout`` -- no container exit
code is even involved), ``TRANSIENT_INFRA`` and ``CONFIG`` (ECS task
placement/image-pull failures -- the container may never have started),
``UNCLASSIFIED`` (the classifier's fallback), and ``ORPHANED`` (a
reconciler-only condition, Section 6.2a) are all assigned by the
`ClassifyFailure` Lambda from ECS/Step-Functions signals -- `stopCode`,
`stoppedReason`, the raw container `exitCode`, or an execution-level event --
that only exist once Phase 3's orchestrator exists. Phase 2 has no worker
image deployed to any orchestrator, so this module documents these values
(the table below is the complete enumeration named anywhere in the
architecture plan) without ever emitting them: a bare CLI process cannot
observe an OOM-kill it did not survive, and claiming to test that boundary
here would be exactly the kind of untested guarantee the plan warns against
(Section 6.3, "error names must not be guessed").
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Exit codes this process actually returns from ``sys.exit``.

    Deliberately skips 1 (Python's own default for an uncaught exception
    that somehow escaped every handler in ``__main__.py`` -- if that value
    is ever observed, it means the documented mapping below did *not* run,
    which is itself the signal worth alarming on) and the 128+n / 137
    range (reserved for signal-terminated processes, which this table
    never assigns to a deliberate exit).
    """

    SUCCESS = 0
    INPUT = 2
    ARTIFACT_INTEGRITY = 3
    INTERNAL = 4


#: The complete documented exit_code -> error_class mapping. Values 0/2/3/4
#: are emitted by this process (see ExitCode above); the remainder are
#: infrastructure-classified (see module docstring) and listed here only so
#: this table is the single, complete reference the architecture plan's
#: vocabulary maps onto -- not because this process can produce them.
ERROR_CLASS_BY_EXIT_CODE: dict[int, str | None] = {
    ExitCode.SUCCESS: None,
    ExitCode.INPUT: "INPUT",
    ExitCode.ARTIFACT_INTEGRITY: "ARTIFACT_INTEGRITY",
    ExitCode.INTERNAL: "INTERNAL",
    # Infrastructure-classified; never emitted by this process (see above).
    137: "RESOURCE",
}

#: Infrastructure-classified error_class values with no exit code of their
#: own at all (assigned from an execution-level event, not a container
#: exit): documented for completeness, not reachable from this table.
INFRASTRUCTURE_ONLY_ERROR_CLASSES: tuple[str, ...] = (
    "TIMEOUT",
    "TRANSIENT_INFRA",
    "CONFIG",
    "UNCLASSIFIED",
    "ORPHANED",
)


class WorkerInputError(ValueError):
    """The request could not be parsed, was missing a required field, named
    an unsupported operation, or asked for something the caller controls
    that this worker cannot honour (e.g. ``rng_scheme="sharded-v1"``).

    Maps to :attr:`ExitCode.INPUT`.
    """


class WorkerArtifactMismatchError(RuntimeError):
    """The artifact loaded from ``--artifact-dir`` is not the one the request named.

    Architecture plan Section 8.3: "The worker recomputes ``artifact_id``
    from the artifact it loaded and compares it with the id named in the
    job request. On mismatch it exits non-zero with
    ``error_class = ARTIFACT_INTEGRITY`` and writes nothing." This is that
    check -- distinct from
    :class:`~scenario_platform.domain.artifacts.ArtifactIntegrityError`
    (which catches a stored artifact disagreeing with *itself*): this one
    catches a caller/loader mismatch -- the wrong artifact directory, a
    registry pointer that named one version while the prefix on disk (or,
    later, in S3) holds another.

    Maps to :attr:`ExitCode.ARTIFACT_INTEGRITY`.
    """
