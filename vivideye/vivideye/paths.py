"""路径解析工具：把配置中的相对路径统一锚定到仓库根目录。

配置里的 storage.db_path / raw_dir / highlights_dir / digest_dir 等键
既支持绝对路径，也支持相对路径（相对仓库根目录，即 main.py 所在目录）。
"""

from __future__ import annotations

from pathlib import Path

from vivideye.config import Config, config


def resolve_path(path: str | Path, cfg: Config | None = None) -> Path:
    """把配置值解析为绝对路径。

    - 绝对路径：原样返回（展开 ~）；
    - 相对路径：拼接 ``cfg.repo_root``（默认全局 config 单例）。
    """
    cfg = cfg or config
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = cfg.repo_root / p
    return p
