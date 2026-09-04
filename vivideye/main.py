#!/usr/bin/env python3
"""VividEye 命令行入口（运行在手机 Termux 中）。

子命令：
    start        启动全部常驻服务：循环录制（多机位）+ 管线调度 + Web 服务器（可选）
    process-now  立即处理一批待处理片段（不启动常驻服务）
    digest       生成“今日精选”Markdown 日报
    status       查看存储/磁盘/待处理状态快照
    bullet-time  为指定高光渲染子弹时间环绕回放（多机位/虚拟机位）

用法示例：
    python main.py start
    python main.py process-now
    python main.py digest --date 2026-09-02
    python main.py status
    python main.py bullet-time <highlight_id>

日志统一输出到 stderr；status / bullet-time 的结果同时打印到 stdout 方便查阅。
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("vivideye")

# 统一日志格式（输出到 stderr）
_LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


# ----------------------------------------------------------------------
# start：常驻服务
# ----------------------------------------------------------------------
def cmd_start(args: argparse.Namespace) -> int:
    from vivideye.capture.multi import MultiRecorder
    from vivideye.pipeline.orchestrator import PipelineService
    from vivideye.storage.db import HighlightsDB
    from vivideye.paths import resolve_path
    from vivideye.config import config

    db = HighlightsDB(resolve_path(config.get("storage.db_path", "data/vivideye.db")))
    # MultiRecorder：capture.cameras 为空时回退单机位（行为与旧版
    # Recorder 完全一致）；非空时每机位独立 ffmpeg 进程
    recorder = MultiRecorder(db=db)
    service = PipelineService(db=db)

    # Web 服务器为可选组件：启动/探测失败只告警，不影响录制与管线
    web_server: Any = None
    try:
        from vivideye.server.app import create_app
        web_server = _start_web_server(create_app())
        if web_server is not None and web_server.started:
            logger.info("Web 服务已启动：http://%s:%s",
                        config.get("server.host", "0.0.0.0"),
                        config.get("server.port", 8666))
        elif web_server is not None:
            logger.warning("Web 服务端口探测失败（%s:%s），Web 服务未就绪；"
                           "录制与管线不受影响",
                           config.get("server.host", "0.0.0.0"),
                           config.get("server.port", 8666))
    except ImportError:
        logger.warning("未找到 vivideye.server.app（Web 模块未就绪），已跳过 Web 服务")
    except Exception:
        logger.exception("Web 服务启动失败，已跳过（其余服务继续）")

    recorder.start()
    service.start()

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: Any) -> None:
        logger.info("收到信号 %s，准备退出……", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("VividEye 全部服务已启动（Ctrl+C 停止）")
    try:
        while not stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass

    logger.info("正在停止全部服务……")
    service.stop()
    recorder.stop()
    if web_server is not None:
        try:
            web_server.shutdown()
        except Exception:
            pass
    db.close()
    logger.info("VividEye 已退出")
    return 0


def _start_web_server(app: Any) -> Any:
    """在后台线程用 uvicorn 启动 Web 服务器，返回可 shutdown() 的句柄。

    - 仅支持 uvicorn：FastAPI 是 ASGI 应用，wsgiref 兜底必然 500，
      已移除（uvicorn 缺失时仅告警跳过）；
    - 启动后主动探测端口，确认真的在监听才认为成功（``handle.started``）；
    - 任何失败只告警，不影响录制与管线。
    """
    from vivideye.config import config
    host = str(config.get("server.host", "0.0.0.0"))
    port = int(config.get("server.port", 8666))

    try:
        import uvicorn
    except ImportError:
        logger.warning("uvicorn 未安装，已跳过 Web 服务（录制与管线不受影响）")
        return None

    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, name="vivideye-web",
                     daemon=True).start()
    started = _wait_port(host, port, timeout=10.0)
    return _WebServerHandle(server, started)


def _wait_port(host: str, port: int, timeout: float = 10.0) -> bool:
    """轮询探测 TCP 端口，确认 Web 服务真的开始监听才返回 True。"""
    probe_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


class _WebServerHandle:
    """uvicorn 服务器的关闭句柄（started 标记端口探测是否成功）。"""

    def __init__(self, server: Any, started: bool):
        self._server = server
        self.started = started

    def shutdown(self) -> None:
        try:
            self._server.should_exit = True
        except Exception:
            pass


# ----------------------------------------------------------------------
# process-now：立即处理一批
# ----------------------------------------------------------------------
def cmd_process_now(args: argparse.Namespace) -> int:
    from vivideye.pipeline.orchestrator import PipelineService

    result = PipelineService().process_now()
    if result.get("skipped"):
        logger.warning("跳过：%s", result.get("reason"))
        return 1
    logger.info("处理结果：%s", result)
    # 顺带打印每段明细
    for item in result.get("segments", []):
        state = "ok" if item.get("ok") else f"failed: {item.get('error', '')[:120]}"
        logger.info("  - %s -> %s", item.get("path"), state)
    return 0 if result.get("failed", 0) == 0 else 1


# ----------------------------------------------------------------------
# digest：生成日报
# ----------------------------------------------------------------------
def cmd_digest(args: argparse.Namespace) -> int:
    from vivideye.pipeline.digest import generate_digest

    path = generate_digest(date=args.date)
    if path is None:
        logger.error("日报生成失败")
        return 1
    print(str(path))
    logger.info("日报已生成：%s", path)
    return 0


# ----------------------------------------------------------------------
# status：状态快照（DB 统计 + 磁盘水位 + 目录概况）
# ----------------------------------------------------------------------
def cmd_status(args: argparse.Namespace) -> int:
    from vivideye.capture.retention import free_gb
    from vivideye.config import config
    from vivideye.paths import resolve_path
    from vivideye.storage.db import HighlightsDB

    db = HighlightsDB(resolve_path(config.get("storage.db_path", "data/vivideye.db")))
    stats = db.stats()
    pending = db.pending_segments(limit=10000)

    raw_dir = resolve_path(config.get("storage.raw_dir", "data/raw"))
    hl_dir = resolve_path(config.get("storage.highlights_dir", "data/highlights"))
    digest_dir = resolve_path(config.get("storage.digest_dir", "data/digests"))
    raw_files = sorted(raw_dir.glob("seg_*.mp4")) if raw_dir.is_dir() else []
    raw_bytes = sum(p.stat().st_size for p in raw_files
                    if p.is_file()) if raw_files else 0
    hl_files = sorted(hl_dir.glob("hl_*.mp4")) if hl_dir.is_dir() else []

    fgb = free_gb(raw_dir)
    min_free = float(config.get("storage.min_free_gb", 2))

    lines = [
        "=== VividEye 状态 ===",
        f"[数据库]   {resolve_path(config.get('storage.db_path'))}",
        f"           片段 {stats['segments_total']} | 高光 {stats['highlights_total']}"
        f" | 今日高光 {stats['highlights_today']} | 待处理 {len(pending)}",
        f"[磁盘]     剩余 {fgb:.2f} GB（录制阈值 {min_free:.2f} GB，"
        f"{'正常' if fgb >= min_free else '低于阈值，录制将暂停'}）",
        f"[原始片段] {raw_dir}：{len(raw_files)} 个文件，共 {raw_bytes / 1048576:.1f} MB"
        + (f"，最新 {raw_files[-1].name}" if raw_files else ""),
        f"[高光]     {hl_dir}：{len(hl_files)} 个文件"
        + (f"，最新 {hl_files[-1].name}" if hl_files else ""),
        f"[日报]     {digest_dir}",
    ]
    if pending:
        lines.append("[待处理]   " + "; ".join(
            Path(p["path"]).name for p in pending[:5])
            + (" ..." if len(pending) > 5 else ""))
    print("\n".join(lines))
    db.close()
    return 0


# ----------------------------------------------------------------------
# bullet-time：为指定高光渲染子弹时间
# ----------------------------------------------------------------------
def cmd_bullet_time(args: argparse.Namespace) -> int:
    from vivideye.bullettime import BulletTimeRenderer
    from vivideye.config import config
    from vivideye.paths import resolve_path
    from vivideye.storage.db import HighlightsDB

    db = HighlightsDB(resolve_path(config.get("storage.db_path", "data/vivideye.db")))
    try:
        hl = db.get_highlight(args.highlight_id)
        if hl is None:
            logger.error("未找到高光 %s（可用 main.py status 查看已有高光）",
                         args.highlight_id)
            return 1
        if not bool(config.get("bullet_time.enabled", True)):
            logger.warning("子弹时间未启用（bullet_time.enabled=false），已跳过")
            return 1
        min_score = float(config.get("bullet_time.min_score", 0.75))
        score = float(hl.get("score") or 0.0)
        if score < min_score:
            logger.warning("高光分数 %.2f 低于子弹时间阈值 %.2f，已跳过"
                           "（可调低 bullet_time.min_score）", score, min_score)
            return 1

        # 高光中点作为环绕中心
        duration = float(hl.get("duration") or 0.0)
        center = float(hl.get("started_at") or 0.0) + duration / 2.0
        hl_dir = resolve_path(config.get("storage.highlights_dir",
                                         "data/highlights"))
        out = BulletTimeRenderer().auto_render(center, hl["id"], hl_dir)
        if out is None:
            logger.warning(
                "子弹时间渲染失败：覆盖该时刻（%s）的原始片段可能已被保留期"
                "清理（data/raw 默认只保留 capture.retention_hours 小时），"
                "或渲染过程出错；详情见上方日志",
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(center)))
            return 1
        db.set_bullet_time(hl["id"], str(out))
        print(str(out))
        logger.info("子弹时间成片：%s（已写入高光记录 bullet_time_path）", out)
        return 0
    finally:
        db.close()


# ----------------------------------------------------------------------
# argparse 装配
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vivideye",
        description="VividEye：旧手机 AI 高光相机（录制 + 云端分析 + 精选日报）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="启动全部常驻服务（录制 + 管线 + Web）")
    p_start.set_defaults(func=cmd_start)

    p_now = sub.add_parser("process-now", help="立即处理一批待处理片段")
    p_now.set_defaults(func=cmd_process_now)

    p_digest = sub.add_parser("digest", help="生成今日精选 Markdown 日报")
    p_digest.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                          help="日报日期（默认今天）")
    p_digest.set_defaults(func=cmd_digest)

    p_status = sub.add_parser("status", help="查看存储/磁盘/待处理状态")
    p_status.set_defaults(func=cmd_status)

    p_bt = sub.add_parser("bullet-time", help="为指定高光渲染子弹时间环绕回放")
    p_bt.add_argument("highlight_id", metavar="HIGHLIGHT_ID",
                      help="高光 ID（见 /api/highlights 或 DB）")
    p_bt.set_defaults(func=cmd_bullet_time)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, stream=sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        logger.info("已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
