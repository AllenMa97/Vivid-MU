#!/usr/bin/env python3
"""复现实验：带不可达音频输入的 ffmpeg 连续两次 spawn 的行为。"""
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAW = Path("/workspace/.vividtest/repro/raw")
RAW.mkdir(parents=True, exist_ok=True)
for p in RAW.glob("seg_*.mp4"):
    p.unlink()


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        p = subprocess.Popen([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-re", "-stream_loop", "-1",
            "-f", "lavfi", "-i", "testsrc=rate=10:size=320x240",
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

    def log_message(self, *a):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 18099), H)
threading.Thread(target=server.serve_forever, daemon=True).start()


def spawn(with_audio: bool):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
           "-f", "mjpeg", "-i", "http://127.0.0.1:18099/video"]
    if with_audio:
        cmd += ["-f", "wav", "-i", "http://127.0.0.1:8080/audio.wav",
                "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"]
    else:
        cmd += ["-map", "0:v:0"]
    cmd += ["-c:v", "copy", "-f", "segment", "-segment_time", "5",
            "-segment_format", "mp4", "-reset_timestamps", "1",
            "-strftime", "1", str(RAW / "seg_%Y%m%d_%H%M%S.mp4")]
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    errs = []
    deadline = time.time() + 12
    while time.time() < deadline:
        rc = p.poll()
        if rc is not None:
            errs = p.stderr.read().decode("utf-8", "replace").strip().splitlines()
            print(f"  退出 code={rc} 存活 {time.time()-t0:.1f}s")
            for e in errs[-4:]:
                print(f"    | {e[:140]}")
            return
        time.sleep(0.5)
    print(f"  12s 后仍存活 pid={p.pid}")
    p.send_signal(2)
    try:
        p.wait(timeout=3)
    except Exception:
        p.kill()


print("== 会话1（带 audio，预期崩） ==")
spawn(True)
print("== 会话2（带 audio，复现测试B的第二次） ==")
spawn(True)
print("== 会话3（不带 audio，预期正常录制） ==")
spawn(False)
files = sorted(RAW.glob("seg_*.mp4"))
print(f"产出切片 {len(files)} 个:", [f.name for f in files])
server.shutdown()
sys.exit(0)
