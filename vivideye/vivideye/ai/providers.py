"""Provider 差异薄封装（dashscope / openai / compatible）。

只做 base_url / 端点 / 各家默认模型链的差异处理，不涉及 HTTP 请求本身。

base_url 规则（resolve_base_url，返回值不带尾斜杠）：
    - dashscope：
        * api_base 为空            -> 官方 compatible-mode 默认地址
        * 已含 compatible-mode     -> 原样使用（去尾斜杠）
        * 其他（只给域名，或误配了原生 /api/v1 等路径）
                                   -> 自动重写为 <scheme://host>/compatible-mode/v1
    - openai  ：为空用官方默认，否则原样
    - compatible：必须显式配置 ai.api_base，否则返回空串（由调用方给出明确错误）
"""

from __future__ import annotations

from urllib.parse import urlsplit

# 各 provider 的默认 base_url
DEFAULT_BASE_URLS: dict[str, str] = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
    "compatible": "",  # 兼容模式：必须显式配置 ai.api_base
}

# 各 provider 默认文生图模型链：(模型名, size)；DashScope 用星号、OpenAI 用 x
DEFAULT_IMAGE_MODELS: dict[str, list[tuple[str, str]]] = {
    "dashscope": [
        ("wanx2.1-t2i-turbo", "1024*1024"),
        ("wanx2.0-t2i-turbo", "1024*1024"),
    ],
    "default": [
        ("gpt-image-1", "1024x1024"),
        ("dall-e-3", "1024x1024"),
    ],
}


def normalize_provider(provider: str | None) -> str:
    """规范 provider 名；未知名一律按 compatible（通用 OpenAI 兼容）处理。"""
    p = str(provider or "").strip().lower()
    return p if p in ("dashscope", "openai", "compatible") else "compatible"


def resolve_base_url(provider: str | None, api_base: str | None = None) -> str:
    """解析最终 base_url（无尾斜杠；配置不全时返回空串，不抛异常）。"""
    p = normalize_provider(provider)
    base = str(api_base or "").strip().rstrip("/")

    if p == "dashscope":
        if not base:
            return DEFAULT_BASE_URLS["dashscope"]
        if "compatible-mode" in base:  # 已带 compatible-mode 后缀：原样使用
            return base
        # 只给了域名，或误配了原生 API 路径（如 /api/v1）：
        # 统一重写为 <scheme://host>/compatible-mode/v1
        parts = urlsplit(base)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}/compatible-mode/v1"
        return f"{base}/compatible-mode/v1"

    if p == "openai":
        return base or DEFAULT_BASE_URLS["openai"]

    # compatible：必须显式给 ai.api_base
    return base


def chat_url(base_url: str) -> str:
    """chat/completions 端点（OpenAI 兼容格式）。"""
    return f"{str(base_url).rstrip('/')}/chat/completions"


def images_url(base_url: str) -> str:
    """/images/generations 端点（OpenAI 格式；DashScope compatible-mode 同构）。"""
    return f"{str(base_url).rstrip('/')}/images/generations"


def default_image_models(provider: str | None) -> list[tuple[str, str]]:
    """各 provider 默认文生图模型链 [(model, size), ...]。"""
    p = normalize_provider(provider)
    return list(DEFAULT_IMAGE_MODELS.get(p, DEFAULT_IMAGE_MODELS["default"]))
