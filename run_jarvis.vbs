' Launches Jarvis's wake-word loop hidden (no console window), logging to
' jarvis_log.txt. Used by the "Jarvis Assistant" scheduled task so it starts
' automatically at logon, like a real always-on assistant.
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\User\elradi brand\jarvis"
WshShell.Run "cmd /c ""C:\Users\User\elradi brand\jarvis\venv\Scripts\python.exe"" -u main.py >> jarvis_log.txt 2>&1", 0, False
