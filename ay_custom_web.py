#!/usr/bin/env python3
"""
Ay-Custom-Sub Web Panel v4.0.0

单文件 Flask 应用，包含所有路由：
- 登录（合并至 dashboard）
- 手工配置 CRUD
- SSH 用户管理（持久化密码）
- 侧边集合管理
- Client Sync + Service Restarter
- Live Logs
- Sync Expiry Dates
"""

import base64
import json
import os
import random
import re
import string
import subprocess
import threading
import time

import requests
from flask import (
    Flask, Response, jsonify, redirect,
    render_template, request, session, url_for,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PROJECT_DIR = "/root/ay-custom-sub"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
CUSTOM_CONFIGS_PATH = os.path.join(PROJECT_DIR, "Ay_Custom_Configs")
SSH_USERS_PATH = os.path.join(PROJECT_DIR, "Ay_SSH_Users")
SIDE_COLLECTIONS_PATH = os.path.join(PROJECT_DIR, "Side_Collections.json")
XUI_DB_PATH = "/etc/x-ui/x-ui.db"
XUI_SETTINGS_TABLE = "settings"
XUI_SYNC_CLIENTS_KEY = "syncClients"

SYNC_TIMER_UNIT = "ay-sync-clients.timer"
SYNC_SERVICE_UNIT = "ay-sync-clients.service"
SERVICES = ["ay-custom-sub", "ay-custom-web", "x-ui", SYNC_TIMER_UNIT]

app = Flask("Ay-Custom-Web")
app.secret_key = os.environ.get("AY_SECRET_KEY", "AyCustomSecretKey-v4")

# 内存中的临时密码（用于展示，重启后丢失；真实密码在 Ay_SSH_Users 中）
temp_passwords: dict[str, str] = {}
_temp_lock = threading.Lock()

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def run_cmd(command: str) -> dict:
    """执行 shell 命令并返回结果。"""
    try:
        result = subprocess.run(
            command, shell=True, check=True,
            capture_output=True, text=True, timeout=30,
        )
        return {"success": True, "output": result.stdout.strip()}
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": e.stderr.strip() or str(e)}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out"}
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}


def gen_password(length: int = 12) -> str:
    """生成随机密码。"""
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def load_config() -> dict:
    """读取 config.json，带默认值回退。"""
    defaults = {
        "username": "AyAdmin", "password": "AyPass",
        "web_port": 4321, "main_port": 8868,
        "extra_ports": [6854, 8688],
        "domain": "www.shammay.ir", "sub_path": "sub",
        "cert_path": "", "key_path": "",
        "expired_message": "⛔ اشتراک شما منقضی شده، لطفا تمدید کنید ⛔",
        "no_sub_message": "⛔ شما اشتراکی در این سرویس ندارید ⛔",
        "dummy_config_template": (
            "vless://00000000-0000-0000-0000-000000000000"
            "@go-to.shammay.ir:1?type=ws#{MESSAGE}"
        ),
    }
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(defaults, f, ensure_ascii=False, indent=2)
        return defaults

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        return cfg
    except (json.JSONDecodeError, IOError):
        return defaults


def save_config(cfg: dict):
    """写入 config.json。"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# SSH 用户
# ---------------------------------------------------------------------------

def load_ssh_users() -> list[tuple[str, str]]:
    """读取 Ay_SSH_Users，返回 [(username, password), ...]。"""
    if not os.path.exists(SSH_USERS_PATH):
        return []
    users = []
    with open(SSH_USERS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                parts = line.split(":", 1)
                users.append((parts[0], parts[1]))
            else:
                # 旧格式：只有用户名，生成新密码
                users.append((line, gen_password()))
    return users


def save_ssh_users(users: list[tuple[str, str]]):
    """写入 Ay_SSH_Users。"""
    with open(SSH_USERS_PATH, "w", encoding="utf-8") as f:
        for name, pw in users:
            f.write(f"{name}:{pw}\n")


def sync_ssh_users_startup():
    """启动时同步：如果用户在文件里但系统里没有，用保存的密码重建。"""
    print("[Ay-Web] Syncing SSH users...")
    run_cmd("groupadd --force AyVpnUsers")

    users = load_ssh_users()
    if not users:
        print("[Ay-Web] No SSH users in file.")
        return

    result = run_cmd("getent group AyVpnUsers | cut -d: -f4")
    system_users = set(result["output"].split(",")) if result["success"] and result["output"] else set()

    changed = False
    for username, password in users:
        if username not in system_users:
            print(f"[Ay-Web] Recreating user '{username}'...")
            cmd = (
                f"useradd -m -s /bin/bash -g AyVpnUsers {username} && "
                f"echo '{username}:{password}' | chpasswd"
            )
            r = run_cmd(cmd)
            if r["success"]:
                with _temp_lock:
                    temp_passwords[username] = password
                print(f"[Ay-Web] User '{username}' recreated.")
            else:
                print(f"[Ay-Web] Failed to recreate '{username}': {r['error']}")

    if changed:
        save_ssh_users(users)


# ---------------------------------------------------------------------------
# 手工配置
# ---------------------------------------------------------------------------

def read_custom_configs() -> list[str]:
    """读取手工配置。"""
    if not os.path.exists(CUSTOM_CONFIGS_PATH):
        return []
    with open(CUSTOM_CONFIGS_PATH, "r", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


def write_custom_configs(configs: list[str]):
    """写入手工配置。"""
    with open(CUSTOM_CONFIGS_PATH, "w", encoding="utf-8") as f:
        for line in configs:
            if line.strip():
                f.write(line.strip() + "\n")


# ---------------------------------------------------------------------------
# 侧边集合
# ---------------------------------------------------------------------------

def load_side_collections() -> list:
    """加载 Side_Collections.json。"""
    if not os.path.exists(SIDE_COLLECTIONS_PATH):
        return []
    try:
        with open(SIDE_COLLECTIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def save_side_collections(data: list):
    """保存 Side_Collections.json。"""
    with open(SIDE_COLLECTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# X-UI 设置
# ---------------------------------------------------------------------------

def _sqlite_query(db: str, sql: str) -> dict:
    """执行 sqlite3 查询。"""
    return run_cmd(f'sqlite3 {db} "{sql}"')


def get_xui_setting(key: str) -> str | None:
    """读取 X-UI settings 表中的某个键。"""
    r = _sqlite_query(XUI_DB_PATH, f"SELECT value FROM {XUI_SETTINGS_TABLE} WHERE key='{key}';")
    if r["success"] and r["output"]:
        return r["output"]
    return None


def set_xui_setting(key: str, value: str) -> bool:
    """写入 X-UI settings 表。如果键存在则 UPDATE，否则 INSERT。"""
    existing = get_xui_setting(key)
    if existing is not None:
        sql = f"UPDATE {XUI_SETTINGS_TABLE} SET value='{value}' WHERE key='{key}';"
    else:
        sql = f"INSERT INTO {XUI_SETTINGS_TABLE} (key, value) VALUES ('{key}', '{value}');"
    return _sqlite_query(XUI_DB_PATH, sql)["success"]


def get_sync_clients_status() -> bool:
    """获取 syncClients 状态。"""
    val = get_xui_setting(XUI_SYNC_CLIENTS_KEY)
    return val is not None and val.lower() == "true"


# ---------------------------------------------------------------------------
# 路由：认证
# ---------------------------------------------------------------------------

def _require_login():
    """检查是否已登录。"""
    return session.get("logged_in", False)


@app.route("/")
def index():
    """主页面。未登录显示登录表单，已登录显示 dashboard。"""
    if _require_login():
        return _render_dashboard()
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    """处理登录。"""
    cfg = load_config()
    username = request.form.get("username", "")
    password = request.form.get("password", "")

    if username == cfg.get("username") and password == cfg.get("password"):
        session["logged_in"] = True
        return redirect(url_for("index"))

    return render_template("login.html", error="نام کاربری یا رمز عبور اشتباه است")


@app.route("/logout")
def logout():
    """登出。"""
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# 路由：Dashboard 数据
# ---------------------------------------------------------------------------

def _render_dashboard():
    """渲染 dashboard 页面，传入所有必要数据。"""
    cfg = load_config()
    custom_configs = read_custom_configs()
    ssh_users_raw = load_ssh_users()
    side_collections = load_side_collections()

    # 构建 /Ay_Custom_Configs 的 URL
    domain = cfg.get("domain", "")
    extra_ports = cfg.get("extra_ports", [])
    first_port = extra_ports[0] if extra_ports else 6854
    custom_configs_url = f"https://{domain}:{first_port}/Ay_Custom_Configs"

    return render_template(
        "dashboard.html",
        settings=cfg,
        custom_configs=custom_configs,
        ssh_users=[u[0] for u in ssh_users_raw],
        side_collections=side_collections,
        custom_configs_url=custom_configs_url,
        sync_clients_enabled=get_sync_clients_status(),
    )


# ---------------------------------------------------------------------------
# 路由：配置
# ---------------------------------------------------------------------------

@app.route("/api/config", methods=["POST"])
def api_save_config():
    """保存配置。"""
    if not _require_login():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    cfg = load_config()
    form = request.form
    cfg["username"] = form.get("username", cfg["username"])
    cfg["password"] = form.get("password", cfg["password"])
    cfg["web_port"] = int(form.get("web_port", cfg["web_port"]))
    cfg["main_port"] = int(form.get("main_port", cfg["main_port"]))
    cfg["extra_ports"] = [
        int(p.strip()) for p in form.get("extra_ports", "").split(",") if p.strip().isdigit()
    ]
    cfg["domain"] = form.get("domain", cfg["domain"])
    cfg["sub_path"] = form.get("sub_path", cfg["sub_path"])
    cfg["cert_path"] = form.get("cert_path", cfg["cert_path"])
    cfg["key_path"] = form.get("key_path", cfg["key_path"])
    cfg["expired_message"] = form.get("expired_message", cfg["expired_message"])
    cfg["no_sub_message"] = form.get("no_sub_message", cfg["no_sub_message"])
    save_config(cfg)
    return jsonify({"status": "success", "message": "تنظیمات ذخیره شد"})


# ---------------------------------------------------------------------------
# 路由：手工配置
# ---------------------------------------------------------------------------

@app.route("/api/custom-configs", methods=["GET"])
def api_get_custom_configs():
    """获取所有手工配置。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401
    return jsonify({"status": "success", "configs": read_custom_configs()})


@app.route("/api/custom-configs", methods=["POST"])
def api_add_custom_config():
    """添加手工配置。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401
    lines = request.form.getlist("config")
    if lines:
        configs = read_custom_configs()
        for line in lines:
            if line.strip():
                configs.append(line.strip())
        write_custom_configs(configs)
    return jsonify({"status": "success"})


@app.route("/api/custom-configs/<int:index>", methods=["DELETE"])
def api_delete_custom_config(index: int):
    """删除指定索引的手工配置。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401
    configs = read_custom_configs()
    if 0 <= index < len(configs):
        configs.pop(index)
        write_custom_configs(configs)
    return jsonify({"status": "success"})


@app.route("/api/custom-configs/all", methods=["DELETE"])
def api_delete_all_custom_configs():
    """删除所有手工配置。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401
    write_custom_configs([])
    return jsonify({"status": "success", "message": "所有手工配置已删除"})


@app.route("/api/custom-configs/<int:index>", methods=["PUT"])
def api_update_custom_config(index: int):
    """更新指定索引的手工配置。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401
    new_value = request.json.get("config", "") if request.is_json else request.form.get("config", "")
    configs = read_custom_configs()
    if 0 <= index < len(configs) and new_value.strip():
        configs[index] = new_value.strip()
        write_custom_configs(configs)
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Invalid index or value"}), 400


# ---------------------------------------------------------------------------
# 路由：SSH 用户
# ---------------------------------------------------------------------------

@app.route("/api/ssh-users", methods=["GET"])
def api_get_ssh_users():
    """获取 SSH 用户列表。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401
    users = load_ssh_users()
    return jsonify({
        "status": "success",
        "users": [{"username": u, "has_temp_password": u in temp_passwords} for u, _ in users],
    })


@app.route("/api/ssh-users", methods=["POST"])
def api_add_ssh_user():
    """添加 SSH 用户。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    username = request.form.get("username", "").strip()
    if not username or not username.isalnum() or len(username) < 3:
        return jsonify({
            "status": "error",
            "message": "نام کاربری باید حداقل ۳ کاراکتر و فقط شامل حروف و اعداد انگلیسی باشد.",
        }), 400

    users = load_ssh_users()
    if any(u == username for u, _ in users):
        return jsonify({"status": "error", "message": "این نام کاربری قبلاً استفاده شده است."}), 400

    password = gen_password()
    cmd = (
        f"useradd -m -s /bin/bash -g AyVpnUsers {username} && "
        f"echo '{username}:{password}' | chpasswd"
    )
    r = run_cmd(cmd)
    if not r["success"]:
        return jsonify({"status": "error", "message": r["error"]}), 500

    users.append((username, password))
    save_ssh_users(users)
    with _temp_lock:
        temp_passwords[username] = password

    return jsonify({
        "status": "success",
        "message": f"کاربر {username} ساخته شد",
        "username": username,
        "password": password,
    })


@app.route("/api/ssh-users/<username>", methods=["DELETE"])
def api_delete_ssh_user(username: str):
    """删除 SSH 用户。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    users = load_ssh_users()
    if not any(u == username for u, _ in users):
        return jsonify({"status": "error", "message": "کاربر یافت نشد"}), 404

    run_cmd(f"userdel -r {username}")
    users = [(u, p) for u, p in users if u != username]
    save_ssh_users(users)
    with _temp_lock:
        temp_passwords.pop(username, None)

    return jsonify({"status": "success", "message": f"کاربر {username} حذف شد"})


@app.route("/api/ssh-users/<username>/password", methods=["GET"])
def api_get_ssh_password(username: str):
    """获取 SSH 密码（优先从内存，否则从文件）。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    users = load_ssh_users()
    for u, p in users:
        if u == username:
            with _temp_lock:
                temp_passwords[username] = p
            return jsonify({"status": "success", "password": p})

    return jsonify({"status": "error", "message": "کاربر یافت نشد"}), 404


@app.route("/api/ssh-users/<username>/password", methods=["POST"])
def api_change_ssh_password(username: str):
    """修改 SSH 密码。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    password = request.form.get("password", "")
    if not password or len(password) < 6:
        return jsonify({"status": "error", "message": "رمز عبور باید حداقل ۶ کاراکتر باشد"}), 400

    r = run_cmd(f"echo '{username}:{password}' | chpasswd")
    if not r["success"]:
        return jsonify({"status": "error", "message": r["error"]}), 500

    users = load_ssh_users()
    users = [(u, password if u == username else p) for u, p in users]
    save_ssh_users(users)
    with _temp_lock:
        temp_passwords[username] = password

    return jsonify({"status": "success", "message": "رمز عبور تغییر کرد"})


# ---------------------------------------------------------------------------
# 路由：侧边集合
# ---------------------------------------------------------------------------

@app.route("/api/side-collections", methods=["GET"])
def api_get_side_collections():
    """获取侧边集合列表。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401
    return jsonify({"status": "success", "collections": load_side_collections()})


@app.route("/api/side-collections", methods=["POST"])
def api_save_side_collections():
    """保存侧边集合列表。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401
    data = request.json if request.is_json else json.loads(request.form.get("data", "[]"))
    save_side_collections(data)
    return jsonify({"status": "success", "message": "侧边集合已保存"})


# ---------------------------------------------------------------------------
# 路由：Client Sync
# ---------------------------------------------------------------------------

@app.route("/api/sync-clients/status", methods=["GET"])
def api_sync_clients_status():
    """获取 syncClients 状态。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401
    enabled = get_sync_clients_status()
    timer_status = run_cmd(f"systemctl is-active {SYNC_TIMER_UNIT}")
    return jsonify({
        "status": "success",
        "enabled": enabled,
        "timer_active": timer_status["success"] and timer_status["output"] == "active",
    })


@app.route("/api/sync-clients/enable", methods=["POST"])
def api_sync_clients_enable():
    """启用 Client Sync：设置 syncClients=true，启动 timer。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    duration = int(request.form.get("duration", 5))
    if duration < 1:
        duration = 5

    # 设置 syncClients=true
    if not set_xui_setting(XUI_SYNC_CLIENTS_KEY, "true"):
        return jsonify({"status": "error", "message": "无法写入 X-UI 数据库"}), 500

    # 更新 timer 持续时间
    timer_file = f"/etc/systemd/system/{SYNC_TIMER_UNIT}"
    if os.path.exists(timer_file):
        with open(timer_file, "r") as f:
            content = f.read()
        content = re.sub(r"OnUnitActiveSec=.*", f"OnUnitActiveSec={duration}min", content)
        with open(timer_file, "w") as f:
            f.write(content)
        run_cmd("systemctl daemon-reload")

    r = run_cmd(f"systemctl restart {SYNC_TIMER_UNIT}")
    if not r["success"]:
        return jsonify({"status": "error", "message": r["error"]}), 500

    return jsonify({"status": "success", "message": f"Client Sync 已启用，每 {duration} 分钟"})


@app.route("/api/sync-clients/disable", methods=["POST"])
def api_sync_clients_disable():
    """禁用 Client Sync：停止 timer，设置 syncClients=false。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    run_cmd(f"systemctl stop {SYNC_TIMER_UNIT}")
    set_xui_setting(XUI_SYNC_CLIENTS_KEY, "false")
    return jsonify({"status": "success", "message": "Client Sync 已禁用"})


# ---------------------------------------------------------------------------
# 路由：服务控制
# ---------------------------------------------------------------------------

@app.route("/api/services", methods=["GET"])
def api_services_status():
    """获取所有相关服务的状态。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    status = {}
    for svc in SERVICES:
        active = run_cmd(f"systemctl is-active {svc}")
        enabled = run_cmd(f"systemctl is-enabled {svc}")
        status[svc] = {
            "active": active["success"] and active["output"] == "active",
            "enabled": enabled["success"] and enabled["output"] == "enabled",
        }
    return jsonify({"status": "success", "services": status})


@app.route("/api/services/<name>/<action>", methods=["POST"])
def api_service_action(name: str, action: str):
    """对服务执行操作：start, stop, restart, enable, disable。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    allowed = {"start", "stop", "restart", "enable", "disable", "reload"}
    if action not in allowed:
        return jsonify({"status": "error", "message": "Invalid action"}), 400

    r = run_cmd(f"systemctl {action} {name}")
    if r["success"]:
        return jsonify({"status": "success", "message": f"{name} {action} 成功"})
    return jsonify({"status": "error", "message": r["error"]}), 500


@app.route("/api/services/<name>/logs", methods=["GET"])
def api_service_logs(name: str):
    """获取服务最近日志。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    lines = request.args.get("lines", 50, type=int)
    r = run_cmd(f"journalctl -u {name} -n {lines} --no-pager -o short")
    if r["success"]:
        return jsonify({"status": "success", "logs": r["output"]})
    return jsonify({"status": "error", "message": r["error"]}), 500


# ---------------------------------------------------------------------------
# 路由：日志下载
# ---------------------------------------------------------------------------

@app.route("/api/logs/<name>/download")
def api_log_download(name: str):
    """下载服务日志。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    r = run_cmd(f"journalctl -u {name} -n 1000 --no-pager -o short")
    body = r["output"] if r["success"] else f"Error: {r.get('error')}"
    return Response(
        body,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={name}.log"},
    )


# ---------------------------------------------------------------------------
# 路由：Sync Expiry Dates
# ---------------------------------------------------------------------------

@app.route("/api/sync-expiry", methods=["POST"])
def api_sync_expiry():
    """同步子账号的到期时间与主账号一致。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    if not os.path.exists(XUI_DB_PATH):
        return jsonify({
            "status": "error",
            "message": f"X-UI 数据库未找到：{XUI_DB_PATH}",
        }), 404

    sql = (
        "UPDATE client_traffics SET expiry_time = "
        "(SELECT parent.expiry_time FROM client_traffics AS parent "
        "WHERE client_traffics.email LIKE '%' || parent.email || '%' "
        "AND client_traffics.email != parent.email "
        "ORDER BY LENGTH(parent.email) DESC LIMIT 1) "
        "WHERE EXISTS (SELECT 1 FROM client_traffics AS parent "
        "WHERE client_traffics.email LIKE '%' || parent.email || '%' "
        "AND client_traffics.email != parent.email);"
    )
    r = _sqlite_query(XUI_DB_PATH, sql)
    if r["success"]:
        return jsonify({"status": "success", "message": "到期时间同步完成"})
    return jsonify({"status": "error", "message": r["error"]}), 500


# ---------------------------------------------------------------------------
# 路由：检查订阅
# ---------------------------------------------------------------------------

@app.route("/api/check-sub", methods=["POST"])
def api_check_sub():
    """检查订阅链接并返回解码后的配置。"""
    if not _require_login():
        return jsonify({"status": "error"}), 401

    sub_url = request.form.get("sub_url", "").strip()
    if not sub_url:
        return jsonify({"status": "error", "message": "请提供订阅链接"}), 400

    try:
        resp = requests.get(sub_url, timeout=10)
        if resp.status_code != 200:
            return jsonify({
                "status": "error",
                "message": f"HTTP {resp.status_code}",
            })

        content = resp.text.strip()
        try:
            decoded = base64.b64decode(content).decode("utf-8")
        except Exception:
            decoded = content

        configs = [l.strip() for l in decoded.splitlines() if l.strip()]
        return jsonify({"status": "success", "configs": configs})
    except requests.RequestException as e:
        return jsonify({"status": "error", "message": str(e)})


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def main():
    """入口点。"""
    sync_ssh_users_startup()

    cfg = load_config()
    cert = cfg.get("cert_path")
    key = cfg.get("key_path")
    port = cfg.get("web_port", 4321)

    ssl_context = None
    if cert and key and os.path.exists(cert) and os.path.exists(key):
        ssl_context = (cert, key)
        print(f"[Ay-Web] HTTPS on port {port}")
    else:
        print(f"[Ay-Web] HTTP on port {port} (no certs)")

    # 使用 Flask dev server（开发环境）；生产环境通过 systemd 启动
    app.run(
        host="0.0.0.0",
        port=port,
        ssl_context=ssl_context,
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()