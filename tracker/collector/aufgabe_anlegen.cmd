@echo off
rem Automatischer Abruf ohne Adminrechte:
rem  1) Windows-Aufgabe: taeglich um 7:00
rem  2) Verknuepfung im Autostart-Ordner: 2 Minuten nach jeder Anmeldung (ohne Fenster)
set "PYW=%LOCALAPPDATA%\TrackMyBudget\venv\Scripts\pythonw.exe"
set "SCRIPT=%~dp0collector.py"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TrackMyBudget Sammler.lnk"
schtasks /Create /F /TN "TrackMyBudget Sammler taeglich" /SC DAILY /ST 07:00 /TR "\"%PYW%\" \"%SCRIPT%\" run" || goto :err
rem verpasste Termine nachholen (PC war um 7:00 aus/im Standby), auch im Akkubetrieb laufen
powershell -NoProfile -Command "$n='TrackMyBudget Sammler taeglich'; $s=(Get-ScheduledTask -TaskName $n).Settings; $s.StartWhenAvailable=$true; $s.DisallowStartIfOnBatteries=$false; $s.StopIfGoingOnBatteries=$false; $s.RunOnlyIfNetworkAvailable=$true; $s.ExecutionTimeLimit='PT15M'; Set-ScheduledTask -TaskName $n -Settings $s | Out-Null" || goto :err
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath=$env:PYW; $s.Arguments='\"' + $env:SCRIPT + '\" run --wait 120'; $s.WorkingDirectory='%~dp0'; $s.Save()" || goto :err
echo.
echo Fertig: taeglich 7:00 und nach jeder Anmeldung. Entfernen mit aufgabe_entfernen.cmd
pause
exit /b
:err
echo Anlegen fehlgeschlagen.
pause
