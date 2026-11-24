@echo off
rem Einmalig: Sammler mit Google Drive verbinden (Browser-Anmeldung). Vorher [drive] in config.toml ausfuellen.
call "%~dp0_venv.cmd" || (echo Einrichtung fehlgeschlagen & pause & exit /b 1)
"%LOCALAPPDATA%\TrackMyBudget\venv\Scripts\python.exe" "%~dp0collector.py" drive
echo.
pause
