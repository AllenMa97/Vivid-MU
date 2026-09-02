"""每日萌宠/娃日报（digest）生成。

接口：
    generate_daily_digest(date_str, highlights) -> dict(markdown_text, stats)

highlights 为当日高光 dict 列表（含 score/title/caption/tags 等字段，
time/started_at 可选）。故事化总结用 text model 生成，失败时优雅降级为
模板拼接，不抛异常。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from vivideye.ai.clients import llm_client
from vivideye.ai.prompts import daily_story_prompt
from vivideye.config import config

logger = logging.getLogger(__name__)

_TOP_N = 5  # 日报中展示的 Top 时刻数量


def _fmt_time(h: dict) -> str:
    """高光的可展示时间（HH:MM 优先，否则留空）。"""
    t = h.get("time") or h.get("started_at") or ""
    s = str(t)
    if s.isdigit() and len(s) >= 13:  # 毫秒时间戳
        import datetime
        return datetime.datetime.fromtimestamp(int(s) / 1000).strftime("%H:%M")
    if s.isdigit() and len(s) == 10:  # 秒时间戳
        import datetime
        return datetime.datetime.fromtimestamp(int(s)).strftime("%H:%M")
    return s[:5] if ":" in s else s


def _build_stats(date_str: str, highlights: List[dict]) -> dict:
    """统计信息。"""
    scores = [float(h.get("score") or 0) for h in highlights]
    top = sorted(highlights, key=lambda h: float(h.get("score") or 0), reverse=True)[:_TOP_N]
    return {
        "date": date_str,
        "total": len(highlights),
        "favorite": sum(1 for h in highlights if h.get("favorite")),
        "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "max_score": round(max(scores), 3) if scores else 0.0,
        "top": [
            {
                "title": h.get("title") or "",
                "score": h.get("score") or 0,
                "time": _fmt_time(h),
                "tags": h.get("tags") or [],
            }
            for h in top
        ],
    }


def _story_paragraph(date_str: str, highlights: List[dict]) -> str:
    """故事化总结：text model 生成，失败降级为模板拼接。"""
    try:
        return llm_client.chat(
            daily_story_prompt(highlights, date_str),
            temperature=0.6, max_tokens=512,
        ).strip()
    except Exception as e:
        logger.warning("故事生成失败，使用模板降级：%s", e)
        if not highlights:
            return "今天安安静静，摄像头没捕捉到特别的瞬间。愿明天有更多惊喜。"
        top = sorted(highlights, key=lambda h: float(h.get("score") or 0), reverse=True)[0]
        title = top.get("title") or "某个瞬间"
        return (f"今天共记录到 {len(highlights)} 个值得纪念的时刻，"
                f"其中「{title}」最让人心动。平凡日子里的小惊喜，都被悄悄收藏。")


def generate_daily_digest(date_str: str, highlights: List[dict]) -> dict:
    """生成当日日报。

    返回 {"markdown_text": str, "stats": dict}；失败也不抛异常
    （无高光时输出"空日报"）。
    """
    highlights = [h for h in (highlights or []) if isinstance(h, dict)]
    stats = _build_stats(date_str, highlights)
    lang = config.get("app.language", "zh_CN")
    en = str(lang).lower().startswith("en")

    # ---------- 标题 ----------
    title = (f"🐾 VividEye Daily · {date_str}" if en
             else f"🐾 萌眼日报 · {date_str}")

    # ---------- 统计行 ----------
    if en:
        stat_lines = [
            f"**Highlights today**: {stats['total']}",
            f"**Best score**: {stats['max_score']}",
            f"**Average score**: {stats['avg_score']}",
            f"**Favorites**: {stats['favorite']}",
        ]
        top_header = "## 🌟 Top Moments"
        story_header = "## 📖 Today's Story"
    else:
        stat_lines = [
            f"**今日高光**：{stats['total']} 个",
            f"**最高分**：{stats['max_score']}",
            f"**平均分**：{stats['avg_score']}",
            f"**收藏数**：{stats['favorite']}",
        ]
        top_header = "## 🌟 今日 Top 时刻"
        story_header = "## 📖 今日小故事"

    # ---------- Top 时刻列表 ----------
    top_lines: List[str] = []
    if stats["top"]:
        for i, t in enumerate(stats["top"], 1):
            tags = "、".join(t["tags"][:3]) if t["tags"] else ""
            tag_part = f"　`{tags}`" if tags else ""
            time_part = f" {t['time']}" if t["time"] else ""
            top_lines.append(
                f"{i}. {'⭐' if i == 1 else '•'} **{t['title'] or '—'}**"
                f"（{t['score']} 分）{time_part}{tag_part}"
            )
    else:
        top_lines.append("-" if en else "今天暂无高光，宠物/娃大概是睡了一整天 😴")

    # ---------- 故事段落 ----------
    story = _story_paragraph(date_str, highlights)

    markdown_text = "\n".join([
        f"# {title}",
        "",
        " | ".join(stat_lines),
        "",
        top_header,
        "",
        *top_lines,
        "",
        story_header,
        "",
        story,
        "",
    ])

    logger.info("日报生成完毕 date=%s total=%d", date_str, stats["total"])
    return {"markdown_text": markdown_text, "stats": stats}
