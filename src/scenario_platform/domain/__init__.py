"""Pure domain layer over the existing quantitative core (architecture plan Section 7).

No boto3, no botocore, no AWS concept, no infrastructure environment
configuration (AGENTS.md invariant 28, enforced by
``tests/test_no_aws_in_core.py``). Everything here is plain Python: frozen
dataclasses and pure functions over ``xtra_takehome``, importable from a
notebook with no cloud credentials in sight.
"""

from __future__ import annotations
