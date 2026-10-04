# https://github.com/casey/just
#set dotenv-load

# Set shell for non-Windows OSs:
set shell := ["powershell", "-c"]

# Set shell for Windows OSs:
#set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set windows-shell := ["cmd.exe", "/c"]

# The tray app with its window
gui:
    uv run iets-speed-control-gui

# The console version: prints every reading and every change to the fan
cli:
    uv run iets-speed-control

lint:
    uv run ruff format .
    uv run ruff check --fix

pre:
    uv run pre-commit run --all-files

sync:
    uv sync --dev

# The whole suite, the way pre-commit and CI run it: four workers, one test file each at a time,
# so every file builds its GUI panel once in its own worker. About 12 s; serial takes about 16 s.
test:
    uv run pytest -n 4 --dist loadfile

# Everything that does not need a Tk root, in parallel: about 2 s. This is the loop to use while
# editing non-GUI code; skipping the GUI tests is what makes it quick. "-n auto" is deliberately
# not used anywhere -- 32 workers each build their own Tk root and rebuild the module-scoped panels.
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
