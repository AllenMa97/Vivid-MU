"""VividEye AI 模块离线自测（mock requests，全程不真实联网）。

覆盖点：
    - providers：base_url 规则（dashscope 自动补 compatible-mode 后缀等）
    - prompts：中英双语切换、场景回退、时间轴说明
    - _extract_json：markdown 围栏 / 前后噪声 / 非法输入
    - chat：图片合并、模型回退（404 换模型）、指数退避重试（5xx）
    - analyze_frames：干净 JSON / 围栏 / 噪声 / 解析失败重试一次 /
      彻底失败返回 {"error":..., "score":0} / 20 帧抽 8 帧 / 音频合并加权
    - daily_summary / generate_image / test_connection 的成功与失败路径

运行方式（在仓库根 /workspace/vivideye 下）：
    /root/miniconda3/envs/gnt/bin/python tests/test_ai_offline.py           # 全部用例
    /root/miniconda3/envs/gnt/bin/python tests/test_ai_offline.py --demo   # 额外打印 analyze_frames 假数据演示
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 保证可以导入仓库根目录下的 vivideye 包
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import vivideye.ai.client as client_mod
from vivideye.ai import prompts, providers
from vivideye.ai.client import AIClient, AIClientError, _extract_json

# 伪造的 JPEG 帧数据（内容无所谓，能被 base64 编码传输即可）
FAKE_JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg-bytes").decode()

# 假的 VLM 判分输出（模拟 qwen3-vl 的正常回答内容）
GOOD_VISION_JSON = {
    "score": 0.82,
    "title": "小猫开门记",
    "caption": "小猫踮起脚尖，第一次学会了压门把手，得意地回头看镜头。",
    "tags": ["可爱", "第一次", "聪明"],
    "subjects": ["小猫"],
    "moments": [{"start": 120.0, "end": 210.5, "reason": "小猫跳上门把手把门打开"}],
}
# 假的音频模型输出
GOOD_AUDIO_JSON = {
    "summary": "小猫兴奋地喵喵叫，背景有电视声",
    "sounds": ["猫叫", "电视声"],
    "score": 0.6,
}


class FakeResponse:
    """requests.Response 的最小替身。"""

    def __init__(self, payload=None, status_code: int = 200,
                 text: str = "", content: bytes = b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = text if text else (
            json.dumps(payload, ensure_ascii=False) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def chat_ok(content: str) -> FakeResponse:
    """一次成功的 chat/completions 响应。"""
    return FakeResponse({"choices": [{"message": {"role": "assistant", "content": content}}],
                         "usage": {"total_tokens": 1}})


def http_error(status: int, msg: str = "boom") -> FakeResponse:
    """一次失败的 HTTP 响应。"""
    return FakeResponse({"error": {"message": msg}}, status_code=status)


class AIClientTestBase(unittest.TestCase):
    """公共 setUp：注入假 key、mock 掉退避 sleep，保证离线且快速。"""

    def setUp(self):
        self.client = AIClient()
        # 测试环境强制确定值（不真实联网）
        self.client.api_key = "test-key"
        self.client.provider = "dashscope"
        self.client.api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.client.max_retries = 2
        # mock 掉指数退避的 sleep，测试不等待
        patcher = mock.patch.object(client_mod.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)


# ----------------------------------------------------------------------
# providers：base_url 规则
# ----------------------------------------------------------------------
class TestProviders(unittest.TestCase):

    def test_dashscope_base_url_rules(self):
        d = "https://dashscope.aliyuncs.com"
        # 为空 -> 官方默认
        self.assertEqual(providers.resolve_base_url("dashscope", ""),
                         providers.DEFAULT_BASE_URLS["dashscope"])
        # 只给根域名（含尾斜杠）-> 自动补 compatible-mode/v1
        self.assertEqual(providers.resolve_base_url("dashscope", d),
                         d + "/compatible-mode/v1")
        self.assertEqual(providers.resolve_base_url("dashscope", d + "/"),
                         d + "/compatible-mode/v1")
        # 已带 compatible-mode -> 原样（去尾斜杠）
        self.assertEqual(providers.resolve_base_url("dashscope", d + "/compatible-mode/v1/"),
                         d + "/compatible-mode/v1")
        # 误配原生 API 路径 -> 重写为 compatible-mode
        self.assertEqual(providers.resolve_base_url("dashscope", d + "/api/v1"),
                         d + "/compatible-mode/v1")

    def test_other_providers(self):
        self.assertEqual(providers.resolve_base_url("openai", ""),
                         "https://api.openai.com/v1")
        self.assertEqual(providers.resolve_base_url("openai", "https://foo/v1"),
                         "https://foo/v1")
        self.assertEqual(providers.resolve_base_url("compatible", "https://my-proxy/v1"),
                         "https://my-proxy/v1")
        # 未知 provider 一律按 compatible 处理
        self.assertEqual(providers.resolve_base_url("whatever", "https://x/v1"),
                         "https://x/v1")

    def test_endpoints(self):
        self.assertEqual(providers.chat_url("https://a/v1/"),
                         "https://a/v1/chat/completions")
        self.assertEqual(providers.images_url("https://a/v1"),
                         "https://a/v1/images/generations")


# ----------------------------------------------------------------------
# prompts：双语与场景
# ----------------------------------------------------------------------
class TestPrompts(unittest.TestCase):

    def test_bilingual_scene_prompts(self):
        zh = prompts.highlight_system_prompt("pet")
        self.assertIn("宠物", zh)          # 场景要点
        self.assertIn("moments", zh)       # JSON 契约
        self.assertIn("空镜头", zh)        # 过滤规则
        en = prompts.highlight_system_prompt("kid", "en_US")
        self.assertIn("kid", en.lower())
        self.assertIn("SAFETY", en)        # 危险行为标注要求

    def test_invalid_scene_mode_falls_back_auto(self):
        p = prompts.highlight_system_prompt("not-a-mode")
        self.assertIn("自动识别", p)        # auto 场景描述

    def test_user_prompt_contains_timeline(self):
        p = prompts.highlight_user_prompt(8, duration=600)
        self.assertIn("8 帧", p)
        self.assertIn("600", p)            # 片段时长锚点

    def test_daily_and_poster_prompts(self):
        hl = [{"time": "09:12", "title": "小猫开门记",
               "caption": "第一次开门", "score": 0.82}]
        dp = prompts.daily_summary_prompt(hl, "2026-09-02")
        self.assertIn("小猫开门记", dp)
        self.assertIn("温暖", dp)
        pp = prompts.poster_image_prompt("小猫第一次学会了开门")
        self.assertIn("海报", pp)
        self.assertIn("不要出现任何文字", pp)

    def test_daily_summary_prompt_empty(self):
        self.assertIn("没有捕捉到高光", prompts.daily_summary_prompt([], "2026-09-02"))


# ----------------------------------------------------------------------
# _extract_json：JSON 鲁棒解析
# ----------------------------------------------------------------------
class TestExtractJson(unittest.TestCase):

    def test_clean(self):
        self.assertEqual(_extract_json('{"score": 0.5}'), {"score": 0.5})

    def test_markdown_fence(self):
        self.assertEqual(_extract_json('```json\n{"score": 0.5}\n```'),
                         {"score": 0.5})
        self.assertEqual(_extract_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_noisy_text(self):
        self.assertEqual(_extract_json('好的，结果如下：\n{"a": 1}\n希望有帮助！'),
                         {"a": 1})

    def test_failures(self):
        self.assertIsNone(_extract_json(""))
        self.assertIsNone(_extract_json("完全不是JSON"))
        self.assertIsNone(_extract_json("[1, 2, 3]"))     # 非对象
        self.assertIsNone(_extract_json('{"broken": '))   # 非法 JSON


# ----------------------------------------------------------------------
# chat：图片合并 / 模型回退 / 重试
# ----------------------------------------------------------------------
class TestChat(AIClientTestBase):

    def test_chat_basic(self):
        with mock.patch.object(client_mod.requests, "post",
                               return_value=chat_ok("你好呀")) as p:
            out = self.client.chat([{"role": "user", "content": "你好"}])
        self.assertEqual(out, "你好呀")
        payload = p.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "qwen3-max")   # text_model 链首位

    def test_chat_images_merged_into_last_user(self):
        with mock.patch.object(client_mod.requests, "post",
                               return_value=chat_ok("ok")) as p:
            self.client.chat(
                [{"role": "system", "content": "sys"},
                 {"role": "user", "content": "看图"}],
                model_key="vision_model",
                images=[FAKE_JPEG_B64,
                        "data:image/jpeg;base64," + FAKE_JPEG_B64])
        messages = p.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "sys"})  # 入参不被修改
        content = messages[1]["content"]
        imgs = [c for c in content if c["type"] == "image_url"]
        self.assertEqual(len(imgs), 2)
        self.assertTrue(imgs[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        texts = [c for c in content if c["type"] == "text"]
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0]["text"], "看图")
        self.assertEqual(p.call_args.kwargs["json"]["model"], "qwen3-vl-flash")

    def test_chat_model_fallback_on_404(self):
        self.client.model_chains["text_model"] = ["no-such-model", "qwen-plus"]
        with mock.patch.object(
                client_mod.requests, "post",
                side_effect=[http_error(404), chat_ok("fallback-ok")]) as p:
            out = self.client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "fallback-ok")
        models = [c.kwargs["json"]["model"] for c in p.call_args_list]
        self.assertEqual(models, ["no-such-model", "qwen-plus"])

    def test_chat_retry_on_500_then_success(self):
        with mock.patch.object(
                client_mod.requests, "post",
                side_effect=[http_error(500), chat_ok("recovered")]) as p:
            out = self.client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "recovered")
        self.assertEqual(p.call_count, 2)                 # 同模型重试一次后成功

    def test_chat_all_fail_raises(self):
        with mock.patch.object(client_mod.requests, "post",
                               side_effect=lambda *a, **k: http_error(500)):
            with self.assertRaises(AIClientError):
                self.client.chat([{"role": "user", "content": "hi"}])

    def test_chat_no_api_key_raises(self):
        self.client.api_key = ""
        with self.assertRaises(AIClientError):
            self.client.chat([{"role": "user", "content": "hi"}])


# ----------------------------------------------------------------------
# analyze_frames：判分主流程（含 JSON 鲁棒性与音频合并）
# ----------------------------------------------------------------------
class TestAnalyzeFrames(AIClientTestBase):

    def test_clean_json(self):
        reply = json.dumps(GOOD_VISION_JSON, ensure_ascii=False)
        with mock.patch.object(client_mod.requests, "post",
                               return_value=chat_ok(reply)) as p:
            result = self.client.analyze_frames([FAKE_JPEG_B64] * 4, None, "pet")
        self.assertNotIn("error", result)
        self.assertEqual(result["score"], 0.82)
        self.assertEqual(result["title"], "小猫开门记")
        self.assertEqual(len(result["moments"]), 1)
        self.assertEqual(result["moments"][0]["end"], 210.5)
        # 请求体：4 个 image_url 部件 + vision 链首位模型
        content = p.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertEqual(len([c for c in content if c["type"] == "image_url"]), 4)
        self.assertEqual(p.call_args.kwargs["json"]["model"], "qwen3-vl-flash")

    def test_markdown_fenced_json(self):
        reply = "```json\n" + json.dumps(GOOD_VISION_JSON, ensure_ascii=False) + "\n```"
        with mock.patch.object(client_mod.requests, "post", return_value=chat_ok(reply)):
            result = self.client.analyze_frames([FAKE_JPEG_B64], None, "pet")
        self.assertNotIn("error", result)
        self.assertEqual(result["score"], 0.82)

    def test_noisy_json(self):
        reply = ("好的！分析结果如下：\n"
                 + json.dumps(GOOD_VISION_JSON, ensure_ascii=False)
                 + "\n如果还有问题随时告诉我~")
        with mock.patch.object(client_mod.requests, "post", return_value=chat_ok(reply)):
            result = self.client.analyze_frames([FAKE_JPEG_B64], None, "home")
        self.assertNotIn("error", result)
        self.assertEqual(result["title"], "小猫开门记")

    def test_retry_once_then_success(self):
        good = "```json\n" + json.dumps(GOOD_VISION_JSON, ensure_ascii=False) + "\n```"
        with mock.patch.object(
                client_mod.requests, "post",
                side_effect=[chat_ok("抱歉我没能输出JSON"),
                             chat_ok(good)]) as p:
            result = self.client.analyze_frames([FAKE_JPEG_B64], None, "auto")
        self.assertEqual(p.call_count, 2)                 # 解析失败 -> 重试一次
        self.assertNotIn("error", result)
        self.assertEqual(result["score"], 0.82)

    def test_total_parse_failure_returns_error_dict(self):
        with mock.patch.object(
                client_mod.requests, "post",
                side_effect=[chat_ok("这不是JSON"),
                             chat_ok("还是不是JSON")]) as p:
            result = self.client.analyze_frames([FAKE_JPEG_B64], None, "auto")
        self.assertEqual(p.call_count, 2)
        self.assertIn("error", result)
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["moments"], [])
        self.assertEqual(result["tags"], [])

    def test_no_api_key_returns_error_dict(self):
        self.client.api_key = ""
        result = self.client.analyze_frames([FAKE_JPEG_B64], None, "pet")
        self.assertIn("error", result)
        self.assertIn("api_key", result["error"])
        self.assertEqual(result["score"], 0)

    def test_empty_frames(self):
        result = self.client.analyze_frames([], None, "pet")
        self.assertIn("error", result)

    def test_frames_sampled_to_8(self):
        reply = json.dumps(GOOD_VISION_JSON, ensure_ascii=False)
        with mock.patch.object(client_mod.requests, "post",
                               return_value=chat_ok(reply)) as p:
            self.client.analyze_frames([FAKE_JPEG_B64] * 20, None, "auto")
        content = p.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertEqual(len([c for c in content if c["type"] == "image_url"]), 8)

    def test_audio_merge(self):
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "audio.wav"
            wav.write_bytes(b"RIFF-fake-wav-bytes")
            vision_reply = json.dumps(GOOD_VISION_JSON, ensure_ascii=False)
            audio_reply = json.dumps(GOOD_AUDIO_JSON, ensure_ascii=False)
            with mock.patch.object(
                    client_mod.requests, "post",
                    side_effect=[chat_ok(vision_reply),
                                 chat_ok(audio_reply)]) as p:
                result = self.client.analyze_frames([FAKE_JPEG_B64] * 2, str(wav), "pet")
        self.assertNotIn("error", result)
        # score 加权：0.7*0.82 + 0.3*0.6 = 0.754
        self.assertAlmostEqual(result["score"], 0.754, places=3)
        # 音频结论并入 caption / tags
        self.assertIn("小猫兴奋地喵喵叫", result["caption"])
        self.assertIn("猫叫", result["tags"])
        self.assertIn("可爱", result["tags"])
        # 第二次调用走 input_audio（qwen-audio 格式）
        audio_payload = p.call_args_list[1].kwargs["json"]
        self.assertEqual(audio_payload["model"], "qwen-audio-turbo")
        parts = audio_payload["messages"][0]["content"]
        self.assertEqual(parts[0]["type"], "input_audio")
        self.assertEqual(parts[0]["input_audio"]["format"], "wav")
        self.assertEqual(parts[0]["input_audio"]["data"],
                         base64.b64encode(b"RIFF-fake-wav-bytes").decode())

    def test_audio_failure_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "audio.wav"
            wav.write_bytes(b"RIFF")
            vision_reply = json.dumps(GOOD_VISION_JSON, ensure_ascii=False)
            # 音频链 2 个模型 × 2 次重试 = 4 次全 500 -> 音频结论放弃，视觉结果不受影响
            with mock.patch.object(
                    client_mod.requests, "post",
                    side_effect=[chat_ok(vision_reply)] + [http_error(500)] * 4):
                result = self.client.analyze_frames([FAKE_JPEG_B64], str(wav), "pet")
        self.assertNotIn("error", result)
        self.assertEqual(result["score"], 0.82)
        self.assertNotIn("声音", result["caption"])

    def test_audio_missing_file_ignored(self):
        vision_reply = json.dumps(GOOD_VISION_JSON, ensure_ascii=False)
        with mock.patch.object(client_mod.requests, "post",
                               return_value=chat_ok(vision_reply)) as p:
            result = self.client.analyze_frames([FAKE_JPEG_B64],
                                                "/no/such/file.wav", "pet")
        self.assertEqual(p.call_count, 1)                 # 只有视觉一次调用
        self.assertEqual(result["score"], 0.82)


# ----------------------------------------------------------------------
# daily_summary / generate_image / test_connection
# ----------------------------------------------------------------------
class TestDailySummary(AIClientTestBase):

    def test_ok(self):
        with mock.patch.object(client_mod.requests, "post",
                               return_value=chat_ok("今天小猫第一次学会了开门……")):
            text = self.client.daily_summary(
                [{"time": "09:12", "title": "小猫开门记",
                  "caption": "第一次开门", "score": 0.82}])
        self.assertIn("小猫", text)

    def test_no_api_key_returns_empty(self):
        self.client.api_key = ""
        self.assertEqual(self.client.daily_summary([{"title": "x"}]), "")

    def test_failure_returns_empty(self):
        with mock.patch.object(client_mod.requests, "post",
                               side_effect=lambda *a, **k: http_error(500)):
            self.assertEqual(self.client.daily_summary([{"title": "x"}]), "")


class TestGenerateImage(AIClientTestBase):

    def test_b64_json_saved(self):
        png_bytes = b"\x89PNG-fake"
        b64 = base64.b64encode(png_bytes).decode()
        resp = FakeResponse({"data": [{"b64_json": b64}]})
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "poster.png"
            with mock.patch.object(client_mod.requests, "post",
                                   return_value=resp) as p:
                got = self.client.generate_image("温馨海报", str(out))
            self.assertEqual(got, out)
            self.assertEqual(out.read_bytes(), png_bytes)
            # DashScope 默认链首位 + 星号 size
            self.assertIn("/images/generations", p.call_args.args[0])
            self.assertEqual(p.call_args.kwargs["json"]["model"], "wanx2.1-t2i-turbo")
            self.assertEqual(p.call_args.kwargs["json"]["size"], "1024*1024")

    def test_url_downloaded(self):
        png_bytes = b"\x89PNG-from-url"
        resp = FakeResponse({"data": [{"url": "https://cdn.example.com/x.png"}]})
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "poster.png"
            with mock.patch.object(client_mod.requests, "post", return_value=resp), \
                 mock.patch.object(client_mod.requests, "get",
                                   return_value=FakeResponse(status_code=200,
                                                             content=png_bytes)) as g:
                got = self.client.generate_image("温馨海报", str(out))
            self.assertEqual(got, out)
            self.assertEqual(out.read_bytes(), png_bytes)
            self.assertEqual(g.call_args.args[0], "https://cdn.example.com/x.png")

    def test_disabled_returns_none(self):
        with mock.patch.object(client_mod.config, "get",
                               side_effect=lambda p, d=None:
                               False if p == "ai.image_gen_enabled" else d):
            self.assertIsNone(self.client.generate_image("x", "/tmp/x.png"))

    def test_no_api_key_returns_none(self):
        self.client.api_key = ""
        self.assertIsNone(self.client.generate_image("x", "/tmp/x.png"))

    def test_all_models_fail_returns_none(self):
        with mock.patch.object(client_mod.requests, "post",
                               side_effect=lambda *a, **k: http_error(500)):
            with tempfile.TemporaryDirectory() as td:
                self.assertIsNone(
                    self.client.generate_image("x", str(Path(td) / "a.png")))


class TestTestConnection(AIClientTestBase):

    def test_ok(self):
        with mock.patch.object(client_mod.requests, "post",
                               return_value=chat_ok("pong")):
            info = self.client.test_connection()
        self.assertTrue(info["ok"])
        self.assertTrue(info["chat_ok"])
        self.assertEqual(info["reply"], "pong")
        self.assertTrue(info["api_key_set"])
        self.assertIn("vision_model", info["models"])

    def test_no_api_key(self):
        self.client.api_key = ""
        info = self.client.test_connection()
        self.assertFalse(info["ok"])
        self.assertFalse(info["api_key_set"])
        self.assertIn("api_key", info["error"])

    def test_failure_reports_error(self):
        with mock.patch.object(client_mod.requests, "post",
                               side_effect=http_error(401)):
            info = self.client.test_connection()
        self.assertFalse(info["ok"])
        self.assertTrue(info["error"])


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
class TestUniformSample(unittest.TestCase):

    def test_under_limit(self):
        self.assertEqual(AIClient._uniform_sample(["a", "b"], 8), ["a", "b"])

    def test_over_limit_keeps_first_and_last(self):
        items = [f"f{i}" for i in range(12)]
        out = AIClient._uniform_sample(items, 8)
        self.assertEqual(len(out), 8)
        self.assertEqual(out[0], "f0")
        self.assertEqual(out[-1], "f11")
        idx = [items.index(x) for x in out]               # 顺序保持（索引递增）
        self.assertEqual(idx, sorted(idx))


class TestMergeImages(unittest.TestCase):

    def test_appends_user_when_missing(self):
        merged = AIClient._merge_images([{"role": "system", "content": "s"}],
                                        [FAKE_JPEG_B64])
        self.assertEqual(merged[0], {"role": "system", "content": "s"})
        self.assertEqual(merged[1]["role"], "user")
        self.assertEqual(merged[1]["content"][0]["type"], "image_url")

    def test_no_images_untouched(self):
        msgs = [{"role": "user", "content": "hi"}]
        self.assertIs(AIClient._merge_images(msgs, None), msgs)


# ----------------------------------------------------------------------
# 演示：analyze_frames 假数据解析结果（供人工核对 / 汇报）
# ----------------------------------------------------------------------
def demo_analyze_frames() -> None:
    """mock requests 跑一次 analyze_frames：12 帧（抽 8）+ 假音频，打印合并结果。"""
    client = AIClient()
    client.api_key = "demo-key"
    client.max_retries = 1
    # 12 帧假数据（JPEG 内容随意），验证“帧多则均匀抽最多 8 帧”
    frames = [base64.b64encode(f"fake-frame-{i:02d}".encode()).decode()
              for i in range(12)]
    # VLM 回答故意带前后噪声 + markdown 围栏，验证解析鲁棒性
    vision_reply = ("当然可以！以下是这段监控的分析结果：\n```json\n"
                    + json.dumps(GOOD_VISION_JSON, ensure_ascii=False)
                    + "\n```\n希望对你有帮助！")
    audio_reply = json.dumps(GOOD_AUDIO_JSON, ensure_ascii=False)

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "seg.wav"
        wav.write_bytes(b"RIFF-fake-wav")
        with mock.patch.object(client_mod.time, "sleep", lambda s: None), \
             mock.patch.object(client_mod.requests, "post",
                               side_effect=[chat_ok(vision_reply),
                                            chat_ok(audio_reply)]):
            result = client.analyze_frames(frames, str(wav), scene_mode="pet")
    print("\n===== analyze_frames 假数据解析验证结果（场景=pet，12帧->8帧，含音频合并）=====")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        sys.argv.remove("--demo")
        demo_analyze_frames()
    unittest.main(verbosity=2)
