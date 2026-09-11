@echo off
chcp 65001 >nul
title 安装开机自启（系统 + 外网隧道）
echo ==========================================================
echo  安装开机自启：本系统 + 外网隧道
echo ==========================================================
echo.
echo  说明：安装到"当前用户的启动文件夹"，开机登录后自动运行：
echo        1) 生产质量管理系统（本机 8000）
echo        2) Cloudflare 外网隧道（临时地址，地址写到桌面）
echo  注意：仅对当前登录用户生效，不需要管理员权限。
echo        卸载 = 删除启动文件夹里的这两个文件即可。
echo.

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set ROOT=%~dp0..

if not exist "%ROOT%\main.py" (
  echo [X] 没找到 main.py，请把本脚本放在 scripts\ 目录下运行
  pause
  exit /b 1
)

echo [1/2] 安装"系统自启"…
> "%STARTUP%\QMS-Start-System.bat" echo @echo off
>>"%STARTUP%\QMS-Start-System.bat" echo chcp 65001 ^>nul
>>"%STARTUP%\QMS-Start-System.bat" echo cd /d "%ROOT%"
>>"%STARTUP%\QMS-Start-System.bat" echo start "" /min python main.py
echo     已创建：%STARTUP%\QMS-Start-System.bat

echo [2/2] 安装"外网隧道自启"…
> "%STARTUP%\QMS-Start-Tunnel.bat" echo @echo off
>>"%STARTUP%\QMS-Start-Tunnel.bat" echo chcp 65001 ^>nul
>>"%STARTUP%\QMS-Start-Tunnel.bat" echo cd /d "%ROOT%"
>>"%STARTUP%\QMS-Start-Tunnel.bat" echo start "" /min python scripts\tunnel_autostart.py
echo     已创建：%STARTUP%\QMS-Start-Tunnel.bat

echo.
echo [OK] 安装完成。下次开机登录后会自动启动。
echo.
echo 提示：
echo   · 外网地址会写到桌面 QMS外网地址.txt（临时地址每次重启会变）
echo   · 想用固定域名：先 python scripts\cloudflare_setup.py 你的域名，
echo     再把隧道自启改成 scripts\start_cloudflare_named.bat
echo   · 立即生效（不重启）：手动双击启动文件夹里这两个文件
echo.
pause
