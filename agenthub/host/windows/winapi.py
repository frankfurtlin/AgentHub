# -*- coding: utf-8 -*-
"""Windows 原生 API 封装（纯 ctypes，零第三方依赖）。

提供三类能力：
  1. 进程枚举 —— 判断某个 Agent 是否在运行、以及累计 CPU 时间
  2. 窗口枚举 —— 找到 Agent 的主窗口句柄
  3. 窗口激活 —— 把窗口切到前台（含绕过 Windows 前台锁定限制）
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_SHOWNORMAL = 1
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x02
MAX_PATH = 260
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# SetWindowPos 的 flag（拖动跟随用：只挪位置，不动尺寸不抢 Z 序不抢焦点）
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SPI_GETWORKAREA = 0x0030


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
]
user32.SystemParametersInfoW.restype = wintypes.BOOL


class FILETIME(ctypes.Structure):
    """FILETIME 结构体，附带一个把高低位拼成 100 纳秒计数的便捷属性。"""

    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]

    @property
    def total(self) -> int:
        return (self.dwHighDateTime << 32) | self.dwLowDateTime



class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    visible: bool = True
    exe: str = field(default="")


# ---------------------------------------------------------------- 进程

kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetProcessTimes.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
]
kernel32.GetProcessTimes.restype = wintypes.BOOL


def iter_processes() -> dict[int, str]:
    """枚举所有进程，返回 {pid: 可执行文件名}。"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return {}
    result: dict[int, str] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                result[int(entry.th32ProcessID)] = entry.szExeFile
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return result


def process_path(pid: int) -> str:
    """按 PID 取可执行文件完整路径，失败返回空串。"""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(MAX_PATH * 4)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------- 窗口

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
user32.SwitchToThisWindow.restype = None


def iter_windows(visible_only: bool = True) -> list[WindowInfo]:
    """枚举顶层窗口。

    visible_only=False 时把隐藏窗口（例如缩在托盘里的主窗口）也一并返回，
    这样即使应用藏起来了，我们依然能拿到它的窗口句柄用于唤起。
    """
    out: list[WindowInfo] = []

    def _callback(hwnd, _lparam):
        visible = bool(user32.IsWindowVisible(hwnd))
        if visible_only and not visible:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append(
            WindowInfo(
                hwnd=int(hwnd),
                pid=pid.value,
                title=title.value,
                class_name=cls.value,
                visible=visible,
            )
        )
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    # 已排到前面的窗口层级更高，保留枚举顺序即可
    return out


def _set_foreground_attached(hwnd: int) -> None:
    """常规解法：把本线程临时挂到当前前台线程上，再抢前台。

    注意 `SetForegroundWindow` 的返回值**不代表真的抢到了**——被前台锁定拦下时
    它照样可以返回真。所以这里不返回布尔值，成败由调用方复核 GetForegroundWindow。
    """
    current_tid = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0

    attached = False
    if fg_tid and fg_tid != current_tid:
        attached = bool(user32.AttachThreadInput(current_tid, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current_tid, fg_tid, False)


def describe_window(hwnd: int) -> str:
    """把一个窗口描述成一句人话，用于日志和排查「是谁挡住了前台」。"""
    if not hwnd:
        return "(无)"
    length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title, length + 1)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    return "hwnd=%s class=%r title=%r" % (hwnd, cls.value, title.value)


def window_pos_phys(hwnd: int) -> tuple[int, int] | None:
    """窗口左上角，物理像素。失败返回 None。

    拖动跟随专用：GetWindowRect 直读，不走 pywebview 的属性（那个跨线程
    要经 WinForms 消息队列，一次几十毫秒，跟着拖会卡）。
    """
    if not hwnd:
        return None
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top)


def move_window_phys(hwnd: int, x: int, y: int) -> bool:
    """把窗口挪到物理像素 (x, y)，不动尺寸、不抢 Z 序、不抢焦点。"""
    if not hwnd:
        return False
    return bool(user32.SetWindowPos(
        hwnd, None, int(x), int(y), 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
    ))


def work_area_phys() -> tuple[int, int, int, int] | None:
    """主屏工作区（去掉任务栏），物理像素 (left, top, right, bottom)。"""
    rect = RECT()
    if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


# ---------------------------------------------------------------- 窗口跟随

WM_MOVE = 0x0003
WM_NCDESTROY = 0x0082
GWLP_WNDPROC = -4

#: 窗口过程签名：LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,      # LRESULT
    wintypes.HWND,         # hWnd
    ctypes.c_uint,         # uMsg
    ctypes.c_size_t,       # wParam
    ctypes.c_ssize_t,      # lParam
)

# SetWindowLongPtrW 在 32 位系统上不存在（那里要用 SetWindowLongW），兜一下
_SetWindowLongPtr = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
_SetWindowLongPtr.restype = ctypes.c_void_p
user32.CallWindowProcW.argtypes = [
    ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
]
user32.CallWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

# ---------------------------------------------------------------- 任务栏隐藏

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080     # 工具窗：不出现在任务栏，也不进 Alt-Tab
WS_EX_APPWINDOW = 0x00040000      # 强制任务栏按钮——pywebview 建窗时带上它了

_GetWindowLongPtr = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLongPtr.restype = ctypes.c_ssize_t


def set_tool_window(hwnd: int) -> bool:
    """把窗口改成「工具窗」：任务栏不再有按钮，Alt-Tab 里也不出现。

    球和面板都是桌面挂件，挂在任务栏里既占地方（用户截图吐槽的那个 Python
    图标窗口就是球），切 Alt-Tab 时还会打断正常工作流。托盘图标由 NotifyIcon
    负责，见 tray.py。

    直接改扩展样式，不走 WinForms 的 `ShowInTaskbar`——那个属性是靠**重建
    窗口句柄**生效的，句柄一换，之前挂在球上的拖动跟随钩子、DPI 记录全作废。
    """
    if not hwnd:
        return False
    ex = int(_GetWindowLongPtr(wintypes.HWND(hwnd), GWL_EXSTYLE) or 0)
    ex = (ex & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
    return bool(_SetWindowLongPtr(
        wintypes.HWND(hwnd), GWL_EXSTYLE, ctypes.c_void_p(ex)))


class DragFollower:
    """把目标窗口钉在源窗口旁边，源窗口每移动一帧就同帧跟上。

    做法是替换源窗口的窗口过程（`SetWindowLongPtrW(GWLP_WNDPROC)`），
    在 `WM_MOVE` 里顺手把目标窗口 `SetWindowPos` 过去：原生拖动期间鼠标每动
    一下就来一条 `WM_MOVE`，所以面板是跟着球逐帧走的，全程在 GUI 线程内
    同一帧完成。

    **为什么不用 `SetWindowSubclass`**（那个更"正规"）：它要求由窗口所属线程
    调用。我们是从后台线程挂的钩子，实测在 pywebview 的 WinForms 窗体上
    直接返回 FALSE、一帧都跟不动。`SetWindowLongPtrW` 允许跨线程安装，
    回调仍旧在窗口所属线程（GUI 线程）里执行，效果一样。

    为什么不用轮询（哪怕 16ms 一轮也到不了 60fps 的手感）：
      1. Windows 的等待粒度就是 15.6ms 起，`Event.wait(0.016)` 精度不够；
      2. `SetWindowPos` 跨线程要等窗口所属线程处理完消息才返回，
         而那个线程正忙在自己的拖动消息循环里。

    生命周期：回调必须一直有 Python 侧引用（ctypes 回调被回收就崩），
    所以实例登记在 `_registry` 里；`remove()` 把原窗口过程写回去。
    """

    _registry: dict[int, "DragFollower"] = {}
    _next_id = 1

    def __init__(self, source: int, target: int, ball: int, panel_w: int,
                 panel_h: int, gap: int, margin: int) -> None:
        self.source = int(source)
        self.target = int(target)
        self.ball = int(ball)
        self.panel_w = int(panel_w)
        self.panel_h = int(panel_h)
        self.gap = int(gap)
        self.margin = int(margin)
        self.work = work_area_phys()
        self.moves = 0                     # 跟着源窗口走了多少帧，排查用
        self._uid = 0
        self._old_proc = None
        self._proc_ref = WNDPROC(self._proc)

    # ---------------- 装卸

    def install(self) -> bool:
        uid = DragFollower._next_id
        DragFollower._next_id += 1
        DragFollower._registry[uid] = self
        prev = _SetWindowLongPtr(
            self.source, GWLP_WNDPROC, ctypes.cast(self._proc_ref, ctypes.c_void_p))
        if not prev or int(prev) == int(ctypes.cast(self._proc_ref, ctypes.c_void_p).value):
            DragFollower._registry.pop(uid, None)
            return False
        self._old_proc = prev
        self._uid = uid
        return True

    def remove(self) -> None:
        if not self._uid:
            return
        try:
            _SetWindowLongPtr(self.source, GWLP_WNDPROC, self._old_proc)
        finally:
            DragFollower._registry.pop(self._uid, None)
            self._uid = 0
            self._old_proc = None

    # ---------------- 摆位

    def follow(self) -> None:
        """按源窗口当前的位置摆好目标窗口（消息钩子里直接调，也供外部纠偏）。"""
        rect = RECT()
        if not user32.GetWindowRect(self.source, ctypes.byref(rect)):
            return
        bx, by = int(rect.left), int(rect.top)
        px = bx - self.panel_w - self.gap
        if px < self.margin:               # 左边放不下就换到源窗口右边
            px = bx + self.ball + self.gap
        py = by + self.ball - self.panel_h
        if self.work is not None:
            px = max(self.margin, min(px, self.work[2] - self.panel_w - self.margin))
            py = max(self.margin, min(py, self.work[3] - self.panel_h - self.margin))
        move_window_phys(self.target, px, py)

    # ---------------- 消息钩子

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_MOVE:
            self.follow()
            self.moves += 1
        elif msg == WM_NCDESTROY:
            # 窗口要没了：别再往回写窗口过程（写了就是往尸体上挂钩子）
            DragFollower._registry.pop(self._uid, None)
            self._uid = 0
            self._old_proc = None
        if self._old_proc:
            return user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def activate_window(hwnd: int) -> bool:
    """把窗口切到前台，自动处理最小化 / 隐藏 / 前台锁定三种情况。

    **返回值是复核过的真实结果**（最后核对一次 GetForegroundWindow），
    而不是 `SetForegroundWindow` 的返回值。

    关于前台锁定：Windows 只把前台交给「刚刚收到过用户输入」的进程。本程序的
    激活动作都是由用户点击球体 / 卡片触发的，那一刻输入事件落在本进程上，所以
    常规路径就够用。这里没有再合成 ALT 按键去强行解锁——那种做法虽常见，但裸按
    一次 ALT 在资源管理器里会切换菜单栏显示，副作用比收益大。
    """
    if not hwnd:
        return False
    if not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOWNORMAL)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    if user32.GetForegroundWindow() == hwnd:
        return True

    _set_foreground_attached(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True

    # 最后一搏：这是未公开 API，但确实能绕过部分前台锁定场景
    user32.SwitchToThisWindow(hwnd, True)
    return user32.GetForegroundWindow() == hwnd


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd)) if hwnd else False


# ---------------------------------------------------------------- 窗口拖动

WM_NCLBUTTONDOWN = 0xA1
HTCAPTION = 2

user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL


def start_window_drag(hwnd: int) -> bool:
    """让系统接管这个窗口的拖动——鼠标按住，松开才结束。

    比在前端自己算位移可靠得多：窗口只有几十像素宽，快速拖动时光标瞬间就跑
    到窗口外了，`mousemove` 收不到，窗口会卡住。交给系统则完全不受这个限制。

    这里用 **PostMessage 而不是 SendMessage**：`WM_NCLBUTTONDOWN` 会进入
    系统的拖动循环、直到鼠标松开才返回，而本函数是从 JS 桥的工作线程发出的，
    同步等待会把 GUI 线程一起卡住。Post 进去就返回，拖动由系统自己跑。
    """
    if not hwnd:
        return False
    user32.ReleaseCapture()
    user32.PostMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
    return True
