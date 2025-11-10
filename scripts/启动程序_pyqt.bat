@echo off
chcp 65001 >nul
title 图片异步上传工具 - PySide6版本
color 0A

echo ========================================
echo   图片异步上传工具 - 启动程序
echo   PySide6/PyQt5 版本
echo ========================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [信息] Python 版本:
python --version
echo.

REM 检查 pip 是否可用
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [错误] pip 不可用，请修复 Python 安装
    pause
    exit /b 1
)

echo [步骤1] 检查依赖库...
echo.

REM 检查 PySide6 是否已安装
python -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo [提示] PySide6 未安装，正在安装...
    python -m pip install PySide6>=6.5 -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [警告] PySide6 安装失败，尝试安装 PyQt5 作为后备...
        python -m pip install PyQt5>=5.15 -i https://pypi.tuna.tsinghua.edu.cn/simple
        if errorlevel 1 (
            echo [错误] Qt 库安装失败，无法运行程序
            pause
            exit /b 1
        )
    )
) else (
    echo [信息] PySide6 已安装
)
echo.

REM 安装其他依赖
echo [步骤2] 安装程序依赖...
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [警告] 部分依赖安装失败，但可能不影响运行
)
echo.

echo [步骤3] 启动程序...
echo ========================================
echo.

REM 运行程序
python pyqt_app.py

REM 程序退出后的处理
if errorlevel 1 (
    echo.
    echo [错误] 程序异常退出，错误码: %errorlevel%
    echo 请检查日志文件获取详细信息
) else (
    echo.
    echo [信息] 程序已正常退出
)

echo.
pause
