@echo off
setlocal enableextensions enabledelayedexpansion

REM ============================================================
REM  图片异步上传工具 - 自动打包脚本 v3.0.0
REM  更新日期: 2025-11-27
REM  v3.0.0: 模块化架构重构完成，使用 src/main.py 作为入口
REM ============================================================

set VERSION=3.0.0
set APP_NAME=图片异步上传工具_v%VERSION%
set DIST_DIR=dist-%VERSION%

echo.
echo ============================================================
echo   图片异步上传工具 v%VERSION% 打包脚本 (模块化架构)
echo ============================================================
echo.

REM [1/9] 检查Python环境
echo [1/9] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: Python 未安装或未添加到PATH
    pause
    exit /b 1
)
echo      Python 环境正常

REM [2/9] 检查PyInstaller
echo [2/9] 检查PyInstaller...
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo      PyInstaller 未安装，正在安装...
    pip install pyinstaller
)
echo      PyInstaller 已就绪

REM [3/9] 检查入口文件
echo [3/9] 检查入口文件...
cd /d "%~dp0.."
if not exist "src\main.py" (
    echo 错误: 找不到入口文件 src\main.py
    pause
    exit /b 1
)
echo      入口文件: src\main.py
echo      工作目录: %CD%

REM 检查图标文件
set ICON_FILE=
if exist "assets\logo.ico" (
    set ICON_FILE=assets\logo.ico
    echo      图标文件: assets\logo.ico
) else if exist "assets\logo.png" (
    echo      警告: 未找到 logo.ico，尝试使用 logo.png
    set ICON_FILE=assets\logo.png
) else (
    echo      警告: 未找到图标文件，将使用默认图标
)

REM [4/9] 检查模块化组件
echo [4/9] 检查模块化组件...
set MISSING_MODULES=0
if not exist "src\ui\main_window.py" (
    echo      警告: 缺少 src\ui\main_window.py
    set MISSING_MODULES=1
)
if not exist "src\ui\widgets.py" (
    echo      警告: 缺少 src\ui\widgets.py
    set MISSING_MODULES=1
)
if not exist "src\workers\upload_worker.py" (
    echo      警告: 缺少 src\workers\upload_worker.py
    set MISSING_MODULES=1
)
if not exist "src\protocols\ftp.py" (
    echo      警告: 缺少 src\protocols\ftp.py
    set MISSING_MODULES=1
)
if not exist "src\config.py" (
    echo      警告: 缺少 src\config.py
    set MISSING_MODULES=1
)
if not exist "src\core\utils.py" (
    echo      警告: 缺少 src\core\utils.py
    set MISSING_MODULES=1
)
if not exist "src\core\permissions.py" (
    echo      警告: 缺少 src\core\permissions.py
    set MISSING_MODULES=1
)
if %MISSING_MODULES%==1 (
    echo      警告: 部分模块缺失，可能影响打包
) else (
    echo      所有模块化组件已就绪
)

REM [5/9] 清理旧的构建文件
echo [5/9] 清理旧的构建文件...
if exist "build" rmdir /s /q "build"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "*.spec" del /q *.spec
echo      清理完成

REM [6/9] 执行打包
echo [6/9] 执行打包 (模块化架构)...
echo      入口: src\main.py
echo      输出: %DIST_DIR%\%APP_NAME%
echo.

REM 构建图标参数
set ICON_PARAM=
if defined ICON_FILE (
    set ICON_PARAM=--icon="%ICON_FILE%"
)

pyinstaller ^
  --name="%APP_NAME%" ^
  --windowed ^
  --onedir ^
  --distpath="%DIST_DIR%" ^
  %ICON_PARAM% ^
  --add-data="assets;assets" ^
  --add-data="config.json;." ^
  --add-data="version.txt;." ^
  --add-data="qt_types.py;." ^
  --add-data="src;src" ^
  --hidden-import=PySide6 ^
  --hidden-import=PySide6.QtCore ^
  --hidden-import=PySide6.QtGui ^
  --hidden-import=PySide6.QtWidgets ^
  --hidden-import=PySide6.QtNetwork ^
  --hidden-import=src ^
  --hidden-import=src.main ^
  --hidden-import=src.config ^
  --hidden-import=src.ui ^
  --hidden-import=src.ui.main_window ^
  --hidden-import=src.ui.widgets ^
  --hidden-import=src.workers ^
  --hidden-import=src.workers.upload_worker ^
  --hidden-import=src.core ^
  --hidden-import=src.core.utils ^
  --hidden-import=src.core.permissions ^
  --hidden-import=src.protocols ^
  --hidden-import=src.protocols.ftp ^
  --hidden-import=qt_types ^
  --hidden-import=pyftpdlib ^
  --hidden-import=pyftpdlib.handlers ^
  --hidden-import=pyftpdlib.servers ^
  --hidden-import=pyftpdlib.authorizers ^
  --exclude-module=PySide6.QtWebEngine ^
  --exclude-module=PySide6.QtWebEngineCore ^
  --exclude-module=PySide6.QtWebEngineWidgets ^
  --exclude-module=PySide6.Qt3DCore ^
  --exclude-module=PySide6.Qt3DInput ^
  --exclude-module=PySide6.Qt3DRender ^
  --exclude-module=PySide6.Qt3DAnimation ^
  --exclude-module=PySide6.Qt3DExtras ^
  --exclude-module=PySide6.Qt3DLogic ^
  --exclude-module=PySide6.QtQuick ^
  --exclude-module=PySide6.QtQuick3D ^
  --exclude-module=PySide6.QtQuickWidgets ^
  --exclude-module=PySide6.QtQml ^
  --exclude-module=PySide6.QtQmlModels ^
  --exclude-module=PySide6.QtSql ^
  --exclude-module=PySide6.QtTest ^
  --exclude-module=PySide6.QtDesigner ^
  --exclude-module=PySide6.QtHelp ^
  --exclude-module=PySide6.QtMultimedia ^
  --exclude-module=PySide6.QtMultimediaWidgets ^
  --exclude-module=PySide6.QtBluetooth ^
  --exclude-module=PySide6.QtNfc ^
  --exclude-module=PySide6.QtPositioning ^
  --exclude-module=PySide6.QtLocation ^
  --exclude-module=PySide6.QtSensors ^
  --exclude-module=PySide6.QtSerialPort ^
  --exclude-module=PySide6.QtRemoteObjects ^
  --exclude-module=PySide6.QtScxml ^
  --exclude-module=PySide6.QtCharts ^
  --exclude-module=PySide6.QtDataVisualization ^
  --exclude-module=PySide6.QtPdf ^
  --exclude-module=PySide6.QtPdfWidgets ^
  --exclude-module=PySide6.QtOpenGL ^
  --exclude-module=PySide6.QtOpenGLWidgets ^
  --exclude-module=PySide6.QtSvgWidgets ^
  --exclude-module=PySide6.QtStateMachine ^
  --exclude-module=PySide6.QtTextToSpeech ^
  --exclude-module=PySide6.QtWebChannel ^
  --exclude-module=PySide6.QtWebSockets ^
  --exclude-module=PySide6.QtHttpServer ^
  --exclude-module=tkinter ^
  --exclude-module=matplotlib ^
  --exclude-module=numpy ^
  --exclude-module=pandas ^
  --exclude-module=scipy ^
  --exclude-module=PIL ^
  --exclude-module=cv2 ^
  --exclude-module=PyQt5 ^
  --exclude-module=PyQt6 ^
  --noconfirm ^
  --clean ^
  "src\main.py"

if errorlevel 1 (
    echo.
    echo 错误: 打包失败！
    pause
    exit /b 1
)

echo.
echo      打包完成

REM [7/9] 创建日志目录
echo [7/9] 创建日志目录...
if not exist "%DIST_DIR%\%APP_NAME%\logs" mkdir "%DIST_DIR%\%APP_NAME%\logs"
echo      日志目录已创建

REM [8/9] 验证打包结果
echo [8/9] 验证打包结果...
if not exist "%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe" (
    echo 错误: 可执行文件未生成
    pause
    exit /b 1
)

for %%A in ("%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe") do set EXE_SIZE=%%~zA
set /a EXE_SIZE_MB=%EXE_SIZE% / 1048576
echo      可执行文件: %APP_NAME%.exe (%EXE_SIZE_MB% MB)

REM 计算 _internal 目录大小
set INTERNAL_SIZE=0
for /r "%DIST_DIR%\%APP_NAME%\_internal" %%F in (*) do set /a INTERNAL_SIZE+=%%~zF
set /a INTERNAL_SIZE_MB=%INTERNAL_SIZE% / 1048576
echo      依赖库大小: %INTERNAL_SIZE_MB% MB

REM [9/9] 创建发布压缩包
echo [9/9] 创建发布压缩包...
set ZIP_NAME=%APP_NAME%_发布版.zip
if exist "%ZIP_NAME%" del /q "%ZIP_NAME%"

REM 使用 PowerShell 创建压缩包
powershell -Command "Compress-Archive -Path '%DIST_DIR%\%APP_NAME%\*' -DestinationPath '%ZIP_NAME%' -Force" 2>nul
if exist "%ZIP_NAME%" (
    for %%A in ("%ZIP_NAME%") do set ZIP_SIZE=%%~zA
    set /a ZIP_SIZE_MB=!ZIP_SIZE! / 1048576
    echo      压缩包: %ZIP_NAME% (!ZIP_SIZE_MB! MB)
) else (
    echo      警告: 压缩包创建失败，请手动压缩
)

echo.
echo ============================================================
echo   打包完成！
echo ============================================================
echo.
echo   输出目录: %DIST_DIR%\%APP_NAME%
echo   可执行文件: %APP_NAME%.exe
echo   压缩包: %ZIP_NAME%
echo.
echo   架构: 模块化 (src/main.py 入口)
echo   模块:
echo     - src/ui/main_window.py (主窗口)
echo     - src/ui/widgets.py (控件)
echo     - src/workers/upload_worker.py (上传)
echo     - src/protocols/ftp.py (FTP协议)
echo     - src/config.py (配置)
echo     - src/core/* (核心工具)
echo.
echo ============================================================
pause
exit /b 0