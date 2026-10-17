# 贡献指南

改代码之前先看这份，能省掉来回问。

---

## 环境

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS: source .venv/bin/activate
pip install -e ".[dev]"
python -m agenthub
```

只想看它读到什么、不开窗口：`python -m agenthub --once`

---

## 三条硬约束

这三条是架构的根，破了后面会很难收场。

### 一、平台相关的代码只能写在 `host/`

`agenthub/host/base.py` 里的 `Backend` 是抽象基类，上层（`runtime.py` / `app.py` /
前端）只认这个接口，**不许直接 import 任何 Win32 或 Cocoa 的东西**。

加平台能力 = 在 `Backend` 上加方法 + 在各平台实现里补。Windows 已经完整实现了；
macOS 是占位，加的时候照着 `host/macos/__init__.py` 顶部的待办清单填。

### 二、`models.py` 里不许有系统调用

`agenthub/models.py` 是**平台无关的**：数据模型 + 文案判据。
`classify()`、`AgentState.waiting`、`parse_toast()` 全是纯函数——可单测、可跨平台复用。

判断"是不是要你审批"这类规则，改这里；怎么拿到通知，改 `host/`。

### 三、状态在 Python 侧算完，前端只渲染

前端拿到的是一份快照（`{agents: [...], totals: {...}}`），**不做任何状态判断**。
改了字段名要同步改 `web/app.js`。

---

## 两个必踩的坑

写界面代码前先记住这两条，都排查过很久。

### 别用 `stop-color="var(--x)"`

SVG 的 `<stop>` **不解析** presentation attribute 里的 CSS 变量。
写了之后渐变整个失效，而失效的表现是**引用它的那个图形直接不画**——
脸凭空消失，控制台一声不响。

正确做法是给 `<stop>` 挂 class，颜色写在 CSS 里：

```css
.cap-fur .cap-fur-hi { stop-color: var(--capy-hi); }
```

（`<circle fill="var(--x)">` 这种是好的，只有 `<stop>` 不行——
正因为大部分地方能用，才特别容易绕进去。）

### 界面里不许出现 `#000000`

透明是 **DWM 玻璃**做的，DWM 的键色写死是黑。
纯黑像素会被键掉、变成透明，于是元素直接消失。

半透明的黑色（`rgba(0,0,0,.x)`）没问题——叠出来不会是纯黑。

### 顺带：别用 `TransparencyKey`

它也能让窗口四角透明，但会把窗口变成 layered，
而 layered 窗口的色键区**放行鼠标消息**——
实测页面里一次 `mousedown` 都收不到，悬浮球直接点不动。

---

## 改前端要注意缓存

pywebview 给本地文件用的内建 HTTP 服务**没有发出 `Cache-Control`**，
WebView2 于是走启发式缓存（新鲜期约是文件年龄的 10%）。

所以两件事：

1. `web/index.html` 里引用的 CSS/JS 带 `?v=`（手动递增的缓存键）；
2. **改了前端就升 `agenthub/__init__.py` 里的 `__version__`** ——
   `webview_env._clear_cache_on_version_change()` 靠它判断要不要清缓存。

只改 `?v=` 不管用：`index.html` 自己也会被缓存。

---

## 测试

```bash
python -m pytest tests/ -q        # 22 个用例，不需要 GUI
python -m pytest tests/test_x.py  # 单独跑一个文件也行
```

`tests/helpers.py` 里的 `FakeBackend` 是个不碰系统的假后端，
测「只显示已装的工具」「完成通知作废审批」这类纯逻辑用它。

涉及真实窗口/通知的改动，用：

```bash
python tools/selftest.py      # 底层能力自检 11 项
python tools/probe_uia.py     # 界面探针专项
```

---

## 提交前

```bash
python -m compileall -q agenthub tools
python -m pytest tests/ -q
python tools/selftest.py
```

改了前端还要升 `__version__` 和 `?v=`。

---

## 加一个工具

1. `agenthub/models.py` 的 `AGENTS` 加一条：
   `key` / `name` / `match`（进程名，小写）/ 品牌色 / `ui_probe` / `install_hints`
2. `install_hints` 给小写关键词，能出现在注册表 DisplayName、开始菜单快捷方式名、
   MSIX 包名里的片段
3. 加个测试：`tests/` 里用 `FakeBackend` 造一份应用清单，断言它能被认出来
