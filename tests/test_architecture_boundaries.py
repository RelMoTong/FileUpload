"""Architecture dependency guards for the incremental MVC migration."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Iterator, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"


# These are pre-MVC dependencies recorded during phase 0. Entries may be
# removed as responsibilities move out of MainWindow, but no new entry should
# be added. The allowlist must be empty when phase 8 is complete.
LEGACY_UI_IMPORTS: set[tuple[str, str]] = set()

UI_FORBIDDEN_PREFIXES = (
    "src.config",
    "src.controllers",
    "src.protocols",
    "src.repositories",
    "src.services",
    "src.workers",
)

MODEL_FORBIDDEN_PREFIXES = (
    "PyQt5",
    "PySide6",
    "src.controllers",
    "src.ui",
)

CONTROLLER_FORBIDDEN_PREFIXES = (
    "ftplib",
    "os",
    "shutil",
    "socket",
    "sqlite3",
    "subprocess",
    "tempfile",
    "winreg",
    "src.protocols",
    "src.repositories",
    "src.services",
    "src.ui",
    "src.workers",
)

CONTROLLER_FORBIDDEN_IO_CALLS = {
    "open",
    "Path.open",
    "Path.mkdir",
    "Path.read_text",
    "Path.write_text",
    "shutil.copy",
    "shutil.copy2",
    "shutil.move",
    "shutil.rmtree",
}

COMPOSITION_ROOT_COMPONENTS = {
    "AuthController",
    "AuthService",
    "CleanupAuditRepository",
    "CleanupController",
    "CleanupService",
    "ConfigRepository",
    "DailyLogRepository",
    "FTPController",
    "FTPEventLogRepository",
    "FTPService",
    "LifecycleController",
    "MainWindow",
    "RuntimeController",
    "RuntimeService",
    "SettingsController",
    "UploadController",
    "UploadService",
    "WindowsStartupRepository",
}

COMPOSITION_ROOT_MODELS = {"AuthModel", "UploadRuntimeState"}

UI_FORBIDDEN_FILESYSTEM_CALLS = {
    "os.walk",
    "os.remove",
    "os.unlink",
    "os.stat",
    "os.scandir",
    "shutil.rmtree",
    "tempfile.mkstemp",
    "send_to_trash",
}

MAIN_WINDOW_FORBIDDEN_INFRASTRUCTURE_IMPORTS = {
    "ctypes",
    "ftplib",
    "json",
    "os",
    "shutil",
    "socket",
    "tempfile",
    "winreg",
}

MAIN_WINDOW_FORBIDDEN_IO_CALLS = {
    "open",
    "Path.open",
    "Path.mkdir",
    "Path.read_text",
    "Path.write_text",
}


def _python_files(folder: Path) -> Iterable[Path]:
    if not folder.exists():
        return ()
    return folder.rglob("*.py")


def _imports(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _matches_prefix(module: str, prefixes: Tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    target: ast.expr = node.func
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts))


def _violations(folder: Path, prefixes: Tuple[str, ...]) -> set[tuple[str, str]]:
    return {
        (_relative(path), module)
        for path in _python_files(folder)
        for module in _imports(path)
        if _matches_prefix(module, prefixes)
    }


def test_ui_does_not_add_new_cross_layer_dependencies() -> None:
    violations = _violations(SRC_ROOT / "ui", UI_FORBIDDEN_PREFIXES)
    unexpected = violations - LEGACY_UI_IMPORTS
    assert not unexpected, f"New UI cross-layer imports: {sorted(unexpected)}"


def test_model_layer_is_independent_from_qt_views_and_controllers() -> None:
    violations = _violations(SRC_ROOT / "models", MODEL_FORBIDDEN_PREFIXES)
    assert not violations, f"Model layer dependency violations: {sorted(violations)}"


def test_controllers_do_not_import_concrete_ui_or_infrastructure() -> None:
    violations = _violations(SRC_ROOT / "controllers", CONTROLLER_FORBIDDEN_PREFIXES)
    assert not violations, f"Controller dependency violations: {sorted(violations)}"

    direct_io: set[tuple[str, str]] = set()
    for path in _python_files(SRC_ROOT / "controllers"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = _call_name(node)
                if call_name in CONTROLLER_FORBIDDEN_IO_CALLS:
                    direct_io.add((_relative(path), call_name))
    assert not direct_io, f"Controller direct I/O calls: {sorted(direct_io)}"


def test_main_is_the_only_production_mvc_composition_root() -> None:
    main_path = SRC_ROOT / "main.py"
    main_tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    main_calls = {
        _call_name(node)
        for node in ast.walk(main_tree)
        if isinstance(node, ast.Call)
    }
    expected = COMPOSITION_ROOT_COMPONENTS | COMPOSITION_ROOT_MODELS
    missing = expected - main_calls
    assert not missing, f"Composition root does not create: {sorted(missing)}"

    misplaced: set[tuple[str, str]] = set()
    for path in _python_files(SRC_ROOT):
        if path == main_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = _call_name(node)
                if call_name in COMPOSITION_ROOT_COMPONENTS:
                    misplaced.add((_relative(path), call_name))
    assert not misplaced, f"MVC components constructed outside main.py: {sorted(misplaced)}"


def test_main_defers_concrete_mvc_imports_until_after_dependency_check() -> None:
    main_path = SRC_ROOT / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    eager_modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            eager_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            eager_modules.add(node.module)
    forbidden = {
        "src.controllers",
        "src.models",
        "src.repositories",
        "src.services",
        "src.ui",
    }
    violations = eager_modules & forbidden
    assert not violations, f"MVC components imported before dependency check: {sorted(violations)}"


def test_cleanup_views_do_not_scan_or_delete_files_directly() -> None:
    violations: set[tuple[str, str]] = set()
    for path in (
        SRC_ROOT / "ui" / "main_window.py",
        SRC_ROOT / "ui" / "dialogs" / "disk_cleanup_dialog.py",
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = _call_name(node)
                if call_name in UI_FORBIDDEN_FILESYSTEM_CALLS:
                    violations.add((_relative(path), call_name))
    assert not violations, f"Cleanup filesystem work leaked into views: {sorted(violations)}"


def test_main_window_has_no_direct_infrastructure_io() -> None:
    path = SRC_ROOT / "ui" / "main_window.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = set(_imports(path))
    forbidden_imports = imports & MAIN_WINDOW_FORBIDDEN_INFRASTRUCTURE_IMPORTS
    calls = {
        _call_name(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    forbidden_calls = calls & MAIN_WINDOW_FORBIDDEN_IO_CALLS
    assert not forbidden_imports, f"MainWindow infrastructure imports: {sorted(forbidden_imports)}"
    assert not forbidden_calls, f"MainWindow direct I/O calls: {sorted(forbidden_calls)}"


def test_cleanup_dialog_uses_explicit_gateway_not_parent_window_privates() -> None:
    path = SRC_ROOT / "ui" / "dialogs" / "disk_cleanup_dialog.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    private_gateway_access: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "settings_gateway"
            and node.attr.startswith("_")
        ):
            private_gateway_access.add(node.attr)
    assert "parent_window" not in source
    assert not private_gateway_access


def test_production_code_never_force_terminates_threads() -> None:
    violations: set[tuple[str, str]] = set()
    for path in _python_files(SRC_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = _call_name(node)
                if call_name == "terminate" or call_name.endswith(".terminate"):
                    violations.add((_relative(path), call_name))
    assert not violations, f"Forced thread termination is forbidden: {sorted(violations)}"
