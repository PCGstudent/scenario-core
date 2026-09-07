"""``upload_run_outputs``'s conditional manifest write (Section 6.3's
retry-safety invariant, applied to S3 rather than the local filesystem)."""

from __future__ import annotations

import json

import boto3
import pytest

from scenario_platform.adapters import s3_store


def _write_local_result(tmp_path, *, artifact_id: str):
    (tmp_path / "returns.npy").write_bytes(b"fake-returns")
    (tmp_path / "risk_report.json").write_text(json.dumps({"var_es": {}}))
    (tmp_path / "manifest.json").write_text(json.dumps({"artifact_id": artifact_id}))
    return tmp_path


def test_first_publish_succeeds_and_is_retrievable(moto_env, tmp_path):
    _write_local_result(tmp_path, artifact_id="sha256:aaa")
    s3_store.upload_run_outputs("job-1", tmp_path)
    assert s3_store.get_manifest("job-1")["artifact_id"] == "sha256:aaa"


def test_second_publish_of_the_same_job_is_superseded_not_an_error(moto_env, tmp_path):
    """Section 6.3: a retry is deterministic, so a second invocation's
    manifest write losing the race is not a failure -- but the FIRST
    manifest must remain the one on record, never silently overwritten."""
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    _write_local_result(first_dir, artifact_id="sha256:aaa")
    s3_store.upload_run_outputs("job-2", first_dir)

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    _write_local_result(second_dir, artifact_id="sha256:aaa")
    with pytest.raises(s3_store.ConcurrentPublishSuperseded):
        s3_store.upload_run_outputs("job-2", second_dir)

    # The original manifest is still the one on record.
    assert s3_store.get_manifest("job-2")["artifact_id"] == "sha256:aaa"


def test_incomplete_publish_never_looks_complete(moto_env, tmp_path):
    """A crash between the data-file uploads and the manifest upload must
    never leave get_manifest() returning something -- exactly the local
    filesystem's own manifest-last completion contract, reproduced in S3."""
    (tmp_path / "returns.npy").write_bytes(b"fake-returns")
    (tmp_path / "risk_report.json").write_text(json.dumps({"var_es": {}}))
    # No manifest.json written -- simulates the process being killed before
    # this invocation reached the manifest-write step.
    s3_store.upload_run_outputs("job-3", tmp_path)

    assert s3_store.get_manifest("job-3") is None
    # The data files ARE present -- exactly the "partial result, no
    # manifest" state a consumer must never mistake for complete.
    s3 = boto3.client("s3", region_name=moto_env["region"])
    s3.head_object(Bucket=moto_env["runs_bucket"], Key="runs/job-3/returns.npy")


class _FakeClientErrorSequence:
    """A stand-in for the boto3 S3 client's ``download_file`` that raises a
    scripted sequence of errors (or succeeds) on successive calls -- used
    to prove download_artifact's classification and internal-retry
    behaviour without needing moto to model transient S3 failures (which
    it does not: moto's S3 mock never fails with SlowDown/InternalError on
    its own)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def download_file(self, bucket, key, dest):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is None:
            with open(dest, "wb") as f:
                f.write(b"ok")
            return
        raise outcome


def _client_error(code: str):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": "x"}}, "GetObject")


class TestDownloadArtifactClassification:
    def test_not_found_is_not_retried(self, moto_env, tmp_path, monkeypatch):
        fake = _FakeClientErrorSequence(
            [_client_error("NoSuchKey"), _client_error("NoSuchKey")]
        )
        monkeypatch.setattr(s3_store, "_client", lambda: fake)
        with pytest.raises(s3_store.ArtifactNotFound):
            s3_store.download_artifact("sha256:aaa", tmp_path)
        assert fake.calls == 1

    def test_access_denied_is_not_retried_and_not_classified_as_missing(
        self, moto_env, tmp_path, monkeypatch
    ):
        fake = _FakeClientErrorSequence([_client_error("AccessDenied")] * 3)
        monkeypatch.setattr(s3_store, "_client", lambda: fake)
        with pytest.raises(s3_store.ArtifactAccessConfigError):
            s3_store.download_artifact("sha256:aaa", tmp_path)
        assert fake.calls == 1

    def test_transient_error_is_retried_and_eventually_succeeds(
        self, moto_env, tmp_path, monkeypatch
    ):
        import time

        monkeypatch.setattr(time, "sleep", lambda _s: None)
        # Fails on the very first download_file call (attempt 1, file 1 of
        # 2); attempt 2 then downloads both artifact files fresh -- 3 calls
        # total, not 2, since a failed attempt is retried whole.
        fake = _FakeClientErrorSequence([_client_error("SlowDown")])
        monkeypatch.setattr(s3_store, "_client", lambda: fake)
        s3_store.download_artifact("sha256:aaa", tmp_path)  # must not raise
        assert fake.calls == 3

    def test_transient_error_exhausts_retries_then_raises_distinctly(
        self, moto_env, tmp_path, monkeypatch
    ):
        import time

        monkeypatch.setattr(time, "sleep", lambda _s: None)
        fake = _FakeClientErrorSequence(
            [_client_error("SlowDown")] * s3_store._ARTIFACT_DOWNLOAD_MAX_ATTEMPTS
        )
        monkeypatch.setattr(s3_store, "_client", lambda: fake)
        with pytest.raises(s3_store.ArtifactTransientError):
            s3_store.download_artifact("sha256:aaa", tmp_path)
        assert fake.calls == s3_store._ARTIFACT_DOWNLOAD_MAX_ATTEMPTS
