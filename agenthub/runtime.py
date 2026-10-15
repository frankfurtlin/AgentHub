# -*- coding: utf-8 -*-
"""状态中枢。

干四件事：

    1. 定时扫描——谁在跑、窗口在哪、CPU 有没有在动
    2. 合并两条判断通道：系统通知 + 界面探针
    3. 算出每个工具的「状态 + 告警级别」，交给界面渲染
    4. 发现「从不在等 变成 在等」的那个边沿时，触发一次告警

平台相关的部分全部通过 host.Backend 走，这里不 import 任何 Win32 / Cocoa。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import replace

from .config import Config
from .host import Backend, get_backend
from .models import AGENTS, KIND_APPROVAL, KIND_DONE, AgentState, Notice, AgentDef


# ---------------------------------------------------------------- CPU 活跃度

class CpuMeter:
    """用滑动窗口的 CPU 均值判断某个工具是不是真的在输出。

    不用瞬时占用：Electron 应用即使闲置，后台索引和动画也会偶发 20~70% 的
    尖峰。真的在流式输出时 CPU 会连续多秒维持高位，闲置尖峰撑不过一个窗口。
    """

    BUSY_PERCENT = 25.0     # 窗口均值要超过单核的 25%
    BUSY_FLOOR = 15.0       # 且窗口内至少 BUSY_HITS 个采样本身要高于 15%
    BUSY_HITS = 3
    WINDOW = 4              # 保留最近 4 次采样（约 6 秒）
    MIN_SAMPLES = 4         # 采样不足一个窗口时不下结论，避免刚启动就闪

    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self._prev: dict[int, float] = {}
        self._prev_at = 0.0
        self._history: dict[str, deque] = {}
        self._busy: dict[str, bool] = {}

    def measure(self, groups: dict[str, list[int]]) -> dict[str, float]:
        """采样并返回 {agent key: 占单核百分比}。"""
        now = time.perf_counter()
        current: dict[int, float] = {}
        for pids in groups.values():
            for pid in pids:
                value = self.backend.cpu_seconds(pid)
                if value is not None:
                    current[pid] = value

        delta = now - self._prev_at if self._prev_at else 0.0
        result: dict[str, float] = {}

        if delta > 0.0 and self._prev:
            for key, pids in groups.items():
                used = sum(current[pid] - self._prev[pid]
                           for pid in pids if pid in current and pid in self._prev)
                percent = max(0.0, used / delta * 100.0)
                result[key] = percent
                history = self._history.setdefault(key, deque(maxlen=self.WINDOW))
                history.append(percent)
                self._busy[key] = self._evaluate(history)
        else:
            # 第一次采样没有基准，只能记下来等下一轮
            for key in groups:
                result[key] = 0.0

        self._prev = current
        self._prev_at = now
        return result

    def _evaluate(self, history: deque) -> bool:
        """均值够高，且不是单根尖峰撑起来的——两个条件同时满足才算在输出。"""
        if len(history) < self.MIN_SAMPLES:
            return False
        mean = sum(history) / len(history)
        hits = sum(1 for value in history if value >= self.BUSY_FLOOR)
        return mean >= self.BUSY_PERCENT and hits >= self.BUSY_HITS

    def busy(self, key: str) -> bool:
        return self._busy.get(key, False)


# ---------------------------------------------------------------- 界面探针调度

class UiProbeWatcher:
    """后台读窗口界面，判断有没有正摆着审批条。

    为什么不放在主循环里：一次探针要 80ms 上下，塞进定时回调会让界面卡出
    可感知的顿挫。放后台线程，主循环只读结果。

    只有 `ui_probe=True` 的工具会被探——也就是通知通道已经证明不可用的
    CodeBuddy 和 Qoder。其余工具继续走通知，没必要让它们的界面平白背上
    可访问性树的维护开销。
    """

    def __init__(self, backend: Backend, interval: float = 2.5) -> None:
        self.backend = backend
        self.interval = interval
        self._targets: dict[str, int] = {}
        self._active: set[str] = set()
        self._results: dict[str, bool] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopped = False
        self.probes = 0          # 探针累计次数，便于自检观察

    # ---------------- 主线程侧

    def set_targets(self, targets: dict[str, int], active=None) -> None:
        """targets：这一轮真正要读界面的窗口（跑着且没最小化）。

        active：这些工具「正在运行」的集合，比 targets 宽——最小化的也算。
        两者的差集就是「暂时读不到界面、但上次的结论还有效」的那批。
        """
        with self._lock:
            changed = targets != self._targets
            self._targets = dict(targets)
            if active is not None:
                changed = changed or set(active) != self._active
                self._active = set(active)
        if changed:
            self._wake.set()

    def result(self, key: str) -> bool:
        with self._lock:
            return self._results.get(key, False)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopped = False
        self._thread = threading.Thread(
            target=self._run, name="agenthub-probe", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped = True
        self._wake.set()

    # ---------------- 合并语义（自检会直接调，不开线程也不碰 COM）

    def settle(self, targets: dict, fresh: dict, active: set) -> None:
        """把这一轮没读完的键并回来。

        规则两条：工具还在跑（在 active 里）但这一轮没探（最小化了 / 被挡住）
        → 沿用上次结论；工具已经不在了 → 结论作废。后者漏了的话，关掉
        CodeBuddy 之后那条「待审批」会一直挂着，点了也没反应。
        """
        for key in list(self._results):
            if key not in targets and key in active:
                fresh[key] = self._results[key]
        self._results = fresh

    # ---------------- 后台线程侧

    def _run(self) -> None:
        session = self.backend.ui_session()
        if not session.ok:
            return
        try:
            while not self._stopped:
                with self._lock:
                    targets = dict(self._targets)
                fresh: dict[str, bool] = {}
                for key, handle in targets.items():
                    fresh[key] = session.probe(handle)
                    self.probes += 1
                with self._lock:
                    self.settle(targets, fresh, set(self._active))
                self._wake.wait(self.interval)
                self._wake.clear()
        finally:
            session.close()


# ---------------------------------------------------------------- 状态中枢

class Runtime:
    """扫描、汇总、判级。界面只跟它打交道。"""

    #: 没跑起来也能算「主窗口」的最低分——用来挡掉那些无标题的辅助窗口
    def __init__(self, backend: Backend | None = None, config: Config | None = None) -> None:
        self.backend = backend or get_backend()
        self.config = config or Config()
        self.cpu = CpuMeter(self.backend)
        self.probe = UiProbeWatcher(
            self.backend,
            interval=float(self.config.get("probeIntervalS") or 2.5),
        )
        self.states: dict[str, AgentState] = {}
        self._exe_paths: dict[str, str] = {}
        self._started: dict[str, float] = {}   # agent key -> 进程启动时刻（排序用）
        self._primed = False
        self._first_scan = True
        self._on_alert = None        # fn(level: int, names: list[str])

    # ---------------- 生命周期

    def start(self) -> None:
        self.probe.start()

    def stop(self) -> None:
        self.probe.stop()

    def on_alert(self, callback) -> None:
        """注册告警回调：fn(level, names)。level 2 = 有人在等你确认。"""
        self._on_alert = callback

    # ---------------- 扫描

    def _group_processes(self, processes):
        """按 exe 名把进程分到各个工具名下，顺带记下 exe 全路径和启动时刻。

        Electron 应用是多进程的（主进程 + 若干 renderer + gpu），它们的 exe
        名相同，所以会一起收进来——CPU 活跃度判定正好需要看全部。
        启动时刻取同名进程里**最早**的那个（主进程总是先起，helper 在后），
        面板排序用：运行中的按它从早到晚排。
        """
        groups: dict[str, list[int]] = {agent.key: [] for agent in AGENTS}
        paths: dict[str, str] = {}
        started: dict[str, float] = {}

        for proc in processes:
            name = (proc.name or "").lower()
            if not name:
                continue
            for agent in AGENTS:
                if not _matches_process(name, agent.match):
                    continue
                groups[agent.key].append(proc.pid)
                if proc.path:
                    paths.setdefault(agent.key, proc.path)
                if proc.started > 0:
                    prev = started.get(agent.key)
                    if prev is None or proc.started < prev:
                        started[agent.key] = proc.started
        return groups, paths, started

    def _score_window(self, win) -> int:
        """给候选窗口打分，挑出最像「主窗口」的那个。"""
        score = 0
        if win.visible:
            score += 8
        if win.title:
            score += 10
        if " - " in win.title or len(win.title) > 3:
            score += 2
        if not self.backend.is_minimized(win.handle):
            score += 5
        if win.class_name.startswith("Chrome_WidgetWin"):
            score += 3
        return score

    def refresh(self) -> dict:
        """扫一轮，返回给界面用的快照。"""
        processes = self.backend.processes()
        groups, paths, started = self._group_processes(processes)
        self._exe_paths.update(paths)
        # started 里只有这轮还在跑的工具；直接整体替换，退出的自然剔除，
        # 排序落回 AGENTS 定义顺序
        self._started = dict(started)
        cpu = self.cpu.measure(groups)

        # 连隐藏窗口一起拿，否则应用缩到托盘后就切不过去了
        windows = self.backend.windows(visible_only=False)

        fresh: dict[str, AgentState] = {}
        targets: dict[str, int] = {}
        active: set[str] = set()

        for agent in AGENTS:
            state = AgentState(definition=agent)
            old = self.states.get(agent.key)
            if old:
                # 这三个字段是跨轮累积的，扫描不产出，得继承
                state.unread = old.unread
                state.approvals = list(old.approvals)
                state.notice = old.notice
                state.orders = list(old.orders)

            pids = groups.get(agent.key) or []
            state.cpu = cpu.get(agent.key, 0.0)
            state.busy = self.cpu.busy(agent.key)
            if pids:
                state.running = True
                state.pid = pids[0]
                pid_set = set(pids)
                candidates = [win for win in windows if win.pid in pid_set]
                if candidates:
                    best = max(candidates, key=self._score_window)
                    state.handle = best.handle
                    state.visible = best.visible
                    state.minimized = self.backend.is_minimized(best.handle)

            state.ui_approval = self.probe.result(agent.key)

            # 只有「跑着、有窗口、能看见、没最小化」才谈得上去读它的界面
            if agent.ui_probe and state.running:
                active.add(agent.key)
                if state.handle and state.visible and not state.minimized:
                    targets[agent.key] = state.handle

            fresh[agent.key] = state

        self.probe.set_targets(targets, active)

        # 边沿①：界面探针刚发现待审批
        newly_waiting = [
            state.name for key, state in fresh.items()
            if state.ui_approval and not self.states.get(key, state).ui_approval
        ]

        # 边沿②：通知里出现了新的审批请求
        notices = self.backend.poll_notices() if self._primed \
            else self.backend.prime_notices()
        self._primed = True
        newly_waiting.extend(self._merge_notices(fresh, notices))

        self.states = fresh

        # 首次扫描不响铃——用户刚打开程序，界面还没渲染完，响一声反而莫名其妙；
        # 状态照常显示，看得见就行。第二次开始正常告警。
        if self._first_scan:
            self._first_scan = False
        elif newly_waiting and self._on_alert:
            self._on_alert(2, sorted(set(newly_waiting)))

        return self.snapshot()

    def _merge_notices(self, states: dict[str, AgentState], notices) -> list[str]:
        """把通知并进状态，返回其中「首次出现待审批」的工具名。"""
        appeared: list[str] = []

        for notice in notices:
            state = states.get(notice.agent_key)
            if state is None or notice.order in state.orders:
                continue

            state.orders.append(notice.order)
            state.orders = state.orders[-200:]      # 只留最近 200 条，防无限增长

            if notice.kind == KIND_APPROVAL:
                if not state.approvals:
                    appeared.append(state.name)
                state.approvals.append(notice)
                state.notice = notice
            elif notice.kind == KIND_DONE:
                state.unread += 1
                state.notice = notice
            else:
                state.notice = notice

        return appeared

    # ---------------- 给界面

    def snapshot(self) -> dict:
        # 排序：跑着的在上（按进程实际启动时刻从早到晚，先启动的先显示），
        # 没跑的沉底，维持 AGENTS 里的定义顺序。
        def _order(item: tuple[str, AgentState]) -> tuple:
            key, state = item
            if state.running:
                return (0, self._started.get(key, float("inf")))
            return (1, 0.0)

        ordered = sorted(self.states.items(), key=_order)
        agents = [state.as_dict() for _, state in ordered]
        running = sum(1 for state in self.states.values() if state.running)
        waiting = sum(1 for state in self.states.values() if state.waiting)
        unread = sum(1 for state in self.states.values() if state.level == 1)

        return {
            "agents": agents,
            "totals": {"running": running, "waiting": waiting, "unread": unread},
            "capabilities": self.backend.capabilities(),
            "at": time.time(),
        }

    def activate(self, key: str) -> bool:
        """把某个工具的窗口切到前台。切过去就当作已读。"""
        state = self.states.get(key)
        if not state or not state.running or not state.handle:
            return False
        ok = self.backend.activate(state.handle)
        if ok:
            state.unread = 0
            state.approvals.clear()     # 交给下一轮扫描重新判定
        return ok

    def icon(self, key: str, size: int = 64) -> bytes | None:
        """取某个工具的应用图标（透明底 PNG）。"""
        path = self._exe_paths.get(key) or self.config.get("exePaths", {}).get(key, "")
        if not path:
            return None
        try:
            return self.backend.icon_png(path, size)
        except Exception:
            return None


# ---------------------------------------------------------------- 小工具

def _matches_process(name: str, patterns: tuple[str, ...]) -> bool:
    """进程名是否属于某个工具。

    Electron 除了主进程还会派生一堆同名的 helper，名字形如
    `codebuddy cn helper`；macOS 上则可能是 `xxx-helper`。所以除了全等，
    还认「模式 + 分隔符」开头。
    """
    for pattern in patterns:
        if name == pattern:
            return True
        if name.startswith(pattern + " ") or name.startswith(pattern + "-"):
            return True
    return False
