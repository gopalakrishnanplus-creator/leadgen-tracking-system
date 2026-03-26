#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/srv/leadgen-tracking-system"

cd "$APP_DIR"
git fetch origin
git pull --ff-only origin main
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart leadgen-tracking
