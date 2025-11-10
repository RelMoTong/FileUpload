# -*- coding: utf-8 -*-
"""
FTP UI 组件模块
为 v2.0 提供 FTP 服务器和客户端配置界面

版本: v2.0
日期: 2025-11-10
作者: 开发团队

设计风格说明：
- 与 pyqt_app.py 保持完全一致的蓝色主题
- 使用相同的颜色方案：#1976D2（主色）、#64B5F6（边框）、#E3F2FD（背景）
- 使用相同的按钮样式类：Primary、Secondary、Warning、Danger
- 使用相同的圆角、间距、字体设置
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, 
    QLineEdit, QPushButton, QSpinBox, QCheckBox, QFileDialog,
    QListWidget, QListWidgetItem, QMessageBox, QComboBox,
    QFormLayout, QTabWidget, QTextEdit, QFrame
)
from PySide6.QtCore import Qt, Signal
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class FTPServerConfigWidget(QWidget):
    """
    FTP 服务器配置面板
    
    功能：
    - 服务器基本配置（IP、端口、用户名、密码）
    - 共享目录选择
    - 高级设置（TLS、被动端口、连接限制）
    - 启动/停止按钮
    - 状态显示
    """
    
    # 信号定义
    start_server_signal = Signal(dict)  # 启动服务器信号
    stop_server_signal = Signal()       # 停止服务器信号
    test_server_signal = Signal()       # 测试服务器信号
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 使用 Card 风格的 Frame
        card = QFrame(self)
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        
        # 标题（使用 Title 样式类）
        title_label = QLabel("FTP 服务器配置")
        title_label.setProperty("class", "Title")
        card_layout.addWidget(title_label)
        
        # 基本配置组
        basic_group = QGroupBox("基本配置")
        basic_layout = QFormLayout()
        
        # 监听地址
        self.host_edit = QLineEdit("0.0.0.0")
        self.host_edit.setPlaceholderText("监听地址（0.0.0.0 表示所有网卡）")
        basic_layout.addRow("监听地址:", self.host_edit)
        
        # 端口
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(2121)  # 默认非特权端口
        self.port_spin.setToolTip("端口 < 1024 需要管理员权限")
        basic_layout.addRow("端口:", self.port_spin)
        
        # 用户名
        self.username_edit = QLineEdit("upload_user")
        self.username_edit.setPlaceholderText("FTP 用户名")
        basic_layout.addRow("用户名:", self.username_edit)
        
        # 密码
        self.password_edit = QLineEdit("upload_pass")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("FTP 密码")
        basic_layout.addRow("密码:", self.password_edit)
        
        # 共享目录
        share_layout = QHBoxLayout()
        self.share_folder_edit = QLineEdit("D:/FTP_Share")
        self.share_folder_edit.setPlaceholderText("选择共享目录")
        share_layout.addWidget(self.share_folder_edit)
        
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self.browse_share_folder)
        share_layout.addWidget(browse_btn)
        
        basic_layout.addRow("共享目录:", share_layout)
        
        basic_group.setLayout(basic_layout)
        card_layout.addWidget(basic_group)
        
        # 高级配置组
        advanced_group = QGroupBox("高级配置")
        advanced_layout = QFormLayout()
        
        # TLS 加密
        self.tls_check = QCheckBox("启用 FTPS (TLS/SSL)")
        self.tls_check.setToolTip("需要证书文件支持")
        self.tls_check.toggled.connect(self.on_tls_toggled)
        advanced_layout.addRow("加密:", self.tls_check)
        
        # 证书文件
        cert_layout = QHBoxLayout()
        self.cert_file_edit = QLineEdit()
        self.cert_file_edit.setPlaceholderText("cert.pem")
        self.cert_file_edit.setEnabled(False)
        cert_layout.addWidget(self.cert_file_edit)
        
        cert_browse_btn = QPushButton("浏览...")
        cert_browse_btn.clicked.connect(self.browse_cert_file)
        cert_browse_btn.setEnabled(False)
        cert_layout.addWidget(cert_browse_btn)
        self.cert_browse_btn = cert_browse_btn
        
        advanced_layout.addRow("证书文件:", cert_layout)
        
        # 密钥文件
        key_layout = QHBoxLayout()
        self.key_file_edit = QLineEdit()
        self.key_file_edit.setPlaceholderText("key.pem")
        self.key_file_edit.setEnabled(False)
        key_layout.addWidget(self.key_file_edit)
        
        key_browse_btn = QPushButton("浏览...")
        key_browse_btn.clicked.connect(self.browse_key_file)
        key_browse_btn.setEnabled(False)
        key_layout.addWidget(key_browse_btn)
        self.key_browse_btn = key_browse_btn
        
        advanced_layout.addRow("密钥文件:", key_layout)
        
        # 被动端口范围
        passive_layout = QHBoxLayout()
        self.passive_start_spin = QSpinBox()
        self.passive_start_spin.setRange(1024, 65535)
        self.passive_start_spin.setValue(60000)
        passive_layout.addWidget(self.passive_start_spin)
        
        passive_layout.addWidget(QLabel("-"))
        
        self.passive_end_spin = QSpinBox()
        self.passive_end_spin.setRange(1024, 65535)
        self.passive_end_spin.setValue(65535)
        passive_layout.addWidget(self.passive_end_spin)
        
        advanced_layout.addRow("被动端口:", passive_layout)
        
        # 最大连接数
        self.max_cons_spin = QSpinBox()
        self.max_cons_spin.setRange(1, 1000)
        self.max_cons_spin.setValue(256)
        advanced_layout.addRow("最大连接数:", self.max_cons_spin)
        
        # 单IP最大连接
        self.max_cons_per_ip_spin = QSpinBox()
        self.max_cons_per_ip_spin.setRange(1, 100)
        self.max_cons_per_ip_spin.setValue(5)
        advanced_layout.addRow("单IP最大连接:", self.max_cons_per_ip_spin)
        
        advanced_group.setLayout(advanced_layout)
        card_layout.addWidget(advanced_group)
        
        # 控制按钮（使用 Primary 和 Danger 样式类）
        button_layout = QHBoxLayout()
        
        self.start_btn = QPushButton("启动服务器")
        self.start_btn.clicked.connect(self.on_start_clicked)
        self.start_btn.setProperty("class", "Primary")  # 使用主色调按钮
        button_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("停止服务器")
        self.stop_btn.clicked.connect(self.on_stop_clicked)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setProperty("class", "Danger")  # 使用危险色按钮
        button_layout.addWidget(self.stop_btn)
        
        card_layout.addLayout(button_layout)
        
        # 状态显示
        status_group = QGroupBox("服务器状态")
        status_layout = QVBoxLayout()
        
        self.status_label = QLabel("未启动")
        status_layout.addWidget(self.status_label)
        
        self.connections_label = QLabel("连接数: 0")
        status_layout.addWidget(self.connections_label)
        
        status_group.setLayout(status_layout)
        card_layout.addWidget(status_group)
        
        card_layout.addStretch()
        layout.addWidget(card)
    
    def browse_share_folder(self):
        """浏览共享目录"""
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择共享目录",
            self.share_folder_edit.text()
        )
        if folder:
            self.share_folder_edit.setText(folder)
    
    def browse_cert_file(self):
        """浏览证书文件"""
        file, _ = QFileDialog.getOpenFileName(
            self,
            "选择证书文件",
            "",
            "PEM Files (*.pem);;All Files (*)"
        )
        if file:
            self.cert_file_edit.setText(file)
    
    def browse_key_file(self):
        """浏览密钥文件"""
        file, _ = QFileDialog.getOpenFileName(
            self,
            "选择密钥文件",
            "",
            "PEM Files (*.pem);;All Files (*)"
        )
        if file:
            self.key_file_edit.setText(file)
    
    def on_tls_toggled(self, checked):
        """TLS 复选框切换"""
        self.cert_file_edit.setEnabled(checked)
        self.cert_browse_btn.setEnabled(checked)
        self.key_file_edit.setEnabled(checked)
        self.key_browse_btn.setEnabled(checked)
    
    def on_start_clicked(self):
        """启动按钮点击"""
        # 验证配置
        if not self.validate_config():
            return
        
        # 获取配置
        config = self.get_config()
        
        # 发射信号
        self.start_server_signal.emit(config)
        
        # 更新按钮状态
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        # 禁用配置编辑
        self.set_config_editable(False)
    
    def on_stop_clicked(self):
        """停止按钮点击"""
        # 发射信号
        self.stop_server_signal.emit()
        
        # 更新按钮状态
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        # 启用配置编辑
        self.set_config_editable(True)
    
    def validate_config(self) -> bool:
        """验证配置"""
        # 检查端口范围
        if self.passive_start_spin.value() >= self.passive_end_spin.value():
            QMessageBox.warning(self, "配置错误", "被动端口起始值必须小于结束值")
            return False
        
        # 检查共享目录
        share_folder = Path(self.share_folder_edit.text())
        if not share_folder.exists():
            reply = QMessageBox.question(
                self, 
                "目录不存在", 
                f"目录 {share_folder} 不存在，是否创建？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    share_folder.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    QMessageBox.critical(self, "创建失败", f"无法创建目录：{e}")
                    return False
            else:
                return False
        
        # 检查 TLS 证书
        if self.tls_check.isChecked():
            cert_file = Path(self.cert_file_edit.text())
            key_file = Path(self.key_file_edit.text())
            
            if not cert_file.exists():
                QMessageBox.warning(self, "配置错误", "证书文件不存在")
                return False
            
            if not key_file.exists():
                QMessageBox.warning(self, "配置错误", "密钥文件不存在")
                return False
        
        return True
    
    def get_config(self) -> dict:
        """获取配置字典"""
        return {
            'host': self.host_edit.text(),
            'port': self.port_spin.value(),
            'username': self.username_edit.text(),
            'password': self.password_edit.text(),
            'shared_folder': self.share_folder_edit.text(),
            'enable_tls': self.tls_check.isChecked(),
            'cert_file': self.cert_file_edit.text() if self.tls_check.isChecked() else '',
            'key_file': self.key_file_edit.text() if self.tls_check.isChecked() else '',
            'passive_ports': (self.passive_start_spin.value(), self.passive_end_spin.value()),
            'max_cons': self.max_cons_spin.value(),
            'max_cons_per_ip': self.max_cons_per_ip_spin.value(),
        }
    
    def set_config(self, config: dict):
        """设置配置"""
        self.host_edit.setText(config.get('host', '0.0.0.0'))
        self.port_spin.setValue(config.get('port', 2121))
        self.username_edit.setText(config.get('username', 'upload_user'))
        self.password_edit.setText(config.get('password', 'upload_pass'))
        self.share_folder_edit.setText(config.get('shared_folder', 'D:/FTP_Share'))
        
        self.tls_check.setChecked(config.get('enable_tls', False))
        self.cert_file_edit.setText(config.get('cert_file', ''))
        self.key_file_edit.setText(config.get('key_file', ''))
        
        passive_ports = config.get('passive_ports', (60000, 65535))
        self.passive_start_spin.setValue(passive_ports[0])
        self.passive_end_spin.setValue(passive_ports[1])
        
        self.max_cons_spin.setValue(config.get('max_cons', 256))
        self.max_cons_per_ip_spin.setValue(config.get('max_cons_per_ip', 5))
    
    def set_config_editable(self, editable: bool):
        """设置配置是否可编辑"""
        self.host_edit.setEnabled(editable)
        self.port_spin.setEnabled(editable)
        self.username_edit.setEnabled(editable)
        self.password_edit.setEnabled(editable)
        self.share_folder_edit.setEnabled(editable)
        self.tls_check.setEnabled(editable)
        self.cert_file_edit.setEnabled(editable and self.tls_check.isChecked())
        self.key_file_edit.setEnabled(editable and self.tls_check.isChecked())
        self.passive_start_spin.setEnabled(editable)
        self.passive_end_spin.setEnabled(editable)
        self.max_cons_spin.setEnabled(editable)
        self.max_cons_per_ip_spin.setEnabled(editable)
    
    def update_status(self, status: dict):
        """更新状态显示"""
        if status.get('running', False):
            # 使用主题色显示运行状态
            self.status_label.setText(f"✓ 运行中 - {status.get('address', 'N/A')}")
            self.status_label.setStyleSheet("color: #166534; font-weight: bold;")  # 绿色成功状态
            
            connections = status.get('connections', 0)
            self.connections_label.setText(f"连接数: {connections}")
        else:
            self.status_label.setText("未启动")
            self.status_label.setStyleSheet("color: #6B7280;")  # 灰色
            self.connections_label.setText("连接数: 0")


class FTPClientConfigWidget(QWidget):
    """
    FTP 客户端配置面板
    
    功能：
    - 多客户端管理（列表）
    - 添加/删除/编辑客户端
    - 客户端配置（服务器地址、端口、认证、路径）
    - 连接测试
    - 状态显示
    """
    
    # 信号定义
    add_client_signal = Signal(str, dict)      # 添加客户端信号 (name, config)
    remove_client_signal = Signal(str)         # 移除客户端信号 (name)
    test_client_signal = Signal(str)           # 测试客户端信号 (name)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_client_name = None
        self.init_ui()
    
    def init_ui(self):
        """初始化界面"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 左侧卡片：客户端列表
        left_card = QFrame(self)
        left_card.setObjectName("Card")
        left_panel = QVBoxLayout(left_card)
        
        list_label = QLabel("FTP 客户端列表")
        list_label.setProperty("class", "Title")
        left_panel.addWidget(list_label)
        
        self.client_list = QListWidget()
        self.client_list.itemClicked.connect(self.on_client_selected)
        left_panel.addWidget(self.client_list)
        
        # 列表操作按钮（使用 Secondary 样式）
        list_btn_layout = QHBoxLayout()
        
        add_btn = QPushButton("新增")
        add_btn.clicked.connect(self.on_add_client)
        add_btn.setProperty("class", "Secondary")
        list_btn_layout.addWidget(add_btn)
        
        self.remove_btn = QPushButton("删除")
        self.remove_btn.clicked.connect(self.on_remove_client)
        self.remove_btn.setEnabled(False)
        self.remove_btn.setProperty("class", "Danger")
        list_btn_layout.addWidget(self.remove_btn)
        
        left_panel.addLayout(list_btn_layout)
        
        layout.addWidget(left_card, 1)
        
        # 右侧卡片：客户端配置
        right_card = QFrame(self)
        right_card.setObjectName("Card")
        right_panel = QVBoxLayout(right_card)
        
        config_label = QLabel("客户端配置")
        config_label.setProperty("class", "Title")
        right_panel.addWidget(config_label)
        
        # 配置表单
        form_layout = QFormLayout()
        
        # 客户端名称
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("FTP客户端1")
        form_layout.addRow("名称:", self.name_edit)
        
        # 服务器地址
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("ftp.example.com")
        form_layout.addRow("服务器:", self.host_edit)
        
        # 端口
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(21)
        form_layout.addRow("端口:", self.port_spin)
        
        # 用户名
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("用户名")
        form_layout.addRow("用户名:", self.username_edit)
        
        # 密码
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("密码")
        form_layout.addRow("密码:", self.password_edit)
        
        # 远程路径
        self.remote_path_edit = QLineEdit("/upload")
        self.remote_path_edit.setPlaceholderText("/upload/photos")
        form_layout.addRow("远程路径:", self.remote_path_edit)
        
        # TLS
        self.tls_check = QCheckBox("使用 FTPS (TLS/SSL)")
        form_layout.addRow("加密:", self.tls_check)
        
        # 被动模式
        self.passive_check = QCheckBox("使用被动模式（推荐）")
        self.passive_check.setChecked(True)
        form_layout.addRow("连接模式:", self.passive_check)
        
        # 超时时间
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(30)
        self.timeout_spin.setSuffix(" 秒")
        form_layout.addRow("超时时间:", self.timeout_spin)
        
        # 重试次数
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(1, 10)
        self.retry_spin.setValue(3)
        self.retry_spin.setSuffix(" 次")
        form_layout.addRow("重试次数:", self.retry_spin)
        
        right_panel.addLayout(form_layout)
        
        # 操作按钮（使用主题样式）
        btn_layout = QHBoxLayout()
        
        self.save_btn = QPushButton("保存配置")
        self.save_btn.clicked.connect(self.on_save_config)
        self.save_btn.setEnabled(False)
        self.save_btn.setProperty("class", "Primary")
        btn_layout.addWidget(self.save_btn)
        
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self.on_test_connection)
        self.test_btn.setEnabled(False)
        self.test_btn.setProperty("class", "Secondary")
        btn_layout.addWidget(self.test_btn)
        
        right_panel.addLayout(btn_layout)
        
        # 状态显示
        self.status_label = QLabel("请选择或新增客户端")
        self.status_label.setStyleSheet("color: #6B7280;")  # 灰色提示文字
        right_panel.addWidget(self.status_label)
        
        right_panel.addStretch()
        
        layout.addWidget(right_card, 2)
    
    def on_client_selected(self, item):
        """客户端列表项被选中"""
        self.current_client_name = item.text()
        self.remove_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.test_btn.setEnabled(True)
        
        # 加载配置（这里简化处理，实际需要从外部获取）
        self.status_label.setText(f"已选择: {self.current_client_name}")
        self.status_label.setStyleSheet("color: #1976D2; font-weight: bold;")  # 主题蓝色
    
    def on_add_client(self):
        """添加新客户端"""
        # 验证配置
        if not self.validate_config():
            return
        
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "输入错误", "请输入客户端名称")
            return
        
        # 检查重复
        for i in range(self.client_list.count()):
            if self.client_list.item(i).text() == name:
                QMessageBox.warning(self, "重复名称", f"客户端 '{name}' 已存在")
                return
        
        # 获取配置
        config = self.get_config()
        
        # 发射信号
        self.add_client_signal.emit(name, config)
        
        # 添加到列表
        item = QListWidgetItem(name)
        self.client_list.addItem(item)
        
        # 清空表单
        self.clear_form()
        
        QMessageBox.information(self, "成功", f"客户端 '{name}' 已添加")
    
    def on_remove_client(self):
        """删除客户端"""
        if not self.current_client_name:
            return
        
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除客户端 '{self.current_client_name}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            # 发射信号
            self.remove_client_signal.emit(self.current_client_name)
            
            # 从列表中移除
            for i in range(self.client_list.count()):
                if self.client_list.item(i).text() == self.current_client_name:
                    self.client_list.takeItem(i)
                    break
            
            # 清空表单
            self.clear_form()
            self.current_client_name = None
            self.remove_btn.setEnabled(False)
            self.save_btn.setEnabled(False)
            self.test_btn.setEnabled(False)
    
    def on_save_config(self):
        """保存配置"""
        if not self.current_client_name:
            return
        
        if not self.validate_config():
            return
        
        config = self.get_config()
        
        # 这里应该保存到配置文件或通知主程序
        # 暂时只显示消息
        QMessageBox.information(self, "成功", f"客户端 '{self.current_client_name}' 配置已保存")
    
    def on_test_connection(self):
        """测试连接"""
        if not self.current_client_name:
            return
        
        # 发射信号
        self.test_client_signal.emit(self.current_client_name)
    
    def validate_config(self) -> bool:
        """验证配置"""
        if not self.host_edit.text().strip():
            QMessageBox.warning(self, "输入错误", "请输入服务器地址")
            return False
        
        if not self.username_edit.text().strip():
            QMessageBox.warning(self, "输入错误", "请输入用户名")
            return False
        
        if not self.password_edit.text().strip():
            QMessageBox.warning(self, "输入错误", "请输入密码")
            return False
        
        return True
    
    def get_config(self) -> dict:
        """获取配置字典"""
        return {
            'name': self.name_edit.text().strip(),
            'host': self.host_edit.text().strip(),
            'port': self.port_spin.value(),
            'username': self.username_edit.text().strip(),
            'password': self.password_edit.text(),
            'remote_path': self.remote_path_edit.text().strip(),
            'enable_tls': self.tls_check.isChecked(),
            'passive_mode': self.passive_check.isChecked(),
            'timeout': self.timeout_spin.value(),
            'retry_count': self.retry_spin.value(),
        }
    
    def set_config(self, config: dict):
        """设置配置"""
        self.name_edit.setText(config.get('name', ''))
        self.host_edit.setText(config.get('host', ''))
        self.port_spin.setValue(config.get('port', 21))
        self.username_edit.setText(config.get('username', ''))
        self.password_edit.setText(config.get('password', ''))
        self.remote_path_edit.setText(config.get('remote_path', '/upload'))
        self.tls_check.setChecked(config.get('enable_tls', False))
        self.passive_check.setChecked(config.get('passive_mode', True))
        self.timeout_spin.setValue(config.get('timeout', 30))
        self.retry_spin.setValue(config.get('retry_count', 3))
    
    def clear_form(self):
        """清空表单"""
        self.name_edit.clear()
        self.host_edit.clear()
        self.port_spin.setValue(21)
        self.username_edit.clear()
        self.password_edit.clear()
        self.remote_path_edit.setText("/upload")
        self.tls_check.setChecked(False)
        self.passive_check.setChecked(True)
        self.timeout_spin.setValue(30)
        self.retry_spin.setValue(3)
        self.status_label.setText("请选择或新增客户端")
        self.status_label.setStyleSheet("color: #6B7280;")  # 灰色
    
    def update_client_status(self, name: str, status: dict):
        """更新客户端状态"""
        if name != self.current_client_name:
            return
        
        if status.get('connected', False):
            self.status_label.setText(f"✓ 已连接到 {status.get('host')}")
            self.status_label.setStyleSheet("color: #166534; font-weight: bold;")  # 绿色成功状态
        else:
            self.status_label.setText(f"未连接")
            self.status_label.setStyleSheet("color: #B91C1C; font-weight: bold;")  # 红色错误状态


class ProtocolSelectorWidget(QWidget):
    """
    协议选择器组件
    
    功能：
    - 选择上传协议（SMB / FTP Server / FTP Client / 混合模式）
    - 根据选择显示对应的配置面板
    """
    
    # 信号定义
    protocol_changed_signal = Signal(str)  # 协议切换信号
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 使用 Card 风格
        card = QFrame(self)
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        
        # 协议选择
        selector_layout = QHBoxLayout()
        
        label = QLabel("上传协议:")
        label.setProperty("class", "Title")
        selector_layout.addWidget(label)
        
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems([
            "SMB (网络共享)",
            "FTP 服务器模式",
            "FTP 客户端模式",
            "混合模式 (FTP Server + Client)"
        ])
        self.protocol_combo.currentIndexChanged.connect(self.on_protocol_changed)
        selector_layout.addWidget(self.protocol_combo)
        
        selector_layout.addStretch()
        
        card_layout.addLayout(selector_layout)
        
        # 说明文本（使用主题灰色）
        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color: #6B7280; padding: 8px; background: #F3F4F6; border-radius: 6px;")
        card_layout.addWidget(self.desc_label)
        
        layout.addWidget(card)
        
        # 更新说明
        self.update_description(0)
    
    def on_protocol_changed(self, index):
        """协议选择变化"""
        self.update_description(index)
        
        # 发射信号
        protocols = ['smb', 'ftp_server', 'ftp_client', 'both']
        self.protocol_changed_signal.emit(protocols[index])
    
    def update_description(self, index):
        """更新协议说明"""
        descriptions = [
            "📁 SMB (网络共享)：通过 Windows 网络共享上传文件，需要目标为共享文件夹。",
            "🖥️ FTP 服务器模式：本机作为 FTP 服务器，其他设备可连接上传文件。",
            "📤 FTP 客户端模式：本机作为 FTP 客户端，连接到远程 FTP 服务器上传文件。",
            "🔄 混合模式：同时运行 FTP 服务器和客户端，灵活应对不同场景。"
        ]
        self.desc_label.setText(descriptions[index])
    
    def get_current_protocol(self) -> str:
        """获取当前选择的协议"""
        protocols = ['smb', 'ftp_server', 'ftp_client', 'both']
        return protocols[self.protocol_combo.currentIndex()]
    
    def set_protocol(self, protocol: str):
        """设置协议"""
        protocol_map = {
            'smb': 0,
            'ftp_server': 1,
            'ftp_client': 2,
            'both': 3
        }
        index = protocol_map.get(protocol, 0)
        self.protocol_combo.setCurrentIndex(index)


# 测试代码
if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget
    
    app = QApplication(sys.argv)
    
    # 创建主窗口
    window = QMainWindow()
    window.setWindowTitle("FTP UI 组件测试 - v2.0 主题风格")
    window.setGeometry(100, 100, 1000, 750)
    
    # 应用与 pyqt_app.py 完全一致的主题样式
    window.setStyleSheet(
        """
        QWidget{font-family:'Segoe UI', 'Microsoft YaHei UI'; font-size:11pt; color:#1F2937; background:#E3F2FD;}
        QMainWindow{background:#E3F2FD;}
        QFrame#Card{background:#FFFFFF; border:2px solid #64B5F6; border-radius:10px; padding: 12px;}
        QLabel{color:#1F2937;}
        QLabel.Title{color:#1976D2; font-weight:700; font-size:14pt;}
        QPushButton{font-size:11pt;}
        QPushButton:disabled{background:#E5E7EB; color:#9CA3AF; border:1px solid #D1D5DB;}
        QPushButton.Primary{background:#1976D2; color:#FFFFFF; border:none; border-radius:8px; padding:8px 12px;}
        QPushButton.Primary:hover{background:#1E88E5;}
        QPushButton.Primary:disabled{background:#BDBDBD; color:#FFFFFF;}
        QPushButton.Secondary{background:#F1F5F9; color:#0F172A; border:1px solid #64B5F6; border-radius:8px; padding:6px 10px;}
        QPushButton.Secondary:hover{background:#E3F2FD;}
        QPushButton.Secondary:disabled{background:#E5E7EB; color:#9CA3AF;}
        QPushButton.Warning{background:#FEF3C7; color:#A16207; border:1px solid #FCD34D; border-radius:8px; padding:6px 10px;}
        QPushButton.Warning:hover{background:#FDE68A;}
        QPushButton.Warning:disabled{background:#E5E7EB; color:#9CA3AF;}
        QPushButton.Danger{background:#FEE2E2; color:#B91C1C; border:1px solid #FCA5A5; border-radius:8px; padding:6px 10px;}
        QPushButton.Danger:hover{background:#FECACA;}
        QPushButton.Danger:disabled{background:#E5E7EB; color:#9CA3AF;}
        QProgressBar{border:1px solid #64B5F6; border-radius:6px; background:#EEF2F5; text-align:center; color:#1F2937;}
        QProgressBar::chunk{border-radius:6px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4FACFE, stop:1 #00F2FE);} 
        QPlainTextEdit{background:#FFFFFF; border:1px solid #64B5F6; color:#1F2937; border-radius:4px;}
        QSpinBox{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:4px; padding:4px;}
        QSpinBox:disabled{background:#F3F4F6; color:#9CA3AF; border:1px solid #D1D5DB;}
        QLineEdit{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:4px; padding:4px;}
        QLineEdit:read-only{background:#F3F4F6; color:#6B7280; border:1px solid #D1D5DB;}
        QCheckBox{color:#1F2937; spacing:8px;}
        QCheckBox:disabled{color:#9CA3AF;}
        QCheckBox::indicator{width:22px; height:22px; background:#FFFFFF; border:2px solid #64B5F6; border-radius:4px;}
        QCheckBox::indicator:disabled{background:#F3F4F6; border:2px solid #D1D5DB;}
        QCheckBox::indicator:checked{background:qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1976D2, stop:1 #2196F3); border:2px solid #1976D2;}
        QCheckBox::indicator:checked:disabled{background:#E0E0E0; border:2px solid #D1D5DB;}
        QComboBox{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; border-radius:4px; padding:4px;}
        QComboBox:disabled{background:#F3F4F6; color:#9CA3AF; border:1px solid #D1D5DB;}
        QComboBox::drop-down{border:none;}
        QComboBox::down-arrow{image:none; border-left:4px solid transparent; border-right:4px solid transparent; border-top:6px solid #1976D2; margin-right:8px;}
        QComboBox::down-arrow:disabled{border-top-color:#9CA3AF;}
        QComboBox QAbstractItemView{background:#FFFFFF; color:#1F2937; border:1px solid #64B5F6; selection-background-color:#E3F2FD;}
        QGroupBox{color:#1976D2; font-weight:600; border:1px solid #64B5F6; border-radius:6px; margin-top:8px; padding-top:8px;}
        QGroupBox::title{subcontrol-origin:margin; left:10px; padding:0 5px;}
        QListWidget{background:#FFFFFF; border:1px solid #64B5F6; border-radius:4px;}
        QListWidget::item{padding:6px; border-radius:3px;}
        QListWidget::item:selected{background:#E3F2FD; color:#1976D2;}
        QListWidget::item:hover{background:#F1F5F9;}
        QTabWidget::pane{border:2px solid #64B5F6; border-radius:8px; background:#FFFFFF;}
        QTabBar::tab{background:#F1F5F9; border:1px solid #64B5F6; padding:8px 16px; border-top-left-radius:6px; border-top-right-radius:6px;}
        QTabBar::tab:selected{background:#FFFFFF; color:#1976D2; font-weight:600;}
        QTabBar::tab:hover{background:#E3F2FD;}
        """
    )
    
    # 创建标签页
    tab_widget = QTabWidget()
    tab_widget.setContentsMargins(8, 8, 8, 8)
    
    # 协议选择器
    protocol_selector = ProtocolSelectorWidget()
    tab_widget.addTab(protocol_selector, "协议选择")
    
    # FTP 服务器配置
    server_widget = FTPServerConfigWidget()
    tab_widget.addTab(server_widget, "FTP 服务器")
    
    # FTP 客户端配置
    client_widget = FTPClientConfigWidget()
    tab_widget.addTab(client_widget, "FTP 客户端")
    
    window.setCentralWidget(tab_widget)
    window.show()
    
    sys.exit(app.exec())
