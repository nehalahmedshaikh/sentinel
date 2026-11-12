"""Canonical policy and assessment identity fingerprints."""

import hashlib
import json
from typing import Any
from uuid import UUID

from sentinel.reliability.contracts import BaselineStrategy, DetectionPolicyConfig


def policy_fingerprint(policy: DetectionPolicyConfig) -> str:
    """Fingerprint every detector selection, setting, and practical threshold."""
    canonical = json.dumps(
        policy.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assessment_identity(
    target_id: UUID,
    strategy: BaselineStrategy,
    baselines: list[dict[str, Any]],
    policy_id: UUID,
    policy_hash: str,
) -> str:
    """Fingerprint target, resolved baseline identity, strategy, and policy."""
    payload = {
        "target_version_id": str(target_id),
        "baseline_strategy": strategy.value,
        "baseline_versions": sorted(baselines, key=lambda item: int(item["version_number"])),
        "policy_id": str(policy_id),
        "policy_fingerprint": policy_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
