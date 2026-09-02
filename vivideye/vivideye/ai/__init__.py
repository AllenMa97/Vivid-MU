"""VividEye AI 能力层：Prompt Engineering + 在线 LLM/VLM/生成 API。

公开接口：
    from vivideye.ai import (
        LLMClient, llm_client,          # 统一多模态客户端
        judge_segment, enhance_highlight,  # 高光判分智能体
        generate_daily_digest,          # 每日日报
        generate_image, generate_audio, generate_video_stub,  # 在线生成
    )
"""

from vivideye.ai.clients import (
    DEFAULT_AUDIO_FALLBACKS,
    DEFAULT_FAST_FALLBACKS,
    DEFAULT_TEXT_FALLBACKS,
    DEFAULT_VISION_FALLBACKS,
    VividEyeAIError,
    LLMClient,
    llm_client,
)
from vivideye.ai.prompts import (
    cover_image_prompt,
    daily_story_prompt,
    enhance_title_prompt,
    highlight_judge_prompt,
)
from vivideye.ai.highlight_agent import (
    enhance_highlight,
    extract_frames,
    judge_segment,
)
from vivideye.ai.digest import generate_daily_digest
from vivideye.ai.generation import (
    generate_audio,
    generate_image,
    generate_video_stub,
)

__all__ = [
    "LLMClient",
    "llm_client",
    "VividEyeAIError",
    "DEFAULT_VISION_FALLBACKS",
    "DEFAULT_AUDIO_FALLBACKS",
    "DEFAULT_TEXT_FALLBACKS",
    "DEFAULT_FAST_FALLBACKS",
    "highlight_judge_prompt",
    "daily_story_prompt",
    "cover_image_prompt",
    "enhance_title_prompt",
    "judge_segment",
    "enhance_highlight",
    "extract_frames",
    "generate_daily_digest",
    "generate_image",
    "generate_audio",
    "generate_video_stub",
]
