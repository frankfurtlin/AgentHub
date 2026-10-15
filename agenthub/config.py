# -*- coding: utf-8 -*-
"""本机配置的读写。

配置文件放在平台约定的用户目录（Windows 是 `%APPDATA%\\AgentHub\\config.json`），
**不进仓库**——里面是本机 exe 路径和悬浮球坐标，对别人没意义。仓库里放一份
`config.example.json` 作模板。

读取时对缺失字段做默认值兜底，所以旧版本写的配置文件不会让新版本崩掉。
"""

from __future__ import annotations

import json
import os

from .host import get_backend

DEFAULTS: dict = {
    # 各工具可执行文件的绝对路径。留空则自动在运行中的进程里找。
    # 形如 {"codebuddy": "D:\\CodeBuddy CN\\CodeBuddy CN.exe"}
    "exePaths": {},
    # 悬浮球位置 [x, y]；None 表示贴着屏幕右下角自动定位
    "ballPos": None,
    # 主循环扫描间隔（毫秒）。太小会让 CPU 白烧，太大手感迟钝
    "refreshMs": 1500,
    # 界面探针间隔（秒）。一次探针约 80ms，没必要跟着主循环跑
    "probeIntervalS": 2.5,
    # 主题：auto 跟随系统 / light / dark
    "theme": "auto",
    # 需要你出手时是否响一声
    "sound": True,
    # 面板是否置顶
    "onTop": True,
    # 追加给 WebView2 的浏览器启动参数。一般不用动；
    # 默认值里已经带了 --disable-gpu-sandbox（见 agenthub/webview_env.py）
    "webviewArgs": [],
}


class Config:
    """一个很薄的 JSON 配置封装，找不到文件就用默认值。"""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(get_backend().config_dir(), "config.json")
        self.data: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            # 文件不存在或内容坏了都当默认值处理——配置不该阻断启动
            pass
        return self.data

    def save(self) -> bool:
        try:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
            return True
        except OSError:
            return False

    def get(self, key: str, default=None):
        if key in self.data:
            return self.data[key]
        if default is not None:
            return default
        return DEFAULTS.get(key)

    def set(self, key: str, value, save: bool = False) -> None:
        self.data[key] = value
        if save:
            self.save()

    def __getitem__(self, key: str):
        return self.get(key)

    def __setitem__(self, key: str, value) -> None:
        self.data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def as_dict(self) -> dict:
        return dict(self.data)
