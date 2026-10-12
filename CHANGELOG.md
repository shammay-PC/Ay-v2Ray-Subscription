# Changelog

## v4.0.0 (2026-05-14)

### Fixed
- **Critical**: Service crash resolved. Replaced Flask dev server with Waitress production server.
- **Critical**: X-UI headers (Subscription-Userinfo, Profile-Title, etc.) now forwarded to clients.
- Added `/health` endpoint for monitoring.
- Thread-safe config cache to avoid repeated file reads.

### Added
- `/Ay_Custom_Configs` public subscription endpoint (base64-encoded).
- **Client Sync** tab with systemd timer control.
- **Live Logs** tab with 2-second polling and download button.
- **Service Restarter** panel (manage ay-custom-sub, ay-custom-web, x-ui, timer).
- **Side Collections** independent JSON file with per-config rename & visibility.
- **Sync Expiry Dates** dedicated tab.

### Changed
- SSH users: persistent `username:password` format (survives restarts).
- Merged login into dashboard (single-page experience).
- UI redesigned with Tailwind CSS + 3X-UI aesthetic.
- `setup.sh` now prompts for configuration during install.
- Removed mandatory `Ay-Custom-Sub-Restarter` timer (optional, disabled by default).

### Migration
- Automatic migration from `config.json#side_collection_urls`.
- Automatic migration from old `Ay_SSH_Users` format.
- No manual steps required.