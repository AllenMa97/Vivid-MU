# ========================================
# Step 2 Filter Configuration
# ========================================

# Aliyun API Configuration
ALIYUN_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
ALIYUN_API_KEY = "sk-b1970ac645544293822abf992831f35d"

# Model Configuration
VISION_MODEL = "qwen3-vl-flash"        # Visual Language Model for video understanding
AUDIO_MODEL = "qwen-audio-turbo"       # Audio understanding model
TEXT_MODEL = "qwen3-max"               # LLM for final selection
FAST_MODEL = "qwen-flash"              # Fast model for simple tasks

# Model Fallback Lists
VISION_MODEL_FALLBACKS = [
    "qwen3-vl-plus", 
    "qwen3-vl-flash",
    "qwen3-vl-flash-2026-01-22",
    "qwen-vl-plus",
    "qwen-vl-max",
    "qwen3.5-vl-plus"
]

AUDIO_MODEL_FALLBACKS = [
    "qwen-audio-turbo",
    "qwen-audio-turbo-latest",
    "qwen3-omni-flash",
    "qwen3-omni-flash-2025-09-15",
    "qwen3-omni-flash-2025-12-01",
    "qwen3-omni-flash-realtime"
]

VIDEO_MODEL_FALLBACKS = [
    "qwen3.5-omni-plus",
    "qwen3-omni-flash",
    "qwen3.5-omni-max",
    "qwen3.5-omni-turbo",
    "qwen3-omni-flash-2025-09-15",
    "qwen3-omni-flash-2025-12-01",
    "qwen3-omni-flash-realtime"
]

TEXT_MODEL_FALLBACKS = [
    "qwen3-max",
    "qwen3-max-preview",
    "qwen3-max-2025-09-23",
    "qwen3-max-2026-01-23",
    "qwen-flash",
    "qwen-flash-2025-07-28",
    "qwen3.5-flash"
]

FAST_MODEL_FALLBACKS = [
    "qwen-flash",
    "qwen-flash-2025-07-28",
    "qwen3.5-flash"
]

# Step 2 Filter Parameters
MAX_SEGMENTS_TO_PROCESS = 100          # Max segments to process in step 2
SELECTION_STRICTNESS = "high"          # high, medium, low
MIN_KEEP_RATIO = 0.1                   # Minimum ratio of segments to keep
MAX_KEEP_RATIO = 0.3                   # Maximum ratio of segments to keep
NUM_SELECTION_SCHEMES = 3              # Number of selection schemes to generate

# Async Processing
MAX_CONCURRENT_REQUESTS = 5            # Max concurrent API requests
REQUEST_TIMEOUT = 60                   # API request timeout in seconds
MAX_RETRIES = 3                        # Max retries for failed requests

# Output Configuration
SUMMARY_MAX_LENGTH = 200               # Max length of each segment summary
OUTPUT_LANGUAGE = "zh_CN"              # Output language for summaries
