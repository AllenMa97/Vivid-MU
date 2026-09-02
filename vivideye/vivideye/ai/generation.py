"""在线生成 API 封装：文生图 / 语音合成 / 视频生成（预留）。

全部接口 try/except 优雅降级：失败返回 None 并写日志，绝不抛异常。

    generate_image(prompt, out_path) -> Optional[str]  生成成功返回文件路径
    generate_audio(text) -> Optional[str]              TTS 成功返回音频路径
    generate_video_stub() -> None                      预留接口（TODO）
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from vivideye.ai.clients import llm_client
from vivideye.config import config

logger = logging.getLogger(__name__)

# 通义万相走 DashScope compatible-mode 的 /images/generations（OpenAI 格式）；
# 其他 provider 降级用 OpenAI images API。
_IMAGE_MODEL_CHAIN = {
    "dashscope": [
        ("wanx2.1-t2i-turbo", "1024*1024"),   # DashScope size 用星号
        ("wanx2.0-t2i-turbo", "1024*1024"),
    ],
    "default": [
        ("gpt-image-1", "1024x1024"),
        ("dall-e-3", "1024x1024"),
    ],
}

# TTS 模型链（DashScope qwen-tts 走 /audio/speech 的 OpenAI 兼容接口）
_TTS_MODEL_CHAIN = {
    "dashscope": [("qwen-tts", "Cherry")],
    "default": [("tts-1", "alloy")],
}


def generate_image(prompt: str, out_path: str) -> Optional[str]:
    """文生图：优先通义万相 wanx2.1-t2i-turbo，失败降级 OpenAI images API。

    成功保存到 out_path 并返回路径；失败返回 None。
    """
    if not config.get("ai.image_gen_enabled", True):
        logger.info("图像生成未启用（ai.image_gen_enabled=false），跳过")
        return None
    try:
        chain = _IMAGE_MODEL_CHAIN.get(
            str(config.get("ai.provider", "dashscope")).lower(),
            _IMAGE_MODEL_CHAIN["default"],
        )
        client = llm_client._ensure_client()

        for model, size in chain:
            try:
                resp = client.images.generate(
                    model=model, prompt=prompt, n=1, size=size,
                    timeout=llm_client.timeout,
                )
                item = resp.data[0] if resp.data else None
                if item is None:
                    raise RuntimeError(f"{model} 返回空结果")
                out = Path(out_path)
                out.parent.mkdir(parents=True, exist_ok=True)
                if getattr(item, "b64_json", None):
                    out.write_bytes(base64.b64decode(item.b64_json))
                elif getattr(item, "url", None):
                    with httpx.Client(timeout=llm_client.timeout, follow_redirects=True) as hc:
                        r = hc.get(item.url)
                        r.raise_for_status()
                        out.write_bytes(r.content)
                else:
                    raise RuntimeError(f"{model} 响应里既无 b64_json 也无 url")

                # 用 Pillow 校验确实是有效图片（顺带统一转存为目标格式）
                from PIL import Image
                with Image.open(out) as im:
                    im.verify()
                with Image.open(out) as im:
                    im.convert("RGB").save(out)
                logger.info("封面图生成成功：%s（model=%s）", out_path, model)
                return str(out)
            except Exception as e:
                logger.warning("图像生成失败（model=%s）：%s", model, e)

        logger.error("图像生成：所有模型均失败，prompt=%r", prompt[:80])
        return None
    except Exception as e:
        logger.error("generate_image 异常：%s", e)
        return None


def generate_audio(text: str) -> Optional[str]:
    """语音合成（TTS）：DashScope qwen-tts 或 OpenAI tts-1。

    成功返回音频文件路径（存于 data/digests/），失败返回 None。
    """
    if not config.get("ai.audio_gen_enabled", True):
        logger.info("语音生成未启用（ai.audio_gen_enabled=false），跳过")
        return None
    if not (text or "").strip():
        logger.warning("TTS 输入为空，跳过")
        return None
    try:
        chain = _TTS_MODEL_CHAIN.get(
            str(config.get("ai.provider", "dashscope")).lower(),
            _TTS_MODEL_CHAIN["default"],
        )
        client = llm_client._ensure_client()

        for model, voice in chain:
            try:
                resp = client.audio.speech.create(
                    model=model, voice=voice, input=text,
                    timeout=llm_client.timeout,
                )
                out = config.data_path("digests", f"digest_tts_{int(time.time())}.mp3")
                # openai SDK 的 speech 响应可直接写文件（兼容 bytes / write_to_file）
                content = getattr(resp, "content", None)
                if content:
                    out.write_bytes(content)
                else:
                    resp.write_to_file(str(out))
                logger.info("语音合成成功：%s（model=%s）", out, model)
                return str(out)
            except Exception as e:
                logger.warning("语音合成失败（model=%s）：%s", model, e)

        logger.error("语音生成：所有模型均失败")
        return None
    except Exception as e:
        logger.error("generate_audio 异常：%s", e)
        return None


def generate_video_stub() -> None:
    """视频生成预留接口。

    TODO: 由具体厂商 API 填充（如可灵 / 通义万相 wan2.x-i2v / Seedance）。
    预期流程：提交任务 -> 轮询状态 -> 下载视频片段。
    当前仅打日志，返回 None。
    """
    if not config.get("ai.video_gen_enabled", False):
        logger.info("视频生成未启用（ai.video_gen_enabled=false），跳过")
        return None
    logger.info("generate_video_stub：视频生成接口尚未接入厂商 API（TODO）")
    return None
