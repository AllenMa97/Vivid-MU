"""片段采样器：从原始片段抽取帧（base64 JPEG）与低码率音频。

手机端只做轻量的 ffmpeg 处理，产物交给云端多模态模型：
- ``sample_frames``：按 ``pipeline.sample_fps`` 抽帧，缩放、JPEG 压缩后
  base64 编码，直接从 ffmpeg 管道读取（不产生中间文件）；
- ``extract_audio``：转出 16 kHz 单声道 wav（默认取片段中间的 60 秒，
  音频内容更具代表性；片段无音轨时返回 None）。
"""

from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 发往云端的最大帧数（600 秒 @0.5fps = 300 帧；ffmpeg 侧按
# max_frames/duration 反算等效 fps + ``-frames:v`` 硬性限流，
# 避免超长片段全量解码浪费 CPU/内存）
DEFAULT_MAX_FRAMES = 60
# 抽帧缩放宽度（像素），足够 VLM 理解画面且省流量
FRAME_WIDTH = 640
# 音频片段限制（秒）与采样参数
AUDIO_MAX_SECONDS = 60
AUDIO_SAMPLE_RATE = 16000

# JPEG 的 SOI/EOI 标记，用于从 mjpeg 管道流中切分单帧
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"


class SamplerError(RuntimeError):
    """采样失败（ffmpeg 缺失、片段损坏等）。"""


def _ffmpeg_bin() -> str:
    bin_path = shutil.which("ffmpeg")
    if bin_path is None:
        raise SamplerError("未找到 ffmpeg，请先在 Termux 安装：pkg install ffmpeg")
    return bin_path


def _run_ffmpeg(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """执行 ffmpeg 子进程（统一加上通用参数），返回 CompletedProcess。"""
    cmd = [_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin"] + args
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise SamplerError(
            f"ffmpeg 执行失败（code={proc.returncode}）："
            f"{proc.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    return proc


# ----------------------------------------------------------------------
# 抽帧
# ----------------------------------------------------------------------
def sample_frames(video_path: str | Path, fps: float = 0.5,
                  max_frames: int = DEFAULT_MAX_FRAMES) -> list[str]:
    """从片段抽帧，返回 base64 编码的 JPEG 列表。

    - 按 ``fps`` 抽帧并缩放到 ``FRAME_WIDTH`` 宽；
    - ``max_frames`` 限量解码（默认 60）：先按片段时长把 fps 反算收紧
      到 ``max_frames / duration``（保证帧均匀覆盖整个片段），再用
      ``-frames:v`` 硬性封顶，600s 片段不会全量解码出 300 帧；
    - 帧数据通过 image2pipe 从 ffmpeg stdout 直读，无中间文件。
    """
    video_path = Path(video_path)
    fps = max(0.05, float(fps or 0.5))
    effective_fps = fps
    if max_frames and int(max_frames) > 0:
        duration = probe_duration(video_path)
        if duration and duration > 0:
            # 反算等效 fps：整段均匀覆盖最多 max_frames 帧所需的采样率
            effective_fps = max(0.01, min(fps, int(max_frames) / duration))
    args: list[str] = ["-i", str(video_path)]
    if max_frames and int(max_frames) > 0:
        args += ["-frames:v", str(int(max_frames))]
    args += [
        "-vf", f"fps={effective_fps},scale={FRAME_WIDTH}:-2",
        "-q:v", "5",
        "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
    ]
    proc = _run_ffmpeg(args)
    jpegs = _split_mjpeg(proc.stdout)
    if not jpegs:
        raise SamplerError(f"未能从片段抽取到任何帧：{video_path}")
    # 兜底：时长探测失败/取整误差导致超量时，内存内等间隔抽样（保留首尾）
    if max_frames and len(jpegs) > max_frames:
        jpegs = _even_pick(jpegs, max_frames)
    logger.info("片段 %s 抽帧 %d 张（fps=%.2f）", video_path.name, len(jpegs), fps)
    return [base64.b64encode(j).decode("ascii") for j in jpegs]


def _split_mjpeg(data: bytes) -> list[bytes]:
    """按 SOI/EOI 标记切分连续的 MJPEG 字节流；尾部残帧丢弃。"""
    frames: list[bytes] = []
    i = 0
    while True:
        start = data.find(_JPEG_SOI, i)
        if start < 0:
            break
        end = data.find(_JPEG_EOI, start + 2)
        if end < 0:
            break  # 半张帧（进程被截断），丢弃
        frames.append(data[start:end + 2])
        i = end + 2
    return frames


def _even_pick(items: list, count: int) -> list:
    """等间隔抽取 count 个元素（保留首尾）。"""
    n = len(items)
    if count <= 1 or n <= 1:
        return items[:1]
    idxs = sorted({round(i * (n - 1) / (count - 1)) for i in range(count)})
    return [items[i] for i in idxs]


# ----------------------------------------------------------------------
# 音频提取
# ----------------------------------------------------------------------
def extract_audio(video_path: str | Path, out_path: str | Path | None = None,
                  max_seconds: int = AUDIO_MAX_SECONDS) -> Optional[Path]:
    """从片段提取 16 kHz 单声道 wav，返回文件路径；无音轨/失败返回 None。

    默认截取片段“中间”的 ``max_seconds`` 秒，避免只拿到开场静音。
    """
    video_path = Path(video_path)
    if out_path is None:
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="vivideye_audio_")
        import os
        os.close(fd)
        out_path = Path(tmp)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    duration = probe_duration(video_path)
    if duration is None or duration <= max_seconds:
        seek = 0.0
    else:
        seek = (duration - max_seconds) / 2.0

    proc = _run_ffmpeg([
        "-ss", f"{seek:.2f}", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE),
        "-t", str(max_seconds),
        "-c:a", "pcm_s16le", "-f", "wav", str(out_path),
    ], check=False)
    if proc.returncode != 0:
        logger.warning("音频提取失败（可能无音轨）：%s",
                       proc.stderr.decode("utf-8", "replace").strip()[-200:])
        out_path.unlink(missing_ok=True)
        return None
    if not out_path.is_file() or out_path.stat().st_size < 44:  # 44 字节 = 空 wav 头
        out_path.unlink(missing_ok=True)
        return None
    logger.info("片段 %s 提取音频 %.0f 秒（起点 %.1fs）",
                video_path.name, min(max_seconds, duration or max_seconds), seek)
    return out_path


def probe_duration(video_path: str | Path) -> Optional[float]:
    """用 ``ffmpeg -i`` 解析片段时长（秒）；不依赖 ffprobe。

    ``ffmpeg -i`` 无输出参数时会以非零码退出，但 stderr 中含有
    ``Duration: HH:MM:SS.ms`` 行，属于通用行为，Termux 版本一致。
    """
    proc = subprocess.run(
        [_ffmpeg_bin(), "-hide_banner", "-i", str(video_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    text = proc.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    hours, minutes, seconds = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return hours * 3600 + minutes * 60 + seconds
