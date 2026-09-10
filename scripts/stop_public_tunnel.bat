@echo off
chcp 65001 >nul
title 关闭外网访问隧道
echo ==========================================================
echo  关闭外网隧道（关闭后外网无法访问，内网仍可正常使用）
echo ==========================================================
echo.
set N=0
for /f "tokens=2 delims=," %%p in ('tasklist /fi "imagename eq cloudflared.exe" /fo csv /nh 2^>nul') do (
  taskkill /PID %%~p /F >nul 2>&1
  echo [OK] 已关闭 cloudflared 隧道进程 PID %%~p
  set /a N+=1
)
if "%N%"=="0" echo     没有正在运行的 cloudflared 隧道。
echo.
echo 提示：用 cpolar 起的隧道请在任务管理器结束 cpolar.exe，或在 cpolar 面板里停止对应隧道。
echo 内网访问不受影响：http://192.168.100.197:8000
echo.
pause
