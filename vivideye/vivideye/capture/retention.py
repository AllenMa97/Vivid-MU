"""原始片段的保留期清理与磁盘水位检查（PHONE-FIRST，存储紧张是常态）。

职责：
- 定期删除 raw 目录中超过 ``capture.retention_hours`` 的过期片段；
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


def run_retention(cfg: Config | None = None, clean: bool = True) -> RetentionReport:
    """执行一次“清理 + 磁盘水位”检查，返回报告。

    录制器周期性调用本函数：``report.disk_ok`` 为 False 时应暂停录制，
    恢复为 True 后可继续。
    """
    cfg = cfg or config
    raw_dir = resolve_path(cfg.get("storage.raw_dir", "data/raw"), cfg)
    raw_dir.mkdir(parents=True, exist_ok=True)

    deleted = freed = 0
    if clean:
        deleted, freed = clean_expired(raw_dir, cfg.get("capture.retention_hours", 72))

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
