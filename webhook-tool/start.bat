@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Запускаю панель вебхуков...
where py >nul 2>nul && (py -3 server.py & goto :done)
where python >nul 2>nul && (python server.py & goto :done)
if exist "C:\Users\Kotik\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" (
  "C:\Users\Kotik\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" server.py
  goto :done
)
echo Python не найден. Установи Python 3 и запусти: python server.py
:done
pause