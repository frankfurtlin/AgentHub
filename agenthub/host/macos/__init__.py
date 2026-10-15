# -*- coding: utf-8 -*-
"""macOS 平台后端（尚未实现）。

这个文件目前只是**占位**，但它把「要做什么」写清楚了——照着 host/windows/
的四块结构补齐即可，上层代码一行都不用动。

--------------------------------------------------------------------------
待办清单
--------------------------------------------------------------------------

1. 窗口枚举与激活（对标 windows/winapi.py）

   用 pyobjc 的 AppKit + Quartz：

       from AppKit import NSWorkspace, NSRunningApplication
       from Quartz import (CGWindowListCopyWindowInfo,
                           kCGWindowListOptionOnScreenOnly,
                           kCGNullWindowID)

   - 进程 -> 窗口：CGWindowListCopyWindowInfo 的 kCGWindowOwnerPID
   - 激活：NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
           .activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
   - 最小化：NSRunningApplication 本身没有这个状态；对窗口用
             kCGWindowIsOnscreen 近似，或走 AX（AXMinimized）更准

   注意两个坑：
     a) kCGWindowName（窗口标题）从 10.15 起需要「屏幕录制」权限，没授权时
        读回来是空串。所以标题优先从 AX 拿（见第 2 条），CGWindowList 只用来
        拿窗口 ID 和层级。
     b) 也可以换 pywinctl 统一封装，但它在 macOS 底层是 AppleScript，
        getActiveWindowTitle() 实测 400~500ms，绝不能放主线程。

2. 界面探针（对标 windows/uia.py）

   AXUIElement：

       from ApplicationServices import (AXUIElementCreateApplication,
                                        AXUIElementCopyAttributeValue)

   递归读 AXChildren / AXTitle / AXRole，判据直接沿用 uia.py 的
   _APPROVAL_REJECT / _APPROVAL_ACTION / _APPROVAL_TITLES 三组常量。

   **必须先拿「辅助功能」权限**，这是 macOS 上绕不过去的一步：
       系统设置 > 隐私与安全性 > 辅助功能
   程序启动时应检测并做引导：
       AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
   没授权时 probe_ready() 返回 False，界面要给出明确提示而不是静默失效。

3. 通知读取（对标 windows/notify.py）

   macOS 把通知存在：
       ~/Library/Group Containers/group.com.apple.usernoted/db2/db
   它与记录表 app.identifier 关联，记录里是**二进制 plist**——
   Python 内置的 plistlib 就能解，不需要第三方库：

       import plistlib
       plistlib.loads(blob)

   db 是 SQLite，但 Apple 通常开着 WAL 且加了锁；只读打开时建议
   `file:...?mode=ro&immutable=1`。没权限时静默退化为「只靠界面探针」。

4. 图标提取（对标 windows/icons.py）

   比 Windows 简单得多，系统直接给 NSImage：

       from AppKit import NSWorkspace
       icon = NSWorkspace.sharedWorkspace().iconForFile_(app_path)
       icon.setSize_((size, size))
       tiff = icon.TIFFRepresentation()
       # NSBitmapImageRep -> PNG

   同样输出透明底 PNG，接口保持不变。

5. 其他

   - config_dir()：~/Library/Application Support/AgentHub
   - reveal()：subprocess.Popen(["open", "-R", path])
   - 开机自启改为 LaunchAgent plist，而不是注册表

--------------------------------------------------------------------------
在没实现之前，get_backend() 会抛错并给出这段说明，而不是让程序带着
一堆 AttributeError 跑起来。
"""

from __future__ import annotations


class MacOSBackend:  # pragma: no cover - 占位
    """未实现的 macOS 后端。

    保留类名是为了让 `get_backend()` 的分支结构完整——真开始做 macOS 时，
    照着上面待办清单把它填成和 WindowsBackend 同构的实现即可。
    """

    name = "macos"
    supports_ui_probe = False
    supports_notifications = False
    supports_icon_extract = False

    def __init__(self) -> None:
        raise NotImplementedError(
            "macOS 后端还没实现。要补的内容见 agenthub/host/macos/__init__.py "
            "顶部的待办清单；Windows 上请直接把 host 指向 windows。"
        )
