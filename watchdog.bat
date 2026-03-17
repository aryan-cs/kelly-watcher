@echo off
:loop
uv run python main.py
echo Bot exited with code %errorlevel%. Restarting in 10 seconds...
timeout /t 10
goto loop
