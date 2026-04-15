# -*- coding: utf-8 -*-
"""
自动清理执行逻辑 — 边界场景回归测试

覆盖场景：
  1. 阈值配置无效 (target >= threshold) → 跳过执行
  2. 多目录中部分不存在 → 记录日志并继续处理其他目录
  3. _get_auto_cleanup_folders 重复路径去重
  4. 文件在候选生成后被删除/占用 → 计入 failed，不崩溃
  5. 达到目标占用率后停止 → 不多删
  6. 回收站不可用 → 跳过执行
  7. _select_cleanup_candidates: bytes_to_free <= 0 → 返回空
  8. _maybe_trigger_auto_cleanup 并发锁保护
  9. _update_auto_cleanup_schedule 各分支
 10. 大目录扫描日志输出验证
"""

import os
import sys
import shutil
import threading
import unittest
from pathlib import Path
from typing import Any, Iterable, List, Tuple
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ui.main_window import MainWindow


# ===========================================================================
# Helpers — 不实例化完整 MainWindow，直接测试 @staticmethod 和可独立调用的方法
# ===========================================================================

class FakeMainWindow:
    """仅包含自动清理逻辑所需属性的假主窗口，避免初始化完整 GUI。"""

    _cleanup_executor: Any
    _auto_cleanup_timer: Any
    _auto_cleanup_task: Any

    def __init__(self) -> None:
        self.enable_auto_delete = True
        self.auto_delete_folder = ""
        self.auto_delete_folders: List[str] = []
        self.auto_delete_threshold = 80
        self.auto_delete_target_percent = 40
        self.auto_delete_check_interval = 300

        self._auto_cleanup_running = False
        self._auto_cleanup_lock = threading.Lock()

        self._log_messages: List[str] = []

    def _emit_async_log(self, msg: str) -> None:
        self._log_messages.append(msg)

    def _append_log(self, msg: str) -> None:
        self._log_messages.append(msg)

    def _get_auto_cleanup_folders(self) -> List[str]:
        """复制自 MainWindow._get_auto_cleanup_folders 的去重逻辑。"""
        return MainWindow._get_auto_cleanup_folders(self)  # type: ignore[arg-type]

    @staticmethod
    def _select_cleanup_candidates(
        file_infos: Iterable[Tuple[float, int, str]],
        bytes_to_free: int,
    ) -> Tuple[List[Tuple[float, int, str]], int]:
        return MainWindow._select_cleanup_candidates(file_infos, bytes_to_free)


# ===========================================================================
# 测试类
# ===========================================================================


class TestGetAutoCleanupFolders(unittest.TestCase):
    """_get_auto_cleanup_folders 去重与回退逻辑"""

    def test_duplicate_paths_are_deduplicated(self) -> None:
        fw = FakeMainWindow()
        fw.auto_delete_folders = ["/a", "/b", "/a", "/c", "/b"]
        result = fw._get_auto_cleanup_folders()
        self.assertEqual(result, ["/a", "/b", "/c"])

    def test_fallback_to_single_folder(self) -> None:
        fw = FakeMainWindow()
        fw.auto_delete_folders = []
        fw.auto_delete_folder = "/legacy"
        result = fw._get_auto_cleanup_folders()
        self.assertEqual(result, ["/legacy"])

    def test_empty_and_whitespace_paths_filtered(self) -> None:
        fw = FakeMainWindow()
        fw.auto_delete_folders = ["", "  ", "/valid", " "]
        result = fw._get_auto_cleanup_folders()
        self.assertEqual(result, ["/valid"])

    def test_both_empty(self) -> None:
        fw = FakeMainWindow()
        fw.auto_delete_folders = []
        fw.auto_delete_folder = ""
        result = fw._get_auto_cleanup_folders()
        self.assertEqual(result, [])


class TestSelectCleanupCandidatesEdge(unittest.TestCase):
    """_select_cleanup_candidates 边界条件"""

    def test_bytes_to_free_zero_returns_empty(self) -> None:
        files = [(1.0, 100, "a"), (2.0, 200, "b")]
        candidates, scanned = MainWindow._select_cleanup_candidates(files, 0)
        self.assertEqual(candidates, [])
        self.assertEqual(scanned, 0)

    def test_bytes_to_free_negative_returns_empty(self) -> None:
        files = [(1.0, 100, "a")]
        candidates, scanned = MainWindow._select_cleanup_candidates(files, -50)
        self.assertEqual(candidates, [])
        self.assertEqual(scanned, 0)

    def test_single_file_exact_match(self) -> None:
        files = [(10.0, 500, "only")]
        candidates, scanned = MainWindow._select_cleanup_candidates(files, 500)
        self.assertEqual(scanned, 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][2], "only")

    def test_empty_input(self) -> None:
        candidates, scanned = MainWindow._select_cleanup_candidates(iter([]), 100)
        self.assertEqual(candidates, [])
        self.assertEqual(scanned, 0)


class TestAutoCleanupTask(unittest.TestCase):
    """_auto_cleanup_task 执行逻辑边界测试"""

    def _make_fake(self) -> FakeMainWindow:
        return FakeMainWindow()

    # ---- 场景 1：阈值配置无效 → 跳过 ----

    def test_invalid_threshold_config_skips_execution(self) -> None:
        """target_percent >= threshold → 任务应记录日志并跳过。"""
        fw = self._make_fake()
        fw.auto_delete_folders = ["/some/path"]
        fw.auto_delete_target_percent = 80
        fw.auto_delete_threshold = 80

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", return_value=True):
            # 直接调用 _auto_cleanup_task 方法（绑定到假窗口）
            MainWindow._auto_cleanup_task(fw)  # type: ignore[arg-type]

        self.assertTrue(any("阈值配置无效" in m for m in fw._log_messages))

    # ---- 场景 2：多目录部分不存在 → 记录并继续 ----

    def test_nonexistent_dir_logged_and_skipped(self) -> None:
        """部分目录不存在 → 记录日志，继续处理其他目录。"""
        fw = self._make_fake()
        fw.auto_delete_folders = ["/valid", "/nonexist"]
        fw.auto_delete_threshold = 80
        fw.auto_delete_target_percent = 40

        def fake_isdir(p):
            return p == "/valid"

        # disk_usage 对 /valid 返回高使用率
        fake_usage = mock.Mock()
        fake_usage.total = 1000
        fake_usage.free = 100  # 90% 使用率

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", side_effect=fake_isdir), \
             mock.patch("shutil.disk_usage", return_value=fake_usage), \
             mock.patch("os.walk", return_value=iter([])):
            MainWindow._auto_cleanup_task(fw)  # type: ignore[arg-type]

        self.assertTrue(any("不可用" in m and "/nonexist" in m for m in fw._log_messages))

    # ---- 场景 3：回收站不可用 → 跳过 ----

    def test_trash_not_supported_skips_execution(self) -> None:
        fw = self._make_fake()
        fw.auto_delete_folders = ["/some/path"]

        with mock.patch("src.ui.main_window.trash_supported", return_value=False):
            MainWindow._auto_cleanup_task(fw)  # type: ignore[arg-type]

        self.assertTrue(any("回收站不可用" in m for m in fw._log_messages))

    # ---- 场景 4：文件删除失败（被占用/已删除）→ 不崩溃 ----

    def test_file_delete_failure_counted_and_does_not_crash(self) -> None:
        """候选文件在删除时抛异常 → 计入 failed 但流程不中断。"""
        fw = self._make_fake()
        fw.auto_delete_folders = ["/clean"]
        fw.auto_delete_threshold = 80
        fw.auto_delete_target_percent = 40

        fake_usage = mock.Mock()
        fake_usage.total = 1000
        fake_usage.free = 100  # 90% used

        clean_dir = "/clean"
        walk_result = [(clean_dir, [], ["a.txt", "b.txt", "c.txt"])]

        stat_map = {
            os.path.join(clean_dir, "a.txt"): mock.Mock(st_mtime=1.0, st_size=200),
            os.path.join(clean_dir, "b.txt"): mock.Mock(st_mtime=2.0, st_size=200),
            os.path.join(clean_dir, "c.txt"): mock.Mock(st_mtime=3.0, st_size=200),
        }

        def fake_stat(p):
            if p in stat_map:
                return stat_map[p]
            raise FileNotFoundError(p)

        call_count = {"trash": 0}

        def fake_send_to_trash(p):
            call_count["trash"] += 1
            if p.endswith("a.txt"):
                raise PermissionError("文件被占用")
            # b.txt 和 c.txt 成功

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("shutil.disk_usage", return_value=fake_usage), \
             mock.patch("os.walk", return_value=iter(walk_result)), \
             mock.patch("os.stat", side_effect=fake_stat), \
             mock.patch("src.ui.main_window.send_to_trash", side_effect=fake_send_to_trash):
            MainWindow._auto_cleanup_task(fw)  # type: ignore[arg-type]

        # 应有完成日志且提到失败数 >= 1
        completion_msgs = [m for m in fw._log_messages if "自动清理完成" in m]
        self.assertTrue(len(completion_msgs) > 0, f"应有完成日志，实际日志: {fw._log_messages}")
        self.assertTrue(any("失败" in m for m in completion_msgs))

    # ---- 场景 5：达到目标后停止 → 不多删 ----

    def test_stops_deleting_after_reaching_target(self) -> None:
        """需要释放 200 字节，有 4 个候选文件 → 删够后停止。"""
        fw = self._make_fake()
        fw.auto_delete_folders = ["/clean"]
        fw.auto_delete_threshold = 80
        fw.auto_delete_target_percent = 60

        fake_usage = mock.Mock()
        fake_usage.total = 1000
        fake_usage.free = 100  # 90% used → 需要降到 60%，释放 300 字节

        clean_dir = "/clean"
        walk_result = [(clean_dir, [], ["oldest.txt", "old.txt", "new.txt", "newest.txt"])]
        stat_map = {
            os.path.join(clean_dir, "oldest.txt"): mock.Mock(st_mtime=1.0, st_size=150),
            os.path.join(clean_dir, "old.txt"): mock.Mock(st_mtime=2.0, st_size=150),
            os.path.join(clean_dir, "new.txt"): mock.Mock(st_mtime=3.0, st_size=150),
            os.path.join(clean_dir, "newest.txt"): mock.Mock(st_mtime=4.0, st_size=150),
        }

        deleted_files: List[str] = []

        def fake_stat(p):
            if p in stat_map:
                return stat_map[p]
            raise FileNotFoundError(p)

        def fake_send_to_trash(p):
            deleted_files.append(p)

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("shutil.disk_usage", return_value=fake_usage), \
             mock.patch("os.walk", return_value=iter(walk_result)), \
             mock.patch("os.stat", side_effect=fake_stat), \
             mock.patch("src.ui.main_window.send_to_trash", side_effect=fake_send_to_trash):
            MainWindow._auto_cleanup_task(fw)  # type: ignore[arg-type]

        # bytes_to_free = 900 - 600 = 300, 候选选出最旧的文件直到够 300 字节
        # 但在删除循环中还有 "deleted_size >= bytes_to_free" 停止条件
        # 确保不会删除全部 4 个文件（600 > 300，不需要全删）
        self.assertLess(len(deleted_files), 4,
                        f"不应删除全部文件，实际删除: {deleted_files}")
        total_deleted_size = sum(stat_map[p].st_size for p in deleted_files)
        self.assertGreaterEqual(total_deleted_size, 300,
                                "删除总量应足以达到目标")

    # ---- 场景 6：目录在执行过程中失效 ----

    def test_directory_becoming_invalid_during_execution(self) -> None:
        """两个目录，第一个在遍历时 disk_usage 抛异常 → 记录并继续处理第二个。"""
        fw = self._make_fake()
        fw.auto_delete_folders = ["/failing", "/working"]
        fw.auto_delete_threshold = 80
        fw.auto_delete_target_percent = 40

        call_order = []

        def fake_isdir(p):
            return True

        def fake_disk_usage(p):
            call_order.append(p)
            if p == "/failing":
                raise OSError("设备不可达")
            usage = mock.Mock()
            usage.total = 1000
            usage.free = 800  # 20% used → 低于阈值
            return usage

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", side_effect=fake_isdir), \
             mock.patch("shutil.disk_usage", side_effect=fake_disk_usage):
            MainWindow._auto_cleanup_task(fw)  # type: ignore[arg-type]

        # 验证两个目录都被尝试了
        self.assertIn("/failing", call_order)
        self.assertIn("/working", call_order)
        # 验证有日志记录 disk_usage 失败
        self.assertTrue(any("无法获取磁盘" in m and "/failing" in m for m in fw._log_messages))

    # ---- 场景 7：_maybe_trigger_auto_cleanup 并发锁 ----

    def test_concurrent_trigger_is_blocked_by_lock(self) -> None:
        """已有清理在运行时，再次触发应被锁阻止。"""
        fw = self._make_fake()
        fw.auto_delete_folders = ["/some"]
        fw._auto_cleanup_running = True  # 模拟正在运行

        submitted = []

        class FakeExecutor:
            def submit(self, fn):
                submitted.append(fn)

        fw._cleanup_executor = FakeExecutor()

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = mock.Mock(total=1000, free=100)
            MainWindow._maybe_trigger_auto_cleanup(fw, "测试")  # type: ignore[arg-type]

        # 因为 _auto_cleanup_running=True，不应提交新任务
        self.assertEqual(len(submitted), 0)

    # ---- 场景 8：_maybe_trigger_auto_cleanup 正常触发 ----

    def test_trigger_submits_task_when_threshold_exceeded(self) -> None:
        fw = self._make_fake()
        fw.auto_delete_folders = ["/some"]
        fw._auto_cleanup_running = False

        submitted = []

        class FakeExecutor:
            def submit(self, fn):
                submitted.append(fn)

        fw._cleanup_executor = FakeExecutor()
        # _maybe_trigger_auto_cleanup 提交 self._auto_cleanup_task
        fw._auto_cleanup_task = lambda: None

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = mock.Mock(total=1000, free=100)
            MainWindow._maybe_trigger_auto_cleanup(fw, "测试")  # type: ignore[arg-type]

        self.assertEqual(len(submitted), 1)
        self.assertTrue(fw._auto_cleanup_running)

    # ---- 场景 9：磁盘低于阈值不触发 ----

    def test_no_trigger_when_disk_below_threshold(self) -> None:
        fw = self._make_fake()
        fw.auto_delete_folders = ["/some"]
        fw.auto_delete_threshold = 80

        submitted = []

        class FakeExecutor:
            def submit(self, fn):
                submitted.append(fn)

        fw._cleanup_executor = FakeExecutor()

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = mock.Mock(total=1000, free=300)  # 70%
            MainWindow._maybe_trigger_auto_cleanup(fw, "测试")  # type: ignore[arg-type]

        self.assertEqual(len(submitted), 0)

    # ---- 场景 10：enable_auto_delete=False 不触发 ----

    def test_disabled_auto_cleanup_does_not_trigger(self) -> None:
        fw = self._make_fake()
        fw.enable_auto_delete = False
        fw.auto_delete_folders = ["/some"]

        submitted = []

        class FakeExecutor:
            def submit(self, fn):
                submitted.append(fn)

        fw._cleanup_executor = FakeExecutor()

        MainWindow._maybe_trigger_auto_cleanup(fw, "测试")  # type: ignore[arg-type]
        self.assertEqual(len(submitted), 0)


class TestAutoCleanupSchedule(unittest.TestCase):
    """_update_auto_cleanup_schedule 各分支覆盖"""

    def _make_fake_with_timer(self) -> FakeMainWindow:
        fw = FakeMainWindow()
        fw._auto_cleanup_timer = mock.MagicMock()
        return fw

    def test_disabled_stops_timer(self) -> None:
        fw = self._make_fake_with_timer()
        fw.enable_auto_delete = False
        MainWindow._update_auto_cleanup_schedule(fw)  # type: ignore[arg-type]
        fw._auto_cleanup_timer.stop.assert_called()

    def test_no_folders_stops_timer(self) -> None:
        fw = self._make_fake_with_timer()
        fw.enable_auto_delete = True
        fw.auto_delete_folders = []
        fw.auto_delete_folder = ""
        MainWindow._update_auto_cleanup_schedule(fw)  # type: ignore[arg-type]
        fw._auto_cleanup_timer.stop.assert_called()
        self.assertTrue(any("未设置清理路径" in m for m in fw._log_messages))

    def test_all_invalid_folders_stops_timer(self) -> None:
        fw = self._make_fake_with_timer()
        fw.enable_auto_delete = True
        fw.auto_delete_folders = ["/nonexist_a", "/nonexist_b"]
        with mock.patch("os.path.isdir", return_value=False):
            MainWindow._update_auto_cleanup_schedule(fw)  # type: ignore[arg-type]
        fw._auto_cleanup_timer.stop.assert_called()
        self.assertTrue(any("不可用" in m for m in fw._log_messages))

    def test_trash_not_supported_stops_timer(self) -> None:
        fw = self._make_fake_with_timer()
        fw.enable_auto_delete = True
        fw.auto_delete_folders = ["/valid"]
        with mock.patch("os.path.isdir", return_value=True), \
             mock.patch("src.ui.main_window.trash_supported", return_value=False):
            MainWindow._update_auto_cleanup_schedule(fw)  # type: ignore[arg-type]
        fw._auto_cleanup_timer.stop.assert_called()

    def test_valid_config_starts_timer(self) -> None:
        fw = self._make_fake_with_timer()
        fw.enable_auto_delete = True
        fw.auto_delete_folders = ["/valid"]
        fw.auto_delete_check_interval = 180
        with mock.patch("os.path.isdir", return_value=True), \
             mock.patch("src.ui.main_window.trash_supported", return_value=True):
            MainWindow._update_auto_cleanup_schedule(fw)  # type: ignore[arg-type]
        fw._auto_cleanup_timer.start.assert_called_with(180 * 1000)

    def test_interval_clamped_to_minimum_60(self) -> None:
        fw = self._make_fake_with_timer()
        fw.enable_auto_delete = True
        fw.auto_delete_folders = ["/valid"]
        fw.auto_delete_check_interval = 10  # 低于最小值 60
        with mock.patch("os.path.isdir", return_value=True), \
             mock.patch("src.ui.main_window.trash_supported", return_value=True):
            MainWindow._update_auto_cleanup_schedule(fw)  # type: ignore[arg-type]
        fw._auto_cleanup_timer.start.assert_called_with(60 * 1000)


class TestAutoCleanupTaskScanLog(unittest.TestCase):
    """大量文件扫描时的日志输出验证"""

    def test_scan_progress_logged_every_5000_files(self) -> None:
        """验证扫描超过 5000 文件时有进度日志。"""
        fw = FakeMainWindow()
        fw.auto_delete_folders = ["/bigdir"]
        fw.auto_delete_threshold = 80
        fw.auto_delete_target_percent = 40

        fake_usage = mock.Mock()
        fake_usage.total = 10_000_000
        fake_usage.free = 1_000_000  # 90% used

        # 生成 6000 个文件
        file_names = [f"file_{i}.dat" for i in range(6000)]
        walk_result = [("/bigdir", [], file_names)]

        call_count = {"stat": 0}

        def fake_stat(p):
            call_count["stat"] += 1
            return mock.Mock(st_mtime=float(call_count["stat"]), st_size=100)

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("shutil.disk_usage", return_value=fake_usage), \
             mock.patch("os.walk", return_value=iter(walk_result)), \
             mock.patch("os.stat", side_effect=fake_stat), \
             mock.patch("src.ui.main_window.send_to_trash"):
            MainWindow._auto_cleanup_task(fw)  # type: ignore[arg-type]

        # 验证有扫描进度日志（每 5000 个文件一条）
        scan_progress = [m for m in fw._log_messages if "已遍历" in m and "5000" in m]
        self.assertGreaterEqual(len(scan_progress), 1,
                                f"应有 5000 文件进度日志，实际日志: {fw._log_messages}")


class TestAutoCleanupFileMissing(unittest.TestCase):
    """文件在候选生成后被外部删除"""

    def test_file_deleted_between_scan_and_delete(self) -> None:
        """stat 成功但 send_to_trash 时文件已不存在 → 计入失败。"""
        fw = FakeMainWindow()
        fw.auto_delete_folders = ["/dir"]
        fw.auto_delete_threshold = 80
        fw.auto_delete_target_percent = 40

        fake_usage = mock.Mock()
        fake_usage.total = 1000
        fake_usage.free = 100

        dir_path = "/dir"
        walk_result = [(dir_path, [], ["gone.txt", "ok.txt"])]
        stat_map = {
            os.path.join(dir_path, "gone.txt"): mock.Mock(st_mtime=1.0, st_size=300),
            os.path.join(dir_path, "ok.txt"): mock.Mock(st_mtime=2.0, st_size=300),
        }

        def fake_stat(p):
            return stat_map[p]

        def fake_send_to_trash(p):
            if "gone" in p:
                raise FileNotFoundError("文件已被删除")

        with mock.patch("src.ui.main_window.trash_supported", return_value=True), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("shutil.disk_usage", return_value=fake_usage), \
             mock.patch("os.walk", return_value=iter(walk_result)), \
             mock.patch("os.stat", side_effect=fake_stat), \
             mock.patch("src.ui.main_window.send_to_trash", side_effect=fake_send_to_trash):
            MainWindow._auto_cleanup_task(fw)  # type: ignore[arg-type]

        completion = [m for m in fw._log_messages if "自动清理完成" in m]
        self.assertTrue(len(completion) > 0)
        # 应至少有 1 个失败
        self.assertTrue(any("失败" in m and "1" in m for m in completion) or
                        any("失败 1" in m for m in completion),
                        f"应记录 1 个失败，实际日志: {completion}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
