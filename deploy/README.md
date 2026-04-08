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

## Notes

- Textual is still the local/dev path in phase 1.
- The deployed service path is `--web`, which implies headless mode.
- Put the dashboard behind a private network or auth layer before exposing it.
