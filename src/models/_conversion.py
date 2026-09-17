"""配置模型共用的轻量类型转换与未知字段保留工具。"""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Type, TypeVar


EnumT = TypeVar("EnumT", bound=Enum)

SHARED_RETIRED_CONFIG_KEYS = frozenset(
    {
        "monitor_mode",
        "enable_resume",
        "resume_min_size_mb",
        "auto_delete_keep_days",
    }
)


def as_bool(value: Any, default: bool) -> bool:
    """把外部配置值转换为布尔值。

    用途：兼容 JSON 中可能出现的布尔、数字和字符串写法。
    输入：待转换值 ``value`` 与无效值的默认值 ``default``。
    输出：可安全传入配置模型的布尔值。
    关键步骤：优先处理真正的布尔值，再识别常见字符串和数字。
    风险点：未知字符串不能猜测其语义，必须回退到默认值。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def as_int(value: Any, default: int) -> int:
    """把配置值转换为整数，并在转换失败时保留默认值。

    用途：为数值配置提供单一、可预测的容错边界。
    输入：待转换值 ``value`` 与默认值 ``default``。
    输出：整数值或默认值。
    关键步骤：捕获类型、格式和溢出异常。
    风险点：本函数不负责范围裁剪，调用模型仍需按业务范围校正。
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def as_float(value: Any, default: float) -> float:
    """把配置值转换为浮点数，并在失败时返回默认值。

    用途：统一处理上传速率、延时等允许小数的配置项。
    输入：待转换值 ``value`` 与默认值 ``default``。
    输出：浮点数或默认值。
    关键步骤：仅执行转换和异常收敛。
    风险点：范围有效性仍由具体配置模型负责。
    """
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def as_str(value: Any, default: str = "") -> str:
    """仅接受字符串配置值，其他类型统一回退为默认值。"""
    return value if isinstance(value, str) else default


def as_str_list(value: Any) -> list[str]:
    """从可迭代配置中提取字符串列表。

    用途：防止字符串本身被误当作逐字符列表。
    输入：任意外部配置值。
    输出：只含字符串元素的新列表。
    关键步骤：先排除字符串、字节串和映射，再过滤非字符串元素。
    风险点：本函数不去重、不规范化路径，保留给对应业务模型处理。
    """
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [item for item in value if isinstance(item, str)]


def as_enum(enum_type: Type[EnumT], value: Any, default: EnumT) -> EnumT:
    """把配置值转换为指定枚举，不能识别时返回默认成员。"""
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


def unknown_fields(data: Mapping[str, Any], known_fields: Iterable[str]) -> Dict[str, Any]:
    """深拷贝并保留模型当前不认识的配置字段。

    用途：实现配置“读后再写”时不丢失未来版本或扩展字段。
    输入：原始配置映射和当前模型已知字段名。
    输出：仅包含未知字段的独立字典。
    关键步骤：建立已知字段集合，再深拷贝其余字段。
    风险点：不能直接返回原对象引用，否则调用方修改模型会污染原始配置。
    """
    known = set(known_fields)
    return {key: deepcopy(value) for key, value in data.items() if key not in known}
