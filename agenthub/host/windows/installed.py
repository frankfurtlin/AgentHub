# -*- coding: utf-8 -*-
"""这台机器上装了哪些应用——给「面板只显示装了的工具」用。

为什么不能只看一处：

    · CodeBuddy / Trae / Qoder / WorkBuddy / DeepSeek Harness
      都是普通安装程序装的，注册表卸载项里有 DisplayName，最可靠；
    · Claude Desktop 是**微软商店（MSIX）**版，不进卸载项，只在
      AppModel\\Repository\\Packages 里有个包名；
    · Codex 是 CLI，压根没有安装程序，只有个 exe 躺在
      %LOCALAPPDATA%\\OpenAI\\Codex\\bin\\ 下。

所以四个来源取并集：卸载项、开始菜单快捷方式、MSIX 包名、可执行文件探路。
都是秒级以内的一次性读取（注册表遍历几百项 + 几个 os.path.exists），
运行时每 5 分钟才重扫一次（见 runtime.Runtime._installed_keys）。
"""

from __future__ import annotations

import os
import shutil

#: 只有 exe、没有安装记录的 CLI 工具，直接看文件在不在
_EXE_PROBES: tuple[tuple[str, str], ...] = (
    ("Codex", r"%LOCALAPPDATA%\OpenAI\Codex\bin\codex.exe"),
    # PATH 里也认一份（用户可能自己配过）
    ("Codex", "codex"),
)

_UNINSTALL_ROOTS = (
    (r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU"),
    (r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
    (r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
)

#: MSIX / 商店应用的包仓库。包名形如 Claude_1.5.0.0_x64__pzs8sxrjxfjjc
_MSIX_ROOT = (r"Software\Classes\Local Settings\Software\Microsoft\Windows"
              r"\CurrentVersion\AppModel\Repository\Packages", "HKCU")


def _reg():
    import winreg
    return winreg


def _uninstall_names() -> list[str]:
    """注册表卸载项里的 DisplayName。"""
    winreg = _reg()
    roots = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    out: list[str] = []
    for path, hive in _UNINSTALL_ROOTS:
        try:
            with winreg.OpenKey(roots[hive], path) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, index)) as item:
                            try:
                                name = winreg.QueryValueEx(item, "DisplayName")[0]
                            except OSError:
                                continue
                            if name:
                                out.append(str(name))
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def _shortcut_names() -> list[str]:
    """开始菜单里的快捷方式名（用户级 + 全局）。"""
    bases = [
        os.path.join(os.environ.get("APPDATA", ""),
                     r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", ""),
                     r"Microsoft\Windows\Start Menu\Programs"),
    ]
    out: list[str] = []
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        for _root, _dirs, files in os.walk(base):
            for name in files:
                if name.lower().endswith(".lnk"):
                    out.append(os.path.splitext(name)[0])
    return out


def _msix_names() -> list[str]:
    """商店应用的包名（Claude 这类 MSIX 版只能从这儿看到）。"""
    winreg = _reg()
    path, hive = _MSIX_ROOT
    root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]
    out: list[str] = []
    try:
        with winreg.OpenKey(root, path) as key:
            total = winreg.QueryInfoKey(key)[0]
            for index in range(total):
                try:
                    out.append(winreg.EnumKey(key, index))
                except OSError:
                    continue
    except OSError:
        return out
    return out


def _exe_probe_names() -> list[str]:
    """只认文件的 CLI 工具。"""
    out: list[str] = []
    for label, target in _EXE_PROBES:
        if "\\" in target or "/" in target:
            if os.path.exists(os.path.expandvars(target)):
                out.append(label)
        elif shutil.which(target):
            out.append(label)
    return out


def installed_apps() -> list[str]:
    """这台机器上装了的应用名（去重后的并集）。"""
    names: set[str] = set()
    for reader in (_uninstall_names, _shortcut_names, _msix_names, _exe_probe_names):
        try:
            names.update(reader())
        except Exception:       # 某一路读失败不该让整个检测报废
            continue
    return sorted(names)


if __name__ == "__main__":       # 手动排查用：python -m agenthub.host.windows.installed
    for item in installed_apps():
        print(item)
