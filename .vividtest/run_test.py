#!/usr/bin/env python3
"""VividEye 采集与管线模块的端到端验证脚本（在独立沙盒目录中运行）。

验证点：
1. 各模块 import（含 mock vivideye.ai.client 的延迟导入路径）；
2. sampler：抽帧（base64 JPEG）+ 16k mono wav 音频提取；
3. orchestrator：process_now 全链路（mock AI -> 剪辑 -> 缩略图 -> 入库 -> done）；
4. moments 归一化的容错格式；
5. digest：日报生成 + 入库；
6. recorder：片段注册逻辑（不真正拉流）；
7. retention：过期清理 + 磁盘水位报告。
"""
import json
import os
import sys
import time
import types
from pathlib import Path

REPO = Path("/workspace/vivideye")
sys.path.insert(0, str(REPO))

TEST_DIR = Path("/workspace/.vividtest")

# ---- 1) mock AI 模块（vivideye.ai.client 由并行开发，测试中注入假实现）----
fake_mod = types.ModuleType("vivideye.ai.client")


class FakeAIClient:
    calls = []

    def analyze_frames(self, frames_b64, audio_path, scene_mode):
        FakeAIClient.calls.append(
            {"frames": len(frames_b64), "audio": audio_path, "scene": scene_mode})
        assert isinstance(frames_b64, list) and frames_b64, "frames 应为非空 base64 列表"
        assert all(isinstance(f, str) for f in frames_b64), "帧应为字符串"
        return {
            "score": 0.92,
            "title": "猫咪玩耍",
            "caption": "猫咪在地板上追着玩具打滚，特别可爱。",
            "tags": ["pet", "funny"],
            "subjects": ["cat"],
            "moments": [{"start": 2, "end": 5}],
        }


fake_mod.AIClient = FakeAIClient
sys.modules["vivideye.ai.client"] = fake_mod

from vivideye.config import config  # noqa: E402

checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


# ---- 2) 生成测试片段（mjpeg+aac，模拟录制器产物）----
import subprocess  # noqa: E402

RAW = Path(config.get("storage.raw_dir"))
RAW.mkdir(parents=True, exist_ok=True)
seg_file = RAW / "seg_20260902_100000.mp4"
subprocess.run([
    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
    "-f", "lavfi", "-i", "testsrc=duration=8:size=320x240:rate=10",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
    "-map", "0:v", "-map", "1:a", "-c:v", "mjpeg", "-q:v", "5",
    "-c:a", "aac", "-b:a", "64k", str(seg_file),
], check=True)
check("构造测试片段", seg_file.is_file(), f"{seg_file.stat().st_size} bytes")

# ---- 3) sampler ----
from vivideye.pipeline.sampler import (  # noqa: E402
    extract_audio, probe_duration, sample_frames)

dur = probe_duration(seg_file)
check("probe_duration", dur is not None and 7 < dur < 9, f"{dur:.2f}s")

frames = sample_frames(seg_file, fps=0.5, max_frames=10)
check("sample_frames", 3 <= len(frames) <= 5, f"{len(frames)} 帧 (8s@0.5fps)")

audio = extract_audio(seg_file, TEST_DIR / "audio.wav")
check("extract_audio", audio is not None and audio.stat().st_size > 5000,
      f"{audio.stat().st_size if audio else 0} bytes")

# ---- 4) orchestrator 全链路 ----
from vivideye.storage.db import HighlightsDB  # noqa: E402
from vivideye.pipeline.orchestrator import PipelineService  # noqa: E402

db_path = Path(config.get("storage.db_path"))
db = HighlightsDB(db_path)
seg_id = db.add_segment(seg_file, started_at=time.time() - 600, duration=8.0,
                        size_bytes=seg_file.stat().st_size)
svc = PipelineService(db=db)
result = svc.process_now()
check("process_now 结果", result.get("processed") == 1 and result.get("failed") == 0,
      json.dumps(result, ensure_ascii=False))
check("AI 收到采样", len(FakeAIClient.calls) == 1 and FakeAIClient.calls[0]["frames"] == len(frames),
      f"frames={FakeAIClient.calls[0]['frames']}, audio={FakeAIClient.calls[0]['audio']}")

pending_after = db.pending_segments()
check("片段标记 done", len(pending_after) == 0)

hls = db.list_highlights(limit=10)
check("高光入库", len(hls) == 1, f"score={hls[0]['score']}, title={hls[0]['title']}")
hl = hls[0]
check("高光文件", Path(hl["video_path"]).is_file() and Path(hl["thumb_path"]).is_file(),
      f"{hl['video_path']}")

# 第二段：验证批次累积
seg2 = RAW / "seg_20260902_102000.mp4"
subprocess.run(["cp", str(seg_file), str(seg2)], check=True)
db.add_segment(seg2, started_at=time.time() - 300, duration=8.0,
               size_bytes=seg2.stat().st_size)
svc2 = PipelineService(db=db)
r2 = svc2.process_now()
check("第二批处理", r2.get("processed") == 1 and r2.get("highlights") == 1,
      json.dumps(r2, ensure_ascii=False))

# ---- 5) moments 归一化容错 ----
from vivideye.pipeline.orchestrator import normalize_moments  # noqa: E402

m = normalize_moments([{"start_sec": 1, "end_sec": 4}, [5, 9],
                       {"from": 2, "to": 1}, "bad", {"start": 0, "end": 3}])
check("moments 归一化", m == [(1.0, 4.0), (5.0, 9.0), (0.0, 3.0)], str(m))

# ---- 6) digest ----
from vivideye.pipeline.digest import generate_digest  # noqa: E402

md = generate_digest(db=db)
check("日报生成", md is not None and md.is_file(), str(md))
d = db.get_digest(time.strftime("%Y-%m-%d"))
check("日报入库", d is not None and d["stats"]["highlight_count"] == 2,
      json.dumps(d["stats"], ensure_ascii=False) if d else "None")

# ---- 7) recorder 注册逻辑（不拉流）----
from vivideye.capture.recorder import Recorder  # noqa: E402

rec = Recorder(db=db)
fake_old = RAW / "seg_20260902_095000.mp4"
fake_old.write_bytes(b"\x00" * 2048)
rec._register_segments(rec._list_seg_files(), final=True)
rows = {Path(r["path"]).name: r for r in db.pending_segments(limit=100)}
check("recorder 片段注册", "seg_20260902_095000.mp4" in rows
      and rows["seg_20260902_095000.mp4"]["duration"] == 600.0,
      str({k: v["duration"] for k, v in rows.items()}))
st = rec.status()
check("recorder.status", isinstance(st, dict) and st["running"] is False)
rec.stop()  # 未启动时 stop 应安全

# ---- 8) retention ----
from vivideye.capture.retention import run_retention  # noqa: E402

os.utime(fake_old, (time.time() - 100 * 3600, time.time() - 100 * 3600))
report = run_retention()
check("retention 清理过期", report.deleted_files == 1 and not fake_old.exists(),
      f"free={report.free_gb}GB, ok={report.disk_ok}")

# ---- 汇总 ----
fails = [c for c in checks if not c[1]]
print(f"\n==== {len(checks) - len(fails)}/{len(checks)} 项通过 ====")
sys.exit(1 if fails else 0)
