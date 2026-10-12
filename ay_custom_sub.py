#!/usr/bin/env python3
"""
Ay-Custom-Sub v4.0.0
Subscription merger service.

- Runs Waitress (production WSGI) with mandatory SSL on every extra port.
- Merges main X-UI subscription with manual configs and side collections.
- Forwards X-UI headers (Subscription-Userinfo, etc.) to the client.
- Caches config in memory behind a lock; invalidated on file change.
- safe_get() adds timeout + retry to every outbound HTTP request.
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
from waitress import serve

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
PROJECT_DIR = "/root/ay-custom-sub"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
CUSTOM_CONFIG_PATH = os.path.join(PROJECT_DIR, "Ay_Custom_Configs")
SIDE_COLLECTIONS_PATH = os.path.join(PROJECT_DIR, "Side_Collections.json")
SIDE_CACHE_PATH = os.path.join(PROJECT_DIR, "Side_Collections_Cache.json")

SIDE_CACHE_TTL = 1800  # 30 minutes

# X-UI headers worth forwarding to the client.
FORWARDED_HEADERS = (
    "Subscription-Userinfo",
    "Profile-Title",
    "Profile-Update-Interval",
    "Profile-Web-Page-Url",
)

app = Flask("Ay-Custom-Sub")

# ---------------------------------------------------------------------------
# Config cache (thread-safe)
# ---------------------------------------------------------------------------
_config_cache = None
_config_lock = threading.Lock()


def load_config():
    """Return config dict from memory cache, reading from disk on first use."""
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
    """Drop cached config so the next load_config() re-reads from disk."""
    global _config_cache
    with _config_lock:
        _config_cache = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[ay-custom-sub] {msg}", flush=True)


def safe_get(url, **kwargs):
    """
    HTTP GET with sane defaults, timeout and exponential-backoff retry.
    Returns a requests.Response or None on total failure.
    """
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
    """Return list of manual configs from Ay_Custom_Configs."""
    if not os.path.exists(CUSTOM_CONFIG_PATH):
        return []
    with open(CUSTOM_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


def build_dummy_config(config, message):
    """Render the dummy config template with a URL-encoded message."""
    template = config.get(
        "dummy_config_template",
        "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1?type=ws#{MESSAGE}",
    )
    return template.replace("{MESSAGE}", quote(message))


# ---------------------------------------------------------------------------
# Side collections
# ---------------------------------------------------------------------------
def _load_side_collections_from_disk():
    """Read Side_Collections.json, migrating from config.json if needed."""
    if os.path.exists(SIDE_COLLECTIONS_PATH):
        try:
            with open(SIDE_COLLECTIONS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            log(f"Failed to read Side_Collections.json: {exc}")

    # Migration from v3: config.json -> Side_Collections.json
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
    """Replace the fragment (after '#') of a config according to rename rules."""
    if "#" in config_str:
        base = config_str.split("#", 1)[0]
    else:
        base = config_str

    if per_config_rename:
        new_name = per_config_rename
    elif global_rename:
        new_name = global_rename
    else:
        return config_str  # nothing to rename

    new_name = (
        new_name
        .replace("{original}", collection_name or "")
        .replace("{index}", str(index))
        .replace("{collection}", collection_name or "")
    )
    return f"{base}#{quote(new_name)}"


def _fetch_collection(entry):
    """
    Fetch one collection entry (plain or base64) and return a list of configs.
    Uses cache if fresh; preserves old cache on error.
    """
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

    # Preserve user per-config overrides when re-fetching.
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
    """
    Return the final list of side-collection configs ready to be served,
    with rename & visibility applied at serve time.
    """
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

    # Persist cache
    try:
        with open(SIDE_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        log(f"Failed to write Side_Collections_Cache.json: {exc}")

    return output


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/Ay_Custom_Configs")
def raw_custom_configs():
    """Return the raw contents of Ay_Custom_Configs (no auth)."""
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

        # ---- Fetch main X-UI subscription --------------------------------
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

        # Forward whitelisted headers regardless of the outcome.
        for h in FORWARDED_HEADERS:
            if h in real_resp.headers:
                forwarded_headers[h] = real_resp.headers[h]

        if real_resp.status_code != 200:
            # User not found -> serve dummy config.
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

        # Detect expired subscription.
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

        # ---- Merge manual configs + side collections --------------------
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


# ---------------------------------------------------------------------------
# SSL / server bootstrap
# ---------------------------------------------------------------------------
def build_ssl_context(cert_path, key_path):
    """
    Build an SSLContext. Exit with a non-zero code if certs are missing.
    We NEVER fall back to plain HTTP.
    """
    cert_path = cert_path or "/root/cert/cert.pem"
    key_path = key_path or "/root/cert/privkey.pem"

    if not os.path.exists(cert_path) or not os.path.exists(key_path):
        log(f"FATAL: SSL cert/key not found. cert={cert_path} key={key_path}")
        log("Refusing to start without HTTPS. Aborting.")
        sys.exit(1)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return ctx


def run_server(port, ssl_ctx):
    serve(app, host="0.0.0.0", port=port, threads=8, ssl_context=ssl_ctx)


def main():
    config = load_config()
    ports = config.get("extra_ports", [6854, 8688])
    cert_path = config.get("cert_path")
    key_path = config.get("key_path")

    ssl_ctx = build_ssl_context(cert_path, key_path)

    threads = []
    for port in ports:
        t = threading.Thread(target=run_server, args=(port, ssl_ctx), daemon=True)
        t.start()
        threads.append(t)
        log(f"HTTPS server listening on port {port}")

    # Keep the main thread alive.
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log("Shutdown requested")


if __name__ == "__main__":
    main()