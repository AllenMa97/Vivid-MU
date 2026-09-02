"""Unified configuration loader for VividEye.

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

import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "app": {
        "name": "VividEye",
        "language": "zh_CN",           # zh_CN | en_US
        "data_dir": "data",            # relative to repo root
    },
    # ------------------------------------------------------------------
    # Phone / camera capture
    # ------------------------------------------------------------------
    "capture": {
        "source_url": "http://192.168.1.100:8080/video",  # MJPEG/RTSP/HLS from phone app
        "segment_seconds": 600,        # split rolling recording into N-second files
        "record_audio": True,
        "video_codec": "copy",         # copy | h264 (re-encode if source is unstable)
        "retention_hours": 72,         # raw segments older than this are deleted
        "restart_on_failure": True,
    },
    # ------------------------------------------------------------------
    # Pipeline (inherited from VividMU 3-step flow)
    # ------------------------------------------------------------------
    "pipeline": {
        "run_interval_minutes": 30,    # how often new raw segments are processed
        "max_segments_per_run": 24,
        "min_highlight_score": 0.55,
        "scene_mode": "auto",          # auto | pet | kid | home
        "step1": {"enabled": True},
        "step2": {
            "enabled": True,
            "yolo_enabled": True,
            "yolo_conf": 0.25,
            "device_policy": "yolo:auto,clip:auto,vad:cpu,face:auto",
        },
        "step3": {"enabled": True},
    },
    # ------------------------------------------------------------------
    # AI capabilities
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
        "max_concurrent_requests": 5,
        # online generation APIs (image / audio / video)
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
    },
    # ------------------------------------------------------------------
    # Web server
    # ------------------------------------------------------------------
    "server": {
        "host": "0.0.0.0",
        "port": 8666,
        "live_stream_proxy": True,     # proxy the phone stream so the UI shows live view
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
    data: dict[str, Any] = dict(DEFAULTS)
    candidates = [
        Path(config_file) if config_file else None,
        REPO_ROOT / "user_config.yaml",
        REPO_ROOT / "config.yaml",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            with open(c, "r", encoding="utf-8") as f:
                user = yaml.safe_load(f) or {}
            data = _deep_merge(data, user)
    data = _apply_env(data)
    return Config(data)


# module-level singleton
config = load_config()
