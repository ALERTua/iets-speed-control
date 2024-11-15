
@echo off
pushd %~dp0
for %%I in (.) do set curdir=%%~nxI

where uv >nul || (echo no uv. exiting & exit /b 1)

uv run %curdir%

set output=%ERRORLEVEL%

echo Exiting %output%
exit /b %output%
