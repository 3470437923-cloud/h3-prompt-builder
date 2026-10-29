@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  H3 Input Text Builder - 打包单文件 exe
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python，请先装 Python 3.9+ 并加到 PATH。
    pause
    exit /b 1
)

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [1/3] 安装 PyInstaller ...
    python -m pip install --upgrade pyinstaller
) else (
    echo [1/3] PyInstaller 已就绪
)

echo [2/3] 自检 ...
python app.py --selftest
if errorlevel 1 (
    echo [错误] 自检没过，先别打包。
    pause
    exit /b 1
)

echo [3/3] 打包 ...
if exist build rd /s /q build
rem 不要 rd /s /q dist：dist 里有你自己放的快捷方式，PyInstaller 只会覆盖同名 exe
if exist "dist\H3InputTextBuilder.exe" del /q "dist\H3InputTextBuilder.exe"
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "H3InputTextBuilder" ^
    --icon app.ico --add-data "app.ico;." ^
    --exclude-module numpy --exclude-module pandas ^
    --exclude-module matplotlib --exclude-module PySide6 ^
    --exclude-module PyQt5 --exclude-module PIL ^
    app.py

echo.
if exist "dist\H3InputTextBuilder.exe" (
    echo 完成 →  %~dp0dist\H3InputTextBuilder.exe
    echo 直接双击就能跑，配置文件存在 %%APPDATA%%\H3PromptBuilder\config.json
) else (
    echo [错误] 没找到 dist\H3InputTextBuilder.exe，看上面的报错。
)
echo.
pause
