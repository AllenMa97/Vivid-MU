"""多机位录制编排：把 capture.cameras 列表映射成一组独立 Recorder。

布局规则（v0.2.0，向后兼容）：
- ``capture.cameras`` 为空（默认）：回退单机位——包装一个使用
  ``capture.source_url`` 的 Recorder（机位名 "main"），片段仍 flat 写
  ``data/raw/seg_*.mp4``，心跳/状态文件结构与旧版完全一致；
- ``capture.cameras`` 非空：每个机位一个独立 Recorder（独立 ffmpeg 子
  进程、独立看门狗/指数退避/磁盘水位逻辑，全部复用 Recorder 机制），
  片段写 ``data/raw/<name>/seg_*.mp4`` 子目录。

状态心跳 data/recorder.json：
- 单机位：由 Recorder 自身周期写入（与旧版一致，顶层字段不变）；
- 多机位：单个 Recorder 的心跳被禁用，由本类的聚合线程统一写入——
  顶层保持 recording / last_file / paused / updated_at 字段
  （recording = 任一机位在录），并额外附 ``cameras`` 映射
  ``{机位名: 该机位状态快照}``，供 Web 状态接口读取。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from vivideye.config import Config, config
from vivideye.paths import resolve_path
from vivideye.capture.recorder import Recorder
from vivideye.storage.db import HighlightsDB

logger = logging.getLogger(__name__)

# 多机位聚合心跳的巡检周期（秒），与 Recorder._TICK_SECONDS 对齐
_HEARTBEAT_SECONDS = 5.0


def _camera_cfg(base: Config, cam: dict) -> Config:
    """基于全局配置派生单个机位的配置视图（深拷贝后覆盖 capture.*）。

    只覆盖流地址相关键，其余（segment_seconds / video_codec / 退避 /
    看门狗 / 磁盘水位 / 路径）全部继承全局配置。
    """
    data = copy.deepcopy(base.as_dict())
    cap = data.setdefault("capture", {})
    cap["source_url"] = str(cam.get("url") or cap.get("source_url") or "")
    audio_url = cam.get("audio_url")
    if audio_url:
        cap["audio_url"] = str(audio_url)
        cap["record_audio"] = True
    else:
        # 该机位没有独立音频流：禁用音频，避免误用其他机位的 audio_url
        cap["audio_url"] = None
        cap["record_audio"] = False
    return Config(data)


class MultiRecorder:
    """多机位录制器：读 capture.cameras，编排 1..N 个 Recorder。

    用法与 Recorder 完全一致::

        rec = MultiRecorder(db)
        rec.start()
        rec.status()
        rec.stop()
    """

    def __init__(self, db: Optional[HighlightsDB] = None, cfg: Config | None = None):
        self._cfg = cfg or config
        self._db = db if db is not None else HighlightsDB(
            resolve_path(self._cfg.get("storage.db_path", "data/vivideye.db"),
                         self._cfg))
        self.raw_root = resolve_path(
            self._cfg.get("storage.raw_dir", "data/raw"), self._cfg)
        self._status_path = self._db.db_path.parent / "recorder.json"

        cameras = list(self._cfg.get("capture.cameras") or [])
        self._single: Optional[Recorder] = None
        self._recorders: dict[str, Recorder] = {}

        if not cameras:
            # 单机位回退：行为与旧版 Recorder 完全一致（flat + 自写心跳）
            self._single = Recorder(db=self._db, cfg=self._cfg,
                                    name="main", out_dir=None)
            self._recorders = {"main": self._single}
        else:
            for i, cam in enumerate(cameras):
                name = str(cam.get("name") or f"cam{i + 1}")
                if name in self._recorders:
                    logger.warning("机位名重复：%s（第 %d 个），忽略后续同名校位",
                                   name, i + 1)
                    continue
                rec = Recorder(db=self._db,
                               cfg=_camera_cfg(self._cfg, cam),
                               name=name,
                               out_dir=self.raw_root / name)
                rec._status_enabled = False   # 聚合心跳由本类统一写
                self._recorders[name] = rec

        # 聚合心跳线程（仅多机位启用）
        self._heartbeat: Optional[threading.Thread] = None
        self._hb_stop = threading.Event()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动全部机位（幂等）。"""
        if self._single is not None:
            self._single.start()
            return
        for name, rec in self._recorders.items():
            rec.start()
            logger.info("机位 [%s] 已启动：目录=%s", name, rec.raw_dir)
        if self._heartbeat is None or not self._heartbeat.is_alive():
            self._hb_stop.clear()
            self._heartbeat = threading.Thread(
                target=self._heartbeat_loop,
                name="vivideye-multi-heartbeat", daemon=True)
            self._heartbeat.start()

    def stop(self) -> None:
        """停止全部机位并写终态心跳。"""
        if self._single is not None:
            self._single.stop()
            return
        for name, rec in self._recorders.items():
            try:
                rec.stop()
            except Exception:
                logger.exception("机位 [%s] 停止异常", name)
        self._hb_stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=10)
            self._heartbeat = None
        self._write_status(final=True)
        logger.info("全部 %d 个机位已停止", len(self._recorders))

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """聚合状态快照。

        - 单机位：直接透传 Recorder.status()（顶层字段与旧版一致）；
        - 多机位：顶层 recording/last_file/paused/updated_at 为聚合值，
          并附 ``cameras`` 映射。
        """
        if self._single is not None:
            return self._single.status()

        snaps = {name: rec.status() for name, rec in self._recorders.items()}
        cams: dict[str, Any] = {}
        for name, s in snaps.items():
            s = dict(s)
            s["recording"] = bool(s.get("running")
                                  and s.get("ffmpeg_pid") is not None)
            cams[name] = s
        last_files = [s.get("last_file") for s in cams.values()
                      if s.get("last_file")]
        return {
            "running": any(s.get("running") for s in snaps.values()),
            "manager_alive": any(s.get("manager_alive") for s in snaps.values()),
            "paused": bool(snaps) and all(s.get("paused") for s in snaps.values()),
            "recording": any(s.get("recording") for s in cams.values()),
            "last_file": max(last_files) if last_files else None,
            "cameras": cams,
            "updated_at": time.time(),
        }

    @property
    def recorders(self) -> dict[str, Recorder]:
        """机位名 -> Recorder 映射（单机位时为 {"main": ...}）。"""
        return dict(self._recorders)

    # ------------------------------------------------------------------
    # 聚合心跳（多机位）
    # ------------------------------------------------------------------
    def _heartbeat_loop(self) -> None:
        while not self._hb_stop.wait(_HEARTBEAT_SECONDS):
            try:
                self._write_status(final=False)
            except Exception:
                logger.exception("多机位心跳线程异常")

    def _write_status(self, final: bool = False) -> None:
        """把聚合 status() 原子写入 data/recorder.json。"""
        snapshot = self.status()
        snapshot["updated_at"] = time.time()
        tmp = self._status_path.with_name(self._status_path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)
            os.replace(tmp, self._status_path)
        except OSError as e:
            logger.warning("写入多机位录制状态心跳失败：%s", e)
