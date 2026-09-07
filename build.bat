@echo off
chcp 936 >NUL
cd /d "%~dp0"

REM ---- ��ѡģʽ: onefile(Ĭ��) | onedir ----
set "MODE=%~1"
if "%MODE%"=="" set "MODE=onefile"

REM ---- �� pause: �ڶ����� nopause �򻷾����� RYUUMD_NOPAUSE=1�����ű�/CI ���ã� ----
set "NOPAUSE="
if /i "%~2"=="nopause" set "NOPAUSE=1"
if "%RYUUMD_NOPAUSE%"=="1" set "NOPAUSE=1"

if /i "%MODE%"=="onefile" goto :onefile
if /i "%MODE%"=="onedir" goto :onedir
echo [����] δ֪ģʽ "%MODE%"
echo �÷�: build.bat [onefile^|onedir] [nopause]
echo   onefile  (Ĭ��) ���ļ� exe�����ڷַ�������� dist\RyuuMD.exe
echo   onedir           Ŀ¼�棬�������죬����� dist\RyuuMD\
echo   nopause          ����ʱ���Ȱ������ű�/CI �ã���Ҳ���� RYUUMD_NOPAUSE=1
call :maybe_pause
exit /b 1

:onefile
set "SPEC=RyuuMD-onefile.spec"
set "DESC=���ļ� onefile"
set "OUTPATH=dist\RyuuMD.exe"
goto :start

:onedir
set "SPEC=RyuuMD.spec"
set "DESC=Ŀ¼�� onedir"
set "OUTPATH=dist\RyuuMD"
goto :start

:start
echo ============================================
echo   RyuuMD ��� - �����ű�  [ģʽ: %DESC%]
echo ============================================
echo.

REM ---- 0. �ر������е� RyuuMD��ռ�� dist ����ᵼ�� PermissionError�� ----
tasklist /fi "imagename eq RyuuMD.exe" 2>NUL | find /i "RyuuMD.exe" >NUL
if not errorlevel 1 (
    echo [0/5] ��⵽�����е� RyuuMD.exe���Ƚ�������ռ�ù������� ...
    taskkill /f /im RyuuMD.exe >NUL 2>&1
    REM ���ļ����ͷ�
    ping -n 3 127.0.0.1 >NUL
)

REM ---- 1. ��� Python ----
python --version >NUL 2>&1
if errorlevel 1 (
    echo [����] δ�ҵ� Python,���Ȱ�װ Python 3.10+ ������ PATH
    call :maybe_pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version') do echo ʹ�� %%v

REM ---- 2. ��鲢��װ���� / PyInstaller���Ѱ�װ�������� ----
echo.
echo [1/5] ��鲢��װ���� / PyInstaller ...

REM �ȼ� PyInstaller �Ƿ��Ѿ�����,����ÿ�ε��� pip(���� C:\Python312\Scripts дȨ������)
python -m PyInstaller --version >NUL 2>&1
if not errorlevel 1 (
    echo   PyInstaller �Ѱ�װ,����������װ
    goto :deps_ok
)

REM PyInstaller ȱʧʱ���� pip
python -m pip --version >NUL 2>&1
if errorlevel 1 (
    echo   δ��⵽ pip,�ȳ���ͨ�� ensurepip ������װ ...
    python -m ensurepip --user
    if errorlevel 1 python -m ensurepip
    if errorlevel 1 (
        echo [����] ensurepip ����ʧ��,���ֶ���װ pip
        call :maybe_pause
        exit /b 1
    )
)

REM ��װ����(ȫ��Ŀ¼��дȨ��ʱ,�˻� --user)
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo   ȫ�ְ�װʧ��,���� --user ��װ ...
    python -m pip install --user -r requirements.txt pyinstaller
    if errorlevel 1 (
        echo [����] ������װʧ��,��������� pip Դ
        call :maybe_pause
        exit /b 1
    )
)

:deps_ok

REM ---- 2.5 ���ǰ��Ԫ����բ�ţ����鵥��ȫ��;E2E ����ʵ����,���ֶ��� python run_tests.py�� ----
echo.
echo [2/5] ���е�Ԫ���ԣ�test_api + test_cloud + test_search + test_fonts�� ...
python -m unittest tests.test_api tests.test_cloud tests.test_search tests.test_fonts tests.test_ai -q >NUL 2>&1
if errorlevel 1 (
    echo [����] ��Ԫ����δͨ��,����ֹ������������� python run_tests.py ��λ�޸�
    call :maybe_pause
    exit /b 1
)
echo   ��Ԫ����ͨ��

REM ---- 3. �����ɲ���(ֻ������ǰģʽĿ��,������һģʽ����) ----
echo.
echo [3/5] �����ɲ��� (%OUTPATH%) ...
if exist build rmdir /s /q build
if exist "%OUTPATH%\" (
    rmdir /s /q "%OUTPATH%"
) else (
    if exist "%OUTPATH%" del /q "%OUTPATH%"
)

REM ---- 4. ִ�д�� ----
echo.
echo [4/5] ִ�� PyInstaller ��� (%SPEC%) ...
python -m PyInstaller --noconfirm --clean "%SPEC%"
if errorlevel 1 (
    echo [����] ���ʧ��
    call :maybe_pause
    exit /b 1
)

REM ---- 5. ��� ----
echo.
echo [5/5] ������!
echo --------------------------------------------
echo  ģʽ     : %DESC%
echo  ���·�� : %cd%\%OUTPATH%
if exist "%OUTPATH%" (
    for %%A in ("%OUTPATH%") do echo  ��С     : %%~zA �ֽ�
)
echo --------------------------------------------
echo.
call :maybe_pause
exit /b 0

:maybe_pause
if not defined NOPAUSE pause
exit /b 0
