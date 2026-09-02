"""Unified configuration loader for VividEye (PHONE-FIRST).

All runtime components (capture, pipeline, AI orchestration, web server)
run on the phone itself inside Termux. The PC only performs one-shot
deployment.

Usage:
    from vivideye.config import config
    api_key = config.get("ai.api_key")
    seg_len = config.get("capture.segment_seconds", 600)

Layered loading (later wins):
    1. built-in defaults (DEFAULTS below)
    2. config.yaml next to the repo root (if present)
    3. user_config.yaml next to the repo root (if present, git-ignored)
    4. environment variables:  VIVIDEYE_AI__API_KEY -> ai.api_key
       (double underscore "__" maps to a nested level)
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # allow importing config before deps installed
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "app": {
        "name": "VividEye",
        "language": "zh_CN",           # zh_CN | en_US
        "data_dir": "data",            # relative to repo root (on the phone)
    },
    # ------------------------------------------------------------------
    # Phone capture: loopback stream from an IP-camera app on the SAME phone
    # ------------------------------------------------------------------
    "capture": {
        "source_url": "http://127.0.0.1:8080/video",   # loopback MJPEG
        "audio_url": "http://127.0.0.1:8080/audio.wav",
        "record_audio": True,
        "segment_seconds": 600,        # one file per 10 minutes
        "video_codec": "copy",         # copy | h264
        # 原始切片保留时长（小时）：原始片段体积 ≈ 摄像头码率 × 时长，
        # 是存储占用的大头。默认 24h 仅作为高光提取的时间窗口，过期自动
        # 删除；高光/收藏单独保存、不受此清理影响。P20 存储有限，建议
        # 保持默认（存储安全优先）；空间充裕可适当调大。
        "retention_hours": 24,
        "restart_on_failure": True,
        "watchdog_seconds": 60,        # recorder restart threshold
    },
    # ------------------------------------------------------------------
    # Pipeline (VividMU-derived; heavy inference offloaded to cloud APIs)
    # ------------------------------------------------------------------
    "pipeline": {
        "run_interval_minutes": 30,    # how often new raw segments are processed
        "max_segments_per_run": 8,     # phone-friendly batch size
        "min_highlight_score": 0.55,
        "scene_mode": "auto",          # auto | pet | kid | home
        "local_nn_enabled": False,     # local ONNX/YOLO inference (P20: keep off)
        "sample_fps": 0.5,             # frames per second sent to cloud VLM
        "step1": {"enabled": True},
        "step2": {
            "enabled": True,
            "yolo_enabled": False,     # cloud VLM replaces on-device YOLO
        },
        "step3": {"enabled": True},
    },
    # ------------------------------------------------------------------
    # AI capabilities (online APIs; the phone is the eye, cloud is the brain)
    # ------------------------------------------------------------------
    "ai": {
        "provider": "dashscope",       # dashscope | openai | compatible
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",                 # or set VIVIDEYE_AI__API_KEY
        "vision_model": "qwen3-vl-flash",
        "audio_model": "qwen-audio-turbo",
        "text_model": "qwen3-max",
        "fast_model": "qwen-flash",
        "request_timeout": 60,
        "max_retries": 3,
        "max_concurrent_requests": 3,
        "image_gen_enabled": True,
        "audio_gen_enabled": True,
        "video_gen_enabled": False,    # heavy; off by default
    },
    # ------------------------------------------------------------------
    # Storage / highlights
    # ------------------------------------------------------------------
    "storage": {
        "db_path": "data/vivideye.db",
        "highlights_dir": "data/highlights",
        "raw_dir": "data/raw",
        "digest_dir": "data/digests",
        "min_free_gb": 2,              # pause recording below this free space
        "highlights_retention_days": 30,  # delete non-favorite highlights older than N days
    },
    # ------------------------------------------------------------------
    # Web server (runs on the phone; LAN devices browse to it)
    # ------------------------------------------------------------------
    "server": {
        "host": "0.0.0.0",
        "port": 8666,
        "live_stream_proxy": True,     # proxy camera stream for UI live view
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_env(cfg: dict, prefix: str = "VIVIDEYE") -> dict:
    for key, value in os.environ.items():
        if not key.startswith(prefix + "_"):
            continue
        path = key[len(prefix) + 1:].lower().split("__")
        node = cfg
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = value
    return cfg


class Config:
    """Dot-access wrapper around the merged config dict."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def as_dict(self) -> dict[str, Any]:
        return self._data

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    def data_path(self, *parts: str) -> Path:
        """Resolve a path under the configured data dir."""
        base = Path(self.get("app.data_dir", "data"))
        if not base.is_absolute():
            base = REPO_ROOT / base
        p = base.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def load_config(config_file: str | Path | None = None) -> Config:
    # deepcopy：_apply_env / _deep_merge 会原地修改嵌套 dict，
    # 浅拷贝会把环境变量写穿到模块级 DEFAULTS
    data: dict[str, Any] = copy.deepcopy(DEFAULTS)
    # 候选顺序即合并顺序（后合并者优先）：config.yaml 先、user_config.yaml
    # 后，保证用户个性化配置最终生效
    candidates = [
        Path(config_file) if config_file else None,
        REPO_ROOT / "config.yaml",
        REPO_ROOT / "user_config.yaml",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            if yaml is None:
                break
            with open(c, "r", encoding="utf-8") as f:
                user = yaml.safe_load(f) or {}
            data = _deep_merge(data, user)
    data = _apply_env(data)
    return Config(data)


# module-level singleton
config = load_config()
