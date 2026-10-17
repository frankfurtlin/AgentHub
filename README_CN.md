# AgentHub

**桌面上蹲着一只水豚，它知道哪个 AI 编程工具正在等你。**

[English](README.md) · [下载最新构建](https://github.com/frankfurtlin/AgentHub/releases/latest)

---

## 要解决什么

四个 GUI 版 AI 编程助手同时干活，真正难受的不是它们干得不好，而是
**你不知道哪一个正卡在那里等你点一下**。

CodeBuddy 的权限弹窗、Trae 的「等待操作」、Qoder 的授权请求——都躲在各自的窗口里。
不切过去看不见，切过去又打断手上的事。

AgentHub 常驻桌面角落，谁在等你就亮谁，点卡片直接切过去。

```
                 ┌──────────────────────────────┐
   悬浮球        │  AgentHub          3/7 运行中  │  ← 点球展开
   （贴边）      │ ────────────────────────────  │
    ●●●●●●●      │  [W] WorkBuddy     待审批   › │  ← 点卡片切窗口
                │  [D] DeepSeek   空闲中·托盘 › │
                │  [C] CodeBuddy     输出中   › │     一个点一个工具，
                │  [T] Trae CN       未启动   › │     颜色跟着状态变
                │  [Q] Qoder CN      未启动   › │
                │ ────────────────────────────  │
                │  有 1 个工具在等你确认         │
                └──────────────────────────────┘
```

---

## 为什么这么做

### 两条独立的判断通道

只靠系统通知**是行不通的**：本机实测 CodeBuddy 和 Qoder 在通知库里**躺着 0 条**
记录——它们注册了处理器，但只在窗口「不聚焦」时才发通知，而审批弹窗恰恰只在你
看着窗口时出现，逻辑上死锁。

所以除了读通知，还额外用 UI Automation **直接读界面上有没有摆着审批条**。

| 通道 | 怎么读 | 实测情况 |
|---|---|---|
| 系统通知 | 读通知中心的 SQLite 库 | CodeBuddy / Qoder 0 条，单靠它必漏 |
| 界面探针 | UI Automation 直接读界面 | 兜住了上面两家；一次约 80ms，所以单独起一条 2.5s 间隔的线程，不跟着主循环跑 |

任一条说「在等你」就算数，取并集。

### 只在必要的时候出声

告警只在**边沿**触发——「不在等 → 在等」这个跳变的瞬间，且一次跳变只响一声。
不会每轮扫描都提示，切个窗口也不会被轰炸。

### 尽量不留下存在感

不驻留服务、不写注册表、不装驱动。配置就是一个 JSON 文件。
窗口从任务栏里摘掉（改成工具窗），入口放在系统托盘。

---

## 上手

### 方式一：下载构建好的包（不用装 Python）

从 [latest release](https://github.com/frankfurtlin/AgentHub/releases/latest)
下载对应平台的 zip，解压，运行。

> **Windows** 需要 WebView2 Runtime。Win11 自带；Win10 一般随 Edge 装好了。
> 它是系统组件，没有跟包一起发。
>
> **macOS**：包是未签名的，第一次启动要右键 → 打开（Gatekeeper 会拦）。
> 另外 macOS 后端还是占位实现，见[平台现状](#平台现状)。

### 方式二：从源码跑

```bash
git clone https://github.com/frankfurtlin/AgentHub.git
cd AgentHub

python -m venv .venv
.venv\Scripts\activate            # macOS: source .venv/bin/activate
pip install -e .
python -m agenthub
```

不想开界面、只想看看它读到什么：

```bash
python -m agenthub --once
```

---

## 支持的工具

只展示**这台机器上真的装了**的工具。七家里装三家，面板就只出现三张卡，
不会有一排永远灰着的卡片。

| 工具 | 判断方式 | 怎么认出来它装了 |
|---|---|---|
| CodeBuddy CN | 界面探针 | 注册表卸载项 / 开始菜单快捷方式 |
| Trae CN | 通知 + CPU | 同上 |
| Qoder CN | 界面探针 | 同上 |
| WorkBuddy | 通知 | 同上 |
| Claude | 通知 | 商店版（MSIX），只有 `AppModel\Repository\Packages` 里看得到 |
| DeepSeek Harness | 通知 | 注册表 + 托盘 |
| Codex | 通知 | CLI，没有安装程序，只能探 `codex.exe` |

检测是**四路取并集**：注册表卸载项（HKCU / HKLM，含 WOW6432Node 视图）、
开始菜单 `.lnk` 名、MSIX 包名、几个 exe 探路。
**正在跑的进程一律算已装**——绿色版、CLI 都不留安装痕迹。
结果缓存 5 分钟，中途装新工具不用重启。

### 加一个工具

在 [`agenthub/models.py`](agenthub/models.py) 的 `AGENTS` 里加一条：
进程名用来找它，品牌色用来画图标底衬，`ui_probe=True` 表示额外用界面探针读审批，
`install_hints` 给小写关键词（能出现在安装名 / 快捷方式名里的片段）。

---

## 怎么用

| 操作 | 结果 |
|---|---|
| 轻点球 | 展开 / 收起面板 |
| 按住球拖 | 挪位置（位移超过 4px 才算拖，不会误触） |
| 点卡片 | 把那个工具的窗口切到前台（最小化 / 托盘都能唤起） |
| `Esc` 或右上角 × | 收起面板 |
| 托盘图标 · 左键 | 开合面板 |
| 托盘图标 · 右键 | 显示/隐藏面板、退出 |

面板贴着球靠近的那条屏幕边，高度**跟着内容自动伸缩**（夹紧到 220–900，
另有一道按屏高收的保险）。位置跨重启记忆。

### 状态

| 状态 | 级别 | 含义 |
|---|---|---|
| 待审批 | 2 | 有东西摆在那儿等你确认 |
| 有新结果 | 1 | 任务跑完了，结果等着看 |
| 输出中 | 0 | 在跑，CPU 有占用 |
| 空闲中 | 0 | 在跑，但不忙 |
| 未启动 | 0 | 没找到进程 |

次要信息缀在后面：`· 最小化`、`· 托盘`。

---

## 结构

```
agenthub/
  models.py       数据模型 + 文案判据（无系统调用，两端共用）
  runtime.py      状态中枢：定时扫描 → 合并两条通道 → 算告警边沿 → 出快照
  app.py          pywebview 外壳：两个无边框透明窗口 + 暴露给前端的桥
  webview_env.py  启动 WebView 前的环境准备（有一处不是可选项，见 docs）
  tray.py         托盘图标
  host/
    base.py            平台抽象层（Backend / UiProbeSession / WindowInfo）
    windows/           Win32 实现：winapi（纯 ctypes）、uia（UIA 探针）、
                       icons（从 exe 抠图标）、notify（读通知库）、installed（装机检测）
    macos/             macOS 实现（待写）
web/
  index.html      两个视图的模板（悬浮球 / 状态面板）
  app.js          渲染 + 交互 + 与 Python 通信
  tokens.css      设计 token（颜色、间距、圆角、阴影）
  style.css       组件样式
tools/
  build.py            打包（PyInstaller）
  selftest.py         底层能力自检（不看界面，先分层定位问题）
  probe_uia.py        界面探针诊断：当场看各家工具界面里读到了什么
  install_startup.py  开机自启开关
  make_tray_icon.py   生成托盘图标
tests/               纯逻辑测试，不需要 GUI
docs/
  development.md      开发过程：三代界面方案的取舍、架构决策、踩坑清单
  roadmap.md          下一步：已知问题、优化点、功能拓展想法
  packaging.md        打包与分发
  webview2-notes.md   WebView2 / pywebview 专项排查笔记
```

三条分层原则：

1. **平台相关的全部关进 `host/`。** `Backend` 是抽象基类，上层只认它。
   Windows 已经完整实现；macOS 只要照着填同一个类，上层一行不用改。
2. **`models.py` 里不许有系统调用。** 通知怎么分类（`classify()`）、
   什么算「在等你」（`AgentState.waiting`），都是纯函数，可单测、可跨平台复用。
3. **状态在 Python 侧算完，前端只渲染。** 前端拿到的是一份快照，不做判断。
   这样将来换界面（CLI、菜单栏 App）都不用重写逻辑。

---

## 配置

配置文件在 `%APPDATA%\AgentHub\config.json`
（macOS 是 `~/Library/Application Support/AgentHub/config.json`），
首次运行自动生成。**不进仓库**——里面是本机 exe 路径和悬浮球坐标。
模板见 [`config.example.json`](config.example.json)。

```jsonc
{
  "exePaths": {},        // 手动指定某个工具的可执行文件；留空则从运行中的进程里找
  "ballPos": null,       // 悬浮球位置 [x, y]；null = 贴屏幕右下角
  "refreshMs": 1500,     // 主循环扫描间隔
  "probeIntervalS": 2.5, // 界面探针间隔（一次约 80ms，没必要跟着主循环跑）
  "theme": "auto",       // auto 跟随系统 / light / dark
  "sound": true,         // 需要你出手时响一声
  "onTop": true,
  "webviewArgs": []      // 追加给 WebView2 的启动参数，一般不用动
}
```

---

## 平台现状

| | |
|---|---|
| Windows | ✅ 完整 —— 进程、窗口、UIA 探针、通知、图标、托盘、开机自启 |
| macOS | 🚧 接口已切好，`agenthub/host/macos/` 还是占位实现，待办清单写在文件顶部 |

macOS 后端要补五块（窗口 / AX 探针 / 通知库 / 图标 / 杂项），
`agenthub/host/macos/__init__.py` 顶部的清单里每块都写了要用哪些 API。

---

## 排查

**界面不动、图标空白、点了没反应。** 先用自检脚本把「探测层」和「界面层」分开：

```bash
python tools/selftest.py        # 11 项；全 PASS 说明问题在 WebView 那一侧
python tools/probe_uia.py       # 界面探针专项：各家工具界面里到底读到了什么
```

自检全过但界面不对，看 [`docs/webview2-notes.md`](docs/webview2-notes.md)。

**开机自启：** `python tools/install_startup.py on|off|status`

---

## 开发

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q        # 22 个用例，不需要 GUI
python -m pytest tests/test_x.py  # 也可以单独跑一个文件
```

打包：`python tools/build.py --clean --zip`，细节见
[`docs/packaging.md`](docs/packaging.md)。

每次往 `main` 推代码会自动构建 Windows 与 macOS 包并重新发布 `latest` release，
见 [`.github/workflows/build.yml`](.github/workflows/build.yml)。

贡献说明：[`CONTRIBUTING.md`](CONTRIBUTING.md)。

---

## 关于形象

球上那只水豚是**自己画的 SVG**（`web/index.html` 里的 `tpl-ball`），不是任何现成的 IP。

特意写一段说明，是因为这件事总会被问到：「能不能用水豚噜噜那个形象？」——不能。
那类热门形象是有版权方的商业 IP（噜噜的版权在铁罐文化，正在做盲盒、联名等付费
授权），把它的形象画进一个 MIT 开源项目里，风险是明确的。但**「水豚」这个物种
本身没有版权**，所以照着物种特征（圆脸、小圆耳、宽鼻吻、黑豆眼）独立画一只，
就既干净又不影响可爱程度——成品就是球上这只。

想改的话：

- **配色**：全在 `web/tokens.css` 的 `--capy-*` 里，亮暗两套各一份。
- **造型**：改 `web/index.html` 里 `tpl-ball` 和 `tpl-panel` 的两个 `<svg>`。
- **表情**：球会跟着状态走——平静时是默认，有新结果眼睛睁大 6%，有待确认再放大
  到 17%（`web/style.css` 里 `.ball[data-level="1"] / [data-level="2"]` 那两条
  `.capy__eye`）。另外它每 6.4 秒眨一次眼。

⚠️ **改样式必踩的坑**：毛色渐变那三个 `<stop>` 的颜色**只能由 CSS class 给**，
不能写成 `stop-color="var(--capy-hi)"`。SVG 的 `<stop>` 不解析 presentation attribute
里的 CSS 变量，渐变会**整个失效**——而失效的表现是引用它的那个圆**直接不画**，
脸凭空消失，控制台连一个错都不报。（反过来 `<circle fill="var(--x)">` 是好的，
只有 `<stop>` 不行，所以特别容易被绕进去。）

⚠️ **界面里不许用 `#000000`**：透明是 DWM 玻璃做的，键色写死是黑，
纯黑像素会被键掉变成透明。半透明阴影不受影响，叠出来不会是纯黑。

---

## 许可

MIT
