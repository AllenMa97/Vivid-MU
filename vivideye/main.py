#!/usr/bin/env python3
"""VividEye 命令行入口（运行在手机 Termux 中）。

子命令：
    start        启动全部常驻服务：循环录制 + 管线调度 + Web 服务器（可选）
    process-now  立即处理一批待处理片段（不启动常驻服务）
    digest       生成“今日精选”Markdown 日报
    status       查看存储/磁盘/待处理状态快照

用法示例：
    python main.py start
    python main.py process-now
    python main.py digest --date 2026-09-02
    python main.py status

日志统一输出到 stderr；status 的结果同时打印到 stdout 方便查阅。
"""

from __future__ import annotations

import argparse
import logging
import signal
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
    from vivideye.capture.recorder import Recorder
    from vivideye.pipeline.orchestrator import PipelineService
    from vivideye.storage.db import HighlightsDB
    from vivideye.paths import resolve_path
    from vivideye.config import config

    db = HighlightsDB(resolve_path(config.get("storage.db_path", "data/vivideye.db")))
    recorder = Recorder(db=db)
    service = PipelineService(db=db)

    # Web 服务器为可选组件：模块未就绪时跳过并提示，不影响其余服务
    web_server: Any = None
    try:
        from vivideye.server.app import create_app
        web_server = _start_web_server(create_app())
        logger.info("Web 服务已启动：http://%s:%s",
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
    """用标准库 wsgiref 在后台线程提供 Web 服务（WSGI app，不引入额外依赖）。"""
    from wsgiref.simple_server import make_server

    from vivideye.config import config
    server = make_server(str(config.get("server.host", "0.0.0.0")),
                         int(config.get("server.port", 8666)), app)
    threading.Thread(target=server.serve_forever,
                     name="vivideye-web", daemon=True).start()
    return server


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
