# -*- coding: utf-8 -*-
"""pytest 的路径引导。

测试直接 `pytest` 跑时，仓库根不一定在 `sys.path` 里（pytest 默认插入的是
第一个不含 `__init__.py` 的父目录，也就是 `tests/` 本身）。所以这里补两条：

    ROOT      让 `import agenthub` 成立
    tests/    让测试之间能 `from helpers import ...`

conftest 由 pytest 保证最先加载，各测试文件里就不必再各写一遍 sys.path 了。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if path not in sys.path:
        sys.path.insert(0, path)
