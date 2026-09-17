# -*- coding: utf-8 -*-
"""
文件名：src/main.py
文件作用：桌面应用的唯一组合根与启动入口。
主要功能：检查依赖、延迟加载 Qt、建立单实例保护、装配 MVC，并进入事件循环。
模块关系：创建并连接 Repository、Service、Model、Controller 与 MainWindow；不承载具体业务规则。
阅读重点：按 main() 的编号步骤理解启动顺序；退出顺序由 LifecycleController 负责。

图片异步上传工具 - 主程序入口

v3.1.1 - 断点续传、中英文切换、配置加载修复
- 使用新的模块化结构
"""
import sys
import os
import time
import json
from pathlib import Path
from typing import Any, List, Tuple

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 切换工作目录到项目根目录（确保配置文件能被正确找到）
os.chdir(project_root)


def check_dependencies() -> Tuple[bool, List[str], List[str]]:
    """检查启动依赖，并区分阻断启动和仅影响扩展功能的缺失项。

    用途：在导入图形组件前给出可执行的依赖安装提示。
    输入：无；依赖清单由函数内部的当前版本定义。
    输出：【全部必需依赖可用、缺失必需项、缺失可选项】三元组。
    关键步骤：先验证 PySide6，再逐项探测 FTP/FTPS 扩展依赖。
    风险点：可选依赖缺失不能阻止基础上传启动，调用方须分别处理两类结果。
    """
    missing_required: List[str] = []
    missing_optional: List[str] = []

    # 可选依赖
    optional_packages = [
        ('pyftpdlib', 'pip install pyftpdlib'),  # FTP 服务器功能
        ('OpenSSL', 'pip install pyOpenSSL'),  # FTPS 服务器 TLS 支持
    ]

    # 检查必需依赖
    try:
        import PySide6  # noqa: F401
    except ImportError:
        missing_required.append('PySide6: pip install PySide6')

    # 检查可选依赖
    for pkg_name, install_cmd in optional_packages:
        try:
            __import__(pkg_name)
        except ImportError:
            missing_optional.append(f'{pkg_name}: {install_cmd}')

    return len(missing_required) == 0, missing_required, missing_optional


def show_dependency_warning(missing_required: List[str], missing_optional: List[str]) -> None:
    """在控制台输出缺失依赖和对应安装建议。"""
    if missing_required:
        print("\n" + "=" * 60)
        print("缺少必需依赖，程序无法启动：")
        for dep in missing_required:
            print(f"   - {dep}")
        print("=" * 60 + "\n")

    if missing_optional:
        print("\n" + "-" * 60)
        print("缺少可选依赖，部分功能不可用：")
        for dep in missing_optional:
            print(f"   - {dep}")
        print("-" * 60 + "\n")


def run_packaged_release_probe(
    app_dir: Path,
) -> tuple[bool, dict[str, Any]]:
    """验证打包版在现场会使用的资源和可写存储。

    用途：在发布环境中覆盖资源定位、状态存储、本地 FTPS 和协作式退出的组合路径。
    输入：打包程序可写的应用目录【app_dir】。
    输出：【是否全部通过、各检查项和错误说明】。
    关键步骤：创建临时状态，验证资源/FTPS，再清理探针文件并写出报告。
    风险点：该探针会在应用目录创建临时文件，必须只在
    【IMAGE_UPLOAD_SMOKE_TEST=1】的发布验证环境调用，而非正常业务启动。
    """
    from src.core import ResumeManager, get_resource_path
    from src.core.file_identity import FileIdentity
    from src.core.i18n import LANG_EN_US, LANG_ZH_CN, TRANSLATIONS
    from src.protocols.ftp import FTPServerManager, TLS_FTPHandler
    from src.repositories import PendingArchiveRepository

    checks: dict[str, bool] = {}
    errors: list[str] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        """把单项检查结果写入汇总；失败时同时保留可读原因。"""
        checks[name] = bool(passed)
        if not passed:
            errors.append(f"{name}: {detail or 'failed'}")

    try:
        import OpenSSL  # noqa: F401
        import PySide6  # noqa: F401
        import pyftpdlib  # noqa: F401
        import send2trash  # noqa: F401
        record("packaged_dependencies", True)
    except Exception as exc:
        record("packaged_dependencies", False, f"{type(exc).__name__}: {exc}")

    record("ftps_tls_handler", TLS_FTPHandler is not None, "TLS_FTPHandler unavailable")
    record(
        "assets",
        all(get_resource_path(name).is_file() for name in ("assets/logo.ico", "assets/logo.png")),
        "logo resources missing",
    )
    translations = TRANSLATIONS.get("app_title", {})
    record(
        "language_resources",
        bool(translations.get(LANG_ZH_CN) and translations.get(LANG_EN_US)),
        "Chinese or English app title missing",
    )
    frozen = bool(getattr(sys, "frozen", False))
    record(
        "version_alignment",
        not frozen or f"v{get_app_version()}" in Path(sys.executable).stem,
        f"executable name does not contain v{get_app_version()}",
    )

    try:
        ResumeManager(app_dir)
        record("resume_store", (app_dir / "resume_data").is_dir(), "resume_data not created")
    except Exception as exc:
        record("resume_store", False, f"{type(exc).__name__}: {exc}")

    try:
        archive_repository = PendingArchiveRepository(app_dir)
        probe_source_path = app_dir / ".release-smoke-source"
        try:
            probe_source_path.write_bytes(b"release-smoke")
            probe_source = str(probe_source_path)
            probe_target = str(app_dir / ".release-smoke-target")
            archive_ok = archive_repository.add(
                probe_source,
                probe_target,
                "delete",
                FileIdentity.capture(probe_source_path),
            )
            archive_ok = archive_ok and archive_repository.remove(probe_source)
        finally:
            probe_source_path.unlink(missing_ok=True)
        record(
            "pending_archive_journal",
            archive_ok and archive_repository.path.is_file(),
            archive_repository.last_error or "pending archive journal not writable",
        )
    except Exception as exc:
        record("pending_archive_journal", False, f"{type(exc).__name__}: {exc}")

    ftps_server = None
    ftps_client = None
    try:
        import datetime
        import io
        import ipaddress
        import socket
        import ssl
        from ftplib import FTP_TLS

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        ftps_dir = app_dir / "data" / "release_smoke_ftps"
        ftps_dir.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_file = ftps_dir / "server-cert.pem"
        key_file = ftps_dir / "server-key.pem"
        cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            ftps_port = int(port_probe.getsockname()[1])
        ftps_server = FTPServerManager(
            {
                "host": "127.0.0.1",
                "port": ftps_port,
                "username": "release_smoke",
                "password": "release_smoke",
                "shared_folder": str(ftps_dir),
                "enable_tls": True,
                "cert_file": str(cert_file),
                "key_file": str(key_file),
                "enable_passive": False,
            }
        )
        if not ftps_server.start():
            raise RuntimeError("packaged FTPS server did not start")
        ftps_client = FTP_TLS(context=ssl._create_unverified_context())
        ftps_client.connect("127.0.0.1", ftps_port, timeout=10)
        ftps_client.login("release_smoke", "release_smoke")
        ftps_client.prot_p()
        ftps_client.storbinary("STOR encrypted-probe.bin", io.BytesIO(b"encrypted-probe"))
        ftps_client.quit()
        ftps_client = None
        record(
            "ftps_control_and_data_handshake",
            (ftps_dir / "encrypted-probe.bin").read_bytes() == b"encrypted-probe",
            "encrypted upload content mismatch",
        )
    except Exception as exc:
        record(
            "ftps_control_and_data_handshake",
            False,
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        if ftps_client is not None:
            try:
                ftps_client.close()
            except Exception:
                pass
        if ftps_server is not None:
            try:
                ftps_server.stop()
                server_thread = getattr(ftps_server, "server_thread", None)
                if server_thread is not None:
                    server_thread.join(timeout=5)
                    record(
                        "ftps_thread_released",
                        not server_thread.is_alive(),
                        "FTPS server thread still alive",
                    )
            except Exception as exc:
                record("ftps_thread_released", False, f"{type(exc).__name__}: {exc}")

    report: dict[str, Any] = {
        "version": get_app_version(),
        "title": get_app_title(),
        "executable": Path(sys.executable).name,
        "frozen": frozen,
        "checks": checks,
        "errors": errors,
    }
    report_path = app_dir / "release_smoke.json"
    try:
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        record("smoke_report", True)
    except Exception as exc:
        record("smoke_report", False, f"{type(exc).__name__}: {exc}")
        report["errors"] = errors
        report["checks"] = checks
    return not errors, report


# 延迟导入核心模块：依赖检查失败时可以给出明确提示，而非直接崩溃。
from src.core import get_app_dir, get_app_version, get_app_title
def wakeup_existing_instance(
    server_name: str,
    attempts: int = 5,
    wait_ms: int = 200,
    connect_ms: int = 300
) -> bool:
    """检查并尝试唤醒已有实例，防止两个进程并行处理同一批文件。

    用途：作为单实例启动流程中的第一层进程间通信检查。
    输入：本地服务名、重试次数、重试等待时长和单次连接超时。
    输出：成功唤醒已有实例返回【True】，未发现可连接实例返回【False】。
    关键步骤：在限定次数内连接本地服务，连接成功后发送【WAKEUP】消息。
    风险点：此函数只能降低竞态概率；调用方仍须结合共享内存锁完成第二层保护。
    """
    for _ in range(attempts):
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if socket.waitForConnected(connect_ms):
            socket.write(b"WAKEUP")
            socket.flush()
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            return True
        time.sleep(wait_ms / 1000.0)

    return False


def main():
    """组装应用依赖、建立单实例保护并进入 Qt 事件循环。

    用途：作为桌面程序的唯一组合根，控制启动前检查和资源生命周期。
    输入：无；从命令行、环境变量和应用目录读取运行环境。
    输出：进程退出码。
    关键步骤：检查依赖、建立单实例锁、构造 MVC 依赖、显示主窗口并启动事件循环。
    风险点：后台 Worker 必须经生命周期控制器退出，不能在此处直接强制终止线程。
    """
    # 第一步：在导入 Qt 前检查依赖，便于在控制台输出可执行的安装建议。
    all_ok, missing_required, missing_optional = check_dependencies()
    if not all_ok:
        show_dependency_warning(missing_required, missing_optional)
        print("程序因缺少必需依赖而无法启动。")
        return 1

    if missing_optional:
        show_dependency_warning([], missing_optional)

    # 第二步：依赖确认后才导入 Qt，避免缺包时产生难以理解的 ImportError。
    global QtCore, QtWidgets, QLocalServer, QLocalSocket  # type: ignore
    try:
        from PySide6 import QtCore, QtWidgets  # type: ignore
        from PySide6.QtNetwork import QLocalServer, QLocalSocket  # type: ignore
    except ImportError as exc:
        if getattr(sys, "frozen", False):
            try:
                (Path(sys.executable).parent / "startup-error.log").write_text(
                    f"PySide6 import failed: {exc}\n", encoding="utf-8"
                )
            except OSError:
                pass
        show_dependency_warning(["PySide6: pip install PySide6"], [])
        return 1

    # 第三步：依赖检查成功后再组装 MVC 组件，避免提前加载平台相关实现。
    from src.controllers import (
        AuthController,
        CleanupController,
        FTPController,
        LifecycleController,
        RuntimeController,
        SettingsController,
        UploadController,
    )
    from src.models import AuthModel, UploadRuntimeState
    from src.repositories import (
        CleanupAuditRepository,
        ConfigRepository,
        DailyLogRepository,
        FTPEventLogRepository,
        WindowsStartupRepository,
    )
    from src.services import (
        AuthService,
        CleanupService,
        FTPService,
        RuntimeService,
        UploadService,
    )
    from src.ui import MainWindow  # type: ignore

    app = QtWidgets.QApplication(sys.argv)

    # 第四步：设置应用元数据，供系统任务栏、设置和日志展示。
    app.setApplicationName("图片异步上传工具")
    app.setApplicationVersion(get_app_version())
    app.setOrganizationName("RelMoTong")

    # 第五步：先检查本机是否已有实例，避免两个进程同时处理同一批源文件。
    server_name = "ImageUploadTool_SingleInstance_Server"
    if wakeup_existing_instance(server_name, attempts=1, wait_ms=0, connect_ms=200):
        # 已有实例运行且已发送唤醒消息，本进程无需继续创建第二套 Worker。
        return 0

    # 第六步：使用共享内存作为辅助锁，缩小 LocalServer 建立前的极端竞态窗口。
    shared_mem = QtCore.QSharedMemory("ImageUploadTool_SingleInstance")
    if not shared_mem.create(1):
        if wakeup_existing_instance(server_name):
            return 0
        # 极少情况：LocalServer 尚未响应但共享内存已存在，仍拒绝重复启动。
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)  # type: ignore[arg-type]
        msg.setWindowTitle("程序启动异常")
        msg.setText("检测到程序可能未正常退出")
        msg.setInformativeText("建议：\n1. 检查任务管理器是否有残留进程\n2. 重启计算机后重试")
        msg.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)  # type: ignore[arg-type]
        msg.exec() if hasattr(msg, 'exec') else msg.exec_()
        return 1

    # 第七步：所有 Controller、Service、Repository 都在组合根创建，再注入主窗口。
    app_dir = get_app_dir()
    settings_repository = ConfigRepository(app_dir / 'config.json')
    ftp_event_repository = FTPEventLogRepository(app_dir)
    cleanup_audit_repository = CleanupAuditRepository(app_dir)
    daily_log_repository = DailyLogRepository(app_dir)
    startup_repository = WindowsStartupRepository()

    auth_service = AuthService()
    ftp_service = FTPService()
    upload_service = UploadService()
    cleanup_service = CleanupService(
        cleanup_audit_repository,
    )
    runtime_service = RuntimeService(
        app_dir,
        daily_log_repository,
        startup_repository,
        executable=sys.executable,
        main_script=Path(__file__).resolve(),
        frozen=bool(getattr(sys, "frozen", False)),
        app_version=get_app_version(),
    )

    auth_model = AuthModel()
    upload_state = UploadRuntimeState()

    settings_controller = SettingsController(settings_repository)
    auth_controller = AuthController(
        auth_service,
        settings_controller,
        model=auth_model,
    )
    ftp_controller = FTPController(ftp_service, ftp_event_repository)
    upload_controller = UploadController(upload_service, upload_state)
    cleanup_controller = CleanupController(cleanup_service)
    runtime_controller = RuntimeController(runtime_service)
    lifecycle_controller = LifecycleController(
        cleanup_controller,
        upload_controller,
        ftp_controller,
        runtime_controller,
    )
    window = MainWindow(
        settings_controller,
        auth_controller,
        ftp_controller,
        upload_controller,
        cleanup_controller,
        runtime_controller,
        lifecycle_controller,
    )
    window._setup_single_instance_server()

    release_smoke = os.environ.get("IMAGE_UPLOAD_SMOKE_TEST") == "1"
    if release_smoke:
        probe_ok, _probe_report = run_packaged_release_probe(app_dir)
        if not probe_ok:
            return 2

    window.show()

    # 打包版冒烟测试：不做界面自动化，而是运行真实组合根、Qt 事件循环和协作式退出流程。
    if release_smoke:
        try:
            smoke_duration_ms = int(
                os.environ.get("IMAGE_UPLOAD_SMOKE_DURATION_MS", "1000")
            )
        except ValueError:
            smoke_duration_ms = 1000
        # 允许真实 72 小时长稳，同时限制非法负值/溢出值，避免发布自动化无限运行。
        smoke_duration_ms = min(max(1000, smoke_duration_ms), 72 * 60 * 60 * 1000)
        QtCore.QTimer.singleShot(smoke_duration_ms, window.app_exit_requested.emit)

    # 最后进入事件循环；之后的用户操作、定时器和后台信号均由 Qt 分发。
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
