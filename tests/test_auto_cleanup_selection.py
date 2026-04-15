# -*- coding: utf-8 -*-
"""
自动清理候选文件选取逻辑测试
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ui.main_window import MainWindow


class TestAutoCleanupSelection(unittest.TestCase):
    def test_select_cleanup_candidates_keeps_oldest_prefix(self) -> None:
        files = [
            (1.0, 40, "oldest-a"),
            (2.0, 50, "oldest-b"),
            (3.0, 60, "newer-c"),
            (4.0, 70, "newest-d"),
        ]

        candidates, scanned = MainWindow._select_cleanup_candidates(files, 90)

        self.assertEqual(scanned, 4)
        self.assertEqual([path for _, _, path in candidates], ["oldest-a", "oldest-b"])

    def test_select_cleanup_candidates_drops_newer_large_file_when_older_files_are_enough(self) -> None:
        files = [
            (1.0, 30, "old-a"),
            (2.0, 30, "old-b"),
            (3.0, 100, "new-c"),
        ]

        candidates, scanned = MainWindow._select_cleanup_candidates((item for item in files), 50)

        self.assertEqual(scanned, 3)
        self.assertEqual([path for _, _, path in candidates], ["old-a", "old-b"])

    def test_select_cleanup_candidates_returns_all_when_space_target_exceeds_total(self) -> None:
        files = [
            (10.0, 15, "a"),
            (20.0, 25, "b"),
        ]

        candidates, scanned = MainWindow._select_cleanup_candidates(files, 100)

        self.assertEqual(scanned, 2)
        self.assertEqual([path for _, _, path in candidates], ["a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
