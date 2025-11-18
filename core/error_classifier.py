# -*- coding: utf-8 -*-
"""
错误分类器 - 智能错误分类和用户建议系统

v2.2.0+ 增强版：
- 支持异常对象分类
- 细粒度错误严重程度
- 智能重试建议
- 更详细的用户指导
"""
import errno
import re
from typing import Tuple, Optional
from enum import Enum


class ErrorCategory(Enum):
    """错误类别枚举"""
    NETWORK = "network"  # 网络错误
    PERMISSION = "permission"  # 权限错误
    DISK = "disk"  # 磁盘错误
    FTP = "ftp"  # FTP特定错误
    FILE = "file"  # 文件错误
    CONFIGURATION = "configuration"  # 配置错误
    UNKNOWN = "unknown"  # 未知错误


class ErrorSeverity(Enum):
    """错误严重程度"""
    LOW = "low"  # 低：可忽略或自动恢复
    MEDIUM = "medium"  # 中：需要用户注意
    HIGH = "high"  # 高：阻止操作继续
    CRITICAL = "critical"  # 严重：需要立即处理


class ErrorInfo:
    """错误信息数据类"""
    
    def __init__(self,
                 category: ErrorCategory,
                 severity: ErrorSeverity,
                 message: str,
                 suggestion: str,
                 is_retryable: bool = False,
                 original_error: Optional[str] = None):
        self.category = category
        self.severity = severity
        self.message = message
        self.suggestion = suggestion
        self.is_retryable = is_retryable
        self.original_error = original_error
    
    def get_user_message(self) -> str:
        """获取用户友好的完整消息"""
        parts = [f"❌ {self.message}"]
        if self.suggestion:
            parts.append(f"\n💡 建议：{self.suggestion}")
        if self.is_retryable:
            parts.append("\n🔄 此错误可以重试")
        return "".join(parts)


class ErrorClassifier:
    """上传错误分类器"""
    
    @staticmethod
    def classify_error(error_message: str) -> Tuple[str, str, str]:
        """分类错误并返回分类、简短提示和详细建议
        
        Args:
            error_message: 错误消息
            
        Returns:
            (error_type, short_message, detailed_advice)
            
        错误类型:
            - ftp_auth: FTP认证失败
            - ftp_connection: FTP连接失败
            - network: 网络问题
            - permission: 权限不足
            - disk_full: 磁盘空间不足
            - file_not_found: 文件不存在
            - timeout: 超时
            - unknown: 未知错误
        """
        error_lower = error_message.lower()
        
        # FTP认证失败
        if any(keyword in error_lower for keyword in ['530', 'login incorrect', 'authentication failed', 'invalid credentials']):
            return (
                'ftp_auth',
                '❌ FTP认证失败',
                '建议: 1. 检查FTP用户名和密码是否正确 2. 确认FTP服务器是否已启动 3. 检查FTP服务器配置'
            )
        
        # FTP连接失败
        if any(keyword in error_lower for keyword in ['connection refused', '10061', 'no route to host', 'timed out', '425', '421']):
            return (
                'ftp_connection',
                '❌ 无法连接到FTP服务器',
                '建议: 1. 检查FTP服务器地址和端口是否正确 2. 确认网络连接是否正常 3. 检查防火墙设置 4. 确认FTP服务是否已启动'
            )
        
        # 网络问题
        if any(keyword in error_lower for keyword in ['network', '网络', 'unreachable', '连接中断', '连接超时', 'broken pipe', 'connection reset']):
            return (
                'network',
                '❌ 网络连接异常',
                '建议: 1. 检查网络连接是否稳定 2. 尝试重新连接网络 3. 检查网线或WiFi信号 4. 稍后重试'
            )
        
        # 权限不足
        if any(keyword in error_lower for keyword in ['permission denied', '拒绝访问', 'access denied', '权限不足', '550', '553']):
            return (
                'permission',
                '❌ 权限不足',
                '建议: 1. 确认对目标文件夹有写入权限 2. 检查文件夹共享设置 3. 以管理员身份运行程序 4. 联系系统管理员'
            )
        
        # 磁盘空间不足
        if any(keyword in error_lower for keyword in ['no space', '磁盘空间不足', 'disk full', '552', 'insufficient storage']):
            return (
                'disk_full',
                '❌ 磁盘空间不足',
                '建议: 1. 清理目标磁盘空间 2. 使用磁盘清理功能 3. 删除不需要的文件 4. 更换目标文件夹'
            )
        
        # 文件不存在
        if any(keyword in error_lower for keyword in ['file not found', '文件不存在', 'no such file', 'cannot find']):
            return (
                'file_not_found',
                '❌ 文件不存在',
                '建议: 1. 检查源文件是否已被删除 2. 确认文件路径是否正确 3. 检查文件是否被移动 4. 刷新源文件夹'
            )
        
        # 超时
        if any(keyword in error_lower for keyword in ['timeout', '超时', 'timed out']):
            return (
                'timeout',
                '❌ 操作超时',
                '建议: 1. 检查网络连接速度 2. 增加超时时间设置 3. 尝试上传较小的文件 4. 稍后重试'
            )
        
        # 未知错误
        return (
            'unknown',
            '❌ 上传失败',
            f'错误信息: {error_message[:100]} -- 建议: 1. 查看完整错误日志 2. 检查网络和权限 3. 尝试重新上传 4. 联系技术支持'
        )
    
    @staticmethod
    def get_user_friendly_message(error_message: str) -> str:
        """获取用户友好的错误提示
        
        Args:
            error_message: 原始错误消息
            
        Returns:
            用户友好的错误提示
        """
        error_type, short_msg, advice = ErrorClassifier.classify_error(error_message)
        
        # 根据错误类型返回简洁的提示
        type_messages = {
            'ftp_auth': '请检查FTP账号密码',
            'ftp_connection': '请检查FTP服务器地址和网络连接',
            'network': '请检查网络连接',
            'permission': '请检查文件夹权限',
            'disk_full': '请清理磁盘空间',
            'file_not_found': '源文件不存在',
            'timeout': '操作超时，请检查网络速度',
            'unknown': '上传失败，请查看日志'
        }
        
        return type_messages.get(error_type, '上传失败')
    
    @staticmethod
    def get_error_icon(error_message: str) -> str:
        """获取错误类型对应的图标
        
        Args:
            error_message: 错误消息
            
        Returns:
            对应的图标emoji
        """
        error_type, _, _ = ErrorClassifier.classify_error(error_message)
        
        icons = {
            'ftp_auth': '🔐',
            'ftp_connection': '🔌',
            'network': '📡',
            'permission': '🚫',
            'disk_full': '💾',
            'file_not_found': '📄',
            'timeout': '⏱️',
            'unknown': '❌'
        }
        
        return icons.get(error_type, '❌')
    
    # ========== 增强方法 (v2.2.0+) ==========
    
    @staticmethod
    def classify_exception(exception: Exception, context: str = "") -> ErrorInfo:
        """分类异常对象（增强版）
        
        Args:
            exception: 异常对象
            context: 上下文信息
        
        Returns:
            ErrorInfo 对象
        """
        error_str = str(exception)
        
        # 导入FTP异常（延迟导入避免循环依赖）
        try:
            from ftplib import error_perm, error_temp, error_proto
            
            if isinstance(exception, error_perm):
                if '530' in error_str:
                    return ErrorInfo(
                        category=ErrorCategory.FTP,
                        severity=ErrorSeverity.HIGH,
                        message="FTP登录失败：用户名或密码错误",
                        suggestion="请检查FTP用户名和密码是否正确",
                        is_retryable=False,
                        original_error=error_str
                    )
                if '550' in error_str:
                    return ErrorInfo(
                        category=ErrorCategory.PERMISSION,
                        severity=ErrorSeverity.HIGH,
                        message="FTP权限不足：无法访问目标路径",
                        suggestion="请确认FTP用户对目标目录有写入权限",
                        is_retryable=False,
                        original_error=error_str
                    )
            
            if isinstance(exception, error_temp):
                return ErrorInfo(
                    category=ErrorCategory.FTP,
                    severity=ErrorSeverity.MEDIUM,
                    message=f"FTP临时错误：{error_str}",
                    suggestion="这是临时性错误，稍后会自动重试",
                    is_retryable=True,
                    original_error=error_str
                )
        except ImportError:
            pass
        
        # 权限错误
        if isinstance(exception, PermissionError):
            return ErrorInfo(
                category=ErrorCategory.PERMISSION,
                severity=ErrorSeverity.HIGH,
                message="权限不足",
                suggestion="请检查文件/目录权限，或以管理员身份运行程序",
                is_retryable=False,
                original_error=error_str
            )
        
        # 磁盘错误
        if isinstance(exception, OSError):
            if hasattr(exception, 'errno'):
                if exception.errno == errno.ENOSPC or exception.errno == 28:
                    return ErrorInfo(
                        category=ErrorCategory.DISK,
                        severity=ErrorSeverity.CRITICAL,
                        message="目标磁盘空间不足",
                        suggestion="请清理磁盘空间或选择其他存储位置",
                        is_retryable=False,
                        original_error=error_str
                    )
        
        # 网络错误
        if isinstance(exception, (ConnectionError, TimeoutError)):
            return ErrorInfo(
                category=ErrorCategory.NETWORK,
                severity=ErrorSeverity.HIGH,
                message=f"网络错误：{error_str}",
                suggestion="请检查网络连接状态，确保目标服务器可访问",
                is_retryable=True,
                original_error=error_str
            )
        
        # 文件错误
        if isinstance(exception, FileNotFoundError):
            return ErrorInfo(
                category=ErrorCategory.FILE,
                severity=ErrorSeverity.MEDIUM,
                message=f"文件不存在：{error_str}",
                suggestion="请检查文件路径是否正确，或文件是否已被移动/删除",
                is_retryable=False,
                original_error=error_str
            )
        
        # 默认：使用字符串分类
        error_type, short_msg, advice = ErrorClassifier.classify_error(error_str)
        return ErrorInfo(
            category=ErrorCategory.UNKNOWN,
            severity=ErrorSeverity.MEDIUM,
            message=short_msg,
            suggestion=advice,
            is_retryable=error_type not in ['permission', 'disk_full', 'file_not_found'],
            original_error=error_str
        )
    
    @staticmethod
    def should_retry(error_info: ErrorInfo, retry_count: int, max_retries: int = 3) -> bool:
        """判断是否应该重试
        
        Args:
            error_info: 错误信息
            retry_count: 当前重试次数
            max_retries: 最大重试次数
        
        Returns:
            True: 应该重试, False: 不应该重试
        """
        if not error_info.is_retryable:
            return False
        
        if retry_count >= max_retries:
            return False
        
        if error_info.severity == ErrorSeverity.CRITICAL:
            return error_info.category == ErrorCategory.NETWORK
        
        return True
    
    @staticmethod
    def get_retry_suggestion(retry_count: int, max_retries: int = 3) -> str:
        """获取重试建议"""
        if retry_count >= max_retries:
            return f"已达到最大重试次数（{max_retries}次），建议检查错误原因后手动重试"
        
        remaining = max_retries - retry_count
        return f"将在稍后自动重试（剩余{remaining}次机会）"
