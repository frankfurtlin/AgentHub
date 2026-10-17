# -*- coding: utf-8 -*-
"""测试用的假后端。

`agenthub.host.base.Backend` 是个抽象骨架，真正干活的是 `host/windows` 那一套，
而它一实例化就要碰系统 API。这里只填状态判定需要的最小接口，这样「只显示已装
的工具」「完成通知作废审批」这类纯逻辑就能脱离桌面环境验证。
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agenthub.config import Config                                  # noqa: E402
from agenthub.host.base import Backend, ProcessInfo                 # noqa: E402
from agenthub.models import KIND_APPROVAL, KIND_DONE, Notice        # noqa: E402
from agenthub.runtime import Runtime                               # noqa: E402

#: 每种通知类型造一条的典型文案，和 models.classify 的判据对应
SAMPLE_TITLES = {
    KIND_APPROVAL: "任务等待确认，请处理",
    KIND_DONE: "任务已完成",
    "info": "已连接到远程服务",
}


def notice(kind: str, order: int = 1, agent_key: str = "workbuddy",
           title: str | None = None, body: str = "", at: float = 0.0) -> Notice:
    """造一条通知。title 不传就用该类型的典型文案。"""
    return Notice(
        order=order,
        agent_key=agent_key,
        kind=kind,
        title=title if title is not None else SAMPLE_TITLES[kind],
        body=body,
        at=at,
    )


def temp_config() -> Config:
    """一个落在临时目录里的配置，免得测试污染用户真实的 config.json。"""
    return Config(path=os.path.join(tempfile.mkdtemp(prefix="agenthub-test-"),
                                    "config.json"))


class FakeBackend(Backend):
    """一个不碰系统的后端：进程清单、已装应用、通知都由调用方指定。"""

    name = "fake"

    def __init__(self, apps: list[str] | None = None,
                 procs: list[ProcessInfo] | None = None,
                 notices: list[Notice] | None = None) -> None:
        self._apps = apps
        self._procs = procs if procs is not None else []
        self._notices = notices if notices is not None else []

    # ---------------- 进程 / 窗口 / 资源 ----------------

    def processes(self) -> list[ProcessInfo]:
        return list(self._procs)

    def windows(self, visible_only: bool = True) -> list:
        return []

    def is_minimized(self, handle: int) -> bool:
        return False

    def activate(self, handle: int) -> bool:
        return False

    def icon_png(self, exe_path: str, size: int):
        return None

    def caps(self) -> dict:
        return {}

    def config_dir(self) -> str:
        return os.path.dirname(tempfile.gettempdir())

    def alert_sound(self, level: int = 2) -> None:
        return None

    # ---------------- 装机检测 / 通知 ----------------

    def installed_apps(self) -> list[str] | None:
        return self._apps

    def poll_notices(self) -> list[Notice]:
        return list(self._notices)

    def prime_notices(self) -> list[Notice]:
        return list(self._notices)

    # ---------------- 窗口操作（一律「不支持」，走代码的退路分支） ----------------

    def make_tool_window(self, native) -> bool:
        return False

    def begin_window_drag(self, native) -> bool:
        return False

    def window_pos(self, native):
        return None

    def place_window(self, native, x: int, y: int) -> bool:
        return False

    def work_area(self):
        return None

    def attach_follow(self, source_native, target_native, sizes) -> bool:
        return False

    def detach_follow(self) -> None:
        return None

    def sync_follow(self) -> None:
        return None

    def screen_scale(self) -> float:
        return 1.0


def make_runtime(apps=None, procs=None, notices=None) -> Runtime:
    """一个不碰系统的 Runtime，配置落在临时目录。"""
    return Runtime(backend=FakeBackend(apps, procs, notices),
                   config=temp_config())


def run_all(module) -> int:
    """跑一个模块里所有 `test_` 开头的函数，返回退出码。

    有 pytest 时直接 `pytest tests/` 就行；没有也能 `python tests/test_x.py`
    单独跑——不为一堆断言给项目加一个测试框架的依赖。
    """
    names = [name for name in sorted(vars(module)) if name.startswith("test_")]
    failed = 0
    for name in names:
        try:
            getattr(module, name)()
            print("  通过  %s" % name, flush=True)
        except AssertionError as exc:
            failed += 1
            print("  失败  %s: %s" % (name, exc), flush=True)
    print("%d 个用例，%d 个失败" % (len(names), failed), flush=True)
    return 1 if failed else 0
