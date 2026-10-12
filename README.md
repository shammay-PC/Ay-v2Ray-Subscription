# Ay-v2Ray-Subscription — v4.0.0

A production-grade v2Ray subscription merger with a beautiful RTL web panel.

## Features

- **HTTPS-only**: refuses to start without valid certificates.
- **Waitress + SSL**: no more `app.run()` in production.
- **Config cache** protected by a lock (no more random crashes).
- **Safe HTTP**: every outbound request uses timeout + exponential-backoff retry.
- **X-UI header forwarding**: `Subscription-Userinfo`, `Profile-Title`, etc.
- **Manual configs** with inline editor (name view / raw view).
- **Side collections**: plain and base64, per-config rename and visibility, cached with TTL.
- **SSH user manager**: `username:password` storage, no random password rotation.
- **Service control**: start/stop/restart/enable/disable for `ay-custom-sub`, `ay-custom-web`, `x-ui`, `ay-sync-clients.timer`.
- **Live logs**: poll every 2s, download button.
- **Sync expiry dates** across sub-clients in X-UI database.
- **Merged login**: single template for login + dashboard.

## File layout
/root/ay-custom-sub/
├── ay_custom_sub.py
├── ay_custom_web.py
├── config.json
├── Ay_Custom_Configs
├── Ay_SSH_Users
├── Side_Collections.json
├── Side_Collections_Cache.json
├── requirements.txt
├── setup.sh
├── systemd/
│ ├── ay-custom-sub.service
│ ├── ay-custom-web.service
│ ├── ay-sync-clients.service
│ └── ay-sync-clients.timer
└── templates/
└── dashboard.html

text

## Installation

```bash
bash <(curl -sSL https://raw.githubusercontent.com/shammay-PC/Ay-v2Ray-Subscription/main/setup.sh) install
Menu
bash
ay-sub
Migration from v3
The installer keeps config.json, Ay_Custom_Configs, and Ay_SSH_Users.
On first run:

side_collection_urls in config.json is migrated into Side_Collections.json.

Ay_SSH_Users lines without : receive random passwords and are rewritten as username:password.

Raw config URL
Every extra port serves /Ay_Custom_Configs (plain text, no auth):

text
https://<domain>:<first_extra_port>/Ay_Custom_Configs
Health check
text
curl -k https://127.0.0.1:<port>/health   # {"status":"ok"}
curl    http://127.0.0.1:<port>/health   # connection refused
Troubleshooting
Service refuses to start.
Check journalctl -u ay-custom-web -n 50. The most common cause is missing certificates. Verify cert_path and key_path in config.json exist and are readable by root.

Client Sync doesn't work.
Verify /etc/x-ui/x-ui.db exists and sqlite3 is installed. Enable the timer manually:

bash
systemctl enable --now ay-sync-clients.timer
SSH user can't log in.
Check the user exists: id <username>. If it was deleted outside the panel, simply re-add it from the SSH Users tab.

License
All rights reserved by Ay Technic.

text

---

# ۸. `CHANGELOG.md`

```markdown
# Changelog

## 4.0.0

- Replaced `app.run()` with Waitress + SSL on all ports.
- Services now refuse to start without valid certificates.
- Added thread-safe in-memory config cache.
- Added `safe_get()` with timeout and exponential-backoff retry for all outbound HTTP.
- Added `/Ay_Custom_Configs` and `/health` routes on every sub port.
- Forward X-UI headers (`Subscription-Userinfo`, `Profile-Title`, ...) to clients.
- Merged `login.html` into `dashboard.html` (single template).
- Added Side Collections with plain/base64 support, rename placeholders and visibility toggles.
- Added per-config editor for manual configs (name view / raw view).
- Switched SSH user storage to `username:password` (automatic v3 migration).
- Removed forced `ayuser` creation and password rotation on restart.
- Added Client Sync, Service Restarter, Live Logs and Sync Expiry tabs.
- Replaced `Ay-Custom-Sub-Restarter` timer with `ay-sync-clients` (disabled by default).
- Added `requirements.txt` with `flask`, `requests`, `waitress`.
- Interactive `setup.sh` with prompts for domain, ports, credentials and cert paths.
