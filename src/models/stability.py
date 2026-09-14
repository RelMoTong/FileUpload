"""Temporary feature gates for the v3.5.1 no-database migration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


# Cross-file persistent indexes were removed in P2-01.  These flags remain
# disabled during final validation because the product supports only
# session-scoped deduplication.
STABILITY_FREEZE_ACTIVE = True
STABILITY_FREEZE_NOTICE = "无数据库改造中不可启用"
STABILITY_FROZEN_BOOLEAN_KEYS = (
    "enable_deduplication",
    "enable_auto_delete",
)


def apply_stability_feature_freeze(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with migration-frozen features forced off."""
    result = deepcopy(config)
    if STABILITY_FREEZE_ACTIVE:
        for key in STABILITY_FROZEN_BOOLEAN_KEYS:
            result[key] = False
    return result
