# WebView2 踩坑记录

pywebview 在 Windows 上走 WinForms + WebView2。大多数机器上开箱即用，
但下面这两条是**必须处理**的，否则界面整个白屏，而且在应用层看不出原因。

---

## 一、GPU 进程沙箱把整个 WebView2 拖死

### 症状

窗口正常出现、位置尺寸都对，但是**一片白**：

- 页面上任何 HTML/CSS 都不渲染，没有任何报错弹窗
- JS 一行都不执行（`window.AgentHub` 不存在）
- `webview.window.evaluate_js()` 永久阻塞，20 秒后抛
  `WebViewException('Main window failed to start')`
- 开 `REMOTE_DEBUGGING_PORT` 后，`http://127.0.0.1:<port>/json` 全部返回 **502 Bad Gateway**
- 退出时 pywebview 打印
  `[pywebview] Failed to delete user data folder: 'NoneType' object has no attribute 'BrowserProcessId'`
  （因为 `CoreWebView2` 压根没初始化成功）

**致命的误导性线索**：`window.events.loaded` 会正常触发。
但那个事件是 pywebview 在 `util.py` 里**无条件置位**的（注入 JS 之后直接
`window.events.loaded.set()`），跟页面是否真的加载成功毫无关系。所以
"loaded 到了" 不能作为 WebView2 正常的证据——这个坑浪费了一轮排查。

### 真正的根因

在 WebView2 的用户数据目录里能看到它自己的日志
（`<profile>/EBWebView/chrome_debug.log`，需要给 WebView2 加
`--enable-logging --v=1` 才会生成）：

```
ERROR:content\browser\gpu\gpu_process_host.cc:1103] GPU process exited unexpectedly: exit_code=7
WARNING:content\browser\gpu\gpu_process_host.cc:1571] The GPU process has crashed 1 time(s)
...（重复 9 次）...
FATAL:content\browser\gpu\gpu_data_manager_impl_private.cc:408] GPU process isn't usable. Goodbye.
```

GPU 子进程连续崩溃 9 次后，Chromium 决定"GPU 不可用"，然后**整个浏览器进程一起放弃**。
所以不是"渲染降级软件绘制"，而是彻底不渲染。同目录下还留了一份 6.5MB 的崩溃转储
（`Crashpad/reports/*.dmp`）。

至于 GPU 进程为什么起不来：本机是装了第三方杀软的 Windows，**大概率是杀软的 HIPS
拦了 GPU 子进程的沙箱初始化**。没有继续深挖——加参数能稳定解决就够了。

### 修法

```
--disable-gpu-sandbox
```

通过环境变量 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 传给 WebView2，
位置见 `agenthub/webview_env.py`（必须在 `webview.start()` 之前设好）。

实测结果：**从"每次必崩"变成"零崩溃"**。

### 试过但**没用**的参数（别再走一遍）

| 参数 | GPU 崩溃次数 | 结果 |
|---|---|---|
| `--disable-gpu` | 6，然后 FATAL | ✗ 无效 |
| `--disable-gpu-compositing` | 9，然后 FATAL | ✗ 无效 |
| `--use-angle=swiftshader --disable-gpu-compositing` | 9，然后 FATAL | ✗ 无效 |
| `--disable-software-rasterizer --disable-gpu-sandbox` | 0 | ✓ 有效（含目标参数） |
| `--disable-gpu-sandbox` | 0 | ✓ **有效，且是最小改动** |
| `--in-process-gpu` | 0 | ✓ 有效（备选） |
| `--no-sandbox` | 0 | ✓ 有效（但把整体沙箱全关了，不要用） |

反直觉的地方：`--disable-gpu` 并不能阻止 GPU 进程被拉起来（软件光栅化下它照样起），
所以照样崩。**真正起作用的是"关掉 GPU 进程的沙箱"，而不是"关掉 GPU"。**

### 排查这类问题的动作顺序

1. 先**看窗口是不是白的**（截屏，不要只看日志）。
2. 给 WebView2 加 `--enable-logging --v=1`，然后读
   `<profile>/EBWebView/chrome_debug.log`。**这是唯一能看到根因的地方。**
3. 顺手看 `<profile>/EBWebView/Crashpad/reports/` 有没有 `.dmp`。
4. 开 `REMOTE_DEBUGGING_PORT` 查 `/json`：502 就等于"浏览器进程内部已经死了"。

---

## 二、用户数据目录指向一个刚被删掉的路径

pywebview 在 `private_mode`（默认开）下这样取 profile 目录：

```python
# webview/platforms/winforms.py
cache_dir = tempfile.TemporaryDirectory().name
```

`tempfile.TemporaryDirectory()` 建完对象就被丢弃，CPython 立刻 GC 它，
`__del__` → `cleanup()` → **目录当场被删除**：

```python
>>> p = tempfile.TemporaryDirectory().name
>>> os.path.exists(p)
False
```

于是 WebView2 拿到的 `UserDataFolder` 是个不存在的路径。

### 修法

自己给一个真实目录，同时把 `private_mode` 关掉（否则退出时它会去
`shutil.rmtree` 一个已经没了的东西，就是上面那条警告的来源）：

```python
webview.start(storage_path=<真实目录>, private_mode=False)
```

见 `agenthub/webview_env.py` 的 `profile_dir()`。

---

## 三、窗口尺寸不能只信 `create_window`

两个独立的问题，症状都是"尺寸不对"：

### `min_size` 默认 `(200, 100)`

```python
# webview/__init__.py
min_size: tuple[int, int] = (200, 100),
```

想要一个 76×76 的悬浮球，实际会得到 200×100 的窗口。
透明窗口没有边框，你看不出区别，但**多出来那一片照样吃鼠标事件**，
而且**点它也会触发交互**。所以必须显式传 `min_size=(76, 76)`。

### 初始 `Size` 会被 WinForms 的 AutoScale 揉一遍

`BrowserForm.__init__` 里设了 `AutoScaleMode = Dpi` / `AutoScaleDimensions = SizeF(96, 96)`，
之后 `self.Size = Size(int(initial_width * scale), ...)` 的赋值会被自动缩放逻辑二次处理。
实测（2560×1440 @125%）要 344×492 的面板，实际客户区只有 330×455——
**右边少 14px、底部少 37px，页脚被裁掉了**。

`resize()` 走的是 `SetWindowPos`，逻辑像素 × DPI 比例直接算，没有那层 AutoScale：

```python
# webview/platforms/winforms.py
def resize(self, width, height, fix_point):
    scale = self._scale
    windll.user32.SetWindowPos(..., int(width * scale), int(height * scale), ...)
```

所以在 GUI 起来之后（要有窗口句柄）显式校正一次即可：

```python
win.resize(344, 492)     # 实测得到 430×615 物理像素 = 344×492 × 1.25 ✓
```

见 `agenthub/app.py` 的 `_fix_sizes()`。

### DPI 的时序陷阱

进程在建第一个窗口之前是 **DPI 不感知**的——这时 `GetSystemMetrics(0)` 返回的是
**假尺寸**（2048×1152，而真实是 2560×1440）。pywebview 在
`winforms.create_window()` 里才调 `SetProcessDPIAware()`，之后才变成"系统级感知"。

后果：任何在窗口建好之前读屏幕尺寸的代码都会算错。本项目里
`_place_ball_default()` / `_place_panel()` 都放在主循环里跑（窗口已建好），
就是为了避开这个。

顺带一句：抓屏做验证时也必须用**窗口建好之后**读到的尺寸，否则抓出来的图
右边和下边会被切掉一半——第一版验证脚本就是这么翻车的。

---

## 四、透明窗口怎么验证

透明窗口是 DWM 合成的，**`PrintWindow` 按句柄抓不到 WebView2 的子窗口内容**
（Electron/WebView2 的渲染在独立子 HWND 里）。

可靠的做法是整屏抓 + 差分：

1. 把窗口 `hide()`，整屏抓一张当基线；
2. `show()`，再抓一张；
3. 两张相减，差出来的像素就是窗口真正画了什么。

判断透明是否真的生效：取窗口**四角**的像素（圆角矩形的四角落在图形外），
和基线比。差值应该只有几个色阶——那点差是 CSS 的 `drop-shadow` 正常合成的结果。
如果四角和基线差出一大截、或者变成了纯黑/纯白，说明透明没生效，
窗口底被 `DefaultBackgroundColor` 填实了。

---

## 五、改了前端却看到旧界面：资源被启发式缓存

pywebview 给本地文件用的内建 HTTP 服务**最终没有发任何 `Cache-Control`**。
它确实设了：

```python
# webview/http.py
@app.route('/<file:path>')
def asset(file):
    bottle.response.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
    bottle.response.set_header('Pragma', 'no-cache')
    bottle.response.set_header('Expires', 0)
    return bottle.static_file(file, root=server.root_path)
```

但 `bottle.static_file()` 返回的是一个 `HTTPResponse` 对象，那个对象会顶替掉全局
`bottle.response`，上面这三个头就丢了。实际抓到的响应只有这些：

```
Server: WSGIServer/0.2 CPython/3.13.15
Content-Type: text/javascript; charset=UTF-8
Content-Length: 16051
Last-Modified: Thu, 15 Oct 2026 18:15:02 GMT
Etag: 4c3757319763cb5b88e59f655598a5f409544ddb
Accept-Ranges: bytes
```

没有 `Cache-Control`，浏览器就走**启发式缓存**：新鲜期 ≈ 文件年龄的 10%。
文件越旧，缓存窗口越长（放了一天就约 2.4 小时）。于是「我明明改了 `app.js`，
界面却没变」——清掉 WebView2 的 profile 目录就好了，但下次还会踩。

修法是给 `web/index.html` 里的资源路径加版本号查询参数（`app.js?v=0.1.0`），
发版时顺手加一版。别指望 ETag 救场：缓存还在新鲜期内，浏览器根本不会去问服务端。

排查时的判别方法：`urllib.request.urlopen('http://127.0.0.1:42001/app.js')`
然后打印 `r.headers.items()`，看一眼有没有 `Cache-Control`。

---

## 六、本机实测环境

记下来备查，环境变了结论可能跟着变：

| | |
|---|---|
| 系统 | Windows 11 专业版，2560×1440 @125% |
| DPI 感知 | 建窗前：不感知；建窗后：系统级（`SetProcessDPIAware`） |
| WebView2 Runtime | 155.0.4283.45 |
| pywebview 内置 SDK | `Microsoft.Web.WebView2.Core.dll` 1.0.3856.49 |
| pywebview | 6.2.1 |
| pythonnet | 3.2.1（clr_loader 0.3.1）|

SDK 比运行时旧一代是正常情况（WebView2 向后兼容），跟本次问题无关。
