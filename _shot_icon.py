# -*- coding: utf-8 -*-
"""确认自带图标真的嵌进了 exe：把 exe 的图标资源导成 PNG（_shot_icon.ps1），
再用同一套判据和 app.ico 的 32px 帧比。

GDI+ 直接读 app.ico 的 32px 帧数出来的蓝色像素明显偏少（157 vs exe 的 880），
所以两边都用 PIL 读，避免读法差异造成的假失败。
"""
import os
import sys

from PIL import Image, IcoImagePlugin

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
EXE_PNG = os.path.join(HERE, "_exe_icon.png")
ICO = os.path.join(HERE, "app.ico")

fails = []


def _flat(im):
    # Pillow 14 起 getdata 改名 get_flattened_data
    return im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()


def blue_ratio(im):
    """强调蓝占比：B 明显压过 R/G 的像素。"""
    im = im.convert("RGBA")
    n = tot = 0
    for r, g, b, a in _flat(im):
        tot += 1
        if a > 128 and b > 140 and (b - r) > 45 and (b - g) > 45:
            n += 1
    return n, tot, 100.0 * n / max(1, tot)


def mean_rgb(im):
    im = im.convert("RGB")
    px = list(_flat(im))
    k = len(px)
    return tuple(sum(c[i] for c in px) // k for i in range(3))


if not os.path.exists(EXE_PNG):
    print("先跑 _shot_icon.ps1 把 exe 图标导出来")
    sys.exit(1)

exe = Image.open(EXE_PNG)
print("exe 图标 %dx%d" % exe.size)

with open(ICO, "rb") as fh:
    ico = IcoImagePlugin.IcoFile(fh)          # IcoFile 要文件对象，不接路径
    sizes = sorted(ico.sizes(), key=lambda s: s[0])
    print("app.ico 内含帧：%s" % [s[0] for s in sizes])
    want = exe.size if exe.size in sizes else (32, 32)
    # getimage 返回的是惰性对象，必须趁文件还开着就解码，否则 load 时 seek 已关闭的文件
    src = ico.getimage(want).convert("RGBA")
print("拿来对比的 app.ico 帧：%dx%d" % src.size)

en, et, ep = blue_ratio(exe)
sn, st, sp = blue_ratio(src)
print("强调蓝占比  exe %d/%d = %.1f%%   ｜  app.ico %d/%d = %.1f%%" % (en, et, ep, sn, st, sp))

em, sm = mean_rgb(exe), mean_rgb(src)
print("平均色  exe %r  ｜  app.ico %r" % (em, sm))

if ep < 40:
    fails.append("exe 图标强调蓝只有 %.1f%%，不像我们的图标（可能还是默认图标）" % ep)
if sp < 40:
    fails.append("app.ico 自己强调蓝只有 %.1f%%，图标源文件有问题" % sp)
if abs(ep - sp) > 15:
    fails.append("两者强调蓝占比差 %.1f 个百分点，exe 里嵌的可能不是 app.ico" % abs(ep - sp))
if max(abs(em[i] - sm[i]) for i in range(3)) > 30:
    fails.append("平均色差太多：exe %r vs app.ico %r" % (em, sm))

if fails:
    print("\nICON FAIL")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("\nICON EMBEDDED OK：exe 里的图标就是 app.ico")
