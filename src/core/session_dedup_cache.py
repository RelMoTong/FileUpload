"""Bounded, process-lifetime duplicate cache.

The cache deliberately has no disk representation.  It is rebuilt from the
target directory for each worker run and therefore cannot become a second
database or survive a restart with stale identities.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Iterable


class SessionDedupCache:
    def __init__(self, capacity: int = 4096) -> None:
        self.capacity = max(1, int(capacity))
        self._items: OrderedDict[tuple[int, str], str] = OrderedDict()

    def clear(self) -> None:
        self._items.clear()

    def put(self, size: int, digest: str, path: str) -> None:
        if not digest:
            return
        key = (int(size), str(digest))
        self._items[key] = str(path)
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def remove_path(self, path: str) -> None:
        for key, value in tuple(self._items.items()):
            if value == str(path):
                self._items.pop(key, None)

    def items(self) -> Iterable[tuple[tuple[int, str], str]]:
        return tuple(self._items.items())
