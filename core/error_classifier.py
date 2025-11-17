# -*- coding: utf-8 -*-
"""
v2.2.0 错误分类器
对上传错误进行分类，提供针对性的错误提示
"""
import re
from typing import Tuple


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
