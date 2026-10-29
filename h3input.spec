# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows / macOS 共用这一份）。

用法（**必须在目标平台上各跑一次** —— PyInstaller 不能跨平台打包）：
    Windows:  pyinstaller --noconfirm --clean h3input.spec
    macOS:    pyinstaller --noconfirm --clean h3input.spec      # 必须在 arm64 上跑

产物：
    Windows: dist/H3InputTextBuilder/H3InputTextBuilder.exe
    macOS:   dist/H3InputTextBuilder.app

为什么要有这个 .spec（原来只有 build_exe.bat 里那串命令行参数）：
  1. `--add-data "app.ico;."` 里的 ";" 是 Windows 专有的分隔符，macOS 上必须写 ":"。
     写进 spec 里用元组列表，两边都不会写错。
  2. `--icon app.ico` 在 macOS 上无效（只认 .icns）。
  3. macOS 上 onefile 启动慢、还容易被 Gatekeeper / 杀软拦 —— 改成 onedir，
     再套一层 BUNDLE 变成标准的 .app。
  4. Retina 需要 NSHighResolutionCapable，只能写在 info_plist 里。
build_exe.bat 保持原样不动，Windows 用户的老路径完全不受影响。
"""
import os
import sys

IS_MAC = sys.platform == "darwin"
NAME = "H3InputTextBuilder"

# SPECPATH 由 PyInstaller 注入（spec 所在目录）；别用 __file__，spec 里不保证有
HERE = os.path.abspath(SPECPATH)

# app.ico：Windows 的窗口/任务栏图标
# app.png：macOS 源码直接跑时的窗口图标兜底（.app 里用不到，留着也无害）
datas = []
for _f in ("app.ico", "app.png"):
    _p = os.path.join(HERE, _f)
    if os.path.exists(_p):
        datas.append((_p, "."))

if IS_MAC:
    icon = os.path.join(HERE, "app.icns")
    if not os.path.exists(icon):
        print("[提示] 没找到 app.icns —— 将使用 PyInstaller 默认图标。"
              "先在 Mac 上跑一次 `python make_icns.py`（需要 Pillow）。")
        icon = None
else:
    icon = os.path.join(HERE, "app.ico")
    if not os.path.exists(icon):
        icon = None

a = Analysis(
    [os.path.join(HERE, "app.py")],
    pathex=[HERE],
    binaries=[],
    datas=datas,
    hiddenimports=[],          # 运行时零第三方依赖，全是静态 import，不用藏模块
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "pandas", "matplotlib", "PySide6", "PyQt5", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,     # ★ onedir：所有依赖放进目录，别塞进单个可执行文件
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # ★ 不要 UPX：会破坏 macOS 代码签名，还常被杀软误报
    console=False,             # GUI 程序，不要黑框
    disable_windowed_traceback=False,
    argv_emulation=False,      # 不需要拖文件到图标上打开
    target_arch=None,          # 跟随本机 Python 的架构（arm64 上就是 arm64）
    codesign_identity=None,    # 签名见文件末尾的注释
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=NAME,
)

if IS_MAC:
    # macOS：把 onedir 的产物包成标准 .app（Dock 图标、菜单栏、⌘Q 都靠它）
    app = BUNDLE(
        coll,
        name=NAME + ".app",
        icon=icon,
        bundle_identifier="com.h3input.promptbuilder",
        info_plist={
            # Retina：不写这条，Tk 界面会被系统当成低分辨率程序整体放大 → 糊
            "NSHighResolutionCapable": True,
            "CFBundleName": "H3 提示词生成器",
            "CFBundleDisplayName": "H3 提示词生成器",
            "CFBundleShortVersionString": "0.3",
            "CFBundleVersion": "0.3",
            "LSMinimumSystemVersion": "11.0",
            # Tk 8.6 没有真正的深色外观支持，而本程序配色是写死的浅色
            # （白卡片 + 冷灰底）。强制浅色外观，免得深色模式下原生控件变黑、
            # 输入框还是白的，花成一片。等换成 Tk 9 / Tk 支持深色后可删掉这行。
            # ★ 待 Mac 上验证：这个 key 自 10.14 起被 Apple 标记为废弃，
            #   macOS 26 上是否仍然生效需要实机确认。
            "NSRequiresAquaSystemAppearance": True,
        },
    )

# ---------------------------------------------------------------------------
# 签名 / 公证（macOS 分发用；只想自己用可以跳过，见 README-macOS.md）
#
#   # 0) Apple Silicon 上必须至少有一次签名，否则二进制根本起不来；
#   #    PyInstaller 会自动做 ad-hoc 签名，手动重签用：
#   codesign --force --deep --sign - dist/H3InputTextBuilder.app
#
#   # 1) 用开发者证书正式签名（要开 hardened runtime）
#   codesign --deep --force --options runtime --timestamp \
#       --sign "Developer ID Application: 你的名字 (TEAMID)" \
#       dist/H3InputTextBuilder.app
#   codesign --verify --deep --strict --verbose=2 dist/H3InputTextBuilder.app
#
#   # 2) 打包成 zip 送公证
#   ditto -c -k --keepParent dist/H3InputTextBuilder.app H3InputTextBuilder.zip
#   xcrun notarytool submit H3InputTextBuilder.zip \
#       --apple-id "你的AppleID" --team-id "TEAMID" --password "App专用密码" --wait
#
#   # 3) 公证通过后装订票据（否则离线打开仍会被拦）
#   xcrun stapler staple dist/H3InputTextBuilder.app
#   spctl -a -vvv -t install dist/H3InputTextBuilder.app
# ---------------------------------------------------------------------------
