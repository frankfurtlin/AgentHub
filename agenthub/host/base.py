# -*- coding: utf-8 -*-
"""平台抽象层——把「和操作系统打交道」的部分收进一个接口后面。

上层（runtime.py / app.py / 前端）只认这里的类型和接口，不直接 import 任何
Win32 或 Cocoa。换平台 = 换一个 Backend 实现，其余代码一行不动。

抽象出来的能力：

    进程 / 窗口     判断工具在不在跑、窗口在哪、能不能切过去
    界面探针        直接读对方窗口，看有没有摆着审批条
    通知读取        读系统通知库的增量
    图标提取        从可执行文件里取出应用图标

实现位置：

    host/windows/   Win32，纯 ctypes（已经成熟）
    host/macos/     占位，待补；待办清单写在该文件顶部

进程枚举和 CPU 采样交给 psutil 统一处理——它是跨平台的，能力完全够，
这样两个平台实现都不用再写一遍。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import psutil

from ..models import Notice


# ---------------------------------------------------------------- 类型

@dataclass
class WindowInfo:
    """一个顶层窗口。

    handle 在 Windows 上是 HWND，在 macOS 上是 CGWindowID——上层只当它是
    一个不透明的整数，不做任何算术。
    """

    handle: int
    pid: int
    title: str
    class_name: str = ""
    visible: bool = True

    def __str__(self) -> str:
        return "handle=%s pid=%s title=%r" % (self.handle, self.pid, self.title)


@dataclass
class ProcessInfo:
    pid: int
    name: str = ""       # 可执行文件名，如 CodeBuddy CN.exe
    path: str = ""       # 完整路径，拿不到时为空
    started: float = 0.0  # 进程创建时间（unix 秒），面板排序用；拿不到时为 0


class UiProbeSession:
    """一次界面探针会话——在同一个线程里复用昂贵的 COM / AX 对象。

    Windows 的 UIA 和 macOS 的 Accessibility 对象都绑定在创建它们的线程上，
    生命周期必须和用它的线程一致，所以不能挂在 Backend 上长期持有：探针线程
    自己开一个、自己关掉。

    不支持的平台拿到的是基类实现——`probe()` 恒为 False，`ok` 恒为 False，
    调用方据此退化到通知通道。
    """

    ok = False

    def probe(self, handle: int) -> bool:
        return False

    def close(self) -> None:
        pass


# ---------------------------------------------------------------- 接口

class Backend(ABC):
    """一个平台的全部系统能力。"""

    name = "base"

    #: 能不能读别的窗口的界面（审批条探针）
    supports_ui_probe = False
    #: 能不能读系统通知库
    supports_notifications = False
    #: 能不能从可执行文件里抠图标
    supports_icon_extract = False

    # ---------------- 进程（psutil 通用实现，子类一般不用覆盖） ----------------

    def processes(self) -> list[ProcessInfo]:
        """枚举本机所有进程。"""
        out: list[ProcessInfo] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "create_time"]):
            info = proc.info
            if info.get("pid") is None:
                continue
            out.append(ProcessInfo(
                pid=int(info["pid"]),
                name=info.get("name") or "",
                path=info.get("exe") or "",
                started=float(info.get("create_time") or 0.0),
            ))
        return out

    def cpu_seconds(self, pid: int) -> float | None:
        """进程累计占用的 CPU 秒数（user + system）。

        用累计值而不是瞬时百分比：调用方自己两次采样做差，才能算出
        「这段间隔内实际用了多少 CPU」，不受采样时刻影响。
        """
        try:
            times = psutil.Process(pid).cpu_times()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return None
        return float(times.user + times.system)

    # ---------------- 窗口 ----------------

    @abstractmethod
    def windows(self, visible_only: bool = True) -> list[WindowInfo]:
        """枚举顶层窗口。

        visible_only=False 时把隐藏窗口也返回——应用缩到托盘后主窗口是隐藏的，
        但那个句柄仍然有效，留着才能把它唤起来。
        """

    @abstractmethod
    def is_minimized(self, handle: int) -> bool:
        """窗口是否处于最小化。"""

    @abstractmethod
    def activate(self, handle: int) -> bool:
        """把窗口切到前台。返回**复核过的真实结果**，不是 API 的返回值。"""

    def describe(self, handle: int) -> str:
        """把窗口描述成一句人话，仅用于日志和排查。"""
        return "handle=%s" % handle

    # ---------------- 界面探针 ----------------

    def probe_approval(self, handle: int) -> bool:
        """读这个窗口的界面，判断有没有正摆着「等你确认」的条。

        不支持的平台返回 False（调用方据此退化到通知通道）。
        """
        return False

    def probe_ready(self) -> bool:
        """探针是否可用（macOS 上要用户先授予辅助功能权限）。"""
        return self.supports_ui_probe

    def ui_session(self) -> UiProbeSession:
        """在当前线程开一个可复用的探针会话。"""
        return UiProbeSession()

    # ---------------- 图标 ----------------

    @abstractmethod
    def icon_png(self, exe_path: str, size: int) -> bytes | None:
        """从可执行文件里取应用图标，返回**透明底** PNG 字节。

        透明底是给 Web 前端用的：底色由 CSS 决定，图标自己不带底板，
        同一张图在浅色/深色主题下都能用。
        """

    # ---------------- 通知 ----------------

    def prime_notices(self) -> list[Notice]:
        """首次调用：记住当前水位，并回看最近一段时间的通知。"""
        return []

    def poll_notices(self) -> list[Notice]:
        """返回本轮新增的通知。"""
        return []

    # ---------------- 杂项 ----------------

    def config_dir(self) -> str:
        """本机存放配置/缓存的目录（不存在时由调用方创建）。"""
        return "."

    def screen_scale(self) -> float:
        """主屏的 DPI 缩放系数（1.0 = 100%，1.25 = 125%）。

        `webview.screens` 报出来的尺寸，语义会随「进程是否感知 DPI」而变
        （见 `webview_env.enable_dpi_awareness()`）——要么是虚拟化后的逻辑值、
        要么是物理像素。要折回 pywebview 的 `move()` 认的逻辑坐标，就得拿这个
        系数去除。不支持的平台返回 1.0，等价于 100% 缩放、不做折算。
        """
        return 1.0

    def begin_window_drag(self, native) -> bool:
        """让系统接管一个窗口的拖动。

        `native` 是 GUI 后端给的窗口对象（pywebview 在 Windows 上是 WinForms
        的 Form，在 macOS 上是 NSWindow）。不支持时返回 False，前端可以退回到
        「自己算鼠标位移」的方案。
        """
        return False

    def make_tool_window(self, native) -> bool:
        """把窗口从任务栏里摘掉。不支持时返回 False（比如平台另有做法）。

        挂件类窗口本来就该待在托盘里，占一个任务栏按钮既碍事又和别的窗口
        混在一起——入口交给托盘图标（见 tray.py）。
        """
        return False

    # ---------------- 拖动跟随的快速通道（默认没有，Windows 后端覆盖） ----------------

    def window_pos(self, native) -> tuple[int, int] | None:
        """窗口左上角（物理像素）。返回 None 表示平台不支持快速通道。"""
        return None

    def place_window(self, native, x: int, y: int) -> bool:
        """把窗口挪到物理像素 (x, y)。不支持时返回 False。"""
        return False

    def work_area(self) -> tuple[int, int, int, int] | None:
        """主屏工作区（物理像素）。不支持时返回 None。"""
        return None

    def attach_follow(self, source_native, target_native, sizes) -> bool:
        """让目标窗口逐帧跟随源窗口（Windows 上是挂消息钩子）。

        `sizes` = (源窗口宽, 目标窗口宽, 目标窗口高, 间隙, 边距)，逻辑像素。
        不支持时返回 False，调用方退回轮询方案。
        """
        return False

    def sync_follow(self) -> bool:
        """按源窗口当前位置立刻纠一次位。不支持时返回 False。"""
        return False

    def detach_follow(self) -> None:
        """摘掉跟随（切换面板显隐时调用）。"""
        return

    def alert_sound(self, level: int = 2) -> None:
        """需要你出手时响一下。

        **故意放在 Python 侧而不是前端**：WebView 的自动播放策略未必允许
        没有用户交互就发声，而且音色要按平台分别调。这个调用是阻塞的
        （一声几百毫秒），调用方应该在后台线程里跑。
        """
        return

    def reveal(self, path: str) -> None:
        """在文件管理器里定位一个文件。"""
        raise NotImplementedError

    def capabilities(self) -> dict:
        """给界面看的能力清单。"""
        return {
            "platform": self.name,
            "uiProbe": self.probe_ready(),
            "notifications": self.supports_notifications,
            "icons": self.supports_icon_extract,
        }
