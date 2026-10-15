# AgentHub

把本机几个 GUI 版 AI 编程助手的状态收进一个悬浮球。

四个工具同时干活的时候，真正难受的不是它们干得不好，而是**你不知道哪一个正卡在那里等你点一下**。
CodeBuddy 的审批弹窗、Trae 的「等待操作」、Qoder 的权限请求——都躲在各自的窗口里，
你不切过去就看不见，切过去又会打断手上的事。

AgentHub 常驻桌面角落，谁在等你就亮谁，点一下直接切到那个窗口。

```
                ┌──────────────────────────────┐
   悬浮球        │  AgentHub          4/4 运行中  │  ← 点球展开
   （贴边）      │ ──────────────────────────── │
    ●●●●        │  [C] CodeBuddy CN  空闲中  › │
                │  [T] Trae CN       输出中  › │  ← 点卡片切窗口
                │  [Q] Qoder CN   待审批 · 2 › │
                │  [W] WorkBuddy     空闲中  › │
                │ ──────────────────────────── │
                │  有 2 个任务在等你确认         │
                └──────────────────────────────┘
```

## 为什么值得用

**两条独立的判断通道。** 光靠系统通知会漏——实测 CodeBuddy 和 Qoder 在本机
通知库里躺着 **0 条**记录（它们注册了处理器，但只在窗口不聚焦时才发通知，
而审批弹窗恰恰只在你看着窗口时出现，逻辑上死锁）。所以除了读通知，
还额外用 UI Automation **直接把界面上的审批条读出来**。任一条说「在等你」就算数。

**只在必要时出声。** 空闲时不管它；「从不在等 → 在等」的这个边沿才响一声并把面板推出来。
不会因为你切了个窗口就轰炸你。

**内存里没有它自己的存在感。** 不驻留服务、不写注册表、不装驱动。配置就是一个 JSON 文件。

## 环境要求

| | |
|---|---|
| 系统 | Windows 10/11（macOS 后端预留，见下文） |
| Python | 3.10+ |
| 运行时 | Microsoft Edge WebView2 Runtime —— Win11 自带；Win10 一般随 Edge 装好了 |

WebView2 Runtime 的好处是**不用跟着安装包发**：它是系统的，界面渲染交给它，
所以这个项目本身很小（几个 Python 文件 + 一份 HTML/CSS，没有 Electron 那样的上百兆运行时）。

## 上手

```bash
git clone https://github.com/frankfurtlin/AgentHub.git
cd AgentHub

python -m venv .venv
.venv\Scripts\activate            # macOS: source .venv/bin/activate
pip install -e .
```

不想开界面、只想看看它读到什么：

```bash
python -m agenthub --once
```

```
平台：windows  能力：{'platform': 'windows', 'uiProbe': True, 'notifications': True, 'icons': True}
扫描耗时 13ms
CodeBuddy CN   空闲中      cpu=  0.0%
Trae CN        空闲中      cpu=  0.0% · 最小化
Qoder CN       空闲中      cpu=  0.0% · 最小化
WorkBuddy      待审批      cpu=  0.0%    任务等待确认，请处理
合计：{'running': 4, 'waiting': 1, 'unread': 0}
```

正常启动：

```bash
python -m agenthub
```

球的交互：**轻点展开面板，按住拖动挪位置**（位移超过 4px 才算拖，不会误触）。
面板里点卡片切到对应窗口，`Esc` 或右上角收起。

## 配置

配置文件在 `%APPDATA%\AgentHub\config.json`（macOS 是 `~/Library/Application Support/AgentHub/config.json`），
首次运行自动生成。仓库里 [`config.example.json`](config.example.json) 是一份带注释的模板。

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

## 支持的工具

| 工具 | 进程名 | 判断方式 |
|---|---|---|
| CodeBuddy CN | `CodeBuddy CN.exe` | 界面探针 |
| Trae CN | `Trae CN.exe` | 通知 + CPU |
| Qoder CN | `Qoder CN.exe` | 界面探针 |
| WorkBuddy | `WorkBuddy.exe` | 通知 |

要加一个工具，只需在 [`agenthub/models.py`](agenthub/models.py) 的 `AGENTS` 里加一条：
进程名用来找它，品牌色用来画图标底衬，`ui_probe=True` 表示额外用界面探针读审批状态。

## 结构

```
agenthub/
  models.py       数据模型 + 文案判据（无系统调用，两端共用）
  runtime.py      状态中枢：定时扫描、合并两条通道、算告警边沿
  app.py          pywebview 外壳：两个无边框透明窗口 + 暴露给前端的桥
  webview_env.py  启动 WebView 前的环境准备（有一处不是可选项，见 docs）
  config.py       配置读写
  host/
    base.py            平台抽象层（Backend / UiProbeSession / WindowInfo）
    windows/           Win32 实现：winapi（纯 ctypes）、uia（UI Automation 探针）、
                       icons（从 exe 抠图标）、notify（读通知库）
    macos/             macOS 实现（待写）
web/
  index.html      两个视图的模板（悬浮球 / 状态面板）
  app.js          渲染 + 交互 + 与 Python 通信
  tokens.css      设计 token（颜色、间距、圆角、阴影）
  style.css       组件样式
```

界面是**原生 HTML/CSS/JS**，没有构建步骤、没有 `node_modules`。
两个窗口共用同一份 `index.html`，靠 URL 上的 `#ball` / `#panel` 区分渲染哪一套。

## 关于形象

悬浮球上那只水豚是**自己画的 SVG**（`web/index.html` 里的 `tpl-ball`），不是任何现成的 IP。

特意写一段说明，是因为这件事总会被问到：「能不能用水豚噜噜那个形象？」
——不能。那类热门形象是有版权方的商业 IP（噜噜的版权在铁罐文化，正在做盲盒、
联名等付费授权），把它的形象画进一个 MIT 开源项目里，风险是明确的。
但**「水豚」这个物种本身没有版权**，所以照着物种特征（圆脸、小圆耳、宽鼻吻、
黑豆眼）独立画一只，就既干净又不影响可爱程度——成品就是球上这只。

想改的话：

- **配色**：全在 `web/tokens.css` 的 `--capy-*` 里，亮暗两套各一份。
- **造型**：改 `web/index.html` 里 `tpl-ball` 和 `tpl-panel` 的两个 `<svg>`。
- **表情**：球会跟着状态走——平静时是默认，有新结果眼睛睁大 6%，有待确认再放大到 17%
  （`web/style.css` 里 `.ball[data-level="1"] / [data-level="2"]` 那两条 `.capy__eye`）。
  另外它每 6.4 秒眨一次眼。

⚠️ 画的时候有个必踩的坑：毛色渐变那三个 `<stop>` 的颜色**只能由 CSS class 给**，
不能写成 `stop-color="var(--capy-hi)"`。SVG 的 `<stop>` 不解析 presentation attribute
里的 CSS 变量，渐变会整个失效——而失效的表现是引用它的那个圆**直接不画**，
脸凭空消失，控制台连一个错都不报。（反过来说，`<circle fill="var(--x)">` 这种是好的，
只有 `<stop>` 不行，所以特别容易被绕进去。）

## 一些踩过的坑

装 Python 就能跑的代价是，平台细节得自己趟。几个不记下来一定会重踩的：

- **WebView2 的 GPU 沙箱**：部分机器上 GPU 子进程起不来，Chromium 连试 9 次后整体放弃，
  窗口一片白、JS 一行不执行，而 Python 侧只会报一句「Main window failed to start」。
  修法是 `--disable-gpu-sandbox`。完整症状、误判过的方案、为什么 `--disable-gpu` 反而没用，
  都写在 [`docs/webview2-notes.md`](docs/webview2-notes.md)。
- **窗口尺寸不能只信 `create_window`**：pywebview 的 `min_size` 默认 `(200, 100)`，
  会把 76×76 的球撑成 200×100；初始 `Size` 还会被 WinForms 的 `AutoScaleMode.Dpi` 揉一遍，
  面板会莫名少十几像素、页脚被裁掉。得在窗口起来之后用 `resize()`（走 `SetWindowPos`）校正。
- **审批条不是按钮**：CodeBuddy 的审批选项是 `<div class="approval-menu-item">`，
  按 Button 类型扫是扫不到的，得另开一条「按标题文字精确匹配」的通道。
- **前端资源会被缓存**：pywebview 给本地文件用的内建 HTTP 服务**没有发
  `Cache-Control`**（它设了，但被 `bottle.static_file` 返回的响应对象顶掉了，只剩
  `Last-Modified` 和 `ETag`），WebView2 于是走启发式缓存，新鲜期大约是文件年龄的
  10%。所以 `web/index.html` 里的资源路径带了 `?v=`（一个手动递增的缓存键，跟包版本
  无关，改前端时顺手加一下就行）。
- **桥是分两步注入的**：pywebview 先把 `window.pywebview = {api: {}}` 建出来并派发
  `pywebviewready`，隔一会儿才把真正的方法挂上去。中间那一小段里 `window.pywebview.api`
  是个真值、却不是能用的桥，拿它调方法会抛 `TypeError`。如果启动逻辑只判断「桥在不在」、
  报错又被 `catch` 吞掉，就会永远停在初始化不完整的半路——我们踩到的表现是配置里的
  主题偏好已经读到了，界面却始终不跟随。所以判存在之外还要判方法是不是 `function`，
  并且失败必须能重试（`web/app.js` 的 `boot()` 就是这么写的）。
- **UIA 得从渲染面取元素**：Electron 只有一个 `Chrome_RenderWidgetHostHWND`，
  从顶层窗口拿到的只有 3 个标题栏按钮。
- **DPI 感知得自己设**：pywebview 不设，但它的 `get_screens()` 注释里写着
  「already DPI-aware scaled due to SetProcessDPIAware」——那是**它的假设**，
  不是它做过的事。不设的话，进程在窗口刚建好那会儿还不感知，
  `webview.screens` 报的是被 Windows 虚拟化过的尺寸（2560×1440 的屏报
  2048×1152）；等 WinForms 稍后把感知补上，坐标语义又变了，这时 `move()` 会
  再乘一次缩放，不折算球会直接飞出屏幕。见 `agenthub/webview_env.py` 的
  `enable_dpi_awareness()` 和 `app.py` 的 `_logical_screen()`。

## macOS

后端接口（`host/base.py` 的 `Backend`）已经按平台无关的方式切好了，Windows 之外的一切
都走它。要补的是 `host/macos/`：

- 进程与窗口：`NSRunningApplication` / `CGWindowListCopyWindowInfo`
- 界面探针：`AXUIElement`（权限同「辅助功能」）
- 通知：`UNUserNotificationCenter` 的数据库（`~/Library/Application Support/.../db2`）
- 图标：`NSWorkspace.iconForFile`

界面那层不用改——`web/` 是纯浏览器代码，pywebview 在 macOS 上走 WKWebView。
`theme: auto` 也跟着系统走。

## 许可

MIT
