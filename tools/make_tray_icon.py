# -*- coding: utf-8 -*-
"""生成托盘图标 `assets/tray.ico`。

几何与配色完全照抄 `web/index.html` 里的水豚 SVG（viewBox 76）：
毛色径向渐变、耳朵、吻部、眼睛、鼻点、腮红、外环。
8 倍超采样绘制再缩小，边缘平滑。

用法（需要 Pillow，只做图标时才有这个依赖）：

    pip install pillow
    python tools/make_tray_icon.py

改了 `tokens.css` 里的 `--capy-*` 配色，或者改了 SVG 造型，重新跑一遍。
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

S = 76            # viewBox 尺寸
SS = 8            # 超采样倍数
W = S * SS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets")

# tokens.css 亮色主题。和 web/tokens.css 的 --capy-* 一一对应，改那边要同步这里
Capy = dict(
    hi=(0xF0, 0xD2, 0xA2), mid=(0xDC, 0xAE, 0x72), lo=(0xBE, 0x8F, 0x55),
    ear=(0xB0, 0x79, 0x3F), muzzle=(0xF6, 0xE8, 0xCE), ink=(0x4A, 0x37, 0x26),
    blush=(0xE8, 0x9A, 0x70), ring=(0x7A, 0x6A, 0x58),
)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def fur_color(x, y):
    """径向渐变：中心 (36%, 26%)，从 hi 到 lo，80% 半径到边。"""
    cx, cy = 0.36 * W, 0.26 * W
    r = math.hypot(x - cx, y - cy) / (0.80 * W)
    t = min(1.0, max(0.0, r))
    # 中段压一下，让 mid 色带出现得和 SVG 渐变一致
    if t < 0.5:
        return lerp(Capy["hi"], Capy["mid"], t * 2)
    return lerp(Capy["mid"], Capy["lo"], (t - 0.5) * 2)


def main() -> None:
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # 毛色径向渐变（逐像素）
    r_head = 31 * SS
    cx = cy = 38 * SS
    head = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    px = head.load()
    for j in range(int(cy - r_head) - 2, int(cy + r_head) + 3):
        for i in range(int(cx - r_head) - 2, int(cx + r_head) + 3):
            if (i - cx) ** 2 + (j - cy) ** 2 <= r_head * r_head:
                c = fur_color(i, j)
                px[i, j] = c + (255,)
    img.alpha_composite(head)

    d = ImageDraw.Draw(img)

    def circle(x, y, r, fill):
        d.ellipse([x * SS - r * SS, y * SS - r * SS,
                   x * SS + r * SS, y * SS + r * SS], fill=fill)

    def ell(x, y, rx, ry, fill):
        d.ellipse([(x - rx) * SS, (y - ry) * SS,
                   (x + rx) * SS, (y + ry) * SS], fill=fill)

    # 耳朵。SVG 里耳朵画在头之前、被头压住内侧；这里直接画完整的圆，
    # 圆心在头的外沿，观感和 SVG 一致
    circle(23, 15.5, 6.6, Capy["ear"])
    circle(53, 15.5, 6.6, Capy["ear"])
    circle(23, 15.8, 2.9, Capy["muzzle"] + (128,))
    circle(53, 15.8, 2.9, Capy["muzzle"] + (128,))

    # 吻部
    ell(38, 46, 12, 7.6, Capy["muzzle"])
    # 鼻点（SVG 里是两个 r=1.7/1.15 的椭圆）
    ell(34.4, 44.4, 1.7, 1.15, Capy["ink"] + (178,))
    ell(41.6, 44.4, 1.7, 1.15, Capy["ink"] + (178,))
    # 嘴 M34.6 50.4 Q38 52.6 41.4 50.4
    d.line([(34.6 * SS, 50.2 * SS), (38 * SS, 52.4 * SS), (41.4 * SS, 50.2 * SS)],
           fill=Capy["ink"], width=SS // 2, joint="curve")
    # 腮红
    ell(23, 42, 4, 2.6, Capy["blush"] + (128,))
    ell(53, 42, 4, 2.6, Capy["blush"] + (128,))
    # 眼睛 + 高光
    circle(28.4, 33.4, 3.15, Capy["ink"])
    circle(47.6, 33.4, 3.15, Capy["ink"])
    circle(27.4, 32.4, 1.05, (255, 255, 255, 217))
    circle(46.6, 32.4, 1.05, (255, 255, 255, 217))
    # 外环（画在最外层，压住毛边）
    d.ellipse([(38 - 35.5) * SS, (38 - 35.5) * SS,
               (38 + 35.5) * SS, (38 + 35.5) * SS],
              outline=Capy["ring"], width=int(2.5 * SS))

    os.makedirs(OUT_DIR, exist_ok=True)
    out = img.resize((304, 304), Image.LANCZOS)
    out.save(os.path.join(OUT_DIR, "tray.ico"), format="ICO",
             sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64)])
    print("saved", os.path.join(OUT_DIR, "tray.ico"))


if __name__ == "__main__":
    main()
