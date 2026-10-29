# -*- coding: utf-8 -*-
"""生成 app.ico（打包时用 --icon 带进去，窗口和桌面快捷方式都会用它）。

渐变强调色圆角方块 + 白色 H3 字样。跑一次就行：python make_icon.py

macOS 的 .icns 由 make_icns.py 生成（它复用本模块的 make()）。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

import mac_compat

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app.ico")

TOP = (79, 106, 235)      # 亮一点
BOTTOM = (43, 66, 176)    # 深一点
S = 256


def gradient(size, c1, c2):
    """对角线线性渐变。"""
    base = Image.new("RGB", (size, size))
    px = base.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2.0 * (size - 1))
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return base


def pick_font(size):
    """挑一个能画出 H3 的字体。

    ★ 原先写死 C:\\Windows\\Fonts\\ 四个候选，macOS 上全不存在 → Pillow 退回
      默认位图字体，图标里的 "H3" 会变成一堆糊字。
      现在把两个平台的候选都交给 mac_compat.find_any_font() 按平台优先级挑
      （它内部还会退回系统常见字体），Windows 上第一个候选不变，行为与原来一致。
    """
    cands = [r"C:\Windows\Fonts\segoeuib.ttf",
             r"C:\Windows\Fonts\arialbd.ttf",
             r"C:\Windows\Fonts\msyhbd.ttc",
             r"C:\Windows\Fonts\msyh.ttc",
             # macOS：优先粗体，再退到通用字体
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
             "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
             "/System/Library/Fonts/SFNS.ttf",
             "/System/Library/Fonts/Helvetica.ttc",
             "/System/Library/Fonts/PingFang.ttc"]
    hit = mac_compat.find_any_font(cands)
    for p in ([hit] if hit else []) + cands:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size), p
            except Exception:
                continue
    return ImageFont.load_default(), "(default bitmap)"


def make(size):
    img = gradient(size, TOP, BOTTOM).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    d2 = ImageDraw.Draw(out)
    f, fname = pick_font(int(size * 0.52))
    text = "H3"
    try:
        bb = d2.textbbox((0, 0), text, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        ox, oy = -bb[0], -bb[1]
    except Exception:
        tw, th = d2.textsize(text, font=f)
        ox = oy = 0
    d2.text(((size - tw) / 2 + ox, (size - th) / 2 + oy - size * 0.02), text,
            font=f, fill=(255, 255, 255, 255))
    # 底部一道浅色小横杠，当作"输入行"的暗示
    bar_w, bar_h = int(size * 0.42), max(2, int(size * 0.045))
    bx = (size - bar_w) // 2
    by = int(size * 0.80)
    d2.rounded_rectangle([bx, by, bx + bar_w, by + bar_h], radius=bar_h // 2,
                         fill=(255, 255, 255, 165))
    if size == S:
        print("字体：%s" % fname)
    return out


def main():
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    big = make(S)
    big.save(OUT, format="ICO", sizes=sizes)
    print("已写出 %s（%d 字节，尺寸 %s）" % (OUT, os.path.getsize(OUT),
                                           [s[0] for s in sizes]))

    # 自检：重新打开，确认多尺寸都在、且不是全透明
    chk = Image.open(OUT)
    info = chk.info.get("sizes") or set()
    print("ICO 内含尺寸：%s" % sorted(s[0] for s in info))
    big2 = make(S).convert("RGBA")
    alpha = big2.getchannel("A")
    lo, hi = alpha.getextrema()
    print("alpha 极值 %s（圆角外必须是 0，内部必须是 255）" % ((lo, hi),))
    assert lo == 0 and hi == 255, "圆角透明通道不对"
    # Pillow 14 起 getdata 改名 get_flattened_data
    flat = alpha.get_flattened_data() if hasattr(alpha, "get_flattened_data") else alpha.getdata()
    opaque = sum(1 for p in flat if p > 128)
    print("不透明像素占比 %.1f%%" % (100.0 * opaque / (S * S)))
    assert 0.80 <= opaque / (S * S) <= 0.99, "圆角方块面积不对"
    print("ICON OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
