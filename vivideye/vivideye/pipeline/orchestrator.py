"""管线编排器：原始片段 → 采样 → 云端 AI 分析 → 高光剪辑 → 入库。

``PipelineService`` 是手机端管线的核心：
1. 从数据库取 pending 片段（单次最多 ``pipeline.max_segments_per_run`` 段）；
2. 每段调用 sampler 抽帧（base64 JPEG）+ 提取 16k mono 音频；
3. 调用云端 AI 客户端 ``vivideye.ai.client.AIClient.analyze_frames()``
   得到 ``{score, title, caption, tags, subjects, moments}``；
4. score >= ``pipeline.min_highlight_score`` 时，用 ffmpeg 按 moments
   给出的起止秒剪辑高光片段（stream copy，MJPEG 全关键帧所以切割精确）
   并生成缩略图，写入 highlights 目录；
5. ``add_highlight`` 入库，片段标记 done；任何一步失败标记 failed。

健壮性约定：
- AI 模块由并行开发，采用**延迟导入**：只在真正调用时 import，
  模块缺失/异常只影响当前片段（标 failed），绝不拖垮整条管线；
- 进程内用线程锁、跨进程用 flock 文件锁防止重复处理同一批片段。
"""

from __future__ import annotations

import fcntl
import logging
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from vivideye.config import Config, config
from vivideye.paths import resolve_path
from vivideye.storage.db import HighlightsDB
from vivideye.pipeline.sampler import extract_audio, sample_frames

logger = logging.getLogger(__name__)

# 单段最多导出的高光数（防御 AI 返回超长 moments 列表拖垮手机）
MAX_MOMENTS_PER_SEGMENT = 5
# AI 未给出 moments 时的兜底高光时长（秒）
FALLBACK_HIGHLIGHT_SECONDS = 60


class PipelineError(RuntimeError):
    """管线处理失败。"""


# ----------------------------------------------------------------------
# 结果规范化工具
# ----------------------------------------------------------------------
def _to_num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_str_list(v: Any) -> list[str]:
    """把 tags/subjects 字段宽容地规范成字符串列表。"""
    if v is None:
        return []
    if isinstance(v, str):
        v = v.strip()
        return [v] if v else []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v)]


def normalize_moments(raw: Any) -> list[tuple[float, float]]:
    """把 AI 返回的 moments 宽容地规范成 [(start, end), ...] 列表。

    兼容格式：
    - ``[{"start": 3, "end": 8}, ...]``（start_sec/end_sec/from/to 同理）
    - ``[[3, 8], ...]`` / ``[(3, 8), ...]``
    - 单个 dict 时视为一个 moment。
    """
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[tuple[float, float]] = []
    for item in raw:
        s = e = None
        if isinstance(item, dict):
            s = next((_to_num(item[k]) for k in
                      ("start", "start_sec", "start_seconds", "begin", "from")
                      if _to_num(item.get(k)) is not None), None)
            e = next((_to_num(item[k]) for k in
                      ("end", "end_sec", "end_seconds", "stop", "to")
                      if _to_num(item.get(k)) is not None), None)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            s, e = _to_num(item[0]), _to_num(item[1])
        if s is None or e is None or e <= s:
            continue
        out.append((max(0.0, s), e))
    return out


def _run_ffmpeg(args: list[str], what: str) -> None:
    """执行 ffmpeg，失败抛 PipelineError。"""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise PipelineError(
            f"{what} 失败（code={proc.returncode}）："
            f"{proc.stderr.decode('utf-8', 'replace').strip()[-300:]}")


def cut_clip(src: Path, start: float, end: float, out: Path) -> None:
    """按起止秒剪辑高光片段（stream copy，避免手机端重编码）。"""
    _run_ffmpeg([
        "-ss", f"{max(0.0, start):.3f}",
        "-i", str(src),
        "-t", f"{max(0.1, end - start):.3f}",
        "-c", "copy", "-avoid_negative_ts", "make_zero",
        str(out),
    ], f"剪辑高光 {out.name}")


def make_thumbnail(video: Path, out: Path, width: int = 480) -> None:
    """取高光片段首帧生成 JPEG 缩略图。"""
    _run_ffmpeg([
        "-i", str(video),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "4",
        str(out),
    ], f"生成缩略图 {out.name}")


# ----------------------------------------------------------------------
# 编排服务
# ----------------------------------------------------------------------
class PipelineService:
    """管线编排服务：start/stop 管理调度线程，process_now 执行一批处理。"""

    def __init__(self, db: Optional[HighlightsDB] = None, cfg: Config | None = None):
        self._cfg = cfg or config
        self._db = db if db is not None else HighlightsDB(
            resolve_path(self._cfg.get("storage.db_path", "data/vivideye.db"), self._cfg))
        # 跨进程文件锁（防止 scheduler 进程与手动 process-now 命令撞车）
        self._lock_path = resolve_path(
            self._cfg.get("storage.db_path", "data/vivideye.db"), self._cfg
        ).parent / "pipeline.lock"
        self._thread_lock = threading.Lock()
        self._scheduler: Any = None   # PipelineScheduler，延迟导入避免循环依赖
        # 运行统计
        self._last_run_at: Optional[float] = None
        self._last_result: dict[str, Any] = {}
        self._processed_total = 0
        self._highlights_total = 0
        # AI 客户端缓存（延迟导入；加载失败不缓存，下次处理时重试）
        self._ai_client: Any = None

    @property
    def db(self) -> HighlightsDB:
        return self._db

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动管线（内部创建并启动调度线程，幂等）。"""
        if self._scheduler is None:
            from vivideye.pipeline.scheduler import PipelineScheduler
            self._scheduler = PipelineScheduler(
                self,
                interval_minutes=self._cfg.get("pipeline.run_interval_minutes", 30))
        self._scheduler.start()

    def stop(self) -> None:
        """停止管线调度。"""
        if self._scheduler is not None:
            self._scheduler.stop()

    def status(self) -> dict[str, Any]:
        """返回管线运行状态快照。"""
        return {
            "scheduler_running": (
                self._scheduler.is_running if self._scheduler is not None else False),
            "last_run_at": self._last_run_at,
            "last_result": self._last_result,
            "processed_total": self._processed_total,
            "highlights_total": self._highlights_total,
            "db_stats": self._db.stats(),
        }

    # ------------------------------------------------------------------
    # 核心流程
    # ------------------------------------------------------------------
    def process_now(self) -> dict[str, Any]:
        """立即处理一批 pending 片段。

        同进程 / 跨进程均有互斥保护；正在处理时返回 skipped。
        返回 ``{"processed": n, "highlights": m, "failed": k}``。
        """
        if not self._thread_lock.acquire(blocking=False):
            return {"skipped": True, "reason": "本进程已有批次在处理中"}
        lock_file = None
        try:
            lock_file = open(self._lock_path, "w")
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return {"skipped": True, "reason": "另一进程正在处理管线"}
            return self._process_batch()
        finally:
            if lock_file is not None:
                lock_file.close()
            self._thread_lock.release()

    def _process_batch(self) -> dict[str, Any]:
        limit = int(self._cfg.get("pipeline.max_segments_per_run", 8))
        threshold = float(self._cfg.get("pipeline.min_highlight_score", 0.55))
        scene_mode = str(self._cfg.get("pipeline.scene_mode", "auto"))

        segments = self._db.pending_segments(limit=limit)
        result: dict[str, Any] = {
            "processed": 0, "highlights": 0, "failed": 0, "segments": [],
        }
        self._last_run_at = time.time()
        if not segments:
            self._last_result = result
            logger.info("没有待处理的片段，本轮空转")
            return result

        logger.info("本轮处理 %d 个片段（阈值 %.2f，场景 %s）",
                    len(segments), threshold, scene_mode)
        for seg in segments:
            path = seg.get("path", "")
            try:
                made = self._process_segment(seg, threshold, scene_mode)
                result["processed"] += 1
                result["highlights"] += made
                result["segments"].append({"path": path, "ok": True,
                                           "highlights": made})
                self._processed_total += 1
                self._highlights_total += made
            except Exception as e:
                # 任何单段失败（含 AI 模块缺失/超时/返回异常）都标记 failed，
                # 绝不让管线整体崩溃
                logger.exception("片段处理失败：%s", path)
                self._db.mark_segment(path, "failed")
                result["processed"] += 1
                result["failed"] += 1
                result["segments"].append({"path": path, "ok": False,
                                           "error": str(e)})
                self._processed_total += 1
        self._last_result = result
        logger.info("本轮完成：处理 %d 段，产出高光 %d 个，失败 %d 段",
                    result["processed"], result["highlights"], result["failed"])
        return result

    # ------------------------------------------------------------------
    # 单段处理
    # ------------------------------------------------------------------
    def _process_segment(self, seg: dict, threshold: float,
                         scene_mode: str) -> int:
        """处理单个片段，返回生成的高光数量。失败抛异常（由调用方标 failed）。"""
        path = Path(seg["path"])
        if not path.is_file():
            raise PipelineError(f"片段文件不存在：{path}")
        self._db.mark_segment(path, "processing")
        logger.info("开始处理片段：%s", path.name)

        with tempfile.TemporaryDirectory(prefix="vivideye_seg_") as tmp:
            # 1) 采样：抽帧 + 音频
            frames = sample_frames(
                path, fps=float(self._cfg.get("pipeline.sample_fps", 0.5)))
            audio_path = extract_audio(path, Path(tmp) / "audio.wav")

            # 2) 云端 AI 分析（延迟导入：模块由并行开发，缺失时运行期才报错）
            client = self._get_ai_client()
            raw = client.analyze_frames(
                frames,
                str(audio_path) if audio_path is not None else None,
                scene_mode,
            )
            info = self._normalize_result(raw)

            # 3) 达标则剪辑高光并入库
            made = 0
            if info["score"] >= threshold:
                made = self._export_highlights(path, seg, info)
            else:
                logger.info("片段 %s 得分 %.2f 低于阈值 %.2f，跳过高光导出",
                            path.name, info["score"], threshold)

        # 4) 标记完成
        self._db.mark_segment(path, "done")
        logger.info("片段 %s 处理完成：score=%.2f，高光 %d 个",
                    path.name, info["score"], made)
        return made

    def _get_ai_client(self):
        """延迟加载并缓存 AI 客户端。

        ``vivideye.ai.client.AIClient`` 由 AI 专家并行开发，这里只在
        首次真正需要时 import——模块缺失抛 ImportError，由单段处理
        的异常兜底捕获，不会在 import 本模块时炸掉整个进程。
        """
        if self._ai_client is None:
            from vivideye.ai.client import AIClient  # 延迟导入（勿上移到模块顶层）
            self._ai_client = AIClient()
        return self._ai_client

    def _normalize_result(self, raw: Any) -> dict[str, Any]:
        """把 AI 返回值规范成内部结构，字段异常时给安全默认值。"""
        if not isinstance(raw, dict):
            raise PipelineError(f"AI 返回了意外类型：{type(raw).__name__}")
        score = _to_num(raw.get("score"))
        score = 0.0 if score is None else max(0.0, min(1.0, score))
        title = str(raw.get("title") or "").strip() or "未命名高光"
        caption = str(raw.get("caption") or "").strip()
        return {
            "score": score,
            "title": title,
            "caption": caption,
            "tags": _as_str_list(raw.get("tags")),
            "subjects": _as_str_list(raw.get("subjects")),
            "moments": normalize_moments(raw.get("moments")),
        }

    # ------------------------------------------------------------------
    # 高光导出
    # ------------------------------------------------------------------
    def _export_highlights(self, seg_path: Path, seg: dict,
                           info: dict[str, Any]) -> int:
        """按 moments 剪辑高光片段 + 缩略图并入库，返回导出数量。"""
        hl_dir = resolve_path(
            self._cfg.get("storage.highlights_dir", "data/highlights"), self._cfg)
        hl_dir.mkdir(parents=True, exist_ok=True)

        moments = info["moments"][:MAX_MOMENTS_PER_SEGMENT]
        if not moments:
            # AI 未给出具体时刻：兜底截取片段开头 60 秒
            dur = float(seg.get("duration") or 0)
            end = min(FALLBACK_HIGHLIGHT_SECONDS, dur) if dur > 0 \
                else FALLBACK_HIGHLIGHT_SECONDS
            if end <= 0:
                end = FALLBACK_HIGHLIGHT_SECONDS
            moments = [(0.0, end)]
            logger.info("AI 未给出 moments，兜底截取 %.0f-%.0f 秒", 0.0, end)

        base_time = datetime.fromtimestamp(
            float(seg.get("started_at") or time.time())).strftime("%Y%m%d_%H%M%S")
        seg_id = seg.get("id") or ""
        made = 0
        for idx, (start, end) in enumerate(moments, 1):
            suffix = f"_{idx}" if len(moments) > 1 else ""
            out_video = hl_dir / f"hl_{base_time}{suffix}.mp4"
            out_thumb = hl_dir / f"hl_{base_time}{suffix}.jpg"
            try:
                cut_clip(seg_path, start, end, out_video)
                make_thumbnail(out_video, out_thumb)
            except PipelineError:
                logger.exception("高光剪辑失败：%s %.1f-%.1fs", seg_path.name, start, end)
                continue
            self._db.add_highlight(
                video_path=str(out_video),
                segment_id=seg_id or None,
                thumb_path=str(out_thumb),
                score=info["score"],
                title=info["title"],
                caption=info["caption"],
                tags=info["tags"],
                subjects=info["subjects"],
                started_at=float(seg.get("started_at") or 0) + start,
                duration=round(end - start, 2),
            )
            made += 1
            logger.info("导出高光：%s（%.1f-%.1fs，score=%.2f）",
                        out_video.name, start, end, info["score"])
        return made
