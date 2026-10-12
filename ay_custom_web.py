# === 新增：Client Sync 管理 ===

@app.route('/api/client-sync/status')
def client_sync_status():
    """Check syncClients status in x-ui.db."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        result = run_command(
            "sqlite3 /etc/x-ui/x-ui.db "
            "\"SELECT value FROM settings WHERE key='syncClients';\""
        )
        val = result.get('output', '').strip().lower()
        return jsonify({
            "status": "success",
            "enabled": val == "true",
            "raw_value": val
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/client-sync/enable', methods=['POST'])
def client_sync_enable():
    """Enable syncClients and schedule timer."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    duration = request.form.get('duration_minutes', '5')
    if not duration.isdigit() or int(duration) < 1:
        return jsonify({"status": "error", "message": "مدت زمان باید عدد مثبت باشد"}), 400

    run_command("sqlite3 /etc/x-ui/x-ui.db \"UPDATE settings SET value='true' WHERE key='syncClients';\"")
    run_command(f"systemctl start ay-sync-clients.timer")
    return jsonify({"status": "success", "message": f"همسان‌سازی برای {duration} دقیقه فعال شد"})


@app.route('/api/client-sync/disable', methods=['POST'])
def client_sync_disable():
    """Disable syncClients and stop timer."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    run_command("systemctl stop ay-sync-clients.timer")
    run_command("sqlite3 /etc/x-ui/x-ui.db \"UPDATE settings SET value='false' WHERE key='syncClients';\"")
    return jsonify({"status": "success", "message": "همسان‌سازی غیرفعال شد"})


# === 新增：Service 管理 ===

MANAGED_SERVICES = [
    "ay-custom-sub",
    "ay-custom-web",
    "x-ui",
    "ay-sync-clients.timer",
]


@app.route('/api/services/status')
def services_status():
    """Get status of all managed services."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    result = {}
    for svc in MANAGED_SERVICES:
        r = run_command(f"systemctl is-active {svc} 2>/dev/null || echo 'inactive'")
        enabled = run_command(f"systemctl is-enabled {svc} 2>/dev/null || echo 'disabled'")
        result[svc] = {
            "active": r.get('output', 'inactive').strip(),
            "enabled": enabled.get('output', 'disabled').strip(),
        }
    return jsonify({"status": "success", "services": result})


@app.route('/api/services/<action>/<service>', methods=['POST'])
def services_action(action, service):
    """Start/stop/restart/enable/disable a managed service."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    if service not in MANAGED_SERVICES:
        return jsonify({"status": "error", "message": "سرویس نامعتبر"}), 400
    if action not in ("start", "stop", "restart", "enable", "disable"):
        return jsonify({"status": "error", "message": "عملیات نامعتبر"}), 400

    r = run_command(f"systemctl {action} {service}")
    if r.get('success'):
        return jsonify({"status": "success", "message": f"{action} {service} انجام شد"})
    return jsonify({"status": "error", "message": r.get('error', 'خطا')}), 500


@app.route('/api/services/logs/<service>')
def service_logs(service):
    """Get last N lines of a service log."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    if service not in MANAGED_SERVICES:
        return jsonify({"status": "error", "message": "سرویس نامعتبر"}), 400
    lines = request.args.get('lines', '50')
    r = run_command(f"journalctl -u {service} -n {lines} --no-pager 2>/dev/null")
    return jsonify({
        "status": "success",
        "service": service,
        "logs": r.get('output', '')
    })


# === 新增：Side Collections 管理 ===

@app.route('/api/side-collections', methods=['GET'])
def get_side_collections():
    """List all side collections."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    cols = load_side_collections()
    return jsonify({"status": "success", "collections": cols})


@app.route('/api/side-collections', methods=['POST'])
def add_side_collection():
    """Add a new side collection."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    url = request.form.get('url', '').strip()
    if not url:
        return jsonify({"status": "error", "message": "لینک الزامی است"}), 400
    is_encoded = request.form.get('is_encoded', 'false').lower() == 'true'
    cols = load_side_collections()
    cols.append({
        "url": url,
        "global_rename": request.form.get('global_rename', ''),
        "configs": [],
        "is_encoded": is_encoded
    })
    save_side_collections(cols)
    # Trigger background fetch
    try:
        fetch_collection_configs(cols[-1], force=True)
    except Exception:
        pass
    return jsonify({"status": "success", "message": "مجموعه اضافه شد"})


@app.route('/api/side-collections/<int:idx>', methods=['DELETE'])
def delete_side_collection(idx):
    """Delete a side collection by index."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    cols = load_side_collections()
    if idx < 0 or idx >= len(cols):
        return jsonify({"status": "error", "message": "آیتم یافت نشد"}), 404
    url = cols[idx].get('url')
    cols.pop(idx)
    save_side_collections(cols)
    return jsonify({"status": "success", "message": "حذف شد"})


@app.route('/api/side-collections/<int:idx>/refresh', methods=['POST'])
def refresh_side_collection(idx):
    """Force refresh cache for a collection."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    cols = load_side_collections()
    if idx < 0 or idx >= len(cols):
        return jsonify({"status": "error", "message": "آیتم یافت نشد"}), 404
    fetch_collection_configs(cols[idx], force=True)
    return jsonify({"status": "success", "message": "کش بازخوانی شد"})


@app.route('/api/side-collections/<int:idx>/configs', methods=['GET'])
def get_collection_configs(idx):
    """Get individual configs from a collection with their overrides."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    cols = load_side_collections()
    if idx < 0 or idx >= len(cols):
        return jsonify({"status": "error", "message": "آیتم یافت نشد"}), 404
    col = cols[idx]
    raw = fetch_collection_configs(col)
    per_config = col.get('configs', [])
    result = []
    for i, cfg in enumerate(raw):
        override = per_config[i] if i < len(per_config) else {}
        result.append({
            "index": i,
            "config": cfg,
            "rename": override.get('rename', ''),
            "visible": override.get('visible', True)
        })
    return jsonify({"status": "success", "configs": result, "collection": col})


@app.route('/api/side-collections/<int:idx>/configs', methods=['POST'])
def update_collection_configs(idx):
    """Update per-config overrides (rename, visibility)."""
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    cols = load_side_collections()
    if idx < 0 or idx >= len(cols):
        return jsonify({"status": "error", "message": "آیتم یافت نشد"}), 404
    data = request.get_json()
    if data and 'configs' in data:
        cols[idx]['configs'] = data['configs']
    save_side_collections(cols)
    return jsonify({"status": "success", "message": "تنظیمات ذخیره شد"})


# === 新增：Sync Expiry Dates 独立路由（已存在于原代码，移到独立 tab） ===
# 保持原有 /sync-expiry-dates 不变，在 dashboard 中移到独立 tab


# === 修改：SSH 用户管理（新格式 username:password） ===

def load_ssh_users():
    """Load SSH users from file. Returns list of (username, password)."""
    if not os.path.exists(SSH_USERS_FILE):
        return []
    users = []
    with open(SSH_USERS_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if ':' in line:
                parts = line.split(':', 1)
                users.append((parts[0], parts[1]))
            else:
                # Migration: old format had just username
                users.append((line, None))
    return users


def save_ssh_users(users):
    """Save SSH users as username:password lines."""
    with open(SSH_USERS_FILE, 'w') as f:
        for username, password in users:
            if password:
                f.write(f"{username}:{password}\n")
            else:
                f.write(f"{username}:\n")