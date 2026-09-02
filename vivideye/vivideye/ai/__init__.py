"""VividEye AI 能力层（requests 同步实现，PHONE-FIRST / Termux 友好）。

公开接口：
    from vivideye.ai import AIClient, AIClientError, ai_client
    from vivideye.ai import prompts, providers

    ai_client.analyze_frames(frames_b64, audio_path, scene_mode)  # 高光判分（帧+音频）
    ai_client.daily_summary(highlights)                           # 日报温暖文案
    ai_client.generate_image(prompt, out_path)                    # 文生图（海报）
    ai_client.test_connection()                                   # 快速自检

约定：高层方法永不抛异常（失败返回错误 dict / 空串 / None）；
仅底层 chat() 会抛 AIClientError。
"""

from vivideye.ai import prompts, providers
from vivideye.ai.client import AIClient, AIClientError, ai_client

__all__ = ["AIClient", "AIClientError", "ai_client", "prompts", "providers"]
