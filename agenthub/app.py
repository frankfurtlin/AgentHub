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

from . import tray, webview_env
from .config import Config
from .models import AGENTS
from .runtime import Runtime

# ---------------------------------------------------------------- 尺寸与间距

BALL = 76          # 悬浮球直径
PANEL_W = 344      # 面板宽
# 面板高度**不写死**：前端每次渲染完把内容实际需要的高度报上来
# （web/app.js 的 syncPanelHeight → Bridge.resize_panel），这里只负责夹紧。
# 下面这个只是第一次打开面板时用的默认值，拿到真实高度后会被配置里的记住值取代。
PANEL_H_DEFAULT = 520
PANEL_H_MIN = 220  # 再小连头部 + 一张卡都放不下
PANEL_H_MAX = 900  # 再大就该滚动了（还有一道按屏幕高度收的保险）
MARGIN = 24        # 球离屏幕边缘的默认距离
GAP = 12           # 面板与球之间的间隙
EDGE = 8           # 面板离屏幕边缘的最小留白
PANEL_RADIUS = 18  # 面板圆角半径（页面 CSS 里是同一个值，透明兜底裁剪要用）
POS_SAVE_EVERY = 4  # 每几轮扫描记一次球的位置（拖动后要记住）
SETTLE_SECONDS = 6.0  # 启动后盯着面板、别让它自己冒出来的时长（见 _guard_panel_hidden）
FOLLOW_TRIES = 10   # 面板每次展开后最多试几次挂跟随钩子（窗口原生对象可能还没就绪）
TRANSPARENCY_EVERY = 6   # 每几轮扫描重设一次窗口透明（约 9 秒，见 _patch_transparency）


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

    def resize_panel(self, height: int) -> bool:
        """前端量完内容高度后调这里：把面板窗改到刚好的高。"""
        return self._app.resize_panel(int(height))

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
        # 面板是否已挂上「逐帧跟随」的消息钩子（拿不到原生句柄时会是 False，
        # 那种情况退回 _follow_ball 的轮询方案）
        self._follower_on = False
        self._follow_tries = 0     # 面板本次展开后试了几次挂钩子
        # 窗口原生改造：从任务栏摘掉（改成工具窗）+ 挂托盘图标。
        # 两件事都要等窗口的原生对象就绪，所以走「每轮试一次」的路子。
        self._chrome_todo = []
        self._tray_ready = False
        self._tray_icon = None
        self._tray_text = ""
        self._transparency_done = False
        # 面板当前高度。首次以内置默认值开窗，前端渲染完把真实需要的高度报上来
        # （见 resize_panel）；记住在配置里，下次开面板就不跳了。
        try:
            saved_h = int(self.config.get("panelHeight") or 0)
        except (TypeError, ValueError):
            saved_h = 0
        self._panel_h = saved_h if PANEL_H_MIN <= saved_h <= PANEL_H_MAX else PANEL_H_DEFAULT

    # ---------------- 窗口

    def _screen(self):
        try:
            screens = webview.screens
        except Exception:
            return None
        return screens[0] if screens else None

    def _logical_screen(self) -> tuple[int, int] | None:
        """主屏尺寸，折算成 `Window.move()` 认的**逻辑坐标**。

        绕这一道是因为 pywebview 在两种情况下给的数据语义不同，而这两种情况
        我们都可能撞上：

            进程**不**感知 DPI：`Screen.width` 是 Windows 虚拟化后的逻辑值
                （2560×1440 的屏报 2048×1152），而 `move()` 内部不缩放，
                直接把参数丢给 `SetWindowPos`；系统随后再把它放大回物理像素。
                「传逻辑、落点正确」这个闭环是成立的。
            进程感知 DPI（`webview_env` 现在会主动设）：`Screen.width` 变成
                物理像素 2560，而 `move()` 这时**会**乘一次缩放（×1.25）。
                不折算的话 2460 会被送到 3075，球直接飞出屏幕右边。

        系数取 `backend.screen_scale()`（Windows 上就是 `GetDpiForSystem()/96`），
        它在两种情况下都和环境自洽——不感知时返回 96，也就是 1.0，除下去等于
        没除。一条公式同时管两种情况。
        """
        screen = self._screen()
        if screen is None:
            return None
        try:
            scale = float(self.runtime.backend.screen_scale())
        except Exception:
            scale = 1.0
        if scale <= 0:
            scale = 1.0
        return int(screen.width / scale), int(screen.height / scale)

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
            width=PANEL_W, height=self._panel_h, min_size=(PANEL_W, PANEL_H_MIN),
            x=x, y=y,
            frameless=True, transparent=True, on_top=True,
            resizable=False, shadow=False, hidden=True,
            js_api=self.bridge,
        )
        # 两个窗口的透明都要等窗体真正建出来之后才补得上，见 _patch_transparency。
        # 第二个元素是兜底裁剪用的半径：球裁成圆，面板按圆角裁。
        self._transparency_targets = [
            (self.ball, BALL // 2), (self.panel, PANEL_RADIUS)
        ] if self.ball is not None and self.panel is not None else []
        self._chrome_todo = [self.ball, self.panel]
        return self

    # ---------------- 运行

    def run(self, debug: bool = False) -> None:
        # 顺序不能动：环境要先摆好，再开窗口，最后进事件循环。
        # 详见 webview_env 模块开头——少这一步 WebView2 会白屏。
        options = webview_env.start_options(self.config, self.runtime.backend.config_dir())
        self.build()
        self.runtime.on_alert(self._on_alert)
        self.runtime.start()
        # 面板跟随球的线程（见 _follow_ball 的注释）
        threading.Thread(target=self._follow_ball, daemon=True,
                         name="agenthub-follow").start()
        webview.start(self._loop, debug=debug, **options)

    def quit(self) -> None:
        self._stop.set()
        self._detach_follow()
        # 托盘图标不主动 Dispose 的话，进程退出了图标还会挂在托盘里，
        # 一直等鼠标划过去才消失——这是个很常见的"程序关了但没完全关"。
        icon = getattr(self, "_tray_icon", None)
        if icon is not None:
            try:
                icon.Visible = False
                icon.Dispose()
            except Exception:
                pass
            self._tray_icon = None
            self._tray_ready = False
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
            self._patch_transparency()
            self._settle_chrome()
            try:
                snapshot = self.runtime.refresh()
                self._push(snapshot)
            except Exception as exc:                      # 单轮失败不该拖垮循环
                _log("扫描失败：%r" % exc)
            self._ticks += 1
            if self._ticks % POS_SAVE_EVERY == 0:
                self.remember_ball_pos()
            self._stop.wait(interval)

    def _patch_transparency(self) -> None:
        """给两个窗口补上 pywebview 漏掉的透明设置。

        不补会怎样：桌面上的球（和面板的圆角）外面裹着一个 `#F0F0F0` 正方形。
        原因和实测数据见 `webview_env.patch_transparency`。

        放在循环里而不是 `_settle_windows()` 里一次做完，是因为窗口是并行建的：
        循环刚跑起来时窗体未必登记好了。每轮试一次，直到两个窗口都成了。

        ⚠️ 补完**不能撒手**。透明有两层，两层都会跑丢：
            1. 窗体底色 + DWM 玻璃——玻璃挂在窗口句柄上，DWM 重启、显示器
               唤醒之后会整片丢；
            2. WebView2 自己的背景色——pywebview 只在建窗时设过一次，
               跑久了会退回不透明白底。
        任意一层丢了，球的四角就浮出一块近白方块，而且不会自愈。所以除了
        首次，每 `TRANSPARENCY_EVERY` 轮无条件重设一遍（两件事都是幂等的）。
        """
        if not self._transparency_targets:
            return
        periodic = self._ticks > 0 and self._ticks % TRANSPARENCY_EVERY == 0
        if self._transparency_done and not periodic:
            return

        pending = 0
        for win, radius in self._transparency_targets:
            force = periodic or not self._transparency_done
            if not webview_env.patch_transparency(win, radius, force=force):
                pending += 1
        if pending == 0 and not self._transparency_done:
            self._transparency_done = True
            _log("窗口透明：已生效（玻璃 + WebView2 透明背景）")

    def _ensure_tool_window(self) -> None:
        """补一次「任务栏里不要出现我」的样式。纯 Win32 改扩展样式，不动句柄。

        实测（`_shots/toolwindow_probe.py`）：改成工具窗之后 `Show()` 并不会把
        `WS_EX_APPWINDOW` 揉回来，句柄也不变。这里保留一次补刷是买个保险——
        样式这类东西换个 WinForms/系统版本就未必还是这个行为，成本就两个
        Win32 调用，不值得省。
        """
        for win in (self.ball, self.panel):
            if win is None:
                continue
            native = getattr(win, "native", None)
            if native is None:
                continue
            try:
                self.runtime.backend.make_tool_window(native)
            except Exception:
                pass

    def _settle_chrome(self) -> None:
        """窗口的「外壳」改造：从任务栏摘掉，再装上托盘图标。

        和透明补丁一个套路——都要等窗口的原生对象就绪，所以每轮试一次，
        成了就从待办里划掉；托盘图标是幂等的（`tray.attach` 自己会挡重复）。

        顺序有讲究：先把窗口改成工具窗，再装托盘。反过来的话托盘图标会在
        任务栏里短暂多出一个入口，看着像闪了一下。
        """
        if self._chrome_todo:
            for win in list(self._chrome_todo):
                native = getattr(win, "native", None)
                if native is None:
                    continue
                if self.runtime.backend.make_tool_window(native):
                    self._chrome_todo.remove(win)
        elif self._ticks > 0 and self._ticks % TRANSPARENCY_EVERY == 0:
            self._ensure_tool_window()      # 周期性兜底，防止样式被揉回去
        if not self._tray_ready:
            tray.attach(self)

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
        size = self._logical_screen()
        if size is None:
            return
        logical_w, logical_h = size
        try:
            self.ball.move(int(logical_w - BALL - MARGIN),
                           int(logical_h - BALL - MARGIN - 56))
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
        self._update_tray_text(snapshot)

    def _update_tray_text(self, snapshot: dict) -> None:
        """托盘图标的悬停提示。只在文案变了的时候写一次。"""
        if not self._tray_ready:
            return
        totals = snapshot.get("totals") or {}
        waiting = int(totals.get("waiting") or 0)
        unread = int(totals.get("unread") or 0)
        running = int(totals.get("running") or 0)
        if waiting:
            text = "AgentHub · %d 个在等你确认" % waiting
        elif unread:
            text = "AgentHub · %d 个有新结果" % unread
        elif running:
            text = "AgentHub · %d 个在跑" % running
        else:
            text = "AgentHub · 都闲着"
        if text != self._tray_text:
            self._tray_text = text
            tray.set_text(self, text)

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
                self._attach_follow()
                self._ensure_tool_window()
                return True
            self._detach_follow()
            self.panel.hide()
            self._panel_shown = False
            self._ensure_tool_window()
            return False
        except Exception as exc:
            self._panel_shown = False
            _log("切换面板失败：%r" % exc)
            return False

    def hide_panel(self) -> None:
        if self.panel is None or not self._panel_shown:
            return
        self._detach_follow()
        try:
            self.panel.hide()
        except Exception:
            pass
        self._panel_shown = False

    # ---------------- 跟随的生平

    def _attach_follow(self) -> None:
        """面板展开时把它挂到球上：球每移动一帧，面板同帧跟上，没有轮询延迟。

        ⚠️ 别只看「面板是不是展开的」就认为挂得上：面板可能在窗口原生对象
        还没就绪时就被推出来（启动时有告警的话，`_on_alert` 会直接弹面板）。
        那种情况这里会返回 False，由 `_follow_ball` 线程隔一会儿重试——
        所以这个函数要能被反复调用，幂等。
        """
        if self._follower_on or self.ball is None or self.panel is None:
            return
        native_ball = getattr(self.ball, "native", None)
        native_panel = getattr(self.panel, "native", None)
        if native_ball is None or native_panel is None:
            # 还没就绪，交给重试。只在第一次说一声，别刷屏
            if self._follow_tries <= 1:
                _log("拖动跟随：窗口原生对象尚未就绪（ball=%s panel=%s），重试中"
                     % (native_ball is not None, native_panel is not None))
            return
        backend = self.runtime.backend
        try:
            ok = backend.attach_follow(
                native_ball, native_panel,
                (BALL, PANEL_W, self._panel_h, GAP, EDGE),
            )
            if ok:
                backend.sync_follow()       # 先对齐一次，不等下一次 WM_MOVE
        except Exception as exc:
            _log("挂拖动跟随失败：%r" % exc)
            ok = False
        self._follower_on = bool(ok)
        if self._follower_on:
            _log("拖动跟随：已挂消息钩子（逐帧同步）")
        else:
            _log("拖动跟随：SetWindowSubclass 没成功，退回轮询")

    def _detach_follow(self) -> None:
        if not self._follower_on:
            return
        try:
            self.runtime.backend.detach_follow()
        except Exception as exc:
            _log("摘拖动跟随失败：%r" % exc)
        self._follower_on = False

    def resize_panel(self, height: int) -> bool:
        """把面板窗改到指定高度（前端量出来的内容高度）。

        调用方是前端（每次渲染完量一遍），所以这里要注意两点：

        1. **面板没显示时不要改窗口尺寸**——`resize()` 的 flag 带 SWP_SHOWWINDOW，
           会把窗口显示出来，等于凭空弹一次面板；
        2. 改完要重新摆位，否则底部会脱开球（面板是底边对齐球的）。
        """
        if self.panel is None:
            return False
        ceiling = PANEL_H_MAX
        size = self._logical_screen()
        if size is not None:
            ceiling = min(ceiling, max(PANEL_H_MIN, size[1] - EDGE * 2))
        target = max(PANEL_H_MIN, min(int(ceiling), int(height)))
        if abs(target - self._panel_h) < 2:
            return False
        self._panel_h = target
        if not self._panel_shown:
            return False                    # 记下高度就行，开面板时 _place_panel 会用
        try:
            self.panel.resize(PANEL_W, target)
        except Exception as exc:
            _log("调整面板高度失败：%r" % exc)
            return False
        self._move_panel()
        self.config.set("panelHeight", target, save=True)
        return True

    def _place_panel(self) -> None:
        """把面板摆在球的旁边，靠近哪边贴哪边。"""
        # 尺寸在这里校正——见 _settle_windows() 的说明：resize 会把窗口显示
        # 出来，而这一步本来就紧接着 show()，顺手做掉最省事。放在最前面，
        # 免得后面因为拿不到屏幕尺寸提前 return 就漏掉了。
        try:
            self.panel.resize(PANEL_W, self._panel_h)
        except Exception as exc:
            _log("校正面板尺寸失败：%r" % exc)
        self._move_panel()

    def _move_panel(self) -> None:
        """只改面板位置，不动尺寸。拖动跟随线程高频调用，省掉无谓的 resize。"""
        size = self._logical_screen()
        if size is None or self.ball is None:
            return
        logical_w, logical_h = size

        bx, by = int(self.ball.x), int(self.ball.y)
        px = bx - PANEL_W - GAP
        if px < EDGE:
            px = bx + BALL + GAP
        py = by + BALL - self._panel_h
        py = max(EDGE, min(py, logical_h - self._panel_h - EDGE))
        try:
            self.panel.move(px, py)
        except Exception:
            pass

    def _follow_ball(self) -> None:
        """面板展开时让它跟着球走（Windows 上另有主力方案，见下）。

        首选是挂消息钩子逐帧同步（`_attach_follow` / winapi.DragFollower）：
        球每移动一帧，面板同帧跟上，没有间隔。这里是它的**兜底**——
        钩子挂不上（拿不到原生句柄 / 非 Windows 平台）时才真正干活：

            快速通道    Win32 直读直写（物理像素），16ms 一轮
            慢速退路    pywebview 的逻辑坐标，150ms 一轮（跨线程排队太慢）

        为什么只能轮询而不能靠鼠标事件：原生拖动是系统接管的，拖动期间鼠标
        事件全被窗口挪走，前端一个 mousemove 都收不到。
        """
        backend = self.runtime.backend
        scale = max(1.0, float(backend.screen_scale() or 1.0))
        ball_p = int(BALL * scale)
        panel_w_p = int(PANEL_W * scale)
        gap_p = int(GAP * scale)
        margin_p = int(EDGE * scale)
        work = backend.work_area()      # (l, t, r, b) 物理像素；拿不到就不做边界收缩

        last_ball = None
        while not self._stop.is_set():
            if not self._panel_shown or self.ball is None or self.panel is None:
                last_ball = None
                self._follow_tries = 0
                self._stop.wait(0.15)
                continue

            # 还没挂上钩子就再试（原生对象就绪得比面板显示晚，启动时尤其明显）
            if not self._follower_on and self._follow_tries < FOLLOW_TRIES:
                self._follow_tries += 1
                self._attach_follow()

            # 钩子已经装好：逐帧同步由它负责。这里留一个低频纠偏，
            # 覆盖「消息没到」的漏网情况（比如窗口被非拖动的方式挪动）。
            if self._follower_on:
                ball_pos = backend.window_pos(getattr(self.ball, "native", None))
                if ball_pos is not None and ball_pos != last_ball:
                    backend.sync_follow()
                    last_ball = ball_pos
                self._stop.wait(0.25)
                continue

            # ---- 快速通道：Win32 直读直写 ----
            # 高度每轮重读：面板会随内容变高（见 resize_panel）
            panel_h_p = int(self._panel_h * scale)
            native_ball = getattr(self.ball, "native", None)
            ball_pos = backend.window_pos(native_ball) if native_ball is not None else None
            if ball_pos is not None:
                if ball_pos == last_ball:
                    self._stop.wait(0.016)   # 球没动，白转一圈的成本只是一次 GetWindowRect
                    continue
                bx, by = ball_pos
                px = bx - panel_w_p - gap_p
                if px < margin_p:            # 左边放不下就换到球右边
                    px = bx + ball_p + gap_p
                py = by + ball_p - panel_h_p
                if work is not None:
                    px = max(margin_p, min(px, work[2] - panel_w_p - margin_p))
                    py = max(margin_p, min(py, work[3] - panel_h_p - margin_p))
                if backend.place_window(getattr(self.panel, "native", None), px, py):
                    last_ball = ball_pos
                    self._stop.wait(0.016)
                    continue
                # place 失败说明快速通道不可用，落到下面的慢速路径

            # ---- 慢速退路（非 Windows / 句柄拿不到）：pywebview 逻辑坐标 ----
            try:
                pos = (int(self.ball.x), int(self.ball.y))
            except Exception:
                pos = None
            if pos is None:
                last_ball = None
            elif pos != last_ball:
                self._move_panel()
                last_ball = pos
            self._stop.wait(0.15)

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
        # 窗口刚建好、还没摆到默认位置时 `x/y` 可能是占位值；那时记下来，
        # 下次启动球就被钉在屏幕左上角了（验收脚本踩过一次）。
        if pos[0] < 0 or pos[1] < 0:
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
