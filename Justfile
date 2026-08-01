# https://github.com/casey/just
#set dotenv-load

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

# The whole suite, serial: about 25 s, of which the GUI tests are roughly 22 s.
test:
    uv run pytest

# Everything that does not need a Tk root, in parallel: about 2 s. This is the loop to use while
# editing non-GUI code. Note that the workers are worth ~0.3 s of that; skipping the GUI tests is
# what makes it quick. "-n auto" is deliberately not used -- 32 workers each build their own Tk root
# and rebuild the module-scoped panels, which measured 3x SLOWER than serial on the full suite.
test-fast:
    uv run pytest -m "not gui and not e2e" -n 8

# End-to-end tests against real sensors; needs AIDA64 and/or LibreHardwareMonitor running
test-e2e:
    uv run pytest -m e2e -rs

build:
    uv build

# Show available commands
help:
    @just --list
