"""AIClient：OpenAI 兼容在线 AI 客户端（requests 同步实现，Termux 友好）。

设计约定
--------
- ``chat()`` 是唯一会抛异常（AIClientError）的底层原语，供上层复用；
- 高层方法 ``analyze_frames`` / ``daily_summary`` / ``generate_image`` /
  ``test_connection`` 永不抛异常：失败返回错误 dict / 空串 / None；
- ``ai.api_key`` 为空时，所有方法直接返回明确错误，不发起任何网络请求。

模型回退
--------
支持在 ai 配置里加 ``<model_key>_fallbacks``（逗号分隔字符串或 yaml 列表），
主模型失败（超时 / 限流 / 模型不存在等）时按链依次降级尝试：
    ai.vision_model_fallbacks: "qwen-vl-plus,qwen-vl-max"
    ai.audio_model_fallbacks / ai.text_model_fallbacks / ai.fast_model_fallbacks
    ai.image_model / ai.image_model_fallbacks（文生图）
用户配置了回退链时以用户为准；未配置时使用内置默认链。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Sequence

import requests

from vivideye.ai import prompts, providers
from vivideye.config import config

logger = logging.getLogger(__name__)

# VLM 单次最多携带的帧数（帧多则均匀抽取）
_MAX_VISION_FRAMES = 8

# 各 model_key 的内置默认回退链（主模型失败时依次降级）
_BUILTIN_CHAINS: dict[str, list[str]] = {
    "vision_model": ["qwen3-vl-flash", "qwen-vl-plus", "qwen-vl-max"],
    "audio_model": ["qwen-audio-turbo", "qwen2-audio-instruct"],
    "text_model": ["qwen3-max", "qwen-plus", "qwen-turbo"],
    "fast_model": ["qwen-flash", "qwen-turbo"],
}


class AIClientError(RuntimeError):
    """AI 客户端统一异常（仅 chat() 会抛出，高层方法均已捕获）。"""


# ----------------------------------------------------------------------
# 模块级小工具
# ----------------------------------------------------------------------
def _parse_model_list(value: object) -> list[str]:
    """配置值 -> 模型名列表：支持逗号分隔字符串（中英文逗号）或列表。"""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        items = [s.strip() for s in re.split(r"[,，]", str(value))]
    return [m for m in items if m]


def _clean_b64(data: str) -> str:
    """清洗 base64 图片数据：去掉 data URL 前缀与所有空白字符。"""
    s = str(data or "").strip()
    if s.startswith("data:"):
        s = re.sub(r"^data:[^,]*,", "", s)
    return "".join(s.split())


def _extract_json(text: str) -> Optional[dict]:
    """从模型输出中鲁棒提取 JSON 对象 dict；失败返回 None。

    容忍：markdown 围栏（```json ... ```）、JSON 前后的多余说明文字。
    """
    if not text:
        return None
    s = text.strip()
    # 剥离 ``` 围栏（可能带语言标注）
    s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s).strip()
    # 截取最外层花括号（容忍前后噪声）
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    s = s[start:end + 1]
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


# ----------------------------------------------------------------------
# AIClient
# ----------------------------------------------------------------------
class AIClient:
    """OpenAI 兼容在线 AI 客户端（同步 requests，手机 Termux 友好）。"""

    def __init__(self):
        # 基础配置：只读配置、不联网，构造永不抛异常
        self.api_key: str = str(config.get("ai.api_key", "") or "").strip()
        self.provider: str = providers.normalize_provider(config.get("ai.provider", "dashscope"))
        self.api_base: str = providers.resolve_base_url(self.provider, config.get("ai.api_base"))
        self.timeout: float = float(config.get("ai.request_timeout", 60) or 60)
        self.max_retries: int = max(1, int(config.get("ai.max_retries", 3) or 3))
        self.language: str = prompts.get_language()
        # 各类模型回退链：主模型（ai.<key>）+ 用户回退（ai.<key>_fallbacks）或内置默认
        self.model_chains: dict[str, list[str]] = {
            key: self._build_chain(key) for key in _BUILTIN_CHAINS
        }
        # 文生图模型链 [(model, size)]
        self.image_chain: list[tuple[str, str]] = self._build_image_chain()
        # 文生图耗时更长，单独超时（可配 ai.image_gen_timeout）
        self.image_timeout: float = float(config.get("ai.image_gen_timeout", 120) or 120)

    # ------------------------------------------------------------------
    # 配置解析
    # ------------------------------------------------------------------
    def _build_chain(self, model_key: str) -> list[str]:
        """模型回退链：主模型 + （用户配置的回退链 或 内置默认链）。"""
        primary = str(config.get(f"ai.{model_key}", "") or "").strip()
        user_fb = _parse_model_list(config.get(f"ai.{model_key}_fallbacks"))
        tail = user_fb if user_fb else list(_BUILTIN_CHAINS[model_key])
        chain: list[str] = []
        for m in [primary, *tail]:
            if m and m not in chain:
                chain.append(m)
        return chain

    def _build_image_chain(self) -> list[tuple[str, str]]:
        """文生图模型链：ai.image_model / ai.image_model_fallbacks 可覆盖默认。"""
        defaults = providers.default_image_models(self.provider)
        primary = str(config.get("ai.image_model", "") or "").strip()
        user_fb = _parse_model_list(config.get("ai.image_model_fallbacks"))
        default_size = defaults[0][1] if defaults else "1024x1024"
        if primary or user_fb:
            names = [primary, *user_fb]
        else:
            names = [m for m, _ in defaults]
        chain: list[tuple[str, str]] = []
        for m in names:
            if not m or any(mm == m for mm, _ in chain):
                continue
            size = next((s for mm, s in defaults if mm == m), default_size)
            chain.append((m, size))
        return chain

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    # ------------------------------------------------------------------
    # 底层原语：chat（唯一会抛 AIClientError 的方法）
    # ------------------------------------------------------------------
    def chat(self, messages: list[dict], model_key: str = "text_model",
             images: Optional[Sequence[str]] = None,
             temperature: float = 0.3, max_tokens: int = 1024) -> str:
        """OpenAI 兼容 chat/completions 调用。

        messages: OpenAI 格式消息列表（system/user/...）。
        images:   base64 JPEG 列表（可带 data URL 前缀），作为 image_url
                  内容部件合并进最后一条 user 消息。
        model_key: vision_model | audio_model | text_model | fast_model，
                  按对应回退链依次尝试；模型间失败自动降级，模型内指数退避重试。
        """
        if not self.api_key:
            raise AIClientError(
                "未配置 ai.api_key（请在 user_config.yaml 设置，或 export VIVIDEYE_AI__API_KEY）")
        if not self.api_base:
            raise AIClientError("未配置 ai.api_base（provider=compatible 时必须显式指定）")

        chain = self.model_chains.get(model_key) or self.model_chains["text_model"]
        payload_messages = self._merge_images(messages, images)
        url = providers.chat_url(self.api_base)

        last_err = "未知错误"
        for model in chain:
            payload = {"model": model, "messages": payload_messages,
                       "temperature": temperature, "max_tokens": max_tokens}
            for attempt in range(self.max_retries):
                try:
                    resp = requests.post(url, headers=self._headers(),
                                         json=payload, timeout=self.timeout)
                except requests.exceptions.Timeout:
                    last_err = f"模型 {model} 请求超时（{self.timeout}s）"
                    logger.warning("%s（第 %d/%d 次）", last_err, attempt + 1, self.max_retries)
                except requests.exceptions.RequestException as e:
                    last_err = f"模型 {model} 网络异常：{e}"
                    logger.warning("%s（第 %d/%d 次）", last_err, attempt + 1, self.max_retries)
                else:
                    if resp.status_code == 200:
                        content = self._extract_content(resp)
                        if content:
                            return content
                        last_err = f"模型 {model} 返回空内容或响应格式异常"
                    elif resp.status_code in (400, 401, 403, 404):
                        # 鉴权/参数/模型不存在：重试无意义，直接换下一个模型
                        last_err = f"模型 {model} HTTP {resp.status_code}：{resp.text[:200]}"
                        logger.warning(last_err)
                        break
                    else:
                        # 429/5xx 等：指数退避后重试同一模型
                        last_err = f"模型 {model} HTTP {resp.status_code}：{resp.text[:200]}"
                        logger.warning("%s（第 %d/%d 次）", last_err, attempt + 1, self.max_retries)
                # 简单指数退避：1s、2s、4s ... 封顶 8s（break 换模型时不会走到这里）
                if attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt, 8))
        raise AIClientError(f"所有模型请求失败（链：{chain}）：{last_err}")

    @staticmethod
    def _extract_content(resp) -> str:
        """从 chat/completions 响应提取文本内容；失败返回空串。"""
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            return str(content).strip() if content else ""
        except (ValueError, KeyError, IndexError, TypeError):
            return ""

    @staticmethod
    def _merge_images(messages: list[dict],
                      images: Optional[Sequence[str]]) -> list[dict]:
        """把 base64 图片列表合并进最后一条 user 消息（不改入参）。"""
        if not images:
            return messages
        parts = [{"type": "image_url",
                  "image_url": {"url": f"data:image/jpeg;base64,{_clean_b64(b)}"}}
                 for b in images if _clean_b64(b)]
        if not parts:
            return messages
        merged = [dict(m) for m in messages]
        for i in range(len(merged) - 1, -1, -1):
            if merged[i].get("role") == "user":
                content = merged[i].get("content")
                if isinstance(content, list):  # 已是多部件内容：图片插到最前
                    merged[i]["content"] = parts + list(content)
                else:  # 纯文本：转成 [图片..., 文本] 部件列表
                    merged[i]["content"] = parts + [{"type": "text", "text": str(content or "")}]
                return merged
        # 没有 user 消息：追加一条仅含图片的
        merged.append({"role": "user", "content": parts})
        return merged

    # ------------------------------------------------------------------
    # 高层能力一：高光判分（帧 + 可选音频）
    # ------------------------------------------------------------------
    def analyze_frames(self, frames_b64: list[str], audio_path: Optional[str] = None,
                       scene_mode: str = "auto") -> dict:
        """分析一组时序帧（+可选音频），返回高光判分 dict；绝不抛异常。

        流程：均匀抽最多 8 帧 -> vision_model 判分（JSON 解析失败附加
        严格指令重试一次）-> 若 audio_path 存在则 audio_model 旁路分析并
        合并（caption/tags 并入，score 加权 0.7*视觉 + 0.3*音频）。

        成功返回 {score, title, caption, tags, subjects, moments}；
        任何失败返回 {...默认值, "error": 原因, "score": 0}。
        """
        default = {"error": "", "score": 0.0, "title": "", "caption": "",
                   "tags": [], "subjects": [], "moments": []}
        if not self.api_key:
            return {**default, "error": "未配置 ai.api_key，无法分析"}
        try:
            frames = self._uniform_sample(frames_b64 or [], _MAX_VISION_FRAMES)
            if not frames:
                return {**default, "error": "没有可用的视频帧"}

            system = prompts.highlight_system_prompt(scene_mode, self.language)
            user = prompts.highlight_user_prompt(len(frames), language=self.language)
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": user}]

            raw = self.chat(messages, model_key="vision_model", images=frames,
                            temperature=0.2, max_tokens=1024)
            result = _extract_json(raw)
            if result is None:
                # 解析失败：附加严格指令重试一次
                logger.warning("VLM 输出 JSON 解析失败，重试一次：%r", (raw or "")[:120])
                retry_messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user +
                     "\n\n注意：只输出一个合法的 JSON 对象，不要任何其他文字或 markdown 围栏。"},
                ]
                raw = self.chat(retry_messages, model_key="vision_model", images=frames,
                                temperature=0.0, max_tokens=1024)
                result = _extract_json(raw)
                if result is None:
                    return {**default,
                            "error": f"模型输出 JSON 解析失败：{(raw or '')[:120]}"}

            merged = self._normalize_result(result)

            # 音频通道（尽力而为：失败只打日志，不影响视觉结果）
            audio_info = self._analyze_audio(audio_path)
            if audio_info:
                merged = self._merge_audio(merged, audio_info)
            return merged
        except Exception as e:  # 管线安全第一：任何异常都不抛给调用方
            logger.warning("analyze_frames 失败（返回默认值）：%s", e)
            return {**default, "error": f"分析失败：{e}"}

    @staticmethod
    def _uniform_sample(items: list[str], limit: int) -> list[str]:
        """帧数超过 limit 时按时间均匀抽取（保留首尾帧），否则原样返回。"""
        if len(items) <= limit:
            return list(items)
        if limit <= 1:
            return [items[0]]
        idx = [round(i * (len(items) - 1) / (limit - 1)) for i in range(limit)]
        seen: set[int] = set()
        out: list[str] = []
        for i in idx:  # round 可能产生重复索引，去重保序
            if i not in seen:
                seen.add(i)
                out.append(items[i])
        return out

    @staticmethod
    def _normalize_result(raw: dict) -> dict:
        """判分结果清洗：字段白名单 + 类型矫正（score 截断 0~1）。"""

        def _clamp_score(v) -> float:
            try:
                return max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                return 0.0

        def _str_list(v, cap: int = 8) -> list[str]:
            if isinstance(v, str):
                v = [v]
            if not isinstance(v, (list, tuple)):
                return []
            return [str(x).strip() for x in v if str(x).strip()][:cap]

        moments = []
        for m in raw.get("moments") or []:
            if not isinstance(m, dict):
                continue
            try:
                start = float(m.get("start") or 0)
                end = float(m.get("end") or 0)
            except (TypeError, ValueError):
                continue
            moments.append({"start": start, "end": end,
                            "reason": str(m.get("reason") or "").strip()[:200]})
        return {
            "score": _clamp_score(raw.get("score", 0.0)),
            "title": str(raw.get("title") or "").strip()[:80],
            "caption": str(raw.get("caption") or "").strip()[:600],
            "tags": _str_list(raw.get("tags")),
            "subjects": _str_list(raw.get("subjects")),
            "moments": moments[:5],
        }

    def _analyze_audio(self, audio_path: Optional[str]) -> Optional[dict]:
        """音频旁路分析（尽力而为）：成功返回 {summary, sounds, score}，否则 None。

        走 OpenAI 兼容的 input_audio 内容部件（qwen-audio 格式）。
        """
        if not audio_path:
            return None
        path = Path(audio_path)
        if not path.is_file():
            logger.warning("音频文件不存在，跳过音频分析：%s", audio_path)
            return None
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
            fmt = "mp3" if path.suffix.lower() in (".mp3", ".m4a", ".aac") else "wav"
            messages = [{
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
                    {"type": "text", "text": prompts.audio_summary_prompt(self.language)},
                ],
            }]
            raw = self.chat(messages, model_key="audio_model",
                            temperature=0.2, max_tokens=256)
            parsed = _extract_json(raw)
            if parsed is not None:
                try:
                    score = max(0.0, min(1.0, float(parsed.get("score"))))
                except (TypeError, ValueError):
                    score = None
                sounds = [str(s).strip() for s in (parsed.get("sounds") or [])
                          if str(s).strip()]
                return {"summary": str(parsed.get("summary") or "").strip()[:200],
                        "sounds": sounds[:6], "score": score}
            # 非 JSON 输出也容忍：整段文本当 summary，不参与加权
            text = (raw or "").strip()
            return {"summary": text[:200], "sounds": [], "score": None} if text else None
        except Exception as e:
            logger.warning("音频分析失败（忽略，仅用视觉结果）：%s", e)
            return None

    def _merge_audio(self, result: dict, audio: dict) -> dict:
        """合并音频结论：summary 并入 caption、sounds 并入 tags、score 加权。"""
        merged = dict(result)
        summary = str(audio.get("summary") or "").strip()
        if summary:
            if self.language == "en":
                merged["caption"] = f"{merged.get('caption') or ''} (sound: {summary})".strip()
            else:
                merged["caption"] = f"{merged.get('caption') or ''}（声音：{summary}）".strip()
        tags = [t for t in (merged.get("tags") or []) if t]
        for s in audio.get("sounds") or []:
            if s not in tags:
                tags.append(s)
        merged["tags"] = tags[:8]
        audio_score = audio.get("score")
        if audio_score is not None:  # 视觉 0.7 / 音频 0.3 加权
            try:
                merged["score"] = round(0.7 * float(merged.get("score") or 0.0)
                                        + 0.3 * float(audio_score), 4)
            except (TypeError, ValueError):
                pass
        return merged

    # ------------------------------------------------------------------
    # 高层能力二：日报总结
    # ------------------------------------------------------------------
    def daily_summary(self, highlights: list[dict]) -> str:
        """当日高光列表 -> 一段温暖文案；失败返回空串（不抛异常）。"""
        if not self.api_key:
            logger.warning("daily_summary：未配置 ai.api_key，返回空串")
            return ""
        try:
            prompt = prompts.daily_summary_prompt(highlights or [], language=self.language)
            text = self.chat([{"role": "user", "content": prompt}],
                             model_key="text_model", temperature=0.6, max_tokens=600)
            return text.strip()
        except Exception as e:
            logger.warning("daily_summary 失败（返回空串）：%s", e)
            return ""

    # ------------------------------------------------------------------
    # 高层能力三：文生图
    # ------------------------------------------------------------------
    def generate_image(self, prompt: str, out_path: str) -> Optional[Path]:
        """在线文生图（/images/generations），b64_json 或 url 落盘；失败返回 None。"""
        if not config.get("ai.image_gen_enabled", True):
            logger.info("图像生成未启用（ai.image_gen_enabled=false），跳过")
            return None
        if not self.api_key:
            logger.warning("generate_image：未配置 ai.api_key")
            return None
        if not str(prompt or "").strip():
            logger.warning("generate_image：prompt 为空，跳过")
            return None

        url = providers.images_url(self.api_base)
        out = Path(out_path)
        for model, size in self.image_chain:
            try:
                resp = requests.post(url, headers=self._headers(),
                                     json={"model": model, "prompt": prompt,
                                           "n": 1, "size": size},
                                     timeout=self.image_timeout)
                if resp.status_code != 200:
                    logger.warning("文生图失败（%s）HTTP %d：%s",
                                   model, resp.status_code, resp.text[:200])
                    continue
                data = (resp.json() or {}).get("data") or []
                if not data:
                    logger.warning("文生图失败（%s）：响应无 data", model)
                    continue
                item = data[0] if isinstance(data[0], dict) else {}
                out.parent.mkdir(parents=True, exist_ok=True)
                if item.get("b64_json"):
                    out.write_bytes(base64.b64decode(item["b64_json"]))
                elif item.get("url"):
                    dl = requests.get(item["url"], timeout=self.image_timeout)
                    dl.raise_for_status()
                    out.write_bytes(dl.content)
                else:
                    logger.warning("文生图失败（%s）：既无 b64_json 也无 url", model)
                    continue
                if out.is_file() and out.stat().st_size > 0:
                    logger.info("文生图成功：%s（model=%s）", out, model)
                    return out
            except Exception as e:
                logger.warning("文生图异常（%s）：%s", model, e)
        logger.error("文生图：所有模型均失败，prompt=%r", str(prompt)[:80])
        return None

    # ------------------------------------------------------------------
    # 高层能力四：连接自检
    # ------------------------------------------------------------------
    def test_connection(self) -> dict:
        """快速自检：列出各模型回退链 + ping 一次 chat；永不抛异常。"""
        info: dict = {
            "ok": False,
            "provider": self.provider,
            "api_base": self.api_base,
            "api_key_set": bool(self.api_key),
            "models": {k: list(v) for k, v in self.model_chains.items()},
            "image_models": [m for m, _ in self.image_chain],
            "chat_ok": False,
            "reply": "",
            "error": "",
        }
        if not self.api_key:
            info["error"] = "未配置 ai.api_key（设置 VIVIDEYE_AI__API_KEY 后重试）"
            return info
        if not self.api_base:
            info["error"] = "未配置 ai.api_base（provider=compatible 时必须显式指定）"
            return info
        try:
            reply = self.chat([{"role": "user", "content": "ping（请只回复 pong）"}],
                              model_key="fast_model", temperature=0.0, max_tokens=16)
            info["chat_ok"] = True
            info["ok"] = True
            info["reply"] = reply.strip()[:100]
        except Exception as e:
            info["error"] = str(e)
        return info


# 模块级单例：构造只读配置、不联网；无 api_key 也不报错，调用时才返回明确错误
ai_client = AIClient()
