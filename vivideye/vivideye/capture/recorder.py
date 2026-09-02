"""循环录制器：用 ffmpeg 拉取手机本地 MJPEG 流并按固定时长切片落盘。

PHONE-FIRST / Termux 设计要点：
- 只依赖 PATH 中的 ffmpeg，全部使用各版本通用的命令行参数
  （不做 --version 探测，不用冷门选项）；
- 视频输入为 IP 摄像头 App 提供的 loopback MJPEG 流（capture.source_url），
  可选叠加音频流（capture.audio_url，wav 流）；
- 切片由 ffmpeg 的 segment muxer + strftime 完成，产出
  ``seg_YYYYmmdd_HHMMSS.mp4``；
- 内置管理线程（daemon）负责：
  * 把已完成的片段注册进数据库（供管线取用）；
  * 看门狗：活跃文件长时间无写入则重启 ffmpeg（capture.watchdog_seconds）；
  * 崩溃自动重启（capture.restart_on_failure）；
  * 磁盘水位：低于 storage.min_free_gb 时暂停录制，恢复后自动续录。
"""

from __future__ import annotations

import logging
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from vivideye.config import Config, config
from vivideye.paths import resolve_path
from vivideye.capture.retention import RetentionReport, run_retention
from vivideye.storage.db import HighlightsDB

logger = logging.getLogger(__name__)

# 片段文件名格式：seg_YYYYmmdd_HHMMSS.mp4
_SEG_RE = re.compile(r"^seg_(\d{8}_\d{6})\.mp4$")
_SEG_TIME_FMT = "%Y%m%d_%H%M%S"

# 管理线程巡检周期（秒）
_TICK_SECONDS = 5.0
# 磁盘水位 / 保留期清理的执行周期（秒）
_RETENTION_INTERVAL = 60.0


def _parse_seg_time(name: str) -> Optional[datetime]:
    """从片段文件名解析录制起始时间；解析失败返回 None。"""
    m = _SEG_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), _SEG_TIME_FMT)
    except ValueError:
        return None


def _as_bool(value: Any) -> bool:
    """宽容解析布尔配置值。

    配置支持环境变量覆盖（VIVIDEYE_CAPTURE__RECORD_AUDIO=false），
    覆盖值是字符串，``bool("false")`` 会误判为 True，必须按内容解析。
    """
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


class Recorder:
    """MJPEG 流循环录制器。

    用法::

        rec = Recorder(db)
        rec.start()
        ...
        rec.status()   # {"running": True, ...}
        rec.stop()
    """

    def __init__(self, db: Optional[HighlightsDB] = None, cfg: Config | None = None):
        self._cfg = cfg or config
        self.raw_dir = resolve_path(self._cfg.get("storage.raw_dir", "data/raw"), self._cfg)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._db = db if db is not None else HighlightsDB(
            resolve_path(self._cfg.get("storage.db_path", "data/vivideye.db"), self._cfg))

        # 进程与线程状态
        self._proc: Optional[subprocess.Popen] = None
        self._manager: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 运行统计
        self._paused = False                      # 磁盘不足导致的暂停
        self._restarts = 0
        self._started_at: Optional[float] = None
        self._last_output_at: Optional[float] = None   # 最后一次观察到写入的时间（看门狗用）
        self._registered: set[str] = set()        # 已注册 DB 的文件名（进程内去重）
        self._registered_count = 0
        self._last_retention: Optional[RetentionReport] = None
        self._last_retention_at = 0.0
        self._last_error = ""
        self._stderr_tail: list[str] = []

        # 看门狗：跟踪“本次 ffmpeg 会话”正在写入的活跃文件
        self._baseline_names: set[str] = set()    # 本次 spawn 前已存在的文件（不算活跃）
        self._active_name: Optional[str] = None
        self._active_size: int = -1

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动录制器（幂等：重复调用无副作用）。"""
        if self._manager is not None and self._manager.is_alive():
            logger.warning("录制器已在运行，忽略重复 start()")
            return
        self._stop_event.clear()
        self._paused = False
        self._started_at = time.time()
        # 先注册上次运行遗留的片段（ffmpeg 未启动，全部视为已完成）
        self._register_segments(self._list_seg_files(), final=True)
        self._manager = threading.Thread(
            target=self._run, name="vivideye-recorder", daemon=True)
        self._manager.start()
        logger.info("录制器已启动：源=%s 切片=%ds 目录=%s",
                    self._cfg.get("capture.source_url"),
                    self._cfg.get("capture.segment_seconds"), self.raw_dir)

    def stop(self) -> None:
        """停止录制器：优雅结束 ffmpeg（SIGINT 让其写完文件头尾），注册最后的片段。"""
        self._stop_event.set()
        self._kill_proc()
        if self._manager is not None:
            self._manager.join(timeout=20)
            self._manager = None
        # ffmpeg 退出后最后一个活跃文件也已收尾，补注册
        self._register_segments(self._list_seg_files(), final=True)
        logger.info("录制器已停止（累计重启 %d 次，注册片段 %d 个）",
                    self._restarts, self._registered_count)

    def status(self) -> dict[str, Any]:
        """返回录制器运行状态快照。"""
        manager_alive = self._manager is not None and self._manager.is_alive()
        proc = self._proc
        pid = proc.pid if (proc is not None and proc.poll() is None) else None
        now = time.time()
        return {
            "running": manager_alive and not self._paused,
            "manager_alive": manager_alive,
            "paused": self._paused,
            "ffmpeg_pid": pid,
            "started_at": self._started_at,
            "restarts": self._restarts,
            "segments_registered": self._registered_count,
            "last_output_at": self._last_output_at,
            "seconds_since_last_output": (
                now - self._last_output_at if self._last_output_at else None),
            "free_gb": self._last_retention.free_gb if self._last_retention else None,
            "last_error": self._last_error,
        }

    @property
    def db(self) -> HighlightsDB:
        return self._db

    # ------------------------------------------------------------------
    # 管理线程主循环
    # ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                # 管理线程自身绝不能崩：任何异常都记录后继续下一轮
                logger.exception("录制器管理线程异常")
            self._stop_event.wait(_TICK_SECONDS)

    def _tick(self) -> None:
        now = time.time()
        self._maybe_retention(now)
        if self._paused:
            return

        files = self._list_seg_files()
        # 注册已完成片段（活跃文件除外）
        self._register_segments(files, final=False)

        proc = self._proc
        if proc is None or proc.poll() is not None:
            if proc is not None:
                # ffmpeg 异常退出（主动停止时会先置 None）
                self._on_ffmpeg_exit(proc)
            if _as_bool(self._cfg.get("capture.restart_on_failure", True)):
                self._spawn()
            else:
                self._last_error = "ffmpeg 已退出且 restart_on_failure=false，录制停止"
                logger.error(self._last_error)
                self._stop_event.set()
            return

        # 看门狗：活跃文件持续无写入则重启
        self._watchdog_check(files, now)

    # ------------------------------------------------------------------
    # 磁盘水位
    # ------------------------------------------------------------------
    def _maybe_retention(self, now: float) -> None:
        """周期性执行保留期清理与磁盘水位检查；空间不足时暂停录制。"""
        if now - self._last_retention_at < _RETENTION_INTERVAL:
            return
        self._last_retention_at = now
        report = run_retention(cfg=self._cfg)
        self._last_retention = report
        if report.disk_ok:
            if self._paused:
                logger.info("磁盘剩余 %.2f GB 已恢复（阈值 %.2f GB），继续录制",
                            report.free_gb, report.min_free_gb)
                self._paused = False
        elif not self._paused:
            logger.warning("磁盘剩余 %.2f GB 低于阈值 %.2f GB，暂停录制",
                           report.free_gb, report.min_free_gb)
            self._paused = True
            self._kill_proc()

    # ------------------------------------------------------------------
    # ffmpeg 进程管理
    # ------------------------------------------------------------------
    def _build_command(self) -> list[str]:
        """构造 ffmpeg 录制命令（只使用通用参数，兼容 Termux 版本）。"""
        cfg = self._cfg
        source = str(cfg.get("capture.source_url", ""))
        seg_seconds = int(float(cfg.get("capture.segment_seconds", 600)))
        codec = str(cfg.get("capture.video_codec", "copy")).lower()
        audio_url = str(cfg.get("capture.audio_url", "") or "")
        with_audio = _as_bool(cfg.get("capture.record_audio", True)) and bool(audio_url)

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "mjpeg", "-i", source]
        if with_audio:
            cmd += ["-f", "wav", "-i", audio_url]

        cmd += ["-map", "0:v:0"]
        if with_audio:
            cmd += ["-map", "1:a:0"]

        if codec == "h264":
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]
        else:
            cmd += ["-c:v", "copy"]
        if with_audio:
            # wav(pcm) 流无法直拷进 mp4，统一转码为低码率 aac
            cmd += ["-c:a", "aac", "-b:a", "64k"]

        cmd += ["-f", "segment",
                "-segment_time", str(seg_seconds),
                "-segment_format", "mp4",
                "-reset_timestamps", "1",
                "-strftime", "1",
                str(self.raw_dir / "seg_%Y%m%d_%H%M%S.mp4")]
        return cmd

    def _spawn(self) -> None:
        """启动一个新的 ffmpeg 会话。"""
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            self._last_error = "未找到 ffmpeg，请先在 Termux 安装：pkg install ffmpeg"
            logger.error(self._last_error)
            self._stop_event.set()
            return
        cmd = self._build_command()
        logger.info("启动 ffmpeg（第 %d 次会话）", self._restarts + 1)
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as e:
            self._last_error = f"ffmpeg 启动失败：{e}"
            logger.error(self._last_error)
            self._stop_event.set()
            return
        threading.Thread(target=self._drain_stderr, args=(self._proc,),
                         name="vivideye-ffmpeg-stderr", daemon=True).start()
        # 记录本次会话的基线：这些旧文件不算“活跃文件”，也不刷新看门狗
        self._baseline_names = set(self._list_seg_files())
        self._active_name = None
        self._active_size = -1
        # spawn 时刻作为看门狗起点：超时仍无任何新文件/写入则视为流断开
        self._last_output_at = time.time()

    def _on_ffmpeg_exit(self, proc: subprocess.Popen) -> None:
        """ffmpeg 非正常退出后的记录（是否重启由调用方决定）。"""
        code = proc.poll()
        self._last_error = (
            f"ffmpeg 异常退出（code={code}）："
            + (" | ".join(self._stderr_tail[-3:]) if self._stderr_tail else "无 stderr 输出"))
        self._restarts += 1
        self._proc = None
        logger.warning("%s，%s", self._last_error,
                       "即将自动重启"
                       if _as_bool(self._cfg.get("capture.restart_on_failure", True))
                       else "restart_on_failure=false")

    def _kill_proc(self, graceful_timeout: float = 5.0) -> None:
        """结束 ffmpeg：先 SIGINT（优雅收尾 mp4），超时再升级 SIGTERM/SIGKILL。"""
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=graceful_timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        """持续读取 ffmpeg stderr，保留末尾若干行用于错误诊断。"""
        try:
            assert proc.stderr is not None
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._stderr_tail = (self._stderr_tail + [line])[-20:]
                    logger.debug("ffmpeg: %s", line)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 片段发现 / 注册 / 看门狗
    # ------------------------------------------------------------------
    def _list_seg_files(self) -> dict[str, Path]:
        """扫描 raw 目录，返回 {文件名: 路径}。"""
        out: dict[str, Path] = {}
        try:
            for p in self.raw_dir.glob("seg_*.mp4"):
                if p.is_file():
                    out[p.name] = p
        except OSError as e:
            logger.warning("扫描 raw 目录失败：%s", e)
        return out

    def _register_segments(self, files: dict[str, Path], final: bool) -> None:
        """把已完成的片段注册进数据库（按 path 幂等）。

        - ffmpeg 存活时，“本次会话新出现的最新文件”视为写入中，暂不注册；
        - final=True（启动前的遗留文件 / 停止收尾）时全部注册。
        """
        names = sorted(files)
        if not names:
            return
        active: Optional[str] = None
        proc = self._proc
        if not final and proc is not None and proc.poll() is None:
            fresh = [n for n in names if n not in self._baseline_names]
            active = fresh[-1] if fresh else None

        seg_seconds = float(self._cfg.get("capture.segment_seconds", 600))
        now = time.time()
        for i, name in enumerate(names):
            if name == active or name in self._registered:
                continue
            path = files[name]
            started = _parse_seg_time(name)
            if started is None:
                continue
            started_ts = started.timestamp()
            # 时长：下一个片段的起始时间 - 本片段起始时间；最后一份用当前时间估算
            nxt = _parse_seg_time(names[i + 1]) if i + 1 < len(names) else None
            if nxt is not None:
                duration = max(0.0, nxt.timestamp() - started_ts)
            else:
                duration = min(seg_seconds, max(0.0, now - started_ts))
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if size <= 0:
                continue
            self._db.add_segment(path, started_ts, duration=round(duration, 1),
                                 size_bytes=size)
            self._registered.add(name)
            self._registered_count += 1
            logger.info("注册片段 %s（约 %.0f 秒，%.1f MB）",
                        name, duration, size / 1048576)

    def _watchdog_check(self, files: dict[str, Path], now: float) -> None:
        """看门狗：本次会话的活跃文件若长时间无任何写入则重启 ffmpeg。"""
        watchdog = float(self._cfg.get("capture.watchdog_seconds", 60))
        # 找活跃文件：本次会话新出现的最新文件
        fresh = [n for n in sorted(files) if n not in self._baseline_names]
        if fresh:
            name = fresh[-1]
            try:
                size = files[name].stat().st_size
            except OSError:
                return
            if name != self._active_name or size != self._active_size:
                # 文件名变化或大小增长 => 有数据写入
                self._active_name = name
                self._active_size = size
                self._last_output_at = now
        # 无新文件 / 无写入时，时间戳持续变老
        if (self._last_output_at is not None
                and now - self._last_output_at > watchdog):
            logger.warning("看门狗触发：%.0f 秒无任何写入，重启 ffmpeg",
                           now - self._last_output_at)
            self._restarts += 1
            self._kill_proc()
            self._last_output_at = None
            self._active_name = None
            self._active_size = -1
