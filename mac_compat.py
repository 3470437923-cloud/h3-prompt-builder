# -*- coding: utf-8 -*-
"""mac_compat.py — 跨平台兼容层（Windows / macOS 双跑）

★ 为什么需要它 ★
    这批项目原先只在 Windows 上跑，散落着三类 Windows 专有写法：
      1. 配置目录写死 `%APPDATA%` / `%LOCALAPPDATA%`
      2. `os.startfile()` 打开文件/目录（macOS 上不存在这个函数）
      3. `ctypes.windll` 调 Win32（消息框 / 单实例 / 窗口激活 / DPI）
    macOS 上这三类全都要换实现。本模块把替换实现集中到一处，
    项目里改成调用这里的函数即可，**不再散落平台分支**。

设计原则：
    · 单文件、零第三方依赖，可以直接拷进任何项目
    · Windows 分支**保持原行为不变**（老平台不能因为移植而回归）
    · macOS 分支用系统自带工具（open / osascript）或标准库，不引入新依赖

用法：
    from mac_compat import app_support_dir, reveal_in_file_manager, msgbox
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WIN and not IS_MAC


# ──────────────────────────────────────────────────────────── 目录

def app_support_dir(app_name, create=True):
    """应用配置/数据目录（跨平台）。

    Windows: %APPDATA%\\<app_name>
    macOS:   ~/Library/Application Support/<app_name>
    Linux:   $XDG_CONFIG_HOME/<app_name> 或 ~/.config/<app_name>
    """
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif IS_MAC:
        base = os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
    d = os.path.join(base, app_name)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def app_cache_dir(app_name, create=True):
    """应用缓存目录（跨平台）。"""
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif IS_MAC:
        base = os.path.join(os.path.expanduser("~"), "Library", "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache")
    d = os.path.join(base, app_name)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def user_desktop():
    """桌面目录（Windows 的 Desktop 可能是中文重定向名，走注册表更准）。"""
    if IS_WIN:
        try:
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                r"\User Shell Folders")
            v, _ = winreg.QueryValueEx(k, "Desktop")
            winreg.CloseKey(k)
            return os.path.expandvars(v)
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


# ──────────────────────────────────────────────────────── 打开 / 定位

def reveal_in_file_manager(path):
    """在系统文件管理器里定位到 path（Windows 资源管理器 / macOS 访达）。

    返回 True 表示已发起调用（不保证窗口一定弹出）。
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return False
    if IS_WIN:
        try:
            os.startfile(path)              # noqa: S606  仅 Windows 存在
            return True
        except Exception:
            return False
    if IS_MAC:
        # -R 表示"在访达中显示"（选中它，而不是打开它）
        cmd = ["open", "-R", path] if os.path.isfile(path) else ["open", path]
        return _spawn(cmd)
    return _spawn(["xdg-open", os.path.dirname(path)
                   if os.path.isfile(path) else path])


def open_path(path):
    """用系统默认程序打开文件或目录。"""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return False
    if IS_WIN:
        try:
            os.startfile(path)              # noqa: S606
            return True
        except Exception:
            return False
    if IS_MAC:
        return _spawn(["open", path])
    return _spawn(["xdg-open", path])


def _spawn(cmd):
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────────── 外部工具

def find_ffmpeg():
    """定位 ffmpeg 可执行文件。

    ★ 关键差异：Windows 上 ffmpeg 通常以 `ffmpeg.exe` 的形式躺在项目目录或
      PATH 里；macOS 上一般用 Homebrew 装（/opt/homebrew/bin/ffmpeg 或
      /usr/local/bin/ffmpeg）。返回 None 表示没找到，调用方要给出
      「brew install ffmpeg」的提示，而不是抛 FileNotFoundError。
    """
    import shutil
    name = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    hit = shutil.which(name) or shutil.which("ffmpeg")
    if hit:
        return hit
    # 项目目录旁边的常见位置
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(here, name),
        os.path.join(here, "bin", name),
        os.path.join(here, "vendor", name),
        "/opt/homebrew/bin/ffmpeg",      # Apple Silicon Homebrew
        "/usr/local/bin/ffmpeg",         # Intel Homebrew
        "/opt/local/bin/ffmpeg",         # MacPorts
    ):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def ffmpeg_hint():
    """找不到 ffmpeg 时给用户的提示文案。"""
    if IS_MAC:
        return ("未找到 ffmpeg。macOS 上请用 Homebrew 安装：\n"
                "    brew install ffmpeg\n"
                "装完重开终端，或用 `which ffmpeg` 确认路径。")
    return ("未找到 ffmpeg.exe。请把它放到项目目录下，或加入 PATH。")


# ──────────────────────────────────────────────────────────── 原生对话框

def msgbox(text, title="提示", kind="info", buttons="ok"):
    """原生消息框（不依赖 tkinter，可在无 GUI 框架的脚本里用）。

    kind:    "info" | "warning" | "error" | "question"
    buttons: "ok" | "yesno"
    返回:    True / False（yesno 场景）；ok 场景恒为 True
    """
    text = str(text)
    title = str(title)

    if IS_WIN:
        return _msgbox_win(text, title, kind, buttons)
    if IS_MAC:
        return _msgbox_mac(text, title, kind, buttons)
    return _msgbox_tk(text, title, kind, buttons)


def _msgbox_win(text, title, kind, buttons):
    try:
        import ctypes
        MB_OK = 0x0
        MB_YESNO = 0x4
        MB_ICONERROR = 0x10
        MB_ICONQUESTION = 0x20
        MB_ICONWARNING = 0x30
        MB_ICONINFO = 0x40
        MB_TOPMOST = 0x40000
        icon = {"error": MB_ICONERROR, "question": MB_ICONQUESTION,
                "warning": MB_ICONWARNING}.get(kind, MB_ICONINFO)
        base = MB_YESNO if buttons == "yesno" else MB_OK
        r = ctypes.windll.user32.MessageBoxW(0, text, title,
                                              base | icon | MB_TOPMOST)
        return r == 6 if buttons == "yesno" else True
    except Exception:
        return _msgbox_tk(text, title, kind, buttons)


def _msgbox_mac(text, title, kind, buttons):
    """macOS 用 osascript 弹原生对话框（System Events / 标准增补）。"""
    icon = {"error": "stop", "question": "note",
            "warning": "caution"}.get(kind, "note")
    # 转义 AppleScript 字符串里的反斜杠与双引号
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    if buttons == "yesno":
        script = ('display dialog "%s" with title "%s" buttons {"取消", "确定"} '
                  'default button "确定" with icon %s'
                  % (esc(text), esc(title), icon))
        r = _osascript(script)
        return r is not None and "确定" in r
    script = ('display dialog "%s" with title "%s" buttons {"好"} '
              'default button "好" with icon %s' % (esc(text), esc(title), icon))
    _osascript(script)
    return True


def _osascript(script, timeout=120):
    """跑一段 AppleScript，返回 stdout（失败返回 None）。"""
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _msgbox_tk(text, title, kind, buttons):
    """兜底：tkinter 消息框（需要已有 Tk 环境，或自己起一个隐藏 root）。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk._default_root
        own = False
        if root is None:
            root = tk.Tk()
            root.withdraw()
            own = True
        fn = {"error": messagebox.showerror,
              "warning": messagebox.showwarning,
              "question": messagebox.askyesno}.get(
                  kind, messagebox.showinfo)
        if buttons == "yesno" or kind == "question":
            res = messagebox.askyesno(title, text)
        else:
            fn(title, text)
            res = True
        if own:
            root.destroy()
        return res
    except Exception:
        print("[msgbox] %s: %s" % (title, text), file=sys.stderr)
        return False


# ──────────────────────────────────────────────────── 单实例 / 窗口激活

class SingleInstance:
    """跨平台单实例锁。

    Windows: 命名互斥体（CreateMutexW + ERROR_ALREADY_EXISTS）
    macOS/Linux: fcntl.flock 锁文件（进程退出自动释放，不会留死锁）

    用法：
        lock = SingleInstance("MyApp.SingleInstance.v1")
        if not lock.acquire():
            print("已有实例在运行")
            sys.exit(0)
    """

    def __init__(self, name):
        self.name = name
        self._fh = None
        self._handle = None

    def acquire(self):
        """拿到锁返回 True；已有实例返回 False。"""
        if IS_WIN:
            try:
                import ctypes
                from ctypes import wintypes
                ERROR_ALREADY_EXISTS = 183
                k32 = ctypes.windll.kernel32
                k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                             wintypes.LPCWSTR]
                k32.CreateMutexW.restype = wintypes.HANDLE
                h = k32.CreateMutexW(None, True, self.name)
                if ctypes.get_last_error() == ERROR_ALREADY_EXISTS or \
                        k32.GetLastError() == ERROR_ALREADY_EXISTS:
                    return False
                self._handle = h
                return True
            except Exception:
                return True          # 拿不到锁机制时放行，别把应用锁死
        # POSIX
        try:
            import fcntl
            d = app_support_dir("mac_compat")
            path = os.path.join(d, self.name.replace("/", "_") + ".lock")
            self._fh = open(path, "a+")
            try:
                fcntl.flock(self._fh.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False
        except Exception:
            return True

    def release(self):
        try:
            if self._fh is not None:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                self._fh.close()
                self._fh = None
        except Exception:
            pass


def activate_window_by_pid(pid=None):
    """把某个进程的窗口提到前台（macOS 用 System Events）。

    ★ 与 Windows 的 FindWindowW/SetForegroundWindow 不同，macOS 上
      首次调用会触发「辅助功能」隐私授权（系统设置 → 隐私与安全性 →
      辅助功能）。未授权时本函数静默失败并返回 False —— 调用方不该
      因为激活失败就崩掉，这只是体验优化。
    """
    if pid is None:
        pid = os.getpid()
    if IS_WIN:
        return False        # Windows 侧项目仍走原有 ctypes 实现
    if IS_MAC:
        script = ('tell application "System Events" to set frontmost of '
                  '(first process whose unix id is %d) to true' % int(pid))
        return _osascript(script, timeout=10) is not None
    return False


# ──────────────────────────────────────────────────────────── 其他

def set_dpi_awareness():
    """Windows 高 DPI 感知。macOS 由系统统一处理，无需设置（空操作）。"""
    if not IS_WIN:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def kill_process_tree(pid):
    """结束进程树。"""
    if IS_WIN:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
            return True
        except Exception:
            return False
    import signal
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return True
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except Exception:
            return False


def temp_dir():
    """临时目录（两个平台都用系统标准位置）。"""
    return tempfile.gettempdir()


# ──────────────────────────────────────────────────────────── 字体

#: 各平台的中文字体候选（按优先级）
_CJK_FONT_CANDIDATES = {
    "win32": [
        r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
        r"C:\Windows\Fonts\msyhbd.ttc",     # 微软雅黑 粗体
        r"C:\Windows\Fonts\simhei.ttf",     # 黑体
        r"C:\Windows\Fonts\simsun.ttc",     # 宋体
    ],
    "darwin": [
        "/System/Library/Fonts/PingFang.ttc",              # 苹方（系统默认中文）
        "/System/Library/Fonts/STHeiti Medium.ttc",        # 华文黑体
        "/System/Library/Fonts/Hiragino Sans GB.ttc",      # 冬青黑体
        "/System/Library/Fonts/Supplemental/Songti.ttc",   # 宋体-简
        "/Library/Fonts/Arial Unicode.ttf",
    ],
}

#: 等宽字体候选
_MONO_FONT_CANDIDATES = {
    "win32": [r"C:\Windows\Fonts\consola.ttf",
              r"C:\Windows\Fonts\cour.ttf"],
    "darwin": ["/System/Library/Fonts/Menlo.ttc",
               "/System/Library/Fonts/SFNSMono.ttf",
               "/System/Library/Fonts/Monaco.ttf"],
}


def _pick_font(cands):
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def find_cjk_font(extra=()):
    """定位一个可用的中文字体文件（给 PIL 画字用）。

    ★ 为什么要跨平台：原先这批工具全都写死 `C:\\Windows\\Fonts\\msyh.ttc`，
      macOS 上那个路径不存在，PIL 会直接抛 OSError 或画出豆腐块。

    返回字体文件路径；全找不到返回 None（调用方应回退到 PIL 默认位图字体，
    或直接用几何图形占位）。
    """
    key = "win32" if IS_WIN else ("darwin" if IS_MAC else "darwin")
    cands = list(extra) + _CJK_FONT_CANDIDATES.get(key, [])
    return _pick_font(cands)


def find_mono_font(extra=()):
    """定位一个可用的等宽字体文件（给 PIL 画代码/ASCII 用）。"""
    key = "win32" if IS_WIN else ("darwin" if IS_MAC else "darwin")
    cands = list(extra) + _MONO_FONT_CANDIDATES.get(key, [])
    return _pick_font(cands)


def find_any_font(extra=()):
    """找一个能用的字体：优先中文，其次等宽，再退回系统常见字体。"""
    f = find_cjk_font(extra) or find_mono_font(extra)
    if f:
        return f
    for p in ("/System/Library/Fonts/Helvetica.ttc",
              "/System/Library/Fonts/Geneva.ttf",
              r"C:\Windows\Fonts\arial.ttf"):
        if os.path.isfile(p):
            return p
    return None


#: 各平台 GUI 字体族名（tkinter / Qt 用）
def ui_font_family(cjk=True):
    """返回当前平台的界面字体族名。

    Windows: "Microsoft YaHei UI" / "Consolas"
    macOS:   "PingFang SC" / "Menlo"
    """
    if IS_WIN:
        return "Microsoft YaHei UI" if cjk else "Consolas"
    if IS_MAC:
        return "PingFang SC" if cjk else "Menlo"
    return "Noto Sans CJK SC" if cjk else "DejaVu Sans Mono"
