# -*- coding: utf-8 -*-
"""Windows 平台后端。

把四块能力接起来：

    winapi.py   进程 / 窗口枚举、窗口激活（纯 ctypes）
    uia.py      界面探针——读窗口里有没有摆着审批条（纯 ctypes COM）
    icons.py    从可执行文件里抠出应用图标
    notify.py   读通知中心的 SQLite 库

进程枚举和 CPU 采样**不在这里**——psutil 已经跨平台做好了，见 host/base.py。
"""

from __future__ import annotations

import os
import subprocess
import time

from ..base import Backend, UiProbeSession, WindowInfo
from . import icons, uia, winapi
from .notify import WinNotificationSource


def _native_handle(native) -> int:
    """从 pywebview 的 WinForms 窗口对象里取出窗口句柄。

    `native.Handle` 是 .NET 的 `IntPtr`——pythonnet 不会把它自动转成 Python
    的 int，直接 `int()` 会抛 TypeError。得走 `ToInt64()`。
    """
    try:
        handle = native.Handle
    except Exception:
        return 0
    for name in ("ToInt64", "ToInt32"):
        try:
            return int(getattr(handle, name)())
        except Exception:
            continue
    try:
        return int(handle)
    except Exception:
        return 0


class _UiaSession(UiProbeSession):
    """一个可复用的 UIA 探针会话。

    UIA 的 COM 对象绑定在创建它的线程上，所以生命周期必须和探针线程一致，
    不能挂在 Backend 上长期持有。

    单个会话内复用同一个 IUIAutomation 实例，按钮条件的缓存也就跟着复用了，
    一次查询约 80ms；不开会话、每次新建的话每个句柄要多花几十毫秒。
    """

    def __init__(self) -> None:
        uia.com_ready()                  # COM 按线程初始化
        self._auto = uia.Automation()
        self.ok = self._auto.ok

    def probe(self, handle: int) -> bool:
        if not self.ok:
            return False
        try:
            return uia.probe_approval(handle, self._auto)
        except Exception:
            return False

    def close(self) -> None:
        self._auto.close()


class WindowsBackend(Backend):
    name = "windows"
    supports_ui_probe = True
    supports_notifications = True
    supports_icon_extract = True

    def __init__(self) -> None:
        self._notify = WinNotificationSource()

    # ---------------- 窗口

    def windows(self, visible_only: bool = True) -> list[WindowInfo]:
        return [
            WindowInfo(handle=win.hwnd, pid=win.pid, title=win.title,
                       class_name=win.class_name, visible=win.visible)
            for win in winapi.iter_windows(visible_only=visible_only)
        ]

    def is_minimized(self, handle: int) -> bool:
        return winapi.is_minimized(handle)

    def activate(self, handle: int) -> bool:
        return winapi.activate_window(handle)

    def describe(self, handle: int) -> str:
        return winapi.describe_window(handle)

    # ---------------- 界面探针

    def probe_approval(self, handle: int) -> bool:
        """一次性探针：自己开自己关。

        给诊断工具用，比会话方式慢几十毫秒，但不持有任何状态。
        """
        return uia.probe_approval(handle)

    def probe_ready(self) -> bool:
        return True

    def ui_session(self) -> UiProbeSession:
        return _UiaSession()

    # ---------------- 图标

    def icon_png(self, exe_path: str, size: int) -> bytes | None:
        return icons.icon_png(exe_path, size)

    # ---------------- 通知

    @property
    def notifications_available(self) -> bool:
        return self._notify.available

    def prime_notices(self):
        return self._notify.prime()

    def poll_notices(self):
        return self._notify.poll()

    # ---------------- 杂项

    def config_dir(self) -> str:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "AgentHub")

    def begin_window_drag(self, native) -> bool:
        """把 pywebview 的 WinForms 窗口句柄交给系统去拖。"""
        hwnd = _native_handle(native)
        if not hwnd:
            return False
        return winapi.start_window_drag(hwnd)

    def alert_sound(self, level: int = 2) -> None:
        """审批响三声短促的升调；只是「跑完了」就一声轻响。

        `Beep` 是按频率合成方波，不依赖系统音效方案，也不受「静音主题」
        影响——用户要的就是「必须听到」。
        """
        try:
            import winsound
        except ImportError:
            return
        try:
            if level >= 2:
                for _ in range(3):
                    winsound.Beep(880, 80)
                    time.sleep(0.06)
            else:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

    def reveal(self, path: str) -> None:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def capabilities(self) -> dict:
        caps = super().capabilities()
        caps["notifications"] = self._notify.available
        return caps
