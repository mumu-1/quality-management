@echo off
REM ============================================================
REM  QMS daily backup + keep 30 days
REM  Schedule: Task Scheduler -> daily 23:30
REM   schtasks /create /tn "QMS-DailyBackup" /tr "D:\qms\scripts\daily_backup.bat" /sc daily /st 23:30 /ru %USERNAME%
REM ============================================================
cd /d "%~dp0.."
echo [%date% %time%] QMS backup start >> backups\backup.log 2>nul
python scripts\backup.py backup >> backups\backup.log 2>&1
echo [%date% %time%] done >> backups\backup.log
