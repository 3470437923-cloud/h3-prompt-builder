# -*- coding: utf-8 -*-
"""MiniMax H3 三图参考 · Input Text 生成器  (v0.1)

给 ComfyUI 的 MiniMax H3 (Ref2VA) 三图参考工作流写 Input Text 的小工具：
  * 三张参考图的内容全靠人工敲字（接的 LLM 没有识图能力）
  * 用强约束 system prompt 逼 LLM 严格按 subject_definitions / detailed_description
    / overall_style / overall_soundscape / non_diegetic_music 的格式输出
  * 输出后本地做结构校验，不合格可一键让 LLM 按校验报告修正

运行：      python app.py
自检：      python app.py --selftest
打包 exe：  build_exe.bat
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

import comfy_params
import config as cfgstore
import h3_skill
import longvideo
import llm_api
import mac_compat
import prompt_spec
import validate

IS_MAC = mac_compat.IS_MAC
IS_WIN = mac_compat.IS_WIN

APP_TITLE = "MiniMax H3 提示词生成器  v0.3 —— 三图参考 / 长视频首尾相连"

# 字体。Windows 保持原样；macOS 上这四个常量会在 _style() 里被 _use_mac_fonts()
# 换成 Tk 的原生命名字体（.AppleSystemUIFont / Menlo），见那个函数的注释。
UI_FONT = ("Microsoft YaHei UI", 10)
UI_FONT_SM = ("Microsoft YaHei UI", 9)
UI_FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
UI_FONT_TITLE = ("Microsoft YaHei UI", 13, "bold")
MONO_FONT = ("Consolas", 10)

# ---- 配色：冷灰页面 + 白卡片 + 单一强调色 ----
PAGE = "#eceef1"        # 页面底（标签页/外框）
CARD = "#ffffff"        # 卡片、输入区
BORDER = "#dfe3e8"      # 描边
TEXT = "#1b1f24"        # 主文字
MUTED = "#6f7780"       # 次要文字
ACCENT = "#3b5bdb"      # 强调：主按钮、选中态、插入光标
ACCENT_DK = "#2f49b0"   # 按下
ACCENT_SOFT = "#e7ecfd"  # 选中文字底色
FIELD = "#ffffff"       # 输入框底
HOVER = "#e9ebef"       # 悬停
TAB_OFF = "#d9dde4"     # 未选中标签
OK = "#1f8a4c"
WARN = "#b7791f"
ERR = "#c0392b"

# ---- 两个页面各自的控件名 ----
# 生成管线（_launch / _worker / _poll / _on_result）是按 OperatorInput 参数化的，
# 所以新页面不需要复制管线，只要告诉它「读哪些控件、写哪个输出框」。
PAGE_WIDGETS = {
    "gen": {
        "pics": "pic_widgets", "brief": "brief", "duration": "duration",
        "seg_style": "seg_style", "seg_count": "seg_count", "camera": "camera",
        "cam_custom": "cam_custom", "loop": "loop_var", "audio": "audio_var",
        "primary": "primary", "dialogue": "dialogue", "sound": "sound",
        "prohib": "prohib", "out": "out", "rep": "rep", "tab": "tab_gen",
    },
    "lv": {
        "pics": "lv_pics", "brief": "lv_brief", "duration": "lv_duration",
        "seg_count": "lv_shots", "camera": "lv_camera", "cam_custom": "lv_cam_custom",
        "loop": "lv_loop_var", "audio": "lv_audio_var", "primary": "lv_primary",
        "dialogue": "lv_dialogue", "sound": "lv_sound", "prohib": "lv_prohib",
        "out": "lv_out", "rep": "lv_rep", "tab": "tab_long",
        "mode": "lv_mode", "window": "lv_window", "context": "lv_context",
    },
}
PAGE_ZH = {"gen": "生成 Input Text", "lv": "长视频提示词"}

ROLE_KEYS = list(prompt_spec.ROLES.keys())
ROLE_ZH = [prompt_spec.ROLES[k]["zh"] for k in ROLE_KEYS]
STYLE_KEYS = list(prompt_spec.SEGMENT_STYLES.keys())
STYLE_ZH = [prompt_spec.SEGMENT_STYLES[k]["zh"] for k in STYLE_KEYS]
CAMERA_KEYS = list(prompt_spec.CAMERA_PRESETS.keys())
SCHEME_KEYS = ["official", "workflow5"]
SCHEME_ZH_LIST = [h3_skill.SCHEMES[k]["zh"] for k in SCHEME_KEYS]
SCHEME_ZH2KEY = dict(zip(SCHEME_ZH_LIST, SCHEME_KEYS))
OFFICIAL_SHOT_ZH = "[Shot N] At MM:SS.mmm —— 官方固定记法"


# ==========================================================================
# 小工具
# ==========================================================================
def enable_dpi_awareness() -> None:
    """高 DPI 感知。

    Windows 的调用语义完全不变（shcore.SetProcessDpiAwareness(2)，失败退
    SetProcessDPIAware），只是搬进 mac_compat 统一管理；macOS 上它是空操作 ——
    Retina 缩放由系统负责，没有「进程 DPI 感知」这个概念。
    """
    mac_compat.set_dpi_awareness()


def set_window_icon(root) -> None:
    """窗口 / 任务栏图标。

    Windows：app.ico（打包后由 PyInstaller 解到 sys._MEIPASS），行为与原来一致。
    macOS：iconbitmap 在 macOS 上根本不存在；Dock 图标只能由 .app bundle 里的
           .icns 决定。源码直接跑时用 iconphoto + app.png 兜底（有 app.png 就用，
           没有就什么都不做，不影响启动）。
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    if IS_WIN:
        try:
            p = os.path.join(base, "app.ico")
            if os.path.exists(p):
                root.iconbitmap(p)
        except Exception:
            pass
        return
    # macOS（以及 Linux）：iconbitmap 不存在 / 不认 .ico，改用 PNG
    try:
        p = os.path.join(base, "app.png")
        if os.path.exists(p):
            img = tk.PhotoImage(file=p)
            root.iconphoto(True, img)
            # 必须留个引用：PhotoImage 被 GC 掉之后图标会变成空白
            root._h3_icon_ref = img
    except Exception:
        pass


def _use_mac_fonts() -> None:
    """macOS：字体改用 Tk 的原生命名字体（原生优先路线的核心一步）。

    ★ 为什么不是把 "Microsoft YaHei UI" 换成 ("PingFang SC", 10) ★
      Tk 在 macOS 上的 TkDefaultFont / TkTextFont 就是系统 UI 字体
      （.AppleSystemUIFont；中文由 CoreText 自动回落到苹方），字号也是系统默认的
      13pt —— 直接沿用才是「原生优先」。硬写 PingFang SC + Windows 那套 10pt
      反而两头不讨好：拉丁字形丢了 SF Pro，10pt 在 Mac 上明显偏小（Mac 的正文是 13pt）。
      这里只按原有的层级关系派生「小号 / 粗体 / 标题」三个变体，保证界面里的
      字号对比和 Windows 版一致（正文 / 小字 / 标题）。
    """
    global UI_FONT, UI_FONT_SM, UI_FONT_BOLD, UI_FONT_TITLE, MONO_FONT
    try:
        base = tkfont.nametofont("TkDefaultFont")
        size = int(base.cget("size"))
        sm = base.copy()
        sm.configure(size=size - 2)
        bold = base.copy()
        bold.configure(weight="bold")
        title = base.copy()
        title.configure(size=size + 2, weight="bold")
        UI_FONT = base
        UI_FONT_SM = sm
        UI_FONT_BOLD = bold
        UI_FONT_TITLE = title
        MONO_FONT = tkfont.nametofont("TkFixedFont")     # macOS 上是 Menlo
    except Exception:
        # 万一 Tk 里没有这些命名字体（非标准构建），退回 mac_compat 给的字体族
        fam = mac_compat.ui_font_family(True)
        mfam = mac_compat.ui_font_family(False)
        UI_FONT = (fam, 13)
        UI_FONT_SM = (fam, 11)
        UI_FONT_BOLD = (fam, 13, "bold")
        UI_FONT_TITLE = (fam, 15, "bold")
        MONO_FONT = (mfam, 12)


def txt_get(w: tk.Text) -> str:
    return w.get("1.0", "end-1c")


def txt_set(w: tk.Text, s: str) -> None:
    w.delete("1.0", "end")
    w.insert("1.0", s or "")


def txt_set_ro(w: tk.Text, s: str) -> None:
    """给只读 Text 写内容。

    注意：tk.Text 处于 state="disabled" 时，delete/insert **既不报错也不生效**（静默吞掉），
    所以必须先切回 normal 写完再切回去。
    """
    was = str(w.cget("state"))
    if was != "normal":
        w.configure(state="normal")
    w.delete("1.0", "end")
    w.insert("1.0", s or "")
    if was != "normal":
        w.configure(state="disabled")


def walk_widgets(w):
    """深度优先遍历一个容器下的所有子控件。"""
    for c in w.winfo_children():
        yield c
        for g in walk_widgets(c):
            yield g


def wheel_steps(event) -> int:
    """把各平台滚轮事件的 delta 归一成「行数」（负数 = 向下滚）。

    ★ 为什么必须归一 ★
      Windows 的 <MouseWheel> delta 是 ±120 的倍数（滚一格 = 120）；
      macOS 的 Tk-Aqua 只给很小的值（常见 ±1，快速划动 ±2/±3）。
      老代码三处都写死 `int(-e.delta / 120)`：在 macOS 上 int(-1/120) == 0，
      滚动量恒为 0 —— 而 on_wheel 照样 `return "break"` 掐掉类绑定和 bind_all，
      结果就是「鼠标停在 Combobox / Spinbox / 小 Text 上时左栏完全不滚」。

    返回值语义与原来完全一致：正数向上、负数向下，绝对值 = 行数
    （Windows 上「快滚 = 多滚几行」的手感保持不动）。
    """
    try:
        d = int(getattr(event, "delta", 0) or 0)
    except (TypeError, ValueError):
        return 0
    if not d:
        return 0
    if abs(d) >= 120:                    # Windows / 部分 Linux：±120 的倍数
        return -int(d / 120)
    return -d                            # macOS / Linux：±1 量级


def guard_wheel(widget, scroll_cmd=None, allow_when_focused=False):
    """掐掉滚轮对 Spinbox / Combobox 的误改。

    tkinter 的绑定顺序是「控件 → 类 → toplevel → all」，Spinbox/Combobox 的**类绑定**
    会把滚轮解释成加减数值 / 换选项，而容器上的 bind_all 排在最后，等它跑完值早就变了。
    所以必须在控件自己身上绑（第一顺位）并返回 "break"，才能掐断类绑定。

    allow_when_focused=True 时（Spinbox）：点进去有焦点了才允许滚轮改值，
    这样"想调时长"和"想滚页面"两种意图都不打架。
    """
    def focused():
        try:
            return widget.winfo_toplevel().focus_get() is widget
        except Exception:
            return False

    def forward(units):
        if scroll_cmd is None:
            return
        try:
            scroll_cmd(units)
        except Exception:
            pass

    def on_wheel(e):
        if allow_when_focused and focused():
            return None                      # 交给类绑定，正常改值
        forward(wheel_steps(e))              # 没焦点：滚轮还给页面
        return "break"

    def on_btn(step):
        def h(_e):
            if allow_when_focused and focused():
                return None
            forward(step)
            return "break"
        return h

    widget.bind("<MouseWheel>", on_wheel)
    widget.bind("<Button-4>", on_btn(-3))    # Linux/触控板
    widget.bind("<Button-5>", on_btn(3))


def text_wheel_passthrough(widget, scroll_cmd):
    """文本框内容不满一屏时，滚轮应该滚页面而不是卡在文本框里。"""
    def on_wheel(e):
        try:
            first, last = widget.yview()
        except Exception:
            return None
        if first <= 0.0 and last >= 1.0:      # 没有可滚动的余地
            try:
                scroll_cmd(wheel_steps(e))
            except Exception:
                pass
            return "break"
        return None                           # 让文本框自己滚
    widget.bind("<MouseWheel>", on_wheel)


class ScrollFrame(ttk.Frame):
    """带纵向滚动的容器（左侧输入项很多）。"""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, width=620)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _on_inner(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self.inner_id, width=e.width)

    def _on_wheel(self, e):
        # 只在鼠标位于本容器内时滚动，避免抢走输出框的滚轮
        w = e.widget
        while w is not None:
            if w is self.inner:
                self.canvas.yview_scroll(wheel_steps(e), "units")
                return
            w = getattr(w, "master", None)


# ==========================================================================
# 主程序
# ==========================================================================
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = cfgstore.load()
        self.q: "queue.Queue" = queue.Queue()
        self.busy = False
        self.cancel = threading.Event()
        self.current_text = ""
        self.last_report: list = []
        self.last_usage: dict = {}

        root.title(APP_TITLE)
        self._fit_window(root)

        self._style()
        self._build()
        self._restore_work()
        self._on_scheme_change(quiet=True)     # 没有存档时也要把分镜记法/音频段文案同步好
        self._build_menu()                     # macOS 原生菜单栏（Windows 上不建，界面保持原样）
        self._bind_keys()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()
        self.set_status("就绪。先填三张参考图的文字描述，再写需求，然后点「生成 Input Text」。")

    # ---------------------------------------------------------------- 窗口
    def _fit_window(self, root):
        """默认 1360x860，但先按屏幕尺寸夹一下 —— 1366x768 的笔记本 / 投屏上
        860 高会直接超出屏幕，标题栏跑到屏幕外就拖不回来了。"""
        try:
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
        except Exception:
            sw, sh = 1360, 860
        w = min(1360, max(760, sw - 40))
        h = min(860, max(520, sh - 120))       # 120 ≈ macOS 菜单栏 + Dock / Windows 任务栏
        root.geometry("%dx%d" % (w, h))
        root.minsize(min(1080, w), min(680, h))

    # ---------------------------------------------------------------- 样式
    def _style(self):
        style = ttk.Style(self.root)
        # clam 是唯一能真正吃颜色配置的内置主题（vista 会忽略绝大部分设置）。
        # macOS 上不切：aqua 是系统原生主题，切到 clam 会让所有按钮、输入框、
        # 标签页变成方头方脑的 Tk 自绘外观 —— 与「原生优先」的验收标准相悖。
        if not IS_MAC:
            try:
                style.theme_use("clam")
            except Exception:
                pass

        if IS_MAC:
            # macOS：直接沿用 Tk 的原生命名字体，不做 family 覆写（原生优先）
            _use_mac_fonts()
        else:
            default = tkfont.nametofont("TkDefaultFont")
            default.configure(family=UI_FONT[0], size=UI_FONT[1])
            for name in ("TkTextFont", "TkMenuFont", "TkTooltipFont", "TkHeadingFont",
                         "TkFixedFont", "TkCaptionFont", "TkSmallCaptionFont"):
                try:
                    tkfont.nametofont(name).configure(family=UI_FONT[0], size=UI_FONT[1])
                except Exception:
                    pass
            try:
                tkfont.nametofont("TkFixedFont").configure(family=MONO_FONT[0],
                                                           size=MONO_FONT[1])
            except Exception:
                pass

        # 经典控件（tk.Text / Canvas / Listbox）用 option database 统一，
        # 免得每处 create 都写一遍颜色。
        # ★ 这一层在两个平台上都**真正生效**（aqua 也吃 option database），
        #   是 macOS 上唯一能完整保住的视觉层，所以原样保留。
        r = self.root
        r.configure(background=PAGE)
        r.option_add("*Text.background", CARD)
        r.option_add("*Text.foreground", TEXT)
        r.option_add("*Text.insertBackground", ACCENT)
        r.option_add("*Text.insertWidth", 2)
        r.option_add("*Text.selectBackground", ACCENT_SOFT)
        r.option_add("*Text.selectForeground", TEXT)
        r.option_add("*Text.relief", "solid")
        r.option_add("*Text.borderWidth", 1)
        r.option_add("*Text.borderColor", BORDER)
        r.option_add("*Text.highlightThickness", 0)
        r.option_add("*Text.padX", 6)
        r.option_add("*Text.padY", 5)
        r.option_add("*Canvas.background", CARD)
        r.option_add("*Canvas.highlightThickness", 0)
        r.option_add("*Listbox.background", CARD)
        r.option_add("*Listbox.selectBackground", ACCENT)
        r.option_add("*Scrollbar.troughColor", CARD)

        # 默认底色 = 卡片白，这样 Labelframe 里的 Label 不会出现灰补丁
        style.configure(".", background=CARD, foreground=TEXT, font=UI_FONT,
                        bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD,
                        focuscolor=ACCENT, selectbackground=ACCENT,
                        selectforeground="#ffffff")
        style.configure("TFrame", background=CARD)
        style.configure("Page.TFrame", background=PAGE)
        style.configure("Card.TFrame", background=CARD)

        style.configure("TLabel", background=CARD, foreground=TEXT)
        style.configure("Hint.TLabel", background=CARD, foreground=MUTED, font=UI_FONT_SM)
        style.configure("Warn.TLabel", background=CARD, foreground=ERR, font=UI_FONT_SM)
        style.configure("Ok.TLabel", background=CARD, foreground=OK, font=UI_FONT_SM)
        style.configure("Page.TLabel", background=PAGE, foreground=TEXT)
        style.configure("PageHint.TLabel", background=PAGE, foreground=MUTED, font=UI_FONT_SM)
        style.configure("Title.TLabel", background=PAGE, foreground=TEXT, font=UI_FONT_TITLE)
        style.configure("Sub.TLabel", background=PAGE, foreground=MUTED, font=UI_FONT_SM)
        style.configure("Badge.TLabel", background=ACCENT_SOFT, foreground=ACCENT,
                        font=UI_FONT_SM, padding=(9, 3))
        style.configure("Status.TLabel", background=PAGE, foreground=MUTED, padding=(10, 6))
        style.configure("Section.TLabel", background=CARD, foreground=ACCENT, font=UI_FONT_BOLD)

        # 卡片：白底 + 1px 描边
        style.configure("TLabelframe", background=CARD, bordercolor=BORDER,
                        relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=CARD, foreground=ACCENT,
                        font=UI_FONT_BOLD)

        # 按钮：次要 = 浅灰描边；主要 = 强调色实心
        style.configure("TButton", background="#f4f6f8", foreground=TEXT, font=UI_FONT,
                        bordercolor="#ccd2da", lightcolor="#f4f6f8", darkcolor="#f4f6f8",
                        padding=(13, 6), borderwidth=1, relief="raised")
        style.map("TButton",
                  background=[("pressed", "#dfe3e8"), ("active", HOVER), ("disabled", "#f6f7f9")],
                  foreground=[("disabled", "#a9aeb6")],
                  bordercolor=[("active", "#b9c0ca")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        font=UI_FONT_BOLD, bordercolor=ACCENT_DK, lightcolor=ACCENT,
                        darkcolor=ACCENT, padding=(18, 9), borderwidth=0)
        style.map("Accent.TButton",
                  background=[("pressed", "#26398f"), ("active", ACCENT_DK),
                              ("disabled", "#aab5e6")],
                  foreground=[("disabled", "#eef1fb")])
        # 老代码里的 Big.TButton 直接升级成强调按钮
        style.configure("Big.TButton", background=ACCENT, foreground="#ffffff",
                        font=UI_FONT_BOLD, bordercolor=ACCENT_DK, lightcolor=ACCENT,
                        darkcolor=ACCENT, padding=(18, 9), borderwidth=0)
        style.map("Big.TButton",
                  background=[("pressed", "#26398f"), ("active", ACCENT_DK),
                              ("disabled", "#aab5e6")],
                  foreground=[("disabled", "#eef1fb")])

        # 输入类
        for s in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(s, fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                            bordercolor="#cfd4da", lightcolor=FIELD, darkcolor=FIELD,
                            insertcolor=TEXT, arrowcolor=MUTED, padding=4,
                            selectbackground=ACCENT, selectforeground="#ffffff")
            style.map(s, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                      darkcolor=[("focus", ACCENT)],
                      fieldbackground=[("disabled", "#f1f2f5"), ("readonly", "#f7f8fa")],
                      foreground=[("disabled", "#9aa0a8"), ("readonly", MUTED)])

        style.configure("TCheckbutton", background=CARD, foreground=TEXT,
                        indicatorcolor="#ffffff", bordercolor="#cfd4da", padding=2)
        style.map("TCheckbutton", background=[("active", CARD)],
                  indicatorcolor=[("selected", ACCENT), ("active", "#eef0f3")])
        style.configure("TRadiobutton", background=CARD, foreground=TEXT,
                        indicatorcolor="#ffffff", bordercolor="#cfd4da", padding=2)
        style.map("TRadiobutton", background=[("active", CARD)],
                  indicatorcolor=[("selected", ACCENT), ("active", "#eef0f3")])

        # 滚动条：细、灰、悬停加深
        for s in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(s, background="#c9ced6", troughcolor=CARD, bordercolor=CARD,
                            lightcolor="#c9ced6", darkcolor="#c9ced6",
                            arrowcolor=MUTED, arrowsize=13)
            style.map(s, background=[("active", "#a8b0bb"), ("pressed", "#8f98a5")])

        style.configure("TProgressbar", background=ACCENT, troughcolor="#e3e6eb",
                        bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)

        # 标签页：未选中偏灰，选中与页面同色（浏览器式）
        style.configure("TNotebook", background=PAGE, bordercolor=BORDER,
                        tabmargins=(8, 6, 8, 0))
        style.configure("TNotebook.Tab", background=TAB_OFF, foreground=MUTED,
                        padding=(18, 9), font=UI_FONT, bordercolor=BORDER)
        style.map("TNotebook.Tab",
                  background=[("selected", PAGE), ("active", "#e3e6eb")],
                  foreground=[("selected", TEXT), ("active", TEXT)],
                  font=[("selected", UI_FONT_BOLD)])

        style.configure("TPanedwindow", background=PAGE)
        style.configure("Horizontal.TPanedwindow", background=PAGE)
        style.configure("Sash", sashthickness=8, background="#d7dbe1")
        style.configure("Treeview", background=CARD, fieldbackground=CARD,
                        foreground=TEXT, rowheight=24, bordercolor=BORDER)
        style.configure("Treeview.Heading", background="#f2f4f7", foreground=MUTED,
                        font=UI_FONT_SM, relief="flat")

        # ---- macOS：aqua 主题的取舍 ----
        # 上面这一大段里，background / fieldbackground / bordercolor / lightcolor /
        # darkcolor / indicatorcolor / troughcolor / relief / borderwidth 全是 clam
        # 专有选项，aqua 下**静默失效**（原生控件自己画外观）。这正是「原生优先」
        # 想要的结果，所以不删、也不为 macOS 重写一套 —— 保留它只为 Windows 继续
        # 用 clam 时颜色逐像素不变。真正在 aqua 下生效的是：font、foreground、
        # padding（字号层级、提示色/警告色/强调色、控件内边距都还在）。
        #
        # 唯一必须动手的是「强调按钮」：aqua 忽略 background，若还留着
        # foreground="#ffffff"，就会变成「原生浅灰按钮 + 白字」→ 直接看不见。
        # 这里把两个强调按钮的前景色改回可读的深色，并清掉 disabled 态的浅色映射。
        if IS_MAC:
            for s in ("Accent.TButton", "Big.TButton"):
                style.configure(s, foreground=TEXT)
                style.map(s, foreground=[])

    # ---------------------------------------------------------------- 构建
    def _build(self):
        # ---------- 顶部标题栏 ----------
        head = ttk.Frame(self.root, style="Page.TFrame")
        head.pack(fill="x", side="top", padx=14, pady=(11, 0))
        ttk.Label(head, text="MiniMax H3 提示词生成器",
                  style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="v0.3", style="Sub.TLabel").pack(side="left", padx=(9, 0), pady=(5, 0))
        self.badge_var = tk.StringVar(value="")
        self.badge = ttk.Label(head, textvariable=self.badge_var, style="Badge.TLabel")
        self.badge.pack(side="right")
        ttk.Label(head, text="Ref2VA 六段 · strict_prompt_tags 硬校验",
                  style="Sub.TLabel").pack(side="right", padx=(0, 12))

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        self.tab_gen = ttk.Frame(self.nb, style="Page.TFrame")
        self.tab_long = ttk.Frame(self.nb, style="Page.TFrame")
        self.tab_api = ttk.Frame(self.nb, style="Page.TFrame")
        self.tab_rule = ttk.Frame(self.nb, style="Page.TFrame")
        self.tab_skill = ttk.Frame(self.nb, style="Page.TFrame")
        self.nb.add(self.tab_gen, text="  生成 Input Text  ")
        self.nb.add(self.tab_long, text="  长视频提示词  ")
        self.nb.add(self.tab_api, text="  API 设置  ")
        self.nb.add(self.tab_rule, text="  格式规则 / 自定义硬规则  ")
        self.nb.add(self.tab_skill, text="  官方 Skill  ")

        self._build_gen_tab()
        self._build_long_tab()
        self._build_api_tab()
        self._build_rule_tab()
        self._build_skill_tab()

        bar = ttk.Frame(self.root, style="Page.TFrame")
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="")
        self.lbl_status = ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel",
                                    anchor="w")
        self.lbl_status.pack(fill="x", side="left", expand=True)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=140)

        self._apply_wheel_guards()

    # ------------------------------------------------------------ 滚轮护栏
    @staticmethod
    def _is_inside(w, ancestor) -> bool:
        while w is not None:
            if w is ancestor:
                return True
            w = getattr(w, "master", None)
        return False

    def _apply_wheel_guards(self):
        """左侧长栏可以滚，但栏里的 Spinbox / Combobox 也吃滚轮 ——
        往下滚页面时很容易把时长、分段数、镜头预设顺手改错。

        统一掐掉：控件没有焦点时，滚轮一律交给长栏滚动；点进去拿到焦点了才允许改值
        （Combobox 更严，任何时候都不吃滚轮，只能点开下拉选）。
        """
        self._wheel_guards = {"spin": 0, "combo": 0, "text": 0, "text_outside": 0, "other": []}

        # 两个页面各有一个长栏，各自的滚轮目标不同
        frames = [sf for sf in (getattr(self, "scroll", None),
                                getattr(self, "lv_scroll", None)) if sf is not None]

        def scroll_of(w):
            for sf in frames:
                if self._is_inside(w, sf.inner):
                    return (lambda units, c=sf.canvas: c.yview_scroll(int(units), "units"))
            return None

        # 这些类别保持原生滚轮行为：长栏外的 Text 各有自己的滚动条，本来就该自己滚
        SKIP = ("TLabel", "TButton", "TCheckbutton", "TRadiobutton", "TFrame", "TLabelframe",
                "Canvas", "TNotebook", "TNotebook.Tab", "TPanedwindow", "TScrollbar",
                "TkRoot", "TProgressbar", "TEntry", "Label", "Frame", "Button", "Sash")
        for w in walk_widgets(self.root):
            cls = w.winfo_class()
            cmd = scroll_of(w)
            inside = cmd is not None
            if cls in ("TSpinbox", "Spinbox"):
                guard_wheel(w, cmd, allow_when_focused=True)
                self._wheel_guards["spin"] += 1
            elif cls == "TCombobox":
                guard_wheel(w, cmd, allow_when_focused=False)
                self._wheel_guards["combo"] += 1
            elif cls == "Text":
                if inside:
                    text_wheel_passthrough(w, cmd)
                    self._wheel_guards["text"] += 1
                else:
                    self._wheel_guards["text_outside"] += 1
            elif cls not in SKIP:
                self._wheel_guards["other"].append(cls)

    # ------------------------------------------------------------ 生成页
    def _build_gen_tab(self):
        self.pane = pane = ttk.Panedwindow(self.tab_gen, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=4, pady=4)

        # ---------- 左：输入 ----------
        left_wrap = ttk.Frame(pane)
        pane.add(left_wrap, weight=5)
        self.scroll = ScrollFrame(left_wrap)
        self.scroll.pack(fill="both", expand=True)
        left = self.scroll.inner
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="① 三张参考图 —— LLM 看不到图，图上的一切都要你手写",
                  style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(6, 2))
        ttk.Label(left, style="Hint.TLabel",
                  text="工作流里 ref_image_0/1/2 的顺序就是 <Picture 1>/<Picture 2>/<Picture 3>，别填反。"
                  ).grid(row=1, column=0, sticky="w", pady=(0, 6))

        self.pic_widgets = []
        for i in range(3):
            lf = ttk.Labelframe(left, text="参考图 %d   →   <Picture %d>" % (i + 1, i + 1))
            lf.grid(row=2 + i, column=0, sticky="nsew", pady=(0, 8), padx=6)
            lf.columnconfigure(1, weight=1)
            lf.columnconfigure(3, weight=2)

            ttk.Label(lf, text="这张图是").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=(8, 2))
            role = ttk.Combobox(lf, values=ROLE_ZH, state="readonly", width=22)
            role.set(ROLE_ZH[0])
            role.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=(8, 2))

            ttk.Label(lf, text="英文短称").grid(row=0, column=2, sticky="e", padx=(0, 4), pady=(8, 2))
            short = ttk.Entry(lf)
            short.grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=(8, 2))

            body = tk.Text(lf, height=4, wrap="word", font=UI_FONT, undo=True)
            body.grid(row=1, column=0, columnspan=4, sticky="nsew", padx=8, pady=(2, 4))
            hint = ("例：年轻男性，短而凌乱的黑发，额前长刘海；深棕色眼睛；自然浓眉；暖白肤色，"
                    "右脸颊有一颗小痣；薄唇；纯白圆领棉T恤。画面里只有他一个人。")
            if i == 1:
                hint = "例：面部细节参考。要严格保留眼型、眉毛粗细、鼻型、唇型、脸颊小痣、自然皮肤质感，不要美颜磨皮。"
            if i == 2:
                hint = "例：场景与光线参考。纯浅米色墙面；正面柔和均匀的室内光；中性色温；半身自拍式构图。"
            ttk.Label(lf, style="Hint.TLabel", text="提示：" + hint, wraplength=460,
                      justify="left").grid(row=2, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))
            self.pic_widgets.append({"role": role, "short": short, "body": body})

        # ---------- 需求 ----------
        lf2 = ttk.Labelframe(left, text="② 视频需求（中文随便写，写清楚“谁、在哪、做什么、怎么变”）")
        lf2.grid(row=5, column=0, sticky="nsew", pady=(4, 8), padx=6)
        lf2.columnconfigure(0, weight=1)
        self.brief = tk.Text(lf2, height=8, wrap="word", font=UI_FONT, undo=True)
        self.brief.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        ttk.Label(lf2, style="Hint.TLabel", wraplength=460, justify="left",
                  text="越具体越好：动作顺序、道具、表情变化、景别（半身/特写）、情绪、节奏。"
                       "没写的东西 LLM 不允许自己编，所以重要的都写出来。"
                  ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        # ---------- 参数 ----------
        lf3 = ttk.Labelframe(left, text="③ 生成参数")
        lf3.grid(row=6, column=0, sticky="nsew", pady=(0, 8), padx=6)
        for c, w in ((0, 0), (1, 1), (2, 0), (3, 1)):
            lf3.columnconfigure(c, weight=w)

        ttk.Label(lf3, text="输出格式方案").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=(8, 2))
        self.scheme = ttk.Combobox(lf3, values=SCHEME_ZH_LIST, state="readonly", width=30)
        self.scheme.set(SCHEME_ZH_LIST[0])
        self.scheme.grid(row=0, column=1, columnspan=2, sticky="w", pady=(8, 2))
        self.scheme.bind("<<ComboboxSelected>>", lambda e: self._on_scheme_change())
        ttk.Label(lf3, style="Hint.TLabel", wraplength=300, justify="left",
                  text="官方六段 = MiniMax 官方 skill 的 Ref2VA 格式（多 summary 和 "
                       "retention_analysis，没有 overall_style）").grid(
            row=0, column=3, sticky="w", padx=(6, 8), pady=(8, 2))

        ttk.Label(lf3, text="时长（秒）").grid(row=1, column=0, sticky="w", padx=(8, 4), pady=(8, 2))
        self.duration = ttk.Spinbox(lf3, from_=2.0, to=15.0, increment=0.5, width=8)
        self.duration.set("6")
        self.duration.grid(row=1, column=1, sticky="w", pady=(8, 2))
        self.duration.bind("<KeyRelease>", lambda e: self._update_note())
        self.duration.bind("<<SpinboxChanged>>", lambda e: self._update_note())

        self.lbl_seg_style = ttk.Label(lf3, text="分段样式")
        self.lbl_seg_style.grid(row=1, column=2, sticky="e", padx=(0, 4), pady=(8, 2))
        self.seg_style = ttk.Combobox(lf3, values=STYLE_ZH, state="readonly", width=30)
        self.seg_style.set(STYLE_ZH[0])
        self.seg_style.grid(row=1, column=3, sticky="w", pady=(8, 2))

        self.lbl_seg_count = ttk.Label(lf3, text="分段数")
        self.lbl_seg_count.grid(row=2, column=0, sticky="w", padx=(8, 4), pady=2)
        self.seg_count = ttk.Spinbox(lf3, from_=0, to=12, increment=1, width=8)
        self.seg_count.set("0")
        self.seg_count.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(lf3, style="Hint.TLabel", text="（0 = 让模型自己定）").grid(
            row=2, column=1, sticky="e", pady=2)

        ttk.Label(lf3, text="镜头").grid(row=2, column=2, sticky="e", padx=(0, 4), pady=2)
        self.camera = ttk.Combobox(lf3, values=CAMERA_KEYS, state="readonly", width=30)
        self.camera.set(CAMERA_KEYS[0])
        self.camera.grid(row=2, column=3, sticky="w", pady=2)
        self.camera.bind("<<ComboboxSelected>>", lambda e: self._update_note())

        self.cam_custom = ttk.Entry(lf3)
        self.cam_custom.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=2)
        ttk.Label(lf3, style="Hint.TLabel", text="镜头补充（可空）").grid(
            row=3, column=0, sticky="w", padx=(8, 4), pady=2)

        self.loop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(lf3, text="首尾帧一致，可无缝循环", variable=self.loop_var).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self.audio_var = tk.BooleanVar(value=True)
        self.chk_audio = ttk.Checkbutton(lf3, text="输出音频三段（style / soundscape / music）",
                                         variable=self.audio_var)
        self.chk_audio.grid(row=4, column=2, columnspan=2, sticky="w", pady=2)

        ttk.Label(lf3, text="主要发声主体").grid(row=5, column=0, sticky="w", padx=(8, 4), pady=2)
        self.primary = ttk.Combobox(lf3, values=["<Subject 1>", "<Subject 2>", "<Subject 3>"],
                                    state="readonly", width=12)
        self.primary.set("<Subject 1>")
        self.primary.grid(row=5, column=1, sticky="w", pady=2)
        ttk.Label(lf3, style="Hint.TLabel", wraplength=300, justify="left",
                  text="对应节点 prompt_primary_audio_ordinal（Subject 1 = 0）").grid(
            row=5, column=2, columnspan=2, sticky="w", pady=2)

        ttk.Label(lf3, text="台词 / 对白（可空）").grid(row=6, column=0, sticky="nw", padx=(8, 4), pady=(6, 2))
        self.dialogue = tk.Text(lf3, height=2, wrap="word", font=UI_FONT, undo=True)
        self.dialogue.grid(row=6, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=(6, 2))

        ttk.Label(lf3, text="声音 / 音乐要求（可空）").grid(row=7, column=0, sticky="nw", padx=(8, 4), pady=2)
        self.sound = tk.Text(lf3, height=2, wrap="word", font=UI_FONT, undo=True)
        self.sound.grid(row=7, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=2)

        ttk.Label(lf3, text="额外禁止项 / 硬性要求").grid(row=8, column=0, sticky="nw", padx=(8, 4), pady=2)
        self.prohib = tk.Text(lf3, height=3, wrap="word", font=UI_FONT, undo=True)
        self.prohib.grid(row=8, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=2)

        self.note_var = tk.StringVar(value="")
        ttk.Label(lf3, style="Hint.TLabel", textvariable=self.note_var, wraplength=460,
                  justify="left").grid(row=9, column=0, columnspan=4, sticky="w", padx=8, pady=(4, 8))

        # ---------- 按钮 ----------
        btns = ttk.Frame(left)
        btns.grid(row=7, column=0, sticky="ew", pady=(2, 12), padx=6)
        self.btn_gen = ttk.Button(btns, text="生成 Input Text   (Ctrl+Enter)",
                                  style="Big.TButton", command=self.on_generate)
        self.btn_gen.pack(side="left")
        self.btn_cancel = ttk.Button(btns, text="取消", command=self.on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=8)
        ttk.Button(btns, text="载入示例", command=self.on_demo).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="清空输入", command=self.on_clear).pack(side="left")

        # ---------- 右：输出 ----------
        right = ttk.Frame(pane)
        pane.add(right, weight=6)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=7)
        right.rowconfigure(3, weight=3)

        head = ttk.Frame(right)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="生成结果 —— 整段复制进 ComfyUI 的 Input Text (Prompt) 节点",
                  style="Section.TLabel").pack(side="left", padx=4, pady=(4, 2))

        out_lf = ttk.Frame(right)
        out_lf.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        out_lf.columnconfigure(0, weight=1)
        out_lf.rowconfigure(0, weight=1)
        self.out = tk.Text(out_lf, wrap="word", font=MONO_FONT, undo=True, spacing3=4)
        self.out.grid(row=0, column=0, sticky="nsew", padx=(4, 0))
        osb = ttk.Scrollbar(out_lf, orient="vertical", command=self.out.yview)
        osb.grid(row=0, column=1, sticky="ns")
        self.out.configure(yscrollcommand=osb.set)

        acts = ttk.Frame(right)
        acts.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(acts, text="复制全部", command=self.on_copy).pack(side="left", padx=(4, 6))
        ttk.Button(acts, text="保存为 .txt", command=self.on_save).pack(side="left", padx=(0, 6))
        ttk.Button(acts, text="重新校验", command=self.on_recheck).pack(side="left", padx=(0, 6))
        self.btn_fix = ttk.Button(acts, text="让 LLM 按报告修正", command=self.on_fix)
        self.btn_fix.pack(side="left", padx=(0, 6))
        ttk.Button(acts, text="清空输出", command=lambda: (txt_set(self.out, ""), self._set_report([]))).pack(
            side="left")

        ttk.Label(right, text="格式校验报告", style="Section.TLabel").grid(
            row=3, column=0, sticky="w", padx=4)
        rep_lf = ttk.Frame(right)
        rep_lf.grid(row=4, column=0, sticky="nsew", pady=(2, 4))
        right.rowconfigure(4, weight=3)
        rep_lf.columnconfigure(0, weight=1)
        rep_lf.rowconfigure(0, weight=1)
        self.rep = tk.Text(rep_lf, height=8, wrap="word", font=UI_FONT, state="disabled",
                           background=CARD)
        self.rep.grid(row=0, column=0, sticky="nsew", padx=(4, 0))
        rsb = ttk.Scrollbar(rep_lf, orient="vertical", command=self.rep.yview)
        rsb.grid(row=0, column=1, sticky="ns")
        self.rep.configure(yscrollcommand=rsb.set)
        self.rep.tag_configure("err", foreground=ERR)
        self.rep.tag_configure("warn", foreground=WARN)
        self.rep.tag_configure("ok", foreground=OK)

        self._update_note()

        # 左栏默认给宽一点（参数控件多），用户还能自己拖分隔条
        self.root.after(200, lambda: self._set_sash(620))

    def _set_sash(self, px: int):
        try:
            if self.pane.winfo_width() > px + 200:
                self.pane.sashpos(0, px)
        except Exception:
            pass

    # ------------------------------------------------------------ API 页
    # -------------------------------------------------------- 长视频页
    def _build_pic_block(self, parent, row0: int):
        """三张参考图输入区（两页共用同一套布局）。返回 (控件列表, 下一个可用行号)。"""
        store = []
        for i in range(3):
            lf = ttk.Labelframe(parent, text="参考图 %d   →   <Picture %d>" % (i + 1, i + 1))
            lf.grid(row=row0 + i, column=0, sticky="nsew", pady=(0, 8), padx=6)
            lf.columnconfigure(1, weight=1)
            lf.columnconfigure(3, weight=2)

            ttk.Label(lf, text="这张图是").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=(8, 2))
            role = ttk.Combobox(lf, values=ROLE_ZH, state="readonly", width=22)
            role.set(ROLE_ZH[0])
            role.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=(8, 2))

            ttk.Label(lf, text="英文短称").grid(row=0, column=2, sticky="e", padx=(0, 4), pady=(8, 2))
            short = ttk.Entry(lf)
            short.grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=(8, 2))

            body = tk.Text(lf, height=4, wrap="word", font=UI_FONT, undo=True)
            body.grid(row=1, column=0, columnspan=4, sticky="nsew", padx=8, pady=(2, 4))
            hint = ("例：年轻男性，短而凌乱的黑发，额前长刘海；深棕色眼睛；自然浓眉；暖白肤色，"
                    "右脸颊有一颗小痣；薄唇；纯白圆领棉T恤。画面里只有他一个人。")
            if i == 1:
                hint = ("例：面部细节参考。要严格保留眼型、眉毛粗细、鼻型、唇型、脸颊小痣、"
                        "自然皮肤质感，不要美颜磨皮。")
            if i == 2:
                hint = "例：场景与光线参考。纯浅米色墙面；正面柔和均匀的室内光；中性色温；半身自拍式构图。"
            ttk.Label(lf, style="Hint.TLabel", text="提示：" + hint, wraplength=460,
                      justify="left").grid(row=2, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))
            store.append({"role": role, "short": short, "body": body})
        return store, row0 + 3

    def _build_long_tab(self):
        pane = ttk.Panedwindow(self.tab_long, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=4, pady=4)

        # ---------- 左：输入 ----------
        left_wrap = ttk.Frame(pane)
        pane.add(left_wrap, weight=5)
        self.lv_scroll = ScrollFrame(left_wrap)
        self.lv_scroll.pack(fill="both", expand=True)
        left = self.lv_scroll.inner
        left.columnconfigure(0, weight=1)

        head = ttk.Frame(left)
        head.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        ttk.Label(head, text="① 三张参考图 —— 和短的三图参考工作流完全一样", style="Section.TLabel"
                  ).pack(side="left")
        ttk.Button(head, text="沿用「生成 Input Text」页的三张图描述",
                   command=self.on_copy_pics_from_gen).pack(side="right")
        ttk.Label(left, style="Hint.TLabel", wraplength=560, justify="left",
                  text="ref_image_0/1/2 的顺序就是 <Picture 1>/<Picture 2>/<Picture 3>。"
                       "循环节点的标签校验（strict_prompt_tags=True）和短工作流一模一样，"
                       "标签写歪一样会 ValueError。").grid(row=1, column=0, sticky="w", padx=6, pady=(0, 6))
        self.lv_pics, nxt = self._build_pic_block(left, 2)

        lf2 = ttk.Labelframe(left, text="② 长视频需求（中文随便写，重点是“整段戏怎么走”而不是“第几秒做什么”）")
        lf2.grid(row=nxt, column=0, sticky="nsew", pady=(4, 8), padx=6)
        lf2.columnconfigure(0, weight=1)
        self.lv_brief = tk.Text(lf2, height=8, wrap="word", font=UI_FONT, undo=True)
        self.lv_brief.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        ttk.Label(lf2, style="Hint.TLabel", wraplength=560, justify="left",
                  text="循环节点会把同一份提示词发给每一段，所以别写“第 3 秒抬头”这种绝对时间；"
                       "写清楚：谁、在哪、整段做什么、情绪怎么演进、哪些东西全程不能变。"
                  ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        lf3 = ttk.Labelframe(left, text="③ 长视频参数")
        lf3.grid(row=nxt + 1, column=0, sticky="nsew", pady=(0, 8), padx=6)
        for c, wgt in ((0, 0), (1, 0), (2, 1), (3, 1)):
            lf3.columnconfigure(c, weight=wgt)

        ttk.Label(lf3, text="总时长（秒）").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=(8, 2))
        self.lv_duration = ttk.Spinbox(lf3, from_=5.0, to=longvideo.LOCAL_DURATION_MAX_HINT,
                                       increment=0.25, width=8)
        self.lv_duration.set("30")
        self.lv_duration.grid(row=0, column=1, sticky="w", pady=(8, 2))
        ttk.Label(lf3, style="Hint.TLabel", text="本地不限时长；官方 CLI 契约是整数 4–15 秒"
                  ).grid(row=0, column=2, columnspan=2, sticky="w", pady=(8, 2))

        ttk.Label(lf3, text="分段方式").grid(row=1, column=0, sticky="w", padx=(8, 4), pady=2)
        self.lv_mode = ttk.Combobox(lf3, values=longvideo.MODE_ZH_LIST, state="readonly", width=30)
        self.lv_mode.set(longvideo.MODE_ZH_LIST[0])
        self.lv_mode.grid(row=1, column=1, columnspan=2, sticky="w", pady=2)

        ttk.Label(lf3, text="分镜数").grid(row=2, column=0, sticky="w", padx=(8, 4), pady=2)
        self.lv_shots = ttk.Spinbox(lf3, from_=0, to=24, increment=1, width=8)
        self.lv_shots.set("0")
        self.lv_shots.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(lf3, style="Hint.TLabel", text="（0 = 让模型自己定）").grid(
            row=2, column=2, sticky="w", pady=2)

        ttk.Label(lf3, text="渲染窗口").grid(row=3, column=0, sticky="w", padx=(8, 4), pady=2)
        self.lv_window = ttk.Spinbox(lf3, from_=longvideo.WINDOW_FRAMES, to=400,
                                     increment=longvideo.WINDOW_STEP, width=8)
        self.lv_window.set(str(longvideo.WINDOW_FRAMES))
        self.lv_window.grid(row=3, column=1, sticky="w", pady=2)
        ttk.Label(lf3, style="Hint.TLabel", text="render_window_frames（≥124，按 17 递增）"
                  ).grid(row=3, column=2, columnspan=2, sticky="w", pady=2)

        ttk.Label(lf3, text="上下文帧").grid(row=4, column=0, sticky="w", padx=(8, 4), pady=2)
        self.lv_context = ttk.Combobox(lf3, values=[str(x) for x in longvideo.CONTEXT_OPTIONS],
                                       state="readonly", width=8)
        self.lv_context.set(str(longvideo.DEFAULT_CONTEXT))
        self.lv_context.grid(row=4, column=1, sticky="w", pady=2)
        ttk.Label(lf3, style="Hint.TLabel", text="context_frames（重叠接缝，越大越连贯越慢）"
                  ).grid(row=4, column=2, columnspan=2, sticky="w", pady=2)

        ttk.Label(lf3, text="镜头").grid(row=5, column=0, sticky="w", padx=(8, 4), pady=2)
        self.lv_camera = ttk.Combobox(lf3, values=CAMERA_KEYS, state="readonly", width=30)
        self.lv_camera.set(CAMERA_KEYS[0])
        self.lv_camera.grid(row=5, column=1, columnspan=2, sticky="w", pady=2)

        ttk.Label(lf3, style="Hint.TLabel", text="镜头补充（可空）").grid(
            row=6, column=0, sticky="w", padx=(8, 4), pady=2)
        self.lv_cam_custom = ttk.Entry(lf3)
        self.lv_cam_custom.grid(row=6, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=2)

        self.lv_loop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(lf3, text="首尾帧一致，可无缝循环", variable=self.lv_loop_var).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self.lv_audio_var = tk.BooleanVar(value=True)
        self.lv_chk_audio = ttk.Checkbutton(lf3, text="输出声音两段（soundscape / music）",
                                            variable=self.lv_audio_var)
        self.lv_chk_audio.grid(row=7, column=2, columnspan=2, sticky="w", pady=2)

        ttk.Label(lf3, text="主要发声主体").grid(row=8, column=0, sticky="w", padx=(8, 4), pady=2)
        self.lv_primary = ttk.Combobox(lf3, values=["<Subject 1>", "<Subject 2>", "<Subject 3>"],
                                       state="readonly", width=12)
        self.lv_primary.set("<Subject 1>")
        self.lv_primary.grid(row=8, column=1, sticky="w", pady=2)
        ttk.Label(lf3, style="Hint.TLabel", wraplength=300, justify="left",
                  text="对应节点 prompt_primary_audio_ordinal（Subject 1 = 0）").grid(
            row=8, column=2, columnspan=2, sticky="w", pady=2)

        ttk.Label(lf3, text="台词 / 对白（可空）").grid(row=9, column=0, sticky="nw", padx=(8, 4), pady=(6, 2))
        self.lv_dialogue = tk.Text(lf3, height=2, wrap="word", font=UI_FONT, undo=True)
        self.lv_dialogue.grid(row=9, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=(6, 2))

        ttk.Label(lf3, text="声音 / 音乐要求（可空）").grid(row=10, column=0, sticky="nw", padx=(8, 4), pady=2)
        self.lv_sound = tk.Text(lf3, height=2, wrap="word", font=UI_FONT, undo=True)
        self.lv_sound.grid(row=10, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=2)

        ttk.Label(lf3, text="额外禁止项 / 硬性要求").grid(row=11, column=0, sticky="nw", padx=(8, 4), pady=2)
        self.lv_prohib = tk.Text(lf3, height=3, wrap="word", font=UI_FONT, undo=True)
        self.lv_prohib.grid(row=11, column=1, columnspan=3, sticky="ew", padx=(0, 8), pady=2)

        # ---------- 分段预算 ----------
        lf4 = ttk.Labelframe(left, text="④ 分段预算（按上面的时长 / 窗口 / 上下文实时算）")
        lf4.grid(row=nxt + 2, column=0, sticky="nsew", pady=(0, 8), padx=6)
        lf4.columnconfigure(0, weight=1)
        self.lv_plan = tk.Text(lf4, height=5, wrap="word", font=UI_FONT, state="disabled")
        self.lv_plan.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        # ---------- 字数预算 ----------
        lf5 = ttk.Labelframe(left, text="⑤ 提示词字数预算（官方 CLI 提交上限 %d 字符）"
                             % longvideo.SUBMISSION_CHAR_LIMIT)
        lf5.grid(row=nxt + 3, column=0, sticky="nsew", pady=(0, 8), padx=6)
        lf5.columnconfigure(0, weight=1)
        self.lv_budget_var = tk.StringVar(value="")
        self.lv_budget_lbl = ttk.Label(lf5, textvariable=self.lv_budget_var, style="Hint.TLabel",
                                       wraplength=560, justify="left")
        self.lv_budget_lbl.grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        self.lv_budget_bar = ttk.Progressbar(lf5, mode="determinate",
                                             maximum=longvideo.SUBMISSION_CHAR_LIMIT, length=320)
        self.lv_budget_bar.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        # ---------- 按钮 ----------
        btns = ttk.Frame(left)
        btns.grid(row=nxt + 4, column=0, sticky="ew", pady=(2, 12), padx=6)
        self.btn_gen_lv = ttk.Button(btns, text="生成长视频提示词   (Ctrl+Shift+Enter)",
                                     style="Big.TButton", command=lambda: self.on_generate("lv"))
        self.btn_gen_lv.pack(side="left")
        self.btn_cancel_lv = ttk.Button(btns, text="取消", state="disabled",
                                        command=self.on_cancel)
        self.btn_cancel_lv.pack(side="left", padx=8)
        ttk.Button(btns, text="载入示例", command=lambda: self.on_demo("lv")).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="清空输入", command=lambda: self.on_clear("lv")).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="看长视频规则", command=self.on_show_lv_rules).pack(side="left")

        # ---------- 右：输出 ----------
        right = ttk.Frame(pane)
        pane.add(right, weight=6)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=7)
        right.rowconfigure(4, weight=3)

        rhead = ttk.Frame(right)
        rhead.grid(row=0, column=0, sticky="ew")
        ttk.Label(rhead, text="生成结果 —— 整段复制进循环节点的 global_prompt",
                  style="Section.TLabel").pack(side="left", padx=4, pady=(4, 2))

        out_lf = ttk.Frame(right)
        out_lf.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        out_lf.columnconfigure(0, weight=1)
        out_lf.rowconfigure(0, weight=1)
        self.lv_out = tk.Text(out_lf, wrap="word", font=MONO_FONT, undo=True, spacing3=4)
        self.lv_out.grid(row=0, column=0, sticky="nsew", padx=(4, 0))
        osb = ttk.Scrollbar(out_lf, orient="vertical", command=self.lv_out.yview)
        osb.grid(row=0, column=1, sticky="ns")
        self.lv_out.configure(yscrollcommand=osb.set)
        self.lv_out.bind("<KeyRelease>", lambda e: self._refresh_lv_budget())

        acts = ttk.Frame(right)
        acts.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(acts, text="复制全部", command=lambda: self.on_copy("lv")).pack(side="left", padx=(4, 6))
        ttk.Button(acts, text="保存为 .txt", command=lambda: self.on_save("lv")).pack(side="left", padx=(0, 6))
        ttk.Button(acts, text="重新校验", command=lambda: self.on_recheck("lv")).pack(side="left", padx=(0, 6))
        self.btn_fix_lv = ttk.Button(acts, text="让 LLM 按报告修正",
                                     command=lambda: self.on_fix("lv"))
        self.btn_fix_lv.pack(side="left", padx=(0, 6))
        ttk.Button(acts, text="清空输出",
                   command=lambda: (txt_set(self.lv_out, ""), self._set_report([], "lv"),
                                    self._refresh_lv_budget())).pack(side="left")

        ttk.Label(right, text="格式校验报告", style="Section.TLabel").grid(
            row=3, column=0, sticky="w", padx=4)
        rep_lf = ttk.Frame(right)
        rep_lf.grid(row=4, column=0, sticky="nsew", pady=(2, 4))
        rep_lf.columnconfigure(0, weight=1)
        rep_lf.rowconfigure(0, weight=1)
        self.lv_rep = tk.Text(rep_lf, height=8, wrap="word", font=UI_FONT, state="disabled",
                              background=CARD)
        self.lv_rep.grid(row=0, column=0, sticky="nsew", padx=(4, 0))
        rsb = ttk.Scrollbar(rep_lf, orient="vertical", command=self.lv_rep.yview)
        rsb.grid(row=0, column=1, sticky="ns")
        self.lv_rep.configure(yscrollcommand=rsb.set)
        self.lv_rep.tag_configure("err", foreground=ERR)
        self.lv_rep.tag_configure("warn", foreground=WARN)
        self.lv_rep.tag_configure("ok", foreground=OK)

        self._refresh_lv_panels()

        # 时长 / 窗口 / 上下文一变，分段预算立刻跟着重算
        for widget in (self.lv_duration, self.lv_window):
            widget.bind("<KeyRelease>", lambda e: self._refresh_lv_panels())
            widget.bind("<ButtonRelease-1>", lambda e: self._refresh_lv_panels())
        self.lv_context.bind("<<ComboboxSelected>>", lambda e: self._refresh_lv_panels())

    # -------------------------------------------------------- 长视频页辅助
    def on_copy_pics_from_gen(self):
        """把「生成 Input Text」页的三张图描述抄过来，省得重敲一遍。"""
        src = self.collect_input("gen").pictures
        n = 0
        for x, p in zip(self.lv_pics, src):
            x["role"].set(prompt_spec.ROLES[p.role]["zh"])
            x["short"].delete(0, "end")
            x["short"].insert(0, p.short_name)
            if p.description.strip():
                txt_set(x["body"], p.description)
                n += 1
        self.set_status("已从生成页抄来 %d 张图的描述。" % n)

    def on_show_lv_rules(self):
        w = tk.Toplevel(self.root)
        w.title("长视频提示词规则")
        w.geometry("880x620")
        t = tk.Text(w, wrap="word", font=UI_FONT, padx=14, pady=12)
        t.pack(fill="both", expand=True)
        t.insert("1.0", longvideo.rules_zh())
        t.configure(state="disabled")

    def _lv_params(self):
        try:
            dur = float(self.lv_duration.get())
        except Exception:
            dur = 30.0
        try:
            win = int(float(self.lv_window.get()))
        except Exception:
            win = longvideo.WINDOW_FRAMES
        try:
            ctx = int(float(self.lv_context.get()))
        except Exception:
            ctx = longvideo.DEFAULT_CONTEXT
        return dur, win, ctx

    def _refresh_lv_panels(self):
        dur, win, ctx = self._lv_params()
        txt_set_ro(self.lv_plan, "\n".join(longvideo.plan_lines(dur, win, ctx)))
        self._refresh_lv_budget()

    def _refresh_lv_budget(self):
        text = txt_get(self.lv_out)
        b = longvideo.budget_status(text)
        if b["over"]:
            self.lv_budget_var.set("当前输出 %d 字符，超出官方上限 %d 个字符 —— 官方 API 路径会判"
                                   "超预算（本地 ComfyUI 不拦，节点也从不截断）。建议砍形容词，"
                                   "别砍身份/镜头/连贯性事实。" % (b["chars"], b["over"]))
            self.lv_budget_lbl.configure(style="Warn.TLabel")
        else:
            self.lv_budget_var.set("当前输出 %d / %d 字符，还剩 %d 字符。%s"
                                   % (b["chars"], b["limit"], b["left"],
                                      longvideo.SUBMISSION_LIMIT_NOTE_ZH))
            self.lv_budget_lbl.configure(style="Hint.TLabel")
        try:
            self.lv_budget_bar.configure(value=min(b["chars"], b["limit"]))
        except Exception:
            pass

    def _build_api_tab(self):
        f = ttk.Frame(self.tab_api, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        rows = [
            ("接口地址 base_url", "api_base", "默认硅基流动：https://api.siliconflow.cn/v1"),
            ("API Key", "api_key", "硅基流动控制台 → API 密钥。默认明文保存在本机配置文件里。"),
        ]
        for r, (label, key, hint) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=8, padx=(0, 10))
            e = ttk.Entry(f, show="*" if key == "api_key" else "")
            e.grid(row=r, column=1, sticky="ew", pady=8)
            setattr(self, "v_" + key, e)
            ttk.Label(f, style="Hint.TLabel", text=hint, wraplength=760,
                      justify="left").grid(row=r, column=2, sticky="w", padx=(10, 0), pady=8)

        ttk.Label(f, text="模型 model").grid(row=2, column=0, sticky="w", pady=8, padx=(0, 10))
        mrow = ttk.Frame(f)
        mrow.grid(row=2, column=1, sticky="ew", pady=8)
        self.v_model = ttk.Combobox(mrow, values=llm_api.MODEL_SUGGESTIONS)
        self.v_model.pack(side="left", fill="x", expand=True)
        self.btn_models = ttk.Button(mrow, text="拉取模型列表", command=self.on_list_models)
        self.btn_models.pack(side="left", padx=(8, 0))
        ttk.Label(f, style="Hint.TLabel", wraplength=760, justify="left",
                  text="必须是没有识图能力的纯文本模型也无所谓——本工具本来就不传图。"
                       "推荐 deepseek-ai/DeepSeek-V3：中文理解好、便宜、格式听话。"
                  ).grid(row=2, column=2, sticky="w", padx=(10, 0), pady=8)

        nums = [("temperature", "temperature", 0.0, 2.0, 0.1, "越低越听话，建议 0.3~0.5"),
                ("max_tokens", "max_tokens", 512, 32768, 256, "整段约 600~900 token，4096 够用"),
                ("timeout", "timeout", 10, 900, 10, "秒。长输出建议 300")]
        for r, (label, key, lo, hi, inc, hint) in enumerate(nums, start=3):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=6, padx=(0, 10))
            sp = ttk.Spinbox(f, from_=lo, to=hi, increment=inc, width=10)
            sp.grid(row=r, column=1, sticky="w", pady=6)
            setattr(self, "v_" + key, sp)
            ttk.Label(f, style="Hint.TLabel", text=hint).grid(row=r, column=2, sticky="w",
                                                               padx=(10, 0), pady=6)

        ttk.Label(f, text="额外请求参数 (JSON)").grid(row=6, column=0, sticky="w", pady=6, padx=(0, 10))
        self.v_extra = ttk.Entry(f)
        self.v_extra.grid(row=6, column=1, sticky="ew", pady=6)
        ttk.Label(f, style="Hint.TLabel", wraplength=760, justify="left",
                  text='可空。例如 {"top_p": 0.9} 或 {"enable_thinking": false}').grid(
            row=6, column=2, sticky="w", padx=(10, 0), pady=6)

        opt = ttk.Frame(f)
        opt.grid(row=7, column=0, columnspan=3, sticky="w", pady=10)
        self.v_remember = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="记住 API Key（明文写入本机配置文件）",
                        variable=self.v_remember).pack(side="left")
        self.v_verify = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="校验 SSL 证书（关掉只用于自签代理调试）",
                        variable=self.v_verify).pack(side="left", padx=(20, 0))
        self.v_showkey = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="显示密钥", variable=self.v_showkey,
                        command=self._toggle_key).pack(side="left", padx=(20, 0))

        btns = ttk.Frame(f)
        btns.grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 10))
        ttk.Button(btns, text="保存设置", command=self.on_save_settings).pack(side="left")
        ttk.Button(btns, text="测试连接（发一次 1 token 请求）",
                   command=self.on_test).pack(side="left", padx=(10, 0))

        ttk.Label(f, style="Hint.TLabel",
                  text="配置文件：" + cfgstore.config_path(), wraplength=980,
                  justify="left").grid(row=9, column=0, columnspan=3, sticky="w")

        # 载入当前值
        c = self.cfg
        self.v_api_base.insert(0, c.get("api_base") or llm_api.DEFAULT_BASE)
        self.v_api_key.insert(0, c.get("api_key") or "")
        self.v_model.set(c.get("model") or llm_api.DEFAULT_MODEL)
        self.v_temperature.set(str(c.get("temperature", llm_api.DEFAULT_TEMPERATURE)))
        self.v_max_tokens.set(str(int(c.get("max_tokens", llm_api.DEFAULT_MAX_TOKENS))))
        self.v_timeout.set(str(int(c.get("timeout", llm_api.DEFAULT_TIMEOUT))))
        self.v_extra.insert(0, c.get("extra_payload") or "")
        self.v_remember.set(bool(c.get("remember_key", True)))
        self.v_verify.set(bool(c.get("verify_ssl", True)))

    def _toggle_key(self):
        self.v_api_key.configure(show="" if self.v_showkey.get() else "*")

    # ------------------------------------------------------------ 规则页
    def _build_rule_tab(self):
        f = ttk.Frame(self.tab_rule, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)
        f.rowconfigure(5, weight=1)

        ttk.Label(f, text="追加自定义硬规则（会拼到 system prompt 最后，用来治 LLM 的老毛病）",
                  style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.extra_rules = tk.Text(f, height=4, wrap="word", font=UI_FONT, undo=True)
        self.extra_rules.grid(row=1, column=0, sticky="ew")
        ttk.Label(f, style="Hint.TLabel", wraplength=1000, justify="left",
                  text='例：所有分段必须是 4 段；每段不超过 3 句；overall_soundscape 里必须提到衣物摩擦声；'
                       '禁止使用 \"cinematic\" 一词。').grid(row=2, column=0, sticky="w", pady=(2, 8))

        bar = ttk.Frame(f)
        bar.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(bar, text="刷新下方预览", command=self._refresh_prompt_preview).pack(side="left")
        ttk.Button(bar, text="复制系统提示词", command=self.on_copy_prompt).pack(side="left", padx=8)
        ttk.Button(bar, text="查看官方范例（工作流里的原文）",
                   command=self.on_show_example).pack(side="left")

        ttk.Label(f, text="当前实际发送给 LLM 的 system prompt（只读预览）",
                  style="Section.TLabel").grid(row=4, column=0, sticky="w", pady=(0, 4))
        self.preview = tk.Text(f, wrap="word", font=MONO_FONT, state="disabled",
                               background="#f7f8fa")
        self.preview.grid(row=5, column=0, sticky="nsew")
        psb = ttk.Scrollbar(f, orient="vertical", command=self.preview.yview)
        psb.grid(row=5, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=psb.set)
        self._refresh_prompt_preview()

    def _refresh_prompt_preview(self):
        inp = self.collect_input()
        text = prompt_spec.build_system_prompt(inp.duration, inp.include_audio, inp.extra_rules,
                                               scheme=inp.format_scheme)
        self.preview.configure(state="normal")
        txt_set(self.preview, text)
        self.preview.configure(state="disabled")

    # ------------------------------------------------------------ 官方 Skill 页
    def _build_skill_tab(self):
        f = self.tab_skill
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=3)
        f.rowconfigure(2, weight=2)

        top = ttk.Labelframe(f, text="官方 MiniMax H3 Prompt Skill（h3-prompt-writing）", padding=8)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        top.columnconfigure(0, weight=1)
        ttk.Label(top, style="Hint.TLabel", wraplength=1000, justify="left",
                  text="来源：" + h3_skill.SKILL_HOME).grid(row=0, column=0, sticky="w")
        self.skill_status = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.skill_status, wraplength=1000,
                  justify="left").grid(row=1, column=0, sticky="w", pady=(2, 6))

        brow = ttk.Frame(top)
        brow.grid(row=2, column=0, sticky="w")
        self.btn_skill_fetch = ttk.Button(brow, text="从 GitHub 拉取官方指南",
                                          command=self.on_fetch_skill)
        self.btn_skill_fetch.pack(side="left")
        ttk.Button(brow, text="刷新", command=self._refresh_skill_tab).pack(side="left", padx=(8, 0))
        ttk.Button(brow, text="打开缓存目录", command=self.on_open_skill_dir).pack(
            side="left", padx=(8, 0))
        ttk.Button(brow, text="官方仓库主页", command=self.on_open_skill_repo).pack(
            side="left", padx=(8, 0))

        ttk.Label(top, style="Warn.TLabel", wraplength=1000, justify="left",
                  text="版权提醒：MiniMax-AI/MiniMax-H3 仓库没有 LICENSE 文件（默认保留所有权利）。"
                       "拉下来的原文只缓存在本机 %s，供你自己查阅，"
                       "请不要再分发。本程序发给 LLM 的提示词是按官方规则重写的自有实现，不含原文。"
                       % h3_skill.cache_dir_path()
                  ).grid(row=3, column=0, sticky="w", pady=(6, 0))

        mid = ttk.Labelframe(f, text="官方指南原文（只读）", padding=6)
        mid.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(1, weight=1)
        drow = ttk.Frame(mid)
        drow.grid(row=0, column=0, sticky="ew")
        ttk.Label(drow, text="文档：").pack(side="left")
        self.skill_doc = ttk.Combobox(drow, values=[n for n, _u, _d in h3_skill.SKILL_FILES],
                                      state="readonly", width=16)
        self.skill_doc.set(h3_skill.SKILL_FILES[1][0])     # 默认 ref-en.txt：Ref2VA 就用这份
        self.skill_doc.pack(side="left", padx=(4, 8))
        self.skill_doc.bind("<<ComboboxSelected>>", lambda e: self._show_skill_doc())
        self.skill_doc_info = ttk.Label(drow, style="Hint.TLabel", text="", wraplength=760,
                                        justify="left")
        self.skill_doc_info.pack(side="left", fill="x", expand=True)
        self.skill_text = tk.Text(mid, wrap="word", font=MONO_FONT)
        self.skill_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        ssb = ttk.Scrollbar(mid, orient="vertical", command=self.skill_text.yview)
        ssb.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.skill_text.configure(yscrollcommand=ssb.set)

        bot = ttk.Labelframe(f, text="本程序按官方规则实现的速查表（真正发给 LLM 的就是这套）",
                             padding=6)
        bot.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))
        bot.columnconfigure(0, weight=1)
        bot.rowconfigure(0, weight=1)
        self.skill_rules = tk.Text(bot, wrap="word", font=MONO_FONT)
        self.skill_rules.grid(row=0, column=0, sticky="nsew")
        rsb = ttk.Scrollbar(bot, orient="vertical", command=self.skill_rules.yview)
        rsb.grid(row=0, column=1, sticky="ns")
        self.skill_rules.configure(yscrollcommand=rsb.set)
        self.skill_rules.insert("1.0", h3_skill.RULES_ZH)
        self.skill_rules.configure(state="disabled")

        self._refresh_skill_tab()

    def _refresh_skill_tab(self):
        self.skill_status.set(h3_skill.cache_summary())
        self._show_skill_doc()

    def _show_skill_doc(self):
        name = self.skill_doc.get()
        desc = dict((n, d) for n, _u, d in h3_skill.SKILL_FILES).get(name, "")
        text = h3_skill.read_cached(name)
        self.skill_text.configure(state="normal")
        if text is None:
            self.skill_doc_info.configure(text=desc)
            txt_set(self.skill_text,
                    "本机还没有缓存这份原文。\n\n"
                    "点上面的「从 GitHub 拉取官方指南」下载（三份共约 42 KB），\n"
                    "或者用浏览器直接打开：\n" + h3_skill.SKILL_HOME)
        else:
            self.skill_doc_info.configure(text="%s ｜ %d 字符" % (desc, len(text)))
            txt_set(self.skill_text, text)
        self.skill_text.configure(state="disabled")

    def on_fetch_skill(self):
        if self.busy:
            messagebox.showinfo("正在忙", "等当前请求结束再拉。")
            return
        s = self._collect_settings()
        self.btn_skill_fetch.configure(state="disabled")
        self.set_status("正在从 GitHub 拉取官方指南…")
        threading.Thread(target=self._skill_worker, args=(bool(s["verify_ssl"]),),
                         daemon=True).start()

    def _skill_worker(self, verify_ssl):
        t0 = time.time()
        try:
            meta = h3_skill.fetch(verify_ssl=verify_ssl, timeout=60)
            self.q.put(("skill", None, meta, time.time() - t0))
        except h3_skill.SkillFetchError as e:
            self.q.put(("skillerr", None, str(e), None))
        except Exception as e:  # noqa: BLE001
            self.q.put(("skillerr", None, "%s\n%s" % (e, traceback.format_exc(limit=3)), None))

    def on_open_skill_dir(self):
        d = h3_skill.cache_dir()
        # 跨平台打开目录：Windows 资源管理器 / macOS 访达。
        # 原来直接 os.startfile —— macOS 上没这个函数，只能靠 except 兜底弹路径。
        if not mac_compat.reveal_in_file_manager(d):
            messagebox.showinfo("缓存目录", d)

    def on_open_skill_repo(self):
        import webbrowser
        webbrowser.open(h3_skill.SKILL_HOME)

    # ------------------------------------------------------------ 菜单栏
    def _build_menu(self):
        """macOS 原生菜单栏（Windows 上不建，界面保持原样）。

        Tk 在 macOS 上不会自动提供 ⌘Q：得自己挂菜单栏，而且名字叫 "apple" 的
        那个子菜单会被系统当作「应用菜单」（标题显示为应用名）。
        """
        if not IS_MAC:
            return
        try:
            menubar = tk.Menu(self.root)
            app_menu = tk.Menu(menubar, name="apple", tearoff=0)
            menubar.add_cascade(menu=app_menu)
            app_menu.add_command(label="关于 MiniMax H3 提示词生成器",
                                 command=self.on_about)
            app_menu.add_separator()
            app_menu.add_command(label="退出", accelerator="Command-Q",
                                 command=self._on_close)
            self.root.configure(menu=menubar)
        except Exception:
            pass          # 菜单是锦上添花，建不起来也不能影响主界面

    def on_about(self):
        messagebox.showinfo(
            "关于",
            "%s\n\n纯 Python 标准库 + tkinter，运行时零第三方依赖。\n配置文件：%s"
            % (APP_TITLE, cfgstore.config_path()))

    # ------------------------------------------------------------ 快捷键
    def _bind_keys(self):
        def page_now():
            try:
                return "lv" if self.nb.index("current") == self.nb.index(self.tab_long) else "gen"
            except Exception:
                return "gen"

        # 在没有取输入焦点的控件上才响应（避免抢 Text 里的回车）。
        # Windows/Linux 只用 Ctrl；macOS 再补一套 ⌘（Control 也留着，习惯 Ctrl 的照样能按）。
        # <Command-*> 只在 macOS 上是合法事件写法，所以必须平台分支。
        for p in (["Control"] + (["Command"] if IS_MAC else [])):
            self.root.bind_all("<%s-Return>" % p, lambda e: self.on_generate(page_now()))
            self.root.bind_all("<%s-Shift-Return>" % p, lambda e: self.on_generate("lv"))
            self.root.bind_all("<%s-Shift-C>" % p, lambda e: self.on_copy(page_now()))
        if IS_MAC:
            # 兜底：万一菜单项的 ⌘Q 没被 Tk 接上，这条也保证能退出（_on_close 幂等）
            self.root.bind_all("<Command-q>", lambda e: self._on_close())

    # ------------------------------------------------------------ 数据
    def collect_input(self, page: str = "gen") -> prompt_spec.OperatorInput:
        """把界面上的输入收成一个 OperatorInput。

        page="gen" → 「生成 Input Text」页；page="lv" → 「长视频提示词」页。
        两页控件名不同，靠 PAGE_WIDGETS 映射；取不到的键退回中性默认值。
        """
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]

        def w(k):
            name = W.get(k)
            return getattr(self, name) if name else None

        pics = []
        for x in (w("pics") or []):
            zh = x["role"].get()
            role = ROLE_KEYS[ROLE_ZH.index(zh)] if zh in ROLE_ZH else "custom"
            pics.append(prompt_spec.Picture(role=role,
                                            short_name=x["short"].get().strip(),
                                            description=txt_get(x["body"])))
        if not pics:
            pics = [prompt_spec.Picture(), prompt_spec.Picture(), prompt_spec.Picture()]

        long_video = (page == "lv")
        style_w = w("seg_style")
        if long_video:
            style_key = "shot"        # 长视频固定 [Shot N]，而且不许带时间码
        elif style_w is not None and style_w.get() in STYLE_ZH:
            style_key = STYLE_KEYS[STYLE_ZH.index(style_w.get())]
        elif self._current_scheme() == "official":
            style_key = "shot"        # 官方六段固定用 [Shot N] 记法，不受下拉框影响
        else:
            style_key = "time"

        dur_w = w("duration")
        try:
            duration = float(dur_w.get()) if dur_w is not None else 6.0
        except Exception:
            duration = 6.0
        if long_video:
            duration = max(0.5, min(longvideo.LOCAL_DURATION_MAX_HINT, duration))
        else:
            duration = max(0.5, min(60.0, duration))

        cnt_w = w("seg_count")
        try:
            seg_count = int(float(cnt_w.get())) if cnt_w is not None else 0
        except Exception:
            seg_count = 0
        pri_w = w("primary")
        primary_txt = pri_w.get() if pri_w is not None else "<Subject 1>"
        try:
            primary = int(primary_txt.replace("<Subject", "").replace(">", "").strip()) - 1
        except Exception:
            primary = 0
        loop_w, audio_w, mode_w = w("loop"), w("audio"), w("mode")
        mode_val = mode_w.get() if mode_w is not None else ""
        return prompt_spec.OperatorInput(
            pictures=pics,
            brief=txt_get(w("brief")),
            duration=duration,
            segment_style=style_key,
            segment_count=seg_count,
            camera=w("camera").get(),
            camera_custom=w("cam_custom").get().strip(),
            loop=bool(loop_w.get()) if loop_w is not None else True,
            include_audio=bool(audio_w.get()) if audio_w is not None else True,
            sound_brief=txt_get(w("sound")),
            dialogue=txt_get(w("dialogue")),
            prohibitions=txt_get(w("prohib")),
            primary_audio_subject=primary,
            extra_rules=txt_get(self.extra_rules),
            format_scheme=self._current_scheme(),
            long_video=long_video,
            lv_mode=longvideo.MODE_ZH2KEY.get(mode_val, longvideo.MODE_CONTINUOUS),
            lv_window=int(self._to_float(w("window").get() if w("window") else None,
                                         longvideo.WINDOW_FRAMES)),
            lv_context=int(self._to_float(w("context").get() if w("context") else None,
                                          longvideo.DEFAULT_CONTEXT)),
            page=page,
        )

    def _collect_settings(self) -> dict:
        return {
            "api_base": self.v_api_base.get().strip() or llm_api.DEFAULT_BASE,
            "api_key": self.v_api_key.get().strip(),
            "model": self.v_model.get().strip() or llm_api.DEFAULT_MODEL,
            "temperature": self._to_float(self.v_temperature.get(), llm_api.DEFAULT_TEMPERATURE),
            "max_tokens": int(self._to_float(self.v_max_tokens.get(), llm_api.DEFAULT_MAX_TOKENS)),
            "timeout": int(self._to_float(self.v_timeout.get(), llm_api.DEFAULT_TIMEOUT)),
            "extra_payload": self.v_extra.get().strip(),
            "remember_key": bool(self.v_remember.get()),
            "verify_ssl": bool(self.v_verify.get()),
        }

    @staticmethod
    def _to_float(s, default):
        try:
            return float(s)
        except Exception:
            return default

    def _work_snapshot(self) -> dict:
        return {
            "pictures": [{"role": w["role"].get(), "short": w["short"].get(),
                          "body": txt_get(w["body"])}
                         for w in self.pic_widgets],
            "brief": txt_get(self.brief),
            "scheme": self.scheme.get(),
            "duration": self.duration.get(),
            "seg_style": self.seg_style.get(),
            "seg_count": self.seg_count.get(),
            "camera": self.camera.get(),
            "cam_custom": self.cam_custom.get(),
            "loop": bool(self.loop_var.get()),
            "audio": bool(self.audio_var.get()),
            "primary": self.primary.get(),
            "dialogue": txt_get(self.dialogue),
            "sound": txt_get(self.sound),
            "prohib": txt_get(self.prohib),
            "extra_rules": txt_get(self.extra_rules),
            "output": txt_get(self.out),
            # 上次停在哪个页面
            "active_page": ("lv" if self.nb.index("current") == self.nb.index(self.tab_long)
                            else "gen"),
            # 长视频页单独存一份，老存档没有这个键也不影响
            "lv": {
                "pictures": [{"role": x["role"].get(), "short": x["short"].get(),
                              "body": txt_get(x["body"])} for x in self.lv_pics],
                "brief": txt_get(self.lv_brief),
                "duration": self.lv_duration.get(),
                "mode": self.lv_mode.get(),
                "shots": self.lv_shots.get(),
                "window": self.lv_window.get(),
                "context": self.lv_context.get(),
                "camera": self.lv_camera.get(),
                "cam_custom": self.lv_cam_custom.get(),
                "loop": bool(self.lv_loop_var.get()),
                "audio": bool(self.lv_audio_var.get()),
                "primary": self.lv_primary.get(),
                "dialogue": txt_get(self.lv_dialogue),
                "sound": txt_get(self.lv_sound),
                "prohib": txt_get(self.lv_prohib),
                "output": txt_get(self.lv_out),
            },
        }

    def _restore_work(self):
        w = self.cfg.get("work") or {}
        if not w:
            return
        for i, pw in enumerate(w.get("pictures") or []):
            if i >= len(self.pic_widgets):
                break
            dst = self.pic_widgets[i]
            if pw.get("role") in ROLE_ZH:
                dst["role"].set(pw["role"])
            dst["short"].delete(0, "end")
            dst["short"].insert(0, pw.get("short", ""))
            txt_set(dst["body"], pw.get("body", ""))
        txt_set(self.brief, w.get("brief", ""))
        if w.get("scheme") in SCHEME_ZH_LIST:
            self.scheme.set(w["scheme"])
        elif "overall_style:" in (w.get("output") or ""):
            # 旧存档没有 scheme 字段：按已生成的输出反推，免得老内容被按新格式判错
            self.scheme.set(h3_skill.SCHEMES["workflow5"]["zh"])
        if w.get("duration"):
            try:
                self.duration.set("%g" % float(w["duration"]))
            except Exception:
                self.duration.set(str(w["duration"]))
        if w.get("seg_style") in STYLE_ZH:
            self.seg_style.set(w["seg_style"])
        if w.get("seg_count"):
            self.seg_count.set(str(w["seg_count"]))
        if w.get("camera") in CAMERA_KEYS:
            self.camera.set(w["camera"])
        self.cam_custom.delete(0, "end")
        self.cam_custom.insert(0, w.get("cam_custom", ""))
        self.loop_var.set(bool(w.get("loop", True)))
        self.audio_var.set(bool(w.get("audio", True)))
        if w.get("primary") in ("<Subject 1>", "<Subject 2>", "<Subject 3>"):
            self.primary.set(w["primary"])
        txt_set(self.dialogue, w.get("dialogue", ""))
        txt_set(self.sound, w.get("sound", ""))
        txt_set(self.prohib, w.get("prohib", ""))
        txt_set(self.extra_rules, w.get("extra_rules", ""))
        if w.get("output"):
            txt_set(self.out, w["output"])
            self.current_text = w["output"]
            self.on_recheck()

        lv = w.get("lv") or {}
        if lv:
            for i, pw in enumerate(lv.get("pictures") or []):
                if i >= len(self.lv_pics):
                    break
                dst = self.lv_pics[i]
                if pw.get("role") in ROLE_ZH:
                    dst["role"].set(pw["role"])
                dst["short"].delete(0, "end")
                dst["short"].insert(0, pw.get("short", ""))
                txt_set(dst["body"], pw.get("body", ""))
            txt_set(self.lv_brief, lv.get("brief", ""))
            if lv.get("duration"):
                try:
                    self.lv_duration.set("%g" % float(lv["duration"]))
                except Exception:
                    self.lv_duration.set(str(lv["duration"]))
            for key, widget in (("shots", self.lv_shots), ("window", self.lv_window),
                                ("context", self.lv_context)):
                if lv.get(key) is not None:
                    widget.set(str(lv[key]))
            if lv.get("mode") in longvideo.MODE_ZH_LIST:
                self.lv_mode.set(lv["mode"])
            if lv.get("camera") in CAMERA_KEYS:
                self.lv_camera.set(lv["camera"])
            self.lv_cam_custom.delete(0, "end")
            self.lv_cam_custom.insert(0, lv.get("cam_custom", ""))
            self.lv_loop_var.set(bool(lv.get("loop", True)))
            self.lv_audio_var.set(bool(lv.get("audio", True)))
            if lv.get("primary") in ("<Subject 1>", "<Subject 2>", "<Subject 3>"):
                self.lv_primary.set(lv["primary"])
            txt_set(self.lv_dialogue, lv.get("dialogue", ""))
            txt_set(self.lv_sound, lv.get("sound", ""))
            txt_set(self.lv_prohib, lv.get("prohib", ""))
            if lv.get("output"):
                txt_set(self.lv_out, lv["output"])
                self.on_recheck("lv")
        self._refresh_lv_panels()
        self._on_scheme_change(quiet=True)
        if w.get("active_page") == "lv":
            try:
                self.nb.select(self.tab_long)
            except Exception:
                pass

    # ------------------------------------------------------------ 格式方案
    def _current_scheme(self) -> str:
        return SCHEME_ZH2KEY.get(self.scheme.get(), h3_skill.DEFAULT_SCHEME)

    def _on_scheme_change(self, quiet: bool = False):
        """官方六段用 [Shot N] At MM:SS.mmm，没有“分段样式”这个选项，所以直接锁掉。"""
        official = self._current_scheme() == "official"
        if getattr(self, "badge_var", None) is not None:
            self.badge_var.set("输出格式：%s" % ("官方六段" if official else "工作流五段"))
        if official:
            cur = self.seg_style.get()
            if cur in STYLE_ZH:
                self._last_style_zh = cur          # 记住，切回五段时还原
            self.seg_style.configure(state="readonly")
            self.seg_style.set(OFFICIAL_SHOT_ZH)
            self.seg_style.configure(state="disabled")
            self.lbl_seg_style.configure(text="分镜记法")
            self.lbl_seg_count.configure(text="分镜数")
            self.chk_audio.configure(text="输出声音两段（soundscape / music）")
        else:
            self.seg_style.configure(state="readonly")
            if self.seg_style.get() not in STYLE_ZH:
                self.seg_style.set(getattr(self, "_last_style_zh", STYLE_ZH[0]))
            self.lbl_seg_style.configure(text="分段样式")
            self.lbl_seg_count.configure(text="分段数")
            self.chk_audio.configure(text="输出音频三段（style / soundscape / music）")
        self._update_note()
        if not quiet:
            self.set_status("输出格式方案：%s" % self.scheme.get())

    def _update_note(self):
        try:
            d = float(self.duration.get())
        except Exception:
            d = 6.0
        extra = ""
        if self.camera.get().startswith("完全静止"):
            extra = ("｜单镜头、无剪辑：官方方案就写一个 [Shot 1]，不带时间戳"
                     if self._current_scheme() == "official"
                     else "｜单镜头、无剪辑：建议用 [0-1.5s] 分段样式")
        elif self._current_scheme() == "official":
            extra = "｜官方方案：[Shot 1] 不带时间戳，之后每镜写 [Shot N] At MM:SS.mmm,"
        self.note_var.set(comfy_params.duration_note(d) + extra)

    # ------------------------------------------------------------ 状态
    def set_status(self, text: str, kind: str = ""):
        self.status_var.set(text)
        # 状态栏按语义变色，不用改任何调用点
        if not kind:
            t = text or ""
            if any(k in t for k in ("失败", "错误", "异常", "Error", "error", " ERR")):
                kind = "err"
            elif any(k in t for k in ("取消", "中断", "跳过", "注意", "warn", "WARN")):
                kind = "warn"
            elif any(k in t for k in ("完成", "已保存", "已复制", "成功", "已生成", "已拉取", " OK")):
                kind = "ok"
        color = {"err": ERR, "warn": WARN, "ok": OK, "busy": ACCENT}.get(kind, MUTED)
        try:
            self.lbl_status.configure(foreground=color)
        except Exception:
            pass

    def _set_busy(self, busy: bool, msg: str = ""):
        self.busy = busy
        for name in ("btn_gen", "btn_fix", "btn_gen_lv", "btn_fix_lv", "btn_models",
                     "btn_skill_fetch"):
            w = getattr(self, name, None)
            if w is not None:
                w.configure(state="disabled" if busy else "normal")
        for name in ("btn_cancel", "btn_cancel_lv"):
            w = getattr(self, name, None)
            if w is not None:
                w.configure(state="normal" if busy else "disabled")
        if busy:
            self.progress.pack(side="right", padx=8)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
        if msg:
            self.set_status(msg, kind="busy" if busy else "")
        elif busy:
            try:
                self.lbl_status.configure(foreground=ACCENT)
            except Exception:
                pass

    # ------------------------------------------------------------ 生成
    def on_generate(self, page: str = "gen"):
        if self.busy:
            return
        inp = self.collect_input(page)
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]
        brief_w = getattr(self, W["brief"])
        missing = [i + 1 for i, p in enumerate(inp.pictures) if not p.description.strip()]
        if not inp.brief.strip():
            if not messagebox.askyesno("需求是空的",
                                       "“%s”还是空的。LLM 看不到图，没有需求它只能瞎编。\n"
                                       "确定要继续吗？" % ("长视频需求" if page == "lv" else "视频需求")):
                self.nb.select(getattr(self, W["tab"]))
                brief_w.focus_set()
                return
        elif missing and not messagebox.askyesno(
                "参考图描述不完整",
                "参考图 %s 的文字描述是空的。\nLLM 看不到图，空的描述会让它无法锁定身份，"
                "只能写最保守的话。\n确定继续？" % "、".join(str(m) for m in missing)):
            return

        self._save_all()
        msgs = prompt_spec.build_messages(inp)
        self._launch(msgs, "generate", inp)

    def on_fix(self, page: str = "gen"):
        if self.busy:
            return
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]
        outw = getattr(self, W["out"])
        text = txt_get(outw).strip()
        if not text:
            messagebox.showinfo("没有可修正的内容", "先点「生成」。")
            return
        inp = self.collect_input(page)
        report = validate.check(text, inp.duration, inp.include_audio, inp.segment_style,
                                inp.format_scheme, None, long_video=bool(inp.long_video))
        problems = validate.problems_only(report)
        if not problems:
            messagebox.showinfo("不需要修正", "校验报告里没有 err 级别的问题。")
            return
        self._save_all()
        msgs = prompt_spec.build_fix_messages(inp, text, problems)
        self._launch(msgs, "fix", inp)

    def _launch(self, messages: list, mode: str, inp):
        s = self._collect_settings()
        try:
            extra = cfgstore.parse_extra_payload(s["extra_payload"])
        except ValueError as e:
            messagebox.showerror("额外请求参数有误", str(e))
            self.nb.select(self.tab_api)
            return
        self.cancel.clear()
        self._set_busy(True, "正在请求 %s …（模型 %s，超时 %ss）" % (
            llm_api.endpoint_of(s["api_base"]), s["model"], s["timeout"]))
        t = threading.Thread(target=self._worker,
                             args=(s, extra, messages, mode, inp), daemon=True)
        t.start()

    def _worker(self, s, extra, messages, mode, inp):
        t0 = time.time()
        try:
            text, usage = llm_api.chat_completion(
                base=s["api_base"], api_key=s["api_key"], model=s["model"], messages=messages,
                temperature=s["temperature"], max_tokens=s["max_tokens"], timeout=s["timeout"],
                extra_payload=extra, verify_ssl=s["verify_ssl"])
            self.q.put(("ok", mode, text, usage, time.time() - t0, inp))
        except llm_api.ApiError as e:
            self.q.put(("apierror", mode, str(e), None, time.time() - t0, inp))
        except Exception as e:  # noqa: BLE001
            self.q.put(("crash", mode, "%s\n%s" % (e, traceback.format_exc(limit=4)), None,
                        time.time() - t0, inp))

    def on_cancel(self):
        self.cancel.set()
        self._set_busy(False, "已取消（本次请求的返回会被忽略）。")

    def _poll(self):
        """工作线程只往队列里丢结果，所有 UI 更新都在这里做（tkinter 不是线程安全的）。"""
        try:
            while True:
                item = self.q.get_nowait()
                kind, mode = item[0], item[1]

                # 测试连接 / 模型列表 的结果不受“取消”影响
                if kind == "test":
                    self.set_status("连接成功。模型回了一句：%r" % item[2])
                    messagebox.showinfo("连接成功", "接口通了。\n模型回复：%s" % item[2])
                    continue
                if kind == "testerr":
                    self.set_status("连接失败")
                    messagebox.showerror("连接失败", item[2])
                    continue
                if kind == "models":
                    ids = item[2] or []
                    self.v_model.configure(values=ids or llm_api.MODEL_SUGGESTIONS)
                    self.set_status("已拉取 %d 个模型，下拉框里选。" % len(ids))
                    self.btn_models.configure(state="normal")
                    continue
                if kind == "modelserr":
                    self.set_status("拉取模型列表失败")
                    messagebox.showerror("拉取模型列表失败", item[2])
                    self.btn_models.configure(state="normal")
                    continue
                if kind == "skill":
                    self.btn_skill_fetch.configure(state="normal")
                    self._refresh_skill_tab()
                    self._show_skill_doc()
                    meta = item[2] or {}
                    self.set_status("官方指南已拉取并缓存（%s%s，用时 %.1fs）" % (
                        meta.get("fetched_at", ""),
                        " · 官方提交 " + meta["commit"] if meta.get("commit") else "",
                        item[3] or 0.0))
                    continue
                if kind == "skillerr":
                    self.btn_skill_fetch.configure(state="normal")
                    self.set_status("拉取官方指南失败")
                    messagebox.showerror("拉取官方指南失败", item[2])
                    continue

                if self.cancel.is_set():
                    self.cancel.clear()
                    continue
                if kind == "ok":
                    self._on_result(mode, item[2], item[3] or {}, item[4], item[5])
                elif kind == "apierror":
                    self._set_busy(False, "接口报错")
                    messagebox.showerror("接口报错", item[2])
                else:
                    self._set_busy(False, "程序内部错误")
                    messagebox.showerror("程序内部错误", item[2])
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _on_result(self, mode, raw_text, usage, elapsed, inp):
        page = getattr(inp, "page", "gen")
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]
        outw = getattr(self, W["out"])
        text = validate.clean_output(raw_text)
        txt_set(outw, text)
        self.current_text = text
        outw.edit_reset()
        report = validate.check(text, inp.duration, inp.include_audio, inp.segment_style,
                                inp.format_scheme, None, long_video=bool(inp.long_video))
        self._set_report(report, page)
        self.last_report = report
        self.last_usage = usage
        tok = ""
        if usage:
            tok = "｜tokens 入%s 出%s" % (usage.get("prompt_tokens", "?"),
                                         usage.get("completion_tokens", "?"))
            if usage.get("note"):
                tok += "｜" + usage["note"]
        errs = validate.problems_only(report)
        self._set_busy(False, "%s完成，用时 %.1f 秒%s｜%s" % (
            "修正" if mode == "fix" else "生成", elapsed, tok,
            ("发现 %d 个格式问题，可点「让 LLM 按报告修正」" % len(errs)) if errs else "格式校验通过"))
        if page == "lv":
            self._refresh_lv_panels()
        if errs:
            self.nb.select(getattr(self, W["tab"]))

    def _set_report(self, report: list, page: str = "gen"):
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]
        rep = getattr(self, W["rep"])
        rep.configure(state="normal")
        rep.delete("1.0", "end")
        if not report:
            rep.insert("end", "（还没有校验结果）", "warn")
        for level, msg in report:
            mark = {"err": "✗", "warn": "!", "ok": "✓"}.get(level, "-")
            rep.insert("end", "%s %s\n" % (mark, msg), level)
        rep.configure(state="disabled")

    def on_recheck(self, page: str = "gen"):
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]
        text = txt_get(getattr(self, W["out"])).strip()
        if not text:
            self._set_report([], page)
            return
        inp = self.collect_input(page)
        report = validate.check(text, inp.duration, inp.include_audio, inp.segment_style,
                                inp.format_scheme, None, long_video=bool(inp.long_video))
        self._set_report(report, page)
        self.last_report = report
        errs = validate.problems_only(report)
        self.set_status("校验完成：%s" % ("发现 %d 个问题" % len(errs) if errs else "全部通过"))

    # ------------------------------------------------------------ 输出操作
    def on_copy(self, page: str = "gen"):
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]
        text = txt_get(getattr(self, W["out"]))
        if not text.strip():
            self.set_status("没有可复制的内容。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # 保证写进系统剪贴板
        self.set_status("已复制 %d 个字符到剪贴板，去 ComfyUI 的 Input Text (Prompt) 节点粘贴。" % len(text))

    def on_save(self, page: str = "gen"):
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]
        text = txt_get(getattr(self, W["out"]))
        if not text.strip():
            messagebox.showinfo("没有可保存的内容", "先生成一段。")
            return
        stem = "h3_longvideo_%Y%m%d_%H%M%S.txt" if page == "lv" else "h3_input_%Y%m%d_%H%M%S.txt"
        path = filedialog.asksaveasfilename(
            title="保存 Input Text",
            defaultextension=".txt",
            initialfile=time.strftime(stem),
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            self.set_status("已保存：" + path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("保存失败", str(e))

    def on_copy_prompt(self):
        inp = self.collect_input()
        sp = prompt_spec.build_system_prompt(inp.duration, inp.include_audio, inp.extra_rules,
                                             scheme=inp.format_scheme)
        self.root.clipboard_clear()
        self.root.clipboard_append(sp)
        self.root.update()
        self.set_status("已复制 system prompt（%d 字符，%s）。" % (len(sp), self.scheme.get()))

    def on_show_example(self):
        win = tk.Toplevel(self.root)
        win.title("工作流里的官方范例（只示范结构，模型被禁止抄内容）")
        win.geometry("900x640")
        t = tk.Text(win, wrap="word", font=MONO_FONT)
        t.pack(fill="both", expand=True)
        t.insert("1.0", prompt_spec.CANONICAL_EXAMPLE)
        t.configure(state="disabled")

    def on_demo(self, page: str = "gen"):
        if page == "lv":
            if not messagebox.askyesno("载入示例", "用“30 秒连续长镜头”的示例数据覆盖长视频页的输入？"):
                return
            self._load_demo_long()
            self.set_status("长视频示例已载入。填好 API Key 后点「生成长视频提示词」即可试跑。")
            return
        if not messagebox.askyesno("载入示例", "用“吃草莓蛋糕”的示例数据覆盖当前所有输入？"):
            return
        self._load_demo()
        self.set_status("示例已载入。填好 API Key 后点「生成 Input Text」即可试跑。")

    def _fill_page(self, page: str, d) -> None:
        """把一份 OperatorInput 填进指定页面的控件（两页共用）。"""
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]

        def g(k):
            name = W.get(k)
            return getattr(self, name) if name else None

        for x, p in zip(g("pics") or [], d.pictures):
            x["role"].set(prompt_spec.ROLES[p.role]["zh"])
            x["short"].delete(0, "end")
            x["short"].insert(0, p.short_name)
            txt_set(x["body"], p.description)
        txt_set(g("brief"), d.brief)
        for key, val in (("duration", "%g" % d.duration), ("seg_count", str(d.segment_count)),
                         ("window", str(d.lv_window)), ("context", str(d.lv_context))):
            x = g(key)
            if x is not None:
                x.set(val)
        cw = g("camera")
        if cw is not None:
            cw.set(d.camera)
        cc = g("cam_custom")
        if cc is not None:
            cc.delete(0, "end")
        for key, val in (("loop", d.loop), ("audio", d.include_audio)):
            x = g(key)
            if x is not None:
                x.set(val)
        pw = g("primary")
        if pw is not None:
            pw.set("<Subject %d>" % (d.primary_audio_subject + 1))
        txt_set(g("dialogue"), d.dialogue)
        txt_set(g("sound"), d.sound_brief)
        txt_set(g("prohib"), d.prohibitions)
        mw = g("mode")
        if mw is not None:
            mw.set(longvideo.MODE_ZH.get(d.lv_mode, longvideo.MODE_ZH_LIST[0]))

    def _load_demo(self):
        d = prompt_spec.demo_input()
        self._fill_page("gen", d)
        self.scheme.set(h3_skill.SCHEMES[d.format_scheme]["zh"])
        self.seg_style.set(prompt_spec.SEGMENT_STYLES[d.segment_style]["zh"])
        self._on_scheme_change(quiet=True)

    def _load_demo_long(self):
        self._fill_page("lv", prompt_spec.demo_input_longvideo())
        self._refresh_lv_panels()

    def on_clear(self, page: str = "gen"):
        if not messagebox.askyesno("清空输入", "清掉这一页的三张图描述、需求和所有参数？"):
            return
        W = PAGE_WIDGETS.get(page) or PAGE_WIDGETS["gen"]

        def g(k):
            name = W.get(k)
            return getattr(self, name) if name else None

        for x in g("pics") or []:
            x["short"].delete(0, "end")
            txt_set(x["body"], "")
        for k in ("brief", "dialogue", "sound", "prohib"):
            txt_set(g(k), "")
        cc = g("cam_custom")
        if cc is not None:
            cc.delete(0, "end")
        dw = g("duration")
        if dw is not None:
            dw.set("30" if page == "lv" else "6")
        cnt = g("seg_count")
        if cnt is not None:
            cnt.set("0")
        cw = g("camera")
        if cw is not None:
            cw.set(CAMERA_KEYS[0])
        for key in ("loop", "audio"):
            x = g(key)
            if x is not None:
                x.set(True)
        pw = g("primary")
        if pw is not None:
            pw.set("<Subject 1>")
        if page == "gen":
            self.scheme.set(SCHEME_ZH_LIST[0])
            self.seg_style.set(STYLE_ZH[0])
            self._on_scheme_change(quiet=True)
        else:
            win, ctx = g("window"), g("context")
            if win is not None:
                win.set(str(longvideo.WINDOW_FRAMES))
            if ctx is not None:
                ctx.set(str(longvideo.DEFAULT_CONTEXT))
            self._refresh_lv_panels()
        self.set_status("输入已清空。")

    # ------------------------------------------------------------ API 操作
    def on_save_settings(self):
        s = self._collect_settings()
        self.cfg.update(s)
        err = cfgstore.save(self.cfg)
        if err:
            messagebox.showerror("保存失败", err)
        else:
            self.set_status("设置已保存到 " + cfgstore.config_path())

    def on_test(self):
        s = self._collect_settings()
        if not s["api_key"]:
            messagebox.showwarning("缺 API Key", "先填 API Key。")
            return
        self.set_status("正在测试连接 …")
        self.root.update_idletasks()

        def run():
            try:
                text, usage = llm_api.chat_completion(
                    base=s["api_base"], api_key=s["api_key"], model=s["model"],
                    messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                    temperature=0.0, max_tokens=8, timeout=min(60, s["timeout"]),
                    verify_ssl=s["verify_ssl"])
                self.q.put(("test", "", text[:80], usage, 0, None))
            except Exception as e:  # noqa: BLE001
                self.q.put(("testerr", "", str(e), None, 0, None))

        threading.Thread(target=run, daemon=True).start()

    def on_list_models(self):
        s = self._collect_settings()
        if not s["api_key"]:
            messagebox.showwarning("缺 API Key", "先填 API Key 才能拉模型列表。")
            return
        self.btn_models.configure(state="disabled")
        self.set_status("正在拉取模型列表 …")

        def run():
            try:
                ids = llm_api.list_models(s["api_base"], s["api_key"], verify_ssl=s["verify_ssl"])
                self.q.put(("models", "", ids, None, 0, None))
            except Exception as e:  # noqa: BLE001
                self.q.put(("modelserr", "", str(e), None, 0, None))

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ 生命周期
    def _save_all(self):
        self.cfg.update(self._collect_settings())
        self.cfg["work"] = self._work_snapshot()
        cfgstore.save(self.cfg)

    def _on_close(self):
        if getattr(self, "_closing", False):     # ⌘Q 可能同时触发菜单项和兜底绑定
            return
        self._closing = True
        try:
            self._save_all()
        except Exception:
            pass
        self.root.destroy()


# ==========================================================================
# 自检（无 GUI）
# ==========================================================================
def selftest() -> int:
    # Windows 控制台默认 GBK，中文/符号会炸，这里强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("== selftest ==")
    inp = prompt_spec.demo_input()
    assert inp.format_scheme == "official", inp.format_scheme
    msgs = prompt_spec.build_messages(inp)
    assert len(msgs) == 2 and msgs[0]["role"] == "system"
    print("官方六段 system prompt: %d chars" % len(msgs[0]["content"]))
    print("官方六段 user message : %d chars" % len(msgs[1]["content"]))
    assert "<Picture 1>" in msgs[1]["content"]
    for s in h3_skill.REF2VA_SECTIONS:
        assert s in msgs[0]["content"], "官方 system prompt 里缺 %s" % s
    assert "FORBIDDEN TAGS" in msgs[1]["content"], "user message 里缺媒体标签禁令"
    assert "raises ValueError" in msgs[0]["content"]

    # 0b) 切回五段方案，system prompt 必须跟着变
    inp5 = prompt_spec.demo_input()
    inp5.format_scheme, inp5.segment_style, inp5.segment_count = "workflow5", "time", 4
    msgs5 = prompt_spec.build_messages(inp5)
    assert "overall_style" in msgs5[0]["content"] and "retention_analysis" not in msgs5[0]["content"]
    assert "[0-1.5s]" in msgs5[1]["content"] and "FORBIDDEN TAGS" not in msgs5[1]["content"]
    print("工作流五段 system prompt: %d chars" % len(msgs5[0]["content"]))

    # 1) 官方六段范例必须通过官方校验
    rep0 = validate.check(validate.clean_output(h3_skill.OFFICIAL_EXAMPLE), 6.0, True,
                          "shot", "official")
    errs0 = validate.problems_only(rep0)
    for lv, m in rep0:
        print("   [%s] %s" % (lv, m))
    assert not errs0, "官方六段范例竟然没过校验：%s" % errs0

    # 1b) 工作流五段范例必须通过五段校验
    rep = validate.check(validate.clean_output(prompt_spec.CANONICAL_EXAMPLE), 10.0, True,
                         "shot", "workflow5")
    errs = validate.problems_only(rep)
    assert not errs, "五段范例竟然没过校验：%s" % errs
    print("   五段范例 0 err OK")

    # 1c) 两套校验必须互不相容（证明真的按方案分支了）
    assert validate.has_errors(validate.check(h3_skill.OFFICIAL_EXAMPLE, 6.0, True,
                                             "time", "workflow5"))
    assert validate.has_errors(validate.check(prompt_spec.CANONICAL_EXAMPLE, 10.0, True,
                                             "shot", "official"))
    print("   交叉校验会报错 OK（两套规则确实不同）")

    # 2) 破坏过的文本必须被抓出来
    broken = (prompt_spec.CANONICAL_EXAMPLE
              .replace("<Subject 1>", "Subject 1", 2)
              .replace("[Shot 4, 7-10s]", "[Shot 4, 7-9s]"))
    rep2 = validate.check(broken, 10.0, True, "shot", "workflow5")
    errs2 = validate.problems_only(rep2)
    assert errs2, "破坏过的文本没被抓出来"
    print("   broken -> %d errors: %s" % (len(errs2), errs2[:3]))

    # 2b) 节点级致命错：<Audio 1> / <Video 1> / <Picture 4> 会让 strict_prompt_tags 直接抛异常
    for tag in ("<Audio 1>", "<Video 1>", "<Picture 4>"):
        poisoned = h3_skill.OFFICIAL_EXAMPLE.replace("<Picture 3>", tag, 1)
        got = [m for m in validate.problems_only(
            validate.check(poisoned, 6.0, True, "shot", "official")) if "节点标签" in m]
        assert got, "%s 没被当成致命错" % tag
    print("   <Audio 1> / <Video 1> / <Picture 4> 全部判为节点级致命错 OK")

    # 2c) 官方六段独有的校验：summary 前缀、retention 标记、<d> 台词
    for patch, why in (
            ("[reference generation]", "The target"),
            ("fully_preserved", "fully_kept"),
            ("<d>[Chinese] 你等等，我还没看完。</d>", "你等等，我还没看完。")):
        bad = h3_skill.OFFICIAL_EXAMPLE.replace(patch, why, 1)
        assert validate.has_errors(validate.check(bad, 6.0, True, "shot", "official")), \
            "改了 %r 却没报错" % why
    print("   summary 前缀 / retention 标记 / <d> 台词 校验 OK")

    # 3) 带围栏+开场白+结尾寒暄的输出要被洗干净
    dirty = "好的，这是结果：\n```text\n" + prompt_spec.CANONICAL_EXAMPLE + "\n```\n希望有帮助！"
    cleaned = validate.clean_output(dirty)
    assert cleaned.startswith("subject_definitions:"), cleaned[:60]
    assert "```" not in cleaned and "希望有帮助" not in cleaned
    assert cleaned.endswith("dramatic percussion."), cleaned[-40:]
    # 官方范例带中文台词，洗的时候不许把台词行吃掉
    dirty2 = "```text\n" + h3_skill.OFFICIAL_EXAMPLE + "\n```\n希望有帮助！"
    cleaned2 = validate.clean_output(dirty2)
    assert "<d>[Chinese] 你等等，我还没看完。</d>" in cleaned2, "clean_output 吃掉了 <d> 台词行"
    print("   clean_output OK（含 <d> 中文台词保护）")

    # 3b) 正文中间的中文不能被悄悄删掉，必须留给 check() 报错
    mid = prompt_spec.CANONICAL_EXAMPLE.replace("Wide establishing shot.", "Wide establishing shot. 镜头缓慢推进。")
    mid_cleaned = validate.clean_output(mid)
    assert "镜头缓慢推进" in mid_cleaned
    assert validate.has_errors(validate.check(mid_cleaned, 10.0, True, "shot", "workflow5"))
    print("   mid-text CJK is reported, not silently dropped OK")

    # 3c) 单镜头参数不能因为运算符优先级说反话（五段方案）
    one = prompt_spec.demo_input()
    one.format_scheme, one.segment_style = "workflow5", "time"
    one.camera = "完全静止，无任何运镜（推荐）"
    assert "YES - one unbroken take" in prompt_spec.build_user_message(one)
    two = prompt_spec.demo_input()
    two.format_scheme, two.segment_style = "workflow5", "shot"
    two.camera = "完全静止，无任何运镜（推荐）"
    assert "follow the camera instruction above" in prompt_spec.build_user_message(two)
    three = prompt_spec.demo_input()
    three.format_scheme, three.segment_style = "workflow5", "time"
    three.camera = "多分镜（有切换）"
    assert "follow the camera instruction above" in prompt_spec.build_user_message(three)
    # 官方方案：镜头预设里写了 no cuts 就收成一个分镜，写了“多分镜”就放开
    off1 = prompt_spec.demo_input()
    off1.camera, off1.segment_count = "完全静止，无任何运镜（推荐）", 0
    assert "exactly ONE shot" in prompt_spec.build_user_message(off1)
    off2 = prompt_spec.demo_input()
    off2.camera, off2.segment_count = "多分镜（有切换）", 3
    assert "exactly 3 shots" in prompt_spec.build_user_message(off2)
    print("   continuous-shot flag OK（五段 + 官方两套）")

    # 4) 帧数数学
    print("   frames(10s) =", comfy_params.frame_count(10), "| frames(6s) =", comfy_params.frame_count(6))

    # 5) 长视频：分段数学必须复现工作流便签里那张表
    for secs, segs, frames in ((12.25, 3, 294), (30.0, 7, 720), (60.0, 14, 1440)):
        s = longvideo.plan_summary(secs)
        assert s["segments"] == segs, (secs, s["segments"])
        assert s["total_frames"] == frames, (secs, s["total_frames"])
    lvp = longvideo.plan_summary(30.0)
    assert [p["new_frames"] for p in lvp["plan"]] == [124, 102, 102, 102, 102, 102, 86], lvp["plan"]
    print("   longvideo: 12.25s→3段 / 30s→7段 / 60s→14段 OK")

    # 6) 长视频规则必须进 system prompt，且校验要认出时间码
    lvi = prompt_spec.demo_input_longvideo()
    lvmsgs = prompt_spec.build_messages(lvi)
    assert "LONG VIDEO MODE" in lvmsgs[0]["content"], "长视频规则块没进 system prompt"
    assert str(longvideo.SUBMISSION_CHAR_LIMIT) in lvmsgs[0]["content"]
    assert "绝对禁止任何绝对时间码" in lvmsgs[1]["content"]
    bad = ("detailed_description:\n[0-4s] she looks. [Shot 2] At 00:03.500, she turns.\n")
    lverr = [m for lv, m in validate.check(bad, 30.0, True, "shot", "official", None,
                                           long_video=True) if lv == "err"]
    assert any("绝对时间码" in m for m in lverr), lverr
    assert any("一致性声明" in m for m in lverr), lverr
    assert not any("缺少 “At MM:SS.mmm,”" in m for m in lverr), lverr
    assert longvideo.budget_status("x" * 7001)["over"] == 1
    print("   longvideo: 规则进 system / 抓时间码 / 抓缺一致性 / 7000 预算 OK")

    print("== PASS ==")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    enable_dpi_awareness()
    root = tk.Tk()
    set_window_icon(root)
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
