# VPS Deployment Notes

Phase 1 deployment target:

- LTAC only
- headless monitor + web dashboard
- SQLite at `/var/lib/metar-monitor/metar.db`

## Recommended flow

1. Run `deploy/setup.sh` on the Ubuntu VPS as root.
2. Copy the repo into `/opt/metar-monitor`.
3. Create the venv and install the app in editable mode.
4. If you have local JSON history, import it once:

```bash
/opt/metar-monitor/.venv/bin/python -m metar_monitor \
  --import-json \
  --state-file /home/metar/.metar_monitor \
  --db-path /var/lib/metar-monitor/metar.db
```

5. Install the systemd unit:

```bash
cp /opt/metar-monitor/deploy/metar-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now metar-monitor
```

## Logs

```bash
journalctl -u metar-monitor -f
```

## Service Behavior

The deployed app runs as a `systemd` service.

- It keeps running until you stop or disable it.
- It restarts automatically if the process crashes.
- It starts again automatically after a droplet reboot.

Useful commands:

```bash
systemctl status metar-monitor
systemctl restart metar-monitor
systemctl stop metar-monitor
systemctl disable metar-monitor
journalctl -u metar-monitor -f
```

## Redeploy After Pushing New Code

Pushing to GitHub does not restart the running service by itself. The droplet
keeps running the old in-memory code until you update the repo, reinstall, and
restart the service.

Run this on the droplet:

```bash
cd /opt/metar-monitor
git pull
sudo -u metar /opt/metar-monitor/.venv/bin/pip install -e /opt/metar-monitor
systemctl restart metar-monitor
systemctl status metar-monitor
```

Only if you changed `deploy/metar-monitor.service`, also run:

```bash
systemctl daemon-reload
systemctl restart metar-monitor
```

## Notes

- Textual is still the local/dev path in phase 1.
- The deployed service path is `--web`, which implies headless mode.
- Put the dashboard behind a private network or auth layer before exposing it.
