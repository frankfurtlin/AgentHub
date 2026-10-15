# -*- coding: utf-8 -*-
"""按当前系统挑选平台后端。

上层只需要 `get_backend()`，不必关心自己跑在什么系统上。
"""

from __future__ import annotations

import sys

from .base import Backend, ProcessInfo, UiProbeSession, WindowInfo

__all__ = [
    "Backend",
    "ProcessInfo",
    "UiProbeSession",
    "WindowInfo",
    "current_platform",
    "get_backend",
]

#: 'win32' / 'darwin' / 'linux'
current_platform = sys.platform

_BACKEND: Backend | None = None


def get_backend(refresh: bool = False) -> Backend:
    """返回当前平台的后端实例（进程内缓存）。"""
    global _BACKEND
    if _BACKEND is not None and not refresh:
        return _BACKEND

    if sys.platform == "win32":
        from .windows import WindowsBackend
        _BACKEND = WindowsBackend()
    elif sys.platform == "darwin":
        from .macos import MacOSBackend
        _BACKEND = MacOSBackend()
    else:
        raise RuntimeError(
            "暂不支持 %s 平台。目前只有 Windows 后端可用，"
            "macOS 后端的待办清单见 agenthub/host/macos/__init__.py。" % sys.platform
        )
    return _BACKEND
