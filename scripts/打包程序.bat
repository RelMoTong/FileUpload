@echo off
chcp 65001 >nul 2>&1
setlocal enableextensions enabledelayedexpansion

set VERSION=3.0.1
set APP_NAME=ImageUploadTool_v%VERSION%
set DIST_DIR=dist-%VERSION%

echo.
echo ============================================================
echo   Image Upload Tool v%VERSION% Build Script
echo ============================================================
echo.

echo [1/9] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found
    pause
    exit /b 1
)
echo      Python OK

echo [2/9] Checking PyInstaller...
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo      Installing PyInstaller...
    pip install pyinstaller
)
echo      PyInstaller OK

echo [3/9] Checking entry file...
cd /d "%~dp0.."
if not exist "src\main.py" (
    echo ERROR: src\main.py not found
    pause
    exit /b 1
)
echo      Entry: src\main.py
echo      Icon: assets\logo.ico (if exists)

set ICON_FILE=
if exist "assets\logo.ico" (
    set ICON_FILE=assets\logo.ico
) else if exist "assets\logo.png" (
    set ICON_FILE=assets\logo.png
)

echo [4/9] Checking modules...
set MISSING_MODULES=0
if not exist "src\ui\main_window.py" set MISSING_MODULES=1
if not exist "src\ui\widgets.py" set MISSING_MODULES=1
if not exist "src\workers\upload_worker.py" set MISSING_MODULES=1
if not exist "src\protocols\ftp.py" set MISSING_MODULES=1
if not exist "src\config.py" set MISSING_MODULES=1
if %MISSING_MODULES%==1 (
    echo      Warning: Some modules missing
) else (
    echo      All modules OK
)

echo [5/9] Cleaning old build...
if exist "build" rmdir /s /q "build"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "*.spec" del /q *.spec 2>nul
echo      Clean OK

echo [6/9] Building...
echo      Entry: src\main.py
echo      Output: %DIST_DIR%\%APP_NAME%
echo.

set ICON_PARAM=
if defined ICON_FILE set ICON_PARAM=--icon="%ICON_FILE%"

pyinstaller --name="%APP_NAME%" --windowed --onedir --distpath="%DIST_DIR%" %ICON_PARAM% --add-data="assets;assets" --add-data="config.json;." --add-data="version.txt;." --add-data="qt_types.py;." --add-data="src;src" --hidden-import=PySide6 --hidden-import=PySide6.QtCore --hidden-import=PySide6.QtGui --hidden-import=PySide6.QtWidgets --hidden-import=PySide6.QtNetwork --hidden-import=src --hidden-import=src.main --hidden-import=src.config --hidden-import=src.ui --hidden-import=src.ui.main_window --hidden-import=src.ui.widgets --hidden-import=src.workers --hidden-import=src.workers.upload_worker --hidden-import=src.core --hidden-import=src.core.utils --hidden-import=src.core.permissions --hidden-import=src.protocols --hidden-import=src.protocols.ftp --hidden-import=qt_types --hidden-import=pyftpdlib --hidden-import=pyftpdlib.handlers --hidden-import=pyftpdlib.servers --hidden-import=pyftpdlib.authorizers --exclude-module=PySide6.QtWebEngine --exclude-module=PySide6.QtWebEngineCore --exclude-module=PySide6.QtWebEngineWidgets --exclude-module=PySide6.Qt3DCore --exclude-module=PySide6.QtQuick --exclude-module=PySide6.QtQml --exclude-module=PySide6.QtSql --exclude-module=PySide6.QtTest --exclude-module=PySide6.QtDesigner --exclude-module=PySide6.QtMultimedia --exclude-module=PySide6.QtBluetooth --exclude-module=PySide6.QtCharts --exclude-module=PySide6.QtDataVisualization --exclude-module=PySide6.QtPdf --exclude-module=PySide6.QtOpenGL --exclude-module=tkinter --exclude-module=matplotlib --exclude-module=numpy --exclude-module=pandas --exclude-module=scipy --exclude-module=PIL --exclude-module=cv2 --exclude-module=PyQt5 --exclude-module=PyQt6 --noconfirm --clean "src\main.py"

if errorlevel 1 (
    echo.
    echo ERROR: Build failed!
    pause
    exit /b 1
)
echo      Build OK

echo [7/9] Creating logs directory...
if not exist "%DIST_DIR%\%APP_NAME%\logs" mkdir "%DIST_DIR%\%APP_NAME%\logs"
echo      OK

echo [8/9] Verifying...
if not exist "%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe" (
    echo ERROR: Executable not found
    pause
    exit /b 1
)
for %%A in ("%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe") do set EXE_SIZE=%%~zA
set /a EXE_SIZE_MB=%EXE_SIZE% / 1048576
echo      Executable: %APP_NAME%.exe (%EXE_SIZE_MB% MB)

echo [9/9] Creating release zip...
set ZIP_NAME=%APP_NAME%_Release.zip
powershell -NoProfile -Command "Compress-Archive -Path '%DIST_DIR%\%APP_NAME%\*' -DestinationPath '%ZIP_NAME%' -Force" 2>nul
if exist "%ZIP_NAME%" (
    echo      Zip: %ZIP_NAME%
) else (
    echo      Warning: Zip failed
)

echo.
echo ============================================================
echo   BUILD COMPLETE
echo ============================================================
echo   Output: %DIST_DIR%\%APP_NAME%
echo   Exe: %APP_NAME%.exe
echo   Entry: src/main.py (modular architecture)
echo ============================================================
echo.
echo Press any key to exit...
pause >nul
exit /b 0