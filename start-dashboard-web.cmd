@echo off
setlocal

pushd "%~dp0dashboard-web" || exit /b 1

if not exist node_modules\vite\package.json (
  echo Installing dashboard-web dependencies...
  call npm install
  if errorlevel 1 goto :end
)

call npm run dev -- %*

:end
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
