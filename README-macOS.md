# macOS 运行 / 打包指南

**适用：Apple Silicon（arm64）+ macOS 26。** 这份文档只讲 macOS；Windows 的用法见 [README.md](README.md)。
两个平台的**功能、逻辑、校验规则完全一致**（同一套代码），差别只在环境、外观和系统集成上 —— 第 6 节逐条列了差异和原因。

---

## 0. 三句话结论

1. **运行时零第三方依赖**：纯 Python 标准库 + tkinter，`pip install` 什么都不用装。
2. **唯一的真坑在环境层**：你机器上的 `python3` 很可能**没有 tkinter** —— Homebrew 的 python 默认不带，Apple Command Line Tools 的 `/usr/bin/python3` 从来不带。见 [1.2](#12-tkinter-从哪来)。
3. **打包必须在 arm64 的 Mac 上做**：PyInstaller 不能跨平台打包。

---

## 1. 环境准备

### 1.1 先验证 tkinter（**第一步就做这个**）

```bash
python3 -c "import tkinter; print(tkinter.TkVersion)"
```

* 打印出 `8.6` → 有 tkinter，继续确认精确版本：

```bash
python3 -c "import tkinter; print(tkinter.Tcl().eval('info patchlevel'))"
```

* 打印 `ModuleNotFoundError: No module named 'tkinter'` → 这个 python 没有 tkinter，看 1.2。

**Tk 版本要求：≥ 8.6.12。**
`< 8.6.10` 的 Tk 在 Retina 屏 / 新系统上有字体发虚甚至崩溃的老问题，建议直接升掉（`brew upgrade python-tk`）。

### 1.2 tkinter 从哪来

| 你的 python3 | tkinter | 怎么办 |
|---|---|---|
| Homebrew 装的 `python@3.13` 等 | ❌ 默认**不带** | `brew install python-tk@3.13`（**版本号必须和 python 一致**） |
| [python.org](https://www.python.org/downloads/macos/) 官方安装包 | ✅ 自带 | 直接用，最省事 |
| Apple CLT 的 `/usr/bin/python3` | ❌ 从来不带，**也没法补装** | 别用它 |
| Xcode 自带的 python3 | ❌ 同上 | 别用它 |

装完再跑一次 1.1 的命令确认。Homebrew 上 `python-tk@3.13` 与 `python@3.13` 必须成对，装错版本号等于没装。

### 1.3 建 venv（可选）

```bash
python3 -m venv .venv
source .venv/bin/activate
```

⚠️ **venv 不会给你补 tkinter**：tkinter 跟着 python 解释器走，不是 pip 包。
如果激活 venv 后 `import tkinter` 失败，说明建这个 venv 的 python 本身就没有 tkinter —— 换一个有 tkinter 的 python 重建。

运行本项目**不需要** `pip install` 任何东西。

---

## 2. 跑源码

```bash
cd 09_H3Input
python3 app.py              # 开窗口
python3 app.py --selftest   # 不开窗口，纯逻辑自检（无头环境也能跑，先跑这个最省事）
```

### 2.1 快捷键（macOS）

| 动作 | 快捷键 |
|---|---|
| 生成当前页的 Input Text | `⌘↩` 或 `⌃↩` |
| 生成长视频提示词（第 2 页） | `⌘⇧↩` 或 `⌃⇧↩` |
| 复制输出 | `⌘⇧C` 或 `⌃⇧C` |
| 退出 | `⌘Q`（菜单栏 → 退出） |

Windows 上只有 `Ctrl+…` 那一套；macOS 上两套都能用。

### 2.2 文件都放哪

| 东西 | macOS | Windows |
|---|---|---|
| 设置 / 草稿 | `~/Library/Application Support/H3PromptBuilder/config.json` | `%APPDATA%\H3PromptBuilder\config.json` |
| 官方指南缓存 | `~/Library/Application Support/H3PromptBuilder/skill/` | `%APPDATA%\H3PromptBuilder\skill\` |

（旧版本写死了 `%APPDATA%`，在 macOS 上会落到家目录根的 `~/H3PromptBuilder/`。**如果那个旧目录里已经有东西，程序会继续用旧目录**，不搬家、不丢数据；没有就一律用新的 Application Support 路径。）

---

## 3. 自检与测试

全部只依赖标准库（`make_icon.py` / `make_icns.py` 才需要 Pillow）：

```bash
python3 app.py --selftest        # 两套方案的 system prompt + 校验 + 清洗 + 帧数 + 长视频规则
python3 test_wheel.py            # 滚轮护栏：真发 MouseWheel 事件（含 macOS 量级的 ±1 delta）
python3 smoke_gui.py             # 界面能否建起来、五个标签页控件在不在、持久化
python3 test_longvideo.py        # 长视频全链路
python3 h3_skill.py              # 官方规则模块自检
python3 longvideo.py             # 长视频分段数学
python3 mock_e2e.py              # 起本地假接口跑完整链路（不需要真 API Key）
```

---

## 4. 打包成 .app

### 4.1 一键

```bash
bash build_mac.sh
```

它会依次：查 python/tkinter/Tk 版本 → 查 PyInstaller（缺就装）→ 跑 `--selftest` → 缺 `app.icns` 就生成 → 用 `h3input.spec` 打包 → 做 ad-hoc 签名。
产物：`dist/H3InputTextBuilder.app`。

### 4.2 手动等价步骤

```bash
python3 -m pip install --upgrade pyinstaller
python3 -m pip install pillow          # 只有生成图标才需要
python3 make_icns.py                   # 生成 app.png + app.icns
python3 -m PyInstaller --noconfirm --clean h3input.spec
codesign --force --deep --sign - dist/H3InputTextBuilder.app
```

`h3input.spec` 是 Windows / macOS 共用的打包配置（Windows 也可以 `pyinstaller --noconfirm --clean h3input.spec` 得到 onedir 目录）。
原来只有 `build_exe.bat` 里的命令行参数，那份**保持原样没动**，Windows 老路径不受影响。

### 4.3 为什么必须在 Mac 上打包

PyInstaller **不能跨平台、也不能交叉编译**：

* 在 Windows 上打不出 macOS 的包（`--add-data` 的分隔符、`--icon` 的格式、bundle 结构全都不一样）；
* 在 Intel Mac 上打出来的是 x86_64 包，在 Apple Silicon 上只能靠 Rosetta 跑。

所以：**在目标架构的 Mac 上跑一次 `build_mac.sh`。**

### 4.4 为什么是 onedir（.app）而不是 onefile

| | onefile | onedir（本项目选的） |
|---|---|---|
| 启动 | 每次启动都要把全部内容解压到临时目录 → 慢 | 直接加载，快 |
| 被拦 | macOS 上更容易被 Gatekeeper / 杀软盯上 | 更规矩 |
| 图标 / 菜单 | 还得额外套 .app | 直接产出标准 `.app`，Dock 图标、菜单栏、`⌘Q` 都正常 |

### 4.5 图标

| 平台 | 文件 | 生成方式 |
|---|---|---|
| Windows | `app.ico`（16~256 共 7 个尺寸） | `python make_icon.py` |
| macOS 打包 | `app.icns` | `python make_icns.py`（PNG → `app.iconset/` → `iconutil -c icns`） |
| macOS 源码运行 | `app.png`（1024×1024） | 同上，`iconphoto` 兜底当窗口图标 |

* `iconutil` 是 macOS 自带工具，**只能在 Mac 上生成 .icns**。在 Windows 上跑 `make_icns.py` 只会画好 `app.png` 并打印出在 Mac 上要敲的命令。
* 仓库里已带 `app.png`；`app.icns` 到 Mac 上生成一次即可（换图标就重跑）。
* `PyInstaller --icon app.ico` 在 macOS 上**无效**（只认 `.icns`），spec 里已经按平台分开处理。

### 4.6 签名 / 公证（要分发给别人时）

```bash
# 0) Apple Silicon 上二进制至少要有一次签名才能跑；PyInstaller 会自动做 ad-hoc 签名，
#    手动重签：codesign --force --deep --sign - dist/H3InputTextBuilder.app

# 1) 用开发者证书正式签名（必须带 hardened runtime）
codesign --deep --force --options runtime --timestamp \
    --sign "Developer ID Application: 你的名字 (TEAMID)" \
    dist/H3InputTextBuilder.app
codesign --verify --deep --strict --verbose=2 dist/H3InputTextBuilder.app

# 2) 打包送公证
ditto -c -k --keepParent dist/H3InputTextBuilder.app H3InputTextBuilder.zip
xcrun notarytool submit H3InputTextBuilder.zip \
    --apple-id "你的AppleID" --team-id "TEAMID" --password "App专用密码" --wait

# 3) 公证通过后装订票据（不装订的话离线打开还是会被拦）
xcrun stapler staple dist/H3InputTextBuilder.app
spctl -a -vvv -t install dist/H3InputTextBuilder.app
```

只想自己用：**不用签名也不用公证**，按第 5 节绕过 Gatekeeper 就行。

### 4.7 验证产物

```bash
open dist/H3InputTextBuilder.app          # 应该正常出窗口
dist/H3InputTextBuilder.app/Contents/MacOS/H3InputTextBuilder --selftest; echo "exit=$?"
```

（打包成 windowed 程序后 stdout 不接到终端，所以看**退出码**：0 = 自检通过。）

---

## 5. Gatekeeper：第一次打不开怎么办

未签名 / 未公证的 .app，双击会被拦（「无法打开，因为 Apple 无法检查它是否包含恶意软件」）。三种解法，任选其一：

1. **Finder 里右键点它 → 「打开」→ 再点「打开」**（只需做一次，之后双击即可）；
2. 命令行去掉隔离标记：
   ```bash
   xattr -dr com.apple.quarantine dist/H3InputTextBuilder.app
   ```
3. 「系统设置 → 隐私与安全性」→ 底部会出现「仍要打开」按钮。

---

## 6. 与 Windows 版的差异（都是有意为之）

| 项目 | Windows | macOS | 为什么 |
|---|---|---|---|
| ttk 主题 | `clam`（自绘，能吃全部配色） | **不切主题，保持原生 `aqua`** | 「原生优先」：切 clam 会让按钮/输入框/标签页全变成 Tk 自绘的方头方脑外观 |
| 界面字体 | Microsoft YaHei UI / Consolas | **系统原生字体**（`.AppleSystemUIFont`，中文自动回落苹方）+ Menlo | 原生优先。硬写「PingFang SC 10pt」反而两头不讨好：丢了 SF Pro 的拉丁字形，10pt 在 Mac 上明显偏小（Mac 正文是 13pt） |
| 配置目录 | `%APPDATA%\H3PromptBuilder` | `~/Library/Application Support/H3PromptBuilder` | 旧代码在 macOS 上会写到家目录根，很脏 |
| 图标 | `app.ico` | `app.icns`（Dock）+ `app.png`（窗口） | macOS 只认 .icns |
| 菜单栏 | 无 | 原生 App 菜单（关于 / 退出 `⌘Q`） | Tk 不会自动给 macOS 提供 `⌘Q` |
| 快捷键 | `Ctrl+…` | `⌘…` 和 `Ctrl+…` 都有 | Mac 习惯 |
| 强调按钮文字 | 白字（蓝底） | 深色字 | aqua **忽略按钮背景色**，白字会落在原生浅灰按钮上 → 直接看不见 |
| 滚轮 | delta = ±120 一格 | delta = ±1 一格 | 已抽成 `wheel_steps()` 归一化，两边都是「一格 = 滚 1 行」 |
| 窗口尺寸 | 1360×860 起 | 同左 | 两个平台都会按屏幕实际尺寸夹取，小屏不会超出屏幕 |

Windows 侧的行为（含 `build_exe.bat` 那条老打包路径）**逐条保持不变**。

---

## 7. 验证状态

### 7.1 已在 Windows 上实测通过

* 所有改动过的 `.py` / `.spec` 过了 `ast.parse` 语法检查；
* `python app.py --selftest` → `== PASS ==`；
* `python test_wheel.py` → 全绿（新增了「macOS 量级 delta=±1」的用例，Windows 上发小 delta 就能复现 macOS 分支）；
* `python smoke_gui.py` → `SMOKE OK`，窗口仍是 1360×860；
* `python make_icon.py` 重跑后 `app.ico` **SHA256 与改动前完全一致**；
* `python make_icns.py` 在非 macOS 上只生成 `app.png` 并打印 Mac 上的手动步骤；
* `h3input.spec` 真跑了一遍 PyInstaller：onedir 产物 24.6 MB，`app.ico`/`app.png` 正确进了 `_internal/`，**打包后的 exe `--selftest` 通过**。

### 7.2 待 Mac 上验证

1. **滚轮**：真机（尤其触控板惯性滚动）滚左栏，确认鼠标停在 Combobox / Spinbox / 小 Text 上时页面照滚、值不被误改 —— 这是本次移植最关键的修复，只做过 delta 模拟。
2. **字体**：确认界面用的是系统字体、中文无豆腐块；`TkDefaultFont` 字号被读成 13pt 时整体比例是否合适。
3. **aqua 下强调按钮**文字是否可读（已把前景色改成深色，但 aqua 各版本行为有差异）。
4. **菜单栏**：App 菜单是否出现、「关于」「退出 ⌘Q」是否生效（`<Command-q>` 兜底绑定是否与菜单重复触发 —— `_on_close` 已做成幂等）。
5. **`⌘↩` / `⌘⇧↩` / `⌘⇧C`** 是否都能触发。
6. **配置文件**是否真的落在 `~/Library/Application Support/H3PromptBuilder/`。
7. **「打开缓存目录」**是否弹出访达并定位到目录。
8. **Tk 版本**：`python3 -c "import tkinter; print(tkinter.Tcl().eval('info patchlevel'))"` ≥ 8.6.12。
9. **打包链路**：`bash build_mac.sh` 能否一路跑通；`.app` 的 ad-hoc 签名、Gatekeeper 首次打开。
10. **深色模式**：`NSRequiresAquaSystemAppearance=True` 在 macOS 26 上是否仍生效（该 key 自 10.14 起被 Apple 标为废弃）。若失效，界面会变成「原生控件变黑 + 输入框还是白」的花脸，届时删掉 spec 里那一行再定策略。
11. **`build_mac.sh` 的 bash 语法**：Windows 上没有可用的 bash，只做了结构检查 + 内嵌 python 块实跑，没跑过 `bash -n`。
