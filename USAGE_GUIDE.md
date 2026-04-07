# VividMU 使用手册

## 项目概述

VividMU 是一个智能化的视频处理管道，通过三个步骤从原始视频中提取最有价值的内容，特别适用于长时间视频的自动剪辑和摘要生成。

## 快速开始

### 1. 环境准备

#### 系统要求
- Windows、macOS 或 Linux
- 至少 8GB RAM（推荐 16GB+）
- 足够的磁盘空间存储视频文件
- 本项目已预配置Python环境和FFmpeg，无需额外安装

### 2. 启动方式

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

### 2. 配置项目

#### 2.1 设置 API 密钥
1. 复制配置模板：
   ```bash
   cp config_template.txt user_config.txt
   ```

2. 编辑 `user_config.txt` 文件，将 `ALIYUN_API_KEY=YOUR_API_KEY_HERE` 替换为您的阿里云API密钥：
   ```
   ALIYUN_API_KEY=your_actual_api_key_here
   ```

3. 根据需要调整其他配置参数

> **注意**：`user_config.txt` 已被添加到 `.gitignore`，您的API密钥不会被提交到Git仓库。

#### 2.2 使用配置向导（可选）
Windows用户可以运行：
```bash
setup_config.bat
```

Linux/macOS用户可以运行：
```bash
chmod +x setup_config.sh
./setup_config.sh
```

### 3. 准备视频数据

#### 3.1 视频存放位置
- **输入视频**：将待处理的视频文件放入 `data/input/` 目录
- **输出结果**：处理后的视频将保存在 `data/output/` 目录的不同子文件夹中

#### 3.2 支持的视频格式
- MP4 (.mp4)
- MKV (.mkv)
- AVI (.avi)
- MOV (.mov)
- 其他常见视频格式

#### 3.3 视频文件组织建议
```
data/
├── input/                 # 待处理的视频文件
│   ├── video1.mp4
│   ├── video2.mov
│   └── folder1/
│       └── video3.mp4
└── output/                # 处理结果
    ├── step1_output/      # Step 1 结果
    ├── step2_output/      # Step 2 结果
    └── step3_output/      # Step 3 结果（最终视频）
```

### 4. 运行处理管道

#### 4.1 完整处理流程
```bash
python main.py
```

#### 4.2 分步处理
如果需要单独运行某个步骤：

- **Step 1 (粗粒度过滤)**：
  ```bash
  python main.py
  ```
  （此步骤会自动执行，或在源代码中单独调用）

- **Step 2 (语义过滤)**：
  ```bash
  python step2_main.py
  ```

- **Step 3 (AI编辑)**：
  ```bash
  python step3_main.py
  ```

#### 4.3 GUI界面（可选）
```bash
python gui.py
```

### 5. 处理流程详解

#### Step 1: 粗粒度过滤器
- **目的**：从原始视频中快速识别和提取有意义的片段
- **功能**：
  - 帧特征提取（dHash、直方图）
  - 音频特征提取（响度、零交叉率等）
  - 片段分割和初步评分
  - 去重和稳定性检测
- **输出**：`data/output/step1_output/`

#### Step 2: 语义过滤器
- **目的**：对Step 1输出的片段进行语义分析
- **功能**：
  - 人脸识别和评分
  - 语音检测和分析
  - 场景多样性评估
  - 语义总结生成
- **输出**：`data/output/step2_output/`

#### Step 3: AI视频编辑器
- **目的**：将精选片段组合成最终视频
- **功能**：
  - 智能转场效果
  - AI增强处理
  - 音效处理
  - 特效应用
- **输出**：`data/output/step3_output/`（最终结果）

## 配置选项详解

### API配置
- `ALIYUN_API_BASE`: 阿里云API端点
- `ALIYUN_API_KEY`: 您的API密钥

### 模型配置
- `VISION_MODEL`: 视觉语言模型
- `AUDIO_MODEL`: 音频理解模型
- `TEXT_MODEL`: 文本处理模型
- `FAST_MODEL`: 快速处理模型

### 处理参数
- `MAX_SEGMENTS_TO_PROCESS`: 最大处理片段数
- `SELECTION_STRICTNESS`: 选择严格度（high/medium/low）
- `MIN_KEEP_RATIO`/`MAX_KEEP_RATIO`: 保留片段比例范围
- `NUM_SELECTION_SCHEMES`: 生成方案数量

## 故障排除

### 常见问题

1. **API密钥错误**
   - 确认 `user_config.txt` 中的API密钥正确
   - 检查API密钥是否过期或达到使用限制

2. **内存不足**
   - 减少同时处理的视频数量
   - 降低视频分辨率或压缩视频

3. **依赖包缺失**
   - 重新运行 `pip install -r requirements.txt`
   - 检查Python版本是否兼容

4. **视频格式不支持**
   - 确认视频格式在支持列表中
   - 尝试转换视频格式

### 日志查看
处理日志通常保存在 `logs/` 目录中（如果存在）。

## 安全注意事项

1. **API密钥安全**
   - 不要在代码中硬编码API密钥
   - 确保 `user_config.txt` 在 `.gitignore` 中
   - 定期轮换API密钥

2. **数据隐私**
   - 上传到云端API的视频数据遵循服务商的隐私政策
   - 敏感视频建议本地处理

## 性能优化

1. **硬件加速**
   - 确保安装了GPU版本的依赖包（如适用）
   - 启用CUDA支持（如适用）

2. **批处理**
   - 合理安排视频处理顺序
   - 避免同时处理过多大文件

## 技术支持

如遇到问题，请检查：
1. 详细阅读错误信息
2. 查看日志文件
3. 确认配置正确
4. 检查网络连接

如有进一步问题，请联系项目维护者或查阅官方文档。

## 作者信息

### 主要开发者

**Zhiyong Ma (马智勇)**   
- 📧 主要开发者 & 项目负责人
- 🌐 `https://www.zhihu.com/people/allenma-49`
- 🎓 `https://scholar.google.com/citations?user=Brs63a8AAAAJ&hl=en`

### 核心贡献者

<!-- 预留位置：其他 N 位团队成员信息 -->