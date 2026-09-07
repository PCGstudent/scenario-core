"""Structural assertions on the Phase 3b state machine (architecture plan
Section 24 Phase 3b's own Tests bullet, Section 6.3):

* ``RunSimulation`` carries no declarative ``Retry`` block at all.
* Its ``Catch`` is ``States.ALL`` -> ``ClassifyFailure``.
* The retry loop (``RetryDecision``) is bounded by ``MAX_ATTEMPTS``.
* An ``UNCLASSIFIED`` classification does not retry (the ``Choice`` only
  special-cases ``TRANSIENT_INFRA``; everything else -- including
  ``UNCLASSIFIED`` -- falls through to its ``Default``).

Asserts structure only, never any speculative ECS error name (Section 6.3's
own warning against exactly that).
"""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE_PATH = (
    Path(__file__).parent.parent.parent
    / "infra"
    / "terraform"
    / "modules"
    / "job_orchestrator"
    / "state_machine.asl.json.tftpl"
)

#: Terraform templatefile() substitutions, standing in for real values so
#: the template's raw text becomes parseable JSON. Values are chosen to be
#: JSON-syntactically correct for wherever the token appears (bare number
#: literal for task_timeout_seconds/max_attempts/retry_wait_seconds and
#: subnet_ids_json, a quoted string everywhere else).
_SUBSTITUTIONS = {
    "${jobs_table_name}": "test-jobs-table",
    "${cluster_arn}": "arn:aws:ecs:eu-west-1:123456789012:cluster/test",
    "${task_definition_arn}": "arn:aws:ecs:eu-west-1:123456789012:task-definition/test:1",
    "${container_name}": "worker",
    "${subnet_ids_json}": '["subnet-1","subnet-2"]',
    "${security_group_id}": "sg-1",
    "${classify_failure_function_arn}": (
        "arn:aws:lambda:eu-west-1:123456789012:function:classify"
    ),
    "${task_timeout_seconds}": "600",
    "${max_attempts}": "2",
    "${retry_wait_seconds}": "10",
}


def _load_definition() -> dict:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    for token, value in _SUBSTITUTIONS.items():
        text = text.replace(token, value)
    assert "${" not in text, "an unsubstituted template token remains"
    return json.loads(text)


def test_template_renders_to_valid_json():
    definition = _load_definition()
    assert definition["States"]


def test_run_simulation_has_no_retry_block():
    definition = _load_definition()
    run_simulation = definition["States"]["RunSimulation"]
    assert "Retry" not in run_simulation


def test_run_simulation_catches_states_all_into_classify_failure():
    definition = _load_definition()
    catches = definition["States"]["RunSimulation"]["Catch"]
    assert len(catches) == 1
    assert catches[0]["ErrorEquals"] == ["States.ALL"]
    assert catches[0]["Next"] == "ClassifyFailure"


def test_retry_decision_is_bounded_by_max_attempts():
    definition = _load_definition()
    retry_decision = definition["States"]["RetryDecision"]
    assert retry_decision["Type"] == "Choice"
    choice = retry_decision["Choices"][0]
    conditions = choice["And"]
    error_class_check = next(
        c for c in conditions if c["Variable"] == "$.classification.error_class"
    )
    attempt_check = next(
        c for c in conditions if c["Variable"] == "$.classification.attempt"
    )
    assert error_class_check["StringEquals"] == "TRANSIENT_INFRA"
    assert attempt_check["NumericLessThan"] == 2
    assert choice["Next"] == "IncrementAttempt"


def test_retry_decision_default_is_record_failed():
    definition = _load_definition()
    assert definition["States"]["RetryDecision"]["Default"] == "RecordFailed"


def test_unclassified_falls_through_to_record_failed_not_retry():
    """The Choice only matches TRANSIENT_INFRA -- an UNCLASSIFIED
    classification therefore matches no Choice branch and falls to
    Default (RecordFailed), never IncrementAttempt/RunSimulation."""
    definition = _load_definition()
    choices = definition["States"]["RetryDecision"]["Choices"]
    matched_error_classes = set()
    for choice in choices:
        for cond in choice.get("And", [choice]):
            if cond.get("Variable") == "$.classification.error_class":
                matched_error_classes.add(cond.get("StringEquals"))
    assert "UNCLASSIFIED" not in matched_error_classes


def test_increment_attempt_leads_back_to_run_simulation():
    definition = _load_definition()
    assert definition["States"]["IncrementAttempt"]["Next"] == "WaitBeforeRetry"
    assert definition["States"]["WaitBeforeRetry"]["Next"] == "RunSimulation"


def test_record_failed_and_record_succeeded_are_terminal():
    definition = _load_definition()
    assert definition["States"]["RecordFailed"].get("End") is True
    assert definition["States"]["RecordSucceeded"].get("End") is True


def test_run_simulation_uses_the_optimized_sync_integration():
    definition = _load_definition()
    assert (
        definition["States"]["RunSimulation"]["Resource"]
        == "arn:aws:states:::ecs:runTask.sync"
    )
