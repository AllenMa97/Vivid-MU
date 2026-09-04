"""VividEye 子弹时间引擎：高光时刻的多机位环绕回放 / 虚拟机位合成。

对外主入口：``BulletTimeRenderer``::

    from vivideye.bullettime import BulletTimeRenderer

    out = BulletTimeRenderer().auto_render(center_ts, hid, highlights_dir)
"""

from vivideye.bullettime.renderer import (
    BulletTimeRenderer,
    find_segments,
    parse_segment_start,
)

__all__ = ["BulletTimeRenderer", "find_segments", "parse_segment_start"]
