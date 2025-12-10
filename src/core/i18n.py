# -*- coding: utf-8 -*-
"""
多语言国际化模块

v3.0.2 新增功能：
- 支持中英文切换
- 动态语言切换，无需重启
- 易于扩展更多语言
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List

logger = logging.getLogger(__name__)


# 语言代码
LANG_ZH_CN = 'zh_CN'
LANG_EN_US = 'en_US'

# 支持的语言列表
SUPPORTED_LANGUAGES = [
    (LANG_ZH_CN, '简体中文'),
    (LANG_EN_US, 'English'),
]


# 翻译字典
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    # ========== 窗口标题和菜单 ==========
    'app_title': {
        LANG_ZH_CN: '图片异步上传工具',
        LANG_EN_US: 'Image Upload Tool',
    },
    'menu_file': {
        LANG_ZH_CN: '文件',
        LANG_EN_US: 'File',
    },
    'menu_settings': {
        LANG_ZH_CN: '设置',
        LANG_EN_US: 'Settings',
    },
    'menu_help': {
        LANG_ZH_CN: '帮助',
        LANG_EN_US: 'Help',
    },
    'menu_language': {
        LANG_ZH_CN: '语言',
        LANG_EN_US: 'Language',
    },
    
    # ========== 文件夹设置卡片 ==========
    'card_folder_settings': {
        LANG_ZH_CN: '📁 文件夹设置',
        LANG_EN_US: '📁 Folder Settings',
    },
    'source_folder': {
        LANG_ZH_CN: '源文件夹',
        LANG_EN_US: 'Source Folder',
    },
    'target_folder': {
        LANG_ZH_CN: '目标文件夹',
        LANG_EN_US: 'Target Folder',
    },
    'backup_folder': {
        LANG_ZH_CN: '备份文件夹',
        LANG_EN_US: 'Backup Folder',
    },
    'browse': {
        LANG_ZH_CN: '浏览',
        LANG_EN_US: 'Browse',
    },
    'enable_backup': {
        LANG_ZH_CN: ' 启用备份功能',
        LANG_EN_US: ' Enable Backup',
    },
    'backup_hint': {
        LANG_ZH_CN: '💡 启用后，上传成功的文件会移动到备份文件夹保存；禁用后文件上传成功会直接删除',
        LANG_EN_US: '💡 When enabled, uploaded files are moved to backup folder; when disabled, files are deleted after upload',
    },
    
    # ========== 上传设置卡片 ==========
    'card_upload_settings': {
        LANG_ZH_CN: '⚙️ 上传设置',
        LANG_EN_US: '⚙️ Upload Settings',
    },
    'upload_protocol': {
        LANG_ZH_CN: '📡 上传协议 (v2.0)',
        LANG_EN_US: '📡 Upload Protocol (v2.0)',
    },
    'protocol_type': {
        LANG_ZH_CN: '协议类型:',
        LANG_EN_US: 'Protocol Type:',
    },
    'protocol_smb': {
        LANG_ZH_CN: 'SMB (网络共享)',
        LANG_EN_US: 'SMB (Network Share)',
    },
    'protocol_ftp_server': {
        LANG_ZH_CN: 'FTP 服务器模式',
        LANG_EN_US: 'FTP Server Mode',
    },
    'protocol_ftp_client': {
        LANG_ZH_CN: 'FTP 客户端模式',
        LANG_EN_US: 'FTP Client Mode',
    },
    'protocol_both': {
        LANG_ZH_CN: '混合模式 (Server + Client)',
        LANG_EN_US: 'Hybrid Mode (Server + Client)',
    },
    'protocol_desc_smb': {
        LANG_ZH_CN: '📁 SMB (网络共享)：通过 Windows 网络共享上传文件到共享文件夹',
        LANG_EN_US: '📁 SMB (Network Share): Upload files via Windows network share',
    },
    'protocol_desc_ftp_server': {
        LANG_ZH_CN: '🖥️ FTP 服务器模式：本机作为 FTP 服务器，其他设备可连接上传文件',
        LANG_EN_US: '🖥️ FTP Server Mode: This machine acts as FTP server, other devices can connect to upload',
    },
    'protocol_desc_ftp_client': {
        LANG_ZH_CN: '📤 FTP 客户端模式：本机作为 FTP 客户端，连接到远程 FTP 服务器上传文件',
        LANG_EN_US: '📤 FTP Client Mode: This machine acts as FTP client, connects to remote FTP server',
    },
    'protocol_desc_both': {
        LANG_ZH_CN: '🔄 混合模式：同时运行 FTP 服务器和客户端，灵活应对不同场景',
        LANG_EN_US: '🔄 Hybrid Mode: Run both FTP server and client for flexible scenarios',
    },
    'interval_seconds': {
        LANG_ZH_CN: '间隔时间(秒)',
        LANG_EN_US: 'Interval (sec)',
    },
    'disk_threshold': {
        LANG_ZH_CN: '磁盘阈值(%)',
        LANG_EN_US: 'Disk Threshold (%)',
    },
    'retry_count': {
        LANG_ZH_CN: '失败重试次数',
        LANG_EN_US: 'Retry Count',
    },
    'disk_check_interval': {
        LANG_ZH_CN: '磁盘检查间隔(秒)',
        LANG_EN_US: 'Disk Check Interval (sec)',
    },
    
    # ========== FTP 配置 ==========
    'ftp_server_config': {
        LANG_ZH_CN: '🖥️ FTP 服务器配置',
        LANG_EN_US: '🖥️ FTP Server Config',
    },
    'ftp_client_config': {
        LANG_ZH_CN: '💻 FTP 客户端配置',
        LANG_EN_US: '💻 FTP Client Config',
    },
    'host_address': {
        LANG_ZH_CN: '监听地址:',
        LANG_EN_US: 'Host Address:',
    },
    'port': {
        LANG_ZH_CN: '端口:',
        LANG_EN_US: 'Port:',
    },
    'username': {
        LANG_ZH_CN: '用户名:',
        LANG_EN_US: 'Username:',
    },
    'password': {
        LANG_ZH_CN: '密码:',
        LANG_EN_US: 'Password:',
    },
    'shared_folder': {
        LANG_ZH_CN: '共享目录:',
        LANG_EN_US: 'Shared Folder:',
    },
    'remote_path': {
        LANG_ZH_CN: '远程路径:',
        LANG_EN_US: 'Remote Path:',
    },
    'timeout': {
        LANG_ZH_CN: '超时时间:',
        LANG_EN_US: 'Timeout:',
    },
    'enable_passive': {
        LANG_ZH_CN: '启用被动模式',
        LANG_EN_US: 'Enable Passive Mode',
    },
    'enable_tls': {
        LANG_ZH_CN: '启用 TLS/SSL (FTPS)',
        LANG_EN_US: 'Enable TLS/SSL (FTPS)',
    },
    'test_config': {
        LANG_ZH_CN: '🧪 测试配置',
        LANG_EN_US: '🧪 Test Config',
    },
    'test_connection': {
        LANG_ZH_CN: '🔌 测试连接',
        LANG_EN_US: '🔌 Test Connection',
    },
    
    # ========== 文件类型限制 ==========
    'file_type_filter': {
        LANG_ZH_CN: '📋 文件类型限制',
        LANG_EN_US: '📋 File Type Filter',
    },
    
    # ========== 高级选项 ==========
    'advanced_options': {
        LANG_ZH_CN: '⚡ 高级选项',
        LANG_EN_US: '⚡ Advanced Options',
    },
    'auto_start_windows': {
        LANG_ZH_CN: '🚀 开机自启动',
        LANG_EN_US: '🚀 Start with Windows',
    },
    'auto_run_on_startup': {
        LANG_ZH_CN: '▶ 启动时自动运行',
        LANG_EN_US: '▶ Auto Run on Startup',
    },
    'show_notifications': {
        LANG_ZH_CN: '🔔 显示托盘通知',
        LANG_EN_US: '🔔 Show Tray Notifications',
    },
    'limit_upload_rate': {
        LANG_ZH_CN: '⚡ 限制上传速率',
        LANG_EN_US: '⚡ Limit Upload Rate',
    },
    'enable_dedup': {
        LANG_ZH_CN: '🔍 启用文件去重 (v1.8)',
        LANG_EN_US: '🔍 Enable Deduplication (v1.8)',
    },
    'hash_algorithm': {
        LANG_ZH_CN: '哈希算法:',
        LANG_EN_US: 'Hash Algorithm:',
    },
    'duplicate_strategy': {
        LANG_ZH_CN: '重复策略:',
        LANG_EN_US: 'Duplicate Strategy:',
    },
    'strategy_skip': {
        LANG_ZH_CN: '跳过',
        LANG_EN_US: 'Skip',
    },
    'strategy_rename': {
        LANG_ZH_CN: '重命名',
        LANG_EN_US: 'Rename',
    },
    'strategy_overwrite': {
        LANG_ZH_CN: '覆盖',
        LANG_EN_US: 'Overwrite',
    },
    'strategy_ask': {
        LANG_ZH_CN: '询问',
        LANG_EN_US: 'Ask',
    },
    'dedup_hint': {
        LANG_ZH_CN: '💡 通过文件哈希检测重复，避免上传相同内容的文件',
        LANG_EN_US: '💡 Detect duplicates by file hash, avoid uploading identical files',
    },
    'network_monitor': {
        LANG_ZH_CN: '🌐 网络监控',
        LANG_EN_US: '🌐 Network Monitor',
    },
    'check_interval': {
        LANG_ZH_CN: '检测间隔:',
        LANG_EN_US: 'Check Interval:',
    },
    'auto_pause_on_disconnect': {
        LANG_ZH_CN: '⏸️ 断网时自动暂停',
        LANG_EN_US: '⏸️ Auto Pause on Disconnect',
    },
    'auto_resume_on_reconnect': {
        LANG_ZH_CN: '▶️ 恢复时自动继续',
        LANG_EN_US: '▶️ Auto Resume on Reconnect',
    },
    'network_hint': {
        LANG_ZH_CN: '💡 实时监控网络状态，断网时自动暂停，恢复后自动继续',
        LANG_EN_US: '💡 Monitor network status, auto pause on disconnect, resume on reconnect',
    },
    
    # ========== 操作控制卡片 ==========
    'card_control': {
        LANG_ZH_CN: '🎮 操作控制',
        LANG_EN_US: '🎮 Control Panel',
    },
    'start_upload': {
        LANG_ZH_CN: '▶ 开始上传',
        LANG_EN_US: '▶ Start Upload',
    },
    'pause_upload': {
        LANG_ZH_CN: '⏸ 暂停上传',
        LANG_EN_US: '⏸ Pause Upload',
    },
    'resume_upload': {
        LANG_ZH_CN: '▶ 继续上传',
        LANG_EN_US: '▶ Resume Upload',
    },
    'stop_upload': {
        LANG_ZH_CN: '⏹ 停止上传',
        LANG_EN_US: '⏹ Stop Upload',
    },
    'save_config': {
        LANG_ZH_CN: '💾 保存配置',
        LANG_EN_US: '💾 Save Config',
    },
    'more': {
        LANG_ZH_CN: '更多 ▾',
        LANG_EN_US: 'More ▾',
    },
    'clear_logs': {
        LANG_ZH_CN: '🗑️ 清空日志',
        LANG_EN_US: '🗑️ Clear Logs',
    },
    'disk_cleanup': {
        LANG_ZH_CN: '💿 磁盘清理',
        LANG_EN_US: '💿 Disk Cleanup',
    },
    'login': {
        LANG_ZH_CN: '🔐 权限登录',
        LANG_EN_US: '🔐 Login',
    },
    'change_password': {
        LANG_ZH_CN: '🔑 修改密码',
        LANG_EN_US: '🔑 Change Password',
    },
    'logout': {
        LANG_ZH_CN: '🚪 退出登录',
        LANG_EN_US: '🚪 Logout',
    },
    
    # ========== 运行状态卡片 ==========
    'card_status': {
        LANG_ZH_CN: '📊 运行状态',
        LANG_EN_US: '📊 Status',
    },
    'status_stopped': {
        LANG_ZH_CN: '🔴 已停止',
        LANG_EN_US: '🔴 Stopped',
    },
    'status_running': {
        LANG_ZH_CN: '🟢 运行中',
        LANG_EN_US: '🟢 Running',
    },
    'status_paused': {
        LANG_ZH_CN: '🟡 已暂停',
        LANG_EN_US: '🟡 Paused',
    },
    'uploaded': {
        LANG_ZH_CN: '已上传',
        LANG_EN_US: 'Uploaded',
    },
    'failed': {
        LANG_ZH_CN: '失败',
        LANG_EN_US: 'Failed',
    },
    'skipped': {
        LANG_ZH_CN: '跳过',
        LANG_EN_US: 'Skipped',
    },
    'rate': {
        LANG_ZH_CN: '速率',
        LANG_EN_US: 'Rate',
    },
    'archive_queue': {
        LANG_ZH_CN: '归档队列',
        LANG_EN_US: 'Archive Queue',
    },
    'runtime': {
        LANG_ZH_CN: '运行时间',
        LANG_EN_US: 'Runtime',
    },
    'target_disk': {
        LANG_ZH_CN: '目标磁盘',
        LANG_EN_US: 'Target Disk',
    },
    'backup_disk': {
        LANG_ZH_CN: '归档磁盘',
        LANG_EN_US: 'Backup Disk',
    },
    'network_status': {
        LANG_ZH_CN: '网络状态',
        LANG_EN_US: 'Network',
    },
    'network_good': {
        LANG_ZH_CN: '正常',
        LANG_EN_US: 'Good',
    },
    'network_unstable': {
        LANG_ZH_CN: '不稳定',
        LANG_EN_US: 'Unstable',
    },
    'network_disconnected': {
        LANG_ZH_CN: '已断开',
        LANG_EN_US: 'Disconnected',
    },
    'network_unknown': {
        LANG_ZH_CN: '未知',
        LANG_EN_US: 'Unknown',
    },
    'current_file': {
        LANG_ZH_CN: '📄 当前文件',
        LANG_EN_US: '📄 Current File',
    },
    'waiting': {
        LANG_ZH_CN: '等待开始...',
        LANG_EN_US: 'Waiting...',
    },
    'progress': {
        LANG_ZH_CN: '总体进度',
        LANG_EN_US: 'Overall Progress',
    },
    
    # ========== 日志卡片 ==========
    'card_log': {
        LANG_ZH_CN: '📜 运行日志',
        LANG_EN_US: '📜 Log',
    },
    'autoscroll': {
        LANG_ZH_CN: ' 自动滚动',
        LANG_EN_US: ' Auto Scroll',
    },
    
    # ========== 角色和权限 ==========
    'role_guest': {
        LANG_ZH_CN: '🔒 未登录',
        LANG_EN_US: '🔒 Guest',
    },
    'role_user': {
        LANG_ZH_CN: '👤 用户',
        LANG_EN_US: '👤 User',
    },
    'role_admin': {
        LANG_ZH_CN: '👑 管理员',
        LANG_EN_US: '👑 Admin',
    },
    
    # ========== 对话框 ==========
    'dialog_login': {
        LANG_ZH_CN: '🔐 权限登录',
        LANG_EN_US: '🔐 Login',
    },
    'dialog_change_password': {
        LANG_ZH_CN: '🔑 修改密码',
        LANG_EN_US: '🔑 Change Password',
    },
    'dialog_disk_cleanup': {
        LANG_ZH_CN: '💿 磁盘清理工具',
        LANG_EN_US: '💿 Disk Cleanup Tool',
    },
    'login_role': {
        LANG_ZH_CN: '登录角色:',
        LANG_EN_US: 'Role:',
    },
    'enter_password': {
        LANG_ZH_CN: '请输入密码',
        LANG_EN_US: 'Enter password',
    },
    'cancel': {
        LANG_ZH_CN: '取消',
        LANG_EN_US: 'Cancel',
    },
    'confirm': {
        LANG_ZH_CN: '确认',
        LANG_EN_US: 'Confirm',
    },
    'ok': {
        LANG_ZH_CN: '确定',
        LANG_EN_US: 'OK',
    },
    
    # ========== 提示消息 ==========
    'msg_login_success': {
        LANG_ZH_CN: '登录成功！',
        LANG_EN_US: 'Login successful!',
    },
    'msg_login_failed': {
        LANG_ZH_CN: '密码错误',
        LANG_EN_US: 'Wrong password',
    },
    'msg_logout': {
        LANG_ZH_CN: '已退出登录',
        LANG_EN_US: 'Logged out',
    },
    'msg_config_saved': {
        LANG_ZH_CN: '配置已保存',
        LANG_EN_US: 'Config saved',
    },
    'msg_logs_cleared': {
        LANG_ZH_CN: '已清空日志',
        LANG_EN_US: 'Logs cleared',
    },
    'msg_upload_started': {
        LANG_ZH_CN: '上传已开始',
        LANG_EN_US: 'Upload started',
    },
    'msg_upload_stopped': {
        LANG_ZH_CN: '上传已停止',
        LANG_EN_US: 'Upload stopped',
    },
    'msg_upload_paused': {
        LANG_ZH_CN: '上传已暂停',
        LANG_EN_US: 'Upload paused',
    },
    'msg_upload_resumed': {
        LANG_ZH_CN: '上传已恢复',
        LANG_EN_US: 'Upload resumed',
    },
    'msg_language_changed': {
        LANG_ZH_CN: '语言已切换',
        LANG_EN_US: 'Language changed',
    },
    'msg_need_login': {
        LANG_ZH_CN: '请先登录',
        LANG_EN_US: 'Please login first',
    },
    'msg_no_permission': {
        LANG_ZH_CN: '权限不足',
        LANG_EN_US: 'Permission denied',
    },
    
    # ========== 断点续传相关 ==========
    'resume_upload': {
        LANG_ZH_CN: '断点续传',
        LANG_EN_US: 'Resume Upload',
    },
    'resume_found': {
        LANG_ZH_CN: '发现未完成的上传任务',
        LANG_EN_US: 'Found incomplete upload',
    },
    'resume_continue': {
        LANG_ZH_CN: '继续上传',
        LANG_EN_US: 'Continue',
    },
    'resume_restart': {
        LANG_ZH_CN: '重新开始',
        LANG_EN_US: 'Restart',
    },
    'resume_progress': {
        LANG_ZH_CN: '续传进度',
        LANG_EN_US: 'Resume Progress',
    },
    
    # ========== 系统托盘 ==========
    'tray_show': {
        LANG_ZH_CN: '显示主窗口',
        LANG_EN_US: 'Show Window',
    },
    'tray_hide': {
        LANG_ZH_CN: '隐藏到托盘',
        LANG_EN_US: 'Hide to Tray',
    },
    'tray_exit': {
        LANG_ZH_CN: '退出程序',
        LANG_EN_US: 'Exit',
    },
    
    # ========== 秒/分钟/小时 ==========
    'seconds': {
        LANG_ZH_CN: '秒',
        LANG_EN_US: 'sec',
    },
    'minutes': {
        LANG_ZH_CN: '分钟',
        LANG_EN_US: 'min',
    },
    'hours': {
        LANG_ZH_CN: '小时',
        LANG_EN_US: 'hr',
    },
    'days': {
        LANG_ZH_CN: '天',
        LANG_EN_US: 'days',
    },
    
    # ========== 路径行标签 ==========
    'source_folder_label': {
        LANG_ZH_CN: '源文件夹',
        LANG_EN_US: 'Source Folder',
    },
    'target_folder_label': {
        LANG_ZH_CN: '目标文件夹',
        LANG_EN_US: 'Target Folder',
    },
    'backup_folder_label': {
        LANG_ZH_CN: '备份文件夹',
        LANG_EN_US: 'Backup Folder',
    },
    
    # ========== 协议相关 ==========
    'upload_protocol_title': {
        LANG_ZH_CN: '📡 上传协议 (v2.0)',
        LANG_EN_US: '📡 Upload Protocol (v2.0)',
    },
    'protocol_type_label': {
        LANG_ZH_CN: '协议类型:',
        LANG_EN_US: 'Protocol:',
    },
    'max_connections': {
        LANG_ZH_CN: '最大连接:',
        LANG_EN_US: 'Max Conn:',
    },
    'ip_limit': {
        LANG_ZH_CN: '  单IP限制:',
        LANG_EN_US: '  IP Limit:',
    },
    'retry_count_label': {
        LANG_ZH_CN: '重试次数:',
        LANG_EN_US: 'Retries:',
    },
    
    # ========== 设置行 ==========
    'interval_label': {
        LANG_ZH_CN: '间隔时间(秒)',
        LANG_EN_US: 'Interval (sec)',
    },
    'disk_threshold_label': {
        LANG_ZH_CN: '磁盘阈值(%)',
        LANG_EN_US: 'Disk Threshold (%)',
    },
    'retry_label': {
        LANG_ZH_CN: '失败重试次数',
        LANG_EN_US: 'Retry Count',
    },
    'disk_check_label': {
        LANG_ZH_CN: '磁盘检查间隔(秒)',
        LANG_EN_US: 'Disk Check (sec)',
    },
    'disk_check_interval_label': {
        LANG_ZH_CN: '磁盘检查间隔(秒)',
        LANG_EN_US: 'Disk Check (sec)',
    },
    'check_interval_label': {
        LANG_ZH_CN: '检测间隔:',
        LANG_EN_US: 'Check Interval:',
    },
    
    # ========== 标题栏 ==========
    'header_title': {
        LANG_ZH_CN: '图片异步上传工具',
        LANG_EN_US: 'Image Upload Tool',
    },
    
    # ========== 当前文件 ==========
    'current_file_label': {
        LANG_ZH_CN: '📄 当前文件',
        LANG_EN_US: '📄 Current File',
    },
    
    # ========== 上传协议选项 ==========
    'protocol_option_smb': {
        LANG_ZH_CN: 'SMB (网络共享)',
        LANG_EN_US: 'SMB (Network Share)',
    },
    'protocol_option_ftp_client': {
        LANG_ZH_CN: 'FTP 客户端模式',
        LANG_EN_US: 'FTP Client Mode',
    },
    'protocol_option_both': {
        LANG_ZH_CN: 'SMB + FTP客户端 (双写)',
        LANG_EN_US: 'SMB + FTP Client (Dual)',
    },
    'enable_ftp_server': {
        LANG_ZH_CN: '🖥️ 启用内置FTP服务器',
        LANG_EN_US: '🖥️ Enable Built-in FTP Server',
    },
    'ftp_server_hint': {
        LANG_ZH_CN: '启用后，本机将作为FTP服务器，其他设备可连接上传文件到指定文件夹',
        LANG_EN_US: 'When enabled, this machine acts as FTP server, others can connect to upload files',
    },
    
    # ========== FTP 表单标签 ==========
    'listen_address': {
        LANG_ZH_CN: '监听地址:',
        LANG_EN_US: 'Listen Address:',
    },
    'port_label': {
        LANG_ZH_CN: '端口:',
        LANG_EN_US: 'Port:',
    },
    'username_label': {
        LANG_ZH_CN: '用户名:',
        LANG_EN_US: 'Username:',
    },
    'password_label': {
        LANG_ZH_CN: '密码:',
        LANG_EN_US: 'Password:',
    },
    'shared_dir': {
        LANG_ZH_CN: '共享目录:',
        LANG_EN_US: 'Shared Dir:',
    },
    'server_address': {
        LANG_ZH_CN: '服务器地址:',
        LANG_EN_US: 'Server Address:',
    },
    'remote_path_label': {
        LANG_ZH_CN: '远程路径:',
        LANG_EN_US: 'Remote Path:',
    },
    'timeout_label': {
        LANG_ZH_CN: '超时(秒):',
        LANG_EN_US: 'Timeout(s):',
    },
    
    # ========== 登录对话框 ==========
    'login_role_label': {
        LANG_ZH_CN: '登录角色:',
        LANG_EN_US: 'Role:',
    },
    'role_user_option': {
        LANG_ZH_CN: '👤 用户',
        LANG_EN_US: '👤 User',
    },
    'role_admin_option': {
        LANG_ZH_CN: '👑 管理员',
        LANG_EN_US: '👑 Admin',
    },
    'enter_password': {
        LANG_ZH_CN: '请输入密码',
        LANG_EN_US: 'Enter password',
    },
    'cancel': {
        LANG_ZH_CN: '取消',
        LANG_EN_US: 'Cancel',
    },
    'ok': {
        LANG_ZH_CN: '确定',
        LANG_EN_US: 'OK',
    },
    'please_enter_password': {
        LANG_ZH_CN: '请输入密码',
        LANG_EN_US: 'Please enter password',
    },
    'wrong_password': {
        LANG_ZH_CN: '密码错误',
        LANG_EN_US: 'Wrong password',
    },
    'user_login_success': {
        LANG_ZH_CN: '👤 用户登录成功！',
        LANG_EN_US: '👤 User logged in!',
    },
    'admin_login_success': {
        LANG_ZH_CN: '👑 管理员登录成功！',
        LANG_EN_US: '👑 Admin logged in!',
    },
    'logged_out': {
        LANG_ZH_CN: '已退出登录',
        LANG_EN_US: 'Logged out',
    },
    
    # ========== 修改密码对话框 ==========
    'change_target': {
        LANG_ZH_CN: '修改对象:',
        LANG_EN_US: 'Target:',
    },
    'old_password': {
        LANG_ZH_CN: '原密码:',
        LANG_EN_US: 'Old Password:',
    },
    'new_password': {
        LANG_ZH_CN: '新密码:',
        LANG_EN_US: 'New Password:',
    },
    'confirm_password': {
        LANG_ZH_CN: '确认密码:',
        LANG_EN_US: 'Confirm:',
    },
    
    # ========== 芯片额外标签 ==========
    'protocol_chip': {
        LANG_ZH_CN: '上传协议',
        LANG_EN_US: 'Protocol',
    },
    'ftp_server_chip': {
        LANG_ZH_CN: 'FTP服务器',
        LANG_EN_US: 'FTP Server',
    },
    'ftp_client_chip': {
        LANG_ZH_CN: 'FTP客户端',
        LANG_EN_US: 'FTP Client',
    },
    'not_started': {
        LANG_ZH_CN: '未启动',
        LANG_EN_US: 'Not Started',
    },
    'not_connected': {
        LANG_ZH_CN: '未连接',
        LANG_EN_US: 'Not Connected',
    },
    
    # ========== 可折叠区块标题 ==========
    'file_filter_title': {
        LANG_ZH_CN: '📋 文件类型限制',
        LANG_EN_US: '📋 File Type Filter',
    },
    'advanced_options_title': {
        LANG_ZH_CN: '⚡ 高级选项',
        LANG_EN_US: '⚡ Advanced Options',
    },
    
    # ========== 工具提示 ==========
    'limit_rate_tooltip': {
        LANG_ZH_CN: '启用后将限制最大上传速度，避免占用过多带宽',
        LANG_EN_US: 'Limit max upload speed to avoid bandwidth hogging',
    },
    'max_rate_tooltip': {
        LANG_ZH_CN: '设置最大上传速率（单位：MB/秒）',
        LANG_EN_US: 'Set max upload rate (MB/s)',
    },
    
    # ========== 去重相关 ==========
    'hash_algorithm': {
        LANG_ZH_CN: '哈希算法',
        LANG_EN_US: 'Hash Algorithm',
    },
    'duplicate_strategy': {
        LANG_ZH_CN: '重复策略',
        LANG_EN_US: 'Duplicate Strategy',
    },
    'strategy_skip': {
        LANG_ZH_CN: '跳过',
        LANG_EN_US: 'Skip',
    },
    'strategy_rename': {
        LANG_ZH_CN: '重命名',
        LANG_EN_US: 'Rename',
    },
    'strategy_overwrite': {
        LANG_ZH_CN: '覆盖',
        LANG_EN_US: 'Overwrite',
    },
    'strategy_ask': {
        LANG_ZH_CN: '询问',
        LANG_EN_US: 'Ask',
    },
    'dedup_hint': {
        LANG_ZH_CN: '💡 通过文件哈希检测重复，避免上传相同内容的文件',
        LANG_EN_US: '💡 Detect duplicates via file hash to avoid uploading identical files',
    },
    
    # ========== 网络监控 ==========
    'network_monitor': {
        LANG_ZH_CN: '🌐 网络监控',
        LANG_EN_US: '🌐 Network Monitor',
    },
    'seconds': {
        LANG_ZH_CN: '秒',
        LANG_EN_US: 's',
    },
    'network_hint': {
        LANG_ZH_CN: '💡 实时监控网络状态，断网时自动暂停，恢复后自动继续',
        LANG_EN_US: '💡 Monitor network status and auto-pause/resume on disconnect/reconnect',
    },
    
    # ========== FTP 配置标签 ==========
    'listen_address': {
        LANG_ZH_CN: '监听地址:',
        LANG_EN_US: 'Listen Addr:',
    },
    'listen_address_tooltip': {
        LANG_ZH_CN: '0.0.0.0 表示监听所有网卡，127.0.0.1 仅本机可访问',
        LANG_EN_US: '0.0.0.0 listens on all interfaces, 127.0.0.1 for localhost only',
    },
    'port_label': {
        LANG_ZH_CN: '端口:',
        LANG_EN_US: 'Port:',
    },
    'port_tooltip': {
        LANG_ZH_CN: '默认FTP端口为21，建议使用2121避免权限问题',
        LANG_EN_US: 'Default FTP port is 21, use 2121 to avoid permission issues',
    },
    'username_label': {
        LANG_ZH_CN: '用户名:',
        LANG_EN_US: 'Username:',
    },
    'username_tooltip': {
        LANG_ZH_CN: 'FTP登录用户名',
        LANG_EN_US: 'FTP login username',
    },
    'password_label': {
        LANG_ZH_CN: '密码:',
        LANG_EN_US: 'Password:',
    },
    'password_tooltip': {
        LANG_ZH_CN: 'FTP登录密码，建议使用强密码',
        LANG_EN_US: 'FTP login password, use a strong password',
    },
    'share_directory': {
        LANG_ZH_CN: '共享目录:',
        LANG_EN_US: 'Share Dir:',
    },
    'select_ftp_share': {
        LANG_ZH_CN: '选择FTP共享目录',
        LANG_EN_US: 'Select FTP share directory',
    },
    'share_dir_tooltip': {
        LANG_ZH_CN: 'FTP服务器的根目录，客户端连接后可访问此目录',
        LANG_EN_US: 'FTP server root directory accessible by clients',
    },
    'passive_mode_tooltip': {
        LANG_ZH_CN: '被动模式适用于NAT/防火墙环境，建议启用',
        LANG_EN_US: 'Passive mode works better with NAT/firewalls, recommended',
    },
    'port_start': {
        LANG_ZH_CN: '起始:',
        LANG_EN_US: 'Start:',
    },
    'port_end': {
        LANG_ZH_CN: '结束:',
        LANG_EN_US: 'End:',
    },
    'port_range': {
        LANG_ZH_CN: '端口范围:',
        LANG_EN_US: 'Port Range:',
    },
    'enable_tls_tooltip': {
        LANG_ZH_CN: '启用加密连接，需要证书文件',
        LANG_EN_US: 'Enable encrypted connection, requires certificate files',
    },
    'max_connections': {
        LANG_ZH_CN: '最大连接:',
        LANG_EN_US: 'Max Conn:',
    },
    'per_ip_limit': {
        LANG_ZH_CN: '单IP限制:',
        LANG_EN_US: 'Per IP Limit:',
    },
    'unit_connections': {
        LANG_ZH_CN: '个',
        LANG_EN_US: '',
    },
    'connection_limit': {
        LANG_ZH_CN: '连接限制:',
        LANG_EN_US: 'Conn Limit:',
    },
    
    # ========== FTP 客户端标签 ==========
    'remote_path': {
        LANG_ZH_CN: '远程路径:',
        LANG_EN_US: 'Remote Path:',
    },
    'remote_path_tooltip': {
        LANG_ZH_CN: '上传到远程FTP服务器的目标路径',
        LANG_EN_US: 'Target path on remote FTP server',
    },
    'server_label': {
        LANG_ZH_CN: '服务器:',
        LANG_EN_US: 'Server:',
    },
    'server_address_tooltip': {
        LANG_ZH_CN: 'FTP服务器地址，可以是域名或IP地址',
        LANG_EN_US: 'FTP server address, can be domain or IP',
    },
    'client_port_tooltip': {
        LANG_ZH_CN: 'FTP服务器端口，标准端口为21',
        LANG_EN_US: 'FTP server port, standard is 21',
    },
    'username_placeholder': {
        LANG_ZH_CN: '用户名',
        LANG_EN_US: 'Username',
    },
    'password_placeholder': {
        LANG_ZH_CN: '密码',
        LANG_EN_US: 'Password',
    },
    'client_username_tooltip': {
        LANG_ZH_CN: 'FTP服务器登录用户名',
        LANG_EN_US: 'FTP server login username',
    },
    'client_password_tooltip': {
        LANG_ZH_CN: 'FTP服务器登录密码',
        LANG_EN_US: 'FTP server login password',
    },
    'timeout_label': {
        LANG_ZH_CN: '超时时间:',
        LANG_EN_US: 'Timeout:',
    },
    'timeout_tooltip': {
        LANG_ZH_CN: '连接和传输超时时间，网络慢时可适当增加',
        LANG_EN_US: 'Connection and transfer timeout, increase for slow networks',
    },
    'ftp_retry_label': {
        LANG_ZH_CN: '重试次数:',
        LANG_EN_US: 'Retry Count:',
    },
    'unit_times': {
        LANG_ZH_CN: '次',
        LANG_EN_US: '',
    },
    'retry_tooltip': {
        LANG_ZH_CN: '连接失败时的重试次数，0表示不重试',
        LANG_EN_US: 'Retry count on failure, 0 for no retry',
    },
    'use_passive_mode': {
        LANG_ZH_CN: '使用被动模式',
        LANG_EN_US: 'Use Passive Mode',
    },
    'client_tls_tooltip': {
        LANG_ZH_CN: '连接到FTPS服务器时启用',
        LANG_EN_US: 'Enable when connecting to FTPS server',
    },
    
    # ========== v3.1.0 新增：密码可见性切换 ==========
    'show_password': {
        LANG_ZH_CN: '显示密码',
        LANG_EN_US: 'Show Password',
    },
    'hide_password': {
        LANG_ZH_CN: '隐藏密码',
        LANG_EN_US: 'Hide Password',
    },
    
    # ========== v3.1.0 新增：协议模式增强 ==========
    'current_mode': {
        LANG_ZH_CN: '当前模式',
        LANG_EN_US: 'Current Mode',
    },
    'mode_smb': {
        LANG_ZH_CN: 'SMB',
        LANG_EN_US: 'SMB',
    },
    'mode_ftp_client': {
        LANG_ZH_CN: 'FTP客户端',
        LANG_EN_US: 'FTP Client',
    },
    'mode_both': {
        LANG_ZH_CN: 'SMB+FTP',
        LANG_EN_US: 'SMB+FTP',
    },
    'protocol_desc_smb_short': {
        LANG_ZH_CN: '通过网络共享上传文件',
        LANG_EN_US: 'Upload via network share',
    },
    'protocol_desc_ftp_client_short': {
        LANG_ZH_CN: '连接到远程FTP服务器上传',
        LANG_EN_US: 'Upload to remote FTP server',
    },
    'protocol_desc_both_short': {
        LANG_ZH_CN: 'SMB和FTP双写冗余',
        LANG_EN_US: 'Dual write to SMB and FTP',
    },
    'toast_protocol_smb': {
        LANG_ZH_CN: '已切换到 SMB 模式',
        LANG_EN_US: 'Switched to SMB mode',
    },
    'toast_protocol_ftp_client': {
        LANG_ZH_CN: '已切换到 FTP客户端 模式',
        LANG_EN_US: 'Switched to FTP Client mode',
    },
    'toast_protocol_both': {
        LANG_ZH_CN: '已切换到 双写 模式',
        LANG_EN_US: 'Switched to Dual Write mode',
    },
    'ftp_server_unavailable_smb': {
        LANG_ZH_CN: 'FTP服务器仅在 FTP客户端 或 双写 模式下可用',
        LANG_EN_US: 'FTP Server only available in FTP Client or Dual mode',
    },
}


class I18n:
    """国际化管理器
    
    支持中英文切换，动态更新 UI 文本
    """
    
    _instance: Optional['I18n'] = None
    _current_lang: str = LANG_ZH_CN
    _listeners: List[Callable[[], None]] = []
    
    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        pass
    
    @classmethod
    def get_instance(cls) -> 'I18n':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def get_current_language(cls) -> str:
        """获取当前语言"""
        return cls._current_lang
    
    @classmethod
    def set_language(cls, lang: str) -> bool:
        """设置当前语言
        
        Args:
            lang: 语言代码 (zh_CN 或 en_US)
            
        Returns:
            是否设置成功
        """
        if lang not in [LANG_ZH_CN, LANG_EN_US]:
            logger.warning(f"不支持的语言: {lang}")
            return False
        
        if lang == cls._current_lang:
            return True
        
        cls._current_lang = lang
        logger.info(f"语言已切换: {lang}")
        
        # 通知所有监听器
        for listener in cls._listeners:
            try:
                listener()
            except Exception as e:
                logger.warning(f"语言切换监听器执行失败: {e}")
        
        return True
    
    @classmethod
    def add_listener(cls, callback: Callable[[], None]):
        """添加语言切换监听器
        
        Args:
            callback: 语言切换时的回调函数
        """
        if callback not in cls._listeners:
            cls._listeners.append(callback)
    
    @classmethod
    def remove_listener(cls, callback: Callable[[], None]):
        """移除语言切换监听器"""
        if callback in cls._listeners:
            cls._listeners.remove(callback)
    
    @classmethod
    def t(cls, key: str, default: str = '') -> str:
        """翻译文本
        
        Args:
            key: 翻译键
            default: 默认值（如果找不到翻译）
            
        Returns:
            翻译后的文本
        """
        translation = TRANSLATIONS.get(key, {})
        if not translation:
            logger.debug(f"未找到翻译: {key}")
            return default or key
        
        return translation.get(cls._current_lang, translation.get(LANG_ZH_CN, default or key))
    
    @classmethod
    def get_language_name(cls, lang: str) -> str:
        """获取语言显示名称"""
        for code, name in SUPPORTED_LANGUAGES:
            if code == lang:
                return name
        return lang
    
    @classmethod
    def get_supported_languages(cls) -> List[tuple]:
        """获取支持的语言列表"""
        return SUPPORTED_LANGUAGES.copy()


# 便捷函数
def t(key: str, default: str = '') -> str:
    """翻译快捷函数
    
    使用方法:
        from src.core.i18n import t
        label = t('start_upload')  # 返回 "▶ 开始上传" 或 "▶ Start Upload"
    """
    return I18n.t(key, default)


def set_language(lang: str) -> bool:
    """设置语言快捷函数"""
    return I18n.set_language(lang)


def get_language() -> str:
    """获取当前语言快捷函数"""
    return I18n.get_current_language()


def add_language_listener(callback: Callable[[], None]):
    """添加语言切换监听器快捷函数"""
    I18n.add_listener(callback)
