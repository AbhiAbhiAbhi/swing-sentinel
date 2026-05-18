#!/bin/bash
# Git Push Helper - Unix/Linux/Mac shell wrapper
# Usage: gpush [--strategy bundle|split] [--message "msg"] [--dry-run] [--no-prompt]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/git_push_helper.py"

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed or not in PATH"
    exit 1
fi

# Check if the script exists
if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    echo "Error: git_push_helper.py not found in $SCRIPT_DIR"
    exit 1
fi

# Run the Python script with all arguments passed through
python3 "$PYTHON_SCRIPT" "$@"
exit $?
