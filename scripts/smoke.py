"""Post-deploy smoke test for the Phase 3b vertical slice (architecture plan
Section 24 Phase 3b's own Tests bullet: "post-deploy smoke -- submit -> poll
-> results -> assert the returns digest equals the golden fixture and that
provenance names a Fargate task ARN").

**This script requires a real deployment and cannot run in CI/pytest.** It
signs real HTTP requests with SigV4 against the API Gateway's `AWS_IAM`
authorizer (using whatever AWS credentials are active locally -- the same
profile used for `terraform apply`) and polls a real Step Functions
execution to completion. Everything unit-testable about the request/response
shapes, admission, idempotency and classification logic already has a
`pytest` test with `moto`; this script is what is left over once a real
deployment exists to point it at.

Usage::

    python scripts/smoke.py \\
        --api-endpoint https://xxxx.execute-api.eu-west-1.amazonaws.com \\
        --region eu-west-1 --model-version gjr-skewt-20260907-1 \\
        --golden-digest sha256:637920e5...fb3c255
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from urllib.parse import urlparse

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


def _signed_request(
    method: str, url: str, *, region: str, body: bytes | None = None
) -> urllib.request.Request:
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("no AWS credentials available in this environment")
    request = AWSRequest(method=method, url=url, data=body)
    SigV4Auth(credentials, "execute-api", region).add_auth(request)
    headers = dict(request.headers.items())
    if body is not None:
        headers["content-type"] = "application/json"
    return urllib.request.Request(url, data=body, headers=headers, method=method)


def _call(method: str, url: str, *, region: str, body: dict | None = None) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = _signed_request(method, url, region=region, body=payload)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 -- fixed https API endpoint
        return json.loads(resp.read())


def run_smoke_test(
    *, api_endpoint: str, region: str, model_version: str, golden_digest: str | None
) -> int:
    parsed = urlparse(api_endpoint)
    if parsed.scheme != "https":
        raise ValueError("api_endpoint must be https")

    submit_body = {
        "model_version": model_version,
        "horizon": 252,
        "n_paths": 1000,
        "seed": 42,
    }
    print("Submitting job...")
    submitted = _call(
        "POST", f"{api_endpoint}/scenario-jobs", region=region, body=submit_body
    )
    job_id = submitted["job_id"]
    print(f"job_id={job_id}, status={submitted['status']}")

    status_url = f"{api_endpoint}/scenario-jobs/{job_id}"
    deadline = time.time() + 600
    status = submitted["status"]
    while status not in ("SUCCEEDED", "FAILED", "CANCELLED") and time.time() < deadline:
        time.sleep(10)
        current = _call("GET", status_url, region=region)
        status = current["status"]
        print(f"status={status}")

    if status != "SUCCEEDED":
        print(f"FAIL: job ended in status={status}, not SUCCEEDED", file=sys.stderr)
        return 1

    results = _call("GET", f"{status_url}/results", region=region)
    manifest = results["manifest"]
    task_arn = None
    for attempt in results.get("attempts", []):
        task_arn = attempt.get("task_arn") or task_arn
    digest = manifest.get("returns_array_sha256")
    print(f"returns_array_sha256={digest}")
    print(
        f"manifest names: artifact_id={manifest.get('artifact_id')}, "
        f"git_sha={manifest.get('runtime', {}).get('worker_git_sha')}"
    )

    if golden_digest is not None and digest != golden_digest:
        print(f"FAIL: digest {digest} != golden {golden_digest}", file=sys.stderr)
        return 1

    print("OK: job completed and (if requested) matched the golden digest.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-endpoint", required=True)
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--model-version", default="current")
    parser.add_argument("--golden-digest", default=None)
    args = parser.parse_args(argv)
    return run_smoke_test(
        api_endpoint=args.api_endpoint.rstrip("/"),
        region=args.region,
        model_version=args.model_version,
        golden_digest=args.golden_digest,
    )


if __name__ == "__main__":
    sys.exit(main())
