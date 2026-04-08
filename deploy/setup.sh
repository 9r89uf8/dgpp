#!/usr/bin/env bash
set -euo pipefail

APP_USER="metar"
APP_DIR="/opt/metar-monitor"
DATA_DIR="/var/lib/metar-monitor"

apt update
apt install -y python3.12 python3.12-venv

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "${APP_USER}"
fi

mkdir -p "${APP_DIR}" "${DATA_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" "${DATA_DIR}"

echo "Next steps:"
echo "1. Copy the project into ${APP_DIR}"
echo "2. Create a venv: sudo -u ${APP_USER} python3.12 -m venv ${APP_DIR}/.venv"
echo "3. Install the app: sudo -u ${APP_USER} ${APP_DIR}/.venv/bin/pip install -e ${APP_DIR}"
echo "4. Copy deploy/metar-monitor.service to /etc/systemd/system/"
echo "5. systemctl daemon-reload && systemctl enable --now metar-monitor"
