@echo off
setlocal enableextensions enabledelayedexpansion

:: ========================================
::  图片异步上传工具 v2.2.0 - 一键打包脚本
:: ========================================
:: 功能：生成免安装的 .exe 可执行文件
:: 日期：2025-11-17
:: ========================================

:: 设置控制台为 UTF-8
chcp 65001 >nul 2>&1

echo.
echo ========================================
echo   图片异步上传工具 v2.2.0 - 打包程序
echo ========================================
echo.
echo [信息] 开始准备打包环境...
echo.

:: 切换到项目根目录（脚本在scripts子目录下）
cd /d "%~dp0.."
echo [信息] 当前工作目录：%CD%
echo.

:: ========================================
:: 1. 检查 Python 环境
:: ========================================
echo [1/7] 检查 Python 环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    echo        下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo.

:: ========================================
:: 2. 检查必要依赖
:: ========================================
echo [2/7] 检查必要依赖...

:: 检查 PySide6/PyQt5
python -c "import PySide6" >nul 2>&1
if %errorlevel% neq 0 (
    python -c "import PyQt5" >nul 2>&1
    if %errorlevel% neq 0 (
        echo [错误] 未检测到 PySide6 或 PyQt5
        echo        请先安装：pip install PySide6
        pause
        exit /b 1
    ) else (
        echo [信息] 使用 PyQt5
    )
) else (
    echo [信息] 使用 PySide6
)

:: 检查并安装 PyInstaller
python -c "import PyInstaller" >nul 2>&1
if %errorlevel% neq 0 (
    echo [信息] 未检测到 PyInstaller，正在安装...
    pip install pyinstaller
    if %errorlevel% neq 0 (
        echo [错误] PyInstaller 安装失败
        pause
        exit /b 1
    )
)
echo [信息] PyInstaller 已就绪
echo.

:: ========================================
:: 3. 结束可能占用文件的进程
:: ========================================
echo [3/7] 检查并结束占用进程...

:: 结束所有可能的进程
taskkill /F /IM "图片异步上传工具*.exe" >nul 2>&1
taskkill /F /IM "python.exe" /FI "WINDOWTITLE eq *pyqt_app*" >nul 2>&1
taskkill /F /IM "pythonw.exe" /FI "WINDOWTITLE eq *pyqt_app*" >nul 2>&1

echo [信息] 等待进程完全退出...
timeout /t 3 /nobreak >nul
echo.

:: ========================================
:: 4. 强力清理旧构建
:: ========================================
echo [4/7] 清理旧构建文件...

:: 使用 Python 脚本强制删除（绕过 Windows 权限限制）
python -c "import shutil, os, time; [shutil.rmtree(d, ignore_errors=True) if os.path.exists(d) else None for d in ['build', 'dist']]; time.sleep(1)"

:: 再次尝试用批处理删除
if exist build (
    echo [信息] 删除 build 目录...
    attrib -r -s -h build\*.* /s /d >nul 2>&1
    rd /s /q build >nul 2>&1
)

if exist dist (
    echo [信息] 删除 dist 目录...
    attrib -r -s -h dist\*.* /s /d >nul 2>&1
    rd /s /q dist >nul 2>&1
)

:: 删除旧 spec 文件
for %%f in (图片异步上传工具*.spec) do (
    if exist "%%f" (
        echo [信息] 删除旧 spec 文件: %%f
        del /f /q "%%f" >nul 2>&1
    )
)

:: 最后验证清理结果
if exist build (
    echo [警告] build 目录仍然存在，但将继续打包...
)

if exist dist (
    echo [警告] dist 目录仍然存在，将尝试重命名...
    
    :: 重命名旧目录为备份
    set BACKUP_NAME=dist_backup_%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
    set BACKUP_NAME=!BACKUP_NAME: =0!
    
    move dist "!BACKUP_NAME!" >nul 2>&1
    
    if exist dist (
        echo [错误] 无法清理 dist 目录
        echo.
        echo 解决方案 1: 手动删除 dist 文件夹后重试
        echo 解决方案 2: 重启电脑后重试
        echo 解决方案 3: 使用管理员权限运行本脚本
        echo.
        echo 提示: 可以尝试使用 Unlocker 等工具解锁文件
        echo       下载地址: https://www.iobit.com/en/iobit-unlocker.php
        echo.
        pause
        exit /b 1
    ) else (
        echo [信息] 已将旧 dist 重命名为: !BACKUP_NAME!
    )
)

echo [信息] 清理完成
echo.

:: ========================================
:: 5. 配置打包参数
:: ========================================
echo [5/7] 配置打包参数...
set APP_NAME=图片异步上传工具
set VERSION=2.2.0
set OUTPUT_NAME=%APP_NAME%_v%VERSION%
set DIST_DIR=dist-%VERSION%
set ENTRY=pyqt_app.py
set ICON_PARAM=

:: 检查图标文件
if exist assets\app.ico (
    set ICON_PARAM=--icon=assets\app.ico
    echo [信息] 使用自定义图标：assets\app.ico
) else (
    echo [信息] 未找到图标文件，使用默认图标
)

echo [信息] 应用名称：%APP_NAME%
echo [信息] 版本号：v%VERSION%
echo [信息] 输出文件名：%OUTPUT_NAME%.exe
echo [信息] 输出目录：%DIST_DIR%\
echo.

:: ========================================
:: 6. 执行打包
:: ========================================
echo [6/7] 开始打包（这可能需要几分钟）...
echo [信息] 使用目录模式打包（启动速度更快）...
echo.
echo ----------------------------------------
echo PyInstaller 日志输出：
echo ----------------------------------------
echo.

pyinstaller --noconfirm ^
  --onedir ^
  --windowed ^
  --name "%OUTPUT_NAME%" ^
  --distpath "%DIST_DIR%" ^
  --add-data "config.json;." ^
  --add-data "assets;assets" ^
  --add-data "logs;logs" ^
  --add-data "core;core" ^
  --hidden-import=PySide6.QtCore ^
  --hidden-import=PySide6.QtGui ^
  --hidden-import=PySide6.QtWidgets ^
  --hidden-import=PySide6.QtNetwork ^
  --exclude-module=PyQt5 ^
  --exclude-module=PyQt6 ^
  --collect-all=PySide6 ^
  %ICON_PARAM% ^
  %ENTRY%

if %errorlevel% neq 0 (
    echo.
    echo ========================================
    echo [错误] 打包失败！
    echo ========================================
    echo.
    echo 可能的原因：
    echo   1. 缺少必要的依赖包
    echo   2. 代码存在语法错误
    echo   3. PyInstaller 版本不兼容
    echo   4. 文件被占用或权限不足
    echo.
    echo 建议：
    echo   1. 运行 pip install -r requirements.txt
    echo   2. 检查 pyqt_app.py 是否有错误
    echo   3. 尝试更新 PyInstaller：pip install -U pyinstaller
    echo   4. 以管理员身份运行本脚本
    echo.
    pause
    exit /b 1
)

:: ========================================
:: 7. 验证打包结果
:: ========================================
echo.
echo [7/7] 验证打包结果...

if not exist "%DIST_DIR%\%OUTPUT_NAME%\%OUTPUT_NAME%.exe" (
    echo [错误] 未找到输出文件：%DIST_DIR%\%OUTPUT_NAME%\%OUTPUT_NAME%.exe
    echo [信息] 打包可能失败，请查看上方日志
    pause
    exit /b 1
)

:: 获取文件大小
for %%A in ("%DIST_DIR%\%OUTPUT_NAME%\%OUTPUT_NAME%.exe") do set FILE_SIZE=%%~zA

:: 计算MB大小（简化版）
set /a SIZE_MB=%FILE_SIZE% / 1048576

echo.
echo ========================================
echo [成功] 打包完成！
echo ========================================
echo.
echo 输出目录：%DIST_DIR%\%OUTPUT_NAME%\
echo 主程序：%OUTPUT_NAME%.exe
echo 程序大小：%SIZE_MB% MB
echo.
echo 📦 打包内容：
echo   ✓ 主程序：pyqt_app.py
echo   ✓ 配置文件：config.json
echo   ✓ 资源文件：assets\*
echo   ✓ 日志目录：logs\
echo   ✓ 依赖库：PySide6/PyQt5
echo   ✓ 运行库：所有依赖 DLL 文件
echo.
echo 📝 使用说明：
echo   1. 将整个 %DIST_DIR%\%OUTPUT_NAME%\ 目录复制给用户
echo   2. 双击 %OUTPUT_NAME%.exe 运行（启动速度快）
echo   3. 首次运行会自动创建配置和日志
echo.
echo 💡 提示：
echo   - 目录模式启动速度比单文件模式快 5-10 倍
echo   - 必须保持整个目录完整，不能只复制 .exe 文件
echo   - 默认用户密码：123
echo   - 默认管理员密码：Tops123
echo.

:: 打开输出目录
echo [信息] 正在打开输出目录...
timeout /t 2 /nobreak >nul
explorer %DIST_DIR%

echo.
echo 按任意键退出...
pause >nul

endlocal
exit /b 0