# -*- coding: utf-8 -*-
"""把 AgentHub 加进 / 移出开机自启（写当前用户的 Run 键，不需要管理员权限）。

用法：
    python tools/install_startup.py on      开机自启
    python tools/install_startup.py off     取消自启
    python tools/install_startup.py status  查看当前状态

两个实现细节，都是踩过的：

· 写 `HKEY_CURRENT_USER` 而不是 `LOCAL_MACHINE`——后者要管理员，而这个工具的
  定位就是「双击就能开」。
· 启动命令要分三种情况：打包出来的 exe 最稳；没打包就用 `pythonw.exe -m agenthub`
  （pythonw 不弹控制台黑窗，用 python.exe 的话每次开机都闪一下黑框）。

目前只有 Windows 实现。macOS 那边对应的是 launchd 的
`~/Library/LaunchAgents/*.plist`，等 `agenthub/host/macos/` 落地时一起补。
"""

from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AgentHub"

# 项目根 = tools/ 上一层
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _command() -> str:
    """挑一条最合适的开机启动命令。"""
    # 一、打包产物优先：不依赖机器上有 Python
    for relative in ("dist/AgentHub/AgentHub.exe", "dist/AgentHub.exe"):
        exe = os.path.join(ROOT, relative)
        if os.path.isfile(exe):
            return '"%s"' % exe

    # 二、退到 pythonw -m agenthub：pythonw 没有控制台窗口
    here = sys.executable or ""
    pythonw = os.path.join(os.path.dirname(here), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = "pythonw.exe"                  # 指 PATH 上的
    return '"%s" -m agenthub' % pythonw


def enable() -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
    print("已开启开机自启：")
    print("   " + _command())


def disable() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        print("已取消开机自启。")
    except FileNotFoundError:
        print("本来就没有开启开机自启。")


def status() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        print("开机自启：已开启")
        print("   " + value)
    except FileNotFoundError:
        print("开机自启：未开启")


def main() -> None:
    if sys.platform != "win32":
        print("这个脚本目前只支持 Windows。")
        return
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if action == "on":
        enable()
    elif action == "off":
        disable()
    elif action == "status":
        status()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
