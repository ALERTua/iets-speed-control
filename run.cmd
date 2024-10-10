
@echo off
pushd %~dp0
for %%I in (.) do set curdir=%%~nxI

uv run %curdir%

set output=%ERRORLEVEL%

echo Exiting %output%
exit /b %output%
