from flask import Flask, Response, request
import requests
import os
import threading
import json

app = Flask("Ay-Custom-Sub")

CONFIG_PATH = "/root/ay-custom-sub/config.json"
CUSTOM_CONFIG_PATH = "/root/ay-custom-sub/Ay_Custom_Configs"

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise Exception("فایل config.json یافت نشد.")
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def read_custom_configs():
    if not os.path.exists(CUSTOM_CONFIG_PATH):
        return []
    with open(CUSTOM_CONFIG_PATH, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

@app.route('/<sub_path>/<username>')
def merged_sub(sub_path, username):
    try:
        config = load_config()
        xui_url = f"https://{config['domain']}:{config['main_port']}/{sub_path}/{username}"
        real_resp = requests.get(xui_url, timeout=10, verify=False)

        if real_resp.status_code != 200:
            return Response("خطا در دریافت سابسکریپشن اصلی", status=502)

        main_configs = real_resp.text.strip().splitlines()
        extra_configs = read_custom_configs()
        final_configs = main_configs + extra_configs

        return Response('\n'.join(final_configs), mimetype='text/plain')

    except Exception as e:
        return Response(f"خطای داخلی سرور:\n{str(e)}", status=500)

def run_server(port, cert_path, key_path):
    app.run(
        host='0.0.0.0',
        port=port,
        ssl_context=(cert_path, key_path),
        debug=False,
        threaded=True
    )

if __name__ == '__main__':
    config = load_config()
    ports = config.get('extra_ports', [6854, 8688, 4586])
    cert_path = config.get('cert_path')
    key_path = config.get('key_path')

    for port in ports:
        threading.Thread(target=run_server, args=(port, cert_path, key_path)).start()
