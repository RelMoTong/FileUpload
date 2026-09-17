"""手动清理与自动清理的控制器。
文件名：src/controllers/cleanup_controller.py
文件作用：控制器层的“cleanup_controller”协调模块。
主要功能：接收界面意图、协调模型与服务，并保持既有 MVC 分层边界。
模块关系：由 src.main 组装；仅通过抽象约定与服务协作，不直接承担界面或底层 IO。
阅读重点：先读公开 Gateway 方法、状态转换与异步回调，再追踪注入的服务。


本模块不直接读写文件，也不直接操作界面。它负责把界面的操作转换为服务层请求，
并管理自动清理任务的生命周期。这样做可以让 UI、后台线程和文件系统规则各自独立，
便于初学者沿着“界面 → 控制器 → 服务”的顺序阅读。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Any, Callable, Optional, Protocol, Tuple

from src.models import AutoCleanupRequest, AutoCleanupResult, CleanupCommandResult, CleanupDeleteRequest, CleanupScanRequest, CleanupValidationResult


class CleanupBusinessService(Protocol):
    """清理服务对控制器公开的最小能力集合。

    用途：让控制器只依赖行为约定，而不依赖 ``CleanupService`` 的具体实现。
    输入：各个方法接收已经由界面收集完成的清理请求。
    输出：同步返回校验/命令结果，异步过程通过回调事件返回。
    关键步骤：协议列出扫描、删除、自动清理以及退出时所需的方法。
    风险点：不要在控制器中绕过这些方法直接触碰 Worker，否则线程归属会变得不清晰。
    """
    trash_available: bool
    is_scanning: bool
    is_deleting: bool
    def validate_scan_request(self, request: CleanupScanRequest) -> CleanupValidationResult:
        """协议占位：声明“validate_scan_request”的最小调用约定，由实现方提供既有行为。"""
        ...
    def start_scan(self, request: CleanupScanRequest, callback: Callable) -> CleanupCommandResult:
        """协议占位：声明“start_scan”的最小调用约定，由实现方提供既有行为。"""
        ...
    def cancel_scan(self) -> None:
        """协议占位：声明“cancel_scan”的最小调用约定，由实现方提供既有行为。"""
        ...
    def cancel(self) -> None:
        """协议占位：声明“cancel”的最小调用约定，由实现方提供既有行为。"""
        ...
    @property
    def has_running_workers(self) -> bool:
        """协议占位：声明“has_running_workers”的最小调用约定，由实现方提供既有行为。"""
        ...
    def start_delete(self, request: CleanupDeleteRequest, callback: Callable) -> CleanupCommandResult:
        """协议占位：声明“start_delete”的最小调用约定，由实现方提供既有行为。"""
        ...
    def shutdown_manual(self) -> None:
        """协议占位：声明“shutdown_manual”的最小调用约定，由实现方提供既有行为。"""
        ...
    def validate_auto_request(self, request: AutoCleanupRequest) -> CleanupValidationResult:
        """协议占位：声明“validate_auto_request”的最小调用约定，由实现方提供既有行为。"""
        ...
    def validate_cleanup_folder_group(self, folders: Any) -> Tuple[bool, str, Any]:
        """协议占位：声明“validate_cleanup_folder_group”的最小调用约定，由实现方提供既有行为。"""
        ...
    def should_trigger(self, request: AutoCleanupRequest) -> Tuple[bool, str]:
        """协议占位：声明“should_trigger”的最小调用约定，由实现方提供既有行为。"""
        ...
    def record_blocked(self, request: AutoCleanupRequest, status: str, error: str) -> None:
        """协议占位：声明“record_blocked”的最小调用约定，由实现方提供既有行为。"""
        ...
    def run_auto_cleanup(self, request: AutoCleanupRequest, cancel_event: Any, log: Callable[[str], None], delete_mode_provider: Optional[Callable[[], bool]] = None) -> AutoCleanupResult:
        """协议占位：声明“run_auto_cleanup”的最小调用约定，由实现方提供既有行为。"""
        ...


class CleanupController:
    """协调手动清理事件与自动清理后台任务。

    用途：作为 UI 和 ``CleanupService`` 之间唯一的业务入口。
    输入：用户发起的扫描、删除、自动清理配置，以及服务层的异步事件。
    输出：向 UI 转发结构化事件，或启动一个受互斥保护的自动清理任务。
    关键步骤：手动操作交给服务层；自动操作交给单线程执行器；关闭时只请求取消。
    风险点：网络盘 I/O 可能长时间不返回，任何等待 Worker 结束的动作都不能放在 UI 线程。
    """

    def __init__(self, service: CleanupBusinessService, executor: Any = None) -> None:
        """创建控制器并初始化手动、自动两类任务的运行状态。

        用途：保存服务实例，并准备自动清理需要的锁、取消事件和执行器。
        输入：服务层实例；可选的执行器主要用于测试或由组合根统一注入。
        输出：一个尚未运行任何清理任务的控制器。
        关键步骤：手动和自动监听器分离；自动状态使用锁保护；删除模式单独加锁保存。
        风险点：自动任务只能有一个运行实例，不能因为多次磁盘告警而并发删除同一批文件。
        """
        self._service = service
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="AutoCleanup")
        self._manual_listener: Optional[Callable[[dict], None]] = None
        self._auto_listener: Optional[Callable[[dict], None]] = None
        self._auto_lock = threading.Lock()
        self._auto_cancel = threading.Event()
        self._auto_running = False
        self._auto_config_lock = threading.Lock()
        self._live_auto_use_trash: Optional[bool] = None
        self._closing = False
        self._discard_manual_events_until_finished = False

    @property
    def trash_available(self) -> bool:
        """返回服务层探测到的回收站可用状态。"""
        return self._service.trash_available

    @property
    def is_scanning(self) -> bool:
        """返回手动扫描是否仍由服务层持有。"""
        return self._service.is_scanning

    @property
    def is_deleting(self) -> bool:
        """返回手动删除是否仍由服务层持有。"""
        return self._service.is_deleting

    @property
    def is_auto_running(self) -> bool:
        """在线程锁保护下读取自动清理运行标记。"""
        with self._auto_lock:
            return self._auto_running

    def set_manual_listener(self, listener: Optional[Callable[[dict], None]]) -> None:
        """登记或解除手动清理窗口的事件接收函数。"""
        self._manual_listener = listener

    def set_auto_listener(self, listener: Callable[[dict], None]) -> None:
        """登记主窗口用于显示自动清理日志和完成状态的接收函数。"""
        self._auto_listener = listener

    def validate_scan_request(
        self, request: CleanupScanRequest
    ) -> CleanupValidationResult:
        """转交扫描前的路径、格式和权限校验，不在控制器重复规则。"""
        return self._service.validate_scan_request(request)

    def start_scan(self, request: CleanupScanRequest) -> CleanupCommandResult:
        """启动手动扫描，并把服务事件统一回流到本控制器。"""
        return self._service.start_scan(request, self._handle_manual_event)

    def cancel_scan(self) -> None:
        """非阻塞地通知服务层在下一个可取消点停止扫描。"""
        self._service.cancel_scan()

    def start_delete(self, request: CleanupDeleteRequest) -> CleanupCommandResult:
        """启动手动删除，并复用与扫描相同的事件转发通道。"""
        return self._service.start_delete(request, self._handle_manual_event)

    def close_manual(self) -> None:
        """关闭手动清理窗口时解除监听并请求取消，绝不在 GUI 线程等待。

        用途：让用户可在网络盘扫描迟迟未返回时立即关闭窗口。
        输入：无；当前扫描状态由服务层保存。
        输出：窗口不再接收手动事件，后台 Worker 在安全取消点自行结束。
        关键步骤：记录是否需丢弃残余扫描事件、发出取消请求、最后解除监听。
        风险点：不能调用 ``shutdown_manual``，它会等待 QThread，SMB I/O 卡住时会冻结界面。
        """
        # 网络盘 I/O 若尚未返回，Worker 会在可取消点自行收尾；服务对象继续持有
        # 线程引用，避免 QThread 被提前销毁。这里仅标记并解除 UI 监听。
        self._discard_manual_events_until_finished = self.is_scanning
        self._service.cancel()
        self._manual_listener = None

    def validate_auto_request(self, request: AutoCleanupRequest) -> CleanupValidationResult:
        """作用：执行“validate_auto_request”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.validate_auto_request(request)
    def validate_folder_group(self, folders: Any) -> Tuple[bool, str, Any]:
        """作用：执行“validate_folder_group”的既有协调或状态处理职责。

        参数：沿用当前函数签名及已有类型、单位和状态约定。
        返回结果：沿用当前实现的返回值、回调或异常语义。
        执行流程：按现有代码顺序完成校验、调用、状态更新与结果交付。
        风险或注意事项：本说明不改变 MVC 分层、线程边界或公开接口；调用方须保持原有调用顺序。
        """
        return self._service.validate_cleanup_folder_group(folders)

    def configure_auto_cleanup(self, request: AutoCleanupRequest) -> bool:
        """发布当前自动清理策略，但不在保存配置时扫描文件。

        用途：让设置页面保存后的删除模式立即可供将来的自动任务读取。
        输入：已经通过界面收集的自动清理请求。
        输出：配置是否具备启用条件；不代表本次已经执行清理。
        关键步骤：更新可动态读取的回收站模式，再检查程序是否正在关闭及基本配置。
        风险点：保存设置时扫描目录会阻塞 UI，也会使候选结果在真正触发时过期。
        """
        self._update_live_delete_mode(request)
        return not self._closing and request.enabled and bool(request.folders)

    def maybe_trigger_auto_cleanup(self, request: AutoCleanupRequest, reason: str = "") -> bool:
        """在磁盘告警后判断是否应当提交一次自动清理。

        用途：把“是否满足阈值”和“是否已有任务运行”两道判断集中在这里。
        输入：当前自动清理请求，以及可选的触发原因文本。
        输出：实际提交后台任务时返回 ``True``；被禁用、无须清理或失败时返回 ``False``。
        关键步骤：刷新动态删除模式、校验阈值/路径、避免重复提交、记录触发日志。
        风险点：不能把路径或跨盘校验错误静默忽略，否则会造成用户以为自动清理已经执行。
        """
        self._update_live_delete_mode(request)
        if not request.enabled or not request.folders or self._closing: return False
        should_run, error = self._service.should_trigger(request)
        if error:
            status = "跨盘配置无效" if "同一磁盘" in error else "路径不可用"
            self._notify_auto("log", message=f"⚠️ {error}")
            self._service.record_blocked(request, status, error)
            return False
        if not should_run or not self.submit_auto_cleanup(request): return False
        self._notify_auto("log", message=f"⚠️ {reason or '磁盘空间不足'}，触发自动清理")
        return True

    def submit_auto_cleanup(self, request: AutoCleanupRequest) -> bool:
        """向单线程执行器提交自动清理，保证同一时刻最多运行一个任务。

        用途：将可能很慢的文件扫描与删除移出 UI 线程。
        输入：已经决定应执行的自动清理请求。
        输出：任务成功排队时返回 ``True``；关闭中、已有任务或提交异常时返回 ``False``。
        关键步骤：在锁内检查并设置运行标记；提交失败时必须回滚该标记并记录审计错误。
        风险点：若不加锁，两个并发磁盘告警可能同时删除，破坏“最旧优先”的预期。
        """
        if self._closing: return False
        with self._auto_lock:
            if self._auto_running: return False
            self._auto_running = True
            self._auto_cancel.clear()
        try:
            self._executor.submit(self._run_auto_cleanup, request)
            return True
        except Exception as exc:
            with self._auto_lock: self._auto_running = False
            error = f"自动清理任务提交失败: {type(exc).__name__}: {exc}"
            self._notify_auto("log", message=f"❌ {error}")
            self._service.record_blocked(request, "任务异常", error)
            return False

    def cancel_auto_cleanup(self) -> None:
        """设置自动任务的协作取消标记；不会强制杀死正在执行的系统 I/O。"""
        self._auto_cancel.set()

    def cancel(self) -> None:
        """同时请求取消自动任务和服务层持有的手动 Worker。"""
        self._auto_cancel.set()
        self._service.cancel()

    @property
    def has_running_workers(self) -> bool:
        """汇总手动 QThread 与自动执行器任务的运行状态。"""
        return self._service.has_running_workers or self.is_auto_running

    def shutdown(self) -> None:
        """应用整体退出时取消任务并释放执行器。

        用途：供应用生命周期控制器在真正退出进程前调用。
        输入：无。
        输出：停止接受新自动任务，并请求现有手动/自动任务停止。
        关键步骤：先设置关闭标记，再取消任务，最后以非阻塞方式关闭自动执行器。
        风险点：这是应用退出路径，服务层会作有限等待；普通对话框关闭绝不能使用此方法。
        """
        self._closing = True
        self.cancel()
        self._service.shutdown_manual()
        try: self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError: self._executor.shutdown(wait=False)

    def _run_auto_cleanup(self, request: AutoCleanupRequest) -> None:
        """在执行器线程中运行自动清理，并确保结束后释放运行标记。"""
        try:
            result = self._service.run_auto_cleanup(request, self._auto_cancel, lambda message: self._notify_auto("log", message=message), lambda: self._current_delete_mode(request.use_trash))
            self._notify_auto("auto_finished", result=result)
        finally:
            with self._auto_lock: self._auto_running = False

    def _update_live_delete_mode(self, request: AutoCleanupRequest) -> None:
        """将最新删除模式写入锁保护字段，供正在运行的任务在删除前复核。"""
        with self._auto_config_lock:
            self._live_auto_use_trash = bool(request.use_trash)

    def _current_delete_mode(self, fallback: bool) -> bool:
        """读取最新删除模式；尚未保存时使用本次请求中的回退值。"""
        with self._auto_config_lock:
            return bool(
                fallback if self._live_auto_use_trash is None else self._live_auto_use_trash
            )

    def _handle_manual_event(self, kind: str, payload: dict) -> None:
        """把服务层事件转交给当前手动窗口，并隔离已关闭窗口的残余扫描事件。

        用途：确保同一控制器复用后，新窗口不会收到旧网络盘扫描的迟到结果。
        输入：服务层给出的事件名称和字段字典。
        输出：当前监听器收到一个包含 ``type`` 的统一事件；或者事件被安全丢弃。
        关键步骤：等待旧扫描的结束事件作为隔离边界，再恢复新事件的正常投递。
        风险点：若直接转发，重新打开窗口后可能显示上一轮扫描的文件，导致删除对象错误。
        """
        # 关闭旧对话框后，不能把其残余扫描事件错误地投递给随后打开的新对话框。
        if self._discard_manual_events_until_finished:
            if kind == "scan_finished":
                self._discard_manual_events_until_finished = False
            return
        if self._manual_listener is not None:
            self._manual_listener({"type": kind, **payload})

    def _notify_auto(self, kind: str, **payload: Any) -> None:
        """向主窗口发送自动清理的日志或完成事件。"""
        if self._auto_listener is not None:
            self._auto_listener({"type": kind, **payload})
