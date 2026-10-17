# -*- coding: utf-8 -*-
"""用 PyInstaller 打出可分发的包。

用法：
    python tools/build.py                 打当前平台
    python tools/build.py --zip           打完再压成一个 zip（发布用）
    python tools/build.py --clean         开打之前先清掉 build/ 和 dist/
    python tools/build.py --onedir        目录形式（默认，启动快）
    python tools/build.py --onefile       单文件形式（启动要解压，慢 1~2 秒）

产物落在 `dist/`：

    Windows  dist/AgentHub/AgentHub.exe      zip 后含 AgentHub/ 整个目录
    macOS    dist/AgentHub.app               zip 后含 AgentHub.app

为什么默认 `--onedir` 而不是 `--onefile`：后者每次启动都要把几百个文件解到
临时目录，常驻挂件的启动速度比少一个文件更重要。真要单文件就加 `--onefile`。

**为什么不需要手工列隐藏依赖**：pywebview 自带
`webview/__pyinstaller/hook-webview.py`，会把各平台的后端（Windows 上的
pythonnet / clr、macOS 上的 pyobjc 那一堆）自动收进来。所以这里只补
运行时才决定的那几个（比如托盘图标用的 `System.*` 是在函数内部 import 的，
静态分析扫不到）。
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = "AgentHub"

#: 运行时才 import 的模块。静态分析扫不到，必须显式列出来。
HIDDEN_IMPORTS = (
    "clr",                      # pythonnet：Windows 上托盘图标、透明补丁要用
    "System.Drawing",
    "System.Windows.Forms",
    "System.IO",
)

#: 一起打进包的数据。打包后代码里用 sys._MEIPASS 找到它们
#: （见 agenthub/app.py 的 web_dir() 与 agenthub/tray.py 的 icon_path()）。
DATA_DIRS = ("web", "assets")
DATA_FILES = ("LICENSE",)


def _arch_tag() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine or "unknown"


def _os_tag() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


def artifact_name() -> str:
    """如 `AgentHub-windows-x64`。CI 和本地用同一套命名。"""
    return "%s-%s-%s" % (APP, _os_tag(), _arch_tag())


def clean() -> None:
    """清掉 build/ 和 dist/。

    清不掉**不能让打包停住**——比如某些环境（带批量删除保护的系统）删大目录
    会要求确认，这时旧产物留着顶多让 dist 里多几个文件，远好过整个构建失败。
    """
    for folder in ("build", "dist"):
        path = os.path.join(ROOT, folder)
        if not os.path.isdir(path):
            continue
        try:
            shutil.rmtree(path)
            print("[build] 已清理 %s/" % folder, flush=True)
        except Exception as exc:
            print("[build] 清理 %s/ 失败（继续打包）：%r" % (folder, exc), flush=True)


def pyinstaller_args(onefile: bool) -> list[str]:
    # ⚠️ Windows 上分隔符是 `;`，其他平台是 `:`——写死成冒号在 Windows 上
    # 会把 `web:` 当成源路径，直接报找不到文件。
    sep = ";" if os.name == "nt" else ":"

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", APP,
        "--paths", ROOT,
        "--onefile" if onefile else "--onedir",
        "--windowed",              # Windows 下等同 --noconsole；macOS 下产出 .app
        # 不指定的话 spec 会甩在仓库根目录，跑一次打包就多一个 AgentHub.spec。
        # 虽然 .gitignore 里有 *.spec，但根目录不该堆这些东西。
        "--specpath", os.path.join(ROOT, "build"),
    ]

    for folder in DATA_DIRS:
        args += ["--add-data", "%s%s%s" % (
            os.path.join(ROOT, folder), sep, folder)]
    for name in DATA_FILES:
        args += ["--add-data", "%s%s." % (os.path.join(ROOT, name), sep)]

    for module in HIDDEN_IMPORTS:
        args += ["--hidden-import", module]

    # 这些都是 pywebview 的可选 GUI 后端，本机用不上，打进去只会让体积翻倍
    for module in ("PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter",
                   "numpy", "pytest", "PIL", "win32com"):
        args += ["--exclude-module", module]

    # 入口。用 `agenthub/__main__.py` 这个文件，而不是 `-m agenthub`：
    # PyInstaller 认的是脚本路径，写成参数里的 `-m` 它不认。
    args.append(os.path.join(ROOT, "agenthub", "__main__.py"))
    return args


def make_zip() -> str:
    """把打包结果压成一个 zip，返回 zip 路径。"""
    import zipfile

    name = artifact_name()
    if sys.platform == "darwin":
        source = os.path.join(ROOT, "dist", APP + ".app")
    else:
        source = os.path.join(ROOT, "dist", APP)

    if not os.path.exists(source):
        raise SystemExit("[build] 没找到产物：%s" % source)

    target = os.path.join(ROOT, "dist", name + ".zip")
    if os.path.exists(target):
        os.remove(target)

    base = os.path.dirname(source)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        if os.path.isdir(source):
            for root, _dirs, files in os.walk(source):
                for file_ in files:
                    full = os.path.join(root, file_)
                    archive.write(full, os.path.relpath(full, base))
        else:
            archive.write(source, os.path.basename(source))
    print("[build] 已生成 %s" % os.path.relpath(target, ROOT), flush=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="用 PyInstaller 打包 AgentHub")
    parser.add_argument("--zip", action="store_true",
                        help="打完再压成一个 zip")
    parser.add_argument("--clean", action="store_true",
                        help="先清掉 build/ 和 dist/")
    parser.add_argument("--onefile", action="store_true",
                        help="打成单个 exe（启动会慢 1~2 秒）")
    parser.add_argument("--onedir", action="store_true",
                        help="打成目录（默认，启动快）")
    args = parser.parse_args()

    if args.clean:
        clean()

    command = pyinstaller_args(onefile=args.onefile and not args.onedir)
    print("[build] %s" % " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    if args.zip:
        make_zip()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
