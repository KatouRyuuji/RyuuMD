@echo off
chcp 936 >NUL
cd /d "%~dp0"

REM ---- 选择模式: onefile(默认) | onedir ----
set "MODE=%~1"
if "%MODE%"=="" set "MODE=onefile"

if /i "%MODE%"=="onefile" goto :onefile
if /i "%MODE%"=="onedir" goto :onedir
echo [错误] 未知模式 "%MODE%"。
echo 用法: build.bat [onefile^|onedir]
echo   onefile  (默认) 单文件 exe,便于分发,产物在 dist\RyuuMD.exe
echo   onedir           单文件夹,启动快,产物在 dist\RyuuMD\
pause
exit /b 1

:onefile
set "SPEC=RyuuMD-onefile.spec"
set "DESC=单文件 onefile"
set "OUTPATH=dist\RyuuMD.exe"
goto :start

:onedir
set "SPEC=RyuuMD.spec"
set "DESC=单文件夹 onedir"
set "OUTPATH=dist\RyuuMD"
goto :start

:start
echo ============================================
echo   RyuuMD 墨读 - 打包脚本  [模式: %DESC%]
echo ============================================
echo.

REM ---- 1. 检查 Python ----
python --version >NUL 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python,请先安装 Python 3.10+ 并加入 PATH。
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version') do echo 使用 %%v

REM ---- 2. 检查并安装依赖(已安装则跳过) ----
echo.
echo [1/5] 检查并安装依赖 / PyInstaller ...

REM 先检查 PyInstaller 是否已经可用,可用则不折腾 pip(避免 C:\Python312\Scripts 写权限问题)
python -m PyInstaller --version >NUL 2>&1
if not errorlevel 1 (
    echo   PyInstaller 已安装,跳过依赖安装
    goto :deps_ok
)

REM PyInstaller 缺失时改用 pip
python -m pip --version >NUL 2>&1
if errorlevel 1 (
    echo   未检测到 pip,先尝试通过 ensurepip 引导安装 ...
    python -m ensurepip --user
    if errorlevel 1 python -m ensurepip
    if errorlevel 1 (
        echo [错误] ensurepip 引导失败,请手动安装 pip。
        pause
        exit /b 1
    )
)

REM 安装依赖(全局目录无写权限时,退回 --user)
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo   全局安装失败,改用 --user 安装 ...
    python -m pip install --user -r requirements.txt pyinstaller
    if errorlevel 1 (
        echo [错误] 依赖安装失败,请检查网络或 pip 源。
        pause
        exit /b 1
    )
)

:deps_ok

REM ---- 2.5 打包前单元测试闸门(E2E 需真实窗口,请手动跑 python run_tests.py) ----
echo.
echo [2/5] 运行单元测试 ...
python -m unittest discover -s tests -p "test_api.py" >NUL 2>&1
if errorlevel 1 (
    echo [错误] 单元测试未通过,已中止打包。请先运行 python run_tests.py 定位修复。
    pause
    exit /b 1
)
echo   单元测试通过。

REM ---- 3. 清理旧产物(只清理当前模式目标,保留另一模式产物) ----
echo.
echo [3/5] 清理旧产物 (%OUTPATH%) ...
if exist build rmdir /s /q build
if exist "%OUTPATH%\" (
    rmdir /s /q "%OUTPATH%"
) else (
    if exist "%OUTPATH%" del /q "%OUTPATH%"
)

REM ---- 4. 执行打包 ----
echo.
echo [4/5] 执行 PyInstaller 打包 (%SPEC%) ...
python -m PyInstaller --noconfirm --clean "%SPEC%"
if errorlevel 1 (
    echo [错误] 打包失败。
    pause
    exit /b 1
)

REM ---- 5. 完成 ----
echo.
echo [5/5] 打包完成!
echo --------------------------------------------
echo  模式     : %DESC%
echo  输出路径 : %cd%\%OUTPATH%
echo --------------------------------------------
echo.
pause
