"""今日精选日报：聚合当天高光，生成 Markdown 日报并入库。

流程：
1. 从数据库取当天（本地时区）的 highlights，按 score 降序排列；
2. 渲染 Markdown（榜单、统计、金句 caption）；
3. 写入 ``storage.digest_dir``（文件名 digest-YYYY-MM-DD.md，连字符，
   与 Web 端日报命名统一）；
4. ``save_digest`` 入库（同一天重复生成会覆盖更新）。
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from vivideye.config import Config, config
from vivideye.paths import resolve_path
from vivideye.storage.db import HighlightsDB

logger = logging.getLogger(__name__)

# 榜单最多展示的条数与金句条数
TOP_LIST_LIMIT = 20
QUOTE_LIMIT = 5


def generate_digest(date: str | None = None,
                    db: HighlightsDB | None = None,
                    cfg: Config | None = None) -> Optional[Path]:
    """生成某天的精选日报，返回 Markdown 文件路径。

    :param date: ``YYYY-MM-DD``，缺省为今天（本地时区）。
    """
    cfg = cfg or config
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    db = db if db is not None else HighlightsDB(
        resolve_path(cfg.get("storage.db_path", "data/vivideye.db"), cfg))

    highlights = db.list_highlights(limit=500)
    day_highlights = [h for h in highlights
                      if _local_date(h.get("created_at")) == date]
    day_highlights.sort(key=lambda h: float(h.get("score") or 0), reverse=True)

    stats = _build_stats(day_highlights)
    markdown = _render_markdown(date, day_highlights, stats)

    digest_dir = resolve_path(cfg.get("storage.digest_dir", "data/digests"), cfg)
    digest_dir.mkdir(parents=True, exist_ok=True)
    out_path = digest_dir / f"digest-{date}.md"
    out_path.write_text(markdown, encoding="utf-8")

    db.save_digest(date, str(out_path), stats)
    logger.info("日报已生成：%s（%d 条高光）", out_path, len(day_highlights))
    return out_path


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------
def _local_date(ts: Any) -> str:
    """epoch 秒 -> 本地日期字符串（异常值归为空串）。"""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _fmt_seconds(seconds: Any) -> str:
    """把秒数格式化为人类可读时长。"""
    try:
        s = int(round(float(seconds)))
    except (TypeError, ValueError):
        s = 0
    if s < 60:
        return f"{s} 秒"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m} 分 {sec} 秒"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分"


def _fmt_time(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return "--:--"


def _build_stats(highlights: list[dict]) -> dict:
    """汇总当天统计：数量、总时长、平均分、高频标签与出镜主体。

    stats 键与 Web 端日报统一：``total`` 为高光总数、``top`` 为得分
    最高的 5 条摘要（入参需已按 score 降序）；同时保留旧键
    ``highlight_count`` 以兼容历史数据。
    """
    n = len(highlights)
    total = sum(float(h.get("duration") or 0) for h in highlights)
    avg = (sum(float(h.get("score") or 0) for h in highlights) / n) if n else 0.0
    tags = Counter(t for h in highlights for t in (h.get("tags") or []))
    subjects = Counter(s for h in highlights for s in (h.get("subjects") or []))
    return {
        "total": n,
        "highlight_count": n,        # 旧键，兼容历史数据
        "top": [
            {
                "title": h.get("title") or "",
                "score": h.get("score") or 0,
                "time": _fmt_time(h.get("started_at")),
                "tags": h.get("tags") or [],
            }
            for h in highlights[:5]
        ],
        "total_duration": round(total, 1),
        "avg_score": round(avg, 3),
        "top_tags": tags.most_common(5),
        "top_subjects": subjects.most_common(5),
        "generated_at": time.time(),
    }


def _render_markdown(date: str, highlights: list[dict], stats: dict) -> str:
    """渲染日报 Markdown 文本。"""
    lines: list[str] = [f"# 📅 VividEye 今日精选 · {date}", ""]

    if not highlights:
        lines += ["今天还没有捕捉到精彩瞬间，继续期待～", ""]
        return "\n".join(lines)

    lines += [
        f"> 今日共捕捉 **{stats['highlight_count']}** 个精彩瞬间，"
        f"累计时长 **{_fmt_seconds(stats['total_duration'])}**，"
        f"平均精彩度 **{stats['avg_score']:.2f}**。",
        "",
        "## 🏆 精彩榜单",
        "",
    ]
    for i, h in enumerate(highlights[:TOP_LIST_LIMIT], 1):
        score = float(h.get("score") or 0)
        meta = [f"⏱ {_fmt_seconds(h.get('duration'))}",
                f"🕒 {_fmt_time(h.get('started_at'))}",
                f"⭐ {score:.2f}"]
        if h.get("tags"):
            meta.append("🏷 " + "、".join(str(t) for t in h["tags"]))
        if h.get("subjects"):
            meta.append("👀 " + "、".join(str(s) for s in h["subjects"]))
        lines.append(f"### {i}. {h.get('title') or '未命名高光'}")
        lines.append("")
        lines.append("- " + " ｜ ".join(meta))
        caption = str(h.get("caption") or "").strip()
        if caption:
            lines.append(f"- 💬 {caption}")
        lines.append("")

    # 统计
    lines += ["## 📊 今日统计", ""]
    lines.append(f"- 高光总数：**{stats['highlight_count']}**")
    lines.append(f"- 累计时长：**{_fmt_seconds(stats['total_duration'])}**")
    lines.append(f"- 平均精彩度：**{stats['avg_score']:.2f}**")
    if stats["top_tags"]:
        lines.append("- 高频标签：" + "、".join(
            f"{t}({c})" for t, c in stats["top_tags"]))
    if stats["top_subjects"]:
        lines.append("- 高频出镜：" + "、".join(
            f"{s}({c})" for s, c in stats["top_subjects"]))
    lines.append("")

    # 金句：取精彩度最高、带 caption 的几条
    quotes = [h for h in highlights if str(h.get("caption") or "").strip()]
    if quotes:
        lines += ["## ✨ 今日金句", ""]
        for h in quotes[:QUOTE_LIMIT]:
            lines.append(f"> {str(h['caption']).strip()}")
            lines.append(">")
        if lines[-1] == ">":
            lines.pop()
        lines.append("")

    return "\n".join(lines)
