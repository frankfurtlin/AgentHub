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


def apply(config=None) -> list[str]:
    """摆好环境。必须在 `webview.start()` 之前调用。"""
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
