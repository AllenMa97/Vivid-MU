@echo off
chcp 65001 >nul
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%python_env\python.exe"
set "FFMPEG_DIR=%PROJECT_DIR%ffmpeg\windows\bin"
set "PATH=%FFMPEG_DIR%;%PATH%"

echo Starting GUI...
"%PYTHON_EXE%" gui.py
if errorlevel 1 (
    echo [ERROR] Failed to start GUI.
    echo Please check python_env folder.
    pause
)
