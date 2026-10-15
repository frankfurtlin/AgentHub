# -*- coding: utf-8 -*-
"""自检：绕开界面，直接验证底层能力是不是还活着。

界面出问题时（图标空白、状态永远是「未启动」、点了没反应），先用这个脚本把
「探测层」和「界面层」分开——如果这里全是 PASS，问题就在 WebView 那一侧，
去查 `docs/webview2-notes.md`；如果有 FAIL，那就是平台后端的事。

    .venv\\Scripts\\python.exe tools\\selftest.py

判据都是「能不能拿到像样的东西」，不是精确断言：本机装了几个 AI 工具、有没有通知
历史都不一样，硬断言只会让脚本在别人机器上误报。
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agenthub.config import Config                      # noqa: E402
from agenthub.host import get_backend                   # noqa: E402
from agenthub.models import AGENTS                      # noqa: E402
from agenthub.runtime import Runtime                    # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []


def check(name: str, ok: bool, note: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print("  [%s] %-28s %s" % ("通过" if ok else "**失败**", name, note), flush=True)


def skip(name: str, why: str) -> None:
    SKIP.append(name)
    print("  [跳过] %-28s %s" % (name, why), flush=True)


def main() -> int:
    print("平台 %s / 后端 %s" % (sys.platform, type(get_backend()).__name__), flush=True)

    backend = get_backend()

    print("\n—— 进程与窗口 ——", flush=True)
    procs = backend.processes()
    check("进程枚举", len(procs) > 10, "%d 个进程" % len(procs))

    found = []
    for agent in AGENTS:
        if any(pat in (p.name or "").lower() or pat in (p.path or "").lower()
               for pat in agent.match for p in procs):
            found.append(agent.key)
    print("       检测到：%s" % (", ".join(found) or "一个都没有（不影响自检）"), flush=True)

    wins = backend.windows()
    check("窗口枚举", len(wins) > 0, "%d 个可见窗口" % len(wins))

    sample = next((p for p in procs if p.pid > 1000), None)
    if sample:
        cpu = backend.cpu_seconds(sample.pid)
        check("CPU 时间采样", cpu is not None, "pid=%s -> %s" % (sample.pid, cpu))
    else:
        skip("CPU 时间采样", "没有可用的进程")

    print("\n—— 状态判断 ——", flush=True)
    runtime = Runtime(backend, Config())
    t0 = time.time()
    snap = runtime.refresh()
    cost = (time.time() - t0) * 1000
    states = snap.get("agents") or []
    totals = snap.get("totals") or {}
    check("一次扫描完成", bool(states), "%.0fms，%d 个工具" % (cost, len(states)))
    check("扫描够快（< 1500ms）", cost < 1500, "%.0fms" % cost)
    for st in states:
        print("       %-10s %-8s %s" % (st.get("key"), st.get("status"),
                                        st.get("detail") or ""), flush=True)
    print("       汇总 %s" % totals, flush=True)

    print("\n—— 图标 ——", flush=True)
    icon_ok = 0
    for agent in AGENTS:
        data = backend.icon_png(_first_exe(backend, agent.match) or "", 64)
        if data and len(data) > 500:
            icon_ok += 1
    if icon_ok:
        check("图标提取", True, "%d 个取到了" % icon_ok)
    else:
        skip("图标提取", "没装对应工具，或可执行文件里没有图标资源")

    print("\n—— 提醒通道 ——", flush=True)
    caps = backend.capabilities()
    print("       %s" % caps, flush=True)
    if caps.get("notifications"):
        notices = backend.prime_notices()
        check("通知读取", True, "历史 %d 条" % len(notices))
    else:
        skip("通知读取", "本平台未实现")
    if caps.get("uiProbe"):
        session = backend.ui_session()
        check("界面探针会话", getattr(session, "ok", False),
              "" if getattr(session, "ok", False) else "IUIAutomation 建不出来")
        session.close()
    else:
        skip("界面探针", "本平台未实现")

    print("\n—— 其他 ——", flush=True)
    # 先设上 DPI 感知再看缩放：不感知时 GetDpiForSystem 会返回 96（系数 1.0），
    # 那是「没设感知」的正常结果，不是 bug——但在自检里会误导，所以与真实运行时对齐。
    from agenthub import webview_env
    webview_env.enable_dpi_awareness()
    scale = backend.screen_scale()
    check("屏幕缩放", scale > 0, "%.2f（DPI %d）" % (scale, round(scale * 96)))
    check("配置目录", os.path.isdir(backend.config_dir()), backend.config_dir())
    check("提示音", _try_sound(backend), "调用没抛异常")

    print("\n—— 汇总 —— 通过 %d，失败 %d，跳过 %d" % (len(PASS), len(FAIL), len(SKIP)),
          flush=True)
    if FAIL:
        print("   失败项：%s" % "、".join(FAIL), flush=True)
        return 1
    return 0


def _first_exe(backend, patterns) -> str | None:
    """找一个匹配的可执行文件完整路径，拿不到图标时至少能看出是「没装」还是「坏了」。"""
    for p in backend.processes():
        blob = ((p.path or "") + " " + (p.name or "")).lower()
        if any(pat in blob for pat in patterns):
            return p.path
    return None


def _try_sound(backend) -> bool:
    try:
        backend.alert_sound(0)          # level 0：不该真的响
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
