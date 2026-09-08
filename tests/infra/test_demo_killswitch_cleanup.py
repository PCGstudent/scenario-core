"""``infra/terraform/modules/demo_killswitch/lambda/cleanup.py`` -- the
demo-window dead-man's switch. Not part of the ``scenario_platform``
package (it is a standalone Lambda source file, deployed via
``data.archive_file`` directly, never installed), so this test imports it
by path.

Cover, against moto-mocked EC2/ECS/SNS: paginated task
discovery, waiter-confirmed stops, per-id endpoint verification (never one
batched call whose failure on one id masks the rest), "deleting" not being
mistaken for "deleted", repeated cleanup, and the SNS failure-alert path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

CLEANUP_PATH = (
    Path(__file__).parent.parent.parent
    / "infra"
    / "terraform"
    / "modules"
    / "demo_killswitch"
    / "lambda"
    / "cleanup.py"
)


@pytest.fixture
def cleanup_module(monkeypatch):
    spec = importlib.util.spec_from_file_location("demo_cleanup", CLEANUP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo_cleanup"] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    yield module
    del sys.modules["demo_cleanup"]


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")


REGION = "eu-west-1"


def _make_endpoint(ec2) -> tuple[str, str]:
    vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    endpoint = ec2.create_vpc_endpoint(
        VpcId=vpc_id, ServiceName=f"com.amazonaws.{REGION}.s3", VpcEndpointType="Gateway"
    )["VpcEndpoint"]
    return vpc_id, endpoint["VpcEndpointId"]


def _make_cluster_and_task(ecs) -> tuple[str, str, str]:
    cluster_arn = ecs.create_cluster(clusterName="test-cluster")["cluster"]["clusterArn"]
    task_def_arn = ecs.register_task_definition(
        family="test-simulate",
        containerDefinitions=[{"name": "worker", "image": "busybox", "memory": 128}],
    )["taskDefinition"]["taskDefinitionArn"]
    task_arn = ecs.run_task(
        cluster=cluster_arn, taskDefinition=task_def_arn, count=1, launchType="FARGATE"
    )["tasks"][0]["taskArn"]
    return cluster_arn, task_def_arn, task_arn


class TestEndpointState:
    def test_endpoint_state_deleted_for_unknown_id(self, aws_env, cleanup_module):
        with mock_aws():
            ec2 = boto3.client("ec2", region_name=REGION)
            assert cleanup_module._endpoint_state(ec2, "vpce-doesnotexist") == "deleted"

    def test_endpoint_state_available_for_existing_endpoint(self, aws_env, cleanup_module):
        with mock_aws():
            ec2 = boto3.client("ec2", region_name=REGION)
            _, endpoint_id = _make_endpoint(ec2)
            assert cleanup_module._endpoint_state(ec2, endpoint_id) == "available"

    def test_mixed_valid_and_invalid_ids_never_masks_the_valid_one(
        self, aws_env, cleanup_module
    ):
        """The original bug this fixes: a single batched DescribeVpcEndpoints
        call with several ids fails ENTIRELY if any one is unrecognised,
        which would report every other (real, still-billing) endpoint as
        gone too. Querying one id at a time closes that."""
        with mock_aws():
            ec2 = boto3.client("ec2", region_name=REGION)
            _, endpoint_id = _make_endpoint(ec2)
            states = {
                eid: cleanup_module._endpoint_state(ec2, eid)
                for eid in [endpoint_id, "vpce-bogus"]
            }
            assert states[endpoint_id] == "available"
            assert states["vpce-bogus"] == "deleted"


class TestDeleteAndVerifyEndpoints:
    def test_deletes_and_confirms_a_real_endpoint(self, aws_env, cleanup_module):
        with mock_aws():
            ec2 = boto3.client("ec2", region_name=REGION)
            _, endpoint_id = _make_endpoint(ec2)
            errors: list[str] = []
            deleted = cleanup_module._delete_and_verify_endpoints(
                ec2, [endpoint_id], errors
            )
            assert deleted == [endpoint_id]
            assert errors == []
            assert cleanup_module._endpoint_state(ec2, endpoint_id) == "deleted"

    def test_already_deleted_endpoint_is_a_no_op(self, aws_env, cleanup_module):
        with mock_aws():
            ec2 = boto3.client("ec2", region_name=REGION)
            errors: list[str] = []
            deleted = cleanup_module._delete_and_verify_endpoints(
                ec2, ["vpce-gone"], errors
            )
            assert deleted == []
            assert errors == []


class TestStopAndConfirmTasks:
    def test_stops_and_confirms_a_running_task(self, aws_env, cleanup_module):
        with mock_aws():
            ecs = boto3.client("ecs", region_name=REGION)
            cluster_arn, _, task_arn = _make_cluster_and_task(ecs)
            errors: list[str] = []
            stopped = cleanup_module._stop_and_confirm_tasks(ecs, [cluster_arn], errors)
            assert stopped == [task_arn]
            assert errors == []

    def test_paginates_across_many_tasks(self, aws_env, cleanup_module, monkeypatch):
        """Proves task discovery does not silently stop at one page --
        forces list_tasks' own paginator to be exercised over more than one
        synthetic page."""
        with mock_aws():
            ecs = boto3.client("ecs", region_name=REGION)
            cluster_arn = ecs.create_cluster(clusterName="paginated")["cluster"][
                "clusterArn"
            ]
            task_def_arn = ecs.register_task_definition(
                family="paginated-task",
                containerDefinitions=[
                    {"name": "worker", "image": "busybox", "memory": 128}
                ],
            )["taskDefinition"]["taskDefinitionArn"]
            task_arns = [
                ecs.run_task(
                    cluster=cluster_arn,
                    taskDefinition=task_def_arn,
                    count=1,
                    launchType="FARGATE",
                )["tasks"][0]["taskArn"]
                for _ in range(3)
            ]
            errors: list[str] = []
            discovered = cleanup_module._running_task_arns(ecs, cluster_arn, errors)
            assert sorted(discovered) == sorted(task_arns)
            assert errors == []

    def test_no_running_tasks_is_a_no_op(self, aws_env, cleanup_module):
        with mock_aws():
            ecs = boto3.client("ecs", region_name=REGION)
            cluster_arn = ecs.create_cluster(clusterName="empty")["cluster"]["clusterArn"]
            errors: list[str] = []
            stopped = cleanup_module._stop_and_confirm_tasks(ecs, [cluster_arn], errors)
            assert stopped == []
            assert errors == []


class TestHandlerEndToEnd:
    def test_full_cleanup_succeeds_and_publishes_nothing_on_success(
        self, aws_env, cleanup_module, monkeypatch
    ):
        with mock_aws():
            ec2 = boto3.client("ec2", region_name=REGION)
            ecs = boto3.client("ecs", region_name=REGION)
            sns = boto3.client("sns", region_name=REGION)
            _, endpoint_id = _make_endpoint(ec2)
            cluster_arn, _, task_arn = _make_cluster_and_task(ecs)
            topic_arn = sns.create_topic(Name="cleanup-failure")["TopicArn"]

            monkeypatch.setenv("VPC_ENDPOINT_IDS", endpoint_id)
            monkeypatch.setenv("ECS_CLUSTER_ARNS", cluster_arn)
            monkeypatch.setenv("FAILURE_SNS_TOPIC_ARN", topic_arn)

            result = cleanup_module.handler({}, None)

            assert result["errors"] == []
            assert result["endpoints_deleted_and_confirmed"] == [endpoint_id]
            assert result["tasks_stopped_and_confirmed"] == [task_arn]
            assert cleanup_module._endpoint_state(ec2, endpoint_id) == "deleted"

    def test_partial_failure_publishes_to_sns_and_raises(
        self, aws_env, cleanup_module, monkeypatch
    ):
        with mock_aws():
            sns = boto3.client("sns", region_name=REGION)
            topic_arn = sns.create_topic(Name="cleanup-failure")["TopicArn"]

            monkeypatch.setenv("VPC_ENDPOINT_IDS", "")
            monkeypatch.setenv("ECS_CLUSTER_ARNS", "")
            monkeypatch.setenv("FAILURE_SNS_TOPIC_ARN", topic_arn)

            def _boom(ec2_client, endpoint_ids, errors):
                errors.append("simulated describe_vpc_endpoints failure")
                return []

            monkeypatch.setattr(cleanup_module, "_delete_and_verify_endpoints", _boom)

            with pytest.raises(RuntimeError, match="demo auto-cleanup failed"):
                cleanup_module.handler({}, None)


def test_stop_confirmation_is_batched(cleanup_module):
    from unittest.mock import MagicMock

    ecs = MagicMock()
    ids = [f"task-{i}" for i in range(205)]
    ecs.get_paginator.return_value.paginate.return_value = [
        {"taskArns": ids[:110]},
        {"taskArns": ids[110:]},
    ]
    errors = []
    assert cleanup_module._stop_and_confirm_tasks(ecs, ["cluster"], errors) == ids
    assert not errors
    assert [
        len(call.kwargs["tasks"])
        for call in ecs.get_waiter.return_value.wait.call_args_list
    ] == [100, 100, 5]


def test_deleting_is_not_success(cleanup_module, monkeypatch):
    from unittest.mock import MagicMock

    ec2 = MagicMock()
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": [{"State": "deleting"}]}
    ec2.delete_vpc_endpoints.return_value = {}
    ticks = iter([0, 0, 100])
    monkeypatch.setattr(cleanup_module.time, "monotonic", lambda: next(ticks))
    errors = []
    assert cleanup_module._delete_and_verify_endpoints(ec2, ["vpce-x"], errors) == []
    assert errors


def test_delete_unsuccessful_is_reported(cleanup_module, monkeypatch):
    from unittest.mock import MagicMock

    ec2 = MagicMock()
    ec2.describe_vpc_endpoints.side_effect = [
        {"VpcEndpoints": [{"State": "available"}]},
        {"VpcEndpoints": [{"State": "deleted"}]},
    ]
    ec2.delete_vpc_endpoints.return_value = {
        "Unsuccessful": [
            {"ResourceId": "vpce-x", "Error": {"Code": "AccessDenied", "Message": "denied"}}
        ]
    }
    errors = []
    cleanup_module._delete_and_verify_endpoints(ec2, ["vpce-x"], errors)
    assert any("AccessDenied" in e for e in errors)


def test_client_exception_is_not_assumed_absent(cleanup_module):
    from botocore.exceptions import ClientError

    assert not cleanup_module._is_not_found(
        ClientError(
            {"Error": {"Code": "ClientException", "Message": "bad request"}}, "StopTask"
        )
    )


def test_unexpected_failure_alerts_and_preserves_endpoints(cleanup_module, monkeypatch):
    from unittest.mock import MagicMock

    client = MagicMock()
    monkeypatch.setattr(cleanup_module.boto3, "client", lambda service: client)
    monkeypatch.setenv("FAILURE_SNS_TOPIC_ARN", "test-topic")
    monkeypatch.setenv("ECS_CLUSTER_ARNS", "cluster")
    monkeypatch.setenv("VPC_ENDPOINT_IDS", "vpce-x")

    def fail(*args):
        raise RuntimeError("unexpected stop failure")

    monkeypatch.setattr(cleanup_module, "_stop_and_confirm_tasks", fail)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        cleanup_module.handler({})
    client.sns.publish.assert_not_called()
    client.publish.assert_called_once()
    client.delete_vpc_endpoints.assert_not_called()
