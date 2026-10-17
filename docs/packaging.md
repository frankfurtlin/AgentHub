# 打包与分发

打包用 **PyInstaller**，两个平台各出一包，构建产物发布到固定的 `latest` release。

---

## 两条命令

本地打包（打当前平台）：

```bash
python tools/build.py --clean --zip
```

CI 打包：由 `.github/workflows/build.yml` 在每次 push 到 main 时自动跑，
不需要手工干预。

产物落在 `dist/`：

| 平台 | 目录形式 | 最终产物 |
|---|---|---|
| Windows | `dist/AgentHub/AgentHub.exe` | `AgentHub-windows-x64.zip` |
| macOS | `dist/AgentHub.app` | `AgentHub-macos-arm64.zip` / `AgentHub-macos-x64.zip` |

---

## 为什么默认 `--onedir` 而不是 `--onefile`

`--onefile` 每次启动都要把几百个文件解到临时目录，常驻挂件的**启动速度**
比「少一个文件」更重要——它是开机就跑、一直在的。默认走目录形式，
想出单文件就加 `--onefile`。

---

## 不需要手工列隐藏依赖

pywebview 自带 `webview/__pyinstaller/hook-webview.py`，各平台的后端
（Windows 上的 pythonnet / `System.*`，macOS 上的 pyobjc 那一堆）
会被自动收进来。

`tools/build.py` 里的 `HIDDEN_IMPORTS` 只补**运行时才决定**的那几个：
托盘图标用的 `System.Drawing` / `System.Windows.Forms` 是在**函数内部**
import 的，静态分析扫不到。

---

## 需要一起打进包的数据

| 路径 | 谁在用 |
|---|---|
| `web/` | 两个窗口的 HTML/CSS/JS，见 `agenthub/app.py` 的 `web_dir()` |
| `assets/` | 托盘图标 `tray.ico`，见 `agenthub/tray.py` 的 `icon_path()` |
| `LICENSE` | 许可声明 |

打包后代码通过 `sys._MEIPASS` 找它们，两个函数里都做了 `frozen` 判断。

---

## 「只保留最新的一个包」是怎么做到的

release 的 **tag 恒为 `latest`**。每次发布前先把旧的 release 连带 tag 删掉，
再建一个同名的。于是 release 页面永远只有一份产物，下载链接也固定：

```
https://github.com/<owner>/<repo>/releases/latest
```

想改成保留历史版本，把 workflow 里 release job 的 tag 换成
`v${{ github.run_number }}` 或 commit SHA 即可。

---

## 两个绕不过去的平台问题

### WebView2 Runtime 不能打进包（Windows）

它是**系统的组件**，不跟随安装包分发。Win11 自带；Win10 一般随 Edge
装好了。没有的话要引导用户去装（微软提供 Evergreen Bootstrapper，
支持静默安装）。

### 签名（macOS）

unsigned 的 `.app` 在别人的机器上会被 Gatekeeper 拦下来，
需要右键「打开」才放行。要干净分发得走 Apple Developer 签名 + 公证，
本仓库目前没做——从 release 下载 Mac 版的话，第一次启动要手动放行一次。

另外 macOS 后端（`agenthub/host/macos/`）目前还是占位实现，
所以 Mac 包**能构建、能启动，但状态读取还没接上**。
待办清单写在该文件顶部。
