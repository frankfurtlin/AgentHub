# 开发过程

这份文档记的是**怎么走到现在这一步**：试过什么、为什么换、中间踩了哪些坑。
想直接上手看 README，想知道下一步做什么看 [`roadmap.md`](roadmap.md)。

---

## 一、要解决的问题

四个 GUI 版 AI 编程助手（CodeBuddy CN / Trae CN / Qoder CN / WorkBuddy）同时干活时，
真正难受的不是它们干得不好，而是**你不知道哪一个正卡在那里等你点一下**。
审批弹窗躲在各自窗口里，不切过去看不见，切过去又打断手上的事。

于是要一个常驻桌面的小球：谁在等你就亮谁，点一下切过去。

---

## 二、界面方案换了三代

### 第一代：tkinter + ctypes（已废弃，代码没进仓库）

第一版直接用 Python 标准库写：tkinter 画球和面板，ctypes 调 Win32 拿进程/窗口/通知。
**零第三方依赖**，双击 `start.vbs` 就跑，还顺手实现了右键菜单、全部标记已读、开机自启。

放弃的原因很直接：界面糊。tkinter 的 `Canvas` 在高 DPI 屏上画圆和文字会发虚，
没有抗锯齿、没有阴影、圆角窗口也做不干净，用户第一眼反馈就是「像素很低，有点糊」。

顺带一个硬约束：**tkinter 的界面代码过不到 macOS 上去**——同样的窗口在 macOS 上外观完全
是另一套，等于要写两遍。

> 这一代的价值全在探测层：状态判定的规则、UIA 探针、图标提取、通知读取，都是那时候趟出来的，
> 后来整体搬进了 `host/windows/`。界面扔了，底层留下了。

### 第二代：PyQt 原型（评估后放弃）

想要好看的界面，第一反应是 Qt。写了个原型（`agenthub-qt/`，四个文件约 1000 行），
渲染质量确实好。**放弃的原因是体积**：PyQt5 / PySide6 打包后光 Qt 的 DLL 就要加
30~50 MB，而用户明确要求「这种小工具还是尽量安装包小一点」。

### 第三代：pywebview + WebView2（现在这个）

关键取舍是**用系统里已有的浏览器运行时**：

| | |
|---|---|
| 体积 | WebView2 Runtime 是系统自带的（Win11 自带，Win10 一般随 Edge 装好），**不用跟着安装包发**，项目本体只有几个 Python 文件 + 一份 HTML/CSS |
| 观感 | 圆角、阴影、渐变、抗锯齿全部交给 CSS，天生清晰 |
| 跨平台 | `web/` 是纯浏览器代码，macOS 上 pywebview 走 WKWebView，前端一行不用改 |
| 代价 | 平台细节得自己趟——GPU 沙箱、窗口透明、DPI、桥的注入时序，全在 `agenthub/webview_env.py` 里，另见 [`webview2-notes.md`](webview2-notes.md) |

界面上的水豚是**自己画的 SVG**，不是现成 IP。原因写在 README 的「关于形象」一节：
热门水豚形象（比如噜噜）是有版权方的商业 IP，画进 MIT 开源项目里风险明确；
但「水豚」这个物种本身没有版权，照着物种特征独立画一只即可。

---

## 三、架构是怎么分层的

```
models.py     数据模型 + 文案判据（无任何系统调用，两端共用）
runtime.py    状态中枢：定时扫描 → 合并两条通道 → 算告警边沿 → 出快照
app.py        pywebview 外壳：两个无边框透明窗口 + 暴露给前端的桥
host/base.py  平台抽象（Backend / UiProbeSession / WindowInfo）
host/windows  Win32 实现：winapi（ctypes）、uia（UIA 探针）、icons、notify
web/          纯前端，不依赖构建工具
```

三条分层原则：

**一、平台相关的全部关进 `host/`。** `Backend` 是一个抽象基类，上层只认它。
目前 Windows 实现了 20 个方法，macOS 只要照着填同一个类，上层一行不用改——
这是「先支持 Windows、未来要支持 macOS」这个要求唯一重要的准备。

**二、`models.py` 里不许有系统调用。** 通知文案怎么分类（`classify()`）、
什么算「在等你」（`AgentState.waiting`），都是纯函数，可以单测、可以跨平台复用。

**三、状态在 Python 侧算完，前端只负责渲染。** 前端拿到的是一份快照
（`{agents: [...], totals: {...}}`），不做任何判断。这样换界面（或者将来做个 CLI、
做个菜单栏 App）都不用重写逻辑。

### 两条判断通道都要留着

判断「某个工具在等你」有两条路，实测下来**必须两条都在**：

| 通道 | 说明 | 实测情况 |
|---|---|---|
| 系统通知 | 读通知中心的 SQLite 库 | CodeBuddy / Qoder 在本机通知库里**躺着 0 条**：它们注册了处理器，但只在窗口不聚焦时才发通知，而审批弹窗恰恰只在你看着窗口时出现——逻辑上死锁 |
| 界面探针 | UI Automation 直接读界面上有没有审批条 | 兜住了上面两家；代价是一次查询约 80ms，所以单独起一个 2.5s 间隔的线程，不跟着主循环跑 |

任一条说「在等你」就算数，取并集。

---

## 四、几个值得记的决策

| 决策 | 备选 | 为什么这么选 |
|---|---|---|
| 界面用 pywebview | PyQt / Electron | 体积。系统自带 WebView2，不用发运行时 |
| 悬浮球画一只水豚 | 几何圆点 / 品牌色圆 | 圆点看不出「活物」，状态变化没有情绪；动物形象能让「在等你」这件事更像有人在喊你 |
| 毛色渐变用 CSS class 上色 | `stop-color="var(--capy-hi)"` | SVG 的 `<stop>` 不解析 presentation attribute 里的 CSS 变量，渐变会**整个失效**，而且失效表现是引用它的圆直接不画、控制台一声不吭 |
| 面板背景改不透明纯色 | `backdrop-filter` 毛玻璃 | 透明无边框窗口采不到窗外像素，毛玻璃无效；试到 alpha .96 仍然透出背后的窗口文字 |
| 透明用 DWM 玻璃 | `TransparencyKey` / `SetWindowRgn` | 见下面踩坑清单第一条 |
| 告警只在边沿触发 | 每轮都提示 | 否则切个窗口就被轰炸一次 |

---

## 五、踩坑清单

按模块整理，都是**不记下来一定会重踩**的。

### WebView2 / pywebview

- **GPU 沙箱**：部分机器上 GPU 子进程起不来，Chromium 连试 9 次后整体放弃，窗口一片白、
  JS 一行不执行，Python 侧只报一句「Main window failed to start」。加
  `--disable-gpu-sandbox` 解决。完整症状、误判过的方案、为什么 `--disable-gpu` 反而没用，
  见 [`webview2-notes.md`](webview2-notes.md)。
- **透明要自己补**：pywebview 只把 WebView2 的背景设透明，窗体自己的 `BackColor` 还是
  WinForms 默认的 `#F0F0F0`。修法记录见 `webview_env.patch_transparency()`。
- **窗口尺寸不能只信 `create_window`**：`min_size` 默认 `(200, 100)` 会把 76×76 的球撑大；
  初始 `Size` 还会被 WinForms 的 `AutoScaleMode.Dpi` 揉一遍，面板莫名少十几像素、页脚被裁。
  得在窗口起来后用 `resize()`（走 `SetWindowPos`）校正。
- **桥是分两步注入的**：先建 `window.pywebview = {api: {}}` 并派发 `pywebviewready`，
  隔一会儿才挂真方法。只判断「桥在不在」会停在半路；还要判方法是不是 `function`，且失败要能重试。
- **本地资源会被缓存**：内建 HTTP 服务没发出 `Cache-Control`，WebView2 走启发式缓存。
  所以 `web/index.html` 里的资源路径带 `?v=`（手动递增的缓存键）。
- **DPI 感知得自己设**：pywebview 不设，但它注释里写着「already DPI-aware scaled due to
  SetProcessDPIAware」——那是**它的假设**，不是它做过的事。不设的话读到的是虚拟化尺寸，
  而 `move()` 会再乘一次缩放，不折算球会飞出屏幕。

### Win32 / UIA

- **审批条不是按钮**：CodeBuddy 的审批项是 `<div>`，按 Button 类型扫不到，得另开一条
  「按标题文字精确匹配」的通道。
- **UIA 要从渲染面取元素**：Electron 只有一个 `Chrome_RenderWidgetHostHWND`，
  从顶层窗口拿到的只有 3 个标题栏按钮。
- **UIA 的 COM 对象绑线程**：`IUIAutomation` 必须和用它的线程同生命周期，
  所以做成 `UiProbeSession` 由探针线程自己开关，不能挂在 Backend 上长期持有。

### 调试方法本身

- **僵尸进程会让一切变慢**：`webview.start()` 在子线程 `destroy()` 之后常常不返回，
  每个探针脚本跑完就留一个进程占着 WebView2 的 profile 目录。曾经堆到
  6 个 python + 18 个 WebView2 进程，新探针起来要跟它们抢锁，验收从几十秒拖到几分钟。
  **探针脚本结尾要硬退出**（`os._exit(0)`），每轮跑之前先清干净。
- **验收脚本会污染配置**：验收为了确定位置会调 `move()`，而主循环会把位置写进配置，
  结果下次启动球被钉在验收用的坐标上。已修（`remember_ball_pos` 现在会判位置是否合法），
  但同类问题还会以别的形式出现——**凡是验收脚本，都要想清楚它会不会留下副作用**。
- **验收必须覆盖「首次默认摆放」**：早期验收是自己 `move()` 到硬编码坐标，绕开了默认定位
  分支，于是 DPI 折算的 bug 藏了很久。

---

## 六、现在怎么验证

| 脚本 | 判什么 |
|---|---|
| `_shots/acceptance.py` | 端到端 7 项：窗口尺寸/位置、四角真透明、球有抗锯齿、面板渲染与无裁切 |
| `_shots/glass_check.py` | 直接调**生产函数**验证透明：四角透 4/4 **且**鼠标点击能收到 |
| `tools/selftest.py` | 底层能力自检 11 项：进程/窗口/CPU/扫描耗时/图标/通知/探针/缩放/配置/提示音 |
| `tools/probe_uia.py` | 界面探针专项：当场看各家工具界面里读到了什么、走的哪条通道 |

`_shots/` 是调试产物，不进仓库（在 `.gitignore` 里）。想复现的话照着 `glass_check.py`
写一个即可：**它调的是生产函数，不是在脚本里抄一份实现**——后者证明不了生产代码能用，
这一点是吃过亏之后才改的。
