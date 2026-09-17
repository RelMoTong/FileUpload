"""Temporary feature gates for the v3.5.1 no-database migration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


# Cross-file persistent indexes were removed in P2-01. Automatic cleanup now
# runs without a database and has no remaining feature-freeze branch.
# Deduplication remains frozen until its session-only semantics are accepted.
DEDUPLICATION_FREEZE_ACTIVE = True
DEDUPLICATION_FREEZE_NOTICE = "无数据库版智能去重暂不可启用"

FEATURE_FREEZE_FLAGS = {
    "enable_deduplication": DEDUPLICATION_FREEZE_ACTIVE,
}
STABILITY_FROZEN_BOOLEAN_KEYS = tuple(
    key for key, frozen in FEATURE_FREEZE_FLAGS.items() if frozen
)


def apply_stability_feature_freeze(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with migration-frozen features forced off."""
    result = deepcopy(config)
    for key, frozen in FEATURE_FREEZE_FLAGS.items():
        if frozen:
            result[key] = False
    return result
