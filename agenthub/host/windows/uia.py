# -*- coding: utf-8 -*-
"""用 UI Automation 读 Electron / VS Code 系应用的界面文字（纯 ctypes，零第三方依赖）。

**为什么需要这个模块**

AgentHub 原本只靠 Windows 通知来判断「某个工具在等你审批」，但有两个工具的
通知通道根本指望不上（实测：`Tencent.CodeBuddyCN` 和 `com.qodercn.app` 在通知库里
注册了处理器，却**从来没发出过一条通知**）。翻 CodeBuddy 的扩展代码能看到原因：

    async sendSystemNotification(ir) {
      if (vscode.window.state.focused)      // 窗口聚焦时直接跳过
        return void (ir.showWhenFocused ? ... : console.log("Window is focused, skipping..."));
      ...
    }

审批弹窗恰恰只在你正看着窗口时才出现，于是这条通知永远发不出来。

所以补一条**不依赖对方配合**的路：直接读窗口的可访问性树。

**为什么不是枚举子窗口**

Electron 整个界面只有一个 Chromium 渲染面（`Chrome_RenderWidgetHostHWND`），
审批条是画在里面的，不是原生子窗口，`EnumChildWindows` 什么也看不到。
只能走 UI Automation：从**渲染面**（不是顶层窗口）取元素，才能拿到整棵树。

**几个容易踩的坑**

  · 从顶层窗口取元素只能看到标题栏那几个按钮，必须从渲染面取。
  · Chromium 是「按需」建可访问性树的，第一次请求会慢一点，之后就稳定了；
    实测取按钮约 70ms、全树遍历（约 900 个节点）约 250ms。
  · COM 接口的槽位顺序是从类型库读出来的（见下面的常量注释），手写容易错位，
    改之前请用 UIAutomationClient 类型库核对。
  · 每个线程用 COM 前都要 CoInitializeEx，所以后台线程里要自己调一次。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

ole32 = ctypes.WinDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")
user32 = ctypes.WinDLL("user32")

# ---------------------------------------------------------------- 常量

# CLSID_CUIAutomation / IID_IUIAutomation（取自 UIAutomationClient 类型库）
CLSID_CUIAutomation = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"
IID_IUIAutomation = "{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}"

# IUIAutomation 的 vtable 槽位（IUnknown 占 0/1/2）
_IUIA_ElementFromHandle = 6
_IUIA_CreatePropertyCondition = 23
_IUIA_CreateOrCondition = 28

# IUIAutomationElement 的 vtable 槽位
_EL_FindAll = 6
_EL_CurControlType = 21
_EL_CurName = 23

# IUIAutomationElementArray 的 vtable 槽位
_ARR_Length = 3
_ARR_GetElement = 4

# IUIAutomationPropertyId
UIA_ControlTypePropertyId = 30003
UIA_NamePropertyId = 30005
UIA_ButtonControlTypeId = 50000
TreeScope_Descendants = 4

VT_I4 = 3
VT_BSTR = 8
COINIT_APARTMENTTHREADED = 0x2
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106，重复初始化时忽略即可

CLSCTX_INPROC_SERVER = 0x1

CHROME_RENDER_CLASS = "Chrome_RenderWidgetHostHWND"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        t = text.strip().strip("{}")
        # 形如 FF48DBA4-60EF-4201-AA87-54103EEF594E，最后两段拼成 8 字节
        p1, p2, p3, p4, p5 = t.split("-")
        rest = bytes.fromhex(p4 + p5)
        return cls(int(p1, 16), int(p2, 16), int(p3, 16), (ctypes.c_ubyte * 8)(*rest))


class VARIANT(ctypes.Structure):
    """只够用来说明「ControlType == Button」这一种取值。"""

    class _Value(ctypes.Union):
        _fields_ = [
            ("llVal", ctypes.c_longlong),
            ("lVal", ctypes.c_long),
            ("dblVal", ctypes.c_double),
            ("p", ctypes.c_void_p),
            ("pad", ctypes.c_byte * 16),
        ]

    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("value", _Value),
    ]


ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
oleaut32.SysAllocString.restype = ctypes.c_void_p
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


# ---------------------------------------------------------------- COM 底层

def com_ready() -> bool:
    """当前线程准备好 COM。重复调用是安全的。"""
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    return hr in (0, 1, RPC_E_CHANGED_MODE)


def _slot(ptr, index, restype, *argtypes):
    """取 COM 接口的第 index 个 vtable 槽，包成可调用对象。"""
    table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(table[index])


def _release(ptr) -> None:
    if ptr:
        try:
            _slot(ptr, 2, ctypes.c_long)(ptr)
        except Exception:
            pass


def _bstr(value) -> str:
    """把 _get_CurrentName 返回的 BSTR 取出来并释放。"""
    if not value:
        return ""
    try:
        return ctypes.cast(value, ctypes.c_wchar_p).value or ""
    finally:
        oleaut32.SysFreeString(value)


def _bstr_alloc(text: str):
    """造一个 BSTR，用完要自己 SysFreeString。"""
    return oleaut32.SysAllocString(text)


class Automation:
    """一个 IUIAutomation 实例。同一个线程复用一个就够。"""

    def __init__(self) -> None:
        self._uia = None
        self._conds: dict[int, ctypes.c_void_p] = {}
        self._name_conds: dict[tuple, ctypes.c_void_p] = {}
        if not com_ready():
            return
        clsid = GUID.parse(CLSID_CUIAutomation)
        iid = GUID.parse(IID_IUIAutomation)
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
                                    ctypes.byref(iid), ctypes.byref(ptr))
        if hr == 0 and ptr.value:
            self._uia = ptr

    @property
    def ok(self) -> bool:
        return bool(self._uia)

    def close(self) -> None:
        for cond in list(self._conds.values()) + list(self._name_conds.values()):
            _release(cond)
        self._conds.clear()
        self._name_conds.clear()
        _release(self._uia)
        self._uia = None

    # -------------------------------------------------- 内部

    def _property_condition(self, prop_id: int, value: VARIANT):
        cond = ctypes.c_void_p()
        fn = _slot(self._uia, _IUIA_CreatePropertyCondition, ctypes.c_long,
                   ctypes.c_int, ctypes.POINTER(VARIANT), ctypes.POINTER(ctypes.c_void_p))
        hr = fn(self._uia, prop_id, ctypes.byref(value), ctypes.byref(cond))
        return cond if hr == 0 and cond.value else None

    def _condition_for(self, control_type: int):
        """惰性创建「ControlType == 某类型」条件并按类型缓存。"""
        cached = self._conds.get(control_type)
        if cached:
            return cached
        val = VARIANT()
        val.vt = VT_I4
        val.value.lVal = control_type
        cond = self._property_condition(UIA_ControlTypePropertyId, val)
        if cond:
            self._conds[control_type] = cond
        return self._conds.get(control_type)

    def _name_condition(self, names):
        """「Name 等于 names 里任意一个」的 OR 条件，按名字集合缓存。

        UIA 的属性条件只做**相等**比较，没有「包含」，所以这里把每个候选
        名字做成一个条件再 OR 起来。好处是筛选发生在 provider 内部，
        不用把几百个元素搬过来再逐一看名字（那要 1 秒以上）。
        """
        key = tuple(names)
        cached = self._name_conds.get(key)
        if cached:
            return cached

        parts = []
        for text in key:
            b = _bstr_alloc(text)
            val = VARIANT()
            val.vt = VT_BSTR
            val.value.p = b
            cond = self._property_condition(UIA_NamePropertyId, val)
            if b:
                oleaut32.SysFreeString(b)
            if cond:
                parts.append(cond)
        if not parts:
            return None

        merged = parts[0]
        if len(parts) > 1:
            orer = _slot(self._uia, _IUIA_CreateOrCondition, ctypes.c_long,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            for nxt in parts[1:]:
                out = ctypes.c_void_p()
                if orer(self._uia, merged, nxt, ctypes.byref(out)) == 0 and out.value:
                    _release(merged)
                    _release(nxt)
                    merged = out
                else:
                    _release(nxt)
        self._name_conds[key] = merged
        return merged

    def _element(self, hwnd: int):
        el = ctypes.c_void_p()
        fn = _slot(self._uia, _IUIA_ElementFromHandle, ctypes.c_long,
                   wintypes.HWND, ctypes.POINTER(ctypes.c_void_p))
        hr = fn(self._uia, hwnd, ctypes.byref(el))
        return el if hr == 0 and el.value else None

    # -------------------------------------------------- 对外

    def control_names(self, hwnd: int, control_type: int = UIA_ButtonControlTypeId) -> list[str]:
        """列出某个窗口下所有指定类型控件的名字。

        只查一类控件（比如按钮）时，实测约 70ms；比整棵树走一遍（约 250ms）划算得多。
        """
        if not self._uia or not hwnd:
            return []
        cond = self._condition_for(control_type)
        if not cond:
            return []
        return self._names_with_condition(hwnd, cond)

    def matching_names(self, hwnd: int, names) -> list[str]:
        """列出窗口里**名字恰好等于**names 中某一项的元素。

        走的是 Name 属性的等值条件，筛选在 provider 里完成，
        比「把整棵树的元素搬过来再逐个看名字」快一个数量级。
        """
        if not self._uia or not hwnd:
            return []
        cond = self._name_condition(names)
        if not cond:
            return []
        return self._names_with_condition(hwnd, cond)

    def _names_with_condition(self, hwnd: int, cond) -> list[str]:
        root = self._element(hwnd)
        if not root:
            return []
        try:
            arr = ctypes.c_void_p()
            fn = _slot(root, _EL_FindAll, ctypes.c_long, ctypes.c_int,
                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            if fn(root, TreeScope_Descendants, cond, ctypes.byref(arr)) != 0 or not arr.value:
                return []
            try:
                n = ctypes.c_int()
                _slot(arr, _ARR_Length, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(arr, ctypes.byref(n))
                out = []
                get = _slot(arr, _ARR_GetElement, ctypes.c_long, ctypes.c_int,
                            ctypes.POINTER(ctypes.c_void_p))
                for i in range(n.value):
                    child = ctypes.c_void_p()
                    if get(arr, i, ctypes.byref(child)) != 0 or not child.value:
                        continue
                    try:
                        name = ctypes.c_void_p()
                        g = _slot(child, _EL_CurName, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))
                        if g(child, ctypes.byref(name)) == 0:
                            text = _bstr(name.value)
                            if text.strip():
                                out.append(text.strip())
                    finally:
                        _release(child)
                return out
            finally:
                _release(arr)
        finally:
            _release(root)

    def all_text(self, hwnd: int, limit: int = 400) -> list[str]:
        """把窗口里所有带名字的元素都读出来，用于排查（比较慢，别放进轮询）。"""
        if not self._uia or not hwnd:
            return []
        root = self._element(hwnd)
        if not root:
            return []
        walker = ctypes.c_void_p()
        fn = _slot(self._uia, 16, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))  # _get_RawViewWalker
        if fn(self._uia, ctypes.byref(walker)) != 0 or not walker.value:
            _release(root)
            return []

        first = _slot(walker, 4, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        nxt = _slot(walker, 6, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        name_fn = _slot(root, _EL_CurName, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))

        out: list[str] = []

        def rec(el, depth=0):
            if depth > 30 or len(out) >= limit:
                return
            name = ctypes.c_void_p()
            if name_fn(el, ctypes.byref(name)) == 0:
                t = _bstr(name.value)
                if t.strip():
                    out.append(t.strip())
            child = ctypes.c_void_p()
            if first(walker, el, ctypes.byref(child)) == 0 and child.value:
                while child.value and len(out) < limit:
                    nxt_ = ctypes.c_void_p()
                    rec(child, depth + 1)
                    if nxt(walker, child, ctypes.byref(nxt_)) != 0 or not nxt_.value:
                        _release(child)
                        break
                    _release(child)
                    child = nxt_

        try:
            rec(root)
        finally:
            _release(walker)
            _release(root)
        return out


# ---------------------------------------------------------------- 窗口定位

def find_render_surface(hwnd: int) -> int:
    """找到 Chromium 的渲染面句柄；找不到就返回原句柄。

    必须从渲染面取元素才能看到界面内容——从顶层窗口取只能拿到标题栏那几个按钮。
    """
    if not hwnd:
        return 0
    found = [0]

    CBF = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(child, _lp):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(child, buf, 256)
        if buf.value == CHROME_RENDER_CLASS:
            found[0] = int(child)
            return False
        return True

    try:
        user32.EnumChildWindows(hwnd, CBF(cb), 0)
    except Exception:
        pass
    return found[0] or hwnd


# ---------------------------------------------------------------- 审批判定

# CodeBuddy 审批条的文案。取自扩展的多语言资源
# （D:\CodeBuddy CN\...\genie\out\webviews\main.*.chunk.js）：
#
#     tool.executeCommand.run             运行
#     tool.executeCommand.skip            跳过
#     tool.executeCommand.reject          拒绝
#     tool.executeCommand.cancel          取消
#     tool.executeCommand.dangerousCommand  包含危险命令，是否仍要运行？
#     tool.executeCommand.runCommand      确认执行命令？
#     tool.writeFile.confirmWrite         确认写入？   /  tool.writeFile.accept  接受
#     tool.deleteFile.confirm             删除文件需要用户确认
#
# **为什么有两条通道**——同一个审批条有两种画法：
#
# 1）「按钮通道」：紧凑版（`execute-command-compact`）用的是真正的 <button>，
#    在 UIA 里就是 Button 控件类型。判据是
#    「有一个『拒绝』类」+「同时有一个『执行/跳过/允许』类」。
#    「拒绝」这个词几乎不会出现在别处，组合起来误报率很低；只认「运行」不行——
#    VS Code 的菜单栏里本来就有一个「运行」。特意**不**把「取消」算进来，它太常见了。
#
# 2）「标题通道」：主审批条（ToolApprovalMenu）渲染成
#        <div class="approval-menu-item" onclick=...>运行</div>
#    ——**普通 div，不是按钮**（实测整个渲染面 2137 个节点里只有 43 个 Button），
#    按钮通道看不见它。但它的标题是一段几乎不会撞车的固定文案，
#    所以用 Name 等值条件直接筛，命中即判定。
_APPROVAL_REJECT = ("拒绝", "reject", "deny", "decline")
_APPROVAL_ACTION = ("运行", "跳过", "允许", "总是允许", "run", "skip", "allow")

_APPROVAL_TITLES = (
    "包含危险命令，是否仍要运行？",
    "确认执行命令？",
    "删除文件需要用户确认",
    "确认写入？",
    "Contains dangerous command, run anyway?",
    "Run Command?",
    "File deletion requires user confirmation",
    "Confirm Write?",
)


def approval_labels(control_names) -> tuple[str, str] | None:
    """命中审批条时返回（拒绝类按钮, 执行类按钮），否则 None。便于排查。"""
    if not control_names:
        return None
    pool = {n.strip().lower(): n.strip() for n in control_names if n and n.strip()}
    reject = next((pool[r.lower()] for r in _APPROVAL_REJECT if r.lower() in pool), None)
    if not reject:
        return None
    action = next((pool[a.lower()] for a in _APPROVAL_ACTION if a.lower() in pool), None)
    if not action:
        return None
    return reject, action


def looks_like_approval(control_names) -> bool:
    """按钮名字里是否出现了审批条的特征组合。"""
    return approval_labels(control_names) is not None


def probe_detail(hwnd: int, automation: "Automation | None" = None):
    """判断窗口是不是在等审批，并说明是**哪条通道**命中的。排查用。

    返回 (是否待审批, 证据说明)。
    """
    own = automation is None
    auto = automation or Automation()
    try:
        if not auto.ok:
            return False, "UIA 不可用"
        render = find_render_surface(hwnd)
        labels = approval_labels(auto.control_names(render))
        if labels:
            return True, "按钮通道命中 %r" % (labels,)
        titles = auto.matching_names(render, _APPROVAL_TITLES)
        if titles:
            return True, "标题通道命中 %r" % (titles,)
        return False, "两条通道都没命中"
    except Exception as exc:
        return False, "探针异常: %r" % (exc,)
    finally:
        if own:
            auto.close()


def probe_approval(hwnd: int, automation: Automation | None = None) -> bool:
    """就着窗口句柄判断它是不是在等审批。"""
    own = automation is None
    auto = automation or Automation()
    try:
        if not auto.ok:
            return False
        render = find_render_surface(hwnd)
        if looks_like_approval(auto.control_names(render)):
            return True
        return bool(auto.matching_names(render, _APPROVAL_TITLES))
    except Exception:
        return False
    finally:
        if own:
            auto.close()
