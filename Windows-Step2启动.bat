@echo off
chcp 65001 >nul
echo ========================================
echo Step 2 Filter - Semantic Analysis
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

echo [INFO] Using Python: %PYTHON_EXE%
echo.

echo [INFO] Running Step 2 Filter...
echo.
"%PYTHON_EXE%" step2_main.py

echo.
echo ========================================
echo Step 2 Filter Complete!
echo Check data/output/step2_results for results
echo ========================================
pause
