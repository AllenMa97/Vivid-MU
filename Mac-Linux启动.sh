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
    exit 1
fi

echo "========================================"
echo "视频核心片段提取系统"
echo "========================================"
echo

# 检查FFmpeg
if [ ! -f "$FFMPEG_DIR/ffmpeg" ] && [ ! -f "$FFMPEG_DIR/bin/ffmpeg" ]; then
    echo "[警告] 未找到内置FFmpeg: $FFMPEG_DIR"
    echo "视频合并功能将不可用，请自行下载FFmpeg放入该目录"
    echo "下载地址: https://ffmpeg.org/download.html"
    echo
fi

echo "[信息] 使用内置Python: $PYTHON_EXE"
echo "[信息] 使用内置FFmpeg: $FFMPEG_DIR"
echo

# 检查模型文件
if [ ! -f "src/models/deploy.prototxt" ]; then
    echo "[提示] 正在下载模型文件..."
    "$PYTHON_EXE" download_models.py
fi

echo "[开始] 正在处理视频..."
echo
"$PYTHON_EXE" main.py

echo
echo "========================================"
echo "处理完成！请查看 data/output 目录"
echo "========================================"
