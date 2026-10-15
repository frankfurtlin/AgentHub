# -*- coding: utf-8 -*-
"""诊断工具：看看界面探针在各家工具上到底读到了什么。

AgentHub 判断「某个工具在等你确认」有两条路：Windows 通知，和直接读界面
（UI Automation）。通知那条路对 CodeBuddy / Qoder 已经证明指望不上（它们要么不发
通知，要么发了不带能判状态的正文），所以真出问题时，用这个脚本当场看一眼界面里
读到了什么，比猜快得多。

用法（不影响正在运行的 AgentHub）：

    .venv\\Scripts\\python.exe tools\\probe_uia.py          循环观察，Ctrl+C 退出
    .venv\\Scripts\\python.exe tools\\probe_uia.py --once   只看一次
    .venv\\Scripts\\python.exe tools\\probe_uia.py --dump   顺便把界面文字全部打出来

**怎么触发一次审批来验证**：在 CodeBuddy 里让它跑一条危险命令（比如
`set -a; . ./x.env; curl ...`），看到「包含危险命令，是否仍要运行？」那条时别点，
切回这里看输出——应当出现 `待审批`，并写明是**哪条通道**命中的（按钮通道 / 标题通道）。
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agenthub import webview_env                       # noqa: E402
from agenthub.host.windows import uia, winapi          # noqa: E402

HINTS = ("codebuddy", "qoder", "trae", "workbuddy")


def main_window(pid_set: set):
    """挑一个最像主窗口的：Electron 的主窗口类名是 Chrome_WidgetWin_1。"""
    best = None
    for w in winapi.iter_windows(visible_only=False):
        if w.pid not in pid_set or "Chrome_WidgetWin_1" not in w.class_name or not w.title:
            continue
        if best is None or w.visible:
            best = w
            if w.visible:
                break
    return best


def report(auto: uia.Automation, dump: bool) -> None:
    procs = winapi.iter_processes()
    print("-" * 74)
    for hint in HINTS:
        pids = {pid for pid, exe in procs.items() if hint in (exe or "").lower()}
        if not pids:
            print("%-10s 没在运行" % hint)
            continue
        win = main_window(pids)
        if not win:
            print("%-10s 进程在，但没找到带标题的主窗口" % hint)
            continue
        render = uia.find_render_surface(win.hwnd)
        minimized = winapi.is_minimized(win.hwnd)

        t0 = time.time()
        names = auto.control_names(render)
        button_cost = time.time() - t0
        t0 = time.time()
        titles = auto.matching_names(render, uia._APPROVAL_TITLES)
        title_cost = time.time() - t0

        verdict = uia.approval_labels(names)
        state = "待审批" if (verdict or titles) else "——"
        print("%-10s %-6s 渲染面=%s 按钮 %2d 个  按钮通道%.0fms 标题通道%.0fms  最小化=%s"
              % (hint, state, "是" if render != win.hwnd else "否(退回主窗口)",
                 len(names), button_cost * 1000, title_cost * 1000, minimized))
        if verdict:
            print("           按钮通道命中: %s / %s" % verdict)
        if titles:
            print("           标题通道命中: %s" % titles)
        if names:
            print("           按钮名: %s" % ", ".join(names[:16]))
        if dump:
            texts = auto.all_text(render, limit=60)
            print("           界面文字(前 25 条): %s" % " | ".join(texts[:25]))


def main() -> None:
    if sys.platform != "win32":
        print("界面探针目前只有 Windows 实现。")
        return
    webview_env.enable_dpi_awareness()
    auto = uia.Automation()
    if not auto.ok:
        print("无法创建 IUIAutomation —— 界面探针不可用。")
        return
    dump = "--dump" in sys.argv
    if "--once" in sys.argv or dump:
        report(auto, dump)
        auto.close()
        return
    print("循环观察中，Ctrl+C 退出。先去 CodeBuddy 触发一条危险命令，再回来看这里。")
    try:
        while True:
            report(auto, False)
            time.sleep(2.5)
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        auto.close()


if __name__ == "__main__":
    main()
