#!/bin/bash
# SlotLab run helper
set -e
PY=${PY:-/tmp/opencode/slotlab-venv/bin/python}
$PY -m pip install -q -r requirements.txt 2>/dev/null || true
$PY manage.py migrate
$PY manage.py runserver 0.0.0.0:8000
