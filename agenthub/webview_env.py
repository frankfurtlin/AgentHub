# -*- coding: utf-8 -*-
"""启动 WebView 之前必须摆好的环境。

这里的东西都很不显眼，但少一样界面就整个白屏，而且在应用层完全看不出原因——
所以每一段都写清楚「不这么做会怎样」。

一、WebView2 的 GPU 沙箱
    WebView2 会为合成单独起一个 GPU 进程。部分机器上这个子进程起不来，
    Chromium 会连试 9 次然后整体放弃：

        ERROR:gpu_process_host.cc] GPU process exited unexpectedly: exit_code=7
        FATAL:gpu_data_manager_impl_private.cc] GPU process isn't usable. Goodbye.

    之后窗口就停在 `DefaultBackgroundColor`（一片白），JS 一行都不执行，
    Python 侧 `evaluate_js()` 抛「Main window failed to start」。
    加 `--disable-gpu-sandbox` 即可——实测在开发机上从「每次必崩」变成「零崩溃」。
    这个开关只去掉 GPU 子进程的沙箱，页面又全是本地文件，代价可以接受；
    真要收紧就在 config.json 的 `webviewArgs` 里把它去掉。

    注意 `--disable-gpu` 反而**没用**（GPU 进程照样起、照样崩），
    试过的无效组合记录在 docs/ 里，别再走一遍。

二、用户数据目录
    pywebview 在 private_mode（默认开）下拿
    `tempfile.TemporaryDirectory().name` 当 profile 目录。但那个
    TemporaryDirectory 对象一转身就被 GC，目录当场被删，WebView2 拿到的是个
    不存在的路径。我们自己指一个真实目录，顺带把 private_mode 关掉，
    省得退出时它去 `shutil.rmtree` 一个已经没了的东西。
"""

from __future__ import annotations

import ctypes
import os
import sys

# WebView2 运行时认这个环境变量，会和 CreateCoreWebView2Environment 的
# 参数一起加给浏览器进程。
ARGV_ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"

# 见模块开头第一条。就这一个开关，不再多动别的——试出来的。
GPU_SANDBOX_FLAG = "--disable-gpu-sandbox"


def _merge_env(flags: list[str]) -> None:
    """把参数并进环境变量，已有内容保留（用户可能自己设过）。"""
    if not flags:
        return
    current = os.environ.get(ARGV_ENV, "").strip()
    merged = (current + " " + " ".join(flags)).strip()
    os.environ[ARGV_ENV] = merged


def browser_flags(config=None) -> list[str]:
    """本次启动要加的浏览器参数。

    平台默认值 + 配置里的 `webviewArgs`。配置里给的是**追加**，
    想去掉默认值就把同一个开关写进 `webviewArgs` 也无济于事——
    真要覆盖请直接设 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 环境变量，
    这里检测到就会尊重它。
    """
    flags: list[str] = []
    if sys.platform == "win32":
        flags.append(GPU_SANDBOX_FLAG)

    extra = config.get("webviewArgs") if config is not None else None
    if isinstance(extra, (list, tuple)):
        flags.extend(str(item) for item in extra if str(item).strip())
    return flags


def enable_dpi_awareness() -> bool:
    """让本进程感知 DPI。**必须在创建任何窗口之前调用。**

    不设的话，Windows 会把我们读到和写下去的一切都按 96dpi 虚拟化一遍：
    2560×1440 的屏报成 2048×1152，而我们正是按这个尺寸算球的落点，多出来的
    那一截靠系统把整个窗口位图拉过 1.25 倍补上。**拉伸就是「界面发糊、边缘发
    毛」的来源**，跟浏览器渲染质量没关系——同一个页面在感知 DPI 的进程里
    渲染，颜色种类会明显多出一截。

    pywebview 自己**不设**。`platforms/winforms.py` 的 `get_screens()` 注释里
    写着 "already DPI-aware scaled due to SetProcessDPIAware"，那是它的**假设**，
    不是它做过的事；全库搜不到一处 SetProcessDPIAware。所以这一步得我们补。

    调用点放在 `apply()` 里、和环境变量一起摆好——两件事都必须在
    `webview.start()` 之前完成，分开放容易漏掉一件。

    ⚠️ 设了之后 `webview.screens` 的语义会变：`Screen.width` 从「虚拟逻辑值」
    变成「物理像素」，于是所有传给 `Window.move()` 的坐标都得先折回逻辑值
    （它内部会再乘一次缩放）。见 `app.AgentHubApp._logical_screen()`。

    重复调用无副作用：第一次生效后，后面的调用都会失败，但不影响已生效的级别。
    """
    if sys.platform != "win32":
        return False

    # Win8.1+：PROCESS_PER_MONITOR_DPI_AWARE = 2。
    # 比 SetProcessDPIAware()（系统级）更细，多显示器缩放不同时不会糊。
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        if shcore.SetProcessDpiAwareness(2) == 0:       # S_OK
            return True
    except (OSError, AttributeError):
        pass

    # 退到 Vista 时代的老 API：系统级感知。至少不再有位图拉伸。
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        return bool(user32.SetProcessDPIAware())
    except (OSError, AttributeError):
        return False


def apply(config=None) -> list[str]:
    """摆好环境。必须在 `webview.start()` 之前调用。"""
    enable_dpi_awareness()
    flags = browser_flags(config)
    if flags and ARGV_ENV not in os.environ:
        _merge_env(flags)
    return flags


def profile_dir(base_dir: str) -> str:
    """给 WebView2 的 profile 目录（见模块开头第二条）。"""
    path = os.path.join(base_dir, "webview")
    os.makedirs(path, exist_ok=True)
    return path


def start_options(config=None, base_dir: str | None = None) -> dict:
    """`webview.start()` 要的那些额外参数。

    做成一个函数，是为了让「摆环境」和「开窗口」在一次调用里完成，
    免得将来有人插了别的代码在中间、顺序被打乱。
    """
    apply(config)
    if base_dir is None:
        return {}
    return {"storage_path": profile_dir(base_dir), "private_mode": False}
