@echo off
setlocal

pushd "%~dp0dashboard" || exit /b 1

if not exist node_modules\ink\package.json (
  echo Installing or repairing dashboard dependencies...
  call npm install --omit=dev
  if errorlevel 1 goto :end
)

call npm start -- %*

:end
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
