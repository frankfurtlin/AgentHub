# -*- coding: utf-8 -*-
"""从 exe 里抠出应用图标，转成 PNG 供 tkinter 显示。

tkinter 画不了 HICON，所以这里用 GDI 把图标读成 32 位位图、手工补上 alpha 通道，
再用内置的 zlib 手打一个 PNG 出来——不引入 Pillow 这类第三方依赖。

几个容易踩的坑，都在这儿处理掉了：
  · 现代图标（32bpp）自带 alpha；老图标没有，得反过来用 AND 掩码推出来。
  · Tk 的 canvas 画带透明的图片时，是把图片和**画布底色**混合，而不是和下层图元
    混合。所以这里直接把图标预乘到底衬色上，避免出现一圈诡异的杂边。
  · 各家图标自带的留白差别很大（有的方块铺满、有的内缩一大圈），并排会显得
    大小不一。这里按不透明区域的外接矩形做归一化，让它们看起来是同一套规格。

图标缓存在 %LOCALAPPDATA%\\AgentHub\\icons，同一个 exe + 尺寸 + 底衬只提取一次。
"""

from __future__ import annotations

import ctypes
import math
import os
import struct
import zlib
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

BI_RGB = 0
DIB_RGB_COLORS = 0

# 归一化后图标内容占目标边长的比例，留一点点边距避免顶格。
FILL_RATIO = 0.94

CACHE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AgentHub", "icons"
)


# ---------------------------------------------------------------- 结构体

class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


user32.PrivateExtractIconsW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
    wintypes.UINT, wintypes.UINT,
]
user32.PrivateExtractIconsW.restype = wintypes.UINT
user32.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [ctypes.c_void_p]

gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.GetDIBits.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]


# ---------------------------------------------------------------- 小工具

def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = (value or "").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255


def _luma(r: int, g: int, b: int) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# ---------------------------------------------------------------- 提取

def _dib_bits(hbm: int, width: int, height: int) -> bytearray | None:
    """把位图读成自顶向下的 32bpp BGRA 缓冲区。"""
    if not hbm:
        return None
    hdc = gdi32.CreateCompatibleDC(None)
    if not hdc:
        return None
    old = gdi32.SelectObject(hdc, hbm)
    try:
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height          # 负高度 = 自顶向下
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(width * height * 4)
        got = gdi32.GetDIBits(hdc, hbm, 0, height, buf,
                              ctypes.byref(header), DIB_RGB_COLORS)
        if not got:
            return None
        return bytearray(buf.raw)
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteDC(hdc)


def extract_rgba(exe_path: str, size: int) -> bytes | None:
    """抠出图标，返回 size*size*4 的 RGBA 字节流（直通 alpha，未预乘）。"""
    if not exe_path or not os.path.isfile(exe_path):
        return None

    hicon = ctypes.c_void_p()
    count = user32.PrivateExtractIconsW(
        exe_path, 0, size, size, ctypes.byref(hicon), None, 1, 0
    )
    if not count or not hicon.value:
        return None

    info = ICONINFO()
    try:
        if not user32.GetIconInfo(hicon, ctypes.byref(info)):
            return None

        color = _dib_bits(info.hbmColor, size, size)
        if color is None:
            return None
        mask = _dib_bits(info.hbmMask, size, size)

        total = size * size
        has_alpha = any(color[i * 4 + 3] for i in range(total))

        rgba = bytearray(total * 4)
        for i in range(total):
            off = i * 4
            b, g, r = color[off], color[off + 1], color[off + 2]
            if has_alpha:
                a = color[off + 3]
            elif mask is not None:
                # 掩码里白色（非 0）代表该像素透明
                a = 0 if mask[off] else 255
            else:
                a = 255
            rgba[off] = r
            rgba[off + 1] = g
            rgba[off + 2] = b
            rgba[off + 3] = a
        return bytes(rgba)
    finally:
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)
        user32.DestroyIcon(hicon)


# ---------------------------------------------------------------- 后处理

def _alpha_bbox(rgba: bytes, size: int, threshold: int = 12):
    """找出不透明像素的外接矩形。图标自带留白时靠它定位实际内容。"""
    min_x, min_y, max_x, max_y = size, size, -1, -1
    for y in range(size):
        row = y * size
        for x in range(size):
            if rgba[(row + x) * 4 + 3] > threshold:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return None
    return min_x, min_y, max_x, max_y


def _crop_center(rgba: bytes, size: int, cx: float, cy: float, out: int) -> bytes:
    """从 size×size 里裁一块以 (cx, cy) 为中心、边长 out 的方图；越界补透明。"""
    result = bytearray(out * out * 4)
    left = int(round(cx - out / 2.0))
    top = int(round(cy - out / 2.0))
    for y in range(out):
        sy = top + y
        if sy < 0 or sy >= size:
            continue
        src_row = sy * size
        dst_row = y * out
        for x in range(out):
            sx = left + x
            if sx < 0 or sx >= size:
                continue
            si = (src_row + sx) * 4
            di = (dst_row + x) * 4
            result[di:di + 4] = rgba[si:si + 4]
    return bytes(result)


def normalized_rgba(exe_path: str, target: int, fill: float = 0.94) -> bytes | None:
    """把图标裁剪并缩放到「内容恰好铺满 target×target」的规格。

    各家图标自带的留白差别很大（有的方块铺满、有的内缩一大圈），直接并排会显得
    大小不一。这里先量出不透明区域的外接矩形，如果内容偏小就换一个更大的尺寸
    重新提取（缩放交给 Windows 自己做，比手写重采样靠谱），再居中裁出目标尺寸。
    """
    base = extract_rgba(exe_path, target)
    if not base:
        return None

    box = _alpha_bbox(base, target)
    if not box:
        return base
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1 + 1) / 2.0, (y0 + y1 + 1) / 2.0

    span = max(x1 - x0 + 1, y1 - y0 + 1) / float(target)
    if span < fill:
        zoom = fill / span
        bigger = int(math.ceil(target * zoom))
        if bigger > target:
            src = extract_rgba(exe_path, bigger)
            if src:
                k = bigger / float(target)
                return _crop_center(src, bigger, cx * k, cy * k, target)

    return _crop_center(base, target, cx, cy, target)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    payload = tag + data
    return (struct.pack(">I", len(data)) + payload
            + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF))


def _encode_png(width: int, height: int, rows: bytes, color_type: int) -> bytes:
    """编码 PNG。color_type：2 = RGB，6 = RGBA。

    `rows` 是「每行前面多一个 filter 字节」的扫描线，长度必须严格等于
    `(width × 每像素字节数 + 1) × height`。对不上就直接报错——PNG 头里的高度
    和 IDAT 里的实际行数是两套数据，不一致时解码器**不会报错**，只会把缺的行
    一律补成黑色。这个坑踩过一次：存 430×615 的抓图，下面 30% 全黑，
    查了半天窗口尺寸和缓存，最后发现是扫描线少了一半的行。
    """
    per_pixel = 4 if color_type == 6 else 3
    expected = (width * per_pixel + 1) * height
    if len(rows) != expected:
        raise ValueError(
            "扫描线长度不对：%d×%d color_type=%d 需要 %d 字节，实际 %d"
            % (width, height, color_type, expected, len(rows)))

    out = b"\x89PNG\r\n\x1a\n"
    out += _png_chunk(b"IHDR",
                      struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
    out += _png_chunk(b"IDAT", zlib.compress(rows, 9))
    out += _png_chunk(b"IEND", b"")
    return out


def _rgba_rows(rgba: bytes, width: int, height: int | None = None) -> bytes:
    """RGBA 像素 -> 带 filter 字节的扫描线（每行首字节 0 = 不过滤）。

    `height` 省略时按正方形算——图标都是方的，调用处不必都写两遍。
    **非正方形一定要显式传 height**：只按宽度切的话会只取出前 `width` 行，
    PNG 头里却写着真实高度，于是下面那截静默变黑（见 `_encode_png`）。
    """
    if height is None:
        height = width
    stride = width * 4
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows += rgba[y * stride:(y + 1) * stride]
    return bytes(rows)


# ---------------------------------------------------------------- 对外接口

_RGBA_CACHE: dict[tuple[str, int], bytes | None] = {}
_PNG_CACHE: dict[tuple[str, int], bytes | None] = {}


def icon_png(exe_path: str, size: int) -> bytes | None:
    """取出应用图标，返回**透明底** PNG 字节；拿不到时返回 None。

    透明底是给 Web 前端用的——底色交给 CSS，同一张图在浅色和深色主题下都能
    直接用。旧 tkinter 版做不到这一点（canvas 贴图只跟画布底色混合、不认下层
    图元），只能按主题各预乘一份，四个图标 × 两种主题 × 多种尺寸 = 一堆 PNG。
    """
    if not exe_path or size <= 0:
        return None

    cache_key = (exe_path.lower(), size)
    if cache_key in _PNG_CACHE:
        return _PNG_CACHE[cache_key]

    if cache_key not in _RGBA_CACHE:
        try:
            _RGBA_CACHE[cache_key] = normalized_rgba(exe_path, size)
        except Exception:
            _RGBA_CACHE[cache_key] = None
    rgba = _RGBA_CACHE[cache_key]

    data: bytes | None = None
    if rgba and len(rgba) == size * size * 4:
        try:
            data = _encode_png(size, size, _rgba_rows(rgba, size), 6)
        except Exception:
            data = None
    _PNG_CACHE[cache_key] = data
    return data


if __name__ == "__main__":      # 手动验证：python icons.py <exe路径> [尺寸]
    import sys

    argv = sys.argv[1:]
    out_size = int(argv[1]) if len(argv) > 1 else 64
    for arg in argv[:1]:
        blob = icon_png(arg, out_size)
        if blob:
            name = os.path.splitext(os.path.basename(arg))[0] + ".png"
            with open(name, "wb") as fp:
                fp.write(blob)
            print("wrote %s (%d bytes)" % (name, len(blob)))
        else:
            print("failed: %s" % arg)
