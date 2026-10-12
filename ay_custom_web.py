#!/usr/bin/env python3
"""
Ay-Custom-Sub Web Panel v4.1.0

- Flask app served by Waitress behind mandatory SSL.
- Login is embedded in the dashboard template (no separate login page).
- Manages manual configs, SSH users, side collections, services,
  X-UI client sync, service restarter, and expiry-date synchronisation.
"""

import base64
import json
import os
import random
import re
import ssl
import string
import subprocess
import sys
import traceback

import requests
from flask import (
    Flask, Response, flash, jsonify, redirect, render_template,
    request, session, url_for,
)

PROJECT_DIR = "/root/ay-custom-sub"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
CUSTOM_CONFIGS_FILE = os.path.join(PROJECT_DIR, "Ay_Custom_Configs")
SSH_USERS_FILE = os.path.join(PROJECT_DIR, "Ay_SSH_Users")
SIDE_COLLECTIONS_FILE = os.path.join(PROJECT_DIR, "Side_Collections.json")
XUI_DB_PATH = "/etc/x-ui/x-ui.db"

RESTARTER_SERVICE = "Ay-Custom-Restarter.service"
RESTARTER_TIMER = "Ay-Custom-Restarter.timer"
RESTARTER_TIMER_PATH = f"/etc/systemd/system/{RESTARTER_TIMER}"
XUI_SYNC_SCRIPT = "/root/xui-sync-job.sh"
CRON_TAG = "xui-sync-job.sh"

app = Flask(__name__)
app.secret_key = "AyCustomSecretKey-v4"

temp_passwords = {}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def run_command(command):
    try:
        result = subprocess.run(
            command, shell=True, check=True, capture_output=True, text=True,
        )
        return {"success": True, "output": result.stdout.strip()}
    except subprocess.CalledProcessError as exc:
        return {"success": False, "error": (exc.stderr or str(exc)).strip()}


def generate_random_password(length=12):
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def load_config():
    default = {
        "username": "AyAdmin", "password": "AyPass",
        "web_port": 4321, "main_port": 8868,
        "domain": "sub.shammay.ir", "sub_path": "sub",
        "extra_ports": [6854, 8688],
        "cert_path": "/root/cert/cert.pem",
        "key_path": "/root/cert/privkey.pem",
        "side_collection_urls": [],
        "expired_message": "⛔ اشتراک شما منقضی شده، لطفا تمدید کنید ⛔",
        "no_sub_message": "⛔ شما اشتراکی در این سرویس ندارید ⛔",
        "dummy_config_template": (
            "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1"
            "?type=ws#{MESSAGE}"
        ),
        "restarter_interval_minutes": 10,
        "restarter_enabled": False,
        "xui_sync_enabled": False,
        "xui_sync_interval_minutes": 60,
    }
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(default, fh, indent=2, ensure_ascii=False)
        return default
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        for k, v in default.items():
            cfg.setdefault(k, v)
        return cfg
    except (json.JSONDecodeError, OSError):
        return default


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# SSH users
# ---------------------------------------------------------------------------
def load_ssh_users():
    if not os.path.exists(SSH_USERS_FILE):
        return []
    with open(SSH_USERS_FILE, "r", encoding="utf-8") as fh:
        raw_lines = [ln.strip() for ln in fh if ln.strip()]
    migrated = False
    users = []
    for line in raw_lines:
        if ":" in line:
            u, p = line.split(":", 1)
            users.append({"username": u.strip(), "password": p.strip()})
        else:
            users.append({"username": line, "password": generate_random_password()})
            migrated = True
    if migrated:
        save_ssh_users(users)
    return users


def save_ssh_users(users):
    with open(SSH_USERS_FILE, "w", encoding="utf-8") as fh:
        for u in users:
            fh.write(f"{u['username']}:{u['password']}\n")


def sync_ssh_users_on_startup():
    print("[ay-custom-web] Syncing SSH users...", flush=True)
    run_command("groupadd --force AyVpnUsers")
    users = load_ssh_users()
    if not users:
        return
    result = run_command("getent group AyVpnUsers | cut -d: -f4")
    system_users = (
        result["output"].split(",") if result["success"] and result["output"] else []
    )
    for entry in users:
        username = entry["username"]
        if username in system_users:
            continue
        print(f"[ay-custom-web] Recreating missing user '{username}'", flush=True)
        password = entry["password"]
        cmd = (
            f"useradd -m -s /bin/bash -g AyVpnUsers {username} && "
            f"echo '{username}:{password}' | chpasswd"
        )
        res = run_command(cmd)
        if res["success"]:
            temp_passwords[username] = password
        else:
            print(f"[ay-custom-web] Failed to create {username}: {res['error']}")


# ---------------------------------------------------------------------------
# Side collections file helpers
# ---------------------------------------------------------------------------
def load_side_collections():
    if not os.path.exists(SIDE_COLLECTIONS_FILE):
        return []
    try:
        with open(SIDE_COLLECTIONS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []


def save_side_collections(data):
    with open(SIDE_COLLECTIONS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# X-UI client sync helpers
# ---------------------------------------------------------------------------
def get_sync_clients_state():
    if not os.path.exists(XUI_DB_PATH):
        return False
    cmd = (
        f"sqlite3 {XUI_DB_PATH} \"SELECT value FROM settings "
        f"WHERE key='syncClients';\""
    )
    res = run_command(cmd)
    if not res["success"]:
        return False
    return res["output"].strip().lower() in ("true", "1", "yes")


def set_sync_clients_state(value: bool):
    if not os.path.exists(XUI_DB_PATH):
        return False, "x-ui database not found"
    v = "true" if value else "false"
    cmd = (
        f"sqlite3 {XUI_DB_PATH} \"INSERT INTO settings (key, value) "
        f"VALUES ('syncClients', '{v}') "
        f"ON CONFLICT(key) DO UPDATE SET value='{v}';\""
    )
    res = run_command(cmd)
    return (True, "ok") if res["success"] else (False, res["error"])


def get_cron_interval():
    """Return the current cron interval (minutes) for xui-sync-job.sh, or None."""
    res = run_command("crontab -l 2>/dev/null | grep xui-sync-job.sh")
    if not res["success"] or not res["output"]:
        return None
    m = re.search(r"^\s*(\S+)\s+\S+\s+\S+\s+\S+\s+\S+", res["output"])
    if not m:
        return None
    expr = m.group(1)
    if expr.startswith("*/"):
        try:
            return int(expr[2:])
        except ValueError:
            return None
    if expr.isdigit():
        return 60
    return None


def get_cron_enabled():
    res = run_command("crontab -l 2>/dev/null | grep xui-sync-job.sh")
    return res["success"] and bool(res["output"])


def _install_cron_line(interval):
    """Replace any existing xui-sync-job.sh cron line with a new one."""
    if interval == 60:
        expr = "0 * * * *"
    else:
        expr = f"*/{interval} * * * *"
    new_line = f"{expr} /bin/bash {XUI_SYNC_SCRIPT}"
    # Get current crontab, filter out old lines.
    res = run_command("crontab -l 2>/dev/null")
    current = res["output"] if res["success"] else ""
    kept = [ln for ln in current.splitlines() if CRON_TAG not in ln]
    kept.append(new_line)
    payload = "\n".join(kept) + "\n"
    p = subprocess.run(["crontab", "-"], input=payload, text=True,
                       capture_output=True)
    return p.returncode == 0


def _remove_cron_line():
    res = run_command("crontab -l 2>/dev/null")
    current = res["output"] if res["success"] else ""
    kept = [ln for ln in current.splitlines() if CRON_TAG not in ln]
    payload = "\n".join(kept) + ("\n" if kept else "")
    p = subprocess.run(["crontab", "-"], input=payload, text=True,
                       capture_output=True)
    return p.returncode == 0


# ---------------------------------------------------------------------------
# Restarter helpers (systemd timer)
# ---------------------------------------------------------------------------
def get_restarter_status():
    active = run_command(f"systemctl is-active {RESTARTER_TIMER}")["output"] or "inactive"
    enabled = run_command(f"systemctl is-enabled {RESTARTER_TIMER}")["output"] or "disabled"
    return active == "active", enabled == "enabled"


def set_restarter_timer_interval(minutes):
    if not os.path.exists(RESTARTER_TIMER_PATH):
        return False, "Restarter timer file not found"
    cmd = (
        f"sed -i 's/^OnUnitActiveSec=.*/OnUnitActiveSec={minutes}min/' "
        f"{RESTARTER_TIMER_PATH}"
    )
    res = run_command(cmd)
    if not res["success"]:
        return False, res["error"]
    run_command("systemctl daemon-reload")
    return True, "ok"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def login_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/", methods=["GET", "POST"])
def login():
    config = load_config()
    if request.method == "POST":
        if (
            request.form.get("username") == config.get("username")
            and request.form.get("password") == config.get("password")
        ):
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("نام کاربری یا رمز عبور اشتباه است", "danger")
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return render_template("dashboard.html", settings=config, logged_in=False)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    config = load_config()

    if request.method == "POST":
        config["username"] = request.form.get("username", config["username"])
        config["password"] = request.form.get("password", config["password"])
        config["web_port"] = int(request.form.get("web_port", config["web_port"]))
        config["main_port"] = int(request.form.get("main_port", config["main_port"]))
        config["extra_ports"] = [
            int(p.strip())
            for p in request.form.get("extra_ports", "").split(",")
            if p.strip().isdigit()
        ]
        config["domain"] = request.form.get("domain", config["domain"])
        config["sub_path"] = request.form.get("sub_path", config["sub_path"])
        config["cert_path"] = request.form.get("cert_path", config["cert_path"])
        config["key_path"] = request.form.get("key_path", config["key_path"])
        config["expired_message"] = request.form.get(
            "expired_message", config["expired_message"]
        )
        config["no_sub_message"] = request.form.get(
            "no_sub_message", config["no_sub_message"]
        )
        save_config(config)
        flash("تنظیمات ذخیره شد. برای اعمال کامل، سرویس را ریستارت کنید.", "success")
        return redirect(url_for("dashboard"))

    custom_configs = []
    if os.path.exists(CUSTOM_CONFIGS_FILE):
        with open(CUSTOM_CONFIGS_FILE, "r", encoding="utf-8") as fh:
            custom_configs = [ln.strip() for ln in fh if ln.strip()]

    ssh_users = load_ssh_users()
    side_collections = load_side_collections()
    sync_enabled = get_sync_clients_state()

    first_port = config["extra_ports"][0] if config["extra_ports"] else 6854
    raw_configs_url = f"https://{config['domain']}:{first_port}/Ay_Custom_Configs"

    return render_template(
        "dashboard.html",
        logged_in=True,
        custom_configs=custom_configs,
        ssh_users=[u["username"] for u in ssh_users],
        settings=config,
        side_collections=side_collections,
        sync_enabled=sync_enabled,
        raw_configs_url=raw_configs_url,
    )


# --- Manual configs --------------------------------------------------------
@app.route("/add-config", methods=["POST"])
@login_required
def add_config():
    configs = request.form.getlist("new_config")
    if configs:
        with open(CUSTOM_CONFIGS_FILE, "a", encoding="utf-8") as fh:
            for cfg in configs:
                if cfg.strip():
                    fh.write(cfg.strip() + "\n")
    return "", 204


@app.route("/delete-config", methods=["POST"])
@login_required
def delete_config():
    index = int(request.form.get("index", -1))
    if index >= 0 and os.path.exists(CUSTOM_CONFIGS_FILE):
        with open(CUSTOM_CONFIGS_FILE, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if index < len(lines):
            lines.pop(index)
            with open(CUSTOM_CONFIGS_FILE, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
    return "", 204


@app.route("/delete-all-configs", methods=["POST"])
@login_required
def delete_all_configs():
    try:
        open(CUSTOM_CONFIGS_FILE, "w").close()
        return jsonify({"status": "success", "message": "تمام کانفیگ‌های دستی حذف شدند."})
    except OSError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/update-config", methods=["POST"])
@login_required
def update_config():
    index = int(request.form.get("index", -1))
    new_value = (request.form.get("config") or "").strip()
    if index < 0 or not new_value:
        return jsonify({"status": "error", "message": "پارامتر نامعتبر"}), 400
    if not os.path.exists(CUSTOM_CONFIGS_FILE):
        return jsonify({"status": "error", "message": "فایل یافت نشد"}), 404
    with open(CUSTOM_CONFIGS_FILE, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    if index >= len(lines):
        return jsonify({"status": "error", "message": "ایندکس خارج از محدوده"}), 400
    lines[index] = new_value + "\n"
    with open(CUSTOM_CONFIGS_FILE, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    return jsonify({"status": "success", "message": "کانفیگ ویرایش شد."})


# --- Side collections ------------------------------------------------------
@app.route("/side-collections", methods=["GET"])
@login_required
def list_side_collections():
    return jsonify(load_side_collections())


@app.route("/side-collections/add", methods=["POST"])
@login_required
def add_side_collection():
    url = (request.form.get("url") or "").strip()
    ctype = request.form.get("type", "plain")
    if not url:
        return jsonify({"status": "error", "message": "URL لازم است"}), 400
    if ctype not in ("plain", "base64"):
        return jsonify({"status": "error", "message": "نوع نامعتبر"}), 400

    data = load_side_collections()
    data.append({
        "url": url, "type": ctype, "global_rename": None,
        "configs": [], "last_fetch": 0,
    })
    save_side_collections(data)
    return jsonify({"status": "success", "message": "مجموعه اضافه شد."})


@app.route("/side-collections/delete", methods=["POST"])
@login_required
def delete_side_collection():
    idx = int(request.form.get("index", -1))
    data = load_side_collections()
    if 0 <= idx < len(data):
        data.pop(idx)
        save_side_collections(data)
        return jsonify({"status": "success", "message": "حذف شد."})
    return jsonify({"status": "error", "message": "ایندکس نامعتبر"}), 400


@app.route("/side-collections/refresh", methods=["POST"])
@login_required
def refresh_side_collection():
    idx = int(request.form.get("index", -1))
    data = load_side_collections()
    if not (0 <= idx < len(data)):
        return jsonify({"status": "error", "message": "ایندکس نامعتبر"}), 400

    entry = data[idx]
    try:
        resp = requests.get(entry["url"], timeout=10, verify=False)
        if resp.status_code != 200:
            return jsonify({
                "status": "error",
                "message": f"HTTP {resp.status_code}",
            }), 502
        text = resp.text.strip()
        if entry.get("type") == "base64":
            text = base64.b64decode(text).decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        existing = {c["config"]: c for c in entry.get("configs", [])}
        entry["configs"] = [
            {
                "config": cfg,
                "rename": existing.get(cfg, {}).get("rename"),
                "visible": existing.get(cfg, {}).get("visible", True),
            }
            for cfg in lines
        ]
        import time as _t
        entry["last_fetch"] = _t.time()
        data[idx] = entry
        save_side_collections(data)
        return jsonify({
            "status": "success",
            "message": f"{len(lines)} کانفیگ دریافت شد.",
            "configs": entry["configs"],
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/side-collections/update", methods=["POST"])
@login_required
def update_side_collection():
    idx = int(request.form.get("index", -1))
    data = load_side_collections()
    if not (0 <= idx < len(data)):
        return jsonify({"status": "error", "message": "ایندکس نامعتبر"}), 400

    entry = data[idx]
    entry["global_rename"] = request.form.get("global_rename") or None

    payload = request.form.get("configs")
    if payload:
        try:
            updated = json.loads(payload)
            current = {c["config"]: c for c in entry.get("configs", [])}
            for item in updated:
                cfg = item.get("config")
                if cfg in current:
                    if "rename" in item:
                        current[cfg]["rename"] = item["rename"] or None
                    if "visible" in item:
                        current[cfg]["visible"] = bool(item["visible"])
            entry["configs"] = list(current.values())
        except json.JSONDecodeError:
            pass

    data[idx] = entry
    save_side_collections(data)
    return jsonify({"status": "success", "message": "ذخیره شد."})


# --- SSH users -------------------------------------------------------------
@app.route("/add-ssh-user", methods=["POST"])
@login_required
def add_ssh_user():
    username = (request.form.get("username") or "").strip()
    if not username or not username.isalnum() or len(username) < 3:
        return jsonify({
            "status": "error",
            "message": "نام کاربری باید حداقل ۳ کاراکتر و فقط حروف/اعداد انگلیسی باشد.",
        }), 400

    users = load_ssh_users()
    if any(u["username"] == username for u in users):
        return jsonify({"status": "error", "message": "این نام کاربری قبلاً استفاده شده."}), 400

    password = generate_random_password()
    cmd = (
        f"useradd -m -s /bin/bash -g AyVpnUsers {username} && "
        f"echo '{username}:{password}' | chpasswd"
    )
    res = run_command(cmd)
    if not res["success"]:
        return jsonify({"status": "error", "message": res["error"]}), 500

    users.append({"username": username, "password": password})
    save_ssh_users(users)
    temp_passwords[username] = password
    return jsonify({
        "status": "success",
        "message": f"کاربر {username} ساخته شد.",
        "username": username,
        "password": password,
    })


@app.route("/delete-ssh-user", methods=["POST"])
@login_required
def delete_ssh_user():
    username = request.form.get("username")
    users = load_ssh_users()
    if not any(u["username"] == username for u in users):
        return jsonify({"status": "error", "message": "کاربر یافت نشد."}), 404

    res = run_command(f"userdel -r {username}")
    users = [u for u in users if u["username"] != username]
    save_ssh_users(users)
    temp_passwords.pop(username, None)

    if res["success"] or "does not exist" in res.get("error", ""):
        return jsonify({"status": "success", "message": f"کاربر {username} حذف شد."})
    return jsonify({"status": "error", "message": res["error"]}), 500


@app.route("/change-ssh-password", methods=["POST"])
@login_required
def change_ssh_password():
    username = request.form.get("username")
    password = request.form.get("password")
    if not password or len(password) < 6:
        return jsonify({"status": "error", "message": "رمز باید حداقل ۶ کاراکتر باشد."}), 400

    res = run_command(f"echo '{username}:{password}' | chpasswd")
    if not res["success"]:
        return jsonify({"status": "error", "message": res["error"]}), 500

    users = load_ssh_users()
    for u in users:
        if u["username"] == username:
            u["password"] = password
            break
    save_ssh_users(users)
    temp_passwords[username] = password
    return jsonify({"status": "success", "message": "رمز عبور تغییر کرد."})


@app.route("/get-ssh-password", methods=["POST"])
@login_required
def get_ssh_password():
    username = request.form.get("username")
    users = load_ssh_users()
    for u in users:
        if u["username"] == username:
            return jsonify({"status": "success", "password": u["password"]})
    return jsonify({"status": "error", "message": "کاربر یافت نشد."}), 404


# --- Services --------------------------------------------------------------
ALLOWED_SERVICES = (
    "ay-custom-sub",
    "ay-custom-web",
    "x-ui",
)


@app.route("/service/<name>/<action>", methods=["POST"])
@login_required
def service_action(name, action):
    if name not in ALLOWED_SERVICES:
        return jsonify({"status": "error", "message": "سرویس نامعتبر"}), 400
    if action not in ("start", "stop", "restart", "enable", "disable"):
        return jsonify({"status": "error", "message": "عملیات نامعتبر"}), 400

    res = run_command(f"systemctl {action} {name}")
    if res["success"]:
        return jsonify({"status": "success", "message": f"{action} {name} انجام شد."})
    return jsonify({"status": "error", "message": res["error"]}), 500


@app.route("/service-status", methods=["GET"])
@login_required
def service_status():
    out = {}
    for svc in ("ay-custom-sub", "ay-custom-web", "x-ui"):
        res = run_command(
            f"systemctl is-active {svc}; systemctl is-enabled {svc}"
        )
        parts = (res.get("output") or "").splitlines()
        out[svc] = {
            "active": parts[0].strip() if len(parts) > 0 else "unknown",
            "enabled": parts[1].strip() if len(parts) > 1 else "unknown",
        }
    return jsonify(out)


@app.route("/logs/<service>", methods=["GET"])
@login_required
def logs(service):
    if service not in ALLOWED_SERVICES:
        return jsonify({"status": "error", "message": "سرویس نامعتبر"}), 400
    lines = min(int(request.args.get("lines", 200)), 500)
    res = run_command(f"journalctl -u {service} -n {lines} --no-pager")
    if res["success"]:
        return Response(res["output"], mimetype="text/plain; charset=utf-8")
    return Response(f"خطا: {res['error']}", status=500, mimetype="text/plain")


# --- X-UI client sync ------------------------------------------------------
@app.route("/client-sync", methods=["GET"])
@login_required
def client_sync_state():
    return jsonify({"enabled": get_sync_clients_state()})


@app.route("/client-sync", methods=["POST"])
@login_required
def client_sync_update():
    enable = request.form.get("enable", "false").lower() == "true"
    interval = int(request.form.get("interval", 5))
    if interval < 1 or interval > 1440:
        return jsonify({"status": "error", "message": "بازه باید بین ۱ تا ۱۴۴۰ دقیقه باشد."}), 400

    ok, msg = set_sync_clients_state(enable)
    if not ok:
        return jsonify({"status": "error", "message": msg}), 500
    return jsonify({"status": "success", "message": "تنظیمات Client Sync ذخیره شد."})


# --- Restarter (new) -------------------------------------------------------
@app.route("/restarter", methods=["GET"])
@login_required
def restarter_state():
    config = load_config()
    active, enabled = get_restarter_status()
    return jsonify({
        "enabled": enabled,
        "active": active,
        "interval": config.get("restarter_interval_minutes", 10),
    })


@app.route("/restarter", methods=["POST"])
@login_required
def restarter_update():
    enable = request.form.get("enable", "false").lower() == "true"
    interval = int(request.form.get("interval", 10))
    if interval < 1 or interval > 1440:
        return jsonify({"status": "error", "message": "بازه باید بین ۱ تا ۱۴۴۰ دقیقه باشد."}), 400

    config = load_config()
    config["restarter_interval_minutes"] = interval
    config["restarter_enabled"] = enable
    save_config(config)

    ok, msg = set_restarter_timer_interval(interval)
    if not ok:
        return jsonify({"status": "error", "message": msg}), 500

    if enable:
        run_command(f"systemctl enable --now {RESTARTER_TIMER}")
    else:
        run_command(f"systemctl disable --now {RESTARTER_TIMER}")

    return jsonify({"status": "success", "message": "تنظیمات Restarter ذخیره شد."})


# --- X-UI sync with cron (new) --------------------------------------------
@app.route("/xui-sync", methods=["GET"])
@login_required
def xui_sync_state():
    sync_clients = get_sync_clients_state()
    cron_enabled = get_cron_enabled()
    interval = get_cron_interval() or 60
    return jsonify({
        "sync_clients": sync_clients,
        "cron_enabled": cron_enabled,
        "interval": interval,
    })


@app.route("/xui-sync", methods=["POST"])
@login_required
def xui_sync_update():
    enable = request.form.get("enable", "false").lower() == "true"
    interval = int(request.form.get("interval", 60))
    if interval < 1 or interval > 1440:
        return jsonify({"status": "error", "message": "بازه باید بین ۱ تا ۱۴۴۰ دقیقه باشد."}), 400

    if enable:
        if not _install_cron_line(interval):
            return jsonify({"status": "error", "message": "خطا در نصب cron"}), 500
    else:
        _remove_cron_line()

    config = load_config()
    config["xui_sync_enabled"] = enable
    config["xui_sync_interval_minutes"] = interval
    save_config(config)

    return jsonify({"status": "success", "message": "تنظیمات X-UI Sync ذخیره شد."})


@app.route("/xui-sync/run", methods=["POST"])
@login_required
def xui_sync_run_now():
    if not os.path.exists(XUI_SYNC_SCRIPT):
        return jsonify({"status": "error", "message": "اسکریپت یافت نشد."}), 404
    # Fire-and-forget: run in background so the HTTP request returns quickly.
    subprocess.Popen(["/bin/bash", XUI_SYNC_SCRIPT])
    return jsonify({"status": "success", "message": "اسکریپت در پس‌زمینه اجرا شد."})


# --- Expiry sync -----------------------------------------------------------
@app.route("/sync-expiry-dates", methods=["POST"])
@login_required
def sync_expiry_dates():
    if not os.path.exists(XUI_DB_PATH):
        return jsonify({"status": "error", "message": "پایگاه داده x-ui یافت نشد."}), 404

    cmd = f"""
    sqlite3 {XUI_DB_PATH} "
    UPDATE client_traffics SET expiry_time = (
        SELECT parent.expiry_time FROM client_traffics AS parent
        WHERE client_traffics.email LIKE '%' || parent.email || '%'
          AND client_traffics.email != parent.email
        ORDER BY LENGTH(parent.email) DESC LIMIT 1
    )
    WHERE EXISTS (
        SELECT 1 FROM client_traffics AS parent
        WHERE client_traffics.email LIKE '%' || parent.email || '%'
          AND client_traffics.email != parent.email
    );"
    """
    res = run_command(cmd)
    if res["success"]:
        return jsonify({"status": "success", "message": "تاریخ انقضاء همسان‌سازی شد."})
    return jsonify({"status": "error", "message": res["error"]}), 500


# --- Misc ------------------------------------------------------------------
@app.route("/restart-services", methods=["POST"])
@login_required
def restart_services():
    try:
        subprocess.run(["systemctl", "restart", "ay-custom-sub"], check=True)
        subprocess.run(["systemctl", "restart", "ay-custom-web"], check=True)
        return "Services restarted successfully.", 200
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return f"Failed to restart services: {exc}", 500


@app.route("/check-sub", methods=["POST"])
@login_required
def check_sub():
    sub_url = request.form.get("sub_url")
    if not sub_url:
        return "No URL provided"
    try:
        res = requests.get(sub_url, timeout=7, verify=False)
        if res.status_code != 200:
            return f"خطا در دریافت لینک: {res.status_code}"
        content = res.text.strip()
        try:
            content = base64.b64decode(content).decode("utf-8")
        except Exception:
            pass
        configs = [ln.strip() for ln in content.splitlines() if ln.strip()]
        return "\n".join(configs)
    except Exception as exc:
        return f"خطا: {exc}"


# ---------------------------------------------------------------------------
# SSL + server bootstrap
# ---------------------------------------------------------------------------
def main():
    sync_ssh_users_on_startup()
    config = load_config()
    port = int(config.get("web_port", 4321))
    cert_path = config.get("cert_path") or "/root/cert/cert.pem"
    key_path = config.get("key_path") or "/root/cert/privkey.pem"

    if not os.path.exists(cert_path) or not os.path.exists(key_path):
        print(f"[ay-custom-web] FATAL: SSL cert/key not found. cert={cert_path} key={key_path}", flush=True)
        sys.exit(1)

    print(f"[ay-custom-web] HTTPS panel on port {port}", flush=True)
    app.run(
        host="0.0.0.0",
        port=port,
        ssl_context=(cert_path, key_path),
        debug=False,
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ay-custom-web] Fatal: {exc}\n{traceback.format_exc()}", flush=True)
        sys.exit(1)