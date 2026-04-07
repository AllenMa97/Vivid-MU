# ========================================
# Step 2 Filter Configuration
# 从根目录的安全配置文件读取配置
# ========================================
import os
from pathlib import Path

# 优先从安全的配置文件读取，如果不存在则从模板文件读取
config_path = Path(__file__).parent.parent / "user_config.txt"
template_config_path = Path(__file__).parent.parent / "config_template.txt"

def read_config_value(key, default_value=None):
    """从配置文件中读取指定键的值，优先从user_config.txt读取，其次从config_template.txt读取"""
    # 首先尝试从user_config.txt读取
    for config_file in [config_path, template_config_path]:
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        k = k.strip()
                        v = v.strip()
                        if k == key:
                            # 处理列表类型的值
                            if ',' in v and ('MODEL_FALLBACKS' in key or key == 'supported_formats'):
                                return [item.strip() for item in v.split(',')]
                            # 处理数字类型的值
                            elif v.replace('.', '').isdigit():
                                return float(v) if '.' in v else int(v)
                            # 处理布尔类型的值
                            elif v.lower() in ['true', 'false']:
                                return v.lower() == 'true'
                            # 其他情况返回字符串
                            else:
                                return v
        except FileNotFoundError:
            continue
    
    return default_value

# Aliyun API Configuration
ALIYUN_API_BASE = read_config_value("ALIYUN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
ALIYUN_API_KEY = read_config_value("ALIYUN_API_KEY", "")

# Model Configuration
VISION_MODEL = read_config_value("VISION_MODEL", "qwen3-vl-flash")
AUDIO_MODEL = read_config_value("AUDIO_MODEL", "qwen-audio-turbo")
TEXT_MODEL = read_config_value("TEXT_MODEL", "qwen3-max")
FAST_MODEL = read_config_value("FAST_MODEL", "qwen-flash")

# Model Fallback Lists
VISION_MODEL_FALLBACKS = read_config_value("VISION_MODEL_FALLBACKS", [
    "qwen3-vl-plus", 
    "qwen3-vl-flash",
    "qwen3-vl-flash-2026-01-22",
    "qwen-vl-plus",
    "qwen-vl-max",
    "qwen3.5-vl-plus"
])

AUDIO_MODEL_FALLBACKS = read_config_value("AUDIO_MODEL_FALLBACKS", [
    "qwen-audio-turbo",
    "qwen-audio-turbo-latest",
    "qwen3-omni-flash",
    "qwen3-omni-flash-2025-09-15",
    "qwen3-omni-flash-2025-12-01",
    "qwen3-omni-flash-realtime"
])

VIDEO_MODEL_FALLBACKS = read_config_value("VIDEO_MODEL_FALLBACKS", [
    "qwen3.5-omni-plus",
    "qwen3-omni-flash",
    "qwen3.5-omni-max",
    "qwen3.5-omni-turbo",
    "qwen3-omni-flash-2025-09-15",
    "qwen3-omni-flash-2025-12-01",
    "qwen3-omni-flash-realtime"
])

TEXT_MODEL_FALLBACKS = read_config_value("TEXT_MODEL_FALLBACKS", [
    "qwen3-max",
    "qwen3-max-preview",
    "qwen3-max-2025-09-23",
    "qwen3-max-2026-01-23",
    "qwen-flash",
    "qwen-flash-2025-07-28",
    "qwen3.5-flash"
])

FAST_MODEL_FALLBACKS = read_config_value("FAST_MODEL_FALLBACKS", [
    "qwen-flash",
    "qwen-flash-2025-07-28",
    "qwen3.5-flash"
])

# Step 2 Filter Parameters
MAX_SEGMENTS_TO_PROCESS = read_config_value("MAX_SEGMENTS_TO_PROCESS", 100)
SELECTION_STRICTNESS = read_config_value("SELECTION_STRICTNESS", "high")
MIN_KEEP_RATIO = read_config_value("MIN_KEEP_RATIO", 0.1)
MAX_KEEP_RATIO = read_config_value("MAX_KEEP_RATIO", 0.3)
NUM_SELECTION_SCHEMES = read_config_value("NUM_SELECTION_SCHEMES", 3)

# Async Processing
MAX_CONCURRENT_REQUESTS = read_config_value("MAX_CONCURRENT_REQUESTS", 5)
REQUEST_TIMEOUT = read_config_value("REQUEST_TIMEOUT", 60)
MAX_RETRIES = read_config_value("MAX_RETRIES", 3)

# Output Configuration
SUMMARY_MAX_LENGTH = read_config_value("SUMMARY_MAX_LENGTH", 200)
OUTPUT_LANGUAGE = read_config_value("OUTPUT_LANGUAGE", "zh_CN")