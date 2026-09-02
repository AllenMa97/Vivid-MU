#!/usr/bin/env python3
"""验证脚本 2（修复后）：
A. 真实 AIClient 无 api_key：返回 score=0 -> 片段标 done、无高光、不崩溃；
B. AI 运行时抛异常（注入假 client）：片段标 failed、process_now 正常返回；
C. 本机 MJPEG HTTP 流模拟 IP 摄像头：Recorder 真实拉流切片 -> 注册 DB -> stop 收尾。
"""
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path("/workspace/vivideye")
sys.path.insert(0, str(REPO))

from vivideye.config import config  # noqa: E402

checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


from vivideye.storage.db import HighlightsDB  # noqa: E402
from vivideye.pipeline.orchestrator import PipelineService  # noqa: E402

RAW = Path(config.get("storage.raw_dir"))
RAW.mkdir(parents=True, exist_ok=True)
db = HighlightsDB(Path(config.get("storage.db_path")))


def make_test_video(path: Path, seconds: int = 4) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10",
        "-c:v", "mjpeg", "-q:v", "5", str(path)], check=True)


# ----------------------------------------------------------------------
# A. 真实 AIClient 无 api_key：analyze_frames 返回 score=0（含 error 字段）
# ----------------------------------------------------------------------
seg_a = RAW / "seg_20260902_110000.mp4"
make_test_video(seg_a)
db.add_segment(seg_a, started_at=time.time() - 60, duration=4.0,
               size_bytes=seg_a.stat().st_size)
r_a = PipelineService(db=db).process_now()
check("无 key AI 调用不崩溃", isinstance(r_a, dict) and r_a.get("processed") == 1
      and r_a.get("failed") == 0, json.dumps(r_a, ensure_ascii=False)[:160])
check("低分片段标 done 且无高光", r_a.get("highlights") == 0
      and len(db.pending_segments()) == 0)

# ----------------------------------------------------------------------
# B. AI 运行时抛异常 -> 段标 failed，管线存活
# ----------------------------------------------------------------------
class ExplodingClient:
    def analyze_frames(self, frames_b64, audio_path, scene_mode):
        raise RuntimeError("模拟 AI 服务超时")


seg_b = RAW / "seg_20260902_111000.mp4"
make_test_video(seg_b)
db.add_segment(seg_b, started_at=time.time() - 50, duration=4.0,
               size_bytes=seg_b.stat().st_size)
svc_b = PipelineService(db=db)
svc_b._ai_client = ExplodingClient()
r_b = svc_b.process_now()
check("AI 异常不崩溃", isinstance(r_b, dict) and r_b.get("failed") == 1,
      json.dumps(r_b, ensure_ascii=False)[:160])
check("AI 异常段标 failed", len(db.pending_segments()) == 0)

# ----------------------------------------------------------------------
# C. Recorder 真实拉流（record_audio=false 应真正不拉音频）
# ----------------------------------------------------------------------
class MJpegHandler(BaseHTTPRequestHandler):
    """把 ffmpeg testsrc 的 mjpeg 管道流转发给客户端，模拟 IP 摄像头。

    rate=25 与 ffmpeg mjpeg demuxer 的默认时间基准一致，
    保证 segment_seconds 的切片节奏与墙钟时间吻合。
    """

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        p = subprocess.Popen([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-re", "-stream_loop", "-1",
            "-f", "lavfi", "-i", "testsrc=rate=25:size=320x240",
            "-c:v", "mjpeg", "-q:v", "5", "-f", "image2pipe", "pipe:1",
        ], stdout=subprocess.PIPE)
        try:
            while True:
                chunk = p.stdout.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            p.kill()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 18099), MJpegHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print("MJPEG 模拟源已启动 :18099/video")

from vivideye.capture.recorder import Recorder  # noqa: E402

def audio_url_not_in(cmd: list) -> bool:
    return not any(str(config.get("capture.audio_url", "")) in x for x in cmd)


rec = Recorder(db=db)
cmd = rec._build_command()
check("record_audio=false 生效", "wav" not in cmd and audio_url_not_in(cmd),
      f"命令不含音频输入: {' '.join(cmd[:8])}...")

rec.start()
time.sleep(13)          # segment_seconds=3：预期 ~4 个切片
mid_status = rec.status()
rec.stop()
time.sleep(0.5)

final_status = rec.status()
seg_files = sorted(RAW.glob("seg_*.mp4"))
t0 = time.time() - 60
live_files = [p for p in seg_files
              if p.stat().st_mtime > t0 and p.stat().st_size > 10000]
registered = db.pending_segments(limit=100)
registered_live = [x for x in registered
                   if Path(x["path"]).stat().st_mtime > t0
                   and Path(x["path"]).stat().st_size > 10000]

check("录制中 running", mid_status["running"] is True, str(
    {k: mid_status[k] for k in ("running", "ffmpeg_pid", "restarts")}))
check("切片产出 >= 3", len(live_files) >= 3,
      " ".join(p.name for p in live_files))
check("无异常重启", final_status["restarts"] == 0, f"restarts={final_status['restarts']}")
check("切片已注册 DB", len(registered_live) >= 2,
      f"{len(registered_live)} 个 pending")
check("stop 后停止", final_status["running"] is False
      and final_status["ffmpeg_pid"] is None)
check("注册总数", final_status["segments_registered"] >= 2,
      f"segments_registered={final_status['segments_registered']}")

server.shutdown()

fails = [c for c in checks if not c[1]]
print(f"\n==== {len(checks) - len(fails)}/{len(checks)} 项通过 ====")
sys.exit(1 if fails else 0)
