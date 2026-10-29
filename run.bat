@echo off
rem ============================================================
rem  xAgent 一键启动脚本
rem  自动完成:创建虚拟环境 -> 安装依赖 -> 启动程序
rem  首次运行需要联网下载依赖,之后直接双击即可
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist venv (
    echo [1/3] 首次运行:正在创建虚拟环境 venv ...
    python -m venv venv
    if errorlevel 1 (
        echo 创建虚拟环境失败,请确认已安装 Python 3.10 及以上版本并勾选了 "Add to PATH"
        pause
        exit /b 1
    )
    echo [2/3] 正在安装依赖 openai / textual ^(需要联网,请稍候^)...
    venv\Scripts\python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo 依赖安装失败,请检查网络后重新运行本脚本
        pause
        exit /b 1
    )
) else (
    echo 虚拟环境已存在,跳过安装步骤
)

rem 把 venv 的 Scripts 目录放到 PATH 最前面:
rem 1. 模型通过 RunCli 执行 "python" 时,用的是本项目 venv 的 Python(已装好 openai/textual);
rem 2. venv 的 python.exe 是本地生成的文件,不会触发 Windows 对官网下载 exe 的
rem    "吊销服务器已脱机"签名检查弹窗。
set PATH=%~dp0venv\Scripts;%PATH%

echo [3/3] 启动 xAgent ^(请确认 LM Studio 已启动并加载模型^)...
echo.
venv\Scripts\python main.py
pause
