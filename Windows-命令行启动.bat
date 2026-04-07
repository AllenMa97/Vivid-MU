@echo off
chcp 65001 >nul
echo ========================================
echo Video Highlight Extractor
echo ========================================
echo.

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%python_env\python.exe"
set "FFMPEG_DIR=%PROJECT_DIR%ffmpeg\windows\bin"
set "PATH=%FFMPEG_DIR%;%PATH%"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found: %PYTHON_EXE%
    echo Please make sure python_env folder exists.
    pause
    exit /b 1
)

if not exist "%FFMPEG_DIR%\ffmpeg.exe" (
    echo [WARNING] FFmpeg not found: %FFMPEG_DIR%\ffmpeg.exe
    echo Video merge will be disabled.
    echo.
)

echo [INFO] Using Python: %PYTHON_EXE%
echo [INFO] Using FFmpeg: %FFMPEG_DIR%
echo.

if not exist "src\models\deploy.prototxt" (
    echo [INFO] Downloading models...
    "%PYTHON_EXE%" download_models.py
)

echo [INFO] Processing video...
echo.
"%PYTHON_EXE%" main.py

echo.
echo ========================================
echo Done! Check data/output folder.
echo ========================================
pause
