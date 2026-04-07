#!/bin/bash

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

# 设置内置环境路径
PYTHON_ENV_DIR="$PROJECT_DIR/python_env"
FFMPEG_DIR="$PROJECT_DIR/ffmpeg/ubuntu"

# 检测系统类型
if [[ "$OSTYPE" == "darwin"* ]]; then
    FFMPEG_DIR="$PROJECT_DIR/ffmpeg/mac"
fi

# 将FFmpeg添加到PATH
export PATH="$FFMPEG_DIR:$PATH"

# 查找Python可执行文件
if [ -f "$PYTHON_ENV_DIR/bin/python3" ]; then
    PYTHON_EXE="$PYTHON_ENV_DIR/bin/python3"
elif [ -f "$PYTHON_ENV_DIR/bin/python" ]; then
    PYTHON_EXE="$PYTHON_ENV_DIR/bin/python"
elif [ -f "$PYTHON_ENV_DIR/python.exe" ]; then
    PYTHON_EXE="$PYTHON_ENV_DIR/python.exe"
else
    echo "[错误] 未找到内置Python环境"
    echo "请确保 python_env 目录存在且包含Python"
    read -p "按回车键退出..."
    exit 1
fi

echo "启动GUI界面..."
"$PYTHON_EXE" gui.py
if [ $? -ne 0 ]; then
    echo "[错误] 启动失败，请检查 python_env 目录是否完整"
    read -p "按回车键退出..."
fi
