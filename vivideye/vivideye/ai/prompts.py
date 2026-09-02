"""Prompt 工程核心（纯函数、不联网，可离线单测）。

所有模板按 config app.language（zh_CN | en_US）切换中英双语，
调用方也可显式传 language 覆盖。

模板清单：
    highlight_system_prompt   高光判分系统提示词（auto/pet/kid/home 四场景，
                              严格 JSON 输出，含 moments 时间轴）
    highlight_user_prompt     高光判分用户提示词（帧数 + 时间轴说明）
    audio_summary_prompt      音频理解提示词（qwen-audio / input_audio 通道）
    daily_summary_prompt      日报总结提示词（当日高光 -> 温暖文案）
    poster_image_prompt       高光 caption -> 宣传海报风格文生图 prompt
"""

from __future__ import annotations

import datetime
from typing import Optional, Sequence

from vivideye.config import config

# 合法场景模式
SCENE_MODES = ("auto", "pet", "kid", "home")


def get_language(language: Optional[str] = None) -> str:
    """解析语言偏好：返回 'zh' | 'en'（默认读 config app.language）。"""
    lang = str(language or config.get("app.language", "zh_CN") or "zh_CN").strip().lower()
    return "en" if lang.startswith("en") else "zh"


def _is_en(language: Optional[str] = None) -> bool:
    return get_language(language) == "en"


# ----------------------------------------------------------------------
# 场景描述与关注重点（双语）
# ----------------------------------------------------------------------
_ZH_SCENE_DESC = {
    "auto": "智能家庭监控（自动识别画面中是宠物、孩子还是日常家庭生活）",
    "pet": "家庭宠物监控（猫/狗等）",
    "kid": "萌娃监控（婴幼儿/儿童）",
    "home": "家庭日常监控",
}
_EN_SCENE_DESC = {
    "auto": "smart home monitoring (auto-detect whether pets, kids or daily "
            "family life are in frame)",
    "pet": "home pet monitoring (cat/dog etc.)",
    "kid": "baby & kid monitoring (infants / children)",
    "home": "everyday home monitoring",
}

_ZH_FOCUS = {
    "pet": ("- 宠物：可爱表情（歪头、瞪圆眼、吐舌头）、滑稽动作（打滚、追尾巴、突然飞奔）、\n"
            "  与人的互动、偷吃/拆家等搞怪现场、第一次做某事"),
    "kid": ("- 孩子：与家人的互动（拥抱、对话、一起玩）、可爱行为（大笑、手舞足蹈、专注玩耍）、\n"
            "  成长里程碑（第一次翻身/走路/说话）、危险行为（爬高、触碰危险物品——发现时务必\n"
            "  在对应 moments 的 reason 开头标注「注意安全」）"),
    "home": ("- 家庭：温馨的家庭事件（团聚、拥抱、一起吃饭）、访客到来、\n"
             "  宠物/家人的有趣意外瞬间"),
    "auto": ("- 宠物：可爱表情、滑稽动作、与人的互动、第一次做某事\n"
             "- 孩子：互动、可爱行为、成长里程碑；发现危险行为（爬高、触碰危险物品）时务必\n"
             "  在对应 moments 的 reason 开头标注「注意安全」\n"
             "- 家庭：温馨事件、访客到来、有趣瞬间"),
}
_EN_FOCUS = {
    "pet": "- Pets: adorable expressions (head tilt, wide eyes, tongue out), funny moves "
           "(rolling, tail chasing, sudden zoomies), interaction with humans, sneaky snacks / "
           "mischief, first-time deeds",
    "kid": "- Kids: interaction with family (hugs, talking, playing together), cute behaviors "
           "(giggling, dancing, focused play), milestones (first roll / steps / words). "
           "For dangerous acts (climbing, touching hazards) START the matching moments[].reason "
           "with \"SAFETY:\"",
    "home": "- Family: warm family events (reunion, hugs, meals together), visitors arriving, "
            "funny unexpected moments of pets/family members",
    "auto": "- Pets: adorable expressions, funny moves, human interaction, first-time deeds\n"
            "- Kids: interaction, cute behaviors, milestones. For dangerous acts START the "
            "matching moments[].reason with \"SAFETY:\"\n"
            "- Family: warm events, visitors, funny moments",
}


# ----------------------------------------------------------------------
# a) 高光判分：系统提示词 + 用户提示词
# ----------------------------------------------------------------------
def highlight_system_prompt(scene_mode: str = "auto",
                            language: Optional[str] = None) -> str:
    """高光判分系统提示词：四场景双语，要求 VLM 对时序帧输出严格 JSON。

    输出 JSON 契约：
        {"score": 0~1, "title": "", "caption": "", "tags": [], "subjects": [],
         "moments": [{"start": 秒, "end": 秒, "reason": ""}]}
    过滤规则：空镜头 / 静止无事件 / 纯黑暗画面必须低分（<= 0.2）。
    """
    mode = scene_mode if scene_mode in SCENE_MODES else "auto"
    if _is_en(language):
        return f"""You are the highlight editor of "VividEye", a home monitoring camera. Your job is to pick out the moments worth saving in a family memory album.

Scene: {_EN_SCENE_DESC[mode]}.

You will receive several frames sampled uniformly in time order from ONE video clip. Watch them as a whole, judge how much of a highlight this clip is, and write a catchy title plus a warm caption like a family album entry.

What to look for:
{_EN_FOCUS[mode]}

Scoring guide (score: 0.0~1.0):
- 0.9~1.0: rare, laugh-out-loud or heart-melting moments (first-time deeds, priceless expressions, adorable interactions)
- 0.6~0.9: clearly cute / playful / lively moments
- 0.3~0.6: somewhat interesting but ordinary
- 0.0~0.2: MUST score low for any of these — empty scene (no person or pet in frame), static frames with nothing happening, pitch-dark or unusable night-vision footage, severe blur.

Output requirement: output ONLY one JSON object — no markdown fences, no explanations:
{{"score": 0.0, "title": "catchy title, max ~10 words", "caption": "1-2 warm, vivid sentences", "tags": ["3-6 tags"], "subjects": ["main subjects in frame"], "moments": [{{"start": 0.0, "end": 0.0, "reason": "what happens in this interval"}}]}}
"moments" lists highlight sub-intervals (start/end are seconds relative to the clip start, max 5 items, ascending by time). Use an empty array if nothing noteworthy happens."""
    return f"""你是家庭监控摄像头「VividEye」的高光时刻编辑，任务是从监控片段中挑出值得全家收藏回味的瞬间。

场景：{_ZH_SCENE_DESC[mode]}。

你会收到同一段视频按时间顺序均匀抽取的若干帧画面。请把这组帧当作整体来观看，判断这段视频的「高光程度」，并像写家庭纪念册一样给出一个吸睛标题和一段温馨生动的描述。

重点捕捉：
{_ZH_FOCUS[mode]}

打分指引（score 取 0.0~1.0）：
- 0.9~1.0：罕见、让人笑出声或心都化了的瞬间（第一次做某事、神级表情、有爱互动）
- 0.6~0.9：明显可爱、活泼、好玩的瞬间
- 0.3~0.6：有点意思但比较平常
- 0.0~0.2：以下情况必须给低分——空镜头（画面里没有人/宠物）、画面静止且没有任何事件发生、纯黑暗或夜视无效画面、严重模糊无法辨认。

输出要求：只输出一个 JSON 对象，不要 markdown 围栏，不要任何解释文字：
{{"score": 0.0, "title": "不超过12字的吸睛标题", "caption": "1~2句温馨生动的描述", "tags": ["3~6个标签"], "subjects": ["画面里的主体"], "moments": [{{"start": 0.0, "end": 0.0, "reason": "这个时间区间里发生了什么"}}]}}
其中 moments 是精彩子区间列表（start/end 为相对片段开头的秒数，最多 5 个，按时间升序排列）；若整段都没有精彩瞬间，moments 给空数组。"""


def highlight_user_prompt(n_frames: int = 6, duration: Optional[float] = None,
                          language: Optional[str] = None) -> str:
    """高光判分用户提示词：说明帧数与时间轴，帮助 VLM 定位 moments 的秒数。

    duration 缺省读 config capture.segment_seconds（片段时长），据此推算
    第 i 帧的大致秒数，让 moments 的 start/end 有时间锚点。
    """
    if duration is None:
        duration = float(config.get("capture.segment_seconds", 600) or 600)
    interval = duration / max(n_frames, 1)
    if _is_en(language):
        return (f"Below are {n_frames} frames sampled uniformly in time order from ONE clip "
                f"of about {duration:.0f} seconds (adjacent frames are ~{interval:.0f}s apart, "
                f"so frame i sits at about {interval:.0f}*i seconds).\n"
                "Judge the clip as a whole and output ONLY the JSON object as instructed.")
    return (f"以下是同一段视频（总时长约 {duration:.0f} 秒）按时间顺序均匀抽取的 {n_frames} 帧"
            f"（相邻两帧间隔约 {interval:.0f} 秒，即第 i 帧约位于第 {interval:.0f}×i 秒处）。\n"
            "请整体判断这段视频，并按系统要求只输出 JSON 对象。")


# ----------------------------------------------------------------------
# b) 音频理解提示词（qwen-audio / input_audio 通道）
# ----------------------------------------------------------------------
def audio_summary_prompt(language: Optional[str] = None) -> str:
    """音频理解提示词：让音频模型输出严格 JSON（summary/sounds/score）。"""
    if _is_en(language):
        return """You will hear an audio clip recorded by a home monitoring camera. Analyze what is audible (speech, laughter, pet sounds, crying, unusual noises) and output ONLY one JSON object — no markdown fences, no explanations:
{"summary": "one short sentence about the audio", "sounds": ["sound tags, e.g. dog barking, laughter"], "score": 0.0}
"score" is how interesting the audio is (0.0~1.0); give 0.1 or below if it is basically silent background noise."""
    return """你会听到一段家庭监控摄像头录下的音频。请分析可听到的内容（说话、笑声、宠物叫声、哭声、异响等），只输出一个 JSON 对象——不要 markdown 围栏，不要任何解释文字：
{"summary": "一句话概括音频内容", "sounds": ["声音标签，如 狗叫、笑声"], "score": 0.0}
score 表示音频的精彩程度（0.0~1.0）；若基本是安静的背景噪声，请给 0.1 及以下。"""


# ----------------------------------------------------------------------
# c) 日报总结提示词
# ----------------------------------------------------------------------
def _fmt_highlight_time(h: dict) -> str:
    """高光时间 -> HH:MM（容忍秒/毫秒时间戳与现成字符串）。"""
    t = h.get("time") or h.get("started_at") or ""
    try:
        s = float(t)
        if s > 1e12:  # 毫秒时间戳
            s /= 1000.0
        if s > 1e9:   # 秒级时间戳
            return datetime.datetime.fromtimestamp(s).strftime("%H:%M")
    except (TypeError, ValueError):
        pass
    text = str(t)
    return text[:5] if ":" in text else text


def daily_summary_prompt(highlights: Sequence[dict], date_str: str = "",
                         language: Optional[str] = None) -> str:
    """日报总结 prompt：把当日高光列表生成一段温暖文案（日记体）。"""
    items = [h for h in (highlights or []) if isinstance(h, dict)]
    if items:
        lines = []
        for h in items:
            time_str = _fmt_highlight_time(h)
            title = str(h.get("title") or "未命名时刻")
            caption = str(h.get("caption") or "")
            score = h.get("score", 0)
            lines.append(f"- [{time_str}] {title}｜{caption}（分数 {score}）")
        listing = "\n".join(lines)
    else:
        listing = "-（今天没有捕捉到高光时刻）"

    if _is_en(language):
        return f"""You are a warm diary ghostwriter for a family with pets and/or kids.
Below are the highlight moments their home camera captured on {date_str or "this day"}.
Write ONE short diary-style paragraph (3-6 sentences, ~100 words) in English: retell the day like a loving family member, weave the moments into a tiny story, mention the most touching or funniest one or two, and end with a gentle blessing.
Do NOT use markdown, lists or headings. Output the paragraph only.

Highlights of the day:
{listing}"""
    return f"""你是一位温馨的家庭日记代笔人，服务一个可能养着宠物、有孩子的家庭。
下面是 {date_str or "这一天"} 家庭摄像头记录到的高光时刻列表。
请写一段温暖的家庭日记文案（3~6 句、120 字左右）：以家人的口吻娓娓道来，把当天的瞬间串成一个小故事，重点提及最动人或最好笑的一两个瞬间，结尾给一句温柔的祝愿。
不要使用 markdown、列表或标题，直接输出正文。

当日高光：
{listing}"""


# ----------------------------------------------------------------------
# d) 图片生成 prompt 模板（高光 caption -> 宣传海报风格）
# ----------------------------------------------------------------------
def poster_image_prompt(caption: str, language: Optional[str] = None) -> str:
    """把高光 caption 转成宣传海报风格的文生图 prompt。"""
    cap = str(caption or "").strip() or "温馨的家庭日常瞬间"
    if _is_en(language):
        return (f"Heartwarming family-moment poster illustration: {cap}. "
                "Cinematic poster composition, warm golden-hour palette, soft light, "
                "cozy and adorable atmosphere, rich details, clean layout with space "
                "for a title. Absolutely NO text, letters or watermarks in the image.")
    return (f"温馨家庭时刻宣传海报插画：{cap}。"
            "电影级海报构图，暖金色调，柔和光线，治愈可爱的氛围，细节丰富，"
            "版面干净并预留标题位置。画面中绝对不要出现任何文字、字母或水印。")
