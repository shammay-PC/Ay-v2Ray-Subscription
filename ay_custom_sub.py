#!/usr/bin/env python3
"""
Ay-Custom-Sub Subscription Server v4.0.0

一个基于 Waitress 的生产级订阅合并服务器。支持：
- 从 X-UI 主服务器获取订阅并合并手工配置与侧边集合
- 转发 X-UI 的 Subscription-Userinfo、Profile-Title 等响应头
- 服务 /Ay_Custom_Configs 端点
- 线程安全的配置缓存
- 健康的 HTTP 重试机制
"""

import json
import os
import sys
import threading
import time
from urllib.parse import quote, unquote

import requests
from flask import Flask, Response, jsonify, request
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from waitress import serve

# ---------------------------------------------------------------------------
# 常量与路径
# ---------------------------------------------------------------------------

PROJECT_DIR = "/root/ay-custom-sub"
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
CUSTOM_CONFIG_PATH = os.path.join(PROJECT_DIR, "Ay_Custom_Configs")
SIDE_COLLECTIONS_PATH = os.path.join(PROJECT_DIR, "Side_Collections.json")
SIDE_COLLECTIONS_CACHE_PATH = os.path.join(PROJECT_DIR, "Side_Collections_Cache.json")

# 侧边集合缓存 TTL（秒）
SIDE_CACHE_TTL = 1800

app = Flask("Ay-Custom-Sub")

# ---------------------------------------------------------------------------
# 线程安全配置缓存
# ---------------------------------------------------------------------------

_config_lock = threading.Lock()
_config_cache = None
_config_cache_mtime = 0


def load_config() -> dict:
    """读取 config.json 并缓存。文件修改时间变化时自动失效。"""
    global _config_cache, _config_cache_mtime

    with _config_lock:
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            mtime = 0

        if _config_cache is not None and mtime == _config_cache_mtime:
            return _config_cache

        if not os.path.exists(CONFIG_PATH):
            raise FileNotFoundError("config.json 未找到")

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)

        _config_cache = config
        _config_cache_mtime = mtime
        return config


def invalidate_config_cache():
    """强制使配置缓存失效（供外部调用或配置变更时使用）。"""
    global _config_cache, _config_cache_mtime
    with _config_lock:
        _config_cache = None
        _config_cache_mtime = 0


# ---------------------------------------------------------------------------
# HTTP 会话（带重试）
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    """创建一个带重试策略的 requests Session。"""
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_http_session = _build_session()

# ---------------------------------------------------------------------------
# 侧边集合处理
# ---------------------------------------------------------------------------

def _migrate_side_collections_from_config():
    """如果 config.json 中还有 side_collection_urls 且 Side_Collections.json 不存在，
    则执行一次性迁移。"""
    if os.path.exists(SIDE_COLLECTIONS_PATH):
        return

    config = load_config()
    old_urls = config.get("side_collection_urls", [])
    if not old_urls:
        return

    print(f"[Ay-Sub] Migrating {len(old_urls)} side collection URLs from config.json")
    migrated = []
    for url in old_urls:
        if url.strip():
            migrated.append({
                "url": url.strip(),
                "global_rename": "",
                "configs": [],
            })

    with open(SIDE_COLLECTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(migrated, f, ensure_ascii=False, indent=2)

    # 从 config.json 中移除旧字段
    if "side_collection_urls" in config:
        del config["side_collection_urls"]
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        invalidate_config_cache()
    print("[Ay-Sub] Migration complete.")


def _load_side_collections() -> list:
    """加载 Side_Collections.json。"""
    if not os.path.exists(SIDE_COLLECTIONS_PATH):
        return []
    try:
        with open(SIDE_COLLECTIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError) as e:
        print(f"[Ay-Sub] Error loading side collections: {e}")
        return []


def _load_side_cache() -> dict:
    """加载侧边集合缓存。返回 {url: {"configs": [...], "ts": ...}}"""
    if not os.path.exists(SIDE_COLLECTIONS_CACHE_PATH):
        return {}
    try:
        with open(SIDE_COLLECTIONS_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, IOError):
        return {}


def _save_side_cache(cache: dict):
    """保存侧边集合缓存。"""
    try:
        with open(SIDE_COLLECTIONS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except IOError as e:
        print(f"[Ay-Sub] Error saving side cache: {e}")


def _fetch_side_collection(url: str) -> list:
    """从 URL 获取侧边集合内容，返回原始行列表。失败返回空列表。"""
    try:
        resp = _http_session.get(url, timeout=8)
        if resp.status_code == 200:
            return [line.strip() for line in resp.text.strip().splitlines() if line.strip()]
        print(f"[Ay-Sub] Side collection {url} returned HTTP {resp.status_code}")
    except requests.RequestException as e:
        print(f"[Ay-Sub] Failed to fetch side collection {url}: {e}")
    return []


def _get_cached_or_fetch_side(url: str) -> list:
    """获取侧边集合内容，优先使用缓存。"""
    cache = _load_side_cache()
    now = time.time()

    if url in cache:
        entry = cache[url]
        if now - entry.get("ts", 0) < SIDE_CACHE_TTL:
            return entry.get("configs", [])

    configs = _fetch_side_collection(url)
    if configs:
        cache[url] = {"configs": configs, "ts": now}
        _save_side_cache(cache)
        return configs

    # 获取失败，返回旧缓存（即使已过期）
    if url in cache:
        print(f"[Ay-Sub] Using stale cache for {url}")
        return cache[url].get("configs", [])

    return []


def _apply_rename(config_line: str, rename_spec: str, index: int, collection_name: str) -> str:
    """对配置行应用重命名。只替换 # 之后的部分。"""
    if not rename_spec:
        return config_line

    if "#" in config_line:
        base, original_name = config_line.split("#", 1)
    else:
        base = config_line
        original_name = ""

    new_name = (
        rename_spec
        .replace("{original}", original_name)
        .replace("{index}", str(index))
        .replace("{collection}", collection_name)
    )
    return f"{base}#{quote(new_name)}"


def _process_side_collections() -> list:
    """处理所有侧边集合，返回最终配置列表。"""
    collections = _load_side_collections()
    result = []

    for coll in collections:
        url = coll.get("url", "").strip()
        if not url:
            continue

        global_rename = coll.get("global_rename", "")
        collection_name = url.split("//")[-1].split("/")[0] if "//" in url else url

        raw_configs = _get_cached_or_fetch_side(url)

        # 构建 per-config 映射
        per_config_map = {}
        for item in coll.get("configs", []):
            cfg = item.get("config", "")
            if cfg:
                per_config_map[cfg] = item

        for idx, line in enumerate(raw_configs):
            line = line.strip()
            if not line:
                continue

            # 检查 per-config override
            item = per_config_map.get(line, {})
            if item.get("visible") is False:
                continue

            rename = item.get("rename", "") or global_rename
            result.append(_apply_rename(line, rename, idx + 1, collection_name))

    return result


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _read_custom_configs() -> list:
    """读取 Ay_Custom_Configs 文件。"""
    if not os.path.exists(CUSTOM_CONFIG_PATH):
        return []
    try:
        with open(CUSTOM_CONFIG_PATH, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except IOError:
        return []


def _make_dummy_response(config: dict, message_key: str, default_msg: str) -> Response:
    """生成占位配置响应。"""
    message = config.get(message_key, default_msg)
    template = config.get(
        "dummy_config_template",
        "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1?type=ws#{MESSAGE}",
    )
    encoded = quote(message)
    body = template.replace("{MESSAGE}", encoded)

    resp = Response(body, status=200, mimetype="text/plain; charset=utf-8")
    # 发送零值 userinfo 头，方便客户端正确显示
    resp.headers["Subscription-Userinfo"] = "upload=0; download=0; total=0; expire=0"
    return resp


def _copy_xui_headers(src_headers, dst_response: Response):
    """将 X-UI 的响应头复制到目标 Response。"""
    # 需要转发的关键头
    forward_keys = [
        "subscription-userinfo",
        "profile-title",
        "profile-update-interval",
        "profile-web-page-url",
        "announce",
        "announce-url",
        "support-url",
        "profile-description",
    ]
    for key in forward_keys:
        value = src_headers.get(key)
        if value:
            dst_response.headers[key] = value


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    """健康检查端点。"""
    return jsonify({"status": "ok", "service": "ay-custom-sub"})


@app.route("/Ay_Custom_Configs")
def custom_configs_sub():
    """公开端点：以 base64 编码形式返回手工配置。"""
    import base64
    configs = _read_custom_configs()
    raw = "\n".join(configs)
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return Response(
        encoded,
        status=200,
        mimetype="text/plain; charset=utf-8",
    )


@app.route("/<sub_path>/<username>")
def merged_sub(sub_path: str, username: str):
    """主订阅合并端点。"""
    try:
        config = load_config()
    except FileNotFoundError:
        return Response("config.json 未找到", status=500)

    # ------------------------------------------------------------------
    # 1. 从 X-UI 主服务器获取订阅
    # ------------------------------------------------------------------
    main_configs = []
    xui_headers = {}
    try:
        xui_url = (
            f"https://{config['domain']}:{config['main_port']}"
            f"/{sub_path}/{username}"
        )
        real_resp = _http_session.get(
            xui_url, timeout=8, verify=False
        )
        status_code = real_resp.status_code

        if status_code == 200:
            response_text = real_resp.text.strip()
            is_expired = False

            if not response_text:
                is_expired = True
                print(f"[Ay-Sub] User '{username}' subscription expired (empty body)")
            else:
                first_line = response_text.splitlines()[0]
                if "#" in first_line:
                    try:
                        encoded_name = first_line.split("#", 1)[1]
                        decoded = unquote(encoded_name).strip()
                        if decoded.startswith("⛔"):
                            is_expired = True
                            print(f"[Ay-Sub] User '{username}' subscription expired (⛔ detected)")
                    except Exception:
                        pass

            if is_expired:
                return _make_dummy_response(
                    config, "expired_message",
                    "⛔ اشتراک شما منقضی شده، لطفا تمدید کنید ⛔",
                )

            main_configs = [l.strip() for l in response_text.splitlines() if l.strip()]
            # 保存 X-UI 响应头
            xui_headers = dict(real_resp.headers)

        else:
            print(f"[Ay-Sub] User '{username}' not found on main server (HTTP {status_code})")
            return _make_dummy_response(
                config, "no_sub_message",
                "⛔ شما اشتراکی در این سرویس ندارید ⛔",
            )

    except requests.RequestException as e:
        print(f"[Ay-Sub] Connection error for '{username}': {e}")
        return Response(
            "خطا در اتصال به سرور اصلی. کانفیگ‌های قبلی شما حفظ شده است.",
            status=502,
        )

    # ------------------------------------------------------------------
    # 2. 合并手工配置与侧边集合
    # ------------------------------------------------------------------
    extra_configs = _read_custom_configs()
    side_configs = _process_side_collections()

    final_configs = main_configs + extra_configs + side_configs
    cleaned = [l for l in final_configs if l.strip()]

    # ------------------------------------------------------------------
    # 3. 构建响应并转发 X-UI 头
    # ------------------------------------------------------------------
    body = "\n".join(cleaned)
    resp = Response(body, status=200, mimetype="text/plain; charset=utf-8")

    if xui_headers:
        _copy_xui_headers(xui_headers, resp)

    return resp


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def _run_port(port: int, cert_path: str | None, key_path: str | None):
    """在指定端口启动 Waitress 服务器。"""
    ssl_context = None
    if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
        ssl_context = (cert_path, key_path)
        print(f"[Ay-Sub] Port {port}: HTTPS enabled")
    else:
        print(f"[Ay-Sub] Port {port}: HTTP mode (no certs)")

    try:
        serve(
            app,
            host="0.0.0.0",
            port=port,
            url_scheme="https" if ssl_context else "http",
            threads=8,
            clear_untrusted_proxy_headers=True,
        )
    except Exception as e:
        print(f"[Ay-Sub] Port {port} failed to start: {e}")


def main():
    """入口点。"""
    try:
        config = load_config()
    except Exception as e:
        print(f"[Ay-Sub] Cannot load config: {e}")
        sys.exit(1)

    # 执行侧边集合迁移
    _migrate_side_collections_from_config()

    ports = config.get("extra_ports", [6854, 8688, 4586])
    cert_path = config.get("cert_path")
    key_path = config.get("key_path")

    print(f"[Ay-Sub] Starting on ports: {ports}")
    print(f"[Ay-Sub] Main domain: {config.get('domain')}")

    threads = []
    for port in ports:
        t = threading.Thread(
            target=_run_port, args=(port, cert_path, key_path), daemon=True
        )
        t.start()
        threads.append(t)
        print(f"[Ay-Sub] Port {port} thread started")

    # 保持主线程存活
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("[Ay-Sub] Shutting down...")


if __name__ == "__main__":
    main()