"""有容量上限、仅在当前进程存活期间有效的重复文件缓存。
文件名：src/core/session_dedup_cache.py
文件作用：通用基础设施与安全边界模块“session_dedup_cache”。
主要功能：提供当前既有能力，并以中文说明固定数据、状态和调用边界。
模块关系：由上层组合根或相邻分层模块调用；不改变现有依赖方向。
阅读重点：先读公开类型/函数、关键状态和单位说明，再按调用链追踪。


该缓存刻意不落盘：每次 Worker 启动时会依据目标目录重建，因而不会形成第二套
数据库，也不会在重启后携带过期的文件身份。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Iterable


class SessionDedupCache:
    """用固定容量的最近访问映射记录当前会话已处理的文件摘要。"""
    def __init__(self, capacity: int = 4096) -> None:
        """内部辅助：完成“__init__”对应的既有局部工作。"""
        self.capacity = max(1, int(capacity))
        self._items: OrderedDict[tuple[int, str], str] = OrderedDict()

    def clear(self) -> None:
        """开始新会话前清空所有仅内存有效的缓存条目。"""
        self._items.clear()

    def put(self, size: int, digest: str, path: str) -> None:
        """记录一个文件摘要；摘要为空时不写入，超出容量时淘汰最早条目。"""
        if not digest:
            return
        # 文件大小和摘要共同构成键，避免仅凭摘要文本产生歧义。
        key = (int(size), str(digest))
        self._items[key] = str(path)
        self._items.move_to_end(key)
        # 固定容量可避免持续扫描让会话级缓存无限占用内存。
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def remove_path(self, path: str) -> None:
        """归档或删除后移除指向该路径的全部摘要记录。"""
        for key, value in tuple(self._items.items()):
            if value == str(path):
                self._items.pop(key, None)

    def items(self) -> Iterable[tuple[tuple[int, str], str]]:
        """返回不可直接修改内部映射的条目快照，供诊断或重建流程使用。"""
        return tuple(self._items.items())
