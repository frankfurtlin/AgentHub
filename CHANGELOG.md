# 更新记录

格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

打包产物不按版本留存——每次 push 到 main 都会重打并覆盖 `latest` release，
见 [`docs/packaging.md`](docs/packaging.md)。

---

## [0.3.0] — 2026-10-17

### 新增

- **面板高度跟着内容自适应**：前端量完内容高度经桥上报，Python 侧夹紧到
  220–900（另有一道按屏高收的保险），不再写死。
- **只展示本机装了的工具**：新增 `agenthub/host/windows/installed.py`，
  四路取并集检测（注册表卸载项 / 开始菜单 `.lnk` / MSIX 包名 / exe 探路）。
  正在跑的进程一律算已装。结果缓存 5 分钟。
- **自动打包发布**：`tools/build.py` + `.github/workflows/build.yml`，
  每次 push 到 main 自动构建 Windows / macOS 包并覆盖 `latest` release。
- **文档**：`README.md` 重写为英文（并大幅扩充），新增 `README_CN.md` 中文版、
  `CONTRIBUTING.md`、`CHANGELOG.md`、`docs/packaging.md`、Issue / PR 模板、
  `.editorconfig`。

### 修复

- **完成通知没有作废审批**：任务跑完之后卡片仍一直显示「待审批」，
  点过去却发现没东西可批。改为 `KIND_DONE` 分支里一并清空 `approvals`。
- **装机检测结果不进缓存**：扫到 0 个应用时 `_installed` 被置为 `None`，
  于是每 1.5 秒重扫一遍注册表 + 开始菜单（几百个条目），白烧 CPU。
  改成用独立标志记录「扫过没有」。
- **球上的状态点只有 4 个写死位置**：工具变 7 个后第 5 个起全部 fallback
  到同一坐标叠在一起。改成按点数算间距和半径，整排居中、总宽封顶。
- **打包后的 exe 一启动就崩**：`__main__.py` 用相对导入，而 PyInstaller
  把入口脚本当顶层模块执行（无包上下文）。改成两种运行方式都支持。

### 变更

- **目录结构**：图标资源从 `agenthub/assets/` 移到根目录 `assets/`，和打包资源归一；
  删掉空的 `web/assets/`；新增 `tests/`、`.github/`；`tools/` 补上 `build.py`
  和 `make_tray_icon.py`（后者原先散落在被 gitignore 的调试目录里）。
- 测试从被 gitignore 的 `_shots/` 搬进 `tests/`，共 22 个用例，
  零依赖（有 pytest 用 pytest，没有也能直接 `python tests/test_x.py`）。
- `pythonnet` 写进依赖声明（代码里直接 `from System.Drawing import ...`），
  不再只靠 pywebview 传递依赖。
- `pyproject.toml` 补上 `dev` extra、pytest 与 ruff 配置。

---

## [0.2.9] — 2026-10-17

### 新增

- 纳管 Claude、DeepSeek Harness、Codex，共 7 个工具。
- 开机自启：`tools/install_startup.py`。

### 修复

- 悬浮球背后的方形底色（三层原因：透明补丁不重试、两层透明各自会丢、
  CSS 投影被窗口边缘硬裁）。
- 面板不跟随悬浮球拖动 —— 改成挂消息钩子逐帧同步。
- 卡片排序：运行中的在上（按启动顺序），未启动沉底。

---

## [0.2.8] — 2026-10-16

- 界面方案从 tkinter 换成 pywebview + WebView2。
- 悬浮球改用自绘水豚 SVG。
- 亮 / 暗双主题。

---

[0.2.10]: https://github.com/frankfurtlin/AgentHub/releases/tag/latest
[0.2.9]: https://github.com/frankfurtlin/AgentHub/releases/tag/latest
[0.2.8]: https://github.com/frankfurtlin/AgentHub/releases/tag/latest
