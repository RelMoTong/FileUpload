# -*- coding: utf-8 -*-
"""Standalone upload settings, status, and log view panels."""

from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtWidgets

from src.core.i18n import t
from src.ui.widgets import ChipWidget, CollapsibleBox


class _PanelShell(QtWidgets.QWidget):
    def __init__(self, host: Any, content: QtWidgets.QWidget) -> None:
        super().__init__(host)
        self.host = host
        self.content = content
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(content)


class UploadFoldersPanel(_PanelShell):
    def __init__(self, host: Any) -> None:
        super().__init__(host, _build_upload_folders(host))


class UploadSettingsPanel(_PanelShell):
    def __init__(self, host: Any) -> None:
        super().__init__(host, _build_upload_settings(host))


class UploadStatusPanel(_PanelShell):
    def __init__(self, host: Any) -> None:
        super().__init__(host, _build_status_panel(host))


class UploadLogPanel(_PanelShell):
    def __init__(self, host: Any) -> None:
        super().__init__(host, _build_log_panel(host))


def _build_upload_folders(host: Any) -> QtWidgets.QFrame:
    card, v, host.title_folder = host._card("📁 文件夹设置", "card_folder_settings")
    
    # source
    host.src_edit, host.btn_choose_src, host.lbl_src = _add_path_row(host, v, "源文件夹", host._choose_source)
    # target
    host.tgt_edit, host.btn_choose_tgt, host.lbl_tgt = _add_path_row(host, v, "目标文件夹", host._choose_target)
    # backup
    host.bak_edit, host.btn_choose_bak, host.lbl_bak = _add_path_row(host, v, "备份文件夹", host._choose_backup)
    
    # v2.1.1 新增：启用备份复选框
    host.cb_enable_backup = QtWidgets.QCheckBox(" 启用备份功能")
    host.cb_enable_backup.setProperty('orig_text', " 启用备份功能")
    host.cb_enable_backup.setChecked(True)
    host.cb_enable_backup.toggled.connect(lambda checked: host._set_checkbox_mark(host.cb_enable_backup, checked))
    host.cb_enable_backup.toggled.connect(host._on_backup_toggled)
    host._set_checkbox_mark(host.cb_enable_backup, host.cb_enable_backup.isChecked())
    v.addWidget(host.cb_enable_backup)
    
    # 添加说明文本
    host.backup_hint = QtWidgets.QLabel(t('backup_hint'))
    host.backup_hint.setWordWrap(True)
    host.backup_hint.setStyleSheet(
        f"color: #666; font-size: {host._font_pt(10, 8)}pt; padding: {host._scale_px(5, 3)}px 0;"
    )
    v.addWidget(host.backup_hint)
    
    card.setFixedHeight(host._scale_px(260, 210, 260))
    
    return card


def _add_path_row(host: Any, layout: QtWidgets.QVBoxLayout, label: str, chooser):
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(host._scale_px(10, 6))
    lab = QtWidgets.QLabel(label + ":")
    lab.setMinimumWidth(host._scale_px(90, 68))
    edit = QtWidgets.QLineEdit()
    edit.setMinimumHeight(host._scale_px(32, 26))
    # v2.2.0 修复：设置路径输入框的文本对齐方式，避免长路径被截断显示
    edit.setCursorPosition(0)  # 默认显示路径开头
    btn = QtWidgets.QPushButton("浏览")
    btn.setProperty("class", "Secondary")
    btn.setMinimumWidth(host._scale_px(80, 58))
    btn.setMinimumHeight(host._scale_px(32, 26))
    btn.clicked.connect(chooser)
    row.addWidget(lab)
    row.addWidget(edit, 1)
    row.addWidget(btn)
    layout.addLayout(row)
    # v2.2.0 修复：为输入框设置工具提示，显示完整路径
    edit.textChanged.connect(lambda text: edit.setToolTip(text) if text else None)
    # v3.3.0：路径手动编辑标记配置修改
    edit.textChanged.connect(lambda _: host._mark_config_modified())
    return edit, btn, lab  # v3.0.2: 返回标签引用用于多语言


def _build_upload_settings(host: Any) -> QtWidgets.QFrame:
    card, v, host.title_settings = host._card("⚙️ 上传设置", "card_upload_settings")
    
    # v3.0.0 修复：将设置内容放入滚动区域，防止可折叠组件展开时影响其他卡片大小
    scroll_area = QtWidgets.QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    # 关键：设置尺寸策略，防止滚动区域随内容扩展
    scroll_area.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Expanding
    )
    # 设置滚动区域的最小高度，防止被压缩得太小
    scroll_area.setMinimumHeight(host._scale_px(200, 150))
    
    # 创建滚动内容容器
    scroll_content = QtWidgets.QWidget()
    scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
    scroll_layout.setContentsMargins(0, 0, host._scale_px(8, 5), 0)
    scroll_layout.setSpacing(host._scale_px(10, 6))
    
    # 将后续所有内容添加到 scroll_layout 而不是 v
    # ========== v2.0 新增：协议选择 ==========
    host.protocol_title_label = QtWidgets.QLabel(t('upload_protocol_title'))
    host.protocol_title_label.setStyleSheet("color:#1976D2; font-size:11px; font-weight:700;")
    scroll_layout.addWidget(host.protocol_title_label)
    
    # 协议选择下拉框
    protocol_row = QtWidgets.QHBoxLayout()
    host.protocol_type_label = QtWidgets.QLabel(t('protocol_type_label'))
    host.combo_protocol = QtWidgets.QComboBox()
    host.combo_protocol.addItems([
        t('protocol_option_smb'),
        t('protocol_option_ftp_client'),
        t('protocol_option_both')
    ])
    host.combo_protocol.currentIndexChanged.connect(host._on_protocol_changed)
    protocol_row.addWidget(host.protocol_type_label)
    protocol_row.addWidget(host.combo_protocol, 1)
    scroll_layout.addLayout(protocol_row)
    
    # 协议说明
    host.protocol_desc = QtWidgets.QLabel()
    host.protocol_desc.setWordWrap(True)
    host.protocol_desc.setStyleSheet("color: #6B7280; padding: 8px; background: #F3F4F6; border-radius: 6px; font-size: 10px;")
    scroll_layout.addWidget(host.protocol_desc)
    host._update_protocol_description(0)
    
    # v3.1.0 新增：FTP 服务器独立开关（默认SMB模式下禁用）
    ftp_server_switch_row = QtWidgets.QHBoxLayout()
    host.cb_enable_ftp_server = QtWidgets.QCheckBox(t('enable_ftp_server'))
    host.cb_enable_ftp_server.setChecked(False)
    host.cb_enable_ftp_server.setEnabled(False)  # 默认SMB模式下禁用
    host.cb_enable_ftp_server.toggled.connect(host._on_ftp_server_toggled)
    ftp_server_switch_row.addWidget(host.cb_enable_ftp_server)
    ftp_server_switch_row.addStretch()
    scroll_layout.addLayout(ftp_server_switch_row)
    
    # FTP 服务器提示
    host.ftp_server_hint = QtWidgets.QLabel(t('ftp_server_hint'))
    host.ftp_server_hint.setWordWrap(True)
    host.ftp_server_hint.setStyleSheet("color: #9CA3AF; font-size: 9px; padding-left: 20px;")
    host.ftp_server_hint.setVisible(False)
    scroll_layout.addWidget(host.ftp_server_hint)
    
    # FTP 配置容器（v3.1.0: 始终可见但根据模式启用/禁用，避免布局跳动）
    host.ftp_config_widget = QtWidgets.QWidget()
    host.ftp_config_widget.setVisible(True)  # 始终可见
    host.ftp_config_widget.setEnabled(False)  # 默认SMB模式下禁用
    ftp_layout = QtWidgets.QVBoxLayout(host.ftp_config_widget)
    ftp_layout.setContentsMargins(0, host._scale_px(8, 5), 0, 0)
    ftp_layout.setSpacing(host._scale_px(10, 6))
    
    # ========== FTP 服务器配置 - 可折叠 ==========
    host.ftp_server_collapsible = CollapsibleBox(t('ftp_server_config'), host)
    server_layout = QtWidgets.QFormLayout()
    server_layout.setSpacing(host._scale_px(8, 5))
    server_layout.setContentsMargins(0, 0, 0, 0)
    
    host.ftp_server_host = QtWidgets.QLineEdit("0.0.0.0")
    host.ftp_server_host.setToolTip(t('listen_address_tooltip'))
    server_layout.addRow(t('listen_address'), host.ftp_server_host)
    
    host.ftp_server_port = QtWidgets.QSpinBox()
    host.ftp_server_port.setRange(1, 65535)
    host.ftp_server_port.setValue(2121)
    host.ftp_server_port.setToolTip(t('port_tooltip'))
    server_layout.addRow(t('port_label'), host.ftp_server_port)
    
    host.ftp_server_user = QtWidgets.QLineEdit("upload_user")
    host.ftp_server_user.setToolTip(t('username_tooltip'))
    server_layout.addRow(t('username_label'), host.ftp_server_user)
    
    # v3.1.0: 密码输入框带可见性切换按钮
    server_pass_row = QtWidgets.QHBoxLayout()
    host.ftp_server_pass = QtWidgets.QLineEdit("upload_pass")
    host.ftp_server_pass.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
    host.ftp_server_pass.setToolTip(t('password_tooltip'))
    host.btn_toggle_server_pass = QtWidgets.QToolButton()
    host.btn_toggle_server_pass.setText("👁")
    host.btn_toggle_server_pass.setToolTip(t('show_password'))
    host.btn_toggle_server_pass.setCheckable(True)
    host.btn_toggle_server_pass.setStyleSheet(
        f"QToolButton {{ border: none; font-size: {host._font_pt(14, 10)}px; padding: {host._scale_px(2, 1)}px; }}"
    )
    host.btn_toggle_server_pass.toggled.connect(lambda checked: host._toggle_password_visibility(
        host.ftp_server_pass, host.btn_toggle_server_pass, checked))
    server_pass_row.addWidget(host.ftp_server_pass, 1)
    server_pass_row.addWidget(host.btn_toggle_server_pass)
    server_layout.addRow(t('password_label'), server_pass_row)
    
    # 共享目录选择
    share_row = QtWidgets.QHBoxLayout()
    host.ftp_server_share = QtWidgets.QLineEdit()
    host.ftp_server_share.setPlaceholderText(t('select_ftp_share'))
    host.ftp_server_share.setToolTip(t('share_dir_tooltip'))
    host.btn_choose_ftp_share = QtWidgets.QPushButton(t('browse'))
    host.btn_choose_ftp_share.setProperty("class", "Secondary")
    host.btn_choose_ftp_share.clicked.connect(host._choose_ftp_share)
    share_row.addWidget(host.ftp_server_share, 1)
    share_row.addWidget(host.btn_choose_ftp_share)
    server_layout.addRow(t('share_directory'), share_row)
    
    # v2.0 新增：高级选项 - 被动模式
    host.cb_server_passive = QtWidgets.QCheckBox(t('enable_passive'))
    host.cb_server_passive.setChecked(True)
    host.cb_server_passive.setToolTip(t('passive_mode_tooltip'))
    server_layout.addRow("", host.cb_server_passive)
    
    # 被动端口范围
    passive_row = QtWidgets.QHBoxLayout()
    host.ftp_server_passive_start = QtWidgets.QSpinBox()
    host.ftp_server_passive_start.setRange(1024, 65535)
    host.ftp_server_passive_start.setValue(60000)
    host.ftp_server_passive_start.setPrefix(t('port_start') + " ")
    passive_row.addWidget(host.ftp_server_passive_start)
    
    host.ftp_server_passive_end = QtWidgets.QSpinBox()
    host.ftp_server_passive_end.setRange(1024, 65535)
    host.ftp_server_passive_end.setValue(65535)
    host.ftp_server_passive_end.setPrefix(t('port_end') + " ")
    passive_row.addWidget(host.ftp_server_passive_end)
    passive_row.addStretch()
    server_layout.addRow("  " + t('port_range'), passive_row)
    
    # v2.0 新增：TLS/SSL选项
    host.cb_server_tls = QtWidgets.QCheckBox(t('enable_tls'))
    host.cb_server_tls.setChecked(False)
    host.cb_server_tls.setToolTip(t('enable_tls_tooltip'))
    server_layout.addRow("", host.cb_server_tls)

    cert_row = QtWidgets.QHBoxLayout()
    host.ftp_server_cert = QtWidgets.QLineEdit()
    host.ftp_server_cert.setPlaceholderText(t('select_tls_cert'))
    host.btn_choose_ftp_cert = QtWidgets.QPushButton(t('browse'))
    host.btn_choose_ftp_cert.setProperty("class", "Secondary")
    host.btn_choose_ftp_cert.clicked.connect(host._choose_ftp_cert)
    cert_row.addWidget(host.ftp_server_cert, 1)
    cert_row.addWidget(host.btn_choose_ftp_cert)
    server_layout.addRow(t('tls_cert_file'), cert_row)

    key_row = QtWidgets.QHBoxLayout()
    host.ftp_server_key = QtWidgets.QLineEdit()
    host.ftp_server_key.setPlaceholderText(t('select_tls_key'))
    host.btn_choose_ftp_key = QtWidgets.QPushButton(t('browse'))
    host.btn_choose_ftp_key.setProperty("class", "Secondary")
    host.btn_choose_ftp_key.clicked.connect(host._choose_ftp_key)
    key_row.addWidget(host.ftp_server_key, 1)
    key_row.addWidget(host.btn_choose_ftp_key)
    server_layout.addRow(t('tls_key_file'), key_row)
    host.cb_server_tls.toggled.connect(host._update_ftp_tls_controls)
    host._update_ftp_tls_controls()
    
    # v2.0 新增：连接数限制
    conn_row = QtWidgets.QHBoxLayout()
    host.conn_label = QtWidgets.QLabel(t('max_connections'))
    host.ftp_server_max_conn = QtWidgets.QSpinBox()
    host.ftp_server_max_conn.setRange(1, 1000)
    host.ftp_server_max_conn.setValue(256)
    host.ftp_server_max_conn.setSuffix(" " + t('unit_connections'))
    conn_row.addWidget(host.conn_label)
    conn_row.addWidget(host.ftp_server_max_conn)
    
    host.ip_label = QtWidgets.QLabel("  " + t('per_ip_limit'))
    host.ftp_server_max_conn_per_ip = QtWidgets.QSpinBox()
    host.ftp_server_max_conn_per_ip.setRange(1, 100)
    host.ftp_server_max_conn_per_ip.setValue(5)
    host.ftp_server_max_conn_per_ip.setSuffix(" " + t('unit_connections'))
    conn_row.addWidget(host.ip_label)
    conn_row.addWidget(host.ftp_server_max_conn_per_ip)
    conn_row.addStretch()
    server_layout.addRow(t('connection_limit'), conn_row)
    
    # v2.0 新增：FTP服务器测试按钮
    host.btn_test_ftp_server = QtWidgets.QPushButton(t('test_config'))
    host.btn_test_ftp_server.setProperty("class", "Secondary")
    host.btn_test_ftp_server.clicked.connect(host._test_ftp_server_config)
    server_layout.addRow("", host.btn_test_ftp_server)

    host.btn_toggle_ftp_server = QtWidgets.QPushButton(t('start_ftp_server'))
    host.btn_toggle_ftp_server.setProperty("class", "Primary")
    host.btn_toggle_ftp_server.clicked.connect(host._toggle_ftp_server_only)
    server_layout.addRow("", host.btn_toggle_ftp_server)
    
    host.ftp_server_collapsible.setContentLayout(server_layout)
    ftp_layout.addWidget(host.ftp_server_collapsible)
    
    # ========== FTP 客户端配置 - 可折叠 ==========
    host.ftp_client_collapsible = CollapsibleBox(t('ftp_client_config'), host)
    client_layout = QtWidgets.QFormLayout()
    client_layout.setSpacing(host._scale_px(8, 5))
    client_layout.setContentsMargins(0, 0, 0, 0)
    
    host.ftp_client_host = QtWidgets.QLineEdit()
    host.ftp_client_host.setPlaceholderText("ftp.example.com")
    host.ftp_client_host.setToolTip(t('server_address_tooltip'))
    client_layout.addRow(t('server_label'), host.ftp_client_host)
    
    host.ftp_client_port = QtWidgets.QSpinBox()
    host.ftp_client_port.setRange(1, 65535)
    host.ftp_client_port.setValue(21)
    host.ftp_client_port.setToolTip(t('client_port_tooltip'))
    client_layout.addRow(t('port_label'), host.ftp_client_port)
    
    host.ftp_client_user = QtWidgets.QLineEdit()
    host.ftp_client_user.setPlaceholderText(t('username_placeholder'))
    host.ftp_client_user.setToolTip(t('client_username_tooltip'))
    client_layout.addRow(t('username_label'), host.ftp_client_user)
    
    # v3.1.0: 密码输入框带可见性切换按钮
    client_pass_row = QtWidgets.QHBoxLayout()
    host.ftp_client_pass = QtWidgets.QLineEdit()
    host.ftp_client_pass.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
    host.ftp_client_pass.setPlaceholderText(t('password_placeholder'))
    host.ftp_client_pass.setToolTip(t('client_password_tooltip'))
    host.btn_toggle_client_pass = QtWidgets.QToolButton()
    host.btn_toggle_client_pass.setText("👁")
    host.btn_toggle_client_pass.setToolTip(t('show_password'))
    host.btn_toggle_client_pass.setCheckable(True)
    host.btn_toggle_client_pass.setStyleSheet(
        f"QToolButton {{ border: none; font-size: {host._font_pt(14, 10)}px; padding: {host._scale_px(2, 1)}px; }}"
    )
    host.btn_toggle_client_pass.toggled.connect(lambda checked: host._toggle_password_visibility(
        host.ftp_client_pass, host.btn_toggle_client_pass, checked))
    client_pass_row.addWidget(host.ftp_client_pass, 1)
    client_pass_row.addWidget(host.btn_toggle_client_pass)
    client_layout.addRow(t('password_label'), client_pass_row)
    
    host.ftp_client_remote = QtWidgets.QLineEdit("/upload")
    host.ftp_client_remote.setToolTip(t('remote_path_tooltip'))
    client_layout.addRow(t('remote_path'), host.ftp_client_remote)
    
    # v2.0 新增：超时和重试配置
    timeout_row = QtWidgets.QHBoxLayout()
    host.ftp_client_timeout = QtWidgets.QSpinBox()
    host.ftp_client_timeout.setRange(10, 300)
    host.ftp_client_timeout.setValue(30)
    host.ftp_client_timeout.setSuffix(" " + t('seconds'))
    host.ftp_client_timeout.setToolTip(t('timeout_tooltip'))
    timeout_row.addWidget(host.ftp_client_timeout)
    timeout_row.addStretch()
    client_layout.addRow(t('timeout_label'), timeout_row)
    
    retry_row = QtWidgets.QHBoxLayout()
    host.ftp_client_retry = QtWidgets.QSpinBox()
    host.ftp_client_retry.setRange(0, 10)
    host.ftp_client_retry.setValue(3)
    host.ftp_client_retry.setSuffix(" " + t('unit_times'))
    host.ftp_client_retry.setToolTip(t('retry_tooltip'))
    retry_row.addWidget(host.ftp_client_retry)
    retry_row.addStretch()
    client_layout.addRow(t('ftp_retry_label'), retry_row)
    
    # v2.0 新增：高级选项 - 被动模式
    host.cb_client_passive = QtWidgets.QCheckBox(t('use_passive_mode'))
    host.cb_client_passive.setChecked(True)
    host.cb_client_passive.setToolTip(t('passive_mode_tooltip'))
    client_layout.addRow("", host.cb_client_passive)
    
    # v2.0 新增：TLS/SSL选项
    host.cb_client_tls = QtWidgets.QCheckBox(t('enable_tls'))
    host.cb_client_tls.setChecked(False)
    host.cb_client_tls.setToolTip(t('client_tls_tooltip'))
    client_layout.addRow("", host.cb_client_tls)
    
    # v2.0 新增：FTP客户端测试按钮
    host.btn_test_ftp_client = QtWidgets.QPushButton(t('test_connection'))
    host.btn_test_ftp_client.setProperty("class", "Secondary")
    host.btn_test_ftp_client.clicked.connect(host._test_ftp_client_connection)
    client_layout.addRow("", host.btn_test_ftp_client)
    
    host.ftp_client_collapsible.setContentLayout(client_layout)
    ftp_layout.addWidget(host.ftp_client_collapsible)
    
    scroll_layout.addWidget(host.ftp_config_widget)
    
    scroll_layout.addWidget(host._hline())
    # ========== v2.0 协议选择结束 ==========
    
    # interval - v3.0.2: 解包返回值保存标签引用用于多语言
    host.spin_interval, host.lbl_interval = _add_spin_row(host, scroll_layout, t("interval_label"), 10, 3600, 30)
    host.spin_disk, host.lbl_disk = _add_spin_row(host, scroll_layout, t("disk_threshold_label"), 5, 50, 10)
    host.spin_retry, host.lbl_retry = _add_spin_row(host, scroll_layout, t("retry_label"), 0, 10, 3)
    host.spin_disk_check, host.lbl_disk_check = _add_spin_row(host, scroll_layout, t("disk_check_label"), 1, 60, 5)
    # 绑定磁盘检查间隔变化事件
    host.spin_disk_check.valueChanged.connect(lambda val: setattr(host, 'disk_check_interval', val))
    # v3.3.0：spin 变更标记配置修改
    host.spin_interval.valueChanged.connect(lambda _: host._mark_config_modified())
    host.spin_disk.valueChanged.connect(lambda _: host._mark_config_modified())
    host.spin_retry.valueChanged.connect(lambda _: host._mark_config_modified())
    host.spin_disk_check.valueChanged.connect(lambda _: host._mark_config_modified())
    
    # ========== 文件类型限制 - 可折叠 ==========
    host.filter_collapsible = CollapsibleBox(t('file_filter_title'), host)
    grid = QtWidgets.QGridLayout()
    grid.setSpacing(10)
    host.cb_ext = {}
    exts = [
        ("JPG", ".jpg"), ("PNG", ".png"), ("BMP", ".bmp"), ("GIF", ".gif"), ("RAW", ".raw")
    ]
    for i, (name, ext) in enumerate(exts):
        cb = QtWidgets.QCheckBox(name)
        # store original text so we can add a visible ✓ fallback if styling fails
        cb.setProperty('orig_text', name)
        cb.setChecked(True)
        # connect toggled to update visible text marker (robust fallback)
        cb.toggled.connect(lambda checked, cb=cb: host._set_checkbox_mark(cb, checked))
        cb.toggled.connect(lambda _: host._mark_config_modified())
        # initialize text with marker if checked
        host._set_checkbox_mark(cb, cb.isChecked())
        host.cb_ext[ext] = cb
        grid.addWidget(cb, i//3, i%3)
    host.filter_collapsible.addLayout(grid)
    scroll_layout.addWidget(host.filter_collapsible)
    
    # ========== 高级选项 - 可折叠 ==========
    host.adv_collapsible = CollapsibleBox(t('advanced_options_title'), host)
    
    host.cb_auto_start_windows = QtWidgets.QCheckBox(t('auto_start_windows'))
    host.cb_auto_start_windows.setProperty('orig_text', t('auto_start_windows'))
    host.cb_auto_start_windows.setChecked(False)
    host.cb_auto_start_windows.toggled.connect(host._toggle_autostart)
    host.cb_auto_start_windows.toggled.connect(lambda checked: host._set_checkbox_mark(host.cb_auto_start_windows, checked))
    host._set_checkbox_mark(host.cb_auto_start_windows, host.cb_auto_start_windows.isChecked())
    host.adv_collapsible.addWidget(host.cb_auto_start_windows)
    
    host.cb_auto_run_on_startup = QtWidgets.QCheckBox(t('auto_run_on_startup'))
    host.cb_auto_run_on_startup.setProperty('orig_text', t('auto_run_on_startup'))
    host.cb_auto_run_on_startup.setChecked(False)
    host.cb_auto_run_on_startup.toggled.connect(lambda checked: host._set_checkbox_mark(host.cb_auto_run_on_startup, checked))
    host._set_checkbox_mark(host.cb_auto_run_on_startup, host.cb_auto_run_on_startup.isChecked())
    host.adv_collapsible.addWidget(host.cb_auto_run_on_startup)
    
    # v2.2.0 新增：托盘通知开关
    host.cb_show_notifications = QtWidgets.QCheckBox(t('show_notifications'))
    host.cb_show_notifications.setProperty('orig_text', t('show_notifications'))
    host.cb_show_notifications.setChecked(True)
    host.cb_show_notifications.toggled.connect(lambda checked: setattr(host, 'show_notifications', checked))
    host.cb_show_notifications.toggled.connect(lambda checked: host._set_checkbox_mark(host.cb_show_notifications, checked))
    host._set_checkbox_mark(host.cb_show_notifications, host.cb_show_notifications.isChecked())
    host.adv_collapsible.addWidget(host.cb_show_notifications)
    
    # v2.3.0 新增：速率限制
    rate_row = QtWidgets.QHBoxLayout()
    host.cb_limit_rate = QtWidgets.QCheckBox(t('limit_upload_rate'))
    host.cb_limit_rate.setProperty('orig_text', t('limit_upload_rate'))
    host.cb_limit_rate.setToolTip(t('limit_rate_tooltip'))
    host.cb_limit_rate.setChecked(False)
    host.cb_limit_rate.toggled.connect(host._on_rate_limit_toggled)
    host.cb_limit_rate.toggled.connect(lambda checked: host._set_checkbox_mark(host.cb_limit_rate, checked))
    host._set_checkbox_mark(host.cb_limit_rate, host.cb_limit_rate.isChecked())
    
    host.spin_max_rate = QtWidgets.QDoubleSpinBox()
    host.spin_max_rate.setRange(0.1, 1000.0)
    host.spin_max_rate.setValue(10.0)
    host.spin_max_rate.setSuffix(" MB/s")
    host.spin_max_rate.setSingleStep(0.5)
    host.spin_max_rate.setEnabled(False)
    host.spin_max_rate.setToolTip(t('max_rate_tooltip'))
    host.spin_max_rate.valueChanged.connect(lambda: setattr(host, 'config_modified', True))
    
    rate_row.addWidget(host.cb_limit_rate)
    rate_row.addWidget(host.spin_max_rate)
    rate_row.addStretch()
    host.adv_collapsible.addLayout(rate_row)
    
    # 添加分隔线
    host.adv_collapsible.addWidget(host._hline())
    
    # 去重功能
    host.cb_dedup_enable = QtWidgets.QCheckBox(t('enable_dedup'))
    host.cb_dedup_enable.setProperty('orig_text', t('enable_dedup'))
    host.cb_dedup_enable.setChecked(False)
    host.cb_dedup_enable.toggled.connect(host._on_dedup_toggled)
    host.cb_dedup_enable.toggled.connect(lambda checked: host._set_checkbox_mark(host.cb_dedup_enable, checked))
    host._set_checkbox_mark(host.cb_dedup_enable, host.cb_dedup_enable.isChecked())
    host.adv_collapsible.addWidget(host.cb_dedup_enable)
    
    # 哈希算法选择
    hash_row = QtWidgets.QHBoxLayout()
    host.hash_lab = QtWidgets.QLabel(t('hash_algorithm') + ":")
    host.combo_hash = QtWidgets.QComboBox()
    host.combo_hash.addItems(["MD5", "SHA256"])
    host.combo_hash.setEnabled(False)
    hash_row.addWidget(host.hash_lab)
    hash_row.addWidget(host.combo_hash)
    host.adv_collapsible.addLayout(hash_row)
    
    # 去重策略选择
    strategy_row = QtWidgets.QHBoxLayout()
    host.strategy_lab = QtWidgets.QLabel(t('duplicate_strategy') + ":")
    host.combo_strategy = QtWidgets.QComboBox()
    host.combo_strategy.addItems([t('strategy_skip'), t('strategy_rename'), t('strategy_overwrite'), t('strategy_ask')])
    host.combo_strategy.setEnabled(False)
    strategy_row.addWidget(host.strategy_lab)
    strategy_row.addWidget(host.combo_strategy)
    host.adv_collapsible.addLayout(strategy_row)
    
    # 说明文本
    host.dedup_hint = QtWidgets.QLabel(t('dedup_hint'))
    host.dedup_hint.setStyleSheet("color:#757575; font-size:9px; padding:4px;")
    host.dedup_hint.setWordWrap(True)
    host.adv_collapsible.addWidget(host.dedup_hint)
    
    # 添加分隔线
    host.adv_collapsible.addWidget(host._hline())
    
    # 网络监控选项
    host.network_sub_lab = QtWidgets.QLabel(t('network_monitor'))
    host.network_sub_lab.setStyleSheet("color:#666; font-size:10px; font-weight:700;")
    host.adv_collapsible.addWidget(host.network_sub_lab)
    
    # 网络检测间隔 - 压缩布局
    network_check_row = QtWidgets.QHBoxLayout()
    host.network_check_lab = QtWidgets.QLabel(t('check_interval_label'))
    host.spin_network_check = QtWidgets.QSpinBox()
    host.spin_network_check.setRange(5, 60)
    host.spin_network_check.setValue(10)
    host.spin_network_check.setSuffix(" " + t('seconds'))
    network_check_row.addWidget(host.network_check_lab)
    network_check_row.addWidget(host.spin_network_check)
    network_check_row.addStretch()
    host.adv_collapsible.addLayout(network_check_row)
    
    host.cb_network_auto_pause = QtWidgets.QCheckBox(t('auto_pause_on_disconnect'))
    host.cb_network_auto_pause.setProperty('orig_text', t('auto_pause_on_disconnect'))
    host.cb_network_auto_pause.setChecked(True)
    host.cb_network_auto_pause.toggled.connect(lambda checked: host._set_checkbox_mark(host.cb_network_auto_pause, checked))
    host.cb_network_auto_pause.toggled.connect(lambda _: host._mark_config_modified())
    host._set_checkbox_mark(host.cb_network_auto_pause, host.cb_network_auto_pause.isChecked())
    host.adv_collapsible.addWidget(host.cb_network_auto_pause)
    
    host.cb_network_auto_resume = QtWidgets.QCheckBox(t('auto_resume_on_reconnect'))
    host.cb_network_auto_resume.setProperty('orig_text', t('auto_resume_on_reconnect'))
    host.cb_network_auto_resume.setChecked(True)
    host.cb_network_auto_resume.toggled.connect(lambda checked: host._set_checkbox_mark(host.cb_network_auto_resume, checked))
    host.cb_network_auto_resume.toggled.connect(lambda _: host._mark_config_modified())
    host._set_checkbox_mark(host.cb_network_auto_resume, host.cb_network_auto_resume.isChecked())
    host.adv_collapsible.addWidget(host.cb_network_auto_resume)
    
    # 说明文本
    host.network_hint = QtWidgets.QLabel(t('network_hint'))
    host.network_hint.setStyleSheet("color:#757575; font-size:9px; padding:4px;")
    host.network_hint.setWordWrap(True)
    host.adv_collapsible.addWidget(host.network_hint)
    
    scroll_layout.addWidget(host.adv_collapsible)
    
    # 添加弹性空间，使内容紧凑排列
    scroll_layout.addStretch()
    
    # 设置滚动区域
    scroll_area.setWidget(scroll_content)
    v.addWidget(scroll_area, 1)  # stretch=1 让滚动区域填满剩余空间
    
    return card


def _add_spin_row(host: Any, layout: QtWidgets.QVBoxLayout, label: str, low: int, high: int, val: int):
    """创建带标签的数值输入行，返回 (QSpinBox, QLabel) 用于多语言支持"""
    row = QtWidgets.QHBoxLayout()
    lab = QtWidgets.QLabel(label + ":")
    sp = QtWidgets.QSpinBox()
    sp.setRange(low, high)
    sp.setValue(val)
    row.addWidget(lab)
    row.addWidget(sp)
    layout.addLayout(row)
    return sp, lab  # v3.0.2: 返回标签用于多语言


def _build_status_panel(host: Any) -> QtWidgets.QFrame:
    card, v, host.title_status = host._card("📊 运行状态", "card_status")
    # status pill
    host.lbl_status = QtWidgets.QLabel(t('status_stopped'))
    host.lbl_status.setStyleSheet(
        f"background:#FEE2E2; color:#B91C1C; padding:{host._scale_px(6, 4)}px {host._scale_px(12, 8)}px; "
        f"font-weight:700; border-radius:{host._scale_px(12, 8)}px; font-size:{host._font_pt(10)}pt;"
    )
    v.addWidget(host.lbl_status)
    # chips - 低分辨率下减少列数，避免中间列横向撑宽。
    grid = QtWidgets.QGridLayout()
    grid.setSpacing(host._scale_px(12, 6))
    host.lbl_uploaded = _chip(host, t('uploaded'), "0", "#E3F2FD", "#1976D2")
    host.lbl_failed = _chip(host, t('failed'), "0", "#FFEBEE", "#C62828")
    host.lbl_skipped = _chip(host, t('skipped'), "0", "#FFF9C3", "#F57F17")
    host.lbl_rate = _chip(host, t('rate'), "0 MB/s", "#E8F5E9", "#2E7D32")
    host.lbl_queue = _chip(host, t('archive_queue'), "0", "#F3E5F5", "#6A1B9A")
    host.lbl_time = _chip(host, t('runtime'), "00:00:00", "#FFF3E0", "#E65100")
    # 新增：磁盘空间芯片
    host.lbl_target_disk = _chip(host, t('target_disk'), "--", "#E1F5FE", "#01579B")
    host.lbl_backup_disk = _chip(host, t('backup_disk'), "--", "#F1F8E9", "#33691E")
    # v1.9 新增：网络状态芯片
    host.lbl_network = _chip(host, t('network_status'), t('network_unknown'), "#ECEFF1", "#546E7A")
    # v2.0 新增：协议和FTP状态芯片
    host.lbl_protocol = _chip(host, t('protocol_chip'), "SMB", "#E8EAF6", "#3F51B5")
    host.lbl_ftp_server = _chip(host, t('ftp_server_chip'), t('not_started'), "#FCE4EC", "#C2185B")
    host.lbl_ftp_client = _chip(host, t('ftp_client_chip'), t('not_connected'), "#FFF8E1", "#F57C00")
    # v3.1.0 新增：当前模式芯片（醒目显示）
    host.lbl_current_mode = _chip(host, t('current_mode'), t('mode_smb'), "#E3F2FD", "#1565C0")
    
    host.status_grid = grid
    host.status_chip_widgets = [
        host.lbl_uploaded, host.lbl_failed, host.lbl_skipped,
        host.lbl_rate, host.lbl_queue, host.lbl_time,
        host.lbl_target_disk, host.lbl_backup_disk, host.lbl_network,
        host.lbl_protocol, host.lbl_ftp_server, host.lbl_ftp_client,
        host.lbl_current_mode,
    ]
    for i, w in enumerate(host.status_chip_widgets):
        grid.addWidget(w, i // host.status_grid_columns, i % host.status_grid_columns)
    v.addLayout(grid)
    
    # 分隔线
    v.addWidget(host._hline())
    
    # 新增：当前文件信息
    host.current_file_label_widget = QtWidgets.QLabel(t('current_file_label'))
    host.current_file_label_widget.setStyleSheet(
        f"font-weight:700; font-size:{host._font_pt(10)}pt; color:#424242; margin-top:{host._scale_px(4, 2)}px;"
    )
    v.addWidget(host.current_file_label_widget)
    
    host.lbl_current_file = QtWidgets.QLabel(t('waiting'))
    host.lbl_current_file.setStyleSheet(
        f"color:#616161; font-size:{host._font_pt(9)}pt; padding:{host._scale_px(4, 2)}px {host._scale_px(8, 5)}px;"
    )
    host.lbl_current_file.setWordWrap(True)
    v.addWidget(host.lbl_current_file)
    
    # 当前文件进度条
    host.pbar_file = QtWidgets.QProgressBar()
    host.pbar_file.setRange(0, 100)
    host.pbar_file.setValue(0)
    host.pbar_file.setTextVisible(True)
    host.pbar_file.setFormat(t('waiting'))
    host.pbar_file.setStyleSheet("""
        QProgressBar {
            border: 2px solid #BDBDBD;
            border-radius: 6px;
            text-align: center;
            min-height: 18px;
            background-color: #F5F5F5;
        }
        QProgressBar::chunk {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                                        stop:0 #4CAF50, stop:1 #81C784);
            border-radius: 4px;
        }
    """)
    v.addWidget(host.pbar_file)
    
    # 分隔线
    v.addWidget(host._hline())
    
    # progress
    host.lbl_progress = QtWidgets.QLabel(t('waiting'))
    v.addWidget(host.lbl_progress)
    host.pbar = QtWidgets.QProgressBar()
    host.pbar.setRange(0, 100)
    host.pbar.setValue(0)
    v.addWidget(host.pbar)
    return card


def _chip(host: Any, title: str, val: str, bg: str, fg: str) -> ChipWidget:
    return ChipWidget(title, val, bg, fg, host)


def _build_log_panel(host: Any) -> QtWidgets.QFrame:
    card, v, host.title_log = host._card("📜 运行日志", "card_log")
    # toolbar
    toolbar = QtWidgets.QHBoxLayout()
    toolbar.addStretch(1)
    
    # 右侧：自动滚动
    host.cb_autoscroll = QtWidgets.QCheckBox("📜 自动滚动")
    host.cb_autoscroll.setChecked(True)
    toolbar.addWidget(host.cb_autoscroll)
    v.addLayout(toolbar)
    # log area - 压缩高度以节省空间
    host.log = QtWidgets.QPlainTextEdit()
    host.log.setReadOnly(True)
    # UI 仅保留最近 5000 行；磁盘日志仍按日完整写入。
    host.log.document().setMaximumBlockCount(5000)
    host.log.setMinimumHeight(host._scale_px(300, 180, 300))
    v.addWidget(host.log)
    return card
