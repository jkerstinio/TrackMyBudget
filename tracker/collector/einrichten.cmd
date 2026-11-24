@echo off
rem Einmalig: Passwörter im Windows-Speicher ablegen und ersten Abruf mit Freigabe in der ING-App machen.
call "%~dp0_venv.cmd" || (echo Einrichtung fehlgeschlagen & pause & exit /b 1)
"%LOCALAPPDATA%\TrackMyBudget\venv\Scripts\python.exe" "%~dp0collector.py" setup
echo.
pause
