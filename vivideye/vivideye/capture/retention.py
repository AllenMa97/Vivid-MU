"""原始片段/高光的保留期清理与磁盘水位检查（PHONE-FIRST，存储紧张是常态）。

职责：
- 定期删除 raw 目录中超过 ``capture.retention_hours`` 的过期片段；
- 定期删除超过 ``storage.highlights_retention_days`` 的非 favorite
  高光（媒体文件 + 数据库记录；favorite 永不删除）；
- 检查磁盘剩余空间是否低于 ``storage.min_free_gb``，供录制器决定
  暂停/恢复录制（本模块只报告状态，不做启停决定）。
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from vivideye.config import Config, config
from vivideye.paths import resolve_path
from vivideye.storage.db import HighlightsDB

logger = logging.getLogger(__name__)

_GB = 1024 ** 3


@dataclass
class RetentionReport:
    """一次保留期检查的结果快照。"""

    free_gb: float = 0.0            # 当前剩余空间（GB）
    min_free_gb: float = 0.0        # 配置的最低剩余空间阈值（GB）
    disk_ok: bool = True            # 剩余空间是否高于阈值（True=可继续录制）
    deleted_files: int = 0          # 本次删除的过期文件数
    freed_bytes: int = 0            # 本次释放的字节数
    checked_at: float = 0.0         # 检查时间戳

    def as_dict(self) -> dict:
        return asdict(self)


def free_gb(path: str | Path) -> float:
    """返回 path 所在文件系统的剩余空间（GB）。"""
    try:
        return shutil.disk_usage(str(path)).free / _GB
    except OSError:
        logger.warning("无法读取磁盘空间：%s", path)
        return 0.0


def disk_ok(path: str | Path, min_free_gb: float) -> bool:
    """磁盘剩余空间是否满足最低要求。"""
    return free_gb(path) >= float(min_free_gb)


def clean_expired(raw_dir: str | Path, retention_hours: float) -> tuple[int, int]:
    """删除 raw 目录中修改时间早于保留期的片段。

    返回 ``(删除文件数, 释放字节数)``。数据库中的 segments 记录保留不动，
    管线遇到已消失的文件会自然标记 failed，不阻塞流程。
    """
    raw_dir = Path(raw_dir)
    cutoff = time.time() - float(retention_hours) * 3600.0
    deleted, freed = 0, 0
    try:
        candidates = list(raw_dir.glob("seg_*.mp4"))
    except OSError:
        logger.warning("无法扫描 raw 目录：%s", raw_dir)
        return 0, 0
    for p in candidates:
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_mtime < cutoff:
            try:
                p.unlink()
                deleted += 1
                freed += st.st_size
            except OSError as e:
                logger.warning("删除过期片段失败 %s：%s", p, e)
    if deleted:
        logger.info("保留期清理：删除 %d 个过期片段，释放 %.1f MB",
                    deleted, freed / 1048576)
    return deleted, freed


def clean_expired_highlights(highlights_dir: str | Path, retention_days: float,
                             db: HighlightsDB | None = None) -> tuple[int, int]:
    """删除超过保留期的非 favorite 高光（媒体文件 + DB 记录）。

    - 按 DB 中 ``created_at`` 判龄，超过 ``retention_days`` 天且未收藏
      的高光连同 mp4/jpg 文件一起清理；favorite 永不删除；
    - 只删除位于 ``highlights_dir`` 内的媒体文件（安全边界，与 Web 端
      删除接口一致），目录外的文件保留、仅清 DB 记录；
    - ``retention_days <= 0`` 视为关闭高光保留期清理；
    - ``db`` 为 None 时无法定位记录，直接跳过。
    返回 ``(删除记录数, 释放字节数)``。
    """
    if db is None:
        return 0, 0
    days = float(retention_days or 0)
    if days <= 0:
        return 0, 0
    highlights_dir = Path(highlights_dir)
    hl_root = highlights_dir.resolve()
    cutoff = time.time() - days * 86400.0
    deleted, freed = 0, 0
    for h in db.expired_highlights(cutoff):
        for key in ("video_path", "thumb_path"):
            raw = h.get(key)
            if not raw:
                continue
            try:
                p = Path(raw).resolve()
                if p.is_file() and hl_root in p.parents:
                    freed += p.stat().st_size
                    p.unlink()
            except OSError as e:
                logger.warning("删除高光文件失败 %s：%s", raw, e)
        db.delete_highlight_by_path(h["video_path"])
        deleted += 1
    if deleted:
        logger.info("高光保留期清理：删除 %d 条超过 %.0f 天的非收藏高光，"
                    "释放 %.1f MB", deleted, days, freed / 1048576)
    return deleted, freed


def run_retention(cfg: Config | None = None, clean: bool = True,
                  db: HighlightsDB | None = None) -> RetentionReport:
    """执行一次“清理 + 磁盘水位”检查，返回报告。

    录制器周期性调用本函数：``report.disk_ok`` 为 False 时应暂停录制，
    恢复为 True 后可继续。传入 ``db`` 时同时执行高光保留期清理。
    """
    cfg = cfg or config
    raw_dir = resolve_path(cfg.get("storage.raw_dir", "data/raw"), cfg)
    raw_dir.mkdir(parents=True, exist_ok=True)

    deleted = freed = 0
    if clean:
        deleted, freed = clean_expired(raw_dir, cfg.get("capture.retention_hours", 24))
        clean_expired_highlights(
            resolve_path(cfg.get("storage.highlights_dir", "data/highlights"), cfg),
            cfg.get("storage.highlights_retention_days", 30),
            db=db,
        )

    min_free = float(cfg.get("storage.min_free_gb", 2))
    fgb = free_gb(raw_dir)
    report = RetentionReport(
        free_gb=round(fgb, 2),
        min_free_gb=min_free,
        disk_ok=fgb >= min_free,
        deleted_files=deleted,
        freed_bytes=freed,
        checked_at=time.time(),
    )
    if not report.disk_ok:
        logger.warning("磁盘剩余 %.2f GB 低于阈值 %.2f GB，建议暂停录制",
                       fgb, min_free)
    return report
