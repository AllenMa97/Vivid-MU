"""管线后台调度器：定时触发 PipelineService.process_now()。

一个轻量的可中断定时线程：
- 每 ``pipeline.run_interval_minutes`` 分钟执行一次批次处理；
- 默认启动时立即先跑一轮（清理上次运行留下的积压片段）；
- ``stop()`` 通过 Event 唤醒线程，正在执行的批次会被完整跑完
  （单批最多 max_segments_per_run 段，手机上很快结束）。

生命周期与录制器（Recorder）统一由 main.py 的 start 子命令管理。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _ProcessNowCallable(Protocol):
    """任何带 process_now() 方法的对象（PipelineService）。"""

    def process_now(self) -> dict: ...


class PipelineScheduler:
    """以固定间隔调用 ``service.process_now()`` 的后台线程。"""

    def __init__(self, service: _ProcessNowCallable,
                 interval_minutes: float = 30.0,
                 run_immediately: bool = True):
        self._service = service
        self._interval = max(60.0, float(interval_minutes or 30.0) * 60.0)
        self._run_immediately = run_immediately
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def interval_minutes(self) -> float:
        return self._interval / 60.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """启动调度线程（幂等）。"""
        if self.is_running:
            logger.warning("管线调度器已在运行，忽略重复 start()")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="vivideye-pipeline-scheduler", daemon=True)
        self._thread.start()
        logger.info("管线调度器已启动：每 %.0f 分钟处理一批", self.interval_minutes)

    def stop(self, timeout: float = 20.0) -> None:
        """请求停止并等待线程退出（正在跑的批次会被跑完）。"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        logger.info("管线调度器已停止")

    # ------------------------------------------------------------------
    def _run(self) -> None:
        if self._run_immediately:
            self._tick()
        # Event.wait(interval) 返回 True 表示被 stop() 唤醒
        while not self._stop_event.wait(self._interval):
            self._tick()

    def _tick(self) -> None:
        """执行一次批次；所有异常都在这里兜底，保证线程存活。"""
        try:
            result: Any = self._service.process_now()
            logger.info("管线批次结束：%s", result)
        except Exception:
            logger.exception("管线批次执行异常（调度继续）")
