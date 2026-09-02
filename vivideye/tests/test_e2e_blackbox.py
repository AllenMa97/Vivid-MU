#!/usr/bin/env python3
"""VividEye 端到端黑盒测试（PHONE-FIRST 场景，本机模拟手机 Termux 环境）。

只通过外部可观察行为（CLI、HTTP 接口、文件系统、DB）验证，不修改被测代码。
AI 链路两种 mock 方式并存：
    1. 测试脚本内 import 后 monkeypatch AIClient.analyze_frames（打桩，任务指定方式）；
    2. 本地 mock OpenAI 兼容 HTTP 服务（供子进程 CLI / main.py start 全链路离线跑通）。

内置组件（全部自包含，可重复运行）：
    - FakeMjpegCamera：25fps multipart MJPEG 假摄像头（支持 stall 卡流 / kill 崩溃 / restart 复活）
    - MockOpenAI：OpenAI 兼容 /chat/completions mock（返回固定高分判分 JSON）
    - 合成视频片段（ffmpeg testsrc）

覆盖阶段：
    A. main.py start 基本链路：切片持续产出、看门狗不误杀、/api/status、
       /api/highlights、/api/live 反代、SIGTERM 优雅退出、无孤儿 ffmpeg
    B. AI 打桩管线：高分多 moment 导出高光/缩略图/DB 记录、低分不导出
    C. Web 全接口回归：favorites、删除、媒体文件、digest 缓存、
       config 掩码热更新、参数校验、/api/pipeline/run
    D. 异常路径：摄像头卡流→看门狗、摄像头崩溃→自动重启恢复；
       磁盘水位暂停→恢复（S3 安全修复后 storage.* 只读：API 写入被忽略，
       恢复改走"直接编辑 user_config.yaml + 白名单段 POST 触发重载"）
    E. CLI：process-now（mock AI 全链路）、digest、status
    F. config.yaml / user_config.yaml 优先级探针

运行方式（仓库根 /workspace/vivideye 下）：
    /root/miniconda3/envs/gnt/bin/python tests/test_e2e_blackbox.py

所有测试产物放 /tmp（WORK 目录），不污染仓库 data/；
user_config.yaml / config.yaml 会在结束时恢复原状。
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
WORK = Path(tempfile.mkdtemp(prefix="vivideye_e2e_"))
LOGS = WORK / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
DATA = WORK / "data"
DB_PATH = DATA / "vivideye.db"
RAW = DATA / "raw"
HL_DIR = DATA / "highlights"
DIGEST_DIR = DATA / "digests"

# ----------------------------------------------------------------------
# 断言记录框架
# ----------------------------------------------------------------------
RESULTS: list[tuple[str, bool, str]] = []
BUGS: list[tuple[str, str, str]] = []      # (文件, 行为, 建议)
_CURRENT = {"test": "<setup>"}


def check(cond, msg: str, bug: tuple[str, str, str] | None = None) -> bool:
    """记录一条断言；失败且给出 bug 三元组时登记 bug 表。"""
    ok = bool(cond)
    RESULTS.append((_CURRENT["test"], ok, msg))
    mark = "PASS" if ok else "FAIL"
    print(f"    [{mark}] {msg}")
    if not ok and bug:
        BUGS.append(bug)
    return ok


def run_test(name: str, fn):
    print(f"\n== {name} ==")
    _CURRENT["test"] = name
    t0 = time.time()
    try:
        fn()
    except Exception as e:  # 测试自身异常也算失败，但继续后续阶段
        import traceback
        traceback.print_exc()
        RESULTS.append((name, False, f"测试函数异常：{e!r}"))
        print(f"    [FAIL] 测试函数异常：{e!r}")
    print(f"-- {name} 结束（{time.time() - t0:.1f}s）")


# ----------------------------------------------------------------------
# 基础设施工具
# ----------------------------------------------------------------------
def wait_for(pred, timeout: float, interval: float = 0.5, desc: str = "") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def seg_count() -> int:
    return len(list(RAW.glob("seg_*.mp4")))


def db_query(sql: str, args: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def db_exec(sql: str, args: tuple = ()):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def write_user_config(cfg: dict):
    (REPO_ROOT / "user_config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def atomic_write_user_config(cfg: dict):
    """原子写 user_config.yaml（tmp + os.replace，与 server 端写法一致）。

    供运行中的服务进程感知外部配置变更：进程内没有文件 watcher，
    但 POST /api/config 成功后会 `config._data = load_config()` 从磁盘
    整体重载，因此"直改文件 + 白名单段 POST"即可让新配置生效。
    """
    path = REPO_ROOT / "user_config.yaml"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    os.replace(tmp, path)


def base_cfg(cam_url: str, web_port: int, mock_port: int,
             min_free_gb: float = 0.0001, segment_seconds: int = 6,
             watchdog: int = 15) -> dict:
    return {
        "app": {"language": "zh_CN"},
        "capture": {
            "source_url": cam_url,
            "record_audio": False,
            "segment_seconds": segment_seconds,
            "video_codec": "copy",
            "retention_hours": 720,
            "restart_on_failure": True,
            "watchdog_seconds": watchdog,
        },
        "pipeline": {
            "run_interval_minutes": 9999,   # 关闭定时调度（启动时会空跑一轮）
            "max_segments_per_run": 8,
            "min_highlight_score": 0.55,
            "scene_mode": "auto",
            "sample_fps": 0.5,
        },
        "ai": {
            "provider": "compatible",
            "api_base": f"http://127.0.0.1:{mock_port}/v1",
            "api_key": "fake-key-e2e",
            "vision_model": "mock-vl",
            "audio_model": "mock-audio",
            "text_model": "mock-text",
            "fast_model": "mock-fast",
            "request_timeout": 10,
            "max_retries": 2,
        },
        "storage": {
            "db_path": str(DB_PATH),
            "raw_dir": str(RAW),
            "highlights_dir": str(HL_DIR),
            "digest_dir": str(DIGEST_DIR),
            "min_free_gb": min_free_gb,
        },
        "server": {
            "host": "127.0.0.1",
            "port": web_port,
            "live_stream_proxy": True,
        },
    }


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_main(phase: str, cfg: dict):
    """启动 `main.py start` 子进程（日志落 WORK/logs/<phase>.log）。"""
    write_user_config(cfg)
    log = LOGS / f"{phase}.log"
    fh = open(log, "wb")
    proc = subprocess.Popen(
        [PY, "main.py", "start"],
        cwd=str(REPO_ROOT), stdout=fh, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    return proc, log


def stop_main(proc: subprocess.Popen, timeout: float = 35) -> int:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait(timeout=5)


def wait_web(web_port: int, timeout: float = 30) -> bool:
    url = f"http://127.0.0.1:{web_port}/api/status"
    return wait_for(
        lambda: requests.get(url, timeout=2).status_code == 200, timeout)


def orphan_ffmpeg() -> str:
    """检查是否有遗留的 ffmpeg（命令行含 raw 目录路径）。"""
    r = subprocess.run(["pgrep", "-af", str(RAW)],
                        capture_output=True, text=True)
    return r.stdout.strip()


def http_get_json(url: str) -> dict:
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------
# 假 MJPEG 摄像头
# ----------------------------------------------------------------------
class FakeMjpegCamera:
    """multipart MJPEG 假摄像头：支持 stall（发 header 后停帧）/ kill / restart。"""

    def __init__(self, frames: list[bytes], fps: int = 25):
        self._frames = frames
        self._fps = fps
        self._state = {"stall": False, "stop": False}
        self._conns: set = set()
        self._lock = threading.Lock()
        self._server = None
        self.port = 0

    def _make_handler(self):
        cam = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/video":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                with cam._lock:
                    cam._conns.add(self.connection)
                i = 0
                try:
                    while not cam._state["stop"]:
                        while cam._state["stall"] and not cam._state["stop"]:
                            time.sleep(0.1)
                        if cam._state["stop"]:
                            break
                        jpg = cam._frames[i % len(cam._frames)]
                        i += 1
                        self.wfile.write(
                            b"--frame\r\nContent-Type: image/jpeg\r\n"
                            + f"Content-Length: {len(jpg)}\r\n\r\n".encode()
                            + jpg + b"\r\n")
                        self.wfile.flush()
                        time.sleep(1.0 / cam._fps)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with cam._lock:
                        cam._conns.discard(self.connection)

            def log_message(self, *a):
                pass

        return H

    def start(self, port: int = 0):
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), self._make_handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever,
                         daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/video"

    def stall(self, on: bool):
        self._state["stall"] = on

    def kill(self):
        """模拟摄像头进程崩溃：断开全部连接并关停 HTTP 服务。"""
        self._state["stop"] = True
        with self._lock:
            conns = list(self._conns)
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass
        self._server.shutdown()
        self._server.server_close()

    def restart(self):
        """同一端口复活。"""
        self._state = {"stall": False, "stop": False}
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", self.port), self._make_handler())
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever,
                         daemon=True).start()


# ----------------------------------------------------------------------
# mock OpenAI 兼容服务
# ----------------------------------------------------------------------
MOCK_VISION = {
    "score": 0.9,
    "title": "Mock高光：测试图案",
    "caption": "mock caption for e2e",
    "tags": ["mock", "e2e"],
    "subjects": ["testcat"],
    "moments": [{"start": 1.0, "end": 4.0}],
}


class MockOpenAI:
    """本地 OpenAI 兼容 /chat/completions mock（离线供子进程全链路使用）。"""

    def __init__(self, content: str):
        self._content = content
        self._calls = 0
        self.port = 0
        self._server = None

    def _make_handler(self):
        srv = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"mock-ai-ok")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                srv._calls += 1
                body = json.dumps({
                    "id": "chatcmpl-mock", "object": "chat.completion",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant",
                                             "content": srv._content}}],
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        return H

    def start(self):
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self._make_handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever,
                         daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()


# ----------------------------------------------------------------------
# 全局句柄（在 main 中初始化）
# ----------------------------------------------------------------------
CAMERA: FakeMjpegCamera | None = None
MOCK: MockOpenAI | None = None
USER_CFG_BACKUP: Path | None = None
REPO_USER_CFG = REPO_ROOT / "user_config.yaml"
REPO_CFG = REPO_ROOT / "config.yaml"
REPO_CFG_HAD_CONFIG_YAML = False


# ----------------------------------------------------------------------
# 阶段 A：start 基本链路
# ----------------------------------------------------------------------
def test_phase_a():
    web_port = free_port()
    proc, log = start_main("phase_a", base_cfg(CAMERA.url, web_port, MOCK.port))
    try:
        ok = wait_web(web_port, timeout=30)
        check(ok, "Web 服务启动，GET /api/status 可访问（HTTP 200）")
        if not ok:
            return

        # --- /api/status 结构 ---
        st = http_get_json(f"http://127.0.0.1:{web_port}/api/status")
        keys_ok = all(k in st for k in
                      ("recording", "pipeline", "db", "disk_free_gb", "server_time"))
        check(keys_ok, f"/api/status 字段齐全（recording/pipeline/db/disk_free_gb/server_time）")

        # --- /api/highlights 初始为空 ---
        hl = http_get_json(f"http://127.0.0.1:{web_port}/api/highlights")
        check(hl.get("items") == [] and hl.get("count") == 0,
              "GET /api/highlights 初始返回空列表")

        # --- /api/live 反代 ---
        try:
            with requests.get(f"http://127.0.0.1:{web_port}/api/live",
                              stream=True, timeout=(5, 10)) as r:
                ctype = r.headers.get("content-type", "")
                first = next(r.iter_content(4096), b"")
            has_soi = b"\xff\xd8" in first
            live_ok = (r.status_code == 200 and "multipart" in ctype
                       and has_soi)
            check(live_ok,
                  f"/api/live 反代 MJPEG 正常（content-type={ctype!r}, "
                  f"首块含 JPEG SOI={has_soi}）")
        except Exception as e:
            check(False, f"/api/live 反代异常：{e!r}")

        # --- raw 目录持续产出切片 ---
        got = wait_for(lambda: seg_count() >= 2, timeout=40)
        check(got, f"raw 目录持续产出切片文件（当前 {seg_count()} 个，"
                  f"segment_seconds=6 下 40 秒内应 ≥2 个）")

        # --- 录制状态（切片已在产出，raw 目录有新鲜写入） ---
        st = http_get_json(f"http://127.0.0.1:{web_port}/api/status")
        rec = st.get("recording") or {}
        check(rec.get("recording") is True,
              f"/api/status 显示录制中（实际 recording={rec}）")
        check(rec.get("source") == "heartbeat",
              "录制状态来自 recorder.json 心跳文件（capture 模块按约写心跳）",
              bug=("vivideye/capture/recorder.py + vivideye/server/app.py",
                   "Recorder 从不写 data/recorder.json 心跳文件，/api/status 只能退回 "
                   "raw 目录 mtime 启发式（source=raw_dir）；录制暂停/刚停止后最长 "
                   "1.5×segment_seconds 内仍误报 recording=true",
                   "Recorder 管理线程周期性把 status() 写入 data/recorder.json，"
                   "供 Web 状态接口优先读取"))
        check((DATA / "recorder.json").exists(),
              "录制运行期间 data/recorder.json 心跳文件存在（供 Web 状态探测）")

        # --- DB 注册片段 ---
        got_db = wait_for(
            lambda: db_query("SELECT COUNT(*) FROM segments")[0][0] >= 1,
            timeout=20)
        n_seg, n_new = (0, 0)
        if got_db:
            n_seg = db_query("SELECT COUNT(*) FROM segments")[0][0]
            n_new = db_query(
                "SELECT COUNT(*) FROM segments WHERE status='new'")[0][0]
        check(got_db and n_new == n_seg,
              f"切片注册进 DB（segments={n_seg}，其中 new={n_new}，应全部为 new）")

        # --- 看门狗不误杀：正常运行期无看门狗/异常退出日志 ---
        time.sleep(6)   # 再观察一个看门狗周期（watchdog_seconds=15）
        text = read_log(log)
        check("看门狗触发" not in text and "异常退出" not in text,
              "健康录制期间看门狗不误杀、ffmpeg 无异常退出")

        # --- SIGTERM 优雅退出 ---
        rc = stop_main(proc)
        text = read_log(log)
        check(rc == 0, f"SIGTERM 后进程退出码为 0（实际 {rc}）")
        check("收到信号 15" in text, "日志记录收到 SIGTERM")
        check("VividEye 已退出" in text, "日志记录优雅退出完成（'VividEye 已退出'）")
        check("Traceback" not in text, "退出过程无异常堆栈")
        check(proc.poll() is not None, "子进程已真正退出（非僵尸）")

        # --- 无孤儿 ffmpeg ---
        orph = orphan_ffmpeg()
        check(not orph, f"退出后无遗留 ffmpeg 进程（实际：{orph or '无'}）",
              bug=("vivideye/capture/recorder.py",
                   "stop() 与管理线程 _tick 存在竞态：_kill_proc 置 _proc=None 后，"
                   "正在执行中的 _tick 可能立即 _spawn 新 ffmpeg，最终无人回收成为孤儿进程",
                   "stop() 中先 join 管理线程再 kill ffmpeg，或 _tick spawn 前检查 _stop_event"))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)


# ----------------------------------------------------------------------
# 阶段 B：AI 打桩（monkeypatch AIClient.analyze_frames）
# ----------------------------------------------------------------------
STUB = {
    "score": 0.92,
    "title": "Stub高光：小猫开门记",
    "caption": "小猫踮起脚尖压门把手，得意地回头看镜头。",
    "tags": ["stub", "可爱"],
    "subjects": ["小猫"],
    "moments": [{"start": 0.5, "end": 2.5}, {"start": 3.0, "end": 4.5}],
}


def _fake_analyze_frames(self, frames_b64, audio_path=None, scene_mode="auto"):
    return dict(STUB)


def _make_synthetic_mp4(out: Path, seconds: int = 8):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-t", str(seconds), "-g", "25", "-c:v", "libx264",
         "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)],
        check=True)


def _probe_duration(path: Path) -> float:
    import re
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)],
                       capture_output=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                  r.stderr.decode("utf-8", "replace"))
    if not m:
        return -1.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def test_phase_b():
    sys.path.insert(0, str(REPO_ROOT))
    import vivideye.ai.client as ai_mod
    from vivideye.pipeline.orchestrator import PipelineService

    orig = ai_mod.AIClient.analyze_frames
    ai_mod.AIClient.analyze_frames = _fake_analyze_frames
    service = PipelineService()
    try:
        n_pending = db_query(
            "SELECT COUNT(*) FROM segments WHERE status='new'")[0][0]
        check(n_pending >= 1, f"阶段 A 的片段处于待处理状态（pending={n_pending}）")

        result = service.process_now()
        n_hl_before = db_query("SELECT COUNT(*) FROM highlights")[0][0]
        exp_hl = 2 * n_pending
        check(result.get("processed") == n_pending and result.get("failed") == 0,
              f"process_now 处理全部 {n_pending} 段且无失败"
              f"（实际 processed={result.get('processed')}, failed={result.get('failed')}）")
        check(result.get("highlights") == exp_hl,
              f"每段导出 2 个高光（打桩含 2 个 moments）：期望 {exp_hl}，"
              f"实际 {result.get('highlights')}")

        # 高光文件 + 缩略图
        mp4s = sorted(HL_DIR.glob("hl_*.mp4"))
        jpgs = sorted(HL_DIR.glob("hl_*.jpg"))
        check(len(mp4s) >= exp_hl and len(jpgs) >= exp_hl,
              f"高光目录产出 hl_*.mp4/{len(mp4s)} 与 hl_*.jpg/{len(jpgs)} 成对文件")
        if jpgs:
            check(jpgs[0].read_bytes()[:2] == b"\xff\xd8",
                  "缩略图为合法 JPEG（SOI 魔数）")

        # DB 记录
        rows = db_query(
            "SELECT title, score, tags, favorite, video_path, thumb_path, "
            "duration FROM highlights ORDER BY created_at DESC LIMIT 3")
        ok_db = rows and rows[0][0] == STUB["title"] and abs(rows[0][1] - 0.92) < 1e-6
        tags = json.loads(rows[0][2]) if rows else []
        check(ok_db and tags == STUB["tags"] and rows[0][3] == 0,
              f"DB 高光记录字段完整（title/score/tags/favorite，"
              f"实际 title={rows[0][0] if rows else None}）")
        check(all(Path(r[4]).is_file() and Path(r[5]).is_file() for r in rows),
              "DB 记录的 video_path/thumb_path 均真实存在")

        # 片段状态 done
        n_done = db_query(
            "SELECT COUNT(*) FROM segments WHERE status='done'")[0][0]
        n_after = db_query(
            "SELECT COUNT(*) FROM segments WHERE status='new'")[0][0]
        check(n_done == n_pending and n_after == 0,
              f"处理的片段全部标记 done（done={n_done}, 剩余 new={n_after}）")

        # 剪辑时长（MJPEG 全关键帧，应精确）
        if mp4s:
            d = _probe_duration(mp4s[0])
            check(abs(d - 2.0) < 0.35,
                  f"高光剪辑时长与 moments 一致（期望≈2.0s，实际 {d:.2f}s）")

        # ---- 低分路径：不导出高光 ----
        low = WORK / "synthetic_low.mp4"
        _make_synthetic_mp4(low)
        db_exec(
            "INSERT INTO segments (id, path, started_at, duration, size_bytes, status) "
            "VALUES (?,?,?,?,?, 'new')",
            ("e2elow" + uuid.uuid4().hex[:6], str(low), time.time(),
             8.0, low.stat().st_size))
        STUB["score"] = 0.1
        result2 = service.process_now()
        n_hl_after = db_query("SELECT COUNT(*) FROM highlights")[0][0]
        low_status = db_query(
            "SELECT status FROM segments WHERE path=?", (str(low),))[0][0]
        check(result2.get("failed") == 0 and n_hl_after == n_hl_before
              and low_status == "done",
              f"低分片段（score=0.1 < 0.55）不导出高光但正常标记 done"
              f"（highlights {n_hl_before}->{n_hl_after}，status={low_status}）")
    finally:
        ai_mod.AIClient.analyze_frames = orig
        STUB["score"] = 0.92
        try:
            service.db.close()
        except Exception:
            pass


# ----------------------------------------------------------------------
# 阶段 C：Web 全接口回归（TestClient）
# ----------------------------------------------------------------------
def test_phase_c():
    from fastapi.testclient import TestClient
    from vivideye.config import config
    from vivideye.server.app import create_app

    app = create_app()
    client = TestClient(app)
    try:
        # --- /api/status ---
        r = client.get("/api/status")
        st = r.json()
        check(r.status_code == 200 and st["db"]["highlights_total"] > 0,
              f"/api/status 反映 DB 统计（highlights_total={st['db'].get('highlights_total')}）")

        # --- /api/highlights 列表/分页/过滤 ---
        r = client.get("/api/highlights?limit=2&offset=0")
        j = r.json()
        check(r.status_code == 200 and len(j["items"]) == 2 and j["count"] == 2,
              "/api/highlights 分页（limit=2）")
        total = client.get("/api/highlights?limit=200").json()["count"]
        check(total >= 2, f"/api/highlights 总数正确（total={total}）")
        r = client.get("/api/highlights?favorite=true")
        check(r.json()["count"] == 0, "/api/highlights?favorite=true 初始为空")
        r = client.get("/api/highlights?tag=stub")
        check(r.json()["count"] >= 1, "/api/highlights?tag=stub 按标签筛选命中")

        hid = j["items"][0]["id"]
        hid2 = j["items"][1]["id"] if len(j["items"]) > 1 else hid

        # --- favorite ---
        r = client.post(f"/api/highlights/{hid}/favorite",
                        json={"favorite": True})
        check(r.status_code == 200 and r.json()["favorite"] is True,
              "POST favorite=true 收藏成功")
        r = client.get("/api/highlights?favorite=true")
        check(r.json()["count"] == 1
              and r.json()["items"][0]["id"] == hid,
              "收藏后 ?favorite=true 命中该条")
        r = client.post(f"/api/highlights/{hid}/favorite",
                        json={"favorite": False})
        check(r.status_code == 200 and r.json()["favorite"] is False,
              "POST favorite=false 取消收藏")
        r = client.post("/api/highlights/no-such-id/favorite",
                        json={"favorite": True})
        check(r.status_code == 404, "收藏不存在的高光返回 404")

        # --- 媒体文件 ---
        r = client.get(f"/api/highlights/{hid}/video")
        check(r.status_code == 200
              and "video/mp4" in r.headers.get("content-type", ""),
              "GET /api/highlights/{id}/video 返回 mp4")
        r = client.get(f"/api/highlights/{hid}/thumb")
        check(r.status_code == 200
              and "image/jpeg" in r.headers.get("content-type", ""),
              "GET /api/highlights/{id}/thumb 返回 jpeg")
        r = client.get("/api/highlights/no-such-id/video")
        check(r.status_code == 404, "视频接口对不存在 id 返回 404")

        # --- digest（web 版） ---
        r = client.get("/api/digest")
        jd = r.json()
        check(r.status_code == 200 and "# " in jd.get("markdown", "")
              and jd.get("stats", {}).get("total", 0) > 0,
              f"GET /api/digest 生成日报（stats.total={jd.get('stats', {}).get('total')}）")
        r2 = client.get("/api/digest")
        check(r2.json().get("cached") is True, "同日再次 GET /api/digest 命中缓存")
        r = client.get("/api/digest?date=2099-13-99")
        check(r.status_code == 400, "非法 date 参数返回 400")

        # --- config 掩码 & 热更新 ---
        r = client.get("/api/config")
        jc = r.json()
        check(jc.get("ai", {}).get("api_key") == "******",
              "GET /api/config 对 api_key 打码展示")

        r = client.post("/api/config", json={"ai": {"api_key": "******"}})
        check(r.status_code == 200 and r.json().get("ok") is True,
              "POST /api/config 提交掩码占位被接受")
        text = REPO_USER_CFG.read_text(encoding="utf-8")
        check("******" not in text and "fake-key-e2e" in text,
              "掩码占位不会写回 user_config.yaml（真实密钥不被覆盖）")

        r = client.post("/api/config",
                        json={"pipeline": {"scene_mode": "pet"}})
        ok_hot = (r.status_code == 200 and r.json().get("ok") is True
                  and config.get("pipeline.scene_mode") == "pet")
        check(ok_hot, "POST /api/config 热更新内存配置（scene_mode=pet 立即生效）")

        r = client.post("/api/config",
                        json={"pipeline": {"scene_mode": "bogus"}})
        check(r.status_code == 400, "非法 scene_mode 返回 400")

        r = client.post("/api/config", json={})
        check(r.status_code == 400, "空请求体返回 400")

        r = client.post("/api/config", json={"hack_section": {"x": 1}})
        check(r.status_code == 200 and r.json().get("ok") is False,
              "非白名单段被拒绝（ok=False，不落盘）")

        # --- /api/pipeline/run（预期应可触发；实际因导入错误 503 → BUG） ---
        r = client.post("/api/pipeline/run")
        check(r.status_code == 200 and r.json().get("started") is True,
              f"POST /api/pipeline/run 可触发立即处理（实际 HTTP {r.status_code}："
              f"{r.json().get('detail') if r.status_code != 200 else ''}）",
              bug=("vivideye/server/app.py",
                   "_pipeline_available() 尝试 `from vivideye.pipeline.orchestrator "
                   "import process_now`，但 orchestrator 只有 PipelineService 类、"
                   "无模块级 process_now 函数 → /api/pipeline/run 永远 503，"
                   "前端“立即处理”按钮完全失效",
                   "改为 from vivideye.pipeline.orchestrator import PipelineService，"
                   "在线程中执行 PipelineService().process_now()"))

        # --- 删除高光 ---
        del_row = db_query(
            "SELECT video_path, thumb_path FROM highlights WHERE id=?", (hid2,))[0]
        del_files = [Path(p) for p in del_row if p]
        r = client.delete(f"/api/highlights/{hid2}")
        check(r.status_code == 200 and r.json().get("ok") is True,
              "DELETE /api/highlights/{id} 删除成功")
        gone_db = db_query(
            "SELECT COUNT(*) FROM highlights WHERE id=?", (hid2,))[0][0] == 0
        gone_files = all(not p.is_file() for p in del_files)
        check(gone_db, "删除后 DB 记录消失")
        check(gone_files, "删除后高光目录内的媒体文件被清理")
        r = client.delete(f"/api/highlights/{hid2}")
        check(r.status_code == 404, "重复删除返回 404")
    finally:
        try:
            app.state.db.close()
        except Exception:
            pass


# ----------------------------------------------------------------------
# 阶段 D1：摄像头异常（卡流→看门狗；崩溃→自动重启恢复）
# ----------------------------------------------------------------------
def test_phase_d1():
    web_port = free_port()
    proc, log = start_main("phase_d1", base_cfg(CAMERA.url, web_port, MOCK.port))
    try:
        ok = wait_web(web_port, timeout=30)
        check(ok, "D1：Web 服务启动")
        if not ok:
            return
        got = wait_for(lambda: seg_count() >= 1, timeout=30)
        check(got, "D1：正常录制中（首切片出现）")
        base_cnt = seg_count()

        # --- 1) 卡流：看门狗触发 ---
        CAMERA.stall(True)
        triggered = wait_for(
            lambda: "看门狗触发" in read_log(log), timeout=35)
        check(triggered, "摄像头卡流（连接保持但停帧）→ 看门狗触发重启日志")
        CAMERA.stall(False)
        recovered1 = wait_for(lambda: seg_count() > base_cnt, timeout=30)
        check(recovered1,
              f"卡流恢复后续录（切片 {base_cnt} -> {seg_count()}）")

        # --- 2) 崩溃：断流自动重启 ---
        cnt_before_kill = seg_count()
        CAMERA.kill()
        time.sleep(10)   # ffmpeg 读到 EOF 退出，管理线程 5s tick 检出并重启
        text = read_log(log)
        exited = "异常退出" in text or "看门狗触发" in text
        check(exited, "摄像头被杀后日志记录 ffmpeg 异常退出/看门狗触发")
        check("即将自动重启" in text,
              "restart_on_failure=true 时日志声明自动重启")

        CAMERA.restart()
        recovered2 = wait_for(lambda: seg_count() > cnt_before_kill, timeout=40)
        check(recovered2,
              f"摄像头复活后自动恢复录制（切片 {cnt_before_kill} -> {seg_count()}）")

        rc = stop_main(proc)
        check(rc == 0, f"D1：SIGTERM 退出码 0（实际 {rc}）")
    finally:
        CAMERA.stall(False)
        if CAMERA._server is None or CAMERA.port == 0:
            pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)


# ----------------------------------------------------------------------
# 阶段 D2：磁盘水位暂停 / 恢复（S3 后 storage.* 只读，恢复走配置文件）
# ----------------------------------------------------------------------
def test_phase_d2():
    web_port = free_port()
    cfg = base_cfg(CAMERA.url, web_port, MOCK.port, min_free_gb=999999)
    proc, log = start_main("phase_d2", cfg)
    base_api = f"http://127.0.0.1:{web_port}"
    try:
        ok = wait_web(web_port, timeout=30)
        check(ok, "D2：Web 服务启动（min_free_gb=999999）")
        if not ok:
            return
        paused = wait_for(lambda: "暂停录制" in read_log(log), timeout=25)
        check(paused, "磁盘剩余低于阈值 → 日志'暂停录制'（录制器停止 ffmpeg）")

        cnt_at_pause = seg_count()
        time.sleep(14)   # 超过 1.5×segment_seconds 的启发式窗口
        check(seg_count() == cnt_at_pause,
              f"暂停期间 raw 目录无新切片（{cnt_at_pause} 个不变）")

        st = http_get_json(f"{base_api}/api/status")
        check(st.get("recording", {}).get("recording") is False,
              f"暂停期间 /api/status 显示未录制"
              f"（实际 {st.get('recording')}）")

        # --- S3 安全特性：POST /api/config 不允许写 storage.* ---
        r = requests.post(f"{base_api}/api/config",
                          json={"storage": {"min_free_gb": 0.0001}}, timeout=5)
        body = r.json()
        msg = body.get("message") or ""
        check(r.status_code == 200 and body.get("ok") is False
              and "只读" in msg and "storage" in msg,
              f"S3 安全特性：POST /api/config 尝试改 storage.min_free_gb 被忽略"
              f"（ok=False，message 提示只读并列出字段，实际 message={msg!r}）")
        disk_cfg = yaml.safe_load(REPO_USER_CFG.read_text(encoding="utf-8")) or {}
        check(disk_cfg.get("storage", {}).get("min_free_gb") == 999999,
              "被忽略的 storage 写入不落盘（user_config.yaml 中 min_free_gb 仍为 999999）")

        # --- 恢复：直接编辑 user_config.yaml（storage 已不能走 API） ---
        # 运行中进程没有配置文件 watcher：内存配置单例仅在 POST /api/config
        # 成功后从磁盘重载（config._data = load_config()）。因此先原子写
        # user_config.yaml 恢复水位，再 POST 一个白名单段（app.language
        # 原值回写，无实际变更）触发重载，60s 巡检周期内生效。
        cfg["storage"]["min_free_gb"] = 0.0001
        atomic_write_user_config(cfg)
        r2 = requests.post(f"{base_api}/api/config",
                           json={"app": {"language": "zh_CN"}}, timeout=5)
        check(r2.status_code == 200 and r2.json().get("ok") is True,
              "白名单段 POST 触发配置从磁盘重载（app.language 原值回写，无实际变更）")

        resumed = wait_for(lambda: "继续录制" in read_log(log), timeout=80)
        check(resumed, "磁盘水位恢复（直接编辑 user_config.yaml）→ "
                       "日志'继续录制'（60s 巡检周期内生效）")
        got = wait_for(lambda: seg_count() > cnt_at_pause, timeout=40)
        check(got, f"恢复后继续产出切片（{cnt_at_pause} -> {seg_count()}）")

        rc = stop_main(proc)
        check(rc == 0, f"D2：SIGTERM 退出码 0（实际 {rc}）")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)


# ----------------------------------------------------------------------
# 阶段 E：CLI 子命令
# ----------------------------------------------------------------------
def test_phase_e():
    today = datetime.now().strftime("%Y-%m-%d")

    # --- process-now（mock AI 全链路）---
    seg = WORK / "synthetic_cli.mp4"
    _make_synthetic_mp4(seg)
    seg_id = "e2ecli" + uuid.uuid4().hex[:6]
    db_exec(
        "INSERT INTO segments (id, path, started_at, duration, size_bytes, status) "
        "VALUES (?,?,?,?,?, 'new')",
        (seg_id, str(seg), time.time(), 8.0, seg.stat().st_size))

    r = subprocess.run([PY, "main.py", "process-now"], cwd=str(REPO_ROOT),
                       capture_output=True, timeout=180,
                       env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
    err = r.stderr.decode("utf-8", "replace")
    check(r.returncode == 0, f"CLI process-now 退出码 0（实际 {r.returncode}）")
    check("failed\": 0" in err.replace(" ", "") or "失败 0 段" in err,
          "CLI process-now 日志显示无失败段落")
    row = db_query(
        "SELECT status FROM segments WHERE id=?", (seg_id,))
    check(row and row[0][0] == "done",
          f"CLI process-now 处理后合成片段标记 done（实际 {row}）")
    hl_rows = db_query(
        "SELECT title, score, duration, video_path, thumb_path FROM highlights "
        "WHERE segment_id=?", (seg_id,))
    check(len(hl_rows) == 1 and hl_rows[0][0] == MOCK_VISION["title"]
          and abs(hl_rows[0][1] - 0.9) < 1e-6,
          f"mock AI 高光入库（title={hl_rows[0][0] if hl_rows else None}, "
          f"score={hl_rows[0][1] if hl_rows else None}）")
    if hl_rows:
        d = _probe_duration(Path(hl_rows[0][3]))
        check(hl_rows[0][2] > 0 and abs(d - 3.0) < 1.3,
              f"CLI 高光剪辑文件时长合理（DB={hl_rows[0][2]}s，实测 {d:.2f}s）")
        check(Path(hl_rows[0][4]).is_file(), "CLI 高光缩略图存在")

    # --- process-now 空转 ---
    r = subprocess.run([PY, "main.py", "process-now"], cwd=str(REPO_ROOT),
                      capture_output=True, timeout=60,
                      env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
    err = r.stderr.decode("utf-8", "replace")
    check(r.returncode == 0 and "没有待处理的片段" in err,
          "CLI process-now 无待处理片段时空转退出（code 0）")

    # --- digest ---
    r = subprocess.run([PY, "main.py", "digest", "--date", today],
                      cwd=str(REPO_ROOT), capture_output=True, timeout=60,
                      env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
    out = r.stdout.decode("utf-8", "replace").strip()
    err = r.stderr.decode("utf-8", "replace")
    check(r.returncode == 0, f"CLI digest 退出码 0（实际 {r.returncode}）")
    md_path = Path(out.splitlines()[-1]) if out else None
    check(md_path is not None and md_path.is_file(),
          f"CLI digest stdout 输出日报路径且文件存在（{md_path}）")
    if md_path and md_path.is_file():
        md = md_path.read_text(encoding="utf-8")
        check("今日精选" in md and ("Stub高光" in md or "Mock高光" in md),
              "CLI 日报 Markdown 含榜单与高光标题")
    drow = db_query("SELECT markdown_path, stats FROM digests WHERE date=?",
                    (today,))
    check(bool(drow) and drow[0][0] == str(md_path),
          "CLI digest 结果入库（digests 表）")

    # 同一天 web 版与 CLI 版日报文件并存 → 命名不统一
    web_md = DIGEST_DIR / f"digest-{today}.md"
    cli_md = DIGEST_DIR / f"digest_{today}.md"
    both = web_md.is_file() and cli_md.is_file()
    check(not both,
          f"同一天只应有一份日报文件（实际 web={web_md.is_file()}, "
          f"cli={cli_md.is_file()}）",
          bug=("vivideye/pipeline/digest.py + vivideye/server/app.py",
               "CLI 生成 digest_YYYY-MM-DD.md（stats 键 highlight_count），"
               "Web 生成 digest-YYYY-MM-DD.md（stats 键 total）；两套命名与缓存键"
               "互不兼容：CLI 产物不会被 Web 缓存复用（stats.total 恒缺失导致"
               "count 比对失败重新生成），同一天会堆积两份内容不一致的日报",
               "统一日报文件名与 stats 结构（如统一用 total+highlight_count），"
               "Web 缓存判断兼容两种 stats 键"))

    # --- status ---
    r = subprocess.run([PY, "main.py", "status"], cwd=str(REPO_ROOT),
                       capture_output=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
    out = r.stdout.decode("utf-8", "replace")
    n_seg = db_query("SELECT COUNT(*) FROM segments")[0][0]
    n_hl = db_query("SELECT COUNT(*) FROM highlights")[0][0]
    check(r.returncode == 0 and "=== VividEye 状态 ===" in out,
          "CLI status 退出码 0 且输出标题")
    check(f"片段 {n_seg}" in out and f"高光 {n_hl}" in out,
          f"CLI status 统计与 DB 一致（片段={n_seg}，高光={n_hl}）")
    check("待处理 0" in out, "CLI status 显示待处理为 0（已全部处理）")


# ----------------------------------------------------------------------
# 阶段 F：config.yaml / user_config.yaml 优先级
# ----------------------------------------------------------------------
def test_phase_f():
    web_port = free_port()
    cfg = base_cfg(CAMERA.url, web_port, MOCK.port)
    cfg["capture"]["segment_seconds"] = 222          # user 层
    write_user_config(cfg)
    REPO_CFG.write_text("capture:\n  segment_seconds: 111\n",
                        encoding="utf-8")            # 项目层
    log = LOGS / "phase_f.log"
    fh = open(log, "wb")
    proc = subprocess.Popen(
        [PY, "main.py", "start"], cwd=str(REPO_ROOT),
        stdout=fh, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
    try:
        got = wait_for(lambda: "录制器已启动" in read_log(log), timeout=30)
        text = read_log(log)
        m = "切片=222s" in text
        check(got and m,
              f"user_config.yaml 应优先于 config.yaml（期望日志'切片=222s'，"
              f"实际：{[l for l in text.splitlines() if '录制器已启动' in l] or '未启动'}）",
              bug=("vivideye/config.py",
                   "load_config 候选顺序为 [user_config.yaml, config.yaml]，"
                   "循环合并使 config.yaml 反而覆盖 user_config.yaml，"
                   "与模块 docstring 声明的分层顺序（user_config 最后生效）相反；"
                   "两个文件并存时用户个性化配置失效",
                   "调换候选顺序为 [config.yaml, user_config.yaml]，"
                   "让用户层配置最后合并生效"))
    finally:
        stop_main(proc, timeout=20)
        REPO_CFG.unlink(missing_ok=True)
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main() -> int:
    global CAMERA, MOCK, USER_CFG_BACKUP, REPO_CFG_HAD_CONFIG_YAML

    print(f"Python: {PY}")
    print(f"仓库: {REPO_ROOT}")
    print(f"测试产物目录: {WORK}")

    # 备份仓库现有 user_config.yaml / config.yaml
    if REPO_USER_CFG.exists():
        USER_CFG_BACKUP = WORK / "user_config.yaml.bak"
        shutil.copy(REPO_USER_CFG, USER_CFG_BACKUP)
    REPO_CFG_HAD_CONFIG_YAML = REPO_CFG.exists()
    if REPO_CFG_HAD_CONFIG_YAML:
        shutil.copy(REPO_CFG, WORK / "config.yaml.bak")

    # 假摄像头帧（ffmpeg testsrc 生成）
    frames_dir = WORK / "frames"
    frames_dir.mkdir()
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=5",
         "-t", "2", "-q:v", "7", str(frames_dir / "frame_%03d.jpg")],
        check=True)
    frames = [p.read_bytes() for p in sorted(frames_dir.glob("frame_*.jpg"))]
    assert frames, "生成假摄像头帧失败"

    CAMERA = FakeMjpegCamera(frames, fps=25)
    CAMERA.start()
    MOCK = MockOpenAI(json.dumps(MOCK_VISION, ensure_ascii=False))
    MOCK.start()
    print(f"假摄像头: {CAMERA.url}")
    print(f"mock AI:  http://127.0.0.1:{MOCK.port}/v1/chat/completions")

    t0 = time.time()
    try:
        run_test("A. start 基本链路（切片/看门狗/状态接口/SIGTERM）", test_phase_a)
        run_test("B. AI 打桩管线（monkeypatch analyze_frames）", test_phase_b)
        run_test("C. Web 全接口回归（收藏/删除/配置掩码/digest）", test_phase_c)
        run_test("D1. 摄像头异常恢复（看门狗/自动重启）", test_phase_d1)
        run_test("D2. 磁盘水位暂停与恢复（storage 只读 + 直改配置文件）", test_phase_d2)
        run_test("E. CLI 子命令（process-now/digest/status）", test_phase_e)
        run_test("F. 配置文件优先级（user_config vs config）", test_phase_f)
    finally:
        # 清理：杀孤儿 ffmpeg、停服务器、恢复仓库配置文件
        r = subprocess.run(["pkill", "-f", str(RAW)], capture_output=True)
        if CAMERA is not None:
            try:
                CAMERA.kill()
            except Exception:
                pass
        if MOCK is not None:
            MOCK.stop()
        if USER_CFG_BACKUP is not None:
            shutil.copy(USER_CFG_BACKUP, REPO_USER_CFG)
        elif REPO_USER_CFG.exists():
            REPO_USER_CFG.unlink()
        if REPO_CFG_HAD_CONFIG_YAML:
            shutil.copy(WORK / "config.yaml.bak", REPO_CFG)
        elif REPO_CFG.exists():
            REPO_CFG.unlink()
        print(f"\n清理完成（孤儿 ffmpeg 清理退出码 {r.returncode}；"
              f"user_config.yaml/config.yaml 已恢复原状）")

    # ---- 汇总 ----
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = total - passed
    print("\n" + "=" * 72)
    print(f"测试结果：{passed}/{total} 通过，{failed} 失败（总耗时 "
          f"{time.time() - t0:.0f}s）")
    if failed:
        print("\n失败断言清单：")
        for name, ok, msg in RESULTS:
            if not ok:
                print(f"  [{name}] {msg}")
    if BUGS:
        print("\n登记 BUG 表：")
        seen = set()
        uniq = []
        for bug in BUGS:
            if bug not in seen:
                seen.add(bug)
                uniq.append(bug)
        for i, (f, beh, sug) in enumerate(uniq, 1):
            print(f"  {i}. 文件: {f}\n     行为: {beh}\n     建议: {sug}")
    print(f"\n测试产物（日志/数据）保留在: {WORK}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
