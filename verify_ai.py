"""AI 能力层验证脚本（无 API key 的异常/降级路径）。"""
import logging
import sys
import json

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

sys.path.insert(0, "/workspace/vivideye")

# 1) 导入验证
import vivideye.ai.highlight_agent
import vivideye.ai.digest
import vivideye.ai.generation
import vivideye.ai as ai
print("[1] import OK:",
      vivideye.ai.highlight_agent.__name__,
      vivideye.ai.digest.__name__,
      vivideye.ai.generation.__name__)

# 2) 用 ffmpeg 造一个 8 秒测试视频（moving testsrc）
import subprocess
test_video = "/tmp/vivideye_test.mp4"
r = subprocess.run([
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc=duration=8:size=320x240:rate=10",
    "-pix_fmt", "yuv420p", test_video,
])
assert r.returncode == 0 and __import__("os").path.exists(test_video), "测试视频生成失败"
print("[2] 测试视频生成 OK:", test_video)

# 3) 抽帧验证（与判分解耦的能力）
frames = ai.extract_frames(test_video, max_frames=6)
print(f"[3] extract_frames 抽到 {len(frames)} 帧:", frames)
assert 1 <= len(frames) <= 6, "抽帧数量异常"

# 4) 无 API key 时 judge_segment 返回默认 dict 不崩溃
assert not ai.llm_client.has_api_key, "当前环境不应有 key"
result = ai.judge_segment(test_video, scene_mode="pet", max_frames=6)
print("[4] judge_segment(无key) ->", json.dumps(result, ensure_ascii=False))
assert result == {"score": 0.0, "title": "", "caption": "", "tags": [], "subjects": []}, \
    "无 key 时应返回默认值"

# 5) 无 key 时 enhance_highlight 不崩溃，返回原 dict
hl = {"score": 0.9, "title": "小猫打滚", "caption": "很可爱", "tags": ["cute"]}
out = ai.enhance_highlight(hl)
print("[5] enhance_highlight(无key) ->", json.dumps(out, ensure_ascii=False))
assert out["title"] == "小猫打滚" and out["score"] == 0.9, "无 key 应保留原内容"

# 6) 无 key 时 chat 抛出清晰异常信息
try:
    ai.llm_client.chat("你好")
    raise AssertionError("无 key 时 chat 应当抛异常")
except ai.VividEyeAIError as e:
    msg = str(e)
    assert "api_key" in msg and "VIVIDEYE_AI__API_KEY" in msg, "异常信息应包含配置指引"
    print("[6] 无 key chat 异常信息 OK:", msg[:60], "...")

# 7) generate_daily_digest 正常工作（故事段走降级模板）
highlights = [
    {"score": 0.92, "title": "第一次开门", "caption": "小猫学会了开门", "tags": ["第一次", "聪明"], "time": "09:12"},
    {"score": 0.75, "title": "午睡翻肚皮", "caption": "睡得四仰八叉", "tags": ["睡姿"], "time": "13:40"},
    {"score": 0.6, "title": "追逗猫棒", "caption": "飞檐走壁", "tags": ["运动"], "time": "18:05"},
]
d = ai.generate_daily_digest("2026-09-01", highlights)
print("[7] digest stats:", json.dumps(d["stats"], ensure_ascii=False))
assert d["stats"]["total"] == 3 and d["stats"]["max_score"] == 0.92
assert d["stats"]["top"][0]["title"] == "第一次开门"
assert "萌眼日报" in d["markdown_text"] and "今日 Top 时刻" in d["markdown_text"]
print("---- markdown 预览 ----")
print(d["markdown_text"])

# 8) 无 key 时生成接口优雅降级
assert ai.generate_image("a cute cat", "/tmp/vivideye_cover.png") is None, "无 key 图像生成应返回 None"
assert ai.generate_audio("你好") is None, "无 key TTS 应返回 None"
assert ai.generate_video_stub() is None, "视频 stub 应返回 None"
print("[8] generation 优雅降级 OK（全部返回 None）")

# 9) JSON 解析健壮性（markdown 围栏 / 前后杂讯 / 越界 score）
from vivideye.ai.highlight_agent import _parse_json_content, _normalize_result
raw = _parse_json_content('好的，以下是结果：\n```json\n{"score": 1.5, "title": "爆睡", '
                          '"caption": "睡着了", "tags": ["睡觉", 3], "subjects": "小猫"}\n```\n以上。')
norm = _normalize_result(raw)
print("[9] 健壮解析 ->", json.dumps(norm, ensure_ascii=False))
assert norm["score"] == 1.0 and norm["title"] == "爆睡"
assert norm["tags"] == ["睡觉", "3"] and norm["subjects"] == ["小猫"], "tags/subjects 应被规范化"

# 10) 双语 prompt 切换
from vivideye.ai.prompts import highlight_judge_prompt, daily_story_prompt, cover_image_prompt
zh = highlight_judge_prompt("pet", language="zh_CN")
en = highlight_judge_prompt("pet", language="en_US")
assert "高光" in zh and "STRICTLY" in en
assert "第一次" in zh and "first-time" in en.lower()
print("[10] 双语 prompt OK（zh/en 长度：", len(zh), "/", len(en), "）")

print("\n=== 全部验证通过 ===")
