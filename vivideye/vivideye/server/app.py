"""VividEye Web 服务：FastAPI 应用工厂与全部 HTTP API。

部署形态：手机 Termux 内运行，局域网设备浏览器访问
    http://<手机IP>:8666

职责（只读消费数据层，不直接写库——写库方为 pipeline）：
    - 高光墙 / 实时画面 / 日报 / 设置 四个界面所需的全部 API
    - 反向代理手机摄像头的 MJPEG 实时画面（capture.source_url）
    - 读写 user_config.yaml（敏感字段打码展示）
    - 触发 pipeline 立即处理（模块未就绪时返回 503）
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vivideye.config import REPO_ROOT, config, load_config
from vivideye.storage.db import HighlightsDB

logger = logging.getLogger("vivideye.server")

# 静态前端目录（与本文件同级的 static/）
STATIC_DIR = Path(__file__).resolve().parent / "static"

# 配置中需要打码展示的敏感字段名
_SENSITIVE_KEYS = {"api_key"}
# 打码占位符：前端原样提交该值时服务端直接忽略（不覆盖真实密钥）
_MASK = "******"

# /api/config 允许写入的顶层配置段（白名单，防止脏数据入库）。
# 收紧到键级（S3）：storage.* 与 ai.api_base / ai.provider 等敏感字段一律
# 只读——GET 仍可展示，POST 一律忽略并在响应里提示；ai 段仅 api_key 可写。
# bullet_time 段同理仅 enabled / min_score 可写（设置页的两个控件），
# style / virtual_mode 等高级项请直接编辑 user_config.yaml。
_ALLOWED_SECTIONS = {"app", "capture", "pipeline", "server"}
_AI_WRITABLE_KEYS = {"api_key"}
_BULLETTIME_WRITABLE_KEYS = {"enabled", "min_score"}

# 合法的场景模式
_SCENE_MODES = {"auto", "pet", "kid", "home"}


class FavoriteIn(BaseModel):
    """收藏请求体。"""
    favorite: bool

# AI 日报生成的最长等待时间（超时则降级为本地模板，避免请求挂死）
_DIGEST_TIMEOUT_SECONDS = 30.0


# ============================================================================
# 通用小工具
# ============================================================================

def _resolve_path(p: "str | Path") -> Path:
    """把配置里的相对路径解析到仓库根目录下（绝对路径则原样返回）。"""
    p = Path(p)
    return p if p.is_absolute() else REPO_ROOT / p


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并（override 优先），语义与 config._deep_merge 一致。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _mask_sensitive(node: Any) -> Any:
    """递归打码敏感字段：仅用于 API 展示，绝不把真实密钥发回浏览器。"""
    if isinstance(node, dict):
        return {
            k: (_MASK if k in _SENSITIVE_KEYS and v else _mask_sensitive(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_mask_sensitive(x) for x in node]
    return node


def _strip_masked(node: Any) -> Any:
    """剔除"打码占位/空值"的敏感字段：避免把掩码写回、覆盖真实密钥。"""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in _SENSITIVE_KEYS and (v is None or v == _MASK or v == ""):
                continue  # 用户没有修改密钥 → 保留原值
            out[k] = _strip_masked(v)
        return out
    if isinstance(node, list):
        return [_strip_masked(x) for x in node]
    return node


def _read_json(path: Path) -> dict:
    """读取 JSON 文件，失败返回空 dict（状态文件可能不存在或损坏）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _serialize_highlight(h: dict) -> dict:
    """高光记录的轻量整形：favorite 转成 bool，其余字段透传。"""
    out = dict(h)
    out["favorite"] = bool(out.get("favorite"))
    return out


# ============================================================================
# 状态探测：录制 / pipeline / 磁盘
# ============================================================================

def _recording_status(app: FastAPI) -> dict:
    """探测录制状态。

    优先级：
      1) 采集器心跳文件 data/recorder.json（capture 模块持续写入）
      2) 兜底启发式：raw 目录最近 1.5 个分片周期内有文件写入 → 录制中

    心跳命中时顺带透传 paused / last_file，供前端推断"未录制"的原因
    （磁盘水位暂停 / 摄像头无信号）。
    """
    now = time.time()
    hb = _read_json(app.state.data_dir / "recorder.json")
    if hb.get("updated_at") and now - float(hb["updated_at"]) < 300:
        return {
            "recording": bool(hb.get("recording")),
            "source": "heartbeat",
            "paused": bool(hb.get("paused")),
            "last_file": hb.get("last_file"),
        }

    seg = float(config.get("capture.segment_seconds", 600) or 600)
    newest = 0.0
    try:
        for p in app.state.raw_dir.iterdir():
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    age = round(now - newest, 1) if newest else None
    return {
        "recording": newest > 0 and (now - newest) < seg * 1.5,
        "source": "raw_dir",
        "last_write_age_sec": age,
    }


def _pipeline_available() -> bool:
    """pipeline 编排模块是否可导入（未部署时返回 False）。"""
    try:
        from vivideye.pipeline.orchestrator import PipelineService  # noqa: F401
        return True
    except Exception:
        return False


def _pipeline_status(app: FastAPI) -> dict:
    """pipeline 状态：可用性 / 是否运行中 / 最近一次运行信息。

    running 同时看进程内标志与 data/pipeline_state.json（后台线程写入），
    便于 /api/status 在触发线程存活期间乃至服务重启后都能如实展示。
    """
    info = _read_json(app.state.data_dir / "pipeline_state.json")
    last_run = info.get("last_run")
    return {
        "available": _pipeline_available(),
        "running": bool(getattr(app.state, "pipeline_running", False))
        or bool(info.get("running")),
        "last_run": last_run,
        "last_run_str": (
            datetime.fromtimestamp(float(last_run)).strftime("%m-%d %H:%M")
            if last_run else None
        ),
        "last_error": info.get("last_error"),
    }


def _write_pipeline_state(state_file: Path, payload: dict) -> None:
    """合并写入 pipeline 状态文件（保留未涉及的字段；失败只打日志）。"""
    merged = _read_json(state_file)
    merged.update(payload)
    try:
        state_file.write_text(json.dumps(merged, ensure_ascii=False),
                              encoding="utf-8")
    except OSError as e:
        logger.warning("pipeline 状态文件写入失败：%s", e)


def _run_pipeline_thread(app: FastAPI) -> None:
    """后台线程：调用 PipelineService.process_now 并维护状态文件。

    - 复用 app.state.db 传给 PipelineService（连接生命周期归 Web 服务管，
      此处绝不 close）；
    - 连 BaseException 也兜底（L6）：任何异常路径都要复位 pipeline_running
      并把 running=false 落盘，仅 KeyboardInterrupt 保持穿透语义。
    """
    state_file = app.state.data_dir / "pipeline_state.json"
    payload: dict = {"running": False, "last_run": time.time(),
                     "last_result": None, "last_error": None}
    try:
        _write_pipeline_state(state_file,
                              {"running": True, "last_run": time.time()})
        from vivideye.pipeline.orchestrator import PipelineService
        svc = PipelineService(db=app.state.db)
        result = svc.process_now()
        payload["last_result"] = str(result)
    except KeyboardInterrupt:
        raise
    except BaseException as e:  # noqa: BLE001 —— 任何失败都要复位状态并回报 UI
        logger.exception("立即处理执行失败")
        payload["last_error"] = str(e)
    finally:
        app.state.pipeline_running = False           # L6：保证复位
        _write_pipeline_state(state_file, payload)   # L5：running/last_run 落盘


# ============================================================================
# 日报：当日高光筛选 + AI 生成（带超时降级）
# ============================================================================

def _day_bounds(date_str: str) -> "tuple[float, float]":
    """某本地日期的 [起始, 结束) epoch 秒。"""
    start = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
    return start, start + 86400.0


def _highlights_of_day(db: HighlightsDB, date_str: str) -> "list[dict]":
    """取出某一天的高光（按 started_at 归类，缺失时退回 created_at）。"""
    lo, hi = _day_bounds(date_str)
    items = db.list_highlights(limit=2000)
    return [
        h for h in items
        if lo <= (h.get("started_at") or h.get("created_at") or 0) < hi
    ]


def _fmt_time(h: dict) -> str:
    """高光的展示时刻 HH:MM。"""
    ts = h.get("started_at") or h.get("created_at") or 0
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    return ""


def _build_stats(date_str: str, highlights: "list[dict]") -> dict:
    """当日统计（AI 版与降级模板共用；total/highlight_count 双键兼容缓存契约）。"""
    scores = [float(h.get("score") or 0) for h in highlights]
    top = sorted(highlights, key=lambda h: float(h.get("score") or 0), reverse=True)[:5]
    return {
        "date": date_str,
        "total": len(highlights),
        "highlight_count": len(highlights),
        "favorite": sum(1 for h in highlights if h.get("favorite")),
        "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "max_score": round(max(scores), 3) if scores else 0.0,
        "top": [
            {
                "title": h.get("title") or "",
                "score": h.get("score") or 0,
                "time": _fmt_time(h),
                "tags": h.get("tags") or [],
            }
            for h in top
        ],
    }


def _build_fallback_digest(date_str: str, highlights: "list[dict]") -> dict:
    """AI 不可用/超时的本地模板日报（结构与 ai.digest 保持一致）。"""
    stats = _build_stats(date_str, highlights)
    if highlights:
        lines = [
            f"# 🐾 萌眼日报 · {date_str}",
            "",
            f"今天共捕捉到 **{len(highlights)}** 个高光时刻，"
            f"最高分 **{stats['max_score']}**。",
            "",
            "## 🌟 今日 Top 时刻",
            "",
        ]
        for i, t in enumerate(stats["top"], 1):
            tags = "、".join(t["tags"][:3])
            lines.append(f"{i}. **{t['title'] or '—'}**（{t['score']} 分）"
                         f"{' ' + t['time'] if t['time'] else ''}"
                         f"{f' `{tags}`' if tags else ''}")
        lines += ["", "> AI 小故事暂时离线，稍后再来看看吧～", ""]
    else:
        lines = [
            f"# 🐾 萌眼日报 · {date_str}",
            "",
            "今天安安静静，摄像头没捕捉到特别的瞬间。",
            "",
            "> 毛孩子们大概在酝酿明天的精彩 😴",
            "",
        ]
    return {"markdown_text": "\n".join(lines), "stats": stats}


def _cache_digest(db: HighlightsDB | None, digest_dir: Path | None,
                  date_str: str, result: dict) -> None:
    """日报落盘 + 入库（INSERT OR REPLACE，可随新高光刷新；失败只打日志）。"""
    if db is None or digest_dir is None:
        return
    try:
        digest_dir.mkdir(parents=True, exist_ok=True)
        md_path = digest_dir / f"digest-{date_str}.md"
        md_path.write_text(result["markdown_text"], encoding="utf-8")
        db.save_digest(date_str, str(md_path), result["stats"])
    except Exception as e:  # noqa: BLE001 —— 缓存失败不影响返回
        logger.warning("日报缓存写入失败：%s", e)


def _generate_digest(date_str: str, highlights: "list[dict]",
                     db: HighlightsDB | None = None,
                     digest_dir: Path | None = None) -> dict:
    """生成日报：优先 AI 故事化正文，失败/超时降级本地模板。

    AI 调用跑在 daemon 工作线程里，主线程最多等 ``_DIGEST_TIMEOUT_SECONDS``
    （M4：不再为每次请求新建一个执行器线程）；超时立即降级返回，但线程
    继续跑完并把结果落缓存（写盘 + 入库），下次请求即可命中 AI 版。
    """
    try:
        from vivideye.ai.digest import generate_daily_digest
    except ModuleNotFoundError as e:  # AI 模块文件不存在（未部署）
        logger.warning("AI 日报模块未部署（%s），使用本地模板", e)
        result = _build_fallback_digest(date_str, highlights)
        _cache_digest(db, digest_dir, date_str, result)
        return result
    except Exception as e:  # 模块存在但导入期出错（依赖缺失等）
        logger.warning("AI 日报模块不可用（%s），使用本地模板", e)
        result = _build_fallback_digest(date_str, highlights)
        _cache_digest(db, digest_dir, date_str, result)
        return result

    fallback = _build_fallback_digest(date_str, highlights)
    box: dict = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            text = generate_daily_digest(
                highlights, str(config.get("app.language", "zh_CN")))
        except Exception as e:  # noqa: BLE001 —— AI 侧异常一律降级
            logger.warning("AI 日报生成失败：%s", e)
            text = ""
        result = ({"markdown_text": text, "stats": fallback["stats"]}
                  if text else fallback)
        box["result"] = result
        _cache_digest(db, digest_dir, date_str, result)  # 跑完即落缓存
        done.set()

    threading.Thread(target=_worker, daemon=True,
                     name="ve-digest").start()
    if done.wait(_DIGEST_TIMEOUT_SECONDS):
        return box.get("result") or fallback
    # 超时：立即降级返回但不落缓存——后台线程跑完后会写入 AI 版，
    # 避免本地模板覆盖它导致 AI 结果永远无法生效。
    logger.warning("AI 日报生成超时（%.0fs），降级本地模板 date=%s",
                   _DIGEST_TIMEOUT_SECONDS, date_str)
    return fallback


# ============================================================================
# 应用工厂
# ============================================================================

def create_app() -> FastAPI:
    """FastAPI 应用工厂：装配依赖（app.state）、路由与静态资源。"""

    # ---- 按配置解析各数据路径，并确保目录存在 ----
    db_path = _resolve_path(config.get("storage.db_path", "data/vivideye.db"))
    highlights_dir = _resolve_path(config.get("storage.highlights_dir", "data/highlights"))
    raw_dir = _resolve_path(config.get("storage.raw_dir", "data/raw"))
    digest_dir = _resolve_path(config.get("storage.digest_dir", "data/digests"))
    for d in (highlights_dir, raw_dir, digest_dir, db_path.parent):
        d.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info("VividEye Web 服务启动完成 http://%s:%s",
                    config.get("server.host"), config.get("server.port"))
        yield
        _app.state.db.close()
        logger.info("Web 服务已停止，数据库连接已关闭")

    app = FastAPI(
        title="VividEye",
        description="家庭高光相机 Web 服务（高光墙 / 实时画面 / 日报 / 设置）",
        version="0.2.0",
        lifespan=lifespan,
    )

    # ---- 依赖注入：数据库句柄与路径挂在 app.state 上 ----
    app.state.db = HighlightsDB(db_path)
    app.state.db_path = db_path
    app.state.highlights_dir = highlights_dir
    app.state.raw_dir = raw_dir
    app.state.digest_dir = digest_dir
    app.state.data_dir = db_path.parent
    app.state.pipeline_running = False

    # ---- 静态资源 & 首页 ----
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """首页（单页应用入口）。"""
        return FileResponse(STATIC_DIR / "index.html")

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @app.get("/api/status")
    def api_status(request: Request) -> dict:
        """顶部状态栏数据：录制状态 / pipeline / 数据库统计 / 磁盘剩余。"""
        db: HighlightsDB = request.app.state.db
        usage = shutil.disk_usage(str(request.app.state.data_dir))
        return {
            "recording": _recording_status(request.app),
            "pipeline": _pipeline_status(request.app),
            "db": db.stats(),
            "disk_free": usage.free,
            "disk_free_gb": round(usage.free / 1024 ** 3, 2),
            # 是否已配置 AI 密钥（仅布尔，不回传 key 本身）：
            # 前端空状态据此引导用户去设置页粘贴 API Key
            "has_api_key": bool(str(config.get("ai.api_key") or "").strip()),
            "server_time": datetime.now().isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------
    # 高光列表 / 收藏 / 删除 / 媒体文件
    # ------------------------------------------------------------------
    @app.get("/api/highlights")
    def api_highlights(
        request: Request,
        limit: int = Query(50, ge=1, le=200, description="单页条数"),
        offset: int = Query(0, ge=0, description="偏移量"),
        tag: str | None = Query(None, description="按标签筛选"),
        favorite: bool = Query(False, description="仅看收藏"),
    ) -> dict:
        """高光列表（按创建时间倒序分页）。"""
        db: HighlightsDB = request.app.state.db
        items = db.list_highlights(
            limit=limit, offset=offset,
            tag=(tag or None), favorite_only=favorite,
        )
        return {
            "items": [_serialize_highlight(h) for h in items],
            "count": len(items),
            "limit": limit,
            "offset": offset,
        }

    def _require_highlight(request: Request, hid: str) -> dict:
        """取高光记录，不存在时抛 404。"""
        h = request.app.state.db.get_highlight(hid)
        if not h:
            raise HTTPException(status_code=404, detail="高光不存在")
        return h

    @app.post("/api/highlights/{hid}/favorite")
    def api_set_favorite(hid: str, body: FavoriteIn, request: Request) -> dict:
        """收藏 / 取消收藏。"""
        _require_highlight(request, hid)
        request.app.state.db.set_favorite(hid, body.favorite)
        return _serialize_highlight(request.app.state.db.get_highlight(hid))

    @app.delete("/api/highlights/{hid}")
    def api_delete_highlight(hid: str, request: Request) -> dict:
        """删除高光：仅清理位于高光目录内的媒体文件（安全边界），再删记录。"""
        h = _require_highlight(request, hid)
        hl_root: Path = request.app.state.highlights_dir.resolve()
        for key in ("video_path", "thumb_path", "bullet_time_path"):
            raw = h.get(key)
            if not raw:
                continue
            try:
                fp = Path(raw).resolve()
                if fp.is_file() and hl_root in fp.parents:
                    fp.unlink()
            except OSError as e:
                logger.warning("删除文件失败 %s: %s", raw, e)
        request.app.state.db.delete_highlight(hid)
        return {"ok": True, "id": hid}

    @app.get("/api/highlights/{hid}/video")
    def api_highlight_video(hid: str, request: Request) -> FileResponse:
        """返回高光视频文件（支持 Range，便于拖动进度条）。"""
        h = _require_highlight(request, hid)
        path = Path(h["video_path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="视频文件不存在（可能已被清理）")
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.get("/api/highlights/{hid}/thumb")
    def api_highlight_thumb(hid: str, request: Request) -> FileResponse:
        """返回高光缩略图。"""
        h = _require_highlight(request, hid)
        raw = h.get("thumb_path")
        if not raw or not Path(raw).is_file():
            raise HTTPException(status_code=404, detail="缩略图不存在")
        return FileResponse(Path(raw), media_type="image/jpeg")

    @app.get("/api/highlights/{hid}/bullettime")
    def api_highlight_bullettime(hid: str, request: Request) -> FileResponse:
        """返回高光的"子弹时间"旋转视角短片（未合成过则 404）。

        ``bullet_time_path`` 由 pipeline 在高光入库后调用
        ``db.set_bullet_time`` 写入；高光列表接口经 SELECT * 自动透出，
        前端据其 truthy 判断是否展示 ⚡ 徽章与切换按钮。
        """
        h = _require_highlight(request, hid)
        raw = h.get("bullet_time_path")
        if not raw or not Path(raw).is_file():
            raise HTTPException(status_code=404, detail="子弹时间短片不存在（未合成或已被清理）")
        path = Path(raw)
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    # ------------------------------------------------------------------
    # 日报
    # ------------------------------------------------------------------
    @app.get("/api/digest")
    def api_digest(request: Request, date: str | None = None) -> dict:
        """某日日报：优先取缓存；高光数量有变化或无缓存时现场生成。"""
        db: HighlightsDB = request.app.state.db
        date_str = date or datetime.now().strftime("%Y-%m-%d")
        try:
            _day_bounds(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="date 参数格式应为 YYYY-MM-DD")

        day_items = _highlights_of_day(db, date_str)

        # 命中缓存：数量一致说明当天没有新增高光，直接返回
        rec = db.get_digest(date_str)
        if rec:
            stats = rec.get("stats") or {}
            if stats.get("total", 0) == len(day_items):
                md_path = rec.get("markdown_path")
                if md_path and Path(md_path).is_file():
                    md = Path(md_path).read_text(encoding="utf-8")
                    if md:
                        return {"date": date_str, "markdown": md,
                                "stats": stats, "cached": True}

        # 缓存落盘由 _generate_digest 内部（或其工作线程）负责
        result = _generate_digest(date_str, day_items,
                                  db, request.app.state.digest_dir)
        md_text, stats = result["markdown_text"], result["stats"]
        return {"date": date_str, "markdown": md_text, "stats": stats, "cached": False}

    # ------------------------------------------------------------------
    # 配置读写（敏感字段打码）
    # ------------------------------------------------------------------
    @app.get("/api/config")
    def api_config_get() -> dict:
        """读取当前生效配置（合并默认值/config.yaml/user_config.yaml/环境变量）。"""
        return _mask_sensitive(config.as_dict())

    @app.post("/api/config")
    def api_config_post(request: Request, payload: dict[str, Any]) -> dict:
        """更新 user_config.yaml（增量深合并），并热更新内存配置。

        键级白名单（S3）：storage.* 与 ai 段除 api_key 外的字段（如
        ai.api_base / ai.provider）一律只读——GET 仍可展示，POST 忽略并提示。
        """
        if not isinstance(payload, dict) or not payload:
            raise HTTPException(status_code=400, detail="请求体必须是非空 JSON 对象")

        # 键级白名单过滤：可写段整段放行；ai 段仅放行 api_key；其余只读
        updates: dict[str, Any] = {}
        ignored: list[str] = []
        for section, body in payload.items():
            if section in _ALLOWED_SECTIONS:
                updates[section] = body
            elif section == "ai":
                if isinstance(body, dict):
                    kept = {k: v for k, v in body.items()
                            if k in _AI_WRITABLE_KEYS}
                    ignored += [f"ai.{k}" for k in body
                                if k not in _AI_WRITABLE_KEYS]
                    if kept:
                        updates["ai"] = kept
                else:
                    ignored.append("ai")
            elif section == "bullet_time":
                if isinstance(body, dict):
                    kept = {k: v for k, v in body.items()
                            if k in _BULLETTIME_WRITABLE_KEYS}
                    ignored += [f"bullet_time.{k}" for k in body
                                if k not in _BULLETTIME_WRITABLE_KEYS]
                    if kept:
                        updates["bullet_time"] = kept
                else:
                    ignored.append("bullet_time")
            else:
                ignored.append(str(section))

        # 剔除打码占位（防止掩码覆盖真实密钥）
        updates = _strip_masked(updates)

        # 场景模式合法性校验（提前给出友好错误）
        scene = (updates.get("pipeline") or {}).get("scene_mode")
        if scene is not None and scene not in _SCENE_MODES:
            raise HTTPException(status_code=400,
                                detail=f"scene_mode 仅支持 {' / '.join(sorted(_SCENE_MODES))}")
        # 子弹时间阈值合法性校验（必须是 0~1 的数字）
        bt_score = (updates.get("bullet_time") or {}).get("min_score")
        if bt_score is not None:
            try:
                bt_val = float(bt_score)
            except (TypeError, ValueError):
                bt_val = -1.0
            if not (0.0 <= bt_val <= 1.0):
                raise HTTPException(status_code=400,
                                    detail="bullet_time.min_score 必须是 0~1 之间的数字")
        if not updates:
            message = "没有可更新的配置项"
            if ignored:
                message += f"（只读字段已忽略：{', '.join(ignored)}）"
            return {"ok": False, "message": message,
                    "config": _mask_sensitive(config.as_dict())}

        user_path = REPO_ROOT / "user_config.yaml"
        existing: dict = {}
        if user_path.is_file():
            try:
                existing = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                raise HTTPException(status_code=500,
                                    detail=f"user_config.yaml 解析失败：{e}")

        merged = _deep_merge(existing, updates)
        # 原子写（M10）：先写临时文件再 os.replace，避免读到写一半的配置
        tmp_path = user_path.with_name(user_path.name + ".tmp")
        try:
            tmp_path.write_text(
                yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            os.replace(tmp_path, user_path)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500,
                                detail="user_config.yaml 写入失败")

        # 热更新内存单例：一次性替换 _data 引用（原子操作，
        # 避免 clear+update 两步之间被其他线程读到半截配置）
        config._data = load_config().as_dict()

        logger.info("配置已更新，涉及的顶层段：%s", ", ".join(updates.keys()))
        message = "配置已保存"
        if ignored:
            message += f"（只读字段已忽略：{', '.join(ignored)}）"
        return {"ok": True, "message": message,
                "config": _mask_sensitive(config.as_dict())}

    # ------------------------------------------------------------------
    # 实时画面（反向代理摄像头 MJPEG 流）
    # ------------------------------------------------------------------
    @app.get("/api/live")
    async def api_live(request: Request):
        """把 capture.source_url 的 MJPEG 字节流原样转发给浏览器 <img>。"""
        if not config.get("server.live_stream_proxy", True):
            raise HTTPException(status_code=503, detail="实时画面代理已关闭（server.live_stream_proxy）")
        url = config.get("capture.source_url")
        if not url:
            raise HTTPException(status_code=503, detail="未配置摄像头源地址（capture.source_url）")

        timeout = httpx.Timeout(5.0, read=None)  # 连接 5s 超时；推流读取不设限
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        try:
            req = client.build_request("GET", url)
            resp = await client.send(req, stream=True)
        except Exception as e:
            await client.aclose()
            raise HTTPException(status_code=503,
                                detail=f"无法连接摄像头画面源：{e}")

        media_type = resp.headers.get("content-type",
                                      "multipart/x-mixed-replace; boundary=frame")

        async def _relay():
            """逐块转发字节流；断开时释放上游连接。"""
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            except Exception:  # 客户端断开 / 上游中断
                pass
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(_relay(), media_type=media_type,
                                 status_code=resp.status_code)

    # ------------------------------------------------------------------
    # 立即处理（pipeline 编排）
    # ------------------------------------------------------------------
    @app.post("/api/pipeline/run")
    def api_pipeline_run(request: Request) -> dict:
        """触发 pipeline 立即处理；模块未部署时返回 503。"""
        if not _pipeline_available():
            raise HTTPException(status_code=503,
                                detail="pipeline 模块尚未部署或不可导入")
        if request.app.state.pipeline_running:
            return {"started": False, "message": "处理任务已在进行中，请稍候"}
        request.app.state.pipeline_running = True
        threading.Thread(
            target=_run_pipeline_thread, args=(request.app,),
            daemon=True, name="ve-pipeline-run",
        ).start()
        return {"started": True, "message": "已触发立即处理，稍后刷新高光墙看看吧"}

    return app
