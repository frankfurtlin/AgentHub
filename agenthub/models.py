# -*- coding: utf-8 -*-
"""与平台无关的数据模型和文案判据。

这里的东西**不含任何系统调用**，Windows / macOS 共用：
    AgentDef / AGENTS   四个被纳管的工具的定义
    Notice              一条解析后的系统通知
    AgentState          某个工具此刻的状态（界面直接渲染它）
    classify()          把通知文案判成「要你审批」还是「跑完了」
    parse_toast()       从通知正文里抽标题和正文
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------- 定义

@dataclass(frozen=True)
class AgentDef:
    """一个被纳管的 AI 编程工具。

    `match` 是进程可执行文件名（小写比较）。同一个应用在不同平台上进程名
    可能不同，所以写成元组，命中任一即可——Windows 上是 `xxx cn.exe`，
    macOS 上通常是 `xxx cn`（不带后缀）。
    """

    key: str
    name: str
    match: tuple[str, ...]
    fill: str            # 品牌底衬（浅色主题）
    stroke: str          # 品牌色
    aumid_hints: tuple[str, ...] = ()   # 通知来源标识里用于识别它的关键字
    glyph: str = ""                     # 拿不到真实图标时的兜底字符
    fill_dark: str = ""                 # 深色主题下的底衬
    stroke_dark: str = ""               # 深色主题下的品牌色
    ui_probe: bool = False              # 是否额外用界面探针读审批状态
    #: 判断「这个工具装没装」的关键字，拿去匹配系统里的应用名（注册表的
    #: DisplayName、开始菜单快捷方式、MSIX 包名……）。全小写比较。
    #: 留空表示不做装机检测：只要平台能查到应用列表就永远认为它已安装。
    install_hints: tuple[str, ...] = ()


AGENTS: tuple[AgentDef, ...] = (
    AgentDef(
        key="codebuddy",
        name="CodeBuddy CN",
        match=("codebuddy cn.exe", "codebuddy cn", "codebuddy"),
        fill="#E1F5EE",
        stroke="#0F6E56",
        aumid_hints=("codebuddy",),
        glyph="C",
        fill_dark="#10352C",
        stroke_dark="#5FD8AC",
        # CodeBuddy 的通知通道实测指望不上（在系统里注册了处理器却一条没发过）：
        # 它只在窗口「不聚焦」时才发系统通知，而审批弹窗恰恰只在你看着窗口时出现。
        # 所以这里改用界面探针直接读。
        ui_probe=True,
        install_hints=("codebuddy",),
    ),
    AgentDef(
        key="trae",
        name="Trae CN",
        match=("trae cn.exe", "trae cn", "trae"),
        fill="#E6F1FB",
        stroke="#185FA5",
        aumid_hints=("trae",),
        glyph="T",
        fill_dark="#12314E",
        stroke_dark="#6FB6F5",
        install_hints=("trae",),
    ),
    AgentDef(
        key="qoder",
        name="Qoder CN",
        match=("qoder cn.exe", "qoder cn", "qoder"),
        fill="#EEEDFE",
        stroke="#534AB7",
        aumid_hints=("qoder",),
        glyph="Q",
        fill_dark="#2A2A55",
        stroke_dark="#A79CF5",
        ui_probe=True,   # 同 CodeBuddy：通知库里至今 0 条
        install_hints=("qoder",),
    ),
    AgentDef(
        key="workbuddy",
        name="WorkBuddy",
        match=("workbuddy.exe", "workbuddy"),
        fill="#EAF3DE",
        stroke="#3B6D11",
        aumid_hints=("workbuddy",),
        glyph="W",
        fill_dark="#26380F",
        stroke_dark="#A8D65A",
        install_hints=("workbuddy",),
    ),
    # Claude Desktop（微软商店 MSIX 版）。运行时进程名是包内的 Claude.exe，
    # 开始菜单别名是 claude-desktop.exe，两个都收。通知来源标识是
    # Anthropic 前缀，所以 aumid_hints 加了 anthropic。
    # 它是聊天应用、没有审批条，状态只靠通知 + CPU，不开界面探针。
    AgentDef(
        key="claude",
        name="Claude",
        match=("claude.exe", "claude-desktop.exe", "claude"),
        fill="#FAEDE6",
        stroke="#C15F3C",
        aumid_hints=("claude", "anthropic"),
        glyph="A",
        fill_dark="#3B241A",
        stroke_dark="#E89070",
        install_hints=("claude",),
    ),
    # DeepSeek Harness（Electron 应用，本机装在 D:\dsh\）。
    # 有 agent 执行审批流，Electron 渲染面 UIA 读得到，开界面探针——
    # 探针判据是固定审批文案 + 拒绝/执行按钮组合，读不到不会误报。
    AgentDef(
        key="deepseek",
        name="DeepSeek Harness",
        match=("deepseek harness.exe", "deepseek harness"),
        fill="#E9EDFE",
        stroke="#4D6BFE",
        aumid_hints=("deepseek", "aidsh", "dsh"),
        glyph="D",
        fill_dark="#182452",
        stroke_dark="#8598FF",
        ui_probe=True,
        install_hints=("deepseek", "harness", "dsh"),
    ),
    # Codex（OpenAI 的 CLI agent，本机在 %LOCALAPPDATA%\OpenAI\Codex\bin\）。
    # 注意它**没有自己的 GUI 窗口**——跑在 Windows Terminal / ConHost 里，
    # 窗口句柄按 PID 找不到（顶层窗口属于 WindowsTerminal.exe），
    # 所以点卡片切不动它。能提供的是「在不在跑」和 CPU 忙闲：
    # codex.exe 干活时会持续吃 CPU，走 Trae 同款的 busy 判定。
    # 通知通道指望不上（CLI 不发 toast），aumid_hints 留着以防以后有桌面壳。
    AgentDef(
        key="codex",
        name="Codex",
        match=("codex.exe", "codex-command-runner.exe"),
        fill="#E4F4EF",
        stroke="#10A37F",
        aumid_hints=("codex", "openai"),
        glyph="X",
        fill_dark="#103529",
        stroke_dark="#4FD1A5",
        install_hints=("codex", "openai"),
    ),
)

AGENTS_BY_KEY = {agent.key: agent for agent in AGENTS}


# ---------------------------------------------------------------- 文案判据

# 先判「要你出手」，再判「跑完了」。判据都来自各家自己的文案，中英都收。
#
#   Trae      「等待操作」/ 任务正在等待您的回复
#   WorkBuddy 「任务等待确认，请处理」/ 任务已完成
#   CodeBuddy 「工具权限请求」「等待您的回复」/「任务已完成」
#             —— 取自 CodeBuddy 扩展的多语言资源 genie/l10n/bundle.l10n.zh-cn.json：
#                tool_permission : "CodeBuddy 需要使用 {0} 的权限。请审查并批准工具使用。"
#                wait_user_reply : "CodeBuddy 正在等待您的回复。请检查对话并提供所需信息。"
#                task_completed  : "任务已成功完成。{0}"
#             （界面语言是英文时走英文原文 Tool Permission Required /
#               Waiting for Your Response / Task has been completed successfully）
APPROVAL_HINTS = (
    # 中文通用
    "等待确认", "等待操作", "等待回复", "等待批准", "等待授权", "等待您的回复",
    "请处理", "需要确认", "需要授权", "需要批准", "待审批", "请求授权",
    "确认后继续", "需要你的确认", "等待你", "等你",
    # CodeBuddy 专用（工具权限请求）
    "工具权限请求", "权限请求", "审查并批准", "需要使用",
    # 英文通用
    "approval", "approve", "confirm", "waiting", "permission",
    "tool permission", "needs permission", "permission to use",
)
DONE_HINTS = (
    "已完成", "任务完成", "完成", "失败", "出错", "错误", "异常", "结束",
    "done", "finished", "failed", "completed", "error",
)

KIND_APPROVAL = "approval"
KIND_DONE = "done"
KIND_INFO = "info"


def classify(title: str, body: str) -> str:
    """判断这条通知是「要你审批」还是「跑完了」。"""
    blob = ("%s %s" % (title, body)).lower()
    for hint in APPROVAL_HINTS:
        if hint.lower() in blob:
            return KIND_APPROVAL
    for hint in DONE_HINTS:
        if hint.lower() in blob:
            return KIND_DONE
    return KIND_INFO


_TEXT_RE = re.compile(r"<text[^>]*>(.*?)</text>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_toast(payload) -> tuple[str, str]:
    """从通知正文里抽出 (标题, 正文)。

    Windows 存的是 toast XML；macOS 那边解出来也是类似的带标签文本，
    所以这个函数两边共用。
    """
    if isinstance(payload, bytes):
        raw = payload.decode("utf-8", "replace")
    else:
        raw = payload or ""
    parts = []
    for chunk in _TEXT_RE.findall(raw):
        text = html.unescape(_TAG_RE.sub("", chunk))
        text = re.sub(r"\s+", " ", text).strip()
        parts.append(text)
    title = parts[0] if parts else ""
    body = parts[1] if len(parts) > 1 else ""
    return title, body


def match_installed(app_names) -> set[str]:
    """把系统里的应用名清单映射成「已安装的 agent key」。

    只做包含匹配（都是小写）：注册表里叫 `CodeBuddy CN (User)`、快捷方式叫
    `TraeCode CN`、MSIX 包叫 `Claude_1.5.0.0_x64__pzs8sxrjxfjjc`——名字后面
    挂什么都可能，所以不要求全等。
    """
    low = [str(name).lower() for name in app_names if name]
    keys: set[str] = set()
    for agent in AGENTS:
        if not agent.install_hints:
            continue
        for hint in agent.install_hints:
            if any(hint in name for name in low):
                keys.add(agent.key)
                break
    return keys


def match_agent(primary_id: str) -> str | None:
    """把通知来源标识（如 WorkBuddy.WorkBuddy）映射到 Agent key。"""
    low = (primary_id or "").lower()
    if not low:
        return None
    for agent in AGENTS:
        for hint in agent.aumid_hints:
            if hint in low:
                return agent.key
    return None


# ---------------------------------------------------------------- 运行时状态

@dataclass
class Notice:
    """一条解析后的系统通知。"""

    order: int
    agent_key: str
    kind: str            # approval / done / info
    title: str
    body: str
    at: float            # unix 时间戳

    @property
    def text(self) -> str:
        """面板里显示的一行摘要。"""
        if self.title and self.title != self.body:
            return self.title
        return self.body or self.title


@dataclass
class AgentState:
    """某个工具此刻的状态——界面直接渲染这个对象。"""

    definition: AgentDef
    running: bool = False
    pid: int = 0
    handle: int = 0                       # 窗口句柄（Win32 HWND / macOS CGWindowID）
    visible: bool = True
    minimized: bool = False
    cpu: float = 0.0                      # 占单核百分比
    busy: bool = False                    # 持续高 CPU -> 正在输出
    unread: int = 0                       # 未查看的「完成」通知数
    approvals: list[Notice] = field(default_factory=list)   # 未处理的审批请求
    notice: Notice | None = None          # 最近一条可展示的通知
    orders: list[int] = field(default_factory=list)         # 已并入本状态的通知 Order
    ui_approval: bool = False             # 界面上正摆着审批条（界面探针读到的）

    @property
    def key(self) -> str:
        return self.definition.key

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def waiting(self) -> bool:
        """是不是在等你出手——通知或界面任一说是就是。"""
        return bool(self.approvals) or self.ui_approval

    @property
    def status(self) -> str:
        if not self.running:
            return "未启动"
        if self.waiting:
            return "待审批"
        if self.unread:
            return "有新结果"
        if self.busy:
            return "输出中"
        return "空闲中"

    @property
    def window_hint(self) -> str:
        """窗口状态，作为次要信息缀在主状态后面。"""
        if not self.running:
            return ""
        if self.minimized:
            return "最小化"
        if not self.visible:
            return "托盘"
        return ""

    @property
    def detail(self) -> str:
        """卡片第二行：通知摘要，没有就留空。"""
        if self.approvals:
            return self.approvals[-1].text
        if self.ui_approval:
            # 这条是从界面上读出来的，没有通知文案可用，给一句人话
            return "界面上有命令等你确认"
        if self.notice:
            return self.notice.text
        return ""

    @property
    def level(self) -> int:
        """0 无 / 1 完成提醒 / 2 审批提醒。数字越大越紧急。"""
        if self.waiting:
            return 2
        if self.unread:
            return 1
        return 0

    def as_dict(self) -> dict:
        """给前端的 JSON。字段名和前端约定好，改这里要同步改 app.js。"""
        definition = self.definition
        return {
            "key": definition.key,
            "name": definition.name,
            "glyph": definition.glyph,
            "fill": definition.fill,
            "stroke": definition.stroke,
            "fillDark": definition.fill_dark,
            "strokeDark": definition.stroke_dark,
            "running": self.running,
            "visible": self.visible,
            "minimized": self.minimized,
            "cpu": round(self.cpu, 1),
            "badge": "!",          # 占位：由前端按 level 决定
            "status": self.status,
            "hint": self.window_hint,
            "detail": self.detail,
            "level": self.level,
            "waiting": self.waiting,
        }


def now() -> float:
    return time.time()
