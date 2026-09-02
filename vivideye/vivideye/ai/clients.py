"""统一的在线 LLM/VLM/音频模型客户端。

基于 openai>=1.0 SDK（OpenAI-compatible 接口），DashScope 的
compatible-mode（https://dashscope.aliyuncs.com/compatible-mode/v1）
也直接兼容，无需切换 SDK。

特性：
    - 模型回退链：vision / audio / text / fast 四类各配默认回退，
      主模型失败（超时 / 限流 / 模型不存在等）时自动降级。
    - 超时重试：每个模型重试 ai.max_retries 次，指数退避。
    - 并发控制：threading.Semaphore(ai.max_concurrent_requests)。

用法：
    from vivideye.ai.clients import llm_client
    text = llm_client.chat("你好")
    text = llm_client.chat_with_images("描述图片", ["/tmp/a.jpg"])
    text = llm_client.chat_with_audio("这段音频说了什么", "/tmp/a.wav")
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import threading
import time
from pathlib import Path
from typing import List, Optional, Sequence

from openai import (
    OpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from vivideye.config import config

logger = logging.getLogger(__name__)

# 扩展名 -> MIME（mimetypes 猜不到时的兜底表，参考旧实现）
_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
}

# 这些错误重试没有意义：直接换下一个模型
_NO_RETRY_ERRORS = (
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    BadRequestError,
)

# 各类模型的默认回退链（去重后使用，config 里配置的模型排在最前）
DEFAULT_VISION_FALLBACKS = ["qwen3-vl-flash", "qwen-vl-plus", "qwen-vl-max"]
DEFAULT_AUDIO_FALLBACKS = ["qwen-audio-turbo", "qwen2-audio-instruct"]
DEFAULT_TEXT_FALLBACKS = ["qwen3-max", "qwen-plus", "qwen-turbo"]
DEFAULT_FAST_FALLBACKS = ["qwen-flash", "qwen-turbo"]


class VividEyeAIError(RuntimeError):
    """AI 能力层统一异常。"""


def _mime_type(file_path: str) -> str:
    """猜测文件 MIME 类型。"""
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        return mime
    return _MIME_MAP.get(Path(file_path).suffix.lower(), "application/octet-stream")


def _to_data_url(file_path: str) -> str:
    """本地文件编码为 data URL（base64）。"""
    data = base64.b64encode(Path(file_path).read_bytes()).decode("utf-8")
    return f"data:{_mime_type(file_path)};base64,{data}"


def _audio_format(file_path: str) -> str:
    """OpenAI input_audio 支持的格式（wav/mp3）。"""
    ext = Path(file_path).suffix.lower()
    return "mp3" if ext in (".mp3", ".m4a", ".aac") else "wav"


def _dedup_chain(primary: Optional[str], defaults: Sequence[str]) -> List[str]:
    """主模型 + 默认回退链，去重并保持顺序。"""
    chain: List[str] = []
    for m in [primary, *defaults]:
        if m and m not in chain:
            chain.append(m)
    return chain


class LLMClient:
    """OpenAI-compatible 统一多模态客户端（同步）。"""

    def __init__(self):
        self.api_key: str = str(config.get("ai.api_key", "") or "").strip()
        self.api_base: str = str(
            config.get("ai.api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        ).rstrip("/")
        self.provider: str = str(config.get("ai.provider", "dashscope"))
        self.timeout: float = float(config.get("ai.request_timeout", 60))
        self.max_retries: int = int(config.get("ai.max_retries", 3))
        self.max_concurrent: int = int(config.get("ai.max_concurrent_requests", 5))

        # 各类模型回退链
        self.vision_chain = _dedup_chain(config.get("ai.vision_model"), DEFAULT_VISION_FALLBACKS)
        self.audio_chain = _dedup_chain(config.get("ai.audio_model"), DEFAULT_AUDIO_FALLBACKS)
        self.text_chain = _dedup_chain(config.get("ai.text_model"), DEFAULT_TEXT_FALLBACKS)
        self.fast_chain = _dedup_chain(config.get("ai.fast_model"), DEFAULT_FAST_FALLBACKS)

        # 并发信号量（同步调用，用 threading 而非 asyncio）
        self._semaphore = threading.Semaphore(self.max_concurrent)

        self._client: Optional[OpenAI] = None

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    def _ensure_client(self) -> OpenAI:
        """惰性创建 OpenAI 客户端；API key 缺失时抛出带配置指引的异常。"""
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise VividEyeAIError(
                "未配置 AI API key（ai.api_key）。请在 user_config.yaml 的 ai 段设置 api_key，"
                "或设置环境变量 VIVIDEYE_AI__API_KEY。"
                "DashScope key 可在 https://bailian.console.aliyun.com/ 获取。"
            )
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.timeout,
            max_retries=0,  # 重试由本类自己控制
        )
        return self._client

    def _request_once(self, messages: list, model: str, **kwargs) -> str:
        """单次 chat 请求，返回文本内容。"""
        client = self._ensure_client()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            timeout=self.timeout,
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    def _request_with_fallback(self, messages: list, chain: Sequence[str], **kwargs) -> str:
        """带模型回退 + 超时重试的请求。

        返回首个成功模型的文本内容；全部失败时抛出 VividEyeAIError。
        """
        last_err: Optional[Exception] = None
        for model in chain:
            for attempt in range(self.max_retries):
                try:
                    with self._semaphore:
                        content = self._request_once(messages, model, **kwargs)
                    if not content or not content.strip():
                        raise VividEyeAIError(f"模型 {model} 返回空内容")
                    return content
                except _NO_RETRY_ERRORS as e:
                    # 鉴权 / 参数 / 模型不存在：重试无意义，直接换模型
                    last_err = e
                    logger.warning("模型 %s 请求失败（不重试）：%s", model, e)
                    break
                except (APIConnectionError, APITimeoutError, RateLimitError,
                        InternalServerError, APIStatusError, VividEyeAIError) as e:
                    last_err = e
                    wait = min(2 ** attempt, 8)
                    logger.warning(
                        "模型 %s 第 %d/%d 次尝试失败：%s（%ds 后重试）",
                        model, attempt + 1, self.max_retries, e, wait,
                    )
                    time.sleep(wait)
                except Exception as e:  # 其余未知异常也走重试
                    last_err = e
                    logger.warning("模型 %s 请求异常：%s", model, e)
                    time.sleep(1)
            # for attempt
        # for model
        raise VividEyeAIError(f"所有模型均请求失败（链：{list(chain)}）：{last_err}")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def chat(self, text: str, system: Optional[str] = None,
             model: Optional[str] = None, temperature: float = 0.3,
             max_tokens: int = 2048) -> str:
        """纯文本对话。

        model 传入时会优先使用该模型（仍会回退到 text 链其余模型）。
        """
        chain = _dedup_chain(model, self.text_chain)
        messages: list = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": text})
        return self._request_with_fallback(messages, chain,
                                           temperature=temperature, max_tokens=max_tokens)

    def chat_fast(self, text: str, system: Optional[str] = None,
                  temperature: float = 0.4, max_tokens: int = 512) -> str:
        """快速模型对话（轻量任务：标题打磨、简单分类等）。"""
        messages: list = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": text})
        return self._request_with_fallback(messages, self.fast_chain,
                                           temperature=temperature, max_tokens=max_tokens)

    def chat_with_images(self, text: str, image_paths: Sequence[str],
                         system: Optional[str] = None,
                         model: Optional[str] = None,
                         temperature: float = 0.2, max_tokens: int = 1024) -> str:
        """图文多模态对话（VLM）。

        image_paths: 本地图片路径列表，自动 base64 编码。
        """
        chain = _dedup_chain(model, self.vision_chain)
        content: list = []
        for p in image_paths:
            path = Path(p)
            if not path.is_file():
                logger.warning("图片不存在，跳过：%s", p)
                continue
            content.append({
                "type": "image_url",
                "image_url": {"url": _to_data_url(str(path))},
            })
        content.append({"type": "text", "text": text})
        if not any(c["type"] == "image_url" for c in content):
            raise VividEyeAIError(f"没有可用的图片输入：{list(image_paths)}")

        messages: list = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        return self._request_with_fallback(messages, chain,
                                           temperature=temperature, max_tokens=max_tokens)

    def chat_with_audio(self, text: str, audio_path: str,
                        system: Optional[str] = None,
                        model: Optional[str] = None,
                        temperature: float = 0.2, max_tokens: int = 1024) -> str:
        """音频多模态对话（音频理解）。

        audio_path: 本地音频文件，base64 后走 input_audio 输入。
        """
        path = Path(audio_path)
        if not path.is_file():
            raise VividEyeAIError(f"音频文件不存在：{audio_path}")
        data = base64.b64encode(path.read_bytes()).decode("utf-8")
        chain = _dedup_chain(model, self.audio_chain)
        content = [
            {
                "type": "input_audio",
                "input_audio": {"data": data, "format": _audio_format(audio_path)},
            },
            {"type": "text", "text": text},
        ]
        messages: list = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        return self._request_with_fallback(messages, chain,
                                           temperature=temperature, max_tokens=max_tokens)

    @property
    def has_api_key(self) -> bool:
        """是否已配置 API key。"""
        return bool(self.api_key)


# 模块级单例（import 时不发起网络请求，无 key 也不报错，调用时才抛异常）
llm_client = LLMClient()
