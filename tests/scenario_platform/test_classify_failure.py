"""The ClassifyFailure classification table (Section 6.3)."""

from __future__ import annotations

import json

from scenario_platform.control.classify_failure import classify


def test_states_timeout_maps_to_timeout_no_retry():
    result = classify(error_name="States.Timeout", cause=None, cluster_arn=None, attempt=1)
    assert result["error_class"] == "TIMEOUT"


def test_deterministic_input_exit_code_maps_to_input_no_retry():
    cause = json.dumps({"exitCode": 2})
    result = classify(
        error_name="States.TaskFailed", cause=cause, cluster_arn=None, attempt=1
    )
    assert result["error_class"] == "INPUT"


def test_artifact_integrity_exit_code_maps_correctly():
    cause = json.dumps({"exitCode": 3})
    result = classify(
        error_name="States.TaskFailed", cause=cause, cluster_arn=None, attempt=1
    )
    assert result["error_class"] == "ARTIFACT_INTEGRITY"


def test_oom_exit_code_137_maps_to_resource():
    cause = json.dumps({"exitCode": 137, "stoppedReason": "OutOfMemoryError"})
    result = classify(
        error_name="States.TaskFailed", cause=cause, cluster_arn=None, attempt=1
    )
    assert result["error_class"] == "RESOURCE"


def test_oom_stopped_reason_maps_to_resource_without_exit_code():
    cause = json.dumps({"stoppedReason": "OutOfMemoryError: Container killed"})
    result = classify(
        error_name="States.TaskFailed", cause=cause, cluster_arn=None, attempt=1
    )
    assert result["error_class"] == "RESOURCE"


def test_task_failed_to_start_is_transient_infra():
    cause = json.dumps({"stopCode": "TaskFailedToStart", "stoppedReason": "capacity"})
    result = classify(
        error_name="States.TaskFailed", cause=cause, cluster_arn=None, attempt=1
    )
    assert result["error_class"] == "TRANSIENT_INFRA"


def test_ecs_unknown_error_name_is_transient_infra():
    result = classify(
        error_name="AmazonECS.Unknown", cause="some failure", cluster_arn=None, attempt=1
    )
    assert result["error_class"] == "TRANSIENT_INFRA"


def test_first_pull_failure_is_transient_second_is_config():
    cause = json.dumps({"stoppedReason": "CannotPullContainerError: no such image"})
    first = classify(
        error_name="States.TaskFailed", cause=cause, cluster_arn=None, attempt=1
    )
    assert first["error_class"] == "TRANSIENT_INFRA"
    second = classify(
        error_name="States.TaskFailed", cause=cause, cluster_arn=None, attempt=2
    )
    assert second["error_class"] == "CONFIG"


def test_unrecognised_failure_is_unclassified_and_never_retried():
    result = classify(
        error_name="States.Runtime", cause="something bizarre", cluster_arn=None, attempt=1
    )
    assert result["error_class"] == "UNCLASSIFIED"


def test_attempt_is_echoed_back():
    result = classify(error_name="States.Timeout", cause=None, cluster_arn=None, attempt=2)
    assert result["attempt"] == 2


def test_signal_kill_without_oom_evidence_is_unclassified():
    result = classify(
        error_name="States.TaskFailed",
        cause=json.dumps({"exitCode": 137}),
        cluster_arn=None,
        attempt=1,
    )
    assert result["error_class"] == "UNCLASSIFIED"


def test_nested_worker_network_exit_preserves_retry_class():
    result = classify(
        error_name="States.TaskFailed",
        cause=json.dumps(
            {
                "StopCode": "EssentialContainerExited",
                "StoppedReason": "exit",
                "Containers": [{"ExitCode": 6}],
            }
        ),
        cluster_arn=None,
        attempt=1,
    )
    assert result["error_class"] == "TRANSIENT_INFRA"
    result = classify(
        error_name="States.TaskFailed",
        cause=json.dumps({"exitCode": 5}),
        cluster_arn=None,
        attempt=1,
    )
    assert result["error_class"] == "CONFIG"


def test_unknown_ecs_error_does_not_retry():
    result = classify(error_name="ECS.Unrecognised", cause="", cluster_arn=None, attempt=1)
    assert result["error_class"] == "UNCLASSIFIED"
