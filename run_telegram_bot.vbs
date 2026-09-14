' Launches the Telegram bot hidden (no console window), logging to
' telegram_bot_log.txt. Used by the "Jarvis Telegram Bot" scheduled task so
' phone access works automatically after every reboot/login too.
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\User\elradi brand\jarvis"
WshShell.Run "cmd /c ""C:\Users\User\elradi brand\jarvis\venv\Scripts\python.exe"" -u telegram_bot.py >> telegram_bot_log.txt 2>&1", 0, False
