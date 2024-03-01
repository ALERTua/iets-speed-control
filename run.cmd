
@echo off
set VENV_DIR=.venv
set PYTHON_VERSION=3.12
pushd %~dp0

py -0 | findstr /IC:"%PYTHON_VERSION%" >nul 2>nul || (
    echo Python version %PYTHON_VERSION% not found
    echo List of Pythons:
    py -0
    exit /b 1
)

if not exist %VENV_DIR% (
    py -%PYTHON_VERSION% -m venv %VENV_DIR%
)

call %VENV_DIR%\Scripts\activate
python -m pip install -U pip packaging poetry
poetry install
python -m source
