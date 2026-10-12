#!/usr/bin/env python3
"""
Ay-Custom-Sub v4.1.0
Subscription merger service.
"""

import json
import os
import ssl
import sys
import threading
import time
import traceback
from urllib.parse import quote, unquote

import requests
from flask import Flask, Response, request
from requests.exceptions import RequestException

PROJECT_DIR = "/root/ay-custom-sub"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
CUSTOM_CONFIG_PATH = os.path.join(PROJECT_DIR, "Ay_Custom_Configs")
SIDE_COLLECTIONS_PATH = os.path.join(PROJECT_DIR, "Side_Collections.json")
SIDE_CACHE_PATH = os.path.join(PROJECT_DIR, "Side_Collections_Cache.json")

SIDE_CACHE_TTL = 1800

FORWARDED_HEADERS = (
    "Subscription-Userinfo",
    "Profile-Title",
    "Profile-Update-Interval",
    "Profile-Web-Page-Url",
)

app = Flask("Ay-Custom-Sub")

_config_cache = None
_config_lock = threading.Lock()


def load_config():
    global _config_cache
    with _config_lock:
        if _config_cache is not None:
            return _config_cache
        if not os.path.exists(CONFIG_PATH):
            raise RuntimeError("config.json not found")
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            _config_cache = json.load(fh)
        return _config_cache


def invalidate_config_cache():
    global _config_cache
    with _config_lock:
        _config_cache = None


def log(msg: str) -> None:
    print(f"[ay-custom-sub] {msg}", flush=True)


def safe_get(url, **kwargs):
    kwargs.setdefault("timeout", 8)
    kwargs.setdefault("verify", False)
    for attempt in range(3):
        try:
            return requests.get(url, **kwargs)
        except RequestException as exc:
            log(f"safe_get attempt {attempt + 1} failed for {url}: {exc}")
            time.sleep(2 ** attempt)
    return None


def read_custom_configs():
    if not os.path.exists(CUSTOM_CONFIG_PATH):
        return []
    with open(CUSTOM_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


def build_dummy_config(config, message):
    template = config.get(
        "dummy_config_template",
        "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1?type=ws#{MESSAGE}",
    )
    return template.replace("{MESSAGE}", quote(message))


def _load_side_collections_from_disk():
    if os.path.exists(SIDE_COLLECTIONS_PATH):
        try:
            with open(SIDE_COLLECTIONS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            log(f"Failed to read Side_Collections.json: {exc}")

    try:
        cfg = load_config()
        legacy_urls = cfg.get("side_collection_urls", []) or []
        migrated = [
            {"url": url, "type": "plain", "global_rename": None,
             "configs": [], "last_fetch": 0}
            for url in legacy_urls if url.strip()
        ]
        if migrated:
            with open(SIDE_COLLECTIONS_PATH, "w", encoding="utf-8") as fh:
                json.dump(migrated, fh, indent=2, ensure_ascii=False)
            log(f"Migrated {len(migrated)} side collection URL(s) from config.json")
        return migrated
    except Exception as exc:
        log(f"Side collection migration failed: {exc}")
        return []


def _apply_rename(config_str, per_config_rename, global_rename, index, collection_name):
    """
    Replace the fragment (after '#') of a config according to rename rules.
    {original} is substituted with the original fragment name (decoded),
    {index} with the 1-based index, {collection} with the collection URL.
    """
    if "#" in config_str:
        base, original = config_str.split("#", 1)
    else:
        base, original = config_str, ""

    if per_config_rename:
        new_name = per_config_rename
    elif global_rename:
        new_name = global_rename
    else:
        return config_str

    try:
        original_decoded = unquote(original) if original else ""
    except Exception:
        original_decoded = original or ""

    new_name = (
        new_name
        .replace("{original}", original_decoded)
        .replace("{index}", str(index))
        .replace("{collection}", collection_name or "")
    )
    return f"{base}#{quote(new_name)}"


def _fetch_collection(entry):
    url = entry.get("url", "").strip()
    if not url:
        return []

    now = time.time()
    if entry.get("last_fetch", 0) + SIDE_CACHE_TTL > now and entry.get("configs"):
        return entry.get("configs", [])

    resp = safe_get(url)
    if resp is None or resp.status_code != 200:
        log(f"Side collection fetch failed for {url}; using cached data if any")
        return entry.get("configs", [])

    text = resp.text.strip()
    if entry.get("type") == "base64":
        import base64
        try:
            text = base64.b64decode(text).decode("utf-8", errors="ignore")
        except Exception as exc:
            log(f"Base64 decode failed for {url}: {exc}")
            return entry.get("configs", [])

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    existing = {c.get("config"): c for c in entry.get("configs", [])}
    new_configs = []
    for cfg in lines:
        prev = existing.get(cfg, {})
        new_configs.append({
            "config": cfg,
            "rename": prev.get("rename"),
            "visible": prev.get("visible", True),
        })

    entry["configs"] = new_configs
    entry["last_fetch"] = now
    return new_configs


def get_side_collection_configs(config):
    entries = _load_side_collections_from_disk()
    if not entries:
        return []

    output = []
    for entry in entries:
        raw = _fetch_collection(entry)
        global_rename = entry.get("global_rename")
        collection_name = entry.get("url", "")
        for idx, item in enumerate(raw):
            if not item.get("visible", True):
                continue
            cfg = item.get("config", "").strip()
            if not cfg:
                continue
            cfg = _apply_rename(
                cfg,
                per_config_rename=item.get("rename"),
                global_rename=global_rename,
                index=idx + 1,
                collection_name=collection_name,
            )
            output.append(cfg)

    try:
        with open(SIDE_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        log(f"Failed to write Side_Collections_Cache.json: {exc}")

    return output


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/Ay_Custom_Configs")
def raw_custom_configs():
    try:
        body = "\n".join(read_custom_configs())
    except Exception as exc:
        log(f"/Ay_Custom_Configs read failed: {exc}")
        body = ""
    return Response(body, status=200, mimetype="text/plain; charset=utf-8")


@app.route("/<sub_path>/<username>")
def merged_sub(sub_path, username):
    try:
        config = load_config()

        main_configs = []
        forwarded_headers = {}

        xui_url = (
            f"https://{config['domain']}:{config['main_port']}"
            f"/{sub_path}/{username}"
        )
        real_resp = safe_get(xui_url)

        if real_resp is None:
            log(f"Main X-UI unreachable for user '{username}'; preserving previous configs")
            return Response(
                "خطا در اتصال به سرور اصلی. کانفیگ‌های قبلی شما حفظ شده است.",
                status=502,
            )

        for h in FORWARDED_HEADERS:
            if h in real_resp.headers:
                forwarded_headers[h] = real_resp.headers[h]

        if real_resp.status_code != 200:
            log(f"User '{username}' not found on X-UI (status={real_resp.status_code})")
            forwarded_headers.setdefault(
                "Subscription-Userinfo", "upload=0; download=0; total=0; expire=0"
            )
            dummy = build_dummy_config(
                config,
                config.get("no_sub_message", "⛔ شما اشتراکی در این سرویس ندارید ⛔"),
            )
            return Response(dummy, status=200, mimetype="text/plain",
                            headers=forwarded_headers)

        response_text = real_resp.text.strip()

        is_expired = False
        if not response_text:
            is_expired = True
        else:
            first_line = response_text.splitlines()[0]
            if "#" in first_line:
                decoded = unquote(first_line.split("#", 1)[1]).strip()
                if decoded.startswith("⛔"):
                    is_expired = True

        if is_expired:
            log(f"Subscription for '{username}' is expired")
            forwarded_headers.setdefault(
                "Subscription-Userinfo", "upload=0; download=0; total=0; expire=0"
            )
            dummy = build_dummy_config(
                config,
                config.get("expired_message", "⛔ اشتراک شما منقضی شده ⛔"),
            )
            return Response(dummy, status=200, mimetype="text/plain",
                            headers=forwarded_headers)

        main_configs = [line for line in response_text.splitlines() if line.strip()]

        extra_configs = read_custom_configs()
        side_configs = get_side_collection_configs(config)

        final_configs = main_configs + extra_configs + side_configs
        cleaned = [line for line in final_configs if line.strip()]

        return Response(
            "\n".join(cleaned),
            status=200,
            mimetype="text/plain",
            headers=forwarded_headers,
        )

    except Exception as exc:
        log(f"Internal error: {exc}\n{traceback.format_exc()}")
        return Response(f"خطای داخلی سرور: {exc}", status=500)


def run_server(port, cert_path, key_path):
    ssl_context = None
    if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
        ssl_context = (cert_path, key_path)
    else:
        log("FATAL: SSL cert/key not found. Aborting.")
        sys.exit(1)

    app.run(
        host="0.0.0.0",
        port=port,
        ssl_context=ssl_context,
        debug=False,
        threaded=True,
        use_reloader=False,
    )


def main():
    config = load_config()
    ports = config.get("extra_ports", [6854, 8688])
    cert_path = config.get("cert_path") or "/root/cert/cert.pem"
    key_path = config.get("key_path") or "/root/cert/privkey.pem"

    if not os.path.exists(cert_path) or not os.path.exists(key_path):
        log(f"FATAL: SSL cert/key not found. cert={cert_path} key={key_path}")
        sys.exit(1)

    threads = []
    for port in ports:
        t = threading.Thread(target=run_server, args=(port, cert_path, key_path), daemon=True)
        t.start()
        threads.append(t)
        log(f"HTTPS server listening on port {port}")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log("Shutdown requested")


if __name__ == "__main__":
    main()