
@echo off
pushd %~dp0
for %%I in (.) do set curdir=%%~nxI

set PYTHON_VERSION=3.12

echo Checking Python Version
py -0 | findstr /IC:"%PYTHON_VERSION%" >nul 2>nul || (
    echo Python version %PYTHON_VERSION% not found
    echo List of Pythons:
    py -0
    exit /b 1
)

echo Installing poetry for Python %PYTHON_VERSION%
py -%PYTHON_VERSION% -m pip install -U pip packaging poetry

set _poetry=py -%PYTHON_VERSION% -m poetry

echo Poetry self update
%_poetry% self update

echo Setting local virtualenv path
%_poetry% config --local virtualenvs.in-project true

echo Running Poetry Install
%_poetry% install

echo Using Python from
%_poetry% run python -c "import sys;print(sys.executable)"

echo Running %curdir%
%_poetry% run %curdir%

set output=%ERRORLEVEL%

echo Exiting %output%
exit /b %output%
