"""VividEye 子弹时间集成离线自测（不依赖真实渲染器，全程可重复运行）。

被测对象与策略：
    1. pipeline/orchestrator —— 高光入库后的子弹时间触发：
       用 sys.modules 注入**伪造的** ``vivideye.bullettime`` 模块
       （BulletTimeRenderer.auto_render 返回假路径、parse_segment_start
       返回固定 epoch），mock 掉 ffmpeg 剪辑与 AI 客户端，跑真实的
       ``_process_segment`` 全流程，用 FakeDB 验证 set_bullet_time：
         ① 高分 + moments -> 恰好调用一次（挂在最长 moment 的高光上，
            center = 段起始 epoch + 最长 moment 中点）；
            低分（< bullet_time.min_score）/ 无 moments -> 不调用；
            恰好等于阈值（0.75）-> 调用（边界 >=）；
         ② bullet_time.enabled = false -> 不调用；
         ③ sys.modules 注入 None 模拟上游模块缺失 -> 管线正常完成；
    2. server/app.py ——
         ④ GET /api/highlights/{id}/bullettime：真实 DB 塞
            bullet_time_path 指向临时文件 -> 200 且 content-type 为
            video/*；无路径 / 文件被清理 / 高光不存在 -> 404；
            列表接口经 SELECT * 自动透出新列（truthy / null）；
         ⑤ POST /api/config：bullet_time.enabled / min_score 在键级
            白名单内可写并落盘 user_config.yaml、热更新内存配置；
            非白名单键（style）被忽略并提示；min_score 非法 -> 400。

运行方式（在仓库根 /workspace/vivideye 下）：
    python tests/test_bullettime_integration.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# 保证可以导入仓库根目录下的 vivideye 包
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from vivideye.config import Config
from vivideye.pipeline import orchestrator as orch
from vivideye.pipeline.orchestrator import PipelineService

# 伪造的段起始 epoch（parse_segment_start 的假返回值）
FAKE_SEG_START = 1700000100.0


# ----------------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------------
class FakeDB:
    """HighlightsDB 的最小替身：记录 add_highlight / set_bullet_time。"""

    def __init__(self):
        self.added: list[dict] = []
        self.bt_calls: list[tuple[str, str]] = []
        self.marks: list[tuple[str, str]] = []
        self._n = 0

    def add_highlight(self, **kwargs) -> str:
        self._n += 1
        hid = f"hl{self._n:03d}"
        self.added.append({"id": hid, **kwargs})
        return hid

    def set_bullet_time(self, hid: str, path: str) -> int:
        self.bt_calls.append((hid, path))
        return 1

    def mark_segment(self, path, status) -> None:
        self.marks.append((str(path), status))


class FakeAIClient:
    """AI 客户端替身：analyze_frames 原样返回预置 dict。"""

    def __init__(self, raw: dict):
        self._raw = raw

    def analyze_frames(self, frames, audio_path, scene_mode):
        return self._raw


def install_fake_bullettime(records: list[dict]) -> None:
    """向 sys.modules 注入伪造的 vivideye.bullettime 包。

    - BulletTimeRenderer().auto_render(center, hid, dir) 记录调用参数并
      返回 ``<dir>/bt_<hid>.mp4`` 假路径（不真的渲染）；
    - parse_segment_start 固定返回 FAKE_SEG_START。

    用 addCleanup 恢复原状，保证测试间互不污染、可重复运行。
    """
    bt_mod = types.ModuleType("vivideye.bullettime")
    renderer_mod = types.ModuleType("vivideye.bullettime.renderer")

    class FakeRenderer:
        def auto_render(self, center_ts: float, hid: str, highlights_dir):
            records.append({"center": center_ts, "hid": hid,
                            "dir": str(highlights_dir)})
            return Path(highlights_dir) / f"bt_{hid}.mp4"

    bt_mod.BulletTimeRenderer = FakeRenderer
    renderer_mod.parse_segment_start = lambda p: FAKE_SEG_START

    keys = ("vivideye.bullettime", "vivideye.bullettime.renderer")
    saved = {k: sys.modules.get(k, mock.sentinel.MISSING) for k in keys}
    sys.modules["vivideye.bullettime"] = bt_mod
    sys.modules["vivideye.bullettime.renderer"] = renderer_mod

    def _restore():
        for k, v in saved.items():
            if v is mock.sentinel.MISSING:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    import unittest as _ut
    _ut.TestCase.addCleanup  # noqa: B018 —— 占位，真正的恢复由调用方注册


def _noop_module(name: str):
    return None


# ----------------------------------------------------------------------
# ①②③ orchestrator：子弹时间触发逻辑
# ----------------------------------------------------------------------
class TestOrchestratorBulletTime(unittest.TestCase):
    """_process_segment 全流程（mock ffmpeg / AI，伪造 bullettime 模块）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ve_bt_orch_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.records: list[dict] = []       # FakeRenderer.auto_render 调用记录

        # 片段文件（内容无所谓，cut_clip 已 mock）
        self.seg_file = Path(self.tmp) / "seg_20260904_120000.mp4"
        self.seg_file.write_bytes(b"fake-segment-bytes")
        self.seg = {"id": "seg001", "path": str(self.seg_file),
                    "started_at": 1700000000.0, "duration": 600.0}

        # 公共打桩：ffmpeg 剪辑/缩略图/采样/音频/AI 客户端全部离线化
        for name in ("cut_clip", "make_thumbnail", "sample_frames"):
            p = mock.patch.object(orch, name, lambda *a, **k: None)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(orch, "extract_audio", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _make_svc(self, bullet_time_cfg: dict):
        cfg = Config({
            "storage": {
                "db_path": str(Path(self.tmp) / "data/vivideye.db"),
                "highlights_dir": str(Path(self.tmp) / "data/highlights"),
            },
            "bullet_time": bullet_time_cfg,
        })
        db = FakeDB()
        return PipelineService(db=db, cfg=cfg), db

    def _run(self, svc, raw: dict) -> int:
        """跑一遍真实 _process_segment（阈值 0.55，场景 auto）。"""
        with mock.patch.object(svc, "_get_ai_client",
                               return_value=FakeAIClient(raw)):
            return svc._process_segment(self.seg, 0.55, "auto")

    # ---- ① 高分 + moments：恰好一次，挂在最长 moment 的高光上 ----
    def test_high_score_triggers_bullet_time_once(self):
        install_fake_bullettime(self.records)
        # 恢复 sys.modules（install_fake_bullettime 只负责注入）
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime", None))
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime.renderer", None))

        svc, db = self._make_svc({"enabled": True, "min_score": 0.75})
        raw = {"score": 0.9, "title": "小猫飞檐走壁", "caption": "帅",
               "tags": ["可爱"], "subjects": ["猫"],
               "moments": [{"start": 10.0, "end": 30.0},
                           {"start": 100.0, "end": 140.0}]}
        made = self._run(svc, raw)

        self.assertEqual(made, 2)                       # 两个 moment 都导出
        self.assertEqual(len(db.added), 2)
        self.assertEqual(len(self.records), 1)          # 渲染器只被调一次
        # 最长 moment (100,140) 对应第 2 条高光；center = 段起始 + 中点
        self.assertEqual(self.records[0]["hid"], "hl002")
        self.assertAlmostEqual(self.records[0]["center"],
                               FAKE_SEG_START + (100.0 + 140.0) / 2.0)
        # set_bullet_time 被调用且参数正确（hid + auto_render 返回的路径）
        hl_dir = Path(self.tmp) / "data/highlights"
        self.assertEqual(db.bt_calls,
                         [("hl002", str(hl_dir / "bt_hl002.mp4"))])
        # 主管线不受影响：片段标 done
        self.assertIn((str(self.seg_file), "done"), db.marks)

    # ---- ① 边界：恰好等于 min_score（>= 含等于）也要触发 ----
    def test_score_exactly_at_threshold_triggers(self):
        install_fake_bullettime(self.records)
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime", None))
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime.renderer", None))

        svc, db = self._make_svc({"enabled": True, "min_score": 0.75})
        made = self._run(svc, {"score": 0.75, "title": "压线时刻",
                               "moments": [{"start": 5.0, "end": 25.0}]})
        self.assertEqual(made, 1)
        self.assertEqual(len(db.bt_calls), 1)

    # ---- ① 低分（导出高光但 < bullet_time.min_score）：不调用 ----
    def test_low_score_skips_bullet_time(self):
        install_fake_bullettime(self.records)
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime", None))
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime.renderer", None))

        svc, db = self._make_svc({"enabled": True, "min_score": 0.75})
        # 0.6 >= 管线阈值 0.55 -> 高光照常导出；但 < 0.75 -> 无子弹时间
        made = self._run(svc, {"score": 0.6, "title": "普通时刻",
                               "moments": [{"start": 10.0, "end": 30.0}]})
        self.assertEqual(made, 1)
        self.assertEqual(db.bt_calls, [])
        self.assertEqual(self.records, [])
        self.assertIn((str(self.seg_file), "done"), db.marks)

    # ---- ① 无 moments（兜底截取）：不调用 ----
    def test_no_moments_skips_bullet_time(self):
        install_fake_bullettime(self.records)
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime", None))
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime.renderer", None))

        svc, db = self._make_svc({"enabled": True, "min_score": 0.75})
        made = self._run(svc, {"score": 0.9, "title": "没有时刻的高光"})
        self.assertEqual(made, 1)                       # 兜底 moment 照常导出
        self.assertEqual(db.bt_calls, [])               # 但不触发子弹时间
        self.assertEqual(self.records, [])

    # ---- ② bullet_time.enabled=false：不调用 ----
    def test_disabled_skips_bullet_time(self):
        install_fake_bullettime(self.records)
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime", None))
        self.addCleanup(lambda: sys.modules.pop("vivideye.bullettime.renderer", None))

        svc, db = self._make_svc({"enabled": False, "min_score": 0.75})
        made = self._run(svc, {"score": 0.95, "title": "再高分也不合成",
                               "moments": [{"start": 10.0, "end": 30.0}]})
        self.assertEqual(made, 1)
        self.assertEqual(db.bt_calls, [])
        self.assertEqual(self.records, [])

    # ---- ③ 上游模块缺失（sys.modules 注入 None）：管线仍正常完成 ----
    def test_import_failure_does_not_break_pipeline(self):
        saved = {k: sys.modules.get(k, mock.sentinel.MISSING)
                 for k in ("vivideye.bullettime",
                           "vivideye.bullettime.renderer")}
        sys.modules["vivideye.bullettime"] = None        # import 即失败
        sys.modules["vivideye.bullettime.renderer"] = None

        def _restore():
            for k, v in saved.items():
                if v is mock.sentinel.MISSING:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.addCleanup(_restore)

        svc, db = self._make_svc({"enabled": True, "min_score": 0.75})
        made = self._run(svc, {"score": 0.9, "title": "模块缺失",
                               "moments": [{"start": 10.0, "end": 30.0}]})
        # 高光照常导出、片段标 done、无子弹时间、无异常抛出
        self.assertEqual(made, 1)
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.bt_calls, [])
        self.assertEqual(self.marks_done(db), 1)

    @staticmethod
    def marks_done(db: FakeDB) -> int:
        return sum(1 for _, s in db.marks if s == "done")


# ----------------------------------------------------------------------
# ④⑤ server：子弹时间路由 + 配置白名单
# ----------------------------------------------------------------------
class ServerTestBase(unittest.TestCase):
    """公共环境：临时仓库根 + 临时存储路径，app 配置全部指向 /tmp。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ve_bt_srv_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp)
        self.cfg = Config({
            "storage": {
                "db_path": str(self.root / "data/vivideye.db"),
                "highlights_dir": str(self.root / "data/highlights"),
                "raw_dir": str(self.root / "data/raw"),
                "digest_dir": str(self.root / "data/digests"),
            },
        })
        # 延迟导入：保证 sys.path 已注入仓库根
        import vivideye.config as config_mod
        import vivideye.server.app as app_mod
        from fastapi.testclient import TestClient
        self.app_mod = app_mod
        self.TestClient = TestClient
        # 把 app 模块的 config / REPO_ROOT 与 config 模块的 REPO_ROOT 全部
        # 指到临时目录：user_config.yaml 写入与 load_config 重读都落在 /tmp，
        # 不污染真实仓库；测试结束自动恢复。
        for target, attr, val in ((app_mod, "config", self.cfg),
                                  (app_mod, "REPO_ROOT", self.root),
                                  (config_mod, "REPO_ROOT", self.root)):
            p = mock.patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def make_client(self):
        app = self.app_mod.create_app()
        return self.TestClient(app)


class TestServerBulletTimeRoute(ServerTestBase):
    """④ GET /api/highlights/{id}/bullettime。"""

    def test_route_serves_file_with_video_content_type(self):
        client = self.make_client()
        db = client.app.state.db

        hl_dir = self.root / "data/highlights"
        bt_file = hl_dir / "bt_demo.mp4"
        bt_file.write_bytes(b"fake-bullet-time-mp4")

        hid_ok = db.add_highlight(video_path=str(hl_dir / "hl1.mp4"),
                                  title="有子弹时间")
        self.assertEqual(db.set_bullet_time(hid_ok, str(bt_file)), 1)

        resp = client.get(f"/api/highlights/{hid_ok}/bullettime")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("video/"))
        self.assertEqual(resp.content, b"fake-bullet-time-mp4")

        # 列表接口经 SELECT * 自动透出新列（truthy）
        items = {it["id"]: it for it in
                 client.get("/api/highlights").json()["items"]}
        self.assertTrue(items[hid_ok]["bullet_time_path"])

    def test_route_404_cases(self):
        client = self.make_client()
        db = client.app.state.db

        hl_dir = self.root / "data/highlights"
        hid_no_bt = db.add_highlight(video_path=str(hl_dir / "hl2.mp4"),
                                     title="未合成子弹时间")
        # 无 bullet_time_path -> 404
        self.assertEqual(
            client.get(f"/api/highlights/{hid_no_bt}/bullettime").status_code,
            404)
        # 高光不存在 -> 404
        self.assertEqual(
            client.get("/api/highlights/no-such-id/bullettime").status_code,
            404)
        # 路径在库但文件已被清理 -> 404
        hid_gone = db.add_highlight(video_path=str(hl_dir / "hl3.mp4"),
                                    title="文件被清理")
        db.set_bullet_time(hid_gone, str(hl_dir / "bt_gone.mp4"))
        self.assertEqual(
            client.get(f"/api/highlights/{hid_gone}/bullettime").status_code,
            404)

        # 列表接口：未合成的高光 bullet_time_path 为 null（前端判 truthy）
        items = {it["id"]: it for it in
                 client.get("/api/highlights").json()["items"]}
        self.assertIsNone(items[hid_no_bt]["bullet_time_path"])


class TestServerBulletTimeConfig(ServerTestBase):
    """⑤ POST /api/config：bullet_time 键级白名单。"""

    def test_post_bullet_time_enabled_and_min_score(self):
        client = self.make_client()
        resp = client.post("/api/config", json={
            "bullet_time": {"enabled": False, "min_score": 0.85}})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"], body)

        # 落盘到（打补丁后的临时）user_config.yaml
        user_yaml = self.root / "user_config.yaml"
        self.assertTrue(user_yaml.is_file())
        saved = yaml.safe_load(user_yaml.read_text(encoding="utf-8"))
        self.assertEqual(saved["bullet_time"]["enabled"], False)
        self.assertEqual(saved["bullet_time"]["min_score"], 0.85)

        # 热更新立即生效：响应与后续 GET 都能看到新值
        self.assertEqual(body["config"]["bullet_time"]["enabled"], False)
        got = client.get("/api/config").json()
        self.assertEqual(got["bullet_time"]["min_score"], 0.85)

    def test_post_non_whitelisted_bullet_time_keys_ignored(self):
        client = self.make_client()
        # 只发非白名单键：整体被忽略，明确提示只读
        resp = client.post("/api/config",
                           json={"bullet_time": {"style": "hyper"}})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("bullet_time.style", body["message"])
        # user_config.yaml 不应被写入
        self.assertFalse((self.root / "user_config.yaml").exists())

    def test_post_invalid_min_score_rejected(self):
        client = self.make_client()
        resp = client.post("/api/config",
                           json={"bullet_time": {"min_score": "abc"}})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse((self.root / "user_config.yaml").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
