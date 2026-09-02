"""VividEye Web 服务启动入口（uvicorn）。

用法（手机 Termux 内，项目根目录下执行）：
    python run.py                 # 前台运行
    nohup python run.py &         # 后台常驻
启动后局域网设备浏览器访问 http://<手机IP>:8666
"""

from __future__ import annotations

import logging

import uvicorn

from vivideye.config import config


def main() -> None:
    """按 user_config.yaml 中 server.host / server.port 启动 Web 服务。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    host: str = config.get("server.host", "0.0.0.0")
    port: int = int(config.get("server.port", 8666))
    uvicorn.run(
        "vivideye.server.app:create_app",  # 工厂模式，支持 --reload 场景
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
