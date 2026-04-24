@echo off
setlocal

REM Run this on the Windows backend machine only.
REM Tailscale:
REM   Windows backend/API: 100.91.53.63
REM   Mac Ink dashboard:  100.104.250.54

set DASHBOARD_API_HOST=100.91.53.63
set DASHBOARD_API_PORT=8765

uv run main
