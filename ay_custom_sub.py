from flask import Flask, Response, request
import requests
from requests.exceptions import RequestException
import os
import threading
import json
import traceback

app = Flask("Ay-Custom-Sub")

CONFIG_PATH = "/root/ay-custom-sub/config.json"
CUSTOM_CONFIG_PATH = "/root/ay-custom-sub/Ay_Custom_Configs"

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise Exception("فایل config.json یافت نشد.")
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
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
        
        main_configs = []
        try:
            xui_url = f"https://{config['domain']}:{config['main_port']}/{sub_path}/{username}"
            real_resp = requests.get(xui_url, timeout=10, verify=False)
            if real_resp.status_code == 200:
                main_configs = real_resp.text.strip().splitlines()
            else:
                print(f"Error fetching main subscription, status code: {real_resp.status_code}")
        except RequestException as e:
            print(f"Could not connect to main subscription URL: {e}")

        extra_configs = read_custom_configs()

        side_collection_configs = []
        side_urls = config.get('side_collection_urls', [])
        if side_urls:
            for url in side_urls:
                if not url.strip():
                    continue
                try:
                    side_resp = requests.get(url.strip(), timeout=10)
                    if side_resp.status_code == 200:
                        side_collection_configs.extend(side_resp.text.strip().splitlines())
                    else:
                        print(f"Error fetching side collection from {url}, status code: {side_resp.status_code}")
                except RequestException as e:
                    print(f"Could not connect to side collection URL {url}: {e}")
        
        final_configs = main_configs + extra_configs + side_collection_configs
        
        cleaned_configs = [line for line in final_configs if line.strip()]

        return Response('\n'.join(cleaned_configs), mimetype='text/plain')

    except Exception as e:
        print(traceback.format_exc())
        return Response(f"خطای داخلی سرور: {str(e)}", status=500)

def run_server(port, cert_path, key_path):
    ssl_context = None
    if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
        ssl_context = (cert_path, key_path)
    
    app.run(
        host='0.0.0.0',
        port=port,
        ssl_context=ssl_context,
        debug=False,
        threaded=True
    )

if __name__ == '__main__':
    try:
        config = load_config()
        ports = config.get('extra_ports', [6854, 8688, 4586])
        cert_path = config.get('cert_path')
        key_path = config.get('key_path')

        for port in ports:
            threading.Thread(target=run_server, args=(port, cert_path, key_path)).start()
            print(f"Server listening on port {port}")
    except Exception as e:
        print(f"Failed to start server: {e}")