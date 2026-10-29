# -*- coding: utf-8 -*-
"""macOS 图标：生成 app.png（窗口图标兜底）+ app.icns（打包进 .app 用）。

用法（在 Mac 上，项目根目录）：
    python make_icns.py

它做三件事：
    1. 按 1024×1024 直接画一张 PNG（app.png）—— 源码直接跑 `python app.py` 时
       Tk 用它当窗口图标（见 app.py 的 set_window_icon）。
    2. 铺出 app.iconset/ 目录（10 个规格，含 @2x）。
    3. 调 macOS 自带的 `iconutil -c icns` 把 iconset 打成 app.icns。

★ iconutil 只在 macOS 上存在。所以在 Windows/Linux 上跑本脚本时只画 PNG，
  然后打印出在 Mac 上要执行的命令，不会报错也不会假装成功。
★ 不签名 / 不公证也能用 .icns：Dock 图标和「关于本机」里的图标都靠它。

为什么要有这一步：Windows 用 app.ico，macOS 只认 .icns。
PyInstaller 在 macOS 上 `--icon app.ico` 是无效的（会报警告并忽略）。
"""
import os
import subprocess
import sys

from PIL import Image

import make_icon

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
PNG = os.path.join(HERE, "app.png")
ICNS = os.path.join(HERE, "app.icns")
ICONSET = os.path.join(HERE, "app.iconset")

#: .icns 需要的 iconset 规格（文件名 → 像素边长）
ICONSET_SPECS = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

try:                                  # Pillow 10+ 用 Resampling 枚举，老版本用常量
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:                # pragma: no cover
    RESAMPLE = Image.LANCZOS


def manual_steps():
    """打印纯命令行版本（不想跑脚本的人照着敲就行）。"""
    print("    手动等价命令（在 Mac 上）：")
    print("        mkdir app.iconset")
    for name, size in ICONSET_SPECS:
        print("        sips -z %d %d app.png --out app.iconset/%s" % (size, size, name))
    print("        iconutil -c icns app.iconset -o app.icns")


def main() -> int:
    # 直接按 1024 画，别拿 256 放大（放大出来的 Retina 图标是糊的）
    big = make_icon.make(1024)
    big.save(PNG)
    print("已写出 %s（%d 字节，1024×1024）" % (PNG, os.path.getsize(PNG)))

    if sys.platform != "darwin":
        print("当前系统不是 macOS：.icns 只能在 Mac 上生成（iconutil 是 macOS 自带工具）。")
        print("到 Mac 上再跑一次 `python make_icns.py`，或者：")
        manual_steps()
        return 0

    os.makedirs(ICONSET, exist_ok=True)
    for name, size in ICONSET_SPECS:
        img = big if size == big.width else big.resize((size, size), RESAMPLE)
        img.save(os.path.join(ICONSET, name))
    print("已铺出 %s（%d 个规格）" % (ICONSET, len(ICONSET_SPECS)))

    try:
        r = subprocess.run(["iconutil", "-c", "icns", ICONSET, "-o", ICNS],
                           capture_output=True, text=True)
    except FileNotFoundError:
        print("[错误] 找不到 iconutil，请确认这是 macOS 且装了 Xcode Command Line Tools。")
        return 1
    if r.returncode != 0:
        print("[错误] iconutil 失败（%d）：\n%s%s" % (r.returncode, r.stdout, r.stderr))
        return 1
    print("已写出 %s（%d 字节）" % (ICNS, os.path.getsize(ICNS)))

    # 自检：读回 .icns 的头，确认是 icns 容器且非空
    with open(ICNS, "rb") as f:
        head = f.read(4)
    assert head == b"icns", "app.icns 头不是 icns：%r" % head
    print("ICNS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
