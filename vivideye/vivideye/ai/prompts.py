"""VividEye 双语 prompt 模板。

按 config app.language（zh_CN | en_US）选择语言。所有模板均为纯函数，
不发起网络请求。

模板清单：
    - highlight_judge_prompt   高光判分（VLM，输入帧 + 场景模式，严格 JSON 输出）
    - daily_story_prompt       每日故事/摘要（温馨日记体）
    - cover_image_prompt       封面图 text-to-image prompt 构造器
    - enhance_title_prompt     高光标题/文案打磨（fast/text model）
"""

from __future__ import annotations

from typing import Optional, Sequence

from vivideye.config import config

# 场景模式说明（用于判分 prompt 中的权重提示）
_SCENE_DESC_ZH = {
    "pet": "场景：家庭宠物监控（猫/狗等）。重点捕捉可爱瞬间、搞怪表情、"
           "歪头杀、追跑打闹、与人的互动、第一次做某事（第一次开门/第一次偷吃）等。",
    "kid": "场景：萌娃监控（婴幼儿/儿童）。重点捕捉天真笑容、第一次做某事"
           "（第一次翻身/走路/说话）、专注玩耍、与家人互动、童言童趣等，注意隐私与安全。",
    "home": "场景：家庭安防。重点捕捉异常事件、访客、宠物/孩子的意外举动、"
            "温馨的家庭日常瞬间。",
    "auto": "场景：智能家庭监控（自动判断宠物 / 萌娃 / 日常）。",
}
_SCENE_DESC_EN = {
    "pet": "Scene: home pet monitoring (cat/dog etc.). Focus on adorable moments, "
           "funny expressions, head tilts, zoomies & play, interaction with humans, "
           "first-time deeds (first door opening / first sneaky snack).",
    "kid": "Scene: baby & kid monitoring. Focus on innocent smiles, first-time "
           "milestones (first roll / steps / words), focused play, family "
           "interaction. Mind privacy and safety.",
    "home": "Scene: home security. Focus on unusual events, visitors, unexpected "
            "pet/kid moves, and warm everyday family moments.",
    "auto": "Scene: smart home monitoring (auto-detect pet / kid / daily life).",
}

JSON_SCHEMA_HINT_ZH = """请严格输出一个 JSON 对象（不要输出任何其他文字、解释或 markdown 围栏）：
{
  "score": 0.0~1.0 的小数（精彩程度），
  "title": "不超过 12 字的吸睛标题",
  "caption": "1~2 句温馨/生动的描述",
  "tags": ["3~6 个标签，如 可爱、搞怪、互动"],
  "subjects": ["画面主体，如 小猫、小孩、妈妈"]
}"""

JSON_SCHEMA_HINT_EN = """Output STRICTLY one JSON object (no other text, no markdown fences):
{
  "score": float 0.0~1.0 (how delightful the moment is),
  "title": "catchy title, max ~10 words",
  "caption": "1-2 warm / vivid sentences",
  "tags": ["3-6 tags, e.g. cute, funny, interaction"],
  "subjects": ["main subjects, e.g. kitten, kid, mom"]
}"""


def _lang(language: Optional[str] = None) -> str:
    """解析语言偏好：zh_CN / en_US。"""
    lang = (language or config.get("app.language", "zh_CN") or "zh_CN").lower()
    return "en" if lang.startswith("en") else "zh"


# ----------------------------------------------------------------------
# a) 高光判分 prompt
# ----------------------------------------------------------------------
def highlight_judge_prompt(scene_mode: str = "auto", language: Optional[str] = None) -> str:
    """高光判分 prompt：输入若干视频帧 + 场景模式，要求 VLM 输出严格 JSON。

    针对宠物/萌娃场景优化：可爱瞬间、搞怪表情、互动、第一次做某事等权重高。
    """
    mode = scene_mode if scene_mode in ("pet", "kid", "home", "auto") else "auto"
    if _lang(language) == "en":
        return f"""You are the highlight editor of a home monitoring camera for pets & kids.
{_SCENE_DESC_EN[mode]}

You are given several frames uniformly sampled from one video segment.
Judge how much of a "highlight moment" this segment is, then write a catchy
title and a warm caption as if for a family memory album.

Scoring guidance (0.0~1.0):
  - 0.9~1.0: rare, laugh-out-loud or heart-melting moment (first-time deeds,
    funny faces, adorable interactions between pet/kid and people)
  - 0.6~0.9: clearly cute / playful / lively moment
  - 0.3~0.6: somewhat interesting but ordinary
  - 0.0~0.3: static, empty room, nobody in frame, or nothing noteworthy
Boost the score when: cute moment, funny expression, human-pet/kid interaction,
first-time behavior. Penalize: empty scene, blur, dark, boring stillness.

{JSON_SCHEMA_HINT_EN}"""
    return f"""你是一台家庭宠物/萌娃监控摄像头的高光时刻编辑。
{_SCENE_DESC_ZH[mode]}

给你的是同一段视频里均匀抽取的若干帧。请判断这段视频的"高光程度"，
并像写家庭纪念册一样给出一个吸睛标题和一段温馨生动的描述。

打分指引（0.0~1.0）：
  - 0.9~1.0：罕见、让人笑出声或心都化了的高光（第一次做某事、
    搞怪表情、宠物/萌娃与人的有爱互动）
  - 0.6~0.9：明显可爱/活泼/好玩的瞬间
  - 0.3~0.6：有点意思但比较平常
  - 0.0~0.3：画面静止、空镜、无人无宠、毫无亮点
加分项：可爱瞬间、搞怪表情、互动、第一次做某事；
减分项：空场景、模糊、过暗、呆滞无变化。

{JSON_SCHEMA_HINT_ZH}"""


# ----------------------------------------------------------------------
# b) 每日故事 / 摘要 prompt
# ----------------------------------------------------------------------
def daily_story_prompt(highlights: Sequence[dict],
                       date_str: str = "",
                       language: Optional[str] = None) -> str:
    """每日故事 prompt：把当日高光列表写成一段温馨日记体文字。"""
    lines = []
    for h in highlights:
        time_str = str(h.get("time") or h.get("started_at") or "")
        title = str(h.get("title") or "未命名时刻")
        caption = str(h.get("caption") or "")
        score = h.get("score", 0)
        lines.append(f"- [{time_str}] {title}｜{caption}（分数 {score}）")
    listing = "\n".join(lines) if lines else "-（暂无高光）"

    if _lang(language) == "en":
        return f"""You are a warm, gentle diary ghostwriter for a family with pets/kids.
Below are today's ({date_str}) highlight moments captured by their home camera.
Write ONE short diary-style paragraph (3-6 sentences, ~100 words) in English:
retell the day like a loving family member, weave the moments into a tiny
story, mention the most touching/funny ones, end with a soft blessing.
Do NOT use markdown, lists or headings. Output the paragraph only.

Today's highlights:
{listing}"""
    return f"""你是一位温馨的家庭日记代笔人，服务一个有宠物/孩子的家庭。
下面是 {date_str} 当天由家庭摄像头捕捉到的高光时刻列表。
请用中文写一段日记体短文（3~6 句，120 字左右）：以家人的口吻娓娓道来，
把当天的高光串成一个小故事，重点提到最动人/最好笑的一两个瞬间，
结尾给一句温柔的祝愿。不要使用 markdown、列表或标题，直接输出正文。

当日高光：
{listing}"""


# ----------------------------------------------------------------------
# c) 封面图生成 prompt 构造器
# ----------------------------------------------------------------------
def cover_image_prompt(theme_summary: str, language: Optional[str] = None) -> str:
    """根据当日主题构造 text-to-image 的封面图 prompt。

    theme_summary: 当日故事摘要或若干关键词（来自 digest 的故事段）。
    """
    if _lang(language) == "en":
        return (f"Warm family memory album cover illustration. Theme: {theme_summary}. "
                "Soft watercolor style, cozy pastel palette, gentle morning light, "
                "heartwarming and adorable mood, clean composition with space for a "
                "title, no text in the image.")
    return (f"温馨家庭纪念册封面插画，主题：{theme_summary}。"
            "柔和水彩风格，暖色粉彩色调，清晨的柔和光线，"
            "治愈可爱的氛围，构图干净、留有标题位置，画面中不要出现文字。")


# ----------------------------------------------------------------------
# 高光文案打磨 prompt（供 enhance_highlight 使用）
# ----------------------------------------------------------------------
def enhance_title_prompt(title: str, caption: str = "",
                          language: Optional[str] = None) -> str:
    """把标题/文案打磨得更吸睛（fast/text model 用）。"""
    if _lang(language) == "en":
        return f"""Polish the highlight title for a family camera app. Keep it short
(<=10 words), vivid and heartwarming; make it feel like a must-click memory.
Original title: "{title}"
Original caption: "{caption}"
Output ONLY the polished title, nothing else."""
    return f"""请打磨一条家庭摄像头高光时刻的标题，让它更吸睛、有点击欲，
但保持温馨不浮夸，不超过 12 个字。
原标题：{title}
原描述：{caption}
只输出打磨后的标题本身，不要任何解释或引号。"""
