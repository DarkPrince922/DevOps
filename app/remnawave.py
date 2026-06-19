import json

import requests

import app.state as state
from app.proxy import requests_proxy


def _cfg():
    try:
        c = state.load_json(state.RW_FILE, {})
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def _base():
    c = _cfg()
    return (c.get("url") or state.REMNAWAVE_URL or "").strip().rstrip("/")


def _token():
    c = _cfg()
    return (c.get("token") or state.REMNAWAVE_TOKEN or "").strip()


def _cookie():
    """Кастомная кука защиты панели (Caddy/eGames и т.п.), если задана."""
    c = _cfg()
    return (c.get("cookie") or state.REMNAWAVE_COOKIE or "").strip()


def has_creds():
    return bool(_base() and _token())


def save_creds(url, token, cookie=""):
    state.save_json(state.RW_FILE, {
        "url": str(url or "").strip().rstrip("/"),
        "token": str(token or "").strip(),
        "cookie": str(cookie or "").strip(),
    })


def _fmt_bytes(n):
    try:
        n = float(n)
    except Exception:
        return str(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _request(method, path, body=None, params=None, timeout=30):
    base, token = _base(), _token()
    if not base:
        return None, "URL панели не задан (REMNAWAVE_URL или кнопка «Подключить»)"
    if not token:
        return None, "API-токен не задан"
    if not path.startswith("/"):
        path = "/" + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    cookie = _cookie()
    if cookie:
        # защита панели на уровне реверс-прокси (Caddy/eGames)
        headers["Cookie"] = cookie
    try:
        r = requests.request(
            method.upper(),
            base + path,
            headers=headers,
            json=body,
            params=params,
            proxies=requests_proxy(),
            timeout=timeout,
        )
    except Exception as e:
        return None, f"сетевая ошибка: {e}"
    try:
        data = r.json() if r.content else {}
    except Exception:
        data = {"_raw": r.text[:500]}
    if r.status_code >= 400:
        msg = ""
        if isinstance(data, dict):
            msg = data.get("message") or data.get("error") or json.dumps(data, ensure_ascii=False)[:300]
        return None, f"HTTP {r.status_code}: {msg or r.text[:200]}"
    # Remnawave оборачивает результат в {"response": ...}
    if isinstance(data, dict) and "response" in data:
        return data["response"], ""
    return data, ""


# ── Универсальный вызов API (для ИИ-агента) ─────────────────────
def api_call(method, path, body=None):
    """Любой запрос к Remnawave API. path обязан начинаться с /api."""
    method = (method or "GET").upper()
    path = str(path or "").strip()
    if not path.startswith("/api"):
        return "❌ Remnawave: path должен начинаться с /api (например /api/users)"
    if isinstance(body, str) and body.strip():
        try:
            body = json.loads(body)
        except Exception:
            return "❌ Remnawave: body не является корректным JSON"
    result, err = _request(method, path, body=body)
    if err:
        return f"❌ Remnawave: {err}"
    text = json.dumps(result, ensure_ascii=False, indent=2) if result is not None else ""
    return (text or "(пустой ответ)")[:6000]


# ── Удобные read-функции для меню и быстрых инструментов ────────
def _as_list(result, *keys):
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for k in keys:
            v = result.get(k)
            if isinstance(v, list):
                return v
    return []


def system_stats():
    result, err = _request("GET", "/api/system/stats")
    if err:
        return f"❌ Remnawave: {err}"
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)[:1500]
    lines = ["📊 Remnawave"]
    users = result.get("users") if isinstance(result.get("users"), dict) else {}
    if users:
        lines.append(f"Пользователи: всего {users.get('totalUsers', '?')}, активных {users.get('activeUsers', users.get('statusCounts', {}).get('ACTIVE', '?'))}")
    if result.get("onlineStats"):
        on = result["onlineStats"]
        lines.append(f"Онлайн сейчас: {on.get('onlineNow', '?')}")
    mem = result.get("memory") or {}
    if mem:
        lines.append(f"Память: {_fmt_bytes(mem.get('used', 0))} / {_fmt_bytes(mem.get('total', 0))}")
    body = "\n".join(lines)
    if len(body.splitlines()) <= 1:
        body += "\n" + json.dumps(result, ensure_ascii=False)[:1200]
    return body[:3500]


def list_users(limit=30):
    try:
        limit = max(1, min(100, int(limit)))
    except Exception:
        limit = 30
    result, err = _request("GET", "/api/users", params={"size": limit, "start": 0})
    if err:
        return f"❌ Remnawave: {err}"
    users = _as_list(result, "users")
    if not users:
        return "Пользователей не найдено."
    lines = [f"👤 Пользователи (показано {min(len(users), limit)}):"]
    for u in users[:limit]:
        name = u.get("username") or u.get("shortUuid") or u.get("uuid")
        status = u.get("status") or ("активен" if u.get("isActive", True) else "выкл")
        used = u.get("usedTrafficBytes")
        lim = u.get("trafficLimitBytes")
        extra = ""
        if used is not None:
            extra = f", трафик {_fmt_bytes(used)}/{_fmt_bytes(lim) if lim else '∞'}"
        lines.append(f"• {name} — {status}{extra}")
    return "\n".join(lines)[:3500]


def list_nodes():
    result, err = _request("GET", "/api/nodes")
    if err:
        return f"❌ Remnawave: {err}"
    nodes = _as_list(result, "nodes")
    if not nodes:
        return "Нод не найдено."
    lines = ["🖥 Ноды:"]
    for n in nodes:
        name = n.get("name") or n.get("uuid")
        addr = n.get("address", "")
        online = "🟢online" if n.get("isConnected") or n.get("isNodeOnline") else "🔴offline"
        disabled = "" if not n.get("isDisabled") else " (отключена)"
        lines.append(f"• {name} {addr} — {online}{disabled}")
    return "\n".join(lines)[:3500]


def list_hosts():
    result, err = _request("GET", "/api/hosts")
    if err:
        return f"❌ Remnawave: {err}"
    hosts = _as_list(result, "hosts")
    if not hosts:
        return "Хостов не найдено."
    lines = ["🔌 Хосты:"]
    for h in hosts:
        remark = h.get("remark") or h.get("uuid")
        addr = h.get("address", "")
        port = h.get("port", "")
        disabled = "" if not h.get("isDisabled") else " (выкл)"
        lines.append(f"• {remark} — {addr}:{port}{disabled}")
    return "\n".join(lines)[:3500]
