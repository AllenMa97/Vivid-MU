"""SQLite storage for raw segments, highlights and daily digests.

This is the shared contract between the pipeline (writes) and the web
server (reads). Keep the schema additive-only unless migrating.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    started_at REAL NOT NULL,
    duration REAL DEFAULT 0,
    size_bytes INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',        -- new | processing | done | failed | skipped
    created_at REAL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS highlights (
    id TEXT PRIMARY KEY,
    segment_id TEXT,
    video_path TEXT NOT NULL,
    thumb_path TEXT,
    score REAL DEFAULT 0,
    title TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',           -- JSON array, e.g. ["pet","funny"]
    subjects TEXT DEFAULT '[]',       -- JSON array, e.g. ["cat","kid"]
    started_at REAL DEFAULT 0,
    duration REAL DEFAULT 0,
    favorite INTEGER DEFAULT 0,
    bullet_time_path TEXT,            -- 子弹时间成片路径（bullet-time 渲染产物）
    created_at REAL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS digests (
    id TEXT PRIMARY KEY,
    date TEXT UNIQUE NOT NULL,        -- YYYY-MM-DD
    markdown_path TEXT,
    stats TEXT DEFAULT '{}',          -- JSON: counts, top moments...
    created_at REAL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_segments_status ON segments(status);
CREATE INDEX IF NOT EXISTS idx_highlights_created ON highlights(created_at DESC);
"""


class HighlightsDB:
    """Thread-safe wrapper around a sqlite3 connection."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # timeout=30：录制/管线/Web 多线程并发写时等待锁而非立刻报
        # "database is locked"；WAL 模式允许读写并发
        self._conn = sqlite3.connect(str(self.db_path), timeout=30,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """增量 schema 迁移（幂等，可对同一库重复执行）。

        旧库的 highlights 表由 CREATE TABLE IF NOT EXISTS 保持原样，
        需要在这里检测缺失列并 ALTER TABLE 补齐。
        """
        cols = [r["name"] for r in
                self._conn.execute("PRAGMA table_info(highlights)").fetchall()]
        if cols and "bullet_time_path" not in cols:
            self._conn.execute(
                "ALTER TABLE highlights ADD COLUMN bullet_time_path TEXT")
            logger.info("已为旧库 highlights 表补充 bullet_time_path 列")

    # ------------------------------------------------------------------
    # segments
    # ------------------------------------------------------------------
    def add_segment(self, path: str | Path, started_at: float,
                    duration: float = 0, size_bytes: int = 0) -> str:
        seg_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO segments (id, path, started_at, duration, size_bytes) "
                "VALUES (?,?,?,?,?)",
                (seg_id, str(path), started_at, duration, size_bytes))
            self._conn.commit()
        return seg_id

    def mark_segment(self, path: str | Path, status: str):
        with self._lock:
            self._conn.execute(
                "UPDATE segments SET status=? WHERE path=?", (status, str(path)))
            self._conn.commit()

    def pending_segments(self, limit: int = 24) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM segments WHERE status='new' "
                "ORDER BY started_at ASC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def reset_stale_processing(self, stale_seconds: float = 3600.0) -> int:
        """把卡在 processing 的陈旧片段复位为 new，返回复位条数。

        管线进程崩溃/被杀会让片段永远停在 processing（pending_segments
        不会取到它）。以 created_at 计龄，超过 ``stale_seconds``（默认
        1 小时）视为陈旧；由 PipelineService 启动时调用。
        """
        cutoff = time.time() - float(stale_seconds)
        with self._lock:
            cur = self._conn.execute(
                "UPDATE segments SET status='new' "
                "WHERE status='processing' AND created_at < ?",
                (cutoff,))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------
    # highlights
    # ------------------------------------------------------------------
    def add_highlight(self, video_path: str, segment_id: str | None = None,
                      thumb_path: str | None = None, score: float = 0.0,
                      title: str = "", caption: str = "",
                      tags: list | None = None, subjects: list | None = None,
                      started_at: float = 0.0, duration: float = 0.0) -> str:
        hid = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT INTO highlights (id, segment_id, video_path, thumb_path, score, "
                "title, caption, tags, subjects, started_at, duration) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (hid, segment_id, video_path, thumb_path, score, title, caption,
                 json.dumps(tags or [], ensure_ascii=False),
                 json.dumps(subjects or [], ensure_ascii=False),
                 started_at or time.time(), duration))
            self._conn.commit()
        return hid

    def list_highlights(self, limit: int = 100, offset: int = 0,
                        tag: str | None = None, favorite_only: bool = False) -> list[dict]:
        q = "SELECT * FROM highlights"
        conds, args = [], []
        if favorite_only:
            conds.append("favorite=1")
        if tag:
            conds.append("tags LIKE ?")
            args.append(f'%"{tag}"%')
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["subjects"] = json.loads(d.get("subjects") or "[]")
            out.append(d)
        return out

    def get_highlight(self, hid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM highlights WHERE id=?", (hid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["subjects"] = json.loads(d.get("subjects") or "[]")
        return d

    def set_favorite(self, hid: str, favorite: bool):
        with self._lock:
            self._conn.execute("UPDATE highlights SET favorite=? WHERE id=?",
                               (1 if favorite else 0, hid))
            self._conn.commit()

    def set_bullet_time(self, hid: str, path: str | Path) -> int:
        """记录高光的子弹时间成片路径，返回更新行数（0=hid 不存在）。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE highlights SET bullet_time_path=? WHERE id=?",
                (str(path), hid))
            self._conn.commit()
            return cur.rowcount

    def delete_highlight(self, hid: str):
        with self._lock:
            self._conn.execute("DELETE FROM highlights WHERE id=?", (hid,))
            self._conn.commit()

    def expired_highlights(self, before: float, limit: int = 1000) -> list[dict]:
        """列出 created_at 早于 before 且未收藏的高光（供保留期清理）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM highlights WHERE favorite=0 AND created_at < ? "
                "ORDER BY created_at ASC LIMIT ?", (before, limit)).fetchall()
        return [dict(r) for r in rows]

    def delete_highlight_by_path(self, video_path: str | Path) -> int:
        """按 video_path 删除高光记录（保留期清理用），返回删除条数。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM highlights WHERE video_path=?", (str(video_path),))
            self._conn.commit()
            return cur.rowcount

    def stats(self) -> dict:
        with self._lock:
            seg_total = self._conn.execute(
                "SELECT COUNT(*) FROM segments").fetchone()[0]
            hl_total = self._conn.execute(
                "SELECT COUNT(*) FROM highlights").fetchone()[0]
            hl_today = self._conn.execute(
                "SELECT COUNT(*) FROM highlights WHERE created_at >= ?",
                (time.time() - 86400,)).fetchone()[0]
        return {"segments_total": seg_total, "highlights_total": hl_total,
                "highlights_today": hl_today}

    # ------------------------------------------------------------------
    # digests
    # ------------------------------------------------------------------
    def save_digest(self, date: str, markdown_path: str, stats: dict) -> str:
        did = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO digests (id, date, markdown_path, stats) "
                "VALUES (?,?,?,?)",
                (did, date, markdown_path, json.dumps(stats, ensure_ascii=False)))
            self._conn.commit()
        return did

    def get_digest(self, date: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM digests WHERE date=?", (date,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["stats"] = json.loads(d.get("stats") or "{}")
        return d

    def close(self):
        with self._lock:
            self._conn.close()


def init_db(db_path: str | Path) -> HighlightsDB:
    return HighlightsDB(db_path)
