# VividMU - 智能视频处理管道

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

VividMU 是一个智能化的视频处理管道，通过三个步骤从原始视频中提取最有价值的内容，特别适用于长时间视频的自动剪辑和摘要生成。

## ✨ 特色功能

- **自适应处理**: 根据视频内容自动调整处理策略
- **多策略支持**: 支持社交、事件、探索等多种处理策略
- **智能评分**: 多维度评分系统确保最佳内容被保留
- **高效处理**: 并行处理架构提高处理效率
- **AI驱动**: 先进的AI模型提供内容理解和编辑能力
- **时间序列理解**: 不仅分析单帧，还理解视频的时间序列特征
- **宠物表情识别**: 专门优化识别宠物表情丰富的时刻
- **情感峰值检测**: 识别视频中最吸引人的关键时刻

## 🚀 快速开始

### 环境要求
- Windows、macOS 或 Linux
- 至少 8GB RAM（推荐 16GB+）
- 本项目已预配置Python环境和FFmpeg，无需额外安装

### 启动方式

#### Windows系统
- **命令行启动**: 双击 `Windows-命令行启动.bat`
- **GUI界面**: 双击 `Windows-GUI启动.bat`

#### macOS/Linux系统
- **命令行启动**: 运行 `Mac-Linux启动.sh`
- **GUI界面**: 运行 `Mac-Linux-GUI启动.sh`

#### 手动启动
如果需要手动启动，可以运行：
```bash
pip install -r requirements.txt
python main.py
```

### 配置项目
1. 复制配置模板：
   ```bash
   cp config_template.txt user_config.txt
   ```

2. 编辑 `user_config.txt`，将 `ALIYUN_API_KEY=YOUR_API_KEY_HERE` 替换为您的阿里云API密钥

3. 根据需要修改其他配置参数

> **注意**: `user_config.txt` 已添加到 `.gitignore` 中，您的敏感信息不会被意外提交到Git仓库。

### 运行处理
1. 将视频文件放入 `data/input/` 目录
2. 运行主程序：
   ```bash
   python main.py
   ```
3. 查看 `data/output/step3_output/` 目录获取最终视频

## 🔧 配置说明

项目的主要配置参数现在统一存储在根目录的配置文件中，包括：

- **API配置**: 阿里云DashScope API的密钥和端点
- **模型配置**: 各种AI模型的设置和回退列表
- **处理参数**: 过滤器参数、异步处理配置等
- **输出配置**: 输出格式和语言设置

### 配置文件说明

- `config_template.txt`: 配置文件模板，包含所有配置项的默认值（不含敏感信息）
- `user_config.txt`: 用户私有的配置文件（**不会被提交到Git**），用于覆盖默认配置

## 🏗️ 架构概览

```
原始视频 → Step 1 (粗粒度过滤) → Step1输出文件夹 → 
Step 2 (语义过滤) → Step2输出文件夹 → 
Step 3 (AI编辑) → Step3输出文件夹 → 最终视频
```

### Step 1: 粗粒度过滤器 (Coarse Filter)
- **目的**: 从原始视频中快速识别和提取有意义的片段
- **功能**: 帧特征提取、音频特征提取、片段分割、初步评分、去重处理、稳定性检测
- **输出**: `data/output/step1_output/`

### Step 2: 语义过滤器 (Semantic Filter)
- **目的**: 对 Step 1 输出的片段进行更深入的语义分析
- **功能**: 人脸识别、语音检测(VAD)、场景多样性分析、**YOLO 物体级语义特征(宠物/人物/互动)**、语义总结
- **神经网络特征层**: YOLO 目标检测 / CLIP 场景语义 / Silero VAD 通过 `src/models/` 下的 ONNX 模型推理, 全部经 `src/device_manager.py` 在 **CPU/NPU/GPU** 上自动调度
- **输出**: `data/output/step2_output/`

### Step 3: AI视频编辑器 (AI Video Editor)
- **目的**: 将 Step 2 精选的片段组合成最终的高质量视频
- **功能**: 智能转场、AI增强、动态剪辑、音效处理、特效应用、节奏同步
- **输出**: `data/output/step3_output/`

## 📁 目录结构

```
project_root/
├── config_template.txt    # 配置文件模板
├── user_config.txt        # 用户配置文件（本地，不提交）
├── USAGE_GUIDE.md         # 详细使用手册
├── setup_config.bat       # Windows配置向导
├── setup_config.sh        # Linux/macOS配置向导
├── data/
│   ├── input/             # 输入视频文件
│   └── output/            # 输出结果
│       ├── step1_output/  # Step 1 输出
│       ├── step2_output/  # Step 2 输出
│       └── step3_output/  # Step 3 输出
├── src/                   # 源代码
│   ├── coarse_filter.py   # Step 1 实现
│   ├── semantic_summarizer.py # Step 2 实现
│   ├── step3_editor.py    # Step 3 实现
│   └── ai_video_editor.py # AI视频编辑器
├── gui.py                 # GUI界面
├── main.py                # 主程序入口
├── step2_main.py          # Step 2 入口
├── step3_main.py          # Step 3 入口
└── README.md              # 本文件
```

## 🛠️ 技术栈

- Python 3.x
- OpenCV - 计算机视觉处理
- Librosa - 音频分析
- FFmpeg - 视频编解码
- 阿里云视觉语言模型 - AI内容理解
- ImageHash - 图像相似度检测
- Pillow - 图像处理
- httpx - API请求处理
- ONNX Runtime - 神经网络特征层推理 (YOLO / CLIP / Silero VAD)

## 🧠 神经网络特征层与多设备调度

Step 2 的语义特征来自本地 ONNX 模型推理 (不再仅靠启发式或云端 LLM):

| 模型 | 产出特征 | 设备偏好 (可在 user_config.txt 覆盖) |
|---|---|---|
| YOLOv8n 目标检测 | 宠物在场、宠物数量、人宠互动、玩具、动作强度 | NPU/GPU (INT8) |
| CLIP 场景语义 *(可选)* | 真实语义多样性 / 独特性 embedding | GPU |
| Silero VAD | 语音活动检测 | CPU |
| OpenCV 人脸检测 | 人脸数量/大小/居中 | 自动 |

**多设备调度** (`src/device_manager.py`):
- 启动时自动探测 ONNX Runtime (CUDA/DirectML/CoreML/TensorRT) 与 OpenVINO (CPU/iGPU/NPU)
- 按模型分组策略选择目标设备, 推理失败自动降级到 CPU (保证纯 CPU 也能跑)
- 下载模型: `python download_models.py` (YOLO/人脸/VAD); 可选 CLIP: `python download_models.py --with-clip`

**选择算法** (`src/selector.py`): 支持带约束的选择 (总时长背包 + 片段最小间隔 + 语义多样性去重), 新增 `pet` 策略优先保留宠物行为/人宠互动丰富的场景。

## 📖 详细使用说明

- [USAGE_GUIDE.md](USAGE_GUIDE.md) - 详细使用手册
- [LAUNCH_GUIDE.md](LAUNCH_GUIDE.md) - 启动脚本说明

## 🛡️ 安全说明

- API密钥存储在本地 `user_config.txt` 文件中，不会被提交到Git仓库
- 所有敏感配置文件均已加入 `.gitignore`
- 上传到云端API的视频数据遵循服务商的隐私政策

## 💻 环境配置

本项目已经预配置了所需的Python环境和FFmpeg工具，用户无需额外安装：

- **Python环境**: 项目根目录包含完整的Python虚拟环境
- **FFmpeg**: 已内置FFmpeg工具，支持多种视频格式处理
- **依赖包**: 所需的Python包已在requirements.txt中列出

## 👥 作者团队

### 主要开发者

**Zhiyong Ma (马智勇)**   
- 📧 主要开发者 & 项目负责人
- 🌐 `https://www.zhihu.com/people/allenma-49`
- 🎓 `https://scholar.google.com/citations?user=Brs63a8AAAAJ&hl=en`

### 核心贡献者

<!-- 预留位置：其他 N 位团队成员信息 -->

## 🤝 贡献

欢迎提交 Issue 和 Pull Request 来改进项目！

## 📄 许可证

请参阅项目许可证文件。