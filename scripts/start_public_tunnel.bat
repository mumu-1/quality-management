@echo off
chcp 65001 >nul
title QMS 外网隧道（Cloudflare Tunnel）
cd /d "%~dp0.."

echo ==========================================================
echo  生产质量管理系统 - 外网访问隧道
echo ==========================================================
echo.
echo  说明：本脚本把本机 8000 端口通过 Cloudflare 隧道发布到外网。
echo        * 临时地址：每次启动都变，适合先试用（无需注册）
echo        * 固定域名：需先在 Cloudflare 注册域名并按《外网访问指南》配置
echo.
echo  安全提醒：请先执行 python scripts\preflight_public.py 体检，
echo            确认没有弱口令再对外发布。
echo.

set CF=%USERPROFILE%\cloudflared\cloudflared.exe
if not exist "%CF%" (
  echo [X] 未找到 %CF%
  echo     请先运行：python scripts\download_cloudflared.py
  pause
  exit /b 1
)

echo [1/3] 确认本地服务在运行...
curl -s -o nul http://localhost:8000/api/health
if errorlevel 1 (
  echo [X] 本地 8000 端口没有响应，请先启动系统：python main.py
  pause
  exit /b 1
)
echo     本地服务正常。

echo.
echo [2/3] 启动隧道（窗口保持打开，关掉窗口=中断外网访问）
echo     稍等 10 秒左右，下面会打印 https://xxxx.trycloudflare.com 形式的外网地址
echo.
echo [3/3] 复制打印出来的地址发给同事/客户即可；如需固定域名见《外网访问指南》
echo ----------------------------------------------------------
"%CF%" tunnel --url http://localhost:8000 --no-autoupdate

echo.
echo 隧道已结束。
pause
