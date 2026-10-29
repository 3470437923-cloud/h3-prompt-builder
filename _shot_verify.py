# -*- coding: utf-8 -*-
"""截图体检：不看图，只用像素统计判断这张截图是不是废的。

判据（上一版 preview.png 就是栽在这里：亮像素只有 0.7%）：
  - 亮像素（均值 > 200）占比 >= 55%   正常是白底 GUI，应该在 60-90%
  - 暗像素（均值 < 60）占比 <= 15%    大面积发黑 = 抓到空窗口/没画完
  - 不同颜色数 >= 200                 纯色 = 窗口没内容
  - 四角都不能是同一个颜色             整窗同色 = 没抓对
  - 必须同时存在红、绿、蓝三类像素     报告的 err/ok 标签和按钮配色
"""
import os
import sys

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview.png")

im = Image.open(P).convert("RGB")
w, h = im.size
px = im.load()
n = w * h

light = dark = 0
reds = greens = blues = 0
seen = set()
for y in range(h):
    for x in range(w):
        r, g, b = px[x, y]
        m = (r + g + b) // 3
        if m > 200:
            light += 1
        if m < 60:
            dark += 1
        if r > 120 and g < 90 and b < 90:
            reds += 1
        if g > 110 and r < 90 and b < 90:
            greens += 1
        if b > 120 and r < 100 and g < 110:
            blues += 1
        seen.add((r >> 3, g >> 3, b >> 3))

corners = [px[2, 2], px[w - 3, 2], px[2, h - 3], px[w - 3, h - 3]]
lp = 100.0 * light / n
dp = 100.0 * dark / n

# ---- 新配色是否真的生效（clam 主题吃不到颜色时，强调色按钮会退回系统灰）----
PALETTE = {
    "ACCENT 主按钮/徽标字 #3b5bdb": ((59, 91, 219), 400),
    "ACCENT_SOFT 徽标底 #e7ecfd": ((231, 236, 253), 80),
    "PAGE 页面灰 #eceef1": ((236, 238, 241), 800),
    "CARD 卡片白 #ffffff": ((255, 255, 255), 20000),
    "TEXT 主文字 #1b1f24": ((27, 31, 36), 300),
    "TAB_OFF 未选中标签 #d9dde4": ((217, 221, 228), 200),
}
TOL = 8
hits = {}
for y in range(h):
    for x in range(w):
        r, g, b = px[x, y]
        for name, (t, _min) in PALETTE.items():
            if abs(r - t[0]) <= TOL and abs(g - t[1]) <= TOL and abs(b - t[2]) <= TOL:
                hits[name] = hits.get(name, 0) + 1

print("尺寸 %dx%d（%d 像素），文件 %d 字节" % (w, h, n, os.path.getsize(P)))
print("亮像素 %.1f%% ｜ 暗像素 %.1f%% ｜ 不同颜色 %d 种" % (lp, dp, len(seen)))
print("红 %d ｜ 绿 %d ｜ 蓝 %d" % (reds, greens, blues))
print("四角颜色", corners)
print("配色命中：")
for name, (t, mn) in PALETTE.items():
    got = hits.get(name, 0)
    print("  %-34s %7d px  （要求 >= %d）%s" % (name, got, mn, "OK" if got >= mn else "不足"))

# ---- 标题栏图标不在这张图里 ----
# PrintWindow(h, hdc, 3) 的 3 = PW_CLIENTONLY(1) | PW_RENDERFULLCONTENT(2)，只抓客户区，
# 标题栏压根不在截图内（左上角那个像素就是 header 的页面灰，正好反证）。
# 自带图标是否嵌进 exe，用 _shot_icon.py 直接从 exe 里抽 Icon 验，别在这里判。
ZONE = 56
accentish = 0
for y in range(min(ZONE, h)):
    for x in range(min(ZONE, w)):
        r, g, b = px[x, y]
        if b > 150 and (b - r) > 50 and g < 175:
            accentish += 1
print("客户区左上 %dx%d 内的强调蓝像素：%d（仅供参考，不作为判据）" % (ZONE, ZONE, accentish))

bad = []
if lp < 55:
    bad.append("亮像素只有 %.1f%%，这张图基本是黑的" % lp)
if dp > 15:
    bad.append("暗像素 %.1f%% 太多" % dp)
if len(seen) < 200:
    bad.append("颜色种类只有 %d，窗口大概是空的" % len(seen))
if len(set(corners)) == 1:
    bad.append("四个角颜色完全一样，可能抓到了一块纯色")
if reds + greens + blues == 0:
    bad.append("整张图没有任何饱和色，报告的 ok/err 标签和按钮都没画出来")
if greens == 0:
    bad.append("一个绿像素都没有（右侧校验报告应该有绿色 ok 标签）")
for name, (t, mn) in PALETTE.items():
    if hits.get(name, 0) < mn:
        bad.append("配色 %s 只有 %d px，低于 %d —— 新主题可能没生效"
                   % (name, hits.get(name, 0), mn))

if bad:
    print("\n判定：废图")
    for b in bad:
        print("  -", b)
    sys.exit(1)
print("\n判定：截图有效")
