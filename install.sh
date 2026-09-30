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

# Remove an older install first, then install the current branch with its pinned dependencies.
"$PYTHON" -m pip uninstall -y niji-agent >/dev/null 2>&1 || true
"$PYTHON" -m pip install --upgrade --force-reinstall --no-cache-dir "$REPO"
"$PYTHON" -c 'import niji, niji.setup_wizard; from importlib.metadata import version; v=version("niji-agent"); assert v == "2.0.0", f"expected 2.0.0, got {v}"; print("Installed niji-agent", v, "from", niji.__file__)'

echo "Installation complete. Launching niji setup/chat..."
if [ -r /dev/tty ]; then
    "$PYTHON" -m niji </dev/tty
else
    echo "No interactive terminal detected. Run: niji"
fi
