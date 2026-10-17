# -*- coding: utf-8 -*-
"""面板卡片的顺序：跑着的在上，按进程启动时刻从早到晚；没跑的沉底。

同时跑四五个工具时，你关心的是「哪个在等我」而不是「哪个字母靠前」。
顺序要稳定，不能每 1.5 秒扫描一次就跳一次。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agenthub.host.base import ProcessInfo                     # noqa: E402
from agenthub.models import AGENTS_BY_KEY                      # noqa: E402
from helpers import make_runtime                               # noqa: E402


def _proc(name: str, started: float, pid: int = 1) -> ProcessInfo:
    return ProcessInfo(pid=pid, name=name, path="", started=started)


def _names(snapshot) -> list[str]:
    return [agent["name"] for agent in snapshot["agents"]]


def test_running_agents_come_first():
    """跑着的一律排在没跑的前面。"""
    runtime = make_runtime(
        apps=["Trae CN", "Qoder CN", "Claude", "Codex"],
        procs=[_proc("Claude.exe", started=100.0, pid=1),
               _proc("Codex.exe", started=200.0, pid=2)],
    )
    names = _names(runtime.refresh())

    assert names[:2] == ["Claude", "Codex"]
    assert set(names) == {"Claude", "Codex", "Trae CN", "Qoder CN"}


def test_running_agents_sorted_by_start_time():
    """跑着的之间按启动时刻从早到晚，先启动的先显示。"""
    # 故意把进程顺序倒着给：判定不能依赖枚举顺序
    runtime = make_runtime(
        apps=["Trae CN", "Qoder CN", "Claude", "Codex"],
        procs=[_proc("Codex.exe", started=300.0, pid=3),
               _proc("Claude.exe", started=100.0, pid=1),
               _proc("Trae CN.exe", started=200.0, pid=2)],
    )
    assert _names(runtime.refresh())[:3] == ["Claude", "Trae CN", "Codex"]


def test_idle_agents_keep_definition_order():
    """没跑的维持 AGENTS 里的定义顺序，别跟着进程枚举乱跳。"""
    runtime = make_runtime(apps=["Claude", "Codex", "Trae CN"])
    names = _names(runtime.refresh())

    expected = [key for key in AGENTS_BY_KEY if key in {"claude", "codex", "trae"}]
    pretty = [AGENTS_BY_KEY[key].name for key in expected]
    assert names == pretty


def test_no_tools_detected_shows_all():
    """装机检测查不到东西时不过滤，全显示——宁可多，不可空。"""
    runtime = make_runtime(apps=[], procs=[])
    assert len(_names(runtime.refresh())) == len(AGENTS_BY_KEY)


def test_totals_match_shown_cards():
    """合计数字只对展示出来的卡片统计，被过滤掉的不算。"""
    runtime = make_runtime(
        apps=["Claude", "Codex"],
        procs=[_proc("Claude.exe", started=100.0, pid=1)],
    )
    totals = runtime.refresh()["totals"]
    assert totals == {"running": 1, "waiting": 0, "unread": 0}


if __name__ == "__main__":              # 有 pytest 就用 pytest，没有就直接跑
    from helpers import run_all
    sys.exit(run_all(sys.modules[__name__]))
