
@echo off
pushd %~dp0
uv run pyinstaller -i media\icon.ico -w source\gui.py --onefile --noupx --clean -p .
echo the exe is at %~dp0dist\gui.exe
