"""VividEye Web 服务层：FastAPI 应用 + 静态前端（手机 Termux 上运行）。

公开接口：
    from vivideye.server import create_app
    app = create_app()
"""

from vivideye.server.app import create_app

__all__ = ["create_app"]
