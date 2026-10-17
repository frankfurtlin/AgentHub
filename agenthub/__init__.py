# -*- coding: utf-8 -*-
"""AgentHub —— 把本机几个 GUI 版 AI 编程助手的状态收进一个小窗口。

一个悬浮球常驻桌面：谁在等你确认、谁刚跑完，一眼可见；点开是四个工具的状态
卡片，点卡片直接切到对应的窗口。

模块分工：

    models.py        与平台无关的数据模型和文案判据
    runtime.py       状态中枢：定时扫描、组装状态、驱动告警
    config.py        本机配置的读写
    app.py           pywebview 外壳与 JS 桥
    host/            平台抽象层（Windows 已实现，macOS 待补）
    web/             前端（原生 HTML/CSS/JS，零构建）
"""

__version__ = "0.3.0"

__all__ = ["__version__"]
