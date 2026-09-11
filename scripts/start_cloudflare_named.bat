@echo off
chcp 65001 >nul
title QMS 固定域名隧道（Cloudflare Named Tunnel）
cd /d "%~dp0.."

set CFG=%USERPROFILE%\.cloudflared\config.yml
set CF=%USERPROFILE%\cloudflared\cloudflared.exe

echo ==========================================================
echo  生产质量管理系统 - 固定域名隧道（长期使用）
echo ==========================================================
echo.

if not exist "%CF%" (
  echo [X] 未找到 %CF%
  echo     请先运行：python scripts\download_cloudflared.py
  pause
  exit /b 1
)

if not exist "%CFG%" (
  echo [X] 还没有配置固定域名隧道。
  echo     请先运行向导：python scripts\cloudflare_setup.py 你的域名
  echo     （没域名可先用临时地址：scripts\start_public_tunnel.bat）
  pause
  exit /b 1
)

echo [1/2] 确认本地服务在运行...
curl -s -o nul http://localhost:8000/api/health
if errorlevel 1 (
  echo [X] 本地 8000 没有响应，请先启动系统：python main.py
  pause
  exit /b 1
)
echo     本地服务正常。

echo.
echo [2/2] 启动隧道（关掉窗口=外网中断）
echo ----------------------------------------------------------
"%CF%" tunnel --config "%CFG%" run

echo.
echo 隧道已结束。可运行 python scripts\tunnel_status.py 查看状态。
pause
