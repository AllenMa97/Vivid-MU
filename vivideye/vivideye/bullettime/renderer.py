"""子弹时间渲染引擎（多机位真实帧环绕 + 单机位虚拟机位合成）。

渲染管线（全部基于 PATH 中的 ffmpeg/ffprobe，串行执行，CPU 友好）：

    1. find_segments：按 seg_YYYYmmdd_HHMMSS.mp4 文件名时间戳定位覆盖
       目标时刻 [center-window/2, center+window/2] 的原始片段
       （兼容 flat 与 <cam>/ 子目录两种布局）；
    2. extract_frame：ffmpeg -ss 快速 seek 从每机位各抽 1 帧高清 jpg；
    3. 真机位 >= 2 帧 → 直接环绕；仅 1 帧且 virtual_mode 允许 →
       synthesize_angles 对该帧做 n 个数字变焦+平移裁剪，合成虚拟机位；
    4. 每帧生成一个短片段（静态帧 loop 或 zoompan 缓推），再用 ffmpeg
       xfade 滤镜链交叉淡化拼接成 duration_seconds 的 mp4
       （libx264 veryfast / yuv420p / faststart / 无音轨 / 720p 上限）；
    5. style=pingpong 时角度序列为 frames + reversed(frames[1:-1])，
       播放像"环绕去又回"；rotate 为顺序环绕。

任何失败一律返回 None、不抛异常（调用方可安全降级）。
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from vivideye.config import Config, config
from vivideye.paths import resolve_path

logger = logging.getLogger(__name__)

# 片段文件名格式：seg_YYYYmmdd_HHMMSS.mp4（与 capture/recorder.py 一致）
_SEG_RE = re.compile(r"^seg_(\d{8}_\d{6})\.mp4$")
_SEG_TIME_FMT = "%Y%m%d_%H%M%S"

# 输出规格（720p 上限）
_OUT_W, _OUT_H = 1280, 720
_FPS = 25
# 单条 ffmpeg 子进程的超时（秒），防挂死
_FFMPEG_TIMEOUT = 180.0


# ----------------------------------------------------------------------
# 片段定位
# ----------------------------------------------------------------------
def parse_segment_start(path: str | Path) -> Optional[float]:
    """从 seg_YYYYmmdd_HHMMSS.mp4 文件名解析录制起始时间（本地时区 epoch）。

    解析失败（命名不合规 / 非法日期）返回 None。
    """
    m = _SEG_RE.match(Path(path).name)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), _SEG_TIME_FMT))
    except (ValueError, OverflowError):
        return None


def find_segments(center_ts: float, window: float,
                  raw_root: str | Path,
                  camera_names: Optional[Sequence[str]] = None
                  ) -> list[tuple[str, Path, float]]:
    """扫描 raw 目录，返回覆盖 [center-window/2, center+window/2] 的片段。

    - 兼容两种布局：flat ``raw_root/seg_*.mp4``（机位名 "main"）与
      子目录 ``raw_root/<cam>/seg_*.mp4``（机位名 = 目录名）；
    - ``camera_names``：None 时扫描全部机位；否则只收集指定机位
      （"main" 对应 flat 布局）；
    - 段的覆盖区间用相邻段起始时间近似（段 i 的结束 = 段 i+1 的开始）；
     每机位最后一段无后继，用文件 mtime 近似其结束时刻；
    - 返回 [(机位名, 路径, 段起始epoch)]，按（机位名, 起始时间）排序。
    """
    raw_root = Path(raw_root)
    if not raw_root.is_dir():
        return []
    lo = center_ts - window / 2.0
    hi = center_ts + window / 2.0
    want = set(camera_names) if camera_names is not None else None

    groups: dict[str, list[tuple[float, Path]]] = {}

    def _collect(cam: str, folder: Path) -> None:
        items: list[tuple[float, Path]] = []
        try:
            for p in folder.glob("seg_*.mp4"):
                if not p.is_file():
                    continue
                st = parse_segment_start(p)
                if st is None:
                    continue
                items.append((st, p))
        except OSError as e:
            logger.warning("扫描目录失败 %s：%s", folder, e)
        if items:
            groups.setdefault(cam, []).extend(items)

    # flat 布局 → 机位名 "main"
    if want is None or "main" in want:
        _collect("main", raw_root)

    # 一层子目录 → 机位名 = 目录名
    try:
        subdirs = sorted(d for d in raw_root.iterdir() if d.is_dir())
    except OSError as e:
        logger.warning("扫描 raw 目录失败 %s：%s", raw_root, e)
        subdirs = []
    for d in subdirs:
        if want is not None and d.name not in want:
            continue
        _collect(d.name, d)

    out: list[tuple[str, Path, float]] = []
    for cam, items in groups.items():
        items.sort(key=lambda x: x[0])
        for i, (st, p) in enumerate(items):
            if st > hi:
                continue
            # 段结束 = 下一段开始；最后一段用 mtime 近似（写完时刻）
            end = items[i + 1][0] if i + 1 < len(items) else None
            if end is None:
                try:
                    end = p.stat().st_mtime
                except OSError:
                    end = st + 3600.0
            if end < lo:
                continue
            out.append((cam, p, st))
    out.sort(key=lambda x: (x[0], x[2]))
    return out


# ----------------------------------------------------------------------
# ffmpeg / ffprobe 小工具
# ----------------------------------------------------------------------
def _run(cmd: list[str], timeout: float = _FFMPEG_TIMEOUT
         ) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def _probe_duration(path: Path) -> Optional[float]:
    """ffprobe 读取媒体时长（秒），失败返回 None。"""
    try:
        r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                 timeout=30)
        if r.returncode != 0:
            return None
        return float(r.stdout.decode("utf-8", "replace").strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _probe_size(path: Path) -> tuple[int, int]:
    """ffprobe 读取首个视频流的 (宽, 高)，失败返回 (0, 0)。"""
    try:
        r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                  "-show_entries", "stream=width,height", "-of", "csv=p=0",
                  str(path)], timeout=30)
        if r.returncode != 0:
            return (0, 0)
        w, h = r.stdout.decode("utf-8", "replace").strip().split(",")[:2]
        return (int(w), int(h))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return (0, 0)


# ----------------------------------------------------------------------
# 渲染器
# ----------------------------------------------------------------------
class BulletTimeRenderer:
    """子弹时间渲染器。

    用法::

        renderer = BulletTimeRenderer()          # 读全局 config
        out = renderer.auto_render(center_ts, hid, highlights_dir)
    """

    def __init__(self, cfg: Config | None = None):
        self._cfg = cfg or config

    # ------------------------------------------------------------------
    # 帧提取
    # ------------------------------------------------------------------
    def extract_frame(self, video: str | Path, ts_in_seg: float,
                      out_jpg: str | Path) -> bool:
        """从视频中按段内相对时间抽一帧（ffmpeg -ss 快速 seek，-q:v 2）。

        容差：``ts_in_seg`` 超出视频时长时取首帧（<0）或尾帧（>duration）。
        成功返回 True。
        """
        video, out_jpg = Path(video), Path(out_jpg)
        try:
            if not video.is_file():
                return False
            dur = _probe_duration(video)
            if not dur or dur <= 0:
                logger.warning("无法探测视频时长：%s", video)
                return False
            ts = float(ts_in_seg)
            if ts < 0.0:
                ts = 0.0
            elif ts > dur:
                ts = max(0.0, dur - 0.05)
            out_jpg.parent.mkdir(parents=True, exist_ok=True)
            r = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                      "-ss", f"{ts:.3f}", "-i", str(video),
                      "-frames:v", "1", "-q:v", "2", str(out_jpg)])
            return (r.returncode == 0 and out_jpg.is_file()
                    and out_jpg.stat().st_size > 0)
        except Exception as e:
            logger.warning("extract_frame 失败 %s：%s", video, e)
            return False

    # ------------------------------------------------------------------
    # 虚拟机位合成
    # ------------------------------------------------------------------
    def synthesize_angles(self, frame_jpg: str | Path, n: int,
                          workdir: str | Path) -> list[Path]:
        """单机位虚拟机位：对帧做 n 个数字变焦+平移裁剪。

        - 变焦从 1.0 渐进到 1.8，同时中心从左到右平移（幅度随可裁剪
          空间增大），模拟环绕机位；
        - 每个角度用 ffmpeg crop+scale 统一到 1280x720；
        - 返回成功生成的角度图列表（最多 n 个；源帧不可用时为空）。
        """
        frame, workdir = Path(frame_jpg), Path(workdir)
        out: list[Path] = []
        try:
            n = int(n)
            if n <= 0 or not frame.is_file():
                return out
            workdir.mkdir(parents=True, exist_ok=True)
            w, h = _probe_size(frame)
            if w <= 0 or h <= 0:
                logger.warning("无法探测帧尺寸：%s", frame)
                return out
            for i in range(n):
                t = i / (n - 1) if n > 1 else 0.0
                zoom = 1.0 + 0.8 * t                      # 1.0 → 1.8
                cw = max(2, int(round(w / zoom)))
                ch = max(2, int(round(h / zoom)))
                # 中心偏移：横向全幅 80% 往返、纵向 40%，且被裁剪框钳制
                ox = (t - 0.5) * 2.0 * 0.8 * (w - cw) / 2.0
                oy = (t - 0.5) * 2.0 * 0.4 * (h - ch) / 2.0
                cx = int(round(w / 2.0 + ox))
                cy = int(round(h / 2.0 + oy))
                cx = max(0, min(w - cw, cx))
                cy = max(0, min(h - ch, cy))
                dst = workdir / f"vangle_{i:02d}.jpg"
                vf = (f"crop={cw}:{ch}:{cx}:{cy},"
                      f"scale={_OUT_W}:{_OUT_H}:flags=lanczos")
                r = _run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                          "-y", "-i", str(frame), "-vf", vf,
                          "-frames:v", "1", "-q:v", "2", str(dst)])
                if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
                    out.append(dst)
                else:
                    logger.warning("虚拟角度 %d 生成失败：%s", i,
                                   r.stderr.decode("utf-8", "replace")[-200:])
            return out
        except Exception as e:
            logger.warning("synthesize_angles 失败：%s", e)
            return out

    # ------------------------------------------------------------------
    # 主渲染
    # ------------------------------------------------------------------
    def render_for_moment(self, center_ts: float, duration: float,
                          segments: Sequence[tuple[str, Any, float]],
                          out_mp4: str | Path) -> Optional[Path]:
        """把覆盖 center_ts 的各机位片段渲染成一段子弹时间 mp4。

        - ``segments``：find_segments 的返回值 [(机位名, 路径, 段起始epoch)]；
        - 每机位取一帧（相对时间 = center_ts - 段起始）；
          真机位帧 >= 2 且 virtual_mode 允许 → 真机位环绕；
          仅 1 帧且 virtual_mode in (auto, virtual) → 虚拟机位合成；
          0 帧（或 real 模式下不足 2 帧）→ 返回 None；
        - 成片：xfade 交叉淡化拼接，时长≈duration，无音轨，720p 上限；
        - 任何失败返回 None，不抛异常。
        """
        try:
            out_mp4 = Path(out_mp4)
            duration = float(duration)
            if duration <= 0 or not segments:
                return None
            bt = {
                "virtual_mode": str(self._cfg.get(
                    "bullet_time.virtual_mode", "auto")).lower(),
                "virtual_angles": int(self._cfg.get(
                    "bullet_time.virtual_angles", 8) or 8),
                "style": str(self._cfg.get(
                    "bullet_time.style", "pingpong")).lower(),
                "zoom_motion": bool(self._cfg.get(
                    "bullet_time.zoom_motion", True)),
            }
            out_mp4.parent.mkdir(parents=True, exist_ok=True)

            with tempfile.TemporaryDirectory(prefix="vivideye_bt_") as tmp_s:
                tmp = Path(tmp_s)

                # 1) 每机位选最佳段（center 落在其覆盖内）并抽帧
                best: dict[str, tuple[float, Path]] = {}
                for item in segments:
                    cam, path, seg_start = str(item[0]), Path(item[1]), float(item[2])
                    ts = center_ts - seg_start
                    cur = best.get(cam)
                    # 偏好 ts>=0 中最小者（center 之后最近开头的段）；
                    # 全为负（center 早于所有段）时取最晚的段
                    if (cur is None
                            or (ts >= 0 and (cur[0] < 0 or ts < cur[0]))
                            or (ts < 0 and cur[0] < 0 and ts > cur[0])):
                        best[cam] = (ts, path)
                frames: list[Path] = []
                for i, (cam, (ts, path)) in enumerate(sorted(best.items())):
                    jpg = tmp / f"real_{i:02d}.jpg"
                    if self.extract_frame(path, ts, jpg):
                        frames.append(jpg)
                    else:
                        logger.warning("机位 [%s] 抽帧失败：%s", cam, path)

                # 2) 真机位 / 虚拟机位决策
                k = len(frames)
                mode = bt["virtual_mode"]
                if k == 0:
                    return None
                if (k == 1 and mode in ("auto", "virtual")) or (
                        k >= 2 and mode == "virtual"):
                    src = frames[0]
                    frames = self.synthesize_angles(
                        src, bt["virtual_angles"], tmp / "virtual")
                    if len(frames) < 2:
                        logger.warning("虚拟机位合成不足 2 个角度（%d 个）",
                                       len(frames))
                        return None
                elif k < 2:
                    # k==1 且 mode == "real"：单真帧无法环绕
                    logger.info("virtual_mode=real 且真机位帧不足 2，跳过渲染")
                    return None

                # 3) 角度序列风格
                seq = list(frames)
                if bt["style"] == "pingpong" and len(frames) >= 2:
                    seq = frames + frames[-2:0:-1]   # + reversed(frames[1:-1])
                # rotate：顺序环绕一遍

                # 4) 每帧一个短片段（静态 loop 或 zoompan 缓推）
                m = len(seq)
                fade = min(0.5, max(0.08, duration / (m * 4.0)))
                per = (duration + (m - 1) * fade) / m
                clips: list[Path] = []
                for i, jpg in enumerate(seq):
                    clip = tmp / f"clip_{i:03d}.mp4"
                    if self._make_clip(jpg, per, clip, bt["zoom_motion"]):
                        clips.append(clip)
                if not clips:
                    return None

                # 5) 拼接（xfade 链 / 单片段直接封装）
                if len(clips) == 1:
                    if m == 1:
                        # 单角度：clip 本身已是 duration 长，补 faststart 封装
                        r = _run(["ffmpeg", "-hide_banner", "-loglevel",
                                  "error", "-y", "-i", str(clips[0]),
                                  "-c:v", "copy", "-movflags", "+faststart",
                                  "-an", str(out_mp4)])
                        if r.returncode == 0 and out_mp4.is_file():
                            return out_mp4
                        return None
                    # m>1 但只有一个 clip 生成成功：退化为单角度
                    return self._remake_single(clips[0], duration, out_mp4)
                if not self._xfade_join(clips, fade, out_mp4):
                    return None
                if out_mp4.is_file() and out_mp4.stat().st_size > 0:
                    return out_mp4
            return None
        except Exception as e:
            logger.warning("render_for_moment 失败：%s", e)
            return None

    # ------------------------------------------------------------------
    # 自动入口
    # ------------------------------------------------------------------
    def auto_render(self, center_ts: float, hid: str,
                    highlights_dir: str | Path) -> Optional[Path]:
        """定位原始片段并渲染子弹时间，输出 highlights_dir/bullet_<hid>.mp4。

        - 机位名来自 capture.cameras（空则 ["main"]），raw 目录来自
          storage.raw_dir（paths.resolve_path 锚定）；
        - bullet_time.enabled=false 或找不到覆盖片段时返回 None。
        """
        try:
            if not bool(self._cfg.get("bullet_time.enabled", True)):
                logger.info("bullet_time.enabled=false，跳过子弹时间渲染")
                return None
            cams = list(self._cfg.get("capture.cameras") or [])
            names = [str(c.get("name") or f"cam{i + 1}")
                     for i, c in enumerate(cams)] or ["main"]
            raw_root = resolve_path(
                self._cfg.get("storage.raw_dir", "data/raw"), self._cfg)
            duration = float(self._cfg.get("bullet_time.duration_seconds", 4) or 4)
            window = max(8.0, duration * 2.0)
            segments = find_segments(center_ts, window, raw_root, names)
            if not segments:
                logger.warning("data/raw 中没有覆盖 %.1f（%s 机位）的原始片段，"
                               "可能已被保留期清理", center_ts, ",".join(names))
                return None
            out = Path(highlights_dir) / f"bullet_{hid}.mp4"
            result = self.render_for_moment(center_ts, duration, segments, out)
            if result is not None:
                logger.info("子弹时间已渲染：%s（%d 机位段参与）",
                            result, len(segments))
            return result
        except Exception as e:
            logger.warning("auto_render 失败：%s", e)
            return None

    # ------------------------------------------------------------------
    # 内部：片段生成与拼接
    # ------------------------------------------------------------------
    def _make_clip(self, jpg: Path, seconds: float, out: Path,
                   zoom_motion: bool) -> bool:
        """把单帧生成一个 seconds 秒的短片段（统一 1280x720 / 25fps）。

        - zoom_motion=True：zoompan 从 1.0 缓推到 ~1.15（先 cover 统一
          尺寸再 2x 放大，避免 zoompan 抖动）；
        - zoom_motion=False：静态帧 loop。
        """
        try:
            frames = max(1, int(round(seconds * _FPS)))
            cover = (f"scale={_OUT_W}:{_OUT_H}:force_original_aspect_ratio=increase,"
                     f"crop={_OUT_W}:{_OUT_H}")
            if zoom_motion:
                step = 0.15 / frames
                vf = (f"{cover},scale=2560:1440,"
                      f"zoompan=z='min(1+{step:.6f}*on,1.15)'"
                      f":d={frames}"
                      ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                      f":s={_OUT_W}x{_OUT_H}:fps={_FPS},format=yuv420p")
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                       "-i", str(jpg), "-vf", vf,
                       "-frames:v", str(frames),
                       "-c:v", "libx264", "-preset", "veryfast", str(out)]
            else:
                vf = cover + ",format=yuv420p"
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                       "-loop", "1", "-i", str(jpg),
                       "-t", f"{seconds:.3f}", "-vf", vf,
                       "-r", str(_FPS),
                       "-c:v", "libx264", "-preset", "veryfast", str(out)]
            r = _run(cmd)
            ok = r.returncode == 0 and out.is_file() and out.stat().st_size > 0
            if not ok:
                logger.warning("短片段生成失败 %s：%s", jpg,
                               r.stderr.decode("utf-8", "replace")[-200:])
            return ok
        except Exception as e:
            logger.warning("_make_clip 失败：%s", e)
            return False

    def _remake_single(self, clip: Path, duration: float,
                       out: Path) -> Optional[Path]:
        """把已生成的单片段重编码为恰好 duration 秒（降级路径）。"""
        try:
            r = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                      "-stream_loop", "-1", "-i", str(clip),
                      "-t", f"{duration:.3f}",
                      "-c:v", "libx264", "-preset", "veryfast",
                      "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                      "-an", str(out)])
            if r.returncode == 0 and out.is_file() and out.stat().st_size > 0:
                return out
            return None
        except Exception as e:
            logger.warning("_remake_single 失败：%s", e)
            return None

    def _xfade_join(self, clips: list[Path], fade: float,
                    out: Path) -> bool:
        """ffmpeg xfade 滤镜链交叉淡化拼接（fade 秒转场，无音轨）。"""
        try:
            n = len(clips)
            if n < 2:
                return False
            inputs: list[str] = []
            for c in clips:
                inputs += ["-i", str(c)]
            durs = [_probe_duration(c) or 0.0 for c in clips]
            parts: list[str] = []
            prev = "[0:v]"
            offset = 0.0
            for i in range(1, n):
                offset += max(0.0, durs[i - 1] - fade)
                lbl = f"[v{i}]" if i < n - 1 else "[vout]"
                parts.append(
                    f"{prev}[{i}:v]xfade=transition=fade:"
                    f"duration={fade:.3f}:offset={offset:.3f}{lbl}")
                prev = f"[v{i}]"
            cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
                   + inputs
                   + ["-filter_complex", ";".join(parts),
                      "-map", "[vout]",
                      "-c:v", "libx264", "-preset", "veryfast",
                      "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                      "-an", str(out)])
            r = _run(cmd)
            ok = r.returncode == 0 and out.is_file() and out.stat().st_size > 0
            if not ok:
                logger.warning("xfade 拼接失败：%s",
                               r.stderr.decode("utf-8", "replace")[-300:])
            return ok
        except Exception as e:
            logger.warning("_xfade_join 失败：%s", e)
            return False
