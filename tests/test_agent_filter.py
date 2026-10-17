# -*- coding: utf-8 -*-
"""只显示「这台机器上装了 / 正在跑」的工具。

七家里装三家，面板就不该出现另外四张永远灰着的卡片。这里验证过滤规则本身，
不需要真的装什么。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agenthub.host.base import ProcessInfo                     # noqa: E402
from agenthub.models import AGENTS, match_installed            # noqa: E402
from helpers import make_runtime                               # noqa: E402


def _names(snapshot) -> list[str]:
    return [agent["name"] for agent in snapshot["agents"]]


def test_only_installed_agents_show():
    """只装了 WorkBuddy 和 Qoder，就只出现这两个。"""
    runtime = make_runtime(["WorkBuddy 5.8.2", "Qoder CN 0.4.8", "某无关软件"])
    names = _names(runtime.refresh())

    assert set(names) == {"WorkBuddy", "Qoder CN"}, names
    assert len(names) == 2


def test_unknown_platform_shows_everything():
    """后端查不到应用清单时不过滤——宁可全显示，也别让面板空成一片。"""
    runtime = make_runtime(apps=None)
    assert len(_names(runtime.refresh())) == len(AGENTS)


def test_running_process_counts_as_installed():
    """正在跑的一律算已装：绿色版、CLI 都不会留下安装痕迹。"""
    runtime = make_runtime(
        apps=["Trae CN"],                                  # 装机检测只认出 Trae
        procs=[ProcessInfo(pid=101, name="Codex.exe",      # 但 Codex 正在跑
                           path="", started=100.0)],
    )
    names = _names(runtime.refresh())

    assert "Codex" in names, "Codex 正在跑，不该被过滤掉"
    assert set(names) == {"Codex", "Trae CN"}


def test_empty_scan_falls_back_to_all():
    """扫到 0 个应用 = 查不到，全显示。

    和「这台机器真没装 AI 工具」在代码里长得一样，区分不了。严格过滤会让
    面板空成一片，比多显示几张灰卡糟得多，所以统一兜底。
    """
    runtime = make_runtime(apps=[], procs=[])
    assert len(_names(runtime.refresh())) == len(AGENTS)


def test_scan_result_is_cached():
    """扫过一次就别反复扫：空结果也要进缓存。

    注册表 + 开始菜单有几百个条目，跟着主循环 1.5 秒扫一遍纯属白烧 CPU。
    """
    runtime = make_runtime(apps=[], procs=[])
    runtime.refresh()

    calls = {"n": 0}
    original = runtime.backend.installed_apps

    def counting():
        calls["n"] += 1
        return original()

    runtime.backend.installed_apps = counting
    for _ in range(20):
        runtime.refresh()

    assert calls["n"] == 0, "TTL 内不该重复扫描（装了 %d 次）" % calls["n"]


def test_match_installed_is_case_insensitive():
    keys = match_installed(["CLAUDE_2.42304.0.0_x64__pzs8sxrjxfjjc"])
    assert keys == {"claude"}


def test_match_installed_ignores_unrelated_apps():
    assert match_installed(["微信", "Notion", "7-Zip"]) == set()


def test_match_installed_tolerates_empty_entries():
    """应用名清单里混进 None / 空串不该让检测崩掉。"""
    assert match_installed([None, "", "Trae CN"]) == {"trae"}


if __name__ == "__main__":              # 有 pytest 就用 pytest，没有就直接跑
    from helpers import run_all
    sys.exit(run_all(sys.modules[__name__]))
