#!/bin/sh
set -eu

REPO="git+https://github.com/stanuser297/niji-agent.git@main"

if command -v python3 >/dev/null 2>&1; then
    PYTHON="${PYTHON:-python3}"
elif command -v python >/dev/null 2>&1; then
    PYTHON="${PYTHON:-python}"
else
    echo "Python 3.10+ is required. On Termux, run: pkg install python git" >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Git is required to install from GitHub. On Termux, run: pkg install git" >&2
    exit 1
fi

"$PYTHON" -m pip install --upgrade --force-reinstall --no-cache-dir "$REPO"
"$PYTHON" -c 'from importlib.metadata import version; print("Installed niji-agent", version("niji-agent"))'

echo "Installation complete. Start the first-run setup with: niji"
