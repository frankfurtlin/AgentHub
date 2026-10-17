# -*- coding: utf-8 -*-
"""`python -m agenthub` 和打包出来的 exe 共用的入口。

为什么要写两个分支：`python -m agenthub` 时本文件属于 `agenthub` 包，
相对导入能用；而 PyInstaller 是把**入口脚本**当顶层模块直接执行的，
那时 `__package__` 是空字符串，`from .app import main` 会抛

    ImportError: attempted relative import with no known parent package

——打包能成功，但 exe 一启动就崩。所以退回绝对导入：打包后 `agenthub/`
被收进 `dist/AgentHub/_internal/`，PyInstaller 运行时会把它加进
`sys.path`，`from agenthub.app import main` 找得到。
"""

from __future__ import annotations

try:
    from .app import main                   # python -m agenthub
except ImportError:                         # 打包后：入口就是顶层脚本
    from agenthub.app import main


if __name__ == "__main__":
    raise SystemExit(main())
