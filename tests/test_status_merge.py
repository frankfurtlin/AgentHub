# -*- coding: utf-8 -*-
"""通知并入状态时，各类型怎么处理。

核心是那条回归用例：收到「任务已完成」之后，之前攒的「等你确认」必须作废。
不清的话卡片会一直挂着「待审批」，点过去却发现没东西可批（实测踩过）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agenthub.models import (KIND_APPROVAL, KIND_DONE, KIND_INFO, classify,  # noqa: E402
                             parse_toast)
from helpers import make_runtime, notice                                     # noqa: E402


def _merge(runtime, notices):
    states = runtime.states
    return runtime._merge_notices(states, notices)


def test_approval_marks_waiting():
    runtime = make_runtime(["WorkBuddy"])
    runtime.refresh()
    state = runtime.states["workbuddy"]

    _merge(runtime, [notice(KIND_APPROVAL, order=1)])

    assert state.waiting
    assert state.level == 2
    assert len(state.approvals) == 1


def test_done_clears_pending_approval():
    """完成通知必须作废先前的审批，并把卡片翻成「有新结果」。"""
    runtime = make_runtime(["WorkBuddy"])
    runtime.refresh()
    state = runtime.states["workbuddy"]

    _merge(runtime, [notice(KIND_APPROVAL, order=1)])
    assert state.waiting, "前置条件：先要处于待审批"

    _merge(runtime, [notice(KIND_DONE, order=2)])

    assert not state.waiting, "任务都跑完了，卡片还挂着待审批"
    assert state.approvals == []
    assert state.unread == 1
    assert state.level == 1


def test_approval_after_done_is_a_new_round():
    """完成之后的审批是新一轮，不能因为 unread 非零就吞掉。"""
    runtime = make_runtime(["WorkBuddy"])
    runtime.refresh()
    state = runtime.states["workbuddy"]

    _merge(runtime, [notice(KIND_DONE, order=1)])
    appeared = _merge(runtime, [notice(KIND_APPROVAL, order=2)])

    assert appeared == ["WorkBuddy"], "新一轮审批应当重新触发告警"
    assert state.waiting
    assert state.unread == 1


def test_same_notice_is_not_counted_twice():
    """同一条通知重复喂进来，不该重复累加。"""
    runtime = make_runtime(["WorkBuddy"])
    runtime.refresh()
    state = runtime.states["workbuddy"]

    first = _merge(runtime, [notice(KIND_DONE, order=7)])
    again = _merge(runtime, [notice(KIND_DONE, order=7)])

    assert first == []
    assert again == []
    assert state.unread == 1, "同一条通知被数了两次"


def test_notice_for_untracked_agent_is_ignored():
    runtime = make_runtime(["WorkBuddy"])
    runtime.refresh()
    assert _merge(runtime, [notice(KIND_APPROVAL, agent_key="不存在的工具")]) == []


def test_classify():
    """各家自己的文案，中英都要认得出来。"""
    cases = [
        ("任务等待确认，请处理", KIND_APPROVAL),
        ("CodeBuddy 正在等待您的回复", KIND_APPROVAL),
        ("Tool Permission Required", KIND_APPROVAL),
        ("任务已完成", KIND_DONE),
        ("Task has been completed successfully", KIND_DONE),
        ("已连接到远程服务", KIND_INFO),
    ]
    for title, expected in cases:
        got = classify(title, "")
        assert got == expected, "classify(%r) => %r，应当 %r" % (title, got, expected)


def test_approval_wins_over_done_in_the_same_text():
    """「等待确认」和「完成」同时出现时，以「要你出手」为准。"""
    assert classify("任务已完成，等待确认", "") == KIND_APPROVAL


def test_parse_toast():
    raw = ('<toast><visual><binding template="ToastText02">'
           '<text id="1">标题</text><text id="2">正文内容</text>'
           '</binding></visual></toast>')
    assert parse_toast(raw) == ("标题", "正文内容")


def test_parse_toast_tolerates_garbage():
    assert parse_toast(None) == ("", "")
    assert parse_toast(b"\xff\xfe not xml") == ("", "")


if __name__ == "__main__":              # 有 pytest 就用 pytest，没有就直接跑
    from helpers import run_all
    sys.exit(run_all(sys.modules[__name__]))
