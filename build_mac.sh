#!/usr/bin/env bash
# ==========================================================================
#  MiniMax H3 提示词生成器 —— macOS 一键打包（对应 Windows 的 build_exe.bat）
#
#  用法：
#      bash build_mac.sh
#
#  它会：查 python3 / tkinter / Tk 版本 → 跑 --selftest → 缺 app.icns 就生成
#        → 用 h3input.spec 打出 dist/H3InputTextBuilder.app → ad-hoc 签名
#
#  ★ PyInstaller 不能跨平台打包：这个脚本必须在 macOS（Apple Silicon / arm64）上跑。
#    在 Windows 上跑 build_exe.bat，在 Mac 上跑本脚本。
# ==========================================================================
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

echo "============================================"
echo " H3 Input Text Builder - macOS 打包 (.app)"
echo "============================================"
echo

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[错误] 找不到 $PY。先装 Python 3.9+（推荐 python.org 官方安装包或 Homebrew）。"
    exit 1
fi

echo "[1/5] 检查环境 ..."
"$PY" - <<'PYEOF'
import platform
import re
import sys

print("  python : %s  (%s)" % (sys.version.split()[0], platform.machine()))
if platform.machine() != "arm64":
    print("  [警告] 当前解释器不是 arm64（%s）。打出来的 .app 会是这个架构，"
          "在 Apple Silicon 上只能靠 Rosetta 跑。" % platform.machine())

try:
    import tkinter
except Exception as e:
    sys.exit("[错误] 这个 python 没有 tkinter —— 这是 macOS 上最常见的坑：\n"
             "        · Homebrew 的 python 默认不带 → brew install python-tk@%d.%d\n"
             "        · Apple CLT 的 /usr/bin/python3 从来不带，而且没法补装\n"
             "        · 最省事：用 python.org 的官方安装包（自带 tkinter）\n"
             "        原始错误：%s" % (sys.version_info[0], sys.version_info[1], e))

try:
    patch = tkinter.Tcl().eval("info patchlevel")      # 例如 8.6.12
except Exception:
    patch = ""
nums = [int(x) for x in re.findall(r"\d+", patch)[:3]]
print("  Tk     : %s" % (patch or tkinter.TkVersion))
if len(nums) < 3:
    # 拿不到精确 patchlevel 时只做粗检查，别误杀一个其实够新的 Tk
    print("  [提示] 拿不到精确的 Tk patchlevel，跳过 8.6.12 门槛检查")
    if tkinter.TkVersion < 8.6:
        sys.exit("[错误] Tk %s 太旧，要求 ≥ 8.6.12。" % tkinter.TkVersion)
elif nums < [8, 6, 10]:
    sys.exit("[错误] Tk %s 太旧：< 8.6.10 在 Retina / 新系统上有字体发虚和崩溃问题，"
             "请升级（brew upgrade python-tk）。" % patch)
elif nums < [8, 6, 12]:
    print("  [警告] Tk %s < 8.6.12：Retina 上可能发虚，建议 brew upgrade python-tk。" % patch)
PYEOF

echo "[2/5] 检查 PyInstaller ..."
if "$PY" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "  PyInstaller 已就绪"
else
    echo "  正在安装 PyInstaller ..."
    "$PY" -m pip install --upgrade pyinstaller
fi

echo "[3/5] 自检 ..."
"$PY" app.py --selftest

echo "[4/5] 图标 ..."
if [ -f app.icns ]; then
    echo "  app.icns 已存在，跳过"
elif "$PY" -c "import PIL" >/dev/null 2>&1; then
    "$PY" make_icns.py
else
    echo "  [跳过] 没装 Pillow，无法生成 app.icns。"
    echo "         想要自定义图标：$PY -m pip install pillow && $PY make_icns.py"
    echo "         否则 .app 会用 PyInstaller 的默认图标（功能不受影响）。"
fi

echo "[5/5] 打包 ..."
# 只清 spec 对应的构建目录，不动 dist（dist 里可能有你自己放的东西）
if [ -d build/h3input ]; then
    rm -rf build/h3input
fi
if [ -e "dist/H3InputTextBuilder.app" ]; then
    rm -rf "dist/H3InputTextBuilder.app"
fi
"$PY" -m PyInstaller --noconfirm --clean h3input.spec

APP="dist/H3InputTextBuilder.app"
if [ ! -d "$APP" ]; then
    echo "[错误] 没找到 $APP，看上面的报错。"
    exit 1
fi

# Apple Silicon 上二进制至少要有一次签名才能跑；PyInstaller 会自动做 ad-hoc 签名，
# 这里再显式做一次，确保 .app 整体（含内部 framework / dylib）都被签过。
echo "  做 ad-hoc 签名 ..."
codesign --force --deep --sign - "$APP"
if codesign --verify --deep --strict "$APP" 2>/dev/null; then
    echo "  签名校验 OK"
else
    echo "  [警告] 签名校验没过。自己用一般没问题；要分发请按 README-macOS.md 用开发者证书重签。"
fi

echo
echo "完成 →  $(pwd)/$APP"
echo "首次打开：Finder 里右键点它 → 「打开」→ 再点「打开」。"
echo "（或在「系统设置 → 隐私与安全性」里点「仍要打开」；命令行可用 xattr -dr com.apple.quarantine \"$APP\"）"
