# -*- coding: utf-8 -*-
"""pywebview 外壳：两个无边框透明窗口 + 暴露给前端的 JS 桥。

两个窗口：
    球（76×76）    常驻桌面，显示汇总状态；可拖动、点击展开面板
    面板（344×492） 四个工具的状态卡片；默认隐藏，点球才出来

为什么用 pywebview 而不是 Electron / PySide6：
    安装包小得多。它复用系统自带的 WebView2（Windows）和 WKWebView（macOS），
    这两样用户机器上本来就有，不用跟着安装包一起发。界面则交给 HTML/CSS 做，
    抗锯齿、圆角、阴影、毛玻璃全是浏览器原生能力——旧 tkinter 版最头疼的
    「圆边有锯齿」「图标要按主题预乘」这类问题在这里根本不存在。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time

import webview

from . import webview_env
from .config import Config
from .runtime import Runtime

# ---------------------------------------------------------------- 尺寸与间距

BALL = 76          # 悬浮球直径
PANEL_W = 344      # 面板宽
PANEL_H = 492      # 面板高
MARGIN = 24        # 球离屏幕边缘的默认距离
GAP = 12           # 面板与球之间的间隙
POS_SAVE_EVERY = 4  # 每几轮扫描记一次球的位置（拖动后要记住）
SETTLE_SECONDS = 6.0  # 启动后盯着面板、别让它自己冒出来的时长（见 _guard_panel_hidden）


def web_dir() -> str:
    """前端资源目录。打包成单文件 exe 后资源会被解到 _MEIPASS。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "web")


def _page(view: str) -> str:
    """给某个视图拼出本地文件地址。

    两个窗口共用同一份 index.html，用 `#hash` 区分渲染哪一套。

    ⚠️ 这里必须传**文件路径**，不能传 `file://` URI：pywebview 只对文件路径
    启动内置的本地 HTTP 服务（它用 `is_local_url()` 判断，而那个函数明确把
    `file://` 开头的情况排除在外）。走 file:// 会绕开服务直接交给 WebView，
    结果就是白屏——踩过一次。
    """
    return os.path.join(web_dir(), "index.html") + "#" + view


# ---------------------------------------------------------------- JS 桥

class Bridge:
    """暴露给前端的接口，前端用 `window.pywebview.api.xxx()` 调。

    注意这些方法是在 pywebview 的工作线程里执行的，**不是 GUI 线程**，
    所以都不能长时间阻塞。

    `_app` 必须是私有属性（下划线开头）：pywebview 生成 JS 侧代理时会遍历
    js_api 对象的**所有公开属性**并递归下去，而 `app.ball.native` 是 WinForms
    窗体对象、属性互相引用，一递归就是无穷深，日志会被刷爆。
    """

    def __init__(self, app: "AgentHubApp") -> None:
        self._app = app

    # ---------------- 数据 ----------------

    def snapshot(self) -> dict:
        return self._app.runtime.snapshot()

    def capabilities(self) -> dict:
        return self._app.runtime.backend.capabilities()

    def config(self) -> dict:
        return self._app.runtime.config.as_dict()

    def icon(self, key: str, size: int = 64) -> str:
        """返回图标的 data URI；拿不到时返回空串，前端用兜底字符顶上。"""
        blob = self._app.runtime.icon(key, int(size))
        if not blob:
            return ""
        return "data:image/png;base64," + base64.b64encode(blob).decode("ascii")

    # ---------------- 交互 ----------------

    def activate(self, key: str) -> bool:
        """把某个工具的窗口切到前台。"""
        return self._app.runtime.activate(key)

    def toggle_panel(self) -> bool:
        return self._app.toggle_panel()

    def hide_panel(self) -> None:
        self._app.hide_panel()

    def begin_drag(self) -> bool:
        """让系统接管悬浮球的拖动。返回 False 时前端退回自己算位移。"""
        return self._app.begin_drag()

    def ball_pos(self) -> list:
        return self._app.ball_pos()

    def move_ball(self, x: int, y: int) -> bool:
        return self._app.move_ball(x, y)

    def save_ball_pos(self) -> bool:
        return self._app.remember_ball_pos()

    def set_config(self, key: str, value) -> bool:
        self._app.runtime.config.set(key, value, save=True)
        return True

    def quit(self) -> None:
        self._app.quit()


# ---------------------------------------------------------------- 应用

class AgentHubApp:
    def __init__(self, runtime: Runtime | None = None, config: Config | None = None) -> None:
        self.config = config or Config()
        self.runtime = runtime or Runtime(config=self.config)
        self.bridge = Bridge(self)
        self.ball: webview.Window | None = None
        self.panel: webview.Window | None = None
        self._stop = threading.Event()
        self._ticks = 0
        # 自己记面板的显隐：pywebview 的 window.hidden 是异步读的，
        # show() 之后立刻读还可能拿到旧值，据此判断会来回抽风。
        self._panel_shown = False

    # ---------------- 窗口

    def _screen(self):
        try:
            screens = webview.screens
        except Exception:
            return None
        return screens[0] if screens else None

    def _ball_pos(self, screen) -> tuple[int, int]:
        """球的初始位置。

        配置里有就用配置的；没有就等 GUI 起来后再挪到右下角——`webview.screens`
        要在窗口系统初始化之后才有值，所以那一步不能在这里做，见
        `_place_ball_default()`。
        """
        saved = self.config.get("ballPos")
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            try:
                return int(saved[0]), int(saved[1])
            except (TypeError, ValueError):
                pass
        return 60, 60

    def build(self) -> "AgentHubApp":
        screen = self._screen()
        x, y = self._ball_pos(screen)

        # min_size 必须显式给：pywebview 默认 (200, 100)，会把 76×76 的球撑到
        # 200×100——透明窗口看不见边框，但多出来的那片区域照样吃鼠标事件。
        self.ball = webview.create_window(
            "AgentHub", _page("ball"),
            width=BALL, height=BALL, min_size=(BALL, BALL), x=x, y=y,
            frameless=True, transparent=True, on_top=True,
            resizable=False, shadow=False,
            js_api=self.bridge,
        )
        self.panel = webview.create_window(
            "AgentHub", _page("panel"),
            width=PANEL_W, height=PANEL_H, min_size=(PANEL_W, PANEL_H), x=x, y=y,
            frameless=True, transparent=True, on_top=True,
            resizable=False, shadow=False, hidden=True,
            js_api=self.bridge,
        )
        return self

    # ---------------- 运行

    def run(self, debug: bool = False) -> None:
        # 顺序不能动：环境要先摆好，再开窗口，最后进事件循环。
        # 详见 webview_env 模块开头——少这一步 WebView2 会白屏。
        options = webview_env.start_options(self.config, self.runtime.backend.config_dir())
        self.build()
        self.runtime.on_alert(self._on_alert)
        self.runtime.start()
        webview.start(self._loop, debug=debug, **options)

    def quit(self) -> None:
        self._stop.set()
        try:
            for win in (self.ball, self.panel):
                if win is not None:
                    win.destroy()
        except Exception:
            pass

    def _loop(self) -> None:
        """后台扫描循环。跑在 pywebview 起的子线程里，不能碰 GUI。"""
        interval = max(0.4, float(self.config.get("refreshMs") or 1500) / 1000.0)
        first = True
        while not self._stop.is_set():
            if first:
                first = False
                self._settle_windows()
                self._place_ball_default()
            try:
                snapshot = self.runtime.refresh()
                self._push(snapshot)
            except Exception as exc:                      # 单轮失败不该拖垮循环
                _log("扫描失败：%r" % exc)
            self._ticks += 1
            if self._ticks % POS_SAVE_EVERY == 0:
                self.remember_ball_pos()
            self._stop.wait(interval)

    def _settle_windows(self) -> None:
        """窗口建好之后的第一次摆正。两件事都得等 GUI 起来（要有窗口句柄）才能做。

        一、校正悬浮球的尺寸
            `create_window(width=..., height=...)` 给的尺寸**不能全信**：WinForms
            建窗体时带着 `AutoScaleMode.Dpi`，初始 `Size` 会被 DPI 自动缩放再揉一遍，
            结果比设计值小十几像素。`resize()` 走 `SetWindowPos`，逻辑像素 × DPI
            比例直接算，没有那层 AutoScale，所以拿它当准。

            ⚠️ 这里**只管球**。`resize()` 的 flag 是 `64`，也就是 `SWP_SHOWWINDOW`
            ——它会顺手把窗口显示出来。面板默认隐藏，在主循环里校正它等于每次启动
            都把面板弹一次。面板的校正放在 `_place_panel()` 里，那时本来就该显示它。

        二、把面板按下去
            pywebview 的 `hidden=True` 对**透明 + 无边框**的窗口不生效：实测无论
            置不置顶，窗口建完就是可见的（`IsWindowVisible` 为真）。

            而且不能只压一次——窗口创建是并行的，`_loop` 第一次跑起来时面板
            可能还没建完，这时 `hide()` 会被随后的 `Show()` 覆盖。所以交给
            `_guard_panel_hidden()` 在一小段时间里持续看着。

            注意别用 `hide_panel()`：它按 `_panel_shown` 判断，而那个标志一开始
            就是 `False`，会直接返回什么都不做。
        """
        if self.ball is not None:
            try:
                self.ball.resize(BALL, BALL)
            except Exception as exc:
                _log("校正悬浮球尺寸失败：%r" % exc)

        threading.Thread(target=self._guard_panel_hidden, daemon=True).start()

    def _guard_panel_hidden(self) -> None:
        """启动后一小段时间里，没人要求显示就把面板压回去。

        `hide()` 内部要等窗口的 `shown` 事件，窗口还没就绪时会阻塞——
        所以单开一条线程，别拖住扫描循环。
        """
        deadline = time.monotonic() + SETTLE_SECONDS
        while time.monotonic() < deadline and not self._stop.is_set():
            if self._panel_shown:
                return
            try:
                self.panel.hide()
            except Exception:
                pass                                # 窗口还没就绪，下一轮再来
            time.sleep(0.5)

    def _place_ball_default(self) -> None:
        """首次启动时把球摆到右下角。

        屏幕尺寸要等窗口系统初始化完才有值，而窗口是在 `webview.start()`
        之前创建的，所以这一步只能放在循环的第一轮。
        """
        if self.config.get("ballPos") or self.ball is None:
            return
        screen = self._screen()
        if screen is None:
            return
        try:
            self.ball.move(int(screen.width - BALL - MARGIN),
                           int(screen.height - BALL - MARGIN - 56))
        except Exception as exc:
            _log("初始定位失败：%r" % exc)

    def _push(self, snapshot: dict, alert: dict | None = None) -> None:
        """把状态推给两个窗口的前端。"""
        payload = json.dumps({"snapshot": snapshot, "alert": alert},
                             ensure_ascii=False)
        for win in (self.ball, self.panel):
            if win is None:
                continue
            try:
                win.evaluate_js(
                    "window.AgentHub && window.AgentHub.receive(%s)" % payload)
            except Exception:
                pass

    def _on_alert(self, level: int, names: list[str]) -> None:
        _log("需要你出手（级别 %d）：%s" % (level, "、".join(names)))
        if self.config.get("sound"):
            # 响一下是阻塞的，扔后台线程，别拖住扫描循环
            threading.Thread(
                target=self.runtime.backend.alert_sound,
                args=(level,), daemon=True,
            ).start()
        self._push(self.runtime.snapshot(), {"level": level, "names": names})
        # 审批是要你尽快处理的，直接把面板推出来，省得你还得先注意到球在闪
        self.toggle_panel(force_show=True)

    # ---------------- 面板

    def panel_visible(self) -> bool:
        return self._panel_shown

    def toggle_panel(self, force_show: bool = False) -> bool:
        if self.panel is None:
            return False
        try:
            if force_show or not self._panel_shown:
                # 先置位再显示：启动守卫线程在后面盯着，这里慢一步它就会把面板压回去
                self._panel_shown = True
                self._place_panel()
                self.panel.show()
                return True
            self.panel.hide()
            self._panel_shown = False
            return False
        except Exception as exc:
            self._panel_shown = False
            _log("切换面板失败：%r" % exc)
            return False

    def hide_panel(self) -> None:
        if self.panel is None or not self._panel_shown:
            return
        try:
            self.panel.hide()
        except Exception:
            pass
        self._panel_shown = False

    def _place_panel(self) -> None:
        """把面板摆在球的旁边，靠近哪边贴哪边。"""
        # 尺寸在这里校正——见 _fix_sizes() 的说明：resize 会把窗口显示出来，
        # 而这一步本来就紧接着 show()，顺手做掉最省事。放在最前面，免得后面
        # 因为拿不到屏幕尺寸提前 return 就漏掉了。
        try:
            self.panel.resize(PANEL_W, PANEL_H)
        except Exception as exc:
            _log("校正面板尺寸失败：%r" % exc)

        screen = self._screen()
        if screen is None or self.ball is None:
            return

        bx, by = int(self.ball.x), int(self.ball.y)
        px = bx - PANEL_W - GAP
        if px < 8:
            px = bx + BALL + GAP
        py = by + BALL - PANEL_H
        py = max(8, min(py, int(screen.height) - PANEL_H - 8))
        try:
            self.panel.move(px, py)
        except Exception:
            pass

    # ---------------- 拖动

    def begin_drag(self) -> bool:
        """让系统接管球的拖动。

        拿到原生窗口句柄就交给系统；拿不到（非 Windows，或 pywebview 换了后端）
        返回 False，前端会退回「自己算鼠标位移」那套。
        """
        if self.ball is None:
            return False
        native = getattr(self.ball, "native", None)
        if native is None:
            return False
        return self.runtime.backend.begin_window_drag(native)

    def remember_ball_pos(self) -> bool:
        """把球当前的位置写进配置——下次启动还在原地。"""
        if self.ball is None:
            return False
        try:
            pos = [int(self.ball.x), int(self.ball.y)]
        except Exception:
            return False
        if self.config.get("ballPos") != pos:
            self.config.set("ballPos", pos, save=True)
        return True

    def ball_pos(self) -> list:
        if self.ball is None:
            return [0, 0]
        try:
            return [int(self.ball.x), int(self.ball.y)]
        except Exception:
            return [0, 0]

    def move_ball(self, x: int, y: int) -> bool:
        """前端自己算位移时的退路（系统不支持原生拖动才会走到这里）。"""
        if self.ball is None:
            return False
        try:
            self.ball.move(int(x), int(y))
            return True
        except Exception:
            return False


def _log(message: str) -> None:
    """诊断输出。pythonw 下没有控制台，但 print 到已关闭的标准流是安全的。"""
    try:
        print("[AgentHub] %s" % message, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 命令行

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="agenthub",
        description="把本机几个 GUI 版 AI 编程助手的状态收进一个小窗口。",
    )
    parser.add_argument("--once", action="store_true",
                        help="只扫描一次，把状态打到终端，不开窗口")
    parser.add_argument("--debug", action="store_true",
                        help="打开 WebView 的开发者工具")
    args = parser.parse_args(argv)

    if args.once:
        return _print_once()

    AgentHubApp().run(debug=args.debug)
    return 0


def _print_once() -> int:
    """诊断用：一轮扫描的结果直接打出来。"""
    runtime = Runtime()
    print("平台：%s  能力：%s" % (runtime.backend.name, runtime.backend.capabilities()))
    started = time.time()
    snapshot = runtime.refresh()
    print("扫描耗时 %.0fms" % ((time.time() - started) * 1000))
    for agent in snapshot["agents"]:
        line = "%-14s %-8s cpu=%5.1f%%" % (agent["name"], agent["status"], agent["cpu"])
        if agent["hint"]:
            line += " · %s" % agent["hint"]
        if agent["detail"]:
            line += "    %s" % agent["detail"]
        print(line)
    print("合计：%s" % snapshot["totals"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
