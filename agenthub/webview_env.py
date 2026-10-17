# -*- coding: utf-8 -*-
"""启动 WebView 之前必须摆好的环境。

这里的东西都很不显眼，但少一样界面就整个白屏，而且在应用层完全看不出原因——
所以每一段都写清楚「不这么做会怎样」。

一、WebView2 的 GPU 沙箱
    WebView2 会为合成单独起一个 GPU 进程。部分机器上这个子进程起不来，
    Chromium 会连试 9 次然后整体放弃：

        ERROR:gpu_process_host.cc] GPU process exited unexpectedly: exit_code=7
        FATAL:gpu_data_manager_impl_private.cc] GPU process isn't usable. Goodbye.

    之后窗口就停在 `DefaultBackgroundColor`（一片白），JS 一行都不执行，
    Python 侧 `evaluate_js()` 抛「Main window failed to start」。
    加 `--disable-gpu-sandbox` 即可——实测在开发机上从「每次必崩」变成「零崩溃」。
    这个开关只去掉 GPU 子进程的沙箱，页面又全是本地文件，代价可以接受；
    真要收紧就在 config.json 的 `webviewArgs` 里把它去掉。

    注意 `--disable-gpu` 反而**没用**（GPU 进程照样起、照样崩），
    试过的无效组合记录在 docs/ 里，别再走一遍。

二、用户数据目录
    pywebview 在 private_mode（默认开）下拿
    `tempfile.TemporaryDirectory().name` 当 profile 目录。但那个
    TemporaryDirectory 对象一转身就被 GC，目录当场被删，WebView2 拿到的是个
    不存在的路径。我们自己指一个真实目录，顺带把 private_mode 关掉，
    省得退出时它去 `shutil.rmtree` 一个已经没了的东西。

三、窗口透明（这个得在窗口**建好之后**补，见 `patch_transparency`）
    pywebview 6.2.1 建透明窗口时只做了两件事：
    `SetStyle(SupportsTransparentBackColor, True)` 和
    `browser.DefaultBackgroundColor = Color.Transparent`（后一步是对的）。
    但**窗体自己的 `BackColor` 从来没被碰过**，保持 WinForms 默认的
    `SystemColors.Control`，即 `#F0F0F0`、alpha=255。WebView2 背景透明之后，
    透出来的就是窗体这块不透明的浅灰——桌面上看就是一个方框把球包住。

    修法首选 **DWM 玻璃**：窗体底色刷黑，再把玻璃框扩展到整个客户区，
    DWM 会把这层黑当玻璃键掉。窗口仍是矩形，命中测试照旧走原路径，
    点击不受影响。

    ⚠️ 为什么不用 `TransparencyKey`（一开始走过这条路，已否决）：
    它确实能让四角透明，但会顺带把窗口变成 layered——MSDN 明确说
    layered 窗口的色键区**放行鼠标消息**，页面里 `mousedown` 一次都收不到，
    悬浮球直接点不动。实测对照见 `_shots/plans_probe.py`：
    色键「透 4/4 / 点击没收到」，DWM 玻璃「透 4/4 / 点击收到」。

    DWM 不可用（返回非 0，例如远程桌面会话、关掉了桌面合成的 Win7 基础主题）
    时退到 `SetWindowRgn` 把窗口裁成圆角矩形——硬边无抗锯齿，但不吃点击、
    也不依赖系统特效开关，比留着一块黑方块强。
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

# WebView2 运行时认这个环境变量，会和 CreateCoreWebView2Environment 的
# 参数一起加给浏览器进程。
ARGV_ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"

# 见模块开头第一条。就这一个开关，不再多动别的——试出来的。
GPU_SANDBOX_FLAG = "--disable-gpu-sandbox"


def _merge_env(flags: list[str]) -> None:
    """把参数并进环境变量，已有内容保留（用户可能自己设过）。"""
    if not flags:
        return
    current = os.environ.get(ARGV_ENV, "").strip()
    merged = (current + " " + " ".join(flags)).strip()
    os.environ[ARGV_ENV] = merged


def browser_flags(config=None) -> list[str]:
    """本次启动要加的浏览器参数。

    平台默认值 + 配置里的 `webviewArgs`。配置里给的是**追加**，
    想去掉默认值就把同一个开关写进 `webviewArgs` 也无济于事——
    真要覆盖请直接设 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 环境变量，
    这里检测到就会尊重它。
    """
    flags: list[str] = []
    if sys.platform == "win32":
        flags.append(GPU_SANDBOX_FLAG)

    extra = config.get("webviewArgs") if config is not None else None
    if isinstance(extra, (list, tuple)):
        flags.extend(str(item) for item in extra if str(item).strip())
    return flags


def enable_dpi_awareness() -> bool:
    """让本进程感知 DPI。**必须在创建任何窗口之前调用。**

    不设的话，Windows 会把我们读到和写下去的一切都按 96dpi 虚拟化一遍：
    2560×1440 的屏报成 2048×1152，而我们正是按这个尺寸算球的落点，多出来的
    那一截靠系统把整个窗口位图拉过 1.25 倍补上。**拉伸就是「界面发糊、边缘发
    毛」的来源**，跟浏览器渲染质量没关系——同一个页面在感知 DPI 的进程里
    渲染，颜色种类会明显多出一截。

    pywebview 自己**不设**。`platforms/winforms.py` 的 `get_screens()` 注释里
    写着 "already DPI-aware scaled due to SetProcessDPIAware"，那是它的**假设**，
    不是它做过的事；全库搜不到一处 SetProcessDPIAware。所以这一步得我们补。

    调用点放在 `apply()` 里、和环境变量一起摆好——两件事都必须在
    `webview.start()` 之前完成，分开放容易漏掉一件。

    ⚠️ 设了之后 `webview.screens` 的语义会变：`Screen.width` 从「虚拟逻辑值」
    变成「物理像素」，于是所有传给 `Window.move()` 的坐标都得先折回逻辑值
    （它内部会再乘一次缩放）。见 `app.AgentHubApp._logical_screen()`。

    重复调用无副作用：第一次生效后，后面的调用都会失败，但不影响已生效的级别。
    """
    if sys.platform != "win32":
        return False

    # Win8.1+：PROCESS_PER_MONITOR_DPI_AWARE = 2。
    # 比 SetProcessDPIAware()（系统级）更细，多显示器缩放不同时不会糊。
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        if shcore.SetProcessDpiAwareness(2) == 0:       # S_OK
            return True
    except (OSError, AttributeError):
        pass

    # 退到 Vista 时代的老 API：系统级感知。至少不再有位图拉伸。
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        return bool(user32.SetProcessDPIAware())
    except (OSError, AttributeError):
        return False


def apply(config=None) -> list[str]:
    """摆好环境。必须在 `webview.start()` 之前调用。"""
    enable_dpi_awareness()
    flags = browser_flags(config)
    if flags and ARGV_ENV not in os.environ:
        _merge_env(flags)
    return flags


def profile_dir(base_dir: str) -> str:
    """给 WebView2 的 profile 目录（见模块开头第二条）。

    顺带处理前端缓存——见 `_clear_cache_on_version_change`。
    """
    path = os.path.join(base_dir, "webview")
    os.makedirs(path, exist_ok=True)
    _clear_cache_on_version_change(path)
    return path


#: 只清这两层。别整个目录删：那里还有 Cookie、localStorage、GPU 缓存之类，
#: 删了整个 profile 会让 WebView2 重建一遍（启动慢好几秒），也会丢掉
#: 「已读过哪些通知」之类将来可能用上的状态。
_CACHE_DIRS = ("Cache", "Code Cache")


def _clear_cache_on_version_change(profile: str) -> None:
    """版本号变了就清一次 WebView2 的 HTTP 缓存。

    为什么非清不可：前端资源（index.html / style.css / app.js）都在本地，
    WebView2 会按启发式规则缓存它们。`?v=` 只能绕开被它引用的 CSS/JS——
    **index.html 自己**一旦被缓存，改前端就等于没改（实测：改了 CSS、
    重启了进程，界面上还是旧样式，排查了半天）。所以版本一升就把缓存清掉，
    这样「改前端 → 升版本 → 重启」三步必然生效。
    """
    import shutil

    from . import __version__

    marker = os.path.join(profile, ".frontend-version")
    try:
        with open(marker, "r", encoding="utf-8") as handle:
            seen = handle.read().strip()
    except OSError:
        seen = ""
    if seen == __version__:
        return

    root = os.path.join(profile, "EBWebView", "Default")
    for name in _CACHE_DIRS:
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    try:
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(__version__)
    except OSError:
        pass
    print("[AgentHub] 前端版本 %s，已清理 WebView2 缓存" % __version__, flush=True)


def start_options(config=None, base_dir: str | None = None) -> dict:
    """`webview.start()` 要的那些额外参数。

    做成一个函数，是为了让「摆环境」和「开窗口」在一次调用里完成，
    免得将来有人插了别的代码在中间、顺序被打乱。
    """
    apply(config)
    if base_dir is None:
        return {}
    return {"storage_path": profile_dir(base_dir), "private_mode": False}


# ---------------------------------------------------------------- 窗口透明

def _winforms_form(window):
    """取 pywebview 底层那个 WinForms 窗体，拿不到返回 None。

    非 Windows、或者窗口还在创建中（`BrowserView.instances` 里还没登记）时都会拿到
    None——这两种情况对调用方是同一件事：**过一会儿再试**。
    """
    if sys.platform != "win32":
        return None
    try:
        from webview.platforms import winforms
    except Exception:
        return None
    return winforms.BrowserView.instances.get(window.uid)


class _Margins(ctypes.Structure):
    """`DwmExtendFrameIntoClientArea` 的边距，全 -1 表示「整个客户区都是玻璃」。"""
    _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]


# HRGN 在 64 位是 64 位句柄；不声明 argtypes 的话 ctypes 按默认 c_int 转，
# 直接报 "int too long to convert"。
_gdi = None
_user32 = None
if sys.platform == "win32":
    _gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    _gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    _gdi.CreateRoundRectRgn.restype = wintypes.HRGN
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    _user32.SetWindowRgn.restype = ctypes.c_int
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.restype = wintypes.BOOL

_GLASS_BLACK = (0, 0, 0)


def _hwnd_of(form) -> int:
    try:
        return int(form.Handle.ToInt64())
    except Exception:
        return 0


def _paint_black(form) -> None:
    """把窗体底色刷成纯黑——这是 DWM 玻璃的键色。

    已经是黑的不再重复赋值 + Refresh：这个函数现在会被周期性重调（见
    `patch_transparency` 的 `force`），每次都刷一遍会让整块界面重绘。
    """
    from webview.platforms import winforms as wf
    black = wf.Color.FromArgb(255, *_GLASS_BLACK)
    try:
        if form.BackColor.ToArgb() == black.ToArgb():
            return
    except Exception:
        pass
    form.BackColor = black
    form.Refresh()


_webview_warned = False


def _webview_transparent(form) -> bool:
    """把 WebView2 自己的背景色重设为「透明」。

    pywebview 只在建窗那一刻设过一次（edgechromium.py：`DefaultBackgroundColor
    = Color.Transparent`，而且是在 `EnsureCoreWebView2Async` **之前**设的）。
    实测跑久了这一层会退回不透明白底——桌面上的表现就是球的四角浮出一块近白
    的方块（球芯照常，四角 `#FBFBFB`/`#F6F6F6` 而窗外是 `#FFFFFF`）。所以和
    玻璃一起周期性重设，谁丢了补谁。
    """
    global _webview_warned
    try:
        from System.Drawing import Color
        browser = getattr(form, "browser", None)
        webview = getattr(browser, "webview", None)
        if webview is None:
            return False
        # 已经是透明的就不重复写：这个函数每十来秒跑一遍，稳态下别白白触发重绘
        try:
            current = webview.DefaultBackgroundColor
            if int(current.A) == 0:
                return True
        except Exception:
            pass
        # ⚠️ 必须跑在 UI 线程：CoreWebView2Controller 的成员跨线程访问会抛
        # InvalidOperationException（实测）。调用方是经 form.Invoke 进来的。
        webview.DefaultBackgroundColor = Color.Transparent
        return True
    except Exception as exc:
        if not _webview_warned:
            _webview_warned = True
            print("[AgentHub] 设 WebView2 透明背景失败：%r" % exc, flush=True)
        return False


def _apply_glass(form) -> bool:
    """主方案：DWM 玻璃。成功返回 True。

    玻璃区里的**纯黑像素会变透明**（DWM 的键色写死是黑），所以界面一律不许用
    `#000000`；`rgba(0,0,0,.x)` 那种半透明阴影不受影响，叠出来不会是纯黑。
    """
    hwnd = _hwnd_of(form)
    if not hwnd:
        return False
    _paint_black(form)
    try:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        # 返回 HRESULT。非 0 表示这次没成（DWM 没在合成），交给 fallback。
        return dwm.DwmExtendFrameIntoClientArea(
            wintypes.HWND(hwnd), ctypes.byref(_Margins(-1, -1, -1, -1))) == 0
    except (OSError, AttributeError):
        return False


def _apply_region(form, radius: int) -> bool:
    """兜底方案：把窗口裁成圆角矩形（radius 给一半宽就是正圆）。

    没有抗锯齿，边缘会硬一点；但既不改窗口的命中测试，也不依赖系统特效开关。
    尺寸用 `GetClientRect` 拿，别用 `GetWindowRect`——后者在 DPI 感知进程里返回
    物理像素，和这里要的窗口坐标系不是一个尺度。
    """
    if _gdi is None:
        return False
    hwnd = _hwnd_of(form)
    if not hwnd:
        return False

    rect = wintypes.RECT()
    if not _user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return False
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return False

    rgn = _gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1,
                                  min(radius * 2, width), min(radius * 2, height))
    if not rgn:
        return False
    return _user32.SetWindowRgn(wintypes.HWND(hwnd), rgn, True) != 0


def patch_transparency(window, radius: int = 0, force: bool = False) -> bool:
    """补上 pywebview 漏掉的窗口透明。True = 已处理好，False = 还没就绪、下轮再试。

    见模块开头第三条。三种做法的实测对照（`_shots/plans_probe.py`，95×95 圆球：

        不补      BackColor=A255 R240 G240 B240 → 四角 #F0F0F0，与窗外差 635
        TransparencyKey  四角透 4/4，但 **点击收不到**（layered 放行色键区）
        DWM 玻璃        四角透 4/4，点击正常 ✓   ← 采用
        SetWindowRgn    四角透 4/4，点击正常 ✓   ← DWM 不可用时的兜底

    `radius` 只在兜底裁剪时用：悬浮球给直径的一半（裁成圆），面板给圆角半径。
    主方案不吃这个参数，传 0 也无妨。

    ⚠️ `force=True`——「不管之前补没补过，再补一次」。玻璃是**挂在窗口句柄上
    的**：DWM 重启、显示器休眠唤醒、主题切换之后玻璃会整片丢掉，而窗口还活得
    好好的（Tag 标记也还在），于是补丁会被永久跳过，球外面重新裹上一层窗体
    底色——跑一整夜之后特别容易撞上。所以除了首次，还得每隔一阵子重新确认。
    重新补一次是幂等的：把底色刷黑 + 再扩一次玻璃，成本就两三个 Win32 调用。

    调用方要注意线程——`_loop` 跑在子线程，而 WinForms 控件归建窗那条线程管，
    所以这里先用 `InvokeRequired` 判断，需要的话 Invoke 过去再改。
    """
    form = _winforms_form(window)
    if form is None:
        return False                    # 窗体还没建好

    if not force:
        try:
            if form.Tag is not None and str(form.Tag).startswith("transparency:"):
                return True             # 补过了，每补一次都要重算样式，没必要重复
        except Exception:
            pass
    def job() -> bool:
        _webview_transparent(form)      # 浏览器那层背景（时间久了会退回白底）
        glass = _apply_glass(form)
        ok = glass or _apply_region(form, radius)
        if ok:
            try:
                form.Tag = "transparency:%s" % ("glass" if glass else "region")
            except Exception:
                pass
            form.Refresh()
        return ok

    try:
        if form.InvokeRequired:
            from System import Action
            box = []
            form.Invoke(Action(lambda: box.append(job())))
            return bool(box and box[0])
        return job()
    except Exception:
        return False                    # 窗口正在销毁之类的，交给下一轮
