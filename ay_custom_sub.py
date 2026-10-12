#!/usr/bin/env python3
"""
Ay-Custom-Sub v4.0.0
Subscription merger service with Waitress production server.
"""

import json
import logging
import os
import sys
import threading
import time
from urllib.parse import unquote, quote
from functools import wraps

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, Response, request, jsonify
from waitress import serve

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ay-sub")

# --- Constants ---
BASE_DIR = "/root/ay-custom-sub"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CUSTOM_CONFIG_PATH = os.path.join(BASE_DIR, "Ay_Custom_Configs")
SIDE_COLLECTIONS_PATH = os.path.join(BASE_DIR, "Side_Collections.json")
SIDE_CACHE_PATH = os.path.join(BASE_DIR, "Side_Collections_Cache.json")
CACHE_TTL = 1800  # 30 minutes

app = Flask("Ay-Custom-Sub")

# --- Thread-safe config cache ---
_config_cache = None
_config_lock = threading.Lock()
_config_mtime = 0


def load_config(force_reload=False):
    """Load config with thread-safe caching. Invalidates on file mtime change."""
    global _config_cache, _config_mtime
    with _config_lock:
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            mtime = 0
        if _config_cache is None or force_reload or mtime != _config_mtime:
            if not os.path.exists(CONFIG_PATH):
                raise FileNotFoundError("config.json not found")
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                _config_cache = json.load(f)
            _config_mtime = mtime
            logger.info("Config reloaded (mtime=%s)", mtime)
        return _config_cache


def make_http_session():
    """Create a requests Session with retry and timeout defaults."""
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"])
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# Module-level session (thread-safe for requests)
_http = make_http_session()


def read_custom_configs():
    """Read manual configs from Ay_Custom_Configs file."""
    if not os.path.exists(CUSTOM_CONFIG_PATH):
        return []
    with open(CUSTOM_CONFIG_PATH, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]


# --- Side Collections logic ---

_side_cache = {}
_side_cache_lock = threading.Lock()


def load_side_collections():
    """Load side collections from JSON. Migrate from config.json if needed."""
    # Migration: if Side_Collections.json missing but config has old format
    if not os.path.exists(SIDE_COLLECTIONS_PATH):
        try:
            config = load_config()
            old_urls = config.get("side_collection_urls", [])
            if old_urls:
                migrated = []
                for url in old_urls:
                    if url.strip():
                        migrated.append({
                            "url": url.strip(),
                            "global_rename": "",
                            "configs": [],
                            "is_encoded": False
                        })
                save_side_collections(migrated)
                logger.info("Migrated %d side collection URLs from config.json", len(migrated))
            else:
                save_side_collections([])
        except Exception as e:
            logger.error("Migration failed: %s", e)
            save_side_collections([])
    try:
        with open(SIDE_COLLECTIONS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (json.JSONDecodeError, IOError) as e:
        logger.error("Failed to load Side_Collections.json: %s", e)
    return []


def save_side_collections(collections):
    """Persist side collections to JSON."""
    with open(SIDE_COLLECTIONS_PATH, 'w', encoding='utf-8') as f:
        json.dump(collections, f, ensure_ascii=False, indent=2)


def load_side_cache():
    """Load cached fetched configs."""
    if not os.path.exists(SIDE_CACHE_PATH):
        return {}
    try:
        with open(SIDE_CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_side_cache(cache):
    """Persist cache to disk."""
    with open(SIDE_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)


def fetch_collection_configs(collection, force=False):
    """Fetch configs from a side collection URL. Uses TTL cache."""
    url = collection.get("url", "").strip()
    if not url:
        return []

    cache = load_side_cache()
    now = time.time()
    cached = cache.get(url)

    if not force and cached and (now - cached.get("ts", 0)) < CACHE_TTL:
        return cached.get("configs", [])

    try:
        resp = _http.get(url, timeout=(5, 10), verify=False)
        if resp.status_code != 200:
            logger.warning("Side collection %s returned %d", url, resp.status_code)
            if cached:
                return cached.get("configs", [])
            return []

        text = resp.text.strip()
        # Detect and decode base64-encoded subscription
        if collection.get("is_encoded", False):
            import base64
            try:
                decoded = base64.b64decode(text).decode('utf-8', errors='ignore')
                lines = [l.strip() for l in decoded.splitlines() if l.strip()]
            except Exception:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
        else:
            lines = [l.strip() for l in text.splitlines() if l.strip()]

        cache[url] = {"ts": now, "configs": lines}
        save_side_cache(cache)
        logger.info("Fetched %d configs from side collection %s", len(lines), url)
        return lines

    except Exception as e:
        logger.error("Failed to fetch side collection %s: %s", url, e)
        if cached:
            return cached.get("configs", [])
        return []


def apply_rename(original_name, collection, index):
    """Apply rename rules: per-config > global > original."""
    # Per-config rename is handled at config level, this is global-level
    global_rename = collection.get("global_rename", "")
    if not global_rename:
        return original_name

    col_name = collection.get("url", "unknown").split("/")[-1][:20]
    result = global_rename
    result = result.replace("{original}", original_name)
    result = result.replace("{index}", str(index + 1))
    result = result.replace("{collection}", col_name)
    return result


def extract_name(config_str):
    """Extract fragment (name) from a v2ray config URI."""
    if '#' in config_str:
        return config_str.split('#', 1)[1]
    return ""


def replace_name(config_str, new_name):
    """Replace fragment in a v2ray config URI."""
    if '#' in config_str:
        base = config_str.split('#', 1)[0]
    else:
        base = config_str
    return f"{base}#{new_name}"


def process_collection_configs(collection):
    """Apply renames and visibility filter to collection configs."""
    raw_configs = fetch_collection_configs(collection)
    per_config = collection.get("configs", [])
    result = []

    for idx, cfg in enumerate(raw_configs):
        if not cfg.strip():
            continue

        # Find per-config override (by index)
        override = per_config[idx] if idx < len(per_config) else {}

        # Visibility
        if override.get("visible", True) is False:
            continue

        original_name = extract_name(cfg)
        new_name = original_name

        # Priority: per-config rename > global_rename
        if override.get("rename"):
            new_name = override["rename"]
        else:
            global_renamed = apply_rename(original_name, collection, idx)
            if global_renamed != original_name:
                new_name = global_renamed

        if new_name != original_name:
            cfg = replace_name(cfg, new_name)

        result.append(cfg)

    return result


# --- X-UI Header Transfer ---

# Headers to forward from X-UI subscription response
XUI_HEADER_KEYS = [
    "subscription-userinfo",
    "profile-title",
    "profile-update-interval",
    "profile-web-page-url",
    "profile-description",
    "support-url",
    "announce",
    "routing-enable",
    "routing",
    "sort-order",
]


def extract_xui_headers(headers):
    """Extract X-UI specific headers, preserving case-insensitive semantics."""
    result = {}
    for key in XUI_HEADER_KEYS:
        # HTTP headers are case-insensitive; iterate and match
        for h_key, h_val in headers.items():
            if h_key.lower() == key:
                result[h_key] = h_val
                break
    return result


# --- Routes ---

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "ay-custom-sub", "version": "4.0.0"})


@app.route('/Ay_Custom_Configs')
def custom_configs_route():
    """Public endpoint serving manual configs as plain text subscription."""
    try:
        configs = read_custom_configs()
        content = '\n'.join(configs)
        return Response(content, status=200, mimetype='text/plain; charset=utf-8')
    except Exception as e:
        logger.error("Error serving Ay_Custom_Configs: %s", e)
        return Response('', status=200, mimetype='text/plain; charset=utf-8')


@app.route('/<sub_path>/<username>')
def merged_sub(sub_path, username):
    """Main subscription merge endpoint."""
    try:
        config = load_config()
        main_configs = []
        xui_headers = {}

        # Build X-UI URL
        xui_url = (
            f"https://{config['domain']}:{config['main_port']}"
            f"/{sub_path}/{username}"
        )

        try:
            real_resp = _http.get(xui_url, timeout=(5, 10), verify=False)
            status_code = real_resp.status_code

            if status_code == 200:
                response_text = real_resp.text.strip()
                # Transfer X-UI headers regardless of content
                xui_headers = extract_xui_headers(real_resp.headers)

                is_expired = False
                if not response_text:
                    is_expired = True
                else:
                    first_line = response_text.splitlines()[0]
                    if '#' in first_line:
                        encoded_name = first_line.split('#', 1)[1]
                        decoded = unquote(encoded_name).strip()
                        if decoded.startswith('⛔'):
                            is_expired = True

                if is_expired:
                    return _make_expired_response(config, xui_headers)

                main_configs = response_text.splitlines()

            elif status_code == 404:
                # User not found
                return _make_no_sub_response(config, xui_headers)

            else:
                # Other status - try to fetch, but log
                logger.warning("X-UI returned %d for user %s", status_code, username)
                return _make_no_sub_response(config, xui_headers)

        except requests.RequestException as e:
            logger.error("X-UI connection failed for %s: %s", username, e)
            # Return cached or empty with correct headers
            return Response(
                "خطا در اتصال به سرور اصلی. کانفیگ‌های قبلی شما حفظ شده است.",
                status=502, mimetype='text/plain; charset=utf-8'
            )

        # Merge with custom configs
        extra_configs = read_custom_configs()

        # Process side collections
        side_collections = load_side_collections()
        side_configs = []
        for coll in side_collections:
            try:
                side_configs.extend(process_collection_configs(coll))
            except Exception as e:
                logger.error("Error processing collection %s: %s", coll.get("url"), e)

        final_configs = main_configs + extra_configs + side_configs
        cleaned = [c for c in final_configs if c.strip()]

        resp = Response(
            '\n'.join(cleaned),
            status=200,
            mimetype='text/plain; charset=utf-8'
        )
        # Transfer X-UI headers
        for key, val in xui_headers.items():
            resp.headers[key] = val
        return resp

    except Exception as e:
        logger.exception("Unhandled error in merged_sub")
        return Response(f"Internal server error: {e}", status=500)


def _make_expired_response(config, headers):
    """Build expired-user response with dummy config."""
    message = config.get("expired_message", "⛔ اشتراک شما منقضی شده ⛔")
    template = config.get(
        "dummy_config_template",
        "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1?type=ws#{MESSAGE}"
    )
    encoded = quote(message)
    dummy = template.replace("{MESSAGE}", encoded)
    resp = Response(dummy, status=200, mimetype='text/plain; charset=utf-8')
    # Zero usage headers
    resp.headers["Subscription-Userinfo"] = "upload=0; download=0; total=0; expire=0"
    for k, v in headers.items():
        if k.lower() != "subscription-userinfo":
            resp.headers[k] = v
    return resp


def _make_no_sub_response(config, headers):
    """Build no-subscription response with dummy config."""
    message = config.get("no_sub_message", "⛔ شما اشتراکی ندارید ⛔")
    template = config.get(
        "dummy_config_template",
        "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1?type=ws#{MESSAGE}"
    )
    encoded = quote(message)
    dummy = template.replace("{MESSAGE}", encoded)
    resp = Response(dummy, status=200, mimetype='text/plain; charset=utf-8')
    resp.headers["Subscription-Userinfo"] = "upload=0; download=0; total=0; expire=0"
    for k, v in headers.items():
        if k.lower() != "subscription-userinfo":
            resp.headers[k] = v
    return resp


# --- Server runner ---

def run_server(port, cert_path=None, key_path=None):
    """Run Waitress on a single port."""
    ssl_context = None
    if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
        ssl_context = (cert_path, key_path)

    logger.info("Starting Waitress on port %d (SSL=%s)", port, bool(ssl_context))
    try:
        serve(
            app,
            host='0.0.0.0',
            port=port,
            threads=8,
            url_scheme='https' if ssl_context else 'http',
            **({} if not ssl_context else {})
        )
    except Exception as e:
        logger.exception("Waitress failed on port %d: %s", port, e)


if __name__ == '__main__':
    try:
        config = load_config()
        ports = config.get('extra_ports', [6854, 8688, 4586])
        cert = config.get('cert_path')
        key = config.get('key_path')

        if not isinstance(ports, list) or not ports:
            ports = [6854]

        threads = []
        for port in ports:
            t = threading.Thread(
                target=run_server,
                args=(port, cert, key),
                daemon=True,
                name=f"waitress-{port}"
            )
            t.start()
            threads.append(t)
            logger.info("Started Waitress thread for port %d", port)

        logger.info("All %d Waitress instances running. PID=%d", len(threads), os.getpid())

        # Keep main thread alive
        while True:
            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.exception("Fatal startup error: %s", e)
        sys.exit(1)