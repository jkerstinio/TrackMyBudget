@echo off
rem Abruf von Hand, z. B. wenn der Tracker "Freigabe nötig" meldet.
call "%~dp0_venv.cmd" || (echo Einrichtung fehlgeschlagen & pause & exit /b 1)
"%LOCALAPPDATA%\TrackMyBudget\venv\Scripts\python.exe" "%~dp0collector.py" run -i
echo.
pause
