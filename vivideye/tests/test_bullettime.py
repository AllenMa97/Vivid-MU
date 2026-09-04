#!/usr/bin/env python3
"""VividEye 子弹时间引擎离线测试（真实 ffmpeg，全程不联网）。

覆盖点：
    1. 多机位真机位渲染：lavfi testsrc 生成 2 个 5s 视频按 seg_ 命名放入
       fake raw 子目录（文件名时间戳与 center 对齐）→ auto_render 产出
       mp4（ffprobe 时长/分辨率/文件大小断言）+ DB set/get bullet_time_path
    2. 单机位虚拟机位：仅 1 个机位时合成 8 个虚拟角度，成片时长≈duration
    3. parse_segment_start / find_segments：flat 单机位与多机位子目录
       两种布局、camera_names 过滤、窗口外排除
    4. 边界：segments 为空 / raw 目录无片段 → 返回 None 不抛异常
    5. DB 迁移幂等：对旧 schema 库 ALTER 补列后重复 init 不报错
    6. 向后兼容冒烟：cameras=[] 时 MultiRecorder 输出路径与命令行参数
       与旧版单机位 Recorder 一致；多机位时各写子目录
    7. retention 清理兼容 flat + 子目录两种布局

运行方式（仓库根 /workspace/vivideye 下）：
    /root/miniconda3/envs/gnt/bin/python tests/test_bullettime.py

可重复运行；全部产物放 /tmp（TemporaryDirectory 自动清理）。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vivideye.config import Config
from vivideye.storage.db import HighlightsDB
from vivideye.bullettime import BulletTimeRenderer, find_segments, parse_segment_start


# ----------------------------------------------------------------------
# 基础设施工具
# ----------------------------------------------------------------------
def make_test_video(out: Path, seconds: int = 5, size: str = "640x360") -> None:
    """用 lavfi testsrc 生成合成视频（离线，不联网）。"""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25",
         "-t", str(seconds), "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", str(out)],
        check=True)


def seg_name(ts: float) -> str:
    """按录制器的命名规则生成 seg_YYYYmmdd_HHMMSS.mp4。"""
    return "seg_" + datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S") + ".mp4"


def probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def probe_size(path: Path) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def make_cfg(root: Path, cameras: list | None = None, **bt_overrides) -> Config:
    """构造独立的测试配置（不依赖仓库 user_config.yaml）。"""
    data = {
        "app": {"data_dir": str(root / "data")},
        "capture": {
            "cameras": cameras or [],
            "segment_seconds": 600,
            "source_url": "http://127.0.0.1:9999/video",
            "record_audio": False,
        },
        "bullet_time": {
            "enabled": True, "min_score": 0.75, "virtual_angles": 8,
            "virtual_mode": "auto", "duration_seconds": 3,
            "style": "pingpong", "zoom_motion": True, **bt_overrides,
        },
        "storage": {
            "db_path": str(root / "data" / "vivideye.db"),
            "raw_dir": str(root / "data" / "raw"),
            "highlights_dir": str(root / "data" / "highlights"),
        },
    }
    return Config(data)


# ----------------------------------------------------------------------
# 3. parse_segment_start / find_segments
# ----------------------------------------------------------------------
class TestParseAndFind(unittest.TestCase):

    def test_parse_segment_start(self):
        ts = int(time.time())
        p = Path(f"/fake/raw/{seg_name(ts)}")
        self.assertAlmostEqual(parse_segment_start(p), float(ts), delta=1.0)
        self.assertAlmostEqual(parse_segment_start(str(p)), float(ts), delta=1.0)
        # 非法命名 / 非法日期 → None
        self.assertIsNone(parse_segment_start(Path("/fake/raw/clip.mp4")))
        self.assertIsNone(parse_segment_start(Path("/fake/raw/seg_99999999_999999.mp4")))
        self.assertIsNone(parse_segment_start(Path("/fake/raw/seg_20260904_120000.txt")))

    def test_find_segments_flat_layout(self):
        with tempfile.TemporaryDirectory(prefix="bt_find_flat_") as d:
            raw = Path(d) / "raw"
            raw.mkdir(parents=True)
            t0 = int(time.time()) - 3600
            (raw / seg_name(t0)).write_bytes(b"x")
            (raw / seg_name(t0 + 600)).write_bytes(b"x")
            os.utime(raw / seg_name(t0 + 600), (time.time(), time.time()))

            # center 落在第一段中部：仅返回第一段（机位名 main）
            segs = find_segments(t0 + 300, 8, raw)
            self.assertEqual(len(segs), 1)
            self.assertEqual(segs[0][0], "main")
            self.assertAlmostEqual(segs[0][2], float(t0), delta=1.0)

            # center 落在段边界附近：两段都覆盖
            segs = find_segments(t0 + 599, 8, raw)
            self.assertEqual(len(segs), 2)

            # camera_names=["main"] 等价（flat 即 main 机位）
            segs = find_segments(t0 + 300, 8, raw, ["main"])
            self.assertEqual(len(segs), 1)

            # center 在全部片段结束之后（真正未来时刻）→ 不返回
            # （最后一段无后继，用文件 mtime 近似结束，未来窗口不会误命中）
            self.assertEqual(find_segments(t0 + 7200, 8, raw), [])

    def test_find_segments_multi_layout(self):
        with tempfile.TemporaryDirectory(prefix="bt_find_multi_") as d:
            raw = Path(d) / "raw"
            cam1, cam2, other = raw / "cam1", raw / "cam2", raw / "other"
            for p in (cam1, cam2, other):
                p.mkdir(parents=True)
            t0 = int(time.time())
            (cam1 / seg_name(t0)).write_bytes(b"x")
            (cam2 / seg_name(t0)).write_bytes(b"x")
            (other / "keep.txt").write_bytes(b"x")     # 非 seg_ 文件，忽略
            now = time.time()
            os.utime(cam1 / seg_name(t0), (now, now))
            os.utime(cam2 / seg_name(t0), (now, now))

            center = t0 + 2.5
            # camera_names 过滤：只要 cam1
            segs = find_segments(center, 8, raw, ["cam1"])
            self.assertEqual(len(segs), 1)
            self.assertEqual(segs[0][0], "cam1")
            # 两个机位
            segs = find_segments(center, 8, raw, ["cam1", "cam2"])
            self.assertEqual(len(segs), 2)
            self.assertEqual({s[0] for s in segs}, {"cam1", "cam2"})
            self.assertTrue(all(s[1].is_file() for s in segs))
            # 不传 camera_names：扫描全部（含 other 目录，但其中无 seg_ 文件）
            segs = find_segments(center, 8, raw)
            self.assertEqual(len(segs), 2)

    def test_find_segments_mixed_layout(self):
        """flat 段（main）+ 子目录段并存。"""
        with tempfile.TemporaryDirectory(prefix="bt_find_mix_") as d:
            raw = Path(d) / "raw"
            raw.mkdir(parents=True)
            (raw / "cam1").mkdir()
            t0 = int(time.time())
            (raw / seg_name(t0)).write_bytes(b"x")
            (raw / "cam1" / seg_name(t0)).write_bytes(b"x")
            now = time.time()
            os.utime(raw / seg_name(t0), (now, now))
            os.utime(raw / "cam1" / seg_name(t0), (now, now))

            segs = find_segments(t0 + 2.5, 8, raw, ["main", "cam1"])
            self.assertEqual({s[0] for s in segs}, {"main", "cam1"})


# ----------------------------------------------------------------------
# 1. 多机位真机位渲染 + DB set/get
# ----------------------------------------------------------------------
class TestMultiCameraRender(unittest.TestCase):

    def test_auto_render_two_cameras_and_db(self):
        with tempfile.TemporaryDirectory(prefix="bt_multi_") as d:
            root = Path(d)
            cameras = [
                {"name": "cam1", "url": "http://127.0.0.1:9001/video", "audio_url": None},
                {"name": "cam2", "url": "http://127.0.0.1:9002/video", "audio_url": None},
            ]
            cfg = make_cfg(root, cameras=cameras, zoom_motion=False)
            raw = root / "data" / "raw"
            t0 = int(time.time())
            # 文件名时间戳与 center 对齐：center = t0 + 2.5
            for cam in ("cam1", "cam2"):
                (raw / cam).mkdir(parents=True)
                make_test_video(raw / cam / seg_name(t0))
            center = t0 + 2.5

            db = HighlightsDB(cfg.get("storage.db_path"))
            try:
                hid = db.add_highlight(
                    video_path=str(root / "hl_fake.mp4"),
                    score=0.9, title="多机位测试",
                    started_at=center - 2.0, duration=4.0)

                out = BulletTimeRenderer(cfg).auto_render(
                    center, hid, cfg.get("storage.highlights_dir"))
                self.assertIsNotNone(out, "双机位 auto_render 应产出成片")
                out = Path(out)
                expected = Path(cfg.get("storage.highlights_dir")) / f"bullet_{hid}.mp4"
                self.assertEqual(out, expected)
                self.assertTrue(out.is_file())
                self.assertGreater(out.stat().st_size, 10_000, "成片应有实际大小")

                # ffprobe：时长≈duration_seconds(3s)、720p 上限、无音轨
                dur = probe_duration(out)
                self.assertLess(abs(dur - 3.0), 0.6,
                                f"成片时长应≈3s（实际 {dur:.2f}s）")
                w, h = probe_size(out)
                self.assertLessEqual(w, 1280)
                self.assertLessEqual(h, 720)
                r = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "a",
                     "-show_entries", "stream=index", "-of", "csv=p=0", str(out)],
                    capture_output=True, text=True, check=True)
                self.assertEqual(r.stdout.strip(), "", "成片应无音轨")

                # DB set/get bullet_time_path
                self.assertEqual(db.set_bullet_time(hid, str(out)), 1)
                hl = db.get_highlight(hid)
                self.assertEqual(hl["bullet_time_path"], str(out))
                self.assertEqual(db.set_bullet_time("no-such-id", str(out)), 0)
            finally:
                db.close()


# ----------------------------------------------------------------------
# 2. 单机位虚拟机位（8 角度）
# ----------------------------------------------------------------------
class TestVirtualAngles(unittest.TestCase):

    def test_single_camera_synthesizes_8_angles(self):
        with tempfile.TemporaryDirectory(prefix="bt_virtual_") as d:
            root = Path(d)
            cfg = make_cfg(root, cameras=[], zoom_motion=True)   # flat 布局
            raw = root / "data" / "raw"
            raw.mkdir(parents=True)
            t0 = int(time.time())
            make_test_video(raw / seg_name(t0))
            center = t0 + 2.5

            db = HighlightsDB(cfg.get("storage.db_path"))
            try:
                hid = db.add_highlight(
                    video_path=str(root / "hl_fake.mp4"),
                    score=0.9, title="虚拟机位测试",
                    started_at=center - 2.0, duration=4.0)
                out = BulletTimeRenderer(cfg).auto_render(
                    center, hid, cfg.get("storage.highlights_dir"))
                self.assertIsNotNone(out, "单机位 auto mode 应合成虚拟机位")
                out = Path(out)
                self.assertTrue(out.is_file())
                self.assertGreater(out.stat().st_size, 10_000)

                dur = probe_duration(out)
                self.assertLess(abs(dur - 3.0), 0.8,
                                f"虚拟机位成片时长应≈3s（实际 {dur:.2f}s）")
                w, h = probe_size(out)
                self.assertEqual((w, h), (1280, 720), "统一到 720p")
            finally:
                db.close()

    def test_virtual_mode_real_single_frame_returns_none(self):
        """virtual_mode=real 且仅 1 帧 → 不渲染（返回 None）。"""
        with tempfile.TemporaryDirectory(prefix="bt_real_") as d:
            root = Path(d)
            cfg = make_cfg(root, cameras=[], virtual_mode="real")
            raw = root / "data" / "raw"
            raw.mkdir(parents=True)
            t0 = int(time.time())
            make_test_video(raw / seg_name(t0))
            out = BulletTimeRenderer(cfg).render_for_moment(
                t0 + 2.5, 3.0,
                [("main", raw / seg_name(t0), float(t0))],
                root / "out.mp4")
            self.assertIsNone(out)


# ----------------------------------------------------------------------
# 4. 空片段 / 无片段 → None 不炸
# ----------------------------------------------------------------------
class TestEmptySegments(unittest.TestCase):

    def test_render_for_moment_empty_segments(self):
        with tempfile.TemporaryDirectory(prefix="bt_empty_") as d:
            root = Path(d)
            renderer = BulletTimeRenderer(make_cfg(root))
            self.assertIsNone(
                renderer.render_for_moment(time.time(), 4.0, [],
                                           root / "out.mp4"))
            self.assertFalse((root / "out.mp4").exists())

    def test_auto_render_no_segments(self):
        with tempfile.TemporaryDirectory(prefix="bt_noseg_") as d:
            root = Path(d)
            cfg = make_cfg(root)      # raw 目录为空
            out = BulletTimeRenderer(cfg).auto_render(
                time.time(), "hid000", cfg.get("storage.highlights_dir"))
            self.assertIsNone(out)

    def test_auto_render_disabled(self):
        with tempfile.TemporaryDirectory(prefix="bt_disabled_") as d:
            root = Path(d)
            cfg = make_cfg(root, enabled=False)
            out = BulletTimeRenderer(cfg).auto_render(
                time.time(), "hid000", cfg.get("storage.highlights_dir"))
            self.assertIsNone(out)


# ----------------------------------------------------------------------
# 5. DB 迁移幂等
# ----------------------------------------------------------------------
class TestDBMigration(unittest.TestCase):

    _OLD_SCHEMA = """
    CREATE TABLE highlights (
        id TEXT PRIMARY KEY,
        segment_id TEXT,
        video_path TEXT NOT NULL,
        thumb_path TEXT,
        score REAL DEFAULT 0,
        title TEXT DEFAULT '',
        caption TEXT DEFAULT '',
        tags TEXT DEFAULT '[]',
        subjects TEXT DEFAULT '[]',
        started_at REAL DEFAULT 0,
        duration REAL DEFAULT 0,
        favorite INTEGER DEFAULT 0,
        created_at REAL DEFAULT (strftime('%s','now'))
    );
    """

    def test_migrate_old_schema_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="bt_db_") as d:
            db_path = Path(d) / "old.db"
            # 造一个旧 schema 库（无 bullet_time_path 列）+ 一条数据
            conn = sqlite3.connect(str(db_path))
            conn.executescript(self._OLD_SCHEMA)
            conn.execute("INSERT INTO highlights (id, video_path) "
                         "VALUES ('old1', '/tmp/x.mp4')")
            conn.commit()
            conn.close()

            def columns() -> list[str]:
                c = sqlite3.connect(str(db_path))
                try:
                    return [r[1] for r in c.execute(
                        "PRAGMA table_info(highlights)")]
                finally:
                    c.close()

            # 第一次 init：补列 + 旧数据可用
            db1 = HighlightsDB(db_path)
            self.assertIn("bullet_time_path", columns())
            self.assertEqual(db1.set_bullet_time("old1", "/tmp/b1.mp4"), 1)
            self.assertEqual(db1.get_highlight("old1")["bullet_time_path"],
                             "/tmp/b1.mp4")
            db1.close()

            # 重复 init：迁移幂等，不报错，数据仍在
            db2 = HighlightsDB(db_path)
            self.assertIn("bullet_time_path", columns())
            self.assertEqual(db2.get_highlight("old1")["bullet_time_path"],
                             "/tmp/b1.mp4")
            self.assertEqual(db2.set_bullet_time("old1", "/tmp/b2.mp4"), 1)
            db2.close()

    def test_new_db_has_column(self):
        with tempfile.TemporaryDirectory(prefix="bt_dbnew_") as d:
            db_path = Path(d) / "new.db"
            db = HighlightsDB(db_path)
            try:
                hid = db.add_highlight(video_path="/tmp/v.mp4", score=0.8)
                db.set_bullet_time(hid, "/tmp/b.mp4")
                self.assertEqual(
                    db.get_highlight(hid)["bullet_time_path"], "/tmp/b.mp4")
            finally:
                db.close()


# ----------------------------------------------------------------------
# 6. MultiRecorder 向后兼容冒烟（不真录，只验证构造与命令行参数）
# ----------------------------------------------------------------------
class TestMultiRecorderCompat(unittest.TestCase):

    def test_single_camera_backward_compatible(self):
        from vivideye.capture.multi import MultiRecorder

        with tempfile.TemporaryDirectory(prefix="bt_mr1_") as d:
            root = Path(d)
            cfg = make_cfg(root, cameras=[])       # 未配置多机位
            db = HighlightsDB(cfg.get("storage.db_path"))
            try:
                rec = MultiRecorder(db=db, cfg=cfg)
                self.assertEqual(list(rec.recorders), ["main"])
                single = rec.recorders["main"]
                self.assertEqual(single.name, "main")
                # 输出路径：flat 写 data/raw/seg_*.mp4（与旧版一致）
                self.assertEqual(single.raw_dir,
                                 Path(cfg.get("storage.raw_dir")))
                self.assertTrue(single._status_enabled,
                                "单机位保持 Recorder 自写心跳")
                # ffmpeg 命令行：strftime 模板落在 flat raw 目录
                cmd = single._build_command()
                self.assertEqual(cmd[-1],
                                 str(single.raw_dir / "seg_%Y%m%d_%H%M%S.mp4"))
                self.assertIn(str(cfg.get("capture.source_url")), cmd)
                # status 透传（顶层字段与旧版一致，无 cameras 键）
                st = rec.status()
                for key in ("running", "manager_alive", "paused",
                            "restarts", "last_file", "last_error"):
                    self.assertIn(key, st)
                self.assertNotIn("cameras", st)
            finally:
                db.close()

    def test_multi_camera_layout(self):
        from vivideye.capture.multi import MultiRecorder

        with tempfile.TemporaryDirectory(prefix="bt_mr2_") as d:
            root = Path(d)
            cameras = [
                {"name": "cam1", "url": "http://127.0.0.1:9001/video",
                 "audio_url": "http://127.0.0.1:9001/audio.wav"},
                {"name": "cam2", "url": "http://127.0.0.1:9002/video",
                 "audio_url": None},
            ]
            cfg = make_cfg(root, cameras=cameras)
            db = HighlightsDB(cfg.get("storage.db_path"))
            try:
                rec = MultiRecorder(db=db, cfg=cfg)
                self.assertEqual(sorted(rec.recorders), ["cam1", "cam2"])
                raw_root = Path(cfg.get("storage.raw_dir"))
                # 各机位写 data/raw/<name>/ 子目录
                self.assertEqual(rec.recorders["cam1"].raw_dir, raw_root / "cam1")
                self.assertEqual(rec.recorders["cam2"].raw_dir, raw_root / "cam2")
                # 各机位命令行：URL/音频独立，strftime 模板落在各自子目录
                cmd1 = rec.recorders["cam1"]._build_command()
                self.assertIn("http://127.0.0.1:9001/video", cmd1)
                self.assertIn("http://127.0.0.1:9001/audio.wav", cmd1)
                self.assertEqual(
                    cmd1[-1],
                    str(raw_root / "cam1" / "seg_%Y%m%d_%H%M%S.mp4"))
                cmd2 = rec.recorders["cam2"]._build_command()
                self.assertIn("http://127.0.0.1:9002/video", cmd2)
                self.assertNotIn("audio.wav", "".join(cmd2))
                self.assertEqual(
                    cmd2[-1],
                    str(raw_root / "cam2" / "seg_%Y%m%d_%H%M%S.mp4"))
                # 心跳聚合模式：子 Recorder 禁写，MultiRecorder 提供 cameras 视图
                self.assertFalse(rec.recorders["cam1"]._status_enabled)
                st = rec.status()
                self.assertIn("cameras", st)
                self.assertEqual(sorted(st["cameras"]), ["cam1", "cam2"])
                self.assertIn("recording", st)
            finally:
                db.close()


# ----------------------------------------------------------------------
# 7. retention 清理兼容两种布局
# ----------------------------------------------------------------------
class TestRetentionLayout(unittest.TestCase):

    def test_clean_expired_both_layouts(self):
        from vivideye.capture.retention import clean_expired

        with tempfile.TemporaryDirectory(prefix="bt_ret_") as d:
            raw = Path(d) / "raw"
            cam1 = raw / "cam1"
            cam1.mkdir(parents=True)
            old = time.time() - 7200.0        # 2 小时前 → 超过 1h 保留期
            flat_old = raw / seg_name(old)
            cam_old = cam1 / seg_name(old)
            flat_keep = raw / seg_name(time.time())      # 新片段，保留
            other_file = raw / "notes.txt"               # 非 seg_ 文件，保留
            cam_other = cam1 / "keep.mp4"                # 子目录非 seg_ 文件，保留
            for p, mtime in ((flat_old, old), (cam_old, old),
                             (flat_keep, time.time()), (other_file, old),
                             (cam_other, old)):
                p.write_bytes(b"x" * 1024)
                os.utime(p, (mtime, mtime))

            deleted, freed = clean_expired(raw, retention_hours=1)
            self.assertEqual(deleted, 2, "flat + 子目录各删 1 个过期 seg_ 片段")
            self.assertGreater(freed, 0)
            self.assertFalse(flat_old.exists())
            self.assertFalse(cam_old.exists())
            self.assertTrue(flat_keep.exists(), "未过期片段保留")
            self.assertTrue(other_file.exists(), "非 seg_ 文件不动")
            self.assertTrue(cam_other.exists(), "子目录内非 seg_ 文件不动")
            # 幂等：再跑一遍无新增删除
            self.assertEqual(clean_expired(raw, retention_hours=1)[0], 0)


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
if __name__ == "__main__":
    unittest.main(verbosity=2)
