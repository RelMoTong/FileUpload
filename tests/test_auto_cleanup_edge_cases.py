# -*- coding: utf-8 -*-
"""
自动清理配置保存 — 边界场景回归测试

覆盖场景：
  1. 启用自动清理但无有效目录时，保存被阻止
  2. 目标阈值 >= 触发阈值时，保存被拒绝
  3. 多目录部分有效部分无效时，保存被拦截（无效路径报错）
  4. 目录重复配置时，保存结果已去重
  5. 保存失败时不污染父窗口状态（回滚机制）
  6. 运行中禁止保存自动清理配置
  7. 禁用自动清理时跳过目录与阈值校验，保存应成功
  8. 全部目录有效时，保存成功且父窗口属性正确更新
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6 import QtWidgets  # type: ignore[import-untyped]

from src.ui.widgets import DiskCleanupDialog


def get_qt_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app  # type: ignore[return-value]


class MockMainWindow(QtWidgets.QWidget):
    """轻量级 MainWindow 替身，仅包含 DiskCleanupDialog 依赖的属性。"""

    def __init__(
        self,
        backup_path: str = "",
        target_path: str = "",
        monitor_path: str = "",
        auto_delete_folders=None,
        save_result: bool = True,
        current_role: str = "admin",
        is_running: bool = False,
    ) -> None:
        super().__init__()
        self.bak_edit = QtWidgets.QLineEdit(backup_path)
        self.tgt_edit = QtWidgets.QLineEdit(target_path)
        self.auto_delete_folder = monitor_path
        self.auto_delete_folders = list(auto_delete_folders or [])
        self.enable_auto_delete = False
        self.auto_delete_threshold = 80
        self.auto_delete_target_percent = 40
        self.auto_delete_keep_days = 10
        self.auto_delete_check_interval = 300
        self.last_config_save_error = ""
        self.save_result = save_result
        self.save_calls = 0
        self.current_role = current_role
        self.is_running = is_running

    def _save_auto_cleanup_config(self, cleanup_config: dict) -> bool:
        self.save_calls += 1
        if not self.save_result:
            if not self.last_config_save_error:
                self.last_config_save_error = "模拟保存失败"
            return False
        self.enable_auto_delete = cleanup_config["enable_auto_delete"]
        self.auto_delete_folders = list(cleanup_config["auto_delete_folders"])
        self.auto_delete_folder = self.auto_delete_folders[0] if self.auto_delete_folders else ""
        self.auto_delete_threshold = cleanup_config["auto_delete_threshold"]
        self.auto_delete_target_percent = cleanup_config["auto_delete_target_percent"]
        self.auto_delete_check_interval = cleanup_config["auto_delete_check_interval"]
        return True


class TestAutoCleanupSaveEdgeCases(unittest.TestCase):
    """DiskCleanupDialog._save_auto_config 边界场景"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = get_qt_app()

    # ---- helpers ----

    def _make_dialog(self, parent: MockMainWindow) -> DiskCleanupDialog:
        dialog = DiskCleanupDialog(parent)
        # 确保自动清理配置控件已创建
        dialog._test_auto_group = dialog._create_auto_cleanup_group()
        return dialog

    def _save_with_mocks(self, dialog: DiskCleanupDialog):
        """执行 _save_auto_config 并屏蔽 QMessageBox 弹窗。"""
        with mock.patch.object(QtWidgets.QMessageBox, "information") as info, \
             mock.patch.object(QtWidgets.QMessageBox, "warning") as warn:
            result = dialog._save_auto_config()
        return result, info, warn

    # ---- 场景 1：启用自动清理但无有效目录 ----

    def test_enabled_without_any_directory_blocks_save(self) -> None:
        """启用自动清理，4 个文件夹全都为空 → 保存应被阻止。"""
        parent = MockMainWindow()
        dialog = self._make_dialog(parent)
        dialog.cb_enable_auto.setChecked(True)
        dialog.cb_backup.setChecked(False)
        dialog.cb_target.setChecked(False)
        dialog.cb_monitor.setChecked(False)
        dialog.cb_custom.setChecked(False)

        result, info, warn = self._save_with_mocks(dialog)

        self.assertFalse(result)
        self.assertEqual(parent.save_calls, 0, "不应调用父窗口保存")
        warn.assert_called()
        info.assert_not_called()

        dialog.close()
        parent.close()

    def test_enabled_with_all_empty_paths_blocks_save(self) -> None:
        """启用自动清理，勾选了复选框但路径全空白 → 保存应被阻止。"""
        parent = MockMainWindow()
        dialog = self._make_dialog(parent)
        dialog.cb_enable_auto.setChecked(True)
        dialog.cb_backup.setChecked(True)
        dialog.edit_backup.setText("   ")
        dialog.cb_target.setChecked(True)
        dialog.edit_target.setText("")
        dialog.cb_monitor.setChecked(False)
        dialog.cb_custom.setChecked(False)

        result, info, warn = self._save_with_mocks(dialog)

        self.assertFalse(result)
        self.assertEqual(parent.save_calls, 0)

        dialog.close()
        parent.close()

    # ---- 场景 2：目标阈值 >= 触发阈值 ----

    def test_target_equals_threshold_blocks_save(self) -> None:
        """目标阈值 == 触发阈值 → 保存应被拒绝。"""
        with tempfile.TemporaryDirectory() as d:
            parent = MockMainWindow(backup_path=d, auto_delete_folders=[d])
            dialog = self._make_dialog(parent)
            dialog.cb_enable_auto.setChecked(True)
            dialog.cb_backup.setChecked(True)
            dialog.spin_threshold.setValue(70)
            dialog.spin_target.setValue(70)

            result, info, warn = self._save_with_mocks(dialog)

            self.assertFalse(result)
            self.assertEqual(parent.save_calls, 0)
            warn.assert_called()

            dialog.close()
            parent.close()

    def test_target_greater_than_threshold_blocks_save(self) -> None:
        """目标阈值 > 触发阈值 → 保存应被拒绝。"""
        with tempfile.TemporaryDirectory() as d:
            parent = MockMainWindow(backup_path=d, auto_delete_folders=[d])
            dialog = self._make_dialog(parent)
            dialog.cb_enable_auto.setChecked(True)
            dialog.cb_backup.setChecked(True)
            dialog.spin_threshold.setValue(60)
            dialog.spin_target.setValue(70)

            result, info, warn = self._save_with_mocks(dialog)

            self.assertFalse(result)
            self.assertEqual(parent.save_calls, 0)
            warn.assert_called()

            dialog.close()
            parent.close()

    # ---- 场景 3：多目录部分有效部分无效 ----

    def test_mixed_valid_invalid_dirs_blocks_save_when_enabled(self) -> None:
        """启用状态下，部分路径存在、部分不存在 → 保存应失败（校验不通过）。"""
        with tempfile.TemporaryDirectory() as valid_dir:
            nonexist = os.path.join(valid_dir, "不存在的子路径")
            parent = MockMainWindow(
                backup_path=valid_dir,
                target_path=nonexist,
                auto_delete_folders=[valid_dir, nonexist],
            )
            dialog = self._make_dialog(parent)
            dialog.cb_enable_auto.setChecked(True)
            dialog.cb_backup.setChecked(True)
            dialog.cb_target.setChecked(True)
            dialog.cb_monitor.setChecked(False)
            dialog.cb_custom.setChecked(False)

            result, info, warn = self._save_with_mocks(dialog)

            self.assertFalse(result)
            self.assertEqual(parent.save_calls, 0)
            warn.assert_called()
            # 确认警告消息中包含不可用路径
            call_args = warn.call_args
            self.assertIn("不可用", call_args[0][2])

            dialog.close()
            parent.close()

    # ---- 场景 4：重复目录去重 ----

    def test_duplicate_directories_are_deduplicated_on_save(self) -> None:
        """相同路径被多次选中 → 保存到父窗口时应去重。"""
        with tempfile.TemporaryDirectory() as d:
            parent = MockMainWindow(
                backup_path=d,
                target_path=d,
                monitor_path=d,
            )
            dialog = self._make_dialog(parent)
            dialog.cb_enable_auto.setChecked(True)
            dialog.cb_backup.setChecked(True)
            dialog.cb_target.setChecked(True)
            dialog.cb_monitor.setChecked(True)
            dialog.cb_custom.setChecked(False)

            result, info, warn = self._save_with_mocks(dialog)

            self.assertTrue(result)
            # 虽然勾选了三个但路径相同，期望只保存一个
            self.assertEqual(parent.auto_delete_folders, [d])
            info.assert_called_once()
            warn.assert_not_called()

            dialog.close()
            parent.close()

    # ---- 场景 5：保存失败不污染父窗口 ----

    def test_save_failure_does_not_pollute_parent_state(self) -> None:
        """父窗口 _save_auto_cleanup_config 返回 False → 父属性不应被修改。"""
        with tempfile.TemporaryDirectory() as d:
            parent = MockMainWindow(
                backup_path=d,
                auto_delete_folders=[d],
                save_result=False,
            )
            # 保存前的快照
            orig_enable = parent.enable_auto_delete
            orig_folders = list(parent.auto_delete_folders)
            orig_threshold = parent.auto_delete_threshold
            orig_target = parent.auto_delete_target_percent

            dialog = self._make_dialog(parent)
            dialog.cb_enable_auto.setChecked(True)
            dialog.cb_backup.setChecked(True)
            dialog.spin_threshold.setValue(90)
            dialog.spin_target.setValue(50)

            result, info, warn = self._save_with_mocks(dialog)

            self.assertFalse(result)
            # 父窗口状态应保持不变（MockMainWindow.save_result=False 不会更新属性）
            self.assertEqual(parent.enable_auto_delete, orig_enable)
            self.assertEqual(parent.auto_delete_threshold, orig_threshold)
            self.assertEqual(parent.auto_delete_target_percent, orig_target)
            warn.assert_called()

            dialog.close()
            parent.close()

    # ---- 场景 6：运行中禁止保存 ----

    def test_saving_blocked_when_upload_is_running(self) -> None:
        """is_running=True → 保存应被阻止（权限检查）。"""
        with tempfile.TemporaryDirectory() as d:
            parent = MockMainWindow(
                backup_path=d,
                auto_delete_folders=[d],
                current_role="admin",
                is_running=True,
            )
            dialog = self._make_dialog(parent)
            dialog.cb_enable_auto.setChecked(True)
            dialog.cb_backup.setChecked(True)

            result, info, warn = self._save_with_mocks(dialog)

            self.assertFalse(result)
            self.assertEqual(parent.save_calls, 0)
            warn.assert_called()

            dialog.close()
            parent.close()

    # ---- 场景 7：禁用自动清理时跳过目录/阈值校验 ----

    def test_disabled_auto_cleanup_skips_validation(self) -> None:
        """关闭自动清理 → 不校验目录和阈值，直接保存成功。"""
        parent = MockMainWindow()
        dialog = self._make_dialog(parent)
        dialog.cb_enable_auto.setChecked(False)
        # 故意不勾选任何目录
        dialog.cb_backup.setChecked(False)
        dialog.cb_target.setChecked(False)
        dialog.cb_monitor.setChecked(False)
        dialog.cb_custom.setChecked(False)

        result, info, warn = self._save_with_mocks(dialog)

        self.assertTrue(result)
        self.assertFalse(parent.enable_auto_delete)
        self.assertEqual(parent.save_calls, 1)
        info.assert_called_once()
        warn.assert_not_called()

        dialog.close()
        parent.close()

    # ---- 场景 8：全部目录有效时正确更新 ----

    def test_all_valid_dirs_save_updates_parent(self) -> None:
        """多个有效目录 + 合法阈值 → 保存成功，父窗口属性全部更新。"""
        with tempfile.TemporaryDirectory() as d1, \
             tempfile.TemporaryDirectory() as d2:
            parent = MockMainWindow(
                backup_path=d1,
                target_path=d2,
            )
            dialog = self._make_dialog(parent)
            dialog.cb_enable_auto.setChecked(True)
            dialog.cb_backup.setChecked(True)
            dialog.cb_target.setChecked(True)
            dialog.cb_monitor.setChecked(False)
            dialog.cb_custom.setChecked(False)
            dialog.spin_threshold.setValue(85)
            dialog.spin_target.setValue(50)
            dialog.spin_check_interval.setValue(120)

            result, info, warn = self._save_with_mocks(dialog)

            self.assertTrue(result)
            self.assertTrue(parent.enable_auto_delete)
            self.assertEqual(parent.auto_delete_folders, [d1, d2])
            self.assertEqual(parent.auto_delete_threshold, 85)
            self.assertEqual(parent.auto_delete_target_percent, 50)
            self.assertEqual(parent.auto_delete_check_interval, 120)
            info.assert_called_once()
            warn.assert_not_called()

            dialog.close()
            parent.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
