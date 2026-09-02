"""AI 故事化日报：把当日高光列表交给大模型生成温暖日记体正文。

对外只暴露一个函数：

    generate_daily_digest(highlights, language) -> str

与 server 端 ``/api/digest`` 的对接契约：
- 返回 markdown 正文（标题 + AI 故事段落）；
- 失败（依赖缺失 / 无密钥 / 网络异常 / 模型输出为空）一律返回空串，
  绝不抛异常——降级为本地模板的策略由调用方负责；
- AI 能力复用 ``AIClient.daily_summary``（其内部已捕获全部异常）。
"""

from __future__ import annotations

import logging

from vivideye.ai import prompts

logger = logging.getLogger(__name__)

# 日报标题（故事正文由模型生成）；按语言二选一
_ZH_TITLE = "# 🐾 萌眼日报"
_EN_TITLE = "# 🐾 VividEye Daily Digest"


def generate_daily_digest(highlights: list[dict], language: str) -> str:
    """当日高光列表 -> AI 故事化日报 markdown 正文；失败返回空串。

    :param highlights: 当日高光记录列表（title / caption / score / started_at…）。
    :param language:   语言偏好（zh_CN / en_US 等，经 prompts.get_language 归一化）。
    """
    try:
        # 延迟导入：requests 等依赖未安装时不影响本模块被 import
        from vivideye.ai.client import AIClient

        client = AIClient()
        # AIClient 默认从全局配置读语言；调用方显式传入时覆盖
        client.language = prompts.get_language(language)
        body = str(client.daily_summary(highlights or []) or "").strip()
        if not body:
            return ""
        title = _EN_TITLE if client.language == "en" else _ZH_TITLE
        return f"{title}\n\n{body}"
    except Exception as e:  # noqa: BLE001 —— 对调用方的契约是"失败给空串"
        logger.warning("AI 日报生成失败（返回空串）：%s", e)
        return ""
