@echo off
chcp 65001 >nul 2>&1
setlocal enableextensions enabledelayedexpansion

echo.
echo ============================================================
echo   Image Upload Tool Build Script
echo ============================================================
echo.

echo [1/7] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found
    pause
    exit /b 1
)
echo      Python OK

echo [2/7] Checking PyInstaller...
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo      Installing PyInstaller...
    pip install pyinstaller
)
echo      PyInstaller OK

echo [3/7] Resolving version...
cd /d "%~dp0.."
if not exist "ImageUploadTool.spec" (
    echo ERROR: ImageUploadTool.spec not found
    pause
    exit /b 1
)
for /f "delims=" %%V in ('python -c "from src import __version__; print(__version__)"') do set VERSION=%%V
set APP_NAME=ImageUploadTool_v%VERSION%
set DIST_DIR=dist
echo      Version: %VERSION%

echo [4/7] Cleaning old build...
if exist "build" rmdir /s /q "build"
if exist "%DIST_DIR%\\%APP_NAME%" rmdir /s /q "%DIST_DIR%\\%APP_NAME%"
echo      Clean OK

echo [5/7] Building...
pyinstaller ImageUploadTool.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo ERROR: Build failed!
    pause
    exit /b 1
)
echo      Build OK

echo [6/7] Creating logs directory...
if not exist "%DIST_DIR%\\%APP_NAME%\\logs" mkdir "%DIST_DIR%\\%APP_NAME%\\logs"
echo      OK

echo [7/7] Verifying...
if not exist "%DIST_DIR%\\%APP_NAME%\\%APP_NAME%.exe" (
    echo ERROR: Executable not found
    pause
    exit /b 1
)
for %%A in ("%DIST_DIR%\\%APP_NAME%\\%APP_NAME%.exe") do set EXE_SIZE=%%~zA
set /a EXE_SIZE_MB=%EXE_SIZE% / 1048576
echo      Executable: %APP_NAME%.exe (%EXE_SIZE_MB% MB)

echo.
echo ============================================================
echo   BUILD COMPLETE
echo ============================================================
echo   Output: %DIST_DIR%\\%APP_NAME%
echo   Exe: %APP_NAME%.exe
echo   Entry: src/main.py
echo ============================================================
echo.
echo Press any key to exit...
pause >nul
exit /b 0
