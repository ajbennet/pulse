#!/usr/bin/env bash
# One-command local start. Creates a venv, installs deps, launches the app.
# On your phone (same Wi-Fi): open the "Network URL" printed below.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
exec ./.venv/bin/streamlit run app.py "$@"
