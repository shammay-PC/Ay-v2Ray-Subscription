from flask import Flask, render_template, request, redirect, url_for, session, flash
import json
import os
import requests

app = Flask(__name__)
app.secret_key = 'AyCustomSecretKey'

CONFIG_PATH = 'config.json'
CONFIGS_FILE = 'Ay_Custom_Configs'

def load_config():
    default_config = {
        "username": "AyAdmin",
        "password": "AyPass",
        "web_port": 6854,
        "main_port": 8868,
        "extra_ports": [6854, 8688, 4586],
        "domain": "org.shammay.ir",
        "sub_path": "sub",
        "cert_path": "",
        "key_path": ""
    }
    if not os.path.exists(CONFIG_PATH):
        return default_config
    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
        for key, val in default_config.items():
            if key not in config:
                config[key] = val
        return config
    except:
        return default_config

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

@app.route('/', methods=['GET', 'POST'])
def login():
    config = load_config()
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == config.get('username') and password == config.get('password'):
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
    if os.path.exists(CONFIGS_FILE):
        with open(CONFIGS_FILE, 'r') as f:
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
        save_config(config)
        flash('تنظیمات با موفقیت ذخیره شد', 'success')
        return redirect(url_for('dashboard'))

    xui_base = f"https://{config.get('domain')}:{config.get('main_port')}"

    return render_template(
        'dashboard.html',
        custom_configs=custom_configs,
        settings=config,
        xui_base=xui_base
    )

@app.route('/add-config', methods=['POST'])
def add_config():
    if not session.get('logged_in'):
        return 'Unauthorized', 401

    configs = request.form.getlist('new_config')
    if configs:
        with open(CONFIGS_FILE, 'a') as f:
            for cfg in configs:
                clean_cfg = cfg.strip()
                if clean_cfg:
                    f.write(clean_cfg + '\n')
    return '', 204

@app.route('/delete-config', methods=['POST'])
def delete_config():
    if not session.get('logged_in'):
        return 'Unauthorized', 401
    index = int(request.form.get('index', -1))
    if index >= 0 and os.path.exists(CONFIGS_FILE):
        with open(CONFIGS_FILE, 'r') as f:
            lines = f.readlines()
        if index < len(lines):
            lines.pop(index)
            with open(CONFIGS_FILE, 'w') as f:
                f.writelines(lines)
    return '', 204

import base64

@app.route('/check-sub', methods=['POST'])
def check_sub():
    if not session.get('logged_in'):
        return 'Unauthorized', 401

    sub_url = request.form.get('sub_url')
    if not sub_url:
        return 'No URL provided'

    try:
        res = requests.get(sub_url, timeout=7)
        if res.status_code != 200:
            return f'<pre>خطا در دریافت لینک: {res.status_code}</pre>'

        content = res.text.strip()

        # دیکد کردن اگر base64 باشد
        try:
            decoded = base64.b64decode(content).decode('utf-8')
        except Exception:
            decoded = content  # اگر دیکد نشد، همون متن رو استفاده کن

        # پاکسازی خطوط و آماده‌سازی برای نمایش منظم
        lines = decoded.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        configs = [line.strip() for line in lines if line.strip()]
        result = '\n' + '\n'.join(configs)  # اضافه کردن خط خالی در ابتدای خروجی

        return f"<pre style='direction: ltr; white-space: pre-wrap;'>{result}</pre>"

    except Exception as e:
        return f"<pre>خطا: {e}</pre>"

if __name__ == '__main__':
    config = load_config()
    cert = config.get('cert_path')
    key = config.get('key_path')
    port = config.get('web_port', 6854)

    if cert and key and os.path.exists(cert) and os.path.exists(key):
        app.run(host='0.0.0.0', port=port, ssl_context=(cert, key))
    else:
        print('SSL certificate or key file not found. Running in HTTP mode...')
        app.run(host='0.0.0.0', port=port)


