#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Please install Python 3.9+ from https://www.python.org/downloads/'
  read -r answer
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || exit 1
python3 app.py
echo 'Application stopped. Press Enter to close.'
read -r answer
