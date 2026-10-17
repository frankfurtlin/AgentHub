# -*- coding: utf-8 -*-
"""托盘图标。

窗口改成「工具窗」（见 winapi.set_tool_window）之后任务栏里就没有按钮了，
那总得留个入口：点一下能展开面板、右键能退出。这里就是那个入口。

两个容易踩的点：

一、NotifyIcon 必须在**有消息循环的那条线程**上建
    pywebview 的 GUI 线程是主那条；`_loop` 跑在子线程上，直接建图标不会报错，
    但点它没反应。所以 `attach()` 一律经 `form.Invoke` 跳过去，和
    `webview_env.patch_transparency` 的做法一致。

二、回调里别做重活
    菜单的 Click 事件就是在 GUI 线程里跑的，而展开面板要 resize + move +
    evaluate_js，一整套走下来能感觉到卡。所以这里只把活丢给一条后台线程，
    和 Bridge 里的处理保持一致。
"""

from __future__ import annotations

import os
import sys
import threading

ICON_FILE = "tray.ico"


def icon_path() -> str:
    """托盘图标的路径。打包成单文件 exe 后资源会被解到 _MEIPASS。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "agenthub", "assets", ICON_FILE)


def attach(app) -> bool:
    """把托盘图标装上。可以被反复调用（窗口原生对象就绪得比建窗晚）。

    返回 True 表示已装好；False 表示还没就绪，调用方下一轮再来。
    """
    if getattr(app, "_tray_ready", False) or app.ball is None:
        return bool(getattr(app, "_tray_ready", False))
    native = getattr(app.ball, "native", None)
    if native is None:
        return False
    try:
        if native.InvokeRequired:
            from System import Action
            box: list = []
            native.Invoke(Action(lambda: box.append(_install(app))))
            return bool(box and box[0])
        return _install(app)
    except Exception as exc:
        _log("装托盘图标失败：%r" % exc)
        return False


def _install(app) -> bool:
    """建图标。**必须在 GUI 线程上跑。**"""
    try:
        from System.IO import MemoryStream
        from System.Drawing import Icon
        from System.Windows.Forms import (ContextMenuStrip, NotifyIcon,
                                         ToolStripMenuItem)
    except Exception as exc:                       # 没有 pythonnet 就别硬撑
        _log("WinForms 不可用，跳过托盘：%r" % exc)
        return False

    path = icon_path()
    icon = None
    try:
        with open(path, "rb") as handle:
            icon = Icon(MemoryStream(bytearray(handle.read())))
    except Exception as exc:
        _log("读托盘图标失败（%s）：%r" % (path, exc))

    tray = NotifyIcon()
    tray.Icon = icon or NotifyIcon().Icon
    tray.Text = "AgentHub"
    tray.Visible = True

    menu = ContextMenuStrip()
    item_toggle = ToolStripMenuItem("显示 / 隐藏面板")
    item_quit = ToolStripMenuItem("退出")
    menu.Items.Add(item_toggle)
    menu.Items.Add(item_quit)
    tray.ContextMenuStrip = menu

    def run(fn):
        threading.Thread(target=fn, daemon=True, name="agenthub-tray").start()

    def on_toggle(sender, args):
        run(app.toggle_panel)

    def on_quit(sender, args):
        tray.Visible = False
        tray.Dispose()
        app._tray_ready = False
        run(app.quit)

    item_toggle.Click += on_toggle
    item_quit.Click += on_quit
    # 左键直接开合面板；右键由框架弹上面的菜单
    tray.Click += on_toggle

    app._tray_icon = tray
    app._tray_ready = True
    try:
        _log("托盘图标已就位（icon=%s）"
             % ("%dx%d" % (icon.Width, icon.Height) if icon else "缺图"))
    except Exception:
        _log("托盘图标已就位")
    return True


def set_text(app, text: str) -> bool:
    """更新悬停提示。必须先 `attach` 成功。"""
    icon = getattr(app, "_tray_icon", None)
    native = getattr(app.ball, "native", None) if app.ball is not None else None
    if icon is None or native is None:
        return False

    def job() -> bool:
        try:
            icon.Text = text[:60]       # NotifyIcon.Text 有长度上限
            return True
        except Exception:
            return False

    try:
        if native.InvokeRequired:
            from System import Action
            box: list = []
            native.Invoke(Action(lambda: box.append(job())))
            return bool(box and box[0])
        return job()
    except Exception:
        return False


def _log(message: str) -> None:
    try:
        print("[AgentHub] %s" % message, flush=True)
    except Exception:
        pass
