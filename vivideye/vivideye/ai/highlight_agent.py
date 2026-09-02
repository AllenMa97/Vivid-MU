"""高光判分智能体：视频片段 -> 高光 dict。

与管线专家约定的接口（签名不可变）：
    judge_segment(video_path, scene_mode="auto", max_frames=6) -> dict
    enhance_highlight(highlight_dict) -> dict

流程：ffmpeg 均匀抽帧（base64）-> VLM 判分 -> 健壮 JSON 解析，
任何一步失败都返回默认值并打日志，绝不抛异常中断管线。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from vivideye.ai.clients import llm_client
from vivideye.ai.prompts import enhance_title_prompt, highlight_judge_prompt

logger = logging.getLogger(__name__)

# 判分结果默认值（失败时返回，score=0 会被 min_highlight_score 自然过滤）
DEFAULT_RESULT = {
    "score": 0.0,
    "title": "",
    "caption": "",
    "tags": [],
    "subjects": [],
}

# 判分输出里允许的字段（多余的丢弃）
_ALLOWED_FIELDS = ("score", "title", "caption", "tags", "subjects")


def _probe_duration(video_path: str) -> Optional[float]:
    """ffprobe 获取视频时长（秒）。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except Exception as e:
        logger.warning("ffprobe 获取时长失败（%s）：%s", video_path, e)
        return None


def extract_frames(video_path: str, max_frames: int = 6,
                   out_dir: Optional[str] = None) -> List[str]:
    """ffmpeg 均匀抽帧，返回帧图片路径列表。

    策略：先拿时长，用 fps=N/duration 均匀采样；拿不到时长时退化为
    thumbnail 滤镜抽代表帧。帧统一缩放到最长边 1024 以控制请求体积。
    """
    video = Path(video_path)
    if not video.is_file():
        logger.warning("视频不存在，无法抽帧：%s", video_path)
        return []

    tmp_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="vivideye_frames_"))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(tmp_dir / "frame_%02d.jpg")

    def _run(args: List[str]) -> bool:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                logger.warning("ffmpeg 失败：%s", (r.stderr or "").strip()[-300:])
                return False
            return True
        except Exception as e:
            logger.warning("ffmpeg 执行异常：%s", e)
            return False

    # 方案一：已知时长 -> fps 滤镜均匀采样
    duration = _probe_duration(video_path)
    if duration and duration > 0.5:
        fps = max_frames / duration
        if _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(video),
                 "-vf", f"fps={fps:.6f},scale='min(1024,iw)':-2",
                 "-frames:v", str(max_frames), "-q:v", "3", pattern]):
            frames = sorted(str(p) for p in tmp_dir.glob("frame_*.jpg"))
            if frames:
                return frames

    # 方案二：兜底 thumbnail 滤镜抽代表帧
    logger.info("均匀抽帧失败，退化为 thumbnail 代表帧：%s", video_path)
    single = tmp_dir / "frame_00.jpg"
    if _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(video),
             "-vf", f"thumbnail={max(1, max_frames)},scale='min(1024,iw)':-2",
             "-frames:v", "1", "-q:v", "3", str(single)]) and single.is_file():
        return [str(single)]
    return []


def _parse_json_content(content: str) -> dict:
    """健壮解析模型输出：剥离 markdown 围栏、截取最外层花括号。"""
    text = (content or "").strip()
    # 剥离 ```json ... ``` / ``` ... ``` 围栏
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    # 截取第一个 { 到最后一个 }（容忍前后多余说明文字）
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def _normalize_result(raw: dict) -> dict:
    """清洗判分结果：字段白名单 + 类型矫正。"""
    result = dict(DEFAULT_RESULT)

    score = raw.get("score", 0.0)
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    result["score"] = max(0.0, min(1.0, score))

    result["title"] = str(raw.get("title") or "").strip()[:80]
    result["caption"] = str(raw.get("caption") or "").strip()[:500]

    for key in ("tags", "subjects"):
        items = raw.get(key) or []
        if isinstance(items, str):
            items = [items]
        result[key] = [str(i).strip() for i in items if str(i).strip()][:8]

    return result


def judge_segment(video_path: str, scene_mode: str = "auto", max_frames: int = 6) -> dict:
    """判分一段视频是否为高光时刻。

    返回 dict(score, title, caption, tags, subjects)；失败返回默认值并打日志。
    """
    try:
        frames = extract_frames(video_path, max_frames=max_frames)
        if not frames:
            logger.warning("抽帧失败，返回默认判分：%s", video_path)
            return dict(DEFAULT_RESULT)

        prompt = highlight_judge_prompt(scene_mode=scene_mode)
        content = llm_client.chat_with_images(prompt, frames, temperature=0.2)
        raw = _parse_json_content(content)
        result = _normalize_result(raw)
        logger.info("判分完成 %s score=%.2f title=%r", video_path, result["score"], result["title"])
        return result
    except Exception as e:
        # 判分是"尽力而为"能力：任何失败都不能打断管线
        logger.warning("judge_segment 失败（返回默认值）：%s -> %s", video_path, e)
        return dict(DEFAULT_RESULT)


def enhance_highlight(highlight_dict: dict) -> dict:
    """用 fast/text 模型把高光标题打磨得更吸睛。

    返回原 dict 的浅拷贝并更新 title（及 caption 为空时的兜底）；
    失败时原样返回（打日志），绝不抛异常。
    """
    enhanced = dict(highlight_dict or {})
    try:
        title = str(enhanced.get("title") or "").strip()
        caption = str(enhanced.get("caption") or "").strip()
        if not title and not caption:
            return enhanced

        prompt = enhance_title_prompt(title or caption[:40], caption)
        new_title = llm_client.chat_fast(prompt).strip().strip('"“”').splitlines()[0]
        if new_title:
            enhanced["title"] = new_title[:80]
            if not enhanced.get("caption"):
                enhanced["caption"] = caption
        return enhanced
    except Exception as e:
        logger.warning("enhance_highlight 失败（保留原标题）：%s", e)
        return enhanced
