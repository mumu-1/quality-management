@echo off
chcp 65001 >nul
title 开放外网访问端口（需要管理员权限）
echo ==========================================================
echo  生产质量管理系统 - 防火墙放行（外网访问用）
echo ==========================================================
echo.
net session >nul 2>&1
if errorlevel 1 (
  echo [X] 需要管理员权限：请右键本文件 -^> "以管理员身份运行"
  pause
  exit /b 1
)

set PORT=18080
echo 即将放行入站 TCP 端口 %PORT% （转发到本机 8000）。
echo 说明：用 18080 而不是 8000，避免与常见扫描/其他程序撞车。
echo.
netsh advfirewall firewall delete rule name="QMS 外网访问 %PORT%" >nul 2>&1
netsh advfirewall firewall add rule name="QMS 外网访问 %PORT%" dir=in action=allow protocol=TCP localport=%PORT%
if errorlevel 1 (
  echo [X] 添加失败
) else (
  echo.
  echo [OK] 已放行 TCP %PORT%
  echo.
  echo 下一步（在路由器管理页做端口映射，不是在这台电脑上）：
  echo    外部端口 %PORT%  -^>  内部 192.168.100.197 : 8000   （协议 TCP）
  echo 做完后用手机流量访问验证：
  echo    http://你的公网IP:%PORT%/api/health
  echo.
  echo 提醒：公网访问务必配 HTTPS（明文会把密码暴露在网络上）。
)
echo.
pause
