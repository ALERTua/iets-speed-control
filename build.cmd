
@echo off
pushd %~dp0
pyinstaller -i media\icon.png -w source\gui.py --onefile --noupx --clean -p .
echo the exe is at %~dp0dist\gui.exe
