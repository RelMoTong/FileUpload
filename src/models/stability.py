"""
文件名：src/models/stability.py
文件作用：纯数据模型与业务契约模块“stability”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。

v3.5.1 无数据库迁移期间的临时功能冻结开关。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


# P2-01 已移除跨文件持久化索引；自动清理无需数据库，也没有遗留冻结分支。
# 去重功能仍保持冻结，直到仅限当前会话的语义得到确认。
DEDUPLICATION_FREEZE_ACTIVE = True
DEDUPLICATION_FREEZE_NOTICE = "无数据库版智能去重暂不可启用"

FEATURE_FREEZE_FLAGS = {
    "enable_deduplication": DEDUPLICATION_FREEZE_ACTIVE,
}
STABILITY_FROZEN_BOOLEAN_KEYS = tuple(
    key for key, frozen in FEATURE_FREEZE_FLAGS.items() if frozen
)


def apply_stability_feature_freeze(config: Dict[str, Any]) -> Dict[str, Any]:
    """返回关闭迁移冻结功能后的配置副本。

    用途：保证无数据库版本不会因旧配置重新启用尚未交付的功能。
    输入：待加载的原始配置字典。
    输出：被冻结开关强制为 ``False`` 的独立字典。
    关键步骤：深拷贝输入后，仅覆盖当前标记为冻结的布尔字段。
    风险点：不得原地修改输入配置，否则可能影响同一配置对象的其他读取方。
    """
    result = deepcopy(config)
    for key, frozen in FEATURE_FREEZE_FLAGS.items():
        if frozen:
            result[key] = False
    return result
