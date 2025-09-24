from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import json
import os
import requests
import base64
import subprocess
import string
import random

app = Flask(__name__)
app.secret_key = 'AyCustomSecretKey'

# --- مسیرهای فایل‌ها ---
CONFIG_PATH = 'config.json'
CUSTOM_CONFIGS_FILE = 'Ay_Custom_Configs'
SSH_USERS_FILE = 'Ay_SSH_Users'
XUI_DB_PATH = '/etc/x-ui/x-ui.db'

# --- متغیر گلوبال برای نگهداری موقت رمزهای عبور ---
# هشدار: این رمزها فقط در حافظه رم سرور باقی می‌مانند و با ریستارت شدن سرویس از بین می‌روند.
temp_passwords = {}

# --- توابع کمکی ---

def run_command(command):
    """یک دستور لینوکس را اجرا کرده و خروجی را برمی‌گرداند."""
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        return {"success": True, "output": result.stdout.strip()}
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": e.stderr.strip()}

def generate_random_password(length=12):
    """یک رمز عبور تصادفی و امن ایجاد می‌کند."""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for i in range(length))

def load_ssh_users():
    """لیست کاربران SSH را از فایل می‌خواند."""
    if not os.path.exists(SSH_USERS_FILE):
        return []
    with open(SSH_USERS_FILE, 'r') as f:
        return [line.strip() for line in f if line.strip()]

def save_ssh_users(users):
    """لیست کاربران SSH را در فایل ذخیره می‌کند."""
    with open(SSH_USERS_FILE, 'w') as f:
        for user in users:
            f.write(user + '\n')

def sync_ssh_users_on_startup():
    """
    کاربران فایل پشتیبان را با کاربران واقعی سیستم همگام‌سازی می‌کند.
    اگر کاربری در فایل بود اما در سیستم نبود، آن را با رمز جدید می‌سازد.
    """
    print("Starting SSH user synchronization...")
    run_command("groupadd --force AyVpnUsers")
    
    users_in_file = load_ssh_users()
    if not users_in_file:
        print("No users in SSH backup file. Sync finished.")
        return

    result = run_command("getent group AyVpnUsers | cut -d: -f4")
    system_users = result['output'].split(',') if result['success'] and result['output'] else []

    for username in users_in_file:
        if username not in system_users:
            print(f"User '{username}' found in backup file but not in system. Re-creating...")
            password = generate_random_password()
            
            add_command = (
                f"useradd -m -s /bin/bash -g AyVpnUsers {username} && "
                f"echo '{username}:{password}' | chpasswd"
            )
            
            creation_result = run_command(add_command)
            if creation_result['success']:
                temp_passwords[username] = password
                print(f"User '{username}' created successfully with a new random password.")
            else:
                print(f"Failed to create user '{username}': {creation_result['error']}")

# --- بارگذاری و ذخیره تنظیمات اصلی ---

def load_config():
    default_config = {
        "username": "AyAdmin", "password": "AyPass", "web_port": 6854, "main_port": 8868,
        "extra_ports": [6854, 8688, 4586], "domain": "www.shammay.ir", "sub_path": "sub",
        "cert_path": "", "key_path": "", "side_collection_urls": [],
        "expired_message": "⛔ اشتراک شما منقضی شده، لطفا تمدید کنید ⛔",
        "no_sub_message": "⛔ شما اشتراکی در این سرویس ندارید ⛔",
        "dummy_config_template": "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1?type=ws#{MESSAGE}"
    }
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'w') as f: json.dump(default_config, f, indent=2)
        return default_config
    try:
        with open(CONFIG_PATH, 'r') as f: config = json.load(f)
        for key, val in default_config.items():
            config.setdefault(key, val)
        return config
    except (json.JSONDecodeError, IOError):
        return default_config

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

# --- مسیرهای Flask (Routes) ---

@app.route('/', methods=['GET', 'POST'])
def login():
    config = load_config()
    if request.method == 'POST':
        if request.form.get('username') == config.get('username') and request.form.get('password') == config.get('password'):
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            flash('نام کاربری یا رمز عبور اشتباه است', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    config = load_config()
    custom_configs = []
    if os.path.exists(CUSTOM_CONFIGS_FILE):
        with open(CUSTOM_CONFIGS_FILE, 'r') as f:
            custom_configs = [line.strip() for line in f if line.strip()]

    if request.method == 'POST':
        config['username'] = request.form.get('username', config.get('username'))
        config['password'] = request.form.get('password', config.get('password'))
        config['web_port'] = int(request.form.get('web_port', config.get('web_port', 6854)))
        config['main_port'] = int(request.form.get('main_port', config.get('main_port', 8868)))
        config['extra_ports'] = [int(p.strip()) for p in request.form.get('extra_ports', '').split(',') if p.strip().isdigit()]
        config['domain'] = request.form.get('domain', config.get('domain'))
        config['sub_path'] = request.form.get('sub_path', config.get('sub_path'))
        config['cert_path'] = request.form.get('cert_path', config.get('cert_path'))
        config['key_path'] = request.form.get('key_path', config.get('key_path'))
        config['expired_message'] = request.form.get('expired_message', config.get('expired_message'))
        config['no_sub_message'] = request.form.get('no_sub_message', config.get('no_sub_message'))
        save_config(config)
        flash('تنظیمات با موفقیت ذخیره شد. برای اعمال برخی تغییرات، سرویس را ریستارت کنید.', 'success')
        return redirect(url_for('dashboard'))
    
    ssh_users = load_ssh_users()
    return render_template(
        'dashboard.html',
        custom_configs=custom_configs,
        ssh_users=ssh_users,
        settings=config
    )

@app.route('/add-config', methods=['POST'])
def add_config():
    if not session.get('logged_in'): return 'Unauthorized', 401
    configs = request.form.getlist('new_config')
    if configs:
        with open(CUSTOM_CONFIGS_FILE, 'a') as f:
            for cfg in configs:
                if cfg.strip(): f.write(cfg.strip() + '\n')
    return '', 204

@app.route('/delete-config', methods=['POST'])
def delete_config():
    if not session.get('logged_in'): return 'Unauthorized', 401
    index = int(request.form.get('index', -1))
    if index >= 0 and os.path.exists(CUSTOM_CONFIGS_FILE):
        with open(CUSTOM_CONFIGS_FILE, 'r') as f: lines = f.readlines()
        if index < len(lines):
            lines.pop(index)
            with open(CUSTOM_CONFIGS_FILE, 'w') as f: f.writelines(lines)
    return '', 204

@app.route('/delete-all-configs', methods=['POST'])
def delete_all_configs():
    if not session.get('logged_in'): return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    try:
        open(CUSTOM_CONFIGS_FILE, 'w').close()
        return jsonify({'status': 'success', 'message': 'تمام کانفیگ‌های دستی حذف شدند.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/save-side-links', methods=['POST'])
def save_side_links():
    if not session.get('logged_in'): return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    try:
        links_list = request.form.getlist('side_collection_urls')
        cleaned_links = [link.strip() for link in links_list if link.strip()]
        config = load_config()
        config['side_collection_urls'] = cleaned_links
        save_config(config)
        return jsonify({'status': 'success', 'message': 'لینک‌های مجموعه جانبی با موفقیت ذخیره شدند.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/restart-services', methods=['POST'])
def restart_services():
    if not session.get('logged_in'): return 'Unauthorized', 401
    try:
        subprocess.run(['systemctl', 'restart', 'ay-custom-sub'], check=True)
        subprocess.run(['systemctl', 'restart', 'ay-custom-web'], check=True)
        return 'Services restarted successfully.', 200
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 'Failed to restart services.', 500

@app.route('/sync-expiry-dates', methods=['POST'])
def sync_expiry_dates():
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    if not os.path.exists(XUI_DB_PATH):
        return jsonify({'status': 'error', 'message': f'پایگاه داده x-ui در مسیر {XUI_DB_PATH} یافت نشد.'}), 404

    command = f"""
    sqlite3 {XUI_DB_PATH} "
    UPDATE client_traffics SET expiry_time = (SELECT parent.expiry_time FROM client_traffics AS parent WHERE client_traffics.email LIKE '%' || parent.email || '%' AND client_traffics.email != parent.email ORDER BY LENGTH(parent.email) DESC LIMIT 1)
    WHERE EXISTS (SELECT 1 FROM client_traffics AS parent WHERE client_traffics.email LIKE '%' || parent.email || '%' AND client_traffics.email != parent.email);
    "
    """
    result = run_command(command)
    if result['success']:
        return jsonify({'status': 'success', 'message': 'تاریخ انقضاء با موفقیت همسان‌سازی شد.'})
    else:
        return jsonify({'status': 'error', 'message': f"خطا در اجرای دستور: {result['error']}"}), 500

@app.route('/add-ssh-user', methods=['POST'])
def add_ssh_user():
    if not session.get('logged_in'): return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    username = request.form.get('username')
    if not username or not username.isalnum() or len(username) < 3:
        return jsonify({'status': 'error', 'message': 'نام کاربری باید حداقل ۳ کاراکتر و فقط شامل حروف و اعداد انگلیسی باشد.'})

    users = load_ssh_users()
    if username in users:
        return jsonify({'status': 'error', 'message': 'این نام کاربری قبلا استفاده شده است.'})

    password = generate_random_password()
    command = f"useradd -m -s /bin/bash -g AyVpnUsers {username} && echo '{username}:{password}' | chpasswd"
    result = run_command(command)

    if result['success']:
        users.append(username)
        save_ssh_users(users)
        temp_passwords[username] = password
        return jsonify({'status': 'success', 'message': f'کاربر {username} با موفقیت ساخته شد.', 'username': username, 'password': password})
    else:
        return jsonify({'status': 'error', 'message': result['error']}), 500

@app.route('/delete-ssh-user', methods=['POST'])
def delete_ssh_user():
    if not session.get('logged_in'): return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    username = request.form.get('username')
    users = load_ssh_users()
    if username not in users:
        return jsonify({'status': 'error', 'message': 'کاربر یافت نشد.'})

    result = run_command(f"userdel -r {username}")
    
    users.remove(username)
    save_ssh_users(users)
    temp_passwords.pop(username, None)

    if result['success']:
        return jsonify({'status': 'success', 'message': f'کاربر {username} با موفقیت از سیستم و لیست حذف شد.'})
    else:
        if "does not exist" in result['error']:
            return jsonify({'status': 'success', 'message': f'کاربر {username} از قبل در سیستم وجود نداشت و فقط از لیست حذف شد.'})
        return jsonify({'status': 'error', 'message': result['error']}), 500

@app.route('/change-ssh-password', methods=['POST'])
def change_ssh_password():
    if not session.get('logged_in'): return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    username = request.form.get('username')
    password = request.form.get('password')

    if not password or len(password) < 6:
        return jsonify({'status': 'error', 'message': 'رمز عبور باید حداقل ۶ کاراکتر باشد.'})

    result = run_command(f"echo '{username}:{password}' | chpasswd")
    if result['success']:
        temp_passwords[username] = password
        return jsonify({'status': 'success', 'message': 'رمز عبور با موفقیت تغییر کرد.'})
    else:
        return jsonify({'status': 'error', 'message': result['error']}), 500

@app.route('/get-ssh-password', methods=['POST'])
def get_ssh_password():
    if not session.get('logged_in'): return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    username = request.form.get('username')
    if username in temp_passwords:
        return jsonify({'status': 'success', 'password': temp_passwords[username]})
    
    password = generate_random_password()
    result = run_command(f"echo '{username}:{password}' | chpasswd")
    if result['success']:
        temp_passwords[username] = password
        return jsonify({'status': 'success', 'password': password})
    else:
        if "does not exist" in result.get('error', ''):
            return jsonify({'status': 'error', 'message': 'این کاربر در سیستم وجود ندارد. لطفا ابتدا او را حذف و دوباره ایجاد کنید.'}), 404
        return jsonify({'status': 'error', 'message': 'خطا در بازیابی رمز عبور.'}), 500

@app.route('/check-sub', methods=['POST'])
def check_sub():
    if not session.get('logged_in'): return 'Unauthorized', 401

    sub_url = request.form.get('sub_url')
    if not sub_url: return 'No URL provided'

    try:
        res = requests.get(sub_url, timeout=7)
        if res.status_code != 200: return f'خطا در دریافت لینک: {res.status_code}'

        content = res.text.strip()
        
        try:
            decoded_content = base64.b64decode(content).decode('utf-8')
        except Exception:
            decoded_content = content

        lines = decoded_content.strip().split('\n')
        configs = [line.strip() for line in lines if line.strip()]
        return '\n'.join(configs)
    except Exception as e:
        return f"خطا: {e}"

if __name__ == '__main__':
    sync_ssh_users_on_startup()
    config = load_config()
    cert, key = config.get('cert_path'), config.get('key_path')
    port = config.get('web_port', 6854)
    ssl_context = (cert, key) if cert and key and os.path.exists(cert) and os.path.exists(key) else None
    
    if not ssl_context: print('SSL certificate or key file not found. Running in HTTP mode...')
        
    app.run(host='0.0.0.0', port=port, ssl_context=ssl_context)