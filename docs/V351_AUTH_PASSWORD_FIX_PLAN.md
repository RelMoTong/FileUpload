# v3.5.1 P0-04 权限与改密缺陷修复方案

- 状态：**待评审，未实施**
- 范围：`src/services/auth_service.py`、`src/controllers/auth_controller.py`、`src/ui/main_window.py`、`src/core/i18n.py`、`tests/`
- 结论：三个现场 Bug 是**一条因果链**，但需要**三处独立修复**（去掉强制改密 / 统一密码真源 / 补断言与回归）

---

## 0. 缺陷复核（因果链与证据）

| 编号 | 现象 | 根因位置 |
| --- | --- | --- |
| Bug 1 | 首次登录默认口令即被强制改密 | `auth_service.py:111,115` 判定 `password == 默认口令` → `password_change_required=True`；`main_window.py:1608-1609` 自动弹改密框；`auth_controller.py:130-143` 据此清零全部控件 |
| Bug 2 | 改完密码不生效，默认口令仍可登录 | `main_window.py:2335` 取 `self.saved_settings.auth`（启动快照，改密后从不更新）+ `2432` 回填 + `1435` 以 `preserve_users=False` 落盘 → 旧哈希覆盖新哈希 |
| Bug 3 | 重新登录后所有按钮灰色 | Bug 2 的下游：只能用默认口令登录 → 再次 `password_change_required=True` → `auth_controller.py:130-143` 把 `btn_choose_src/btn_choose_tgt/btn_save/btn_start` 全置 False |

实测复现（真实 `ConfigRepository` + 临时 config.json）：

```
改密成功后磁盘:        users.admin = pbkdf2_sha256$600000$cqCk...
模拟一次"保存配置"后:  users.admin = 580a1867...（sha256("Tops123")，退回旧值）
重启后:  Abcd1234 → 密码错误 / Tops123 → 成功
         Tops123 登录后: btn_choose_src=False btn_choose_tgt=False btn_save=False btn_start=False
```

两个必须一并记录的事实：

1. **Bug 2 的触发条件**：磁盘 `users` 为空 `{}` 时 `ConfigManager._deep_merge` 会保留旧值、反而不会覆盖（首次安装那一轮我验证过是安全的）。但 `auth_service.py:113` 的登录升级会立刻把哈希落盘，**所以从第二次启动开始必现**；此外点一次"保存配置"（`832`）、或在"开始上传"时选择保存（`2636`）、或保存自动清理配置（`2466-2479`）都会触发。
2. **`tests/` 目录已在提交 `4d704ce`「清理并收敛 v3.5.1 发布源码」中被整体删除**（21 个文件），当前工作区没有测试目录，但 `requirements.lock.txt:7-8` 仍锁定 `pytest==9.0.2`、`pyright==1.1.411`。回归测试需要先恢复基建（见 §3.0）。

---

## 1. 修复一（F1）：去掉现场版强制改密

### F1-1 `src/services/auth_service.py` — 策略开关

在 `AuthService` 类头（现 `29` 行 `SIMPLE_PASSWORDS` 之后）新增策略位：

```python
class AuthService:
    SIMPLE_PASSWORDS = {"123", "123456", "password", "upload_pass", "Tops123"}

    # P0-04：现场版不强制改密；置 True 可恢复"默认口令必须改密"的旧行为。
    ENFORCE_DEFAULT_PASSWORD_CHANGE = False
```

`authenticate`（`104-121`）只改 `115` 行一处：

```python
-        model.password_change_required = default_password
+        model.password_change_required = (
+            default_password and self.ENFORCE_DEFAULT_PASSWORD_CHANGE
+        )
```

`LoginResult.uses_default_password=default_password`（`117-119`）**不动**，登录提醒与"默认弱口令"审计日志继续可用。

### F1-2 `src/ui/main_window.py:1605-1609` — 不再自动弹改密框

```python
         self._update_ui_permissions()
         self._warn_if_default_password_in_use(result.role.value)
         dialog.render_authenticated()
-        if result.uses_default_password:
-            QtCore.QTimer.singleShot(0, self._show_change_password)
+        # P0-04：现场版仅提示，不强制改密，也不自动弹框。
+        self._sync_auth_snapshot()
```

### F1-3 `src/ui/main_window.py:1419-1427` — 文案由"已禁用"改为"建议"

```python
     def _warn_if_default_password_in_use(self, role: str) -> None:
-        """登录成功后明确告知强制改密状态。"""
+        """登录成功后提示默认口令风险（现场版不强制改密）。"""
         weak_roles = set(self.auth_controller.default_password_roles)
         if role == 'user' and UserRole.USER in weak_roles:
-            self._append_log("⚠️ 用户角色仍在使用默认口令，修改密码前已禁用业务操作。")
-            self._toast('当前使用默认口令，必须先修改密码', 'warning')
+            self._append_log(f"⚠️ {t('msg_default_password_hint')}")
         elif role == 'admin' and UserRole.ADMIN in weak_roles:
-            self._append_log("⚠️ 管理员仍在使用默认口令，修改密码前已禁用业务操作。")
-            self._toast('管理员使用默认口令，必须先修改密码', 'warning')
+            self._append_log(f"⚠️ 管理员：{t('msg_default_password_hint')}")
```

- 去掉 `_toast`（现场每次登录弹窗会被当成故障）。
- 顺手修掉一个既有小缺陷：这两行原来是硬编码中文，切英文界面不生效。

### F1-4 `src/controllers/auth_controller.py:128-143` — 解除权限清零

整块替换为：

```python
     def compute_permissions(self, context: PermissionContext) -> ControlPermissions:
-        permissions = self._service.compute_permissions(self.current_role, context)
-        if not self._model.password_change_required:
-            return permissions
-        values = permissions.to_mapping()
-        for name in values:
-            values[name] = False
-        values.update(
-            btn_more=True,
-            ftp_config_widget=True,
-            ftp_server_collapsible=True,
-            menu_change_password=True,
-            menu_logout=True,
-            menu_language=True,
-        )
-        return ControlPermissions(**values)
+        # P0-04：默认口令不再作为业务门禁，权限只由角色与运行态决定。
+        return self._service.compute_permissions(self.current_role, context)
```

修完后的语义变化（**必须写进变更说明**）：`password_change_required` 退化为纯提醒信号，不再影响任何控件。

- 连带行为变化：`main_window.py:1637-1645` 的条件 `if password_change_required or current_role is USER` 在开关关闭后，等价于"仅普通用户锁死改自己"，这是期望结果，不需要改代码，但**测试断言要跟着改**。
- 保留 `main_window.py:1637-1645` 的 `setEnabled(False)`：改密框仍默认选中并锁定目标角色，避免误改他人口令。

### F1-5（可选）`src/core/i18n.py` — 补文案键

在 `msg_login_failed`（`445` 附近）之后追加：

```python
    'msg_default_password_hint': {
        LANG_ZH_CN: '当前正在使用默认口令，建议尽快在"更多 → 修改密码"中更换',
        LANG_EN_US: 'Default password in use. Change it in More > Change Password.',
    },
```

### F1 验收断言

- `AuthService.ENFORCE_DEFAULT_PASSWORD_CHANGE is False`
- `controller.login(ADMIN, "Tops123")` → `success is True`、`uses_default_password is True`、`password_change_required is False`
- `controller.compute_permissions(PermissionContext())` → `btn_start / btn_save / btn_choose_src / btn_choose_tgt` 全为 `True`
- 登录后 `_show_change_password` **未被调用**（现场不弹框）

---

## 2. 修复二（F2）：统一密码真源

### 真源契约（先定规则，再改代码）

> `AuthController._model.users` 是用户凭据的**唯一真源**；`config.json:users` 只是它的持久化副本；`ApplicationSettings.auth` 与 `MainWindow.saved_settings.auth` 是普通配置字段，**永远不得作为密码来源**。

### F2-1 `src/ui/main_window.py:1429-1438` — 落盘策略改为"保留磁盘 users"

```python
     def _write_settings(self, settings: ApplicationSettings) -> bool:
         """Persist the canonical settings object at the repository boundary."""
         self.last_config_save_error = ''
         if self.settings_controller is None:
             self.last_config_save_error = '配置控制器未初始化'
             return False
-        success = self.settings_controller.save(settings, preserve_users=False)
+        # P0-04：配置保存一律保留磁盘上的 users，密码只能由 AuthController 写。
+        success = self.settings_controller.save(settings, preserve_users=True)
         if not success:
             self.last_config_save_error = self.settings_controller.last_error or '配置保存失败'
         return success
```

这是**机制层兜底**：`ConfigManager.save(preserve_users=True)`（`config.py:218-219`）会用磁盘 users 覆盖入参，配置保存从此在物理上无法回写错误口令。该语义已被既有测试 `tests/test_settings_controller.py::test_config_manager_can_preserve_or_replace_users` 固化，改动零风险。

### F2-2 `src/ui/main_window.py:2334-2335, 2432` — 移除启动快照来源

```python
-        # 保留现有用户密码
-        users = self.saved_settings.auth.to_mapping()
-
         try:
```
```python
-            'users': users,
+            # 真源来自 AuthController，而不是启动时的配置快照
+            'users': self.auth_controller.users_mapping(),
```

F2-1 与 F2-2 建议**同时做**（双保险）：前者保证即使有人再写错来源也不会落盘，后者保证 `users` 字段语义显式、不依赖 `_deep_merge` 的隐式行为。

### F2-3 `src/controllers/auth_controller.py` — 新增只读导出

放在 `is_authenticated`（`152-156`）附近：

```python
    def users_mapping(self) -> Dict[str, Any]:
        """只读导出当前凭据快照，供持久化层以真源身份写入。"""
        return self._model.to_mapping()
```

### F2-4 `src/ui/main_window.py` — 新增 `_sync_auth_snapshot()` 并接入三处

```python
    def _sync_auth_snapshot(self) -> None:
        """把 auth 真源同步进配置快照，避免任何旧哈希被后续保存回写。"""
        try:
            self.saved_settings.auth.users = self.auth_controller.users_mapping()
        except Exception as e:
            logger.debug(f"同步凭据快照失败: {e}")
```

调用点：

| 位置 | 时机 | 原因 |
| --- | --- | --- |
| `_on_login_requested`（`1605` 之后） | 登录成功后 | 登录会触发 legacy sha256 → pbkdf2 升级并落盘 |
| `_on_change_password_requested`（`1680` `_update_ui_permissions()` 之前） | 改密成功后 | 本次 Bug 的核心同步点 |
| `_load_config`（`2528-2529` 之后） | 启动加载后 | 与 `auth_controller.load_users` 保持同刻一致 |

### F2-5 `src/controllers/auth_controller.py:95-126` — 改密写盘后读回断言

在 `change_password` 落盘成功分支后追加读回校验（**F3 要求的"状态断言"落点**）：

```python
         try:
             config = self._settings.load_raw()
             config["users"] = self._model.to_mapping()
             if not self._settings.save_raw(config, preserve_users=False):
                 self._model.users = previous_users
                 return PasswordChangeResult(
                     False, target_role, self._settings.last_error or "写入配置文件失败"
                 )
+            # P0-04 状态断言：读回校验目标角色哈希，杜绝"提示成功但实际未落盘"
+            persisted = self._settings.load_raw().get("users", {})
+            if persisted.get(target_role.value) != self._model.users.get(target_role.value):
+                self._model.users = previous_users
+                return PasswordChangeResult(
+                    False, target_role, "密码写入校验失败，请重试"
+                )
         except Exception as exc:
```

同样建议在 `login` 的升级分支（`78-90`）加同构校验：落盘后读回，`users[role]` 与模型不一致则回滚并返回失败。`load_raw()` 本身是廉价本地读，无性能顾虑。

只校验**目标角色键**而不是整体 `users` 相等，是为了避免磁盘上存在额外角色键（人工改过 config.json）时误报失败。

### F2-6（加固，可选）`src/ui/main_window.py:_load_config` — 启动凭据自检

`load_users`（`2528`）之后追加：若某角色哈希既不以 `pbkdf2_sha256$` 开头、也不是 64 位 hex，则写日志 + 托盘提示"凭据文件异常，已回退默认口令"。目的：让"静默降级到默认口令"这种体验不再无声发生。

### 明确不动项

| 不动 | 原因 |
| --- | --- |
| `ConfigManager.save` 的 `preserve_users` 语义（`config.py:189-231`） | 已被测试固化，且是新契约的基石 |
| `ApplicationSettings.to_config()` 的 `users` 字段（`settings.py:51`） | 保证配置往返不丢键 |
| `AuthService.password_hash` 的"缺失即默认口令"回退（`86-90`） | 默认口令可用是现场要求 |
| `ChangePasswordDialog` 与 `_show_change_password` 的目标锁定（`1637-1645`） | 与本次缺陷无关 |
| `login` 的 legacy 哈希升级 + 失败回滚（`auth_controller.py:76-90`） | 为正确实现，勿动 |

### F2 风险

- `_deep_merge` 是**并集**语义：磁盘上多出来的角色键不会被删除。当前无"删除角色凭据"需求，符合预期。
- 改 `preserve_users=True` 后，将来若要删除某角色凭据，必须显式走 `AuthController` 写盘路径（需在 F2-1 的注释里写明）。

---

## 3. 修复三（F3）：状态断言与回归测试

### F3-0 测试基建恢复（前置动作）

`tests/` 在 `4d704ce` 被整体删除。**按需选择性恢复，不要整体 checkout**，因为该提交同时改动了 `src/`（94 files changed），旧套件里有一批针对已删除模块的用例会必然失败。

第一批（本次缺陷直接相关，3 个）：

```bash
git checkout 4d704ce^ -- tests/test_auth_mvc.py tests/test_settings_controller.py tests/test_more_menu_permissions.py
```

**必须排除**（依赖已随无数据库迁移删除的符号 `CleanupIndexRepository` / `CleanupIndexRecord` / `DedupIndexRepository` / `CLEANUP_INDEX_WRITE_BATCH`）：

| 文件 | 失效原因 |
| --- | --- |
| `tests/test_cleanup_index.py` | 导入 `CleanupIndexResult`、`CleanupIndexRepository`、`CLEANUP_INDEX_WRITE_BATCH`（均已移除） |
| `tests/test_dedup_index.py` | 导入 `DedupIndexRepository`（已移除） |
| `tests/test_cleanup_policy_and_startup.py` | 导入 `CleanupIndexRecord`（已移除） |

第二批（依赖符号在现行 `src` 中均已核实存在，可后续恢复）：`test_ftp_basic`、`test_ftp_mvc`、`test_ftp_server_logging`、`test_ftp_upload_commit`、`test_lifecycle_mvc`、`test_runtime_mvc`、`test_upload_mvc`、`test_mvc_models`、`test_path_safety`、`test_pending_archive`、`test_resume_without_size_limit`、`test_view_components`、`test_responsive_layout`、`test_architecture_boundaries`。
（已核实存在：`_DeleteWorker`、`ResumableFileUploader`、`FTPClientUploader`、`extract_startup_target`、`src/services/path_safety.py`。）恢复后先跑基线，把失败用例区分为"因无数据库迁移失效"与"需随本次改造更新"两类。

新增 `tests/conftest.py`（历史上不存在，属于补齐）：

```python
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

### F3-1 必须反转的既有断言（不改就是"测试通过但功能仍错"）

| 文件 | 用例 | 旧断言 | 新断言 |
| --- | --- | --- | --- |
| `tests/test_auth_mvc.py:155-177` | `test_default_password_login_is_persisted_but_business_actions_stay_locked` → 改名 `..._keeps_business_actions_enabled` | `assert controller.password_change_required`；`assert not permissions.btn_start`；`assert not permissions.btn_save` | `assert not controller.password_change_required`；`assert permissions.btn_start`；`assert permissions.btn_save`；`assert permissions.btn_choose_src` |
| `tests/test_more_menu_permissions.py:67-88` | `test_default_password_login_opens_change_flow_and_locks_start` → 改名 `test_default_password_login_keeps_ui_usable` | `show_change.assert_called_once()`；`assertFalse(window.btn_start.isEnabled())` | `show_change.assert_not_called()`；`assertTrue(window.btn_start.isEnabled())`；`assertTrue(window.btn_save.isEnabled())`；`assertTrue(window.btn_choose_src.isEnabled())`；`assertFalse(window.auth_controller.password_change_required)` |

两个用例中"改密后 `password_change_required` 转 False"、"`menu_change_password` 可用"的断言保留。

### F3-2 新增回归用例：`tests/test_auth_password_regression.py`（纯无 Qt）

用**真实** `ConfigRepository` + 临时 `config.json`。注意 `tests/test_auth_mvc.py` 里的 `FakeSettings`（`50-64`）无视 `preserve_users` 语义，**不可能**捕获本次 Bug，不能作为回归载体。

| 编号 | 用例 | 断言要点 | 对应修复 |
| --- | --- | --- | --- |
| R1 | `test_password_change_survives_subsequent_config_save` | 改密成功 → 模拟保存配置 → 退出 → **新口令成功、默认口令失败**；磁盘 `users.admin` 以 `pbkdf2_sha256$` 开头 | F2-1 + F2-2 + F2-4 |
| R2 | `test_config_write_preserves_users_when_payload_carries_stale_hash` | 用"带旧哈希的 payload + `preserve_users=True`"保存 → 磁盘仍是新哈希 | F2-1（契约固化） |
| R3 | `test_users_survive_config_write_when_field_omitted` | payload 不带 `users` 键 → 保存后磁盘 `users` 不变 | F2-2（隐式合并语义） |
| R4 | `test_password_change_fails_loudly_when_persisted_hash_mismatch` | 假 settings（写盘时丢弃 users）→ `change_password` 返回 `success is False`，模型回滚，默认口令仍可登录 | F2-5（状态断言） |
| R5 | `test_auth_snapshot_matches_true_source_after_change` | 改密后 `controller.users_mapping()` 与磁盘 `users` 完全一致 | F2-3 + F2-4 |

R1 骨架（其余同构，替换构造即可）：

```python
def test_password_change_survives_subsequent_config_save(tmp_path):
    repository = ConfigRepository(tmp_path / "config.json")
    settings = SettingsController(repository)
    model = AuthModel()
    controller = AuthController(AuthService(), settings, model=model)

    loaded = settings.load_settings()                  # 等价 MainWindow._load_config
    controller.load_users(loaded.auth.to_mapping())

    assert controller.login(UserRole.ADMIN, "Tops123").success
    assert controller.change_password(
        UserRole.ADMIN, "Tops123", "FieldAdmin123!", "FieldAdmin123!"
    ).success

    payload = ConfigManager.get_default_config()       # 等价 _request_save_settings
    payload["source_folder"] = "D:/camera"
    payload["users"] = controller.users_mapping()
    assert settings.save(ApplicationSettings.from_config(payload), preserve_users=True)

    controller.logout()
    assert not controller.login(UserRole.ADMIN, "Tops123").success
    assert controller.login(UserRole.ADMIN, "FieldAdmin123!").success

    persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert persisted["users"]["admin"].startswith("pbkdf2_sha256$")
```

### F3-3 契约测试（UI 层，offscreen，加进 `tests/test_more_menu_permissions.py`）

复用该文件已有的 `create_main_window()`（`30-63`），把 `SettingsStub` 换成**记录型 spy**：

| 用例 | 做法 | 断言 |
| --- | --- | --- |
| `test_config_write_keeps_users_by_default` | spy 记录 `save(settings, preserve_users)` 实参，调用 `window._write_settings(ApplicationSettings())` | `preserve_users is True` |
| `test_login_and_change_password_sync_auth_snapshot` | 默认口令登录 + 改密后 | `window.saved_settings.auth.users == window.auth_controller.users_mapping()` |
| `test_default_password_login_keeps_ui_usable` | 见 F3-1 | 控件可用 |

### F3-4 覆盖矩阵

| 缺陷 | 修复 | 回归用例 |
| --- | --- | --- |
| Bug 1 强制改密 | F1-1/2/3/4 | 反转后的 `test_default_password_login_keeps_*` ×2 |
| Bug 2 改密被覆盖 | F2-1/2/3/4 | R1、R2、R3、R5、`test_config_write_keeps_users_by_default` |
| Bug 3 登录后全灰 | F1-4（根因）+ Bug 2 修复 | 反转后的 UI 用例 + R1 |
| 静默失败 | F2-5 | R4 |

### F3-5 执行与门禁

```bash
python -m pytest -q tests -p no:cacheprovider --basetemp .pytest_tmp_p0_04
python -m pyright src
python -m compileall -q src
```

现场包另跑既有冒烟入口（`src/main.py:448-452`）：`IMAGE_UPLOAD_SMOKE_TEST=1`。
收尾需更新：`docs/CHANGELOG.md`「待发布」段补 P0-04 条目；`docs/V351_RELEASE_VERIFICATION.md` 更新测试计数与结论。

---

## 4. 变更清单汇总

| 文件 | 行号 | 动作 | 关联 |
| --- | --- | --- | --- |
| `src/services/auth_service.py` | `29` 之后 | 新增 `ENFORCE_DEFAULT_PASSWORD_CHANGE = False` | F1-1 |
| `src/services/auth_service.py` | `115` | 改密标记与策略开关绑定 | F1-1 |
| `src/ui/main_window.py` | `1608-1609` | 删除自动弹改密框，改调 `_sync_auth_snapshot()` | F1-2 / F2-4 |
| `src/ui/main_window.py` | `1419-1427` | 文案改为建议 + 去 toast + 接 i18n | F1-3 |
| `src/ui/main_window.py` | `1429-1438` | `preserve_users=False` → `True` | F2-1 |
| `src/ui/main_window.py` | `2334-2335`、`2432` | 删除快照来源，改取真源 | F2-2 |
| `src/ui/main_window.py` | 新增方法 | `_sync_auth_snapshot()` + 3 处调用 | F2-4 |
| `src/ui/main_window.py` | `2528` 之后 | （可选）启动凭据自检 | F2-6 |
| `src/controllers/auth_controller.py` | `128-143` | 删除权限清零块 | F1-4 |
| `src/controllers/auth_controller.py` | `152` 附近 | 新增 `users_mapping()` | F2-3 |
| `src/controllers/auth_controller.py` | `110-126` | 落盘后读回断言 + 回滚 | F2-5 |
| `src/controllers/auth_controller.py` | `78-90` | （建议）升级落盘后同构校验 | F2-5 |
| `src/core/i18n.py` | `445` 附近 | 新增 `msg_default_password_hint` | F1-5 |
| `tests/conftest.py` | 新增 | offscreen + sys.path | F3-0 |
| `tests/test_auth_password_regression.py` | 新增 | R1–R5 | F3-2 |
| `tests/test_auth_mvc.py` | `155-177` | 反转断言 + 改名 | F3-1 |
| `tests/test_more_menu_permissions.py` | `67-88` 等 | 反转断言 + 2 个契约用例 | F3-1 / F3-3 |
| `docs/CHANGELOG.md`、`docs/V351_RELEASE_VERIFICATION.md` | — | 记录 P0-04 与测试计数 | F3-5 |

---

## 5. 实施顺序与提交拆分

| 提交 | 内容 | 验证 |
| --- | --- | --- |
| 1 | F1 全部 + F3-0/F3-1（恢复 3 个测试文件 + 反转断言） | `pytest -q tests/test_auth_mvc.py tests/test_more_menu_permissions.py` |
| 2 | F2-1 ~ F2-5 + F3-2（R1–R5） | 新增用例全绿；R1 在**打补丁前应先红**（先写测试再改代码以证伪） |
| 3 | F3-3 契约用例 + 第二批测试恢复 + 文档/CHANGELOG | 全量 `pytest -q tests` + `pyright src` + 现场冒烟 |

拆分原则：**每个提交都可独立回退**，且提交 2 之前不留"半统一真源"的中间态。

---

## 6. 风险与回退

| 风险 | 处置 |
| --- | --- |
| 去掉强制改密后现场长期使用弱口令 | 保留登录日志提醒 + 托盘一次性提示；`ENFORCE_DEFAULT_PASSWORD_CHANGE = True` 即可一键恢复旧策略 |
| `preserve_users=True` 语义被后续改动推翻 | F3-3 的契约用例（断言 `preserve_users is True`）会立刻报警 |
| 第二批测试带回"已失效"用例造成噪音 | 按 F3-0 的分类表剔除，只保留现行 `src` 有对应实现的用例 |
| 现场已装在旧版本 | 继续使用既有 `v3.5.0` 回退包；本次改动不涉及 `config.json` 结构，配置向后兼容 |

---

## 7. 现场验收清单（人工，逐条留证）

1. 全新解压首次运行 → 管理员 `Tops123` 登录 → **不弹**改密框，日志出现默认口令建议提示。
2. 登录后「源文件夹 / 目标文件夹 / 浏览 / 保存配置 / 开始上传」全部可点击。
3. 「更多 → 修改密码」将管理员口令改为现场口令 → 提示成功。
4. 点击一次「保存配置」。
5. **完全退出程序并重启** → 新口令登录成功，`Tops123` 登录失败。
6. 退出登录后重复第 5 步，界面按钮仍全部可用（不再全灰）。
7. 普通用户 `123` 登录 → 按钮可用（不做强制改密）。
8. 修改普通用户口令 → 保存配置 → 重启 → 新口令生效。
9. 断开网络 / 重启 SMB 服务后仍可正常登录（凭据不依赖网络与外部服务）。
10. 检查现场 `config.json`：`users.admin` 与 `users.user` 均为 `pbkdf2_sha256$` 前缀，且与登录口令一致。
