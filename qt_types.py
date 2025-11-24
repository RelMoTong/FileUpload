"""
Qt 类型提示和枚举访问辅助
为 PySide6/PyQt5 提供类型安全的枚举访问，避免使用 type: ignore
"""
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    # 仅用于类型检查时的导入
    try:
        from PySide6 import QtWidgets, QtCore
    except ImportError:
        from PyQt5 import QtWidgets, QtCore  # type: ignore


# ============================================
# 类型安全的枚举访问器
# ============================================

class QtEnumAccessor:
    """Qt 枚举的类型安全访问器
    
    解决 PySide6 强类型枚举在 Pylance 中的类型检查问题
    通过延迟获取枚举值，避免直接使用 type: ignore
    """
    
    _qt_widgets: Any = None
    _qt_core: Any = None
    
    @classmethod
    def _ensure_qt_imported(cls):
        """确保 Qt 模块已导入"""
        if cls._qt_widgets is None:
            try:
                from PySide6 import QtWidgets, QtCore
                cls._qt_widgets = QtWidgets
                cls._qt_core = QtCore
            except ImportError:
                from PyQt5 import QtWidgets, QtCore  # type: ignore
                cls._qt_widgets = QtWidgets
                cls._qt_core = QtCore
    
    @classmethod
    def get_message_box_icon(cls, icon_name: str) -> Any:
        """获取消息框图标枚举
        
        Args:
            icon_name: 'NoIcon', 'Information', 'Warning', 'Critical', 'Question'
        
        Returns:
            QMessageBox.Icon 枚举值
        """
        cls._ensure_qt_imported()
        return getattr(cls._qt_widgets.QMessageBox.Icon, icon_name, cls._qt_widgets.QMessageBox.Icon.Information)
    
    @classmethod
    def get_message_box_button(cls, button_name: str) -> Any:
        """获取消息框按钮枚举
        
        Args:
            button_name: 'Yes', 'No', 'Ok', 'Cancel', 等
        
        Returns:
            QMessageBox.StandardButton 枚举值
        """
        cls._ensure_qt_imported()
        return getattr(cls._qt_widgets.QMessageBox.StandardButton, button_name, cls._qt_widgets.QMessageBox.StandardButton.Ok)
    
    @classmethod
    def get_tray_icon_type(cls, icon_name: str) -> Any:
        """获取托盘图标类型枚举
        
        Args:
            icon_name: 'NoIcon', 'Information', 'Warning', 'Critical'
        
        Returns:
            QSystemTrayIcon.MessageIcon 枚举值
        """
        cls._ensure_qt_imported()
        return getattr(cls._qt_widgets.QSystemTrayIcon.MessageIcon, icon_name, cls._qt_widgets.QSystemTrayIcon.MessageIcon.Information)
    
    @classmethod
    def get_event_type(cls, type_name: str) -> Any:
        """获取事件类型枚举
        
        Args:
            type_name: 'WindowStateChange', 'Timer', 等
        
        Returns:
            QEvent.Type 枚举值
        """
        cls._ensure_qt_imported()
        return getattr(cls._qt_core.QEvent.Type, type_name, cls._qt_core.QEvent.Type.None_)


# ============================================
# 便捷访问变量（常用枚举值）
# ============================================

class MessageBoxIcons:
    """消息框图标常量"""
    @property
    def Information(self) -> Any:
        return QtEnumAccessor.get_message_box_icon('Information')
    
    @property
    def Warning(self) -> Any:
        return QtEnumAccessor.get_message_box_icon('Warning')
    
    @property
    def Critical(self) -> Any:
        return QtEnumAccessor.get_message_box_icon('Critical')
    
    @property
    def Question(self) -> Any:
        return QtEnumAccessor.get_message_box_icon('Question')


class MessageBoxButtons:
    """消息框按钮常量"""
    @property
    def Yes(self) -> Any:
        return QtEnumAccessor.get_message_box_button('Yes')
    
    @property
    def No(self) -> Any:
        return QtEnumAccessor.get_message_box_button('No')
    
    @property
    def Ok(self) -> Any:
        return QtEnumAccessor.get_message_box_button('Ok')
    
    @property
    def Cancel(self) -> Any:
        return QtEnumAccessor.get_message_box_button('Cancel')


class TrayIcons:
    """托盘图标常量"""
    @property
    def Information(self) -> Any:
        return QtEnumAccessor.get_tray_icon_type('Information')
    
    @property
    def Warning(self) -> Any:
        return QtEnumAccessor.get_tray_icon_type('Warning')
    
    @property
    def Critical(self) -> Any:
        return QtEnumAccessor.get_tray_icon_type('Critical')


class EventTypes:
    """事件类型常量"""
    @property
    def WindowStateChange(self) -> Any:
        return QtEnumAccessor.get_event_type('WindowStateChange')


# 创建单例实例供导入使用
MessageBoxIcon = MessageBoxIcons()
MessageBoxButton = MessageBoxButtons()
TrayIconType = TrayIcons()
EventType = EventTypes()

