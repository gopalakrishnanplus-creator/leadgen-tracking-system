#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/leadgen-tracking-system"
VENV_DIR="/var/www/.venv"

cd "$APP_DIR"
git fetch origin
git pull --ff-only origin main
sudo install -m 644 deploy/nginx/leadgen-upload-limits.conf /etc/nginx/conf.d/leadgen-upload-limits.conf
sudo nginx -t
sudo systemctl reload nginx
python3.11 -m venv "$VENV_DIR"
"$VENV_DIR"/bin/pip install --upgrade pip
"$VENV_DIR"/bin/pip install -r requirements.txt
"$VENV_DIR"/bin/python manage.py migrate
"$VENV_DIR"/bin/python manage.py collectstatic --noinput
sudo systemctl restart leadgen-tracking
