# https://github.com/casey/just
set dotenv-load

# Set shell for non-Windows OSs:
set shell := ["powershell", "-c"]

# Set shell for Windows OSs:
#set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set windows-shell := ["cmd.exe", "/c"]

lint:
    uv run ruff format .
    uv run ruff check --fix

pre:
    uv run pre-commit run --all-files

sync:
    uv sync --dev

test:
    uv run pytest

# End-to-end tests against real sensors; needs AIDA64 and/or LibreHardwareMonitor running
test-e2e:
    uv run pytest -m e2e -rs

build:
    uv build

# Show available commands
help:
    @just --list
