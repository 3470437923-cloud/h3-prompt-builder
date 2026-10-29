# -*- coding: utf-8 -*-
"""滚轮护栏的功能测试：真的生成 MouseWheel 事件，看值有没有被误改、页面有没有滚。"""
import sys, os, time, tkinter as tk
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A

fails = []
def ck(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)

root = tk.Tk()
a = A.App(root)
root.geometry("1200x720")
root.deiconify()
for _ in range(3):
    root.update(); root.update_idletasks(); time.sleep(0.05)

g = a._wheel_guards
print("护栏统计：spin=%d combo=%d text=%d text_outside=%d  未归类=%s"
      % (g["spin"], g["combo"], g["text"], g["text_outside"], g["other"]))
ck(g["spin"] >= 2, "至少罩住 2 个 Spinbox（时长 / 分段数）")
ck(g["combo"] >= 7, "至少罩住 7 个 Combobox（方案/分段样式/镜头/主体/3个角色/模型）")
ck(g["text"] >= 7, "左栏里的 Text 都做了滚动穿透")
ck(g["text_outside"] >= 2, "左栏外的 Text（输出/报告）保持原生滚动")
ck(not g["other"], "没有漏网的控件类别：%r" % (g["other"],))

canvas = a.scroll.canvas
spin, combo = a.duration, a.camera
small_text = a.pic_widgets[0]["body"]          # 图1 的身份 Text

def yview():
    return canvas.yview()

ck(yview()[1] < 1.0, "左栏内容确实溢出（否则无从验证滚动）：yview=%r" % (yview(),))

# ---- A：没焦点时，滚轮压在 Spinbox 上：值不许变，页面要滚 ----
spin.set(10)
before = (spin.get(), yview())
for _ in range(3):
    spin.event_generate("<MouseWheel>", delta=-120)
root.update()
after = (spin.get(), yview())
print("  A spin  %r -> %r" % (before, after))
ck(after[0] in ("10", "10.0"), "滚轮压在时长 Spinbox 上，值没被改（还是 %r）" % after[0])
ck(after[1][0] > before[1][0], "滚轮被转给长栏，页面往下滚了 %.3f -> %.3f" % (before[1][0], after[1][0]))

# ---- B：没焦点时，滚轮压在 Combobox 上：选项不许变 ----
idx0, yb = combo.current(), yview()
for _ in range(3):
    combo.event_generate("<MouseWheel>", delta=-120)
root.update()
ck(combo.current() == idx0, "滚轮压在镜头预设上，选项没被改（index 仍是 %d）" % combo.current())
ck(yview()[0] >= yb[0], "Combobox 上的滚轮也转给了长栏")

# ---- C：内容不满一屏的小 Text，滚轮穿透给长栏 ----
A.txt_set(small_text, "一个女生")
canvas.yview_moveto(0.0)
root.update()
yc = yview()[0]
for _ in range(2):
    small_text.event_generate("<MouseWheel>", delta=-120)
root.update()
ck(yview()[0] > yc, "短内容 Text 上的滚轮穿透到长栏：%.3f -> %.3f" % (yc, yview()[0]))
ck(A.txt_get(small_text) == "一个女生", "穿透没有改到 Text 内容")

# ---- D：内容超一屏的大 Text，滚轮归它自己，长栏不动 ----
A.txt_set(a.out, "\n".join("line %d" % i for i in range(400)))
root.update()
out_y0, page_y0 = a.out.yview()[0], yview()[0]
a.out.event_generate("<MouseWheel>", delta=-120)
root.update()
ck(a.out.yview()[0] > out_y0, "长文本自己滚：%.3f -> %.3f" % (out_y0, a.out.yview()[0]))
ck(abs(yview()[0] - page_y0) < 1e-9, "长文本滚动时长栏没跟着乱跑")

# ---- E：点进去有焦点时，Spinbox 仍然能用滚轮调值 ----
canvas.yview_moveto(0.0)
root.focus_force(); spin.focus_force()
for _ in range(2):
    root.update(); time.sleep(0.03)
try:
    focused = root.focus_get() is spin
except Exception:
    focused = False
print("  E focus_get 命中 Spinbox: %s" % focused)
if focused:
    v0 = float(spin.get())
    spin.event_generate("<MouseWheel>", delta=120)
    root.update()
    v1 = float(spin.get())
    print("  E spin  %.1f -> %.1f" % (v0, v1))
    ck(v1 != v0, "有焦点时滚轮照常调值（%.1f -> %.1f）" % (v0, v1))
else:
    print("  SKIP 拿不到 Tk 焦点（无人值守会话常见），跳过有焦点分支")

# ---- F：其他字段没被连带改掉 ----
ck(a.seg_count.get() == "0", "分段数没被顺手改掉：%r" % a.seg_count.get())
# 方案是存档恢复出来的（用户上次存的五段），所以只校验徽标与真实状态一致
scheme = a._current_scheme()
zh = "官方六段" if scheme == "official" else "工作流五段"
ck(scheme in ("official", "workflow5"), "方案值合法：%r" % scheme)
ck(a.badge_var.get() == "输出格式：" + zh,
   "顶部徽标与真实方案一致：%r" % a.badge_var.get())
# 手动切一次，徽标要跟着变
a.scheme.set(A.SCHEME_ZH_LIST[0])
a._on_scheme_change(quiet=True)
ck(a.badge_var.get() == "输出格式：官方六段", "切到官方六段后徽标跟着变：%r" % a.badge_var.get())
ck(str(a.seg_style["state"]) == "disabled", "官方六段下分段样式仍然锁死")

# ---- G：macOS 量级的 delta（±1/±3）也要能滚 —— 老代码 int(-1/120)==0 会滚不动 ----
# Tk-Aqua 的 <MouseWheel> delta 是很小的值，而 Windows 是 ±120 的倍数。
# 在 Windows 上用 event_generate 直接塞小 delta，就能复现 macOS 那条分支。
# 注意：E 段把焦点留在 Spinbox 上了，而「有焦点时滚轮改值」是设计行为，
# 所以这里先显式把焦点挪走，测的才是「无焦点」这条主路径。
try:
    canvas.focus_set()
    root.update()
except Exception:
    pass
if root.focus_get() is spin:
    print("  SKIP 抢不走 Spinbox 的焦点（无人值守会话），跳过 macOS 量级小 delta 分支")
else:
    spin.set(10)
    canvas.yview_moveto(0.0)
    root.update()
    y_small = yview()[0]
    for _ in range(3):
        spin.event_generate("<MouseWheel>", delta=-1)
    root.update()
    ck(yview()[0] > y_small,
       "macOS 量级 delta=-1 压在 Spinbox 上，左栏照样滚：%.3f -> %.3f" % (y_small, yview()[0]))
    ck(spin.get() in ("10", "10.0"), "小 delta 也没改到时长值：%r" % spin.get())
ck(A.wheel_steps(type("E", (), {"delta": -120})()) == 1
   and A.wheel_steps(type("E", (), {"delta": -1})()) == 1,
   "wheel_steps 归一：Windows(-120)→1 行、macOS(-1)→1 行")

root.destroy()
print("WHEEL FAIL: " + " | ".join(fails) if fails else "WHEEL OK")
sys.exit(1 if fails else 0)
